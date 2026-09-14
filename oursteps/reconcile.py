"""Bounded search reconciliation. State/cache are private; archive writes require recovery."""
from .config import UID, USERNAME
import contextlib
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.error import URLError
from urllib.parse import urljoin, urlsplit
from .fallback import Transport, FORM, SEARCH, form_fields, results, SearchExpired
from .parser import soup_of, query, parse_thread, thread_url, Busy, Blocked, ParseError, NotFound
from .auth_verify import VerificationIssue
from .store import Store
from .performance import Performance, measure
from .manual_missing import archive_db, local_state, refresh, active_tids

SCHEMA='''
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
CREATE TABLE IF NOT EXISTS shards(
 id INTEGER PRIMARY KEY, mode TEXT NOT NULL, keyword TEXT NOT NULL, fid TEXT NOT NULL,
 seconds INTEGER NOT NULL DEFAULT 0, before_flag TEXT NOT NULL DEFAULT '',
 state TEXT NOT NULL DEFAULT 'pending', page_url TEXT, buffer TEXT, next_url TEXT,
 offset INTEGER NOT NULL DEFAULT 0, checked INTEGER NOT NULL DEFAULT 0,
 total INTEGER, capped INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
 retry_at REAL NOT NULL DEFAULT 0, error TEXT, first_seen REAL, last_seen REAL,
 UNIQUE(mode,keyword,fid,seconds,before_flag));
CREATE TABLE IF NOT EXISTS candidates(
 tid INTEGER PRIMARY KEY,title TEXT,displayed_date TEXT,forum TEXT,source TEXT,
 first_seen REAL,last_seen REAL,local_state TEXT,ownership TEXT NOT NULL DEFAULT 'unknown',
 recovery TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 retry_at REAL NOT NULL DEFAULT 0,error TEXT,probe_snapshot INTEGER);
CREATE TABLE IF NOT EXISTS sightings(
 shard INTEGER,tid INTEGER,source TEXT,PRIMARY KEY(shard,tid));
CREATE TABLE IF NOT EXISTS pages(
 shard INTEGER,url TEXT,PRIMARY KEY(shard,url));
'''


class BudgetReached(Exception):pass


@contextlib.contextmanager
def locked(path):
    with path.open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield


