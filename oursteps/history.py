"""User-invoked inventory/backfill; no scheduler or model dependency."""
from .config import UID
import json,time,random,re
from .parser import directory_url,thread_url,parse_discovery,soup_of,query,ParseError,Blocked,Busy,PaginationLimit
from .fetch import Fetcher,OutsideWindow,RateLimited
from .auth_verify import VerificationIssue
from .store import atomic_write
from urllib.error import URLError
SCHEMA='''CREATE TABLE IF NOT EXISTS inventory(tid INTEGER PRIMARY KEY REFERENCES threads(tid),first_seen REAL,last_seen REAL);
CREATE TABLE IF NOT EXISTS inventory_progress(id INTEGER PRIMARY KEY CHECK(id=1),next_page INTEGER DEFAULT 1,state TEXT DEFAULT 'pending',attempts INTEGER DEFAULT 0,retry_at REAL DEFAULT 0,error TEXT);
INSERT OR IGNORE INTO inventory_progress(id) VALUES(1);'''

def prepare(store):store.db.executescript(SCHEMA)

def inventory_page(store,content,url):
    parsed=parse_discovery(content,url)
    current=int(query(url).get('page','1'))
    # Missing next-page control must not silently turn a truncated pager into EOF.
    for a in soup_of(content).select('a[href]'):
        q=query(a['href'])
        if q.get('uid')==str(UID) and q.get('do')=='thread' and q.get('page','').isdigit() and int(q['page'])>current and not parsed['next_url']:
            raise ParseError('inventory_next_link_unrecognized')
    if b'</html>' not in content.lower():raise ParseError('inventory_incomplete_html')
    with store.db:
        for t in parsed['threads']:
            store.db.execute("INSERT INTO threads(tid,title,fid,discovered_via) VALUES(?,?,?,'inventory') ON CONFLICT(tid) DO UPDATE SET title=CASE WHEN threads.status='complete' THEN threads.title ELSE excluded.title END,fid=COALESCE(excluded.fid,threads.fid)",(t['tid'],t['title'],t['fid']))
            store.db.execute('INSERT INTO inventory VALUES(?,?,?) ON CONFLICT(tid) DO UPDATE SET last_seen=excluded.last_seen',(t['tid'],time.time(),time.time()))
        store.db.execute('UPDATE inventory_progress SET next_page=?,state=?,retry_at=0,error=NULL WHERE id=1',(current+1 if parsed['next_url'] else current,'pending' if parsed['next_url'] else 'complete'))
    return parsed['next_url']

def discover(store,now=False):
    prepare(store);state=store.db.execute('SELECT * FROM inventory_progress').fetchone()
    if max(state['retry_at'],float(store.setting('pause_until','0')))>time.time():return 'retry_later'
    # Resume the first uncommitted page exactly; never restart a blocked pass.
    page=1 if state['state']=='complete' else max(1,state['next_page'])
    fetch=Fetcher(store);fetch.daily_now=now;seen=set()
    try:
        while True:
            url=directory_url(page)
            if url in seen:raise ParseError('inventory_pagination_loop')
            seen.add(url)
            if page>=50:fetch.delay=max(fetch.delay,10)
            snap=fetch.get(url)
            nxt=inventory_page(store,store.raw(snap),url)
            if not nxt:return 'complete'
            page=int(query(nxt)['page'])
    except OutsideWindow:return 'waiting_window'
    except (Busy,URLError,TimeoutError,ConnectionError,OSError,VerificationIssue,ParseError) as e:
        transient=isinstance(e,(Busy,URLError,TimeoutError,ConnectionError,OSError)) or (isinstance(e,VerificationIssue) and e.retry)
        category='pagination_limited' if isinstance(e,PaginationLimit) else ('retry_later' if transient else (e.category if isinstance(e,VerificationIssue) else ('permission_or_challenge' if isinstance(e,Blocked) else 'parse_error')))
        attempts=state['attempts']+1;delay=max(getattr(e,'retry_after',0),min(3600,60*2**min(attempts,6))*random.uniform(1,1.3))
        paused=transient or category in ('pagination_limited','permission_or_challenge','permission_denied','manual_required')
        if category=='pagination_limited':delay=max(delay,86400)
        elif paused and not transient:delay=max(delay,3600)
        with store.db:store.db.execute('UPDATE inventory_progress SET state=?,attempts=?,retry_at=?,error=? WHERE id=1',(category,attempts,time.time()+delay if paused else 0,str(e)))
        if transient:store.set_setting('pause_until',time.time()+delay)
        return category