class State:
    def __init__(self,root):
        self.root=root
        self.db=sqlite3.connect(str(root/'data/reconcile.sqlite3'),timeout=5)
        os.chmod(root/'data/reconcile.sqlite3',0o600)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript(SCHEMA)
    def setting(self,key,default='0'):
        row=self.db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return row[0] if row else default
    def set(self,key,value):
        self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,str(value)))
    def shard(self,mode,keyword,fid='all',seconds=0,before=''):
        self.db.execute('INSERT OR IGNORE INTO shards(mode,keyword,fid,seconds,before_flag,first_seen,last_seen) VALUES(?,?,?,?,?,?,?)',(mode,keyword,str(fid),seconds,before,time.time(),time.time()))
    def add_candidate(self,row,source,shard=None):
        now=time.time()
        self.db.execute('''INSERT INTO candidates(tid,title,displayed_date,forum,source,first_seen,last_seen)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(tid) DO UPDATE SET
            title=COALESCE(excluded.title,candidates.title),
            displayed_date=COALESCE(excluded.displayed_date,candidates.displayed_date),
            forum=COALESCE(excluded.forum,candidates.forum),last_seen=excluded.last_seen''',
            (row['tid'],row.get('title'),row.get('displayed_date') or row.get('created_at_raw'),row.get('forum'),source,now,now))
        if shard is not None:
            self.db.execute('INSERT OR IGNORE INTO sightings VALUES(?,?,?)',(shard,row['tid'],source))
    def compare(self):
        db=archive_db(self.root)
        try:
            with self.db:
                for row in self.db.execute('SELECT tid FROM candidates').fetchall():
                    local=local_state(self.root,db,row['tid'])
                    self.db.execute('UPDATE candidates SET local_state=? WHERE tid=?',(local['local_state'],row['tid']))
        finally:db.close()
        refresh(self.root,[r[0] for r in self.db.execute('SELECT tid FROM candidates')])
    def summary(self,mode=None,keyword=''):
        where='';params=()
        if mode:
            where=' WHERE tid IN (SELECT tid FROM sightings WHERE shard IN (SELECT id FROM shards WHERE mode=? AND keyword=?))'
            params=(mode,keyword)
        rows=[dict(r) for r in self.db.execute('SELECT * FROM candidates'+where+' ORDER BY tid',params)]
        shards=[dict(r) for r in self.db.execute('SELECT * FROM shards'+(' WHERE mode=? AND keyword=?' if mode else '')+' ORDER BY id',params)]
        states={}
        for s in shards:states[s['state']]=states.get(s['state'],0)+1
        return dict(scope=mode or 'all_recorded',keyword=keyword or None,active_missing_total=len(active_tids(self.root)),search_results_checked=sum(s['checked'] for s in shards),unique_tids=len(rows),
            reported_results=max([s['total'] or 0 for s in shards if s['fid']=='all'] or [0]) or None,
            tids=[r['tid'] for r in rows],already_archived=sum(r['local_state'] in ('archived','complete_unpublished') for r in rows),
            incomplete=sum(r['local_state']=='incomplete' for r in rows),missing_candidates=sum(r['local_state']=='missing' for r in rows),
            thread_owner_verified=sum(r['ownership']=='verified' for r in rows),
            missing_verified=sum(r['ownership']=='verified' and r['local_state']=='missing' for r in rows),
            recovered=sum(r['recovery']=='recovered' for r in rows),
            unresolved=sum(r['local_state'] not in ('archived','complete_unpublished') for r in rows),
            pending=sum(r['recovery'] not in ('recovered','rejected','existing_incomplete') and r['local_state'] not in ('archived','complete_unpublished') for r in rows),
            failed_retry_pending=sum(bool(r['error']) for r in rows)+sum(bool(s['error']) for s in shards),
            tea_busy_pauses=int(self.setting('tea_busy_pauses')),retry_at=float(self.setting('pause_until')),
            shard_states=states,next_pending_shard=next((dict(id=s['id'],fid=s['fid'],seconds=s['seconds'],before=s['before_flag'],state=s['state']) for s in shards if s['state'] not in ('complete','split')),None),
            coverage_complete=bool(shards) and all(s['state'] in ('complete','split') for s in shards),
            errors=[dict(tid=r['tid'],status=r['recovery'],error=r['error']) for r in rows if r['error']])


def search_page(content,url):
    """Reuse the existing owner-filtering search parser, enrich only metadata."""
    rows,nxt,capped=results(content,url)
    soup=soup_of(content);text=soup.get_text(' ',strip=True)
    total=re.search(r'(?:相关内容|找到|搜索到|共(?:有)?)\s*([0-9,]+)\s*(?:个|条|篇)',text)
    total=int(total[1].replace(',','')) if total else None
    for row in rows:
        for h in soup.select('h3 a[href]'):
            if query(h.get('href','')).get('tid')==str(row['tid']) or re.search(r'thread-%s-'%row['tid'],h.get('href','')):
                li=h.find_parent('li')
                forum=next((a.get_text(' ',strip=True) for a in li.select('a[href]') if query(a['href']).get('mod')=='forumdisplay'),None)
                row['forum']=forum
                # The normal result template prints a timestamp before the author.
                # Preserve it as DISPLAY metadata only, never canonical publication time.
                for meta in li.select('p'):
                    if any(query(a['href']).get('uid')==str(UID) for a in meta.select('a[href]')):
                        stamp=re.match(r'^(20\d\d-\d{1,2}-\d{1,2}(?: \d{1,2}:\d{2})?)\b',meta.get_text(' ',strip=True))
                        if stamp:row['displayed_date']=stamp[1]
                break
    if nxt:
        u=urlsplit(nxt)
        if u.scheme!='https' or u.netloc!='www.oursteps.com.au' or u.path!='/bbs/search.php':
            raise Blocked('search_next_off_origin')
    return rows,nxt,capped or (total is not None and total>=500),total


def search_fields(content,shard):
    fields,forums,ranges=form_fields(content)
    form=soup_of(content).select_one('input[name=srchuname]').find_parent('form')
    options=lambda name:{n.get('value','') for n in form.select('[name="%s"] option, input[name="%s"]'%(name,name))}
    required={'srchfilter':'all','srchfrom':str(shard['seconds']),'before':shard['before_flag'],'orderby':'dateline','ascdesc':'desc','srchfid[]':shard['fid']}
    for name,value in required.items():
        if value not in options(name):raise ParseError('search_option_changed:'+name)
    fields.update(required)
    fields.update(srchuname=USERNAME,srchtxt=shard['keyword'],searchsubmit='yes')
    fields.pop('special[]',None)
    return fields,forums,ranges


def split_shard(state,shard,forums,ranges):
    """Only real form options. No invented absolute dates or exhaustive-coverage claim."""
    args=(shard['mode'],shard['keyword'])
    if shard['fid']=='all':
        for fid in forums:state.shard(*args,fid=fid)
    elif not shard['before_flag'] and any(n<shard['seconds'] or shard['seconds']==0 for n in ranges):
        seconds=max(n for n in ranges if n<shard['seconds'] or shard['seconds']==0)
        state.shard(*args,fid=shard['fid'],seconds=seconds)
        state.shard(*args,fid=shard['fid'],seconds=seconds,before='1')
    else:
        state.db.execute("UPDATE shards SET state='unresolved_limited',error='supported_relative_ranges_cannot_split_older_remainder' WHERE id=?",(shard['id'],))
        return
    state.db.execute("UPDATE shards SET state='split' WHERE id=?",(shard['id'],))


class LimitedTransport(Transport):
    def __init__(self,store,deadline,max_requests=60):
        super().__init__(store);store.historical_mode=True
        self.deadline=deadline;self.remaining=max_requests;self.requests=0
        self.exhausted_reason=None;self.thread_attempts=0
        self.performance_threads=[]
    def budget(self):
        if time.time()>=self.deadline:
            self.exhausted_reason='time_budget'
            raise BudgetReached(self.exhausted_reason)
        if self.remaining<=0:
            self.exhausted_reason='logical_request_budget'
            raise BudgetReached(self.exhausted_reason)
        self.remaining-=1;self.requests+=1
    def search(self,url=FORM,data=None):
        self.budget();return super().search(url,data)
    def get(self,url):
        self.budget()
        # Existing worker supplies an empty presentation-only extra parameter.
        # Canonicalize it here; core route allowlist and the old backlog stay unchanged.
        q=query(url)
        if '&extra=' in url and q.get('mod')=='viewthread':
            from urllib.parse import parse_qs
            raw=parse_qs(urlsplit(url).query,keep_blank_values=True)
            if set(raw)<= {'mod','tid','page','extra'} and raw.get('extra')==['']:
                url=thread_url(q['tid'],q.get('page',1))
        return measure(self.store,'fetch_seconds',super().get,url)


def transient(error):
    return isinstance(error,(Busy,URLError,TimeoutError,ConnectionError,http.client.HTTPException,SearchExpired)) or (isinstance(error,VerificationIssue) and error.retry)


def error_code(error):
    # Never log arbitrary exception text/URLs/session values.
    if isinstance(error,VerificationIssue):return error.category+':'+error.reason
    if isinstance(error,ParseError):
        return str(error).split(':')[0] if re.fullmatch(r'[A-Za-z0-9_:]+',str(error)) else type(error).__name__
    return type(error).__name__