def backfill(store,now=False,best_effort=False):
    from .cli import run
    from .preview import build
    prepare(store)
    if not store.db.execute('SELECT 1 FROM inventory LIMIT 1').fetchone():return 'inventory_empty: run discovery first'
    # Migrate only the old blanket redirect stop; preserve other global safeguards.
    old_halt=store.setting('halt')
    if re.fullmatch(r'http_(301|302|303|307|308); redirect/auth/rate-limit not followed',old_halt or ''):
        with store.db:
            store.db.execute("UPDATE jobs SET state='pending',error='reauth_fetch',retry_at=0 WHERE error=? AND tid IN (SELECT tid FROM inventory)",(old_halt,))
        store.set_setting('halt','')
    if store.setting('halt'):return 'halted: inspect existing halt before retrying'
    if best_effort:store.set_setting('backfill_mode','BEST_EFFORT')
    status='complete';fetch=Fetcher(store);fetch.daily_now=now
    # Page limit extension applies only inside this explicit historical worker.
    store.historical_mode=True
    try:
        tids=[r[0] for r in store.db.execute("SELECT i.tid FROM inventory i JOIN threads t ON t.tid=i.tid WHERE t.status!='complete' ORDER BY i.tid DESC")]
        from .auth import STATE
        verified=json.loads(STATE.read_text()).get('verified_at',0) if STATE.exists() else 0
        for tid in tids:
            with store.db:
                store.db.execute("INSERT OR IGNORE INTO jobs(url,kind,tid,page) VALUES(?,'thread',?,1)",(thread_url(tid),tid))
                store.db.execute("UPDATE jobs SET state='pending',error='reauth_fetch',retry_at=0 WHERE tid=? AND state='auth_required' AND error LIKE 'auth_required:%' AND retry_at<?",(tid,verified))
            eligible=store.db.execute("SELECT 1 FROM jobs WHERE tid=? AND state IN ('pending','retry_later') AND retry_at<=?",(tid,time.time())).fetchone()
            if not eligible:continue
            status=run(store,tids=[tid],fetcher_override=fetch)
            if status in ('halted','waiting_window') or store.setting('halt'):break
            pause_until = float(store.setting('pause_until','0'))
            if pause_until > time.time():
                # Historical backfill is intended to run unattended.
                # Wait through transient site/backoff pauses instead of exiting
                # and requiring the user to manually restart the command.
                while pause_until > time.time():
                    time.sleep(min(60, max(1, pause_until-time.time())))
                    pause_until = float(store.setting('pause_until','0'))
                status = 'partial'
                continue
            # One attempt per due page per pass. Persistent backoff handles later retries.
            status='partial' if status not in ('complete','success') else status
    except BaseException:
        status='interrupted'
        raise
    finally:
        store.historical_mode=False
        # URL-only media policy:
        # image URLs remain archived in assets; historical backfill does not
        # download remote image binaries.
        problems=[dict(r) for r in store.db.execute("SELECT j.tid,j.page,j.url,j.state,j.error,j.attempts,j.retry_at FROM jobs j JOIN inventory i ON i.tid=j.tid WHERE j.state!='success' ORDER BY j.tid,j.page")]
        atomic_write(store.root/'backfill-problems.json',json.dumps(problems,ensure_ascii=False,indent=2).encode())
        build(store)
    return 'best_effort_pass_finished' if best_effort and status in ('complete','success','partial') else status

def report(store):
    prepare(store)
    total=store.db.execute('SELECT count(*) FROM inventory').fetchone()[0]
    done=store.db.execute("SELECT count(*) FROM inventory i JOIN threads t ON t.tid=i.tid WHERE t.status='complete'").fetchone()[0]
    years={};dates=[]
    date_query='SELECT t.created_at_raw FROM threads t JOIN inventory i ON i.tid=t.tid'
    if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='fallback_dates'").fetchone():
        date_query='SELECT COALESCE(t.created_at_raw,d.created_at_raw) FROM threads t JOIN inventory i ON i.tid=t.tid LEFT JOIN fallback_dates d ON d.tid=t.tid'
    for r in store.db.execute(date_query):
        m=re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})',r[0] or '')
        if m:
            y,month,day=map(int,m.groups());dates.append('%04d-%02d-%02d'%(y,month,day));years[str(y)]=years.get(str(y),0)+1
    failed=store.db.execute("SELECT count(DISTINCT j.tid) FROM jobs j JOIN inventory i ON i.tid=j.tid WHERE j.state NOT IN ('success','pending')").fetchone()[0]
    result=dict(total_discovered=total,fully_archived=done,remaining=total-done,failed_or_retry_pending=failed,earliest_known_date=min(dates) if dates else None,latest_known_date=max(dates) if dates else None,counts_by_year=years,date_unknown=total-len(dates),inventory_state=dict(store.db.execute('SELECT * FROM inventory_progress').fetchone()))
    from .fallback import status as fallback_status
    result.update(fallback_status(store))
    result['archive_mode']=store.setting('backfill_mode','BEST_EFFORT' if result['completeness']=='NO' else 'KNOWN_INVENTORY')
    result['archive_scope']='INCOMPLETE_DISCOVERY' if result['completeness']=='NO' else 'KNOWN_INVENTORY_ONLY'
    result['awaiting_backfill']=total-done
    result['discovery_failures_retry_pending']=sum(n for k,n in result.get('shard_states',{}).items() if k not in ('complete','split','pending'))
    atomic_write(store.root/'inventory-report.json',json.dumps(result,ensure_ascii=False,indent=2).encode());return result