def record_error(state,table,key,error,clock=time.time):
    row=state.db.execute('SELECT attempts FROM '+table+' WHERE '+('id' if table=='shards' else 'tid')+'=?',(key,)).fetchone()
    attempt=row[0] if row else 0;retry=transient(error)
    delay=max(min(60*2**min(attempt,6),3600),getattr(error,'retry_after',0),60)
    until=clock()+delay if retry else 0
    category='retry_later' if retry else ('rejected' if isinstance(error,ParseError) and 'thread_not_owned' in str(error) else 'review_required')
    with state.db:
        field='state' if table=='shards' else 'recovery'
        state.db.execute('UPDATE '+table+' SET '+field+'=?,attempts=attempts+1,retry_at=?,error=? WHERE '+('id' if table=='shards' else 'tid')+'=?',(category,until,error_code(error),key))
        if category=='rejected':state.db.execute("UPDATE candidates SET ownership='rejected' WHERE tid=?",(key,))
        if retry:
            state.set('pause_until',max(float(state.setting('pause_until')),until))
            if 'busy' in error_code(error):state.set('tea_busy_pauses',int(state.setting('tea_busy_pauses'))+1)
        if isinstance(error,SearchExpired):state.db.execute('UPDATE shards SET page_url=NULL WHERE id=?',(key,))
    return category


def audit(state,transport,mode,keyword='',limit=200):
    with state.db:state.shard(mode,keyword)
    checked=0
    while checked<limit:
        if time.time()<float(state.setting('pause_until')):return 'retry_later'
        shard=state.db.execute("SELECT * FROM shards WHERE mode=? AND keyword=? AND state IN ('pending','retry_later') AND retry_at<=? ORDER BY id LIMIT 1",(mode,keyword,time.time())).fetchone()
        if shard is None:return 'finished_available_shards'
        try:
            if shard['buffer'] is None:
                form,_=transport.search();fields,forums,ranges=search_fields(form,shard)
                with state.db:
                    state.set('forums',json.dumps(forums));state.set('ranges',json.dumps(ranges))
                body,url=transport.search(shard['page_url']) if shard['page_url'] else transport.search(SEARCH,fields)
                rows,nxt,capped,total=search_page(body,url)
                if state.db.execute('SELECT 1 FROM pages WHERE shard=? AND url=?',(shard['id'],url)).fetchone():
                    raise ParseError('search_pagination_loop')
                with state.db:
                    state.db.execute('INSERT INTO pages VALUES(?,?)',(shard['id'],url))
                    state.db.execute("UPDATE shards SET buffer=?,offset=0,next_url=?,page_url=?,total=COALESCE(?,total),capped=?,state='pending',error=NULL,last_seen=? WHERE id=?",(json.dumps(rows,ensure_ascii=False),nxt,url,total,int(capped),time.time(),shard['id']))
                shard=state.db.execute('SELECT * FROM shards WHERE id=?',(shard['id'],)).fetchone()
            rows=json.loads(shard['buffer']);take=rows[shard['offset']:shard['offset']+limit-checked]
            # Tracking is done before the cursor transaction so a crash never loses a missing TID.
            refresh(state.root,[r['tid'] for r in take])
            with state.db:
                for row in take:state.add_candidate(row,shard['page_url'],shard['id'])
                offset=shard['offset']+len(take);checked+=len(take)
                state.db.execute('UPDATE shards SET offset=?,checked=checked+?,last_seen=? WHERE id=?',(offset,len(take),time.time(),shard['id']))
                if offset==len(rows):
                    unique=state.db.execute('SELECT count(*) FROM sightings WHERE shard=?',(shard['id'],)).fetchone()[0]
                    if shard['capped'] or unique>=500:
                        split_shard(state,shard,json.loads(state.setting('forums')),json.loads(state.setting('ranges')))
                    else:
                        state.db.execute("UPDATE shards SET state=?,page_url=?,buffer=NULL,offset=0,error=NULL WHERE id=?",('pending' if shard['next_url'] else 'complete',shard['next_url'],shard['id']))
        except BudgetReached:return 'budget_reached'
        except (ParseError,VerificationIssue,URLError,TimeoutError,ConnectionError,http.client.HTTPException) as error:
            return record_error(state,'shards',shard['id'],error)
    return 'result_limit_reached'


def verify_candidate(state,cache,transport,tid):
    snap=transport.get(thread_url(tid));body=cache.raw(snap)
    parsed=measure(cache,'parse_seconds',parse_thread,body,thread_url(tid))
    if parsed['owner_uid']!=UID:raise ParseError('thread_not_owned_by_configured_owner')
    with state.db:
        state.db.execute("UPDATE candidates SET ownership='verified',probe_snapshot=?,error=NULL,recovery='pending',retry_at=0 WHERE tid=?",(snap['id'],tid))
    return parsed,snap


def verify_pending(state,cache,transport,limit=4,mode=None,keyword=''):
    state.compare()
    where="ownership='unknown' AND recovery IN ('pending','retry_later') AND retry_at<=? AND local_state='missing'"
    params=[time.time()]
    if mode:
        where+=' AND tid IN (SELECT tid FROM sightings WHERE shard IN (SELECT id FROM shards WHERE mode=? AND keyword=?))';params += [mode,keyword]
    rows=state.db.execute('SELECT tid FROM candidates WHERE '+where+' ORDER BY tid LIMIT ?',params+[limit]).fetchall()
    for row in rows:
        if float(state.setting('pause_until'))>time.time():break
        try:verify_candidate(state,cache,transport,row['tid'])
        except BudgetReached:break
        except (ParseError,VerificationIssue,URLError,TimeoutError,ConnectionError,http.client.HTTPException) as error:
            record_error(state,'candidates',row['tid'],error)
            if transient(error) or isinstance(error,(Blocked,VerificationIssue)):break



def count_worker_busy(state,jobs):
    """Metrics only: the worker preserves Busy as transient:Busy, not site_busy.

    Deduplicate the same persisted job attempt on repeated status/resume calls.
    Generic Busy may include a transient 5xx; no retry timing is changed here.
    """
    for job in jobs:
        error=job['error'] or ''
        if error!='transient:Busy' and 'site_busy' not in error:
            continue
        key='busy_job_attempt:'+job['url']
        if job['attempts']>int(state.setting(key,'-1')):
            state.set(key,job['attempts'])
            state.set('tea_busy_pauses',int(state.setting('tea_busy_pauses'))+1)


def recover_one(state,cache,transport,tid):
    perf=Performance(); previous=getattr(cache,'performance',None); cache.performance=perf
    start=time.monotonic(); requests=getattr(transport,'requests',0)
    before=cache.db.execute("SELECT count(*) FROM pages WHERE tid=? AND result='parsed'",(tid,)).fetchone()[0]
    outcome='exception'
    try:
        outcome=_recover_one(state,cache,transport,tid)
        return outcome
    except BudgetReached:
        outcome='budget_reached'
        raise
    finally:
        row=cache.db.execute('SELECT count(*),max(declared_pages) FROM pages WHERE tid=?',(tid,)).fetchone()
        entry=dict(tid=tid,outcome=outcome,declared_pages=row[1],
                   successful_pages_at_start=before,parsed_pages_after=row[0],
                   logical_requests=getattr(transport,'requests',0)-requests,
                   total_seconds=round(time.monotonic()-start,6),**perf.report())
        entry['import_seconds']=round(perf.values.get('import_seconds',0),6)
        entry['pages_fetched_unique']=len(perf.pages)
        entry['retry_at']=float(state.db.execute('SELECT retry_at FROM candidates WHERE tid=?',(tid,)).fetchone()[0])
        if not hasattr(transport,'performance_threads'):transport.performance_threads=[]
        transport.performance_threads.append(entry)
        cache.performance=previous


def cached_parsed_page(cache, thread, page):
    """Adapt normalized cache rows to save_page's existing input contract."""
    from .parser import VERSION
    if page['result']!='parsed' or page['parser_version']!=VERSION:
        raise ParseError('cache_page_not_current_parsed')
    metrics=cache.db.execute('SELECT views,replies FROM metrics WHERE tid=? AND snapshot_id=?',
                             (thread['tid'],page['snapshot_id'])).fetchone()
    if metrics is None:raise ParseError('cache_page_metrics_missing')
    posts=[]
    for row in cache.db.execute('SELECT * FROM posts WHERE tid=? AND page=? ORDER BY rowid',
                                (thread['tid'],page['page'])):
        if row['snapshot_id']!=page['snapshot_id']:
            raise ParseError('cache_post_snapshot_mismatch')
        post={key:row[key] for key in ('pid','tid','author_uid','author_name','floor','page','posted_at_raw','edited_at_raw')}
        post.update(html=row['body_html'],text=row['body_text'],hash=row['content_sha256'],
                    links=[dict(r) for r in cache.db.execute('SELECT url,label FROM links WHERE pid=? ORDER BY rowid',(row['pid'],))],
                    assets=[dict(kind=r['kind'],url=r['url'],attributes=json.loads(r['attributes'])) for r in
                            cache.db.execute('SELECT kind,url,attributes FROM assets WHERE pid=? ORDER BY position',(row['pid'],))])
        posts.append(post)
    return dict(tid=thread['tid'],title=thread['title'],fid=thread['fid'],forum=thread['forum'],
                page=page['page'],posts=posts,views=metrics['views'],replies=metrics['replies'],
                all_pids=json.loads(page['all_pids']),declared_pages=page['declared_pages'],next_pages=[],
                gaps=[dict(r) for r in cache.db.execute('SELECT pid,reason FROM gaps WHERE url=? ORDER BY rowid',(page['url'],))])


def _recover_one(state,cache,transport,tid):
    """Complete in private staging, then reuse Store/parser import. Never run global history."""
    db=archive_db(state.root)
    try:local=local_state(state.root,db,tid)
    finally:db.close()
    if local['local_state'] in ('archived','complete_unpublished'):return 'already_archived'
    if local['local_state']=='incomplete' and local['provenance']!='reconciliation':return 'existing_incomplete'
    candidate=state.db.execute('SELECT * FROM candidates WHERE tid=?',(tid,)).fetchone()
    if candidate['ownership']=='rejected':return 'rejected'
    if candidate['ownership']!='verified':parsed,snap=verify_candidate(state,cache,transport,tid)
    else:
        snap=cache.db.execute('SELECT * FROM snapshots WHERE id=?',(candidate['probe_snapshot'],)).fetchone()
        if snap is None:parsed,snap=verify_candidate(state,cache,transport,tid)
        else:
            if getattr(cache,'performance',None) is not None:cache.performance.add('cached_page_uses')
            parsed=measure(cache,'parse_seconds',parse_thread,cache.raw(snap),thread_url(tid))
    cache.historical_mode=True
    with cache.db:
        cache.db.execute("INSERT OR IGNORE INTO threads(tid,discovered_via) VALUES(?,'reconciliation')",(tid,))
        cache.db.execute("INSERT OR IGNORE INTO jobs(url,kind,tid,page) VALUES(?,'thread',?,1)",(thread_url(tid),tid))
    if not cache.db.execute('SELECT 1 FROM pages WHERE tid=? AND page=1',(tid,)).fetchone():cache.save_page(thread_url(tid),parsed,snap)
    with state.db:state.db.execute("UPDATE candidates SET recovery='collecting' WHERE tid=?",(tid,))
    from .cli import run
    outcome=run(cache,tids=[tid],fetcher_override=transport)
    if cache.db.execute('SELECT status FROM threads WHERE tid=?',(tid,)).fetchone()[0]!='complete':
        jobs=cache.db.execute("SELECT url,attempts,retry_at,state,error FROM jobs WHERE tid=? AND state!='success' ORDER BY retry_at DESC",(tid,)).fetchall()
        due=max([r['retry_at'] for r in jobs] or [0])
        with state.db:
            state.db.execute("UPDATE candidates SET recovery=?,retry_at=?,error=? WHERE tid=?",('retry_later' if any(r['state']=='retry_later' for r in jobs) else 'review_required',due,outcome,tid))
            count_worker_busy(state,jobs)
            if due>time.time():state.set('pause_until',max(due,float(state.setting('pause_until'))))
        return outcome
    # Resolve publication time with the existing footer/verified-offset logic.
    from .dates import publication_time, cached_display_offset
    owner=next(p for p in parsed['posts'] if p['floor']=='1#')
    try:published=publication_time(owner['posted_at_raw'],cache.raw(snap))
    except ParseError:
        from .auth import STATE
        if not STATE.exists():raise VerificationIssue('timezone_required','missing_verified_display_timezone')
        saved=json.loads(STATE.read_text())
        published=publication_time(owner['posted_at_raw'],cache.raw(snap),cached_display_offset(saved))
    cached_thread=cache.db.execute('SELECT * FROM threads WHERE tid=?',(tid,)).fetchone()
    cached_jobs=cache.db.execute('SELECT * FROM jobs WHERE tid=? ORDER BY page,url',(tid,)).fetchall()
    if not cached_jobs or any(job['state']!='success' for job in cached_jobs):
        raise ParseError('cache_jobs_not_complete')
    import_start=time.monotonic()
    live=Store(state.root/'data');live.historical_mode=True
    live.performance=getattr(cache,'performance',None)
    try:
        # No inventory cursor/reset and no selection of pre-existing pending jobs.
        with live.db:
            live.db.execute("INSERT OR IGNORE INTO threads(tid,discovered_via) VALUES(?,'reconciliation')",(tid,))
            live.db.execute('INSERT OR IGNORE INTO inventory VALUES(?,?,?)',(tid,time.time(),time.time()))
            # Copy the complete job topology, but keep live jobs pending until their pages save.
            # This preserves the previous import's crash/retry semantics without next-page parsing.
            for job in cached_jobs:
                live.db.execute('INSERT OR IGNORE INTO jobs(url,kind,tid,page) VALUES(?,?,?,?)',
                                (job['url'],job['kind'],tid,job['page']))
        for page in cache.db.execute('SELECT * FROM pages WHERE tid=? ORDER BY page',(tid,)).fetchall():
            saved=cache.db.execute('SELECT * FROM snapshots WHERE id=?',(page['snapshot_id'],)).fetchone()
            body=cache.raw(saved);parsed_page=cached_parsed_page(cache,cached_thread,page)
            copied=live.snapshot(page['url'],body,status=saved['status'],mode=saved['mode'])
            live.save_page(page['url'],parsed_page,copied)
        with live.db:live.db.execute('INSERT OR REPLACE INTO publication_times VALUES(?,?)',(tid,published))
        if live.db.execute('SELECT status FROM threads WHERE tid=?',(tid,)).fetchone()[0]!='complete':raise ParseError('import_not_complete')
    finally:
        live.db.close()
        if live.performance is not None:live.performance.add('import_seconds',time.monotonic()-import_start)
    with state.db:state.db.execute("UPDATE candidates SET recovery='recovered',error=NULL,retry_at=0 WHERE tid=?",(tid,))
    return 'recovered'


def backfill(state,cache,transport,limit=10):
    with state.db:
        for tid in active_tids(state.root):state.add_candidate(dict(tid=tid),'manual-missing')
    state.compare()
    outcome='bounded_backfill_finished'
    rows=state.db.execute("SELECT tid FROM candidates WHERE recovery IN ('pending','retry_later','collecting') AND retry_at<=? AND local_state NOT IN ('archived','complete_unpublished') ORDER BY tid LIMIT ?",(time.time(),limit)).fetchall()
    for row in rows:
        if float(state.setting('pause_until'))>time.time():break
        try:
            transport.thread_attempts=getattr(transport,'thread_attempts',0)+1
            outcome=recover_one(state,cache,transport,row['tid'])
            if outcome in ('existing_incomplete','already_archived'):
                with state.db:state.db.execute('UPDATE candidates SET recovery=? WHERE tid=?',(outcome,row['tid']))
            if outcome not in ('recovered','already_archived','existing_incomplete'):break
        except BudgetReached:
            outcome='budget_reached';break
        except (ParseError,VerificationIssue,URLError,TimeoutError,ConnectionError,http.client.HTTPException) as error:
            outcome=record_error(state,'candidates',row['tid'],error)
            if transient(error) or isinstance(error,(Blocked,VerificationIssue)):break
    state.compare()
    return 'retry_later' if float(state.setting('pause_until'))>time.time() else outcome
