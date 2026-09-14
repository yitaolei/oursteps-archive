"""Normal author/forum search only. Unsupported date ranges are never invented."""
from .config import UID, USERNAME
import json,time,random,re,hashlib
from urllib.parse import urlsplit,urljoin,urlencode
from urllib.request import Request
from urllib.error import HTTPError,URLError
from .fetch import Fetcher,AGENT,longest_rule_allowed,retry_after
from .parser import soup_of,query,guard,Busy,Blocked,ParseError,PaginationLimit
from .auth_verify import VerificationIssue
from .history import prepare as prepare_inventory
from .store import atomic_write
FORM='https://www.oursteps.com.au/bbs/search.php?mod=forum&adv=yes'
SEARCH='https://www.oursteps.com.au/bbs/search.php?mod=forum'
class SearchExpired(ParseError):pass

SCHEMA='''CREATE TABLE IF NOT EXISTS fallback_dates(tid INTEGER PRIMARY KEY,created_at_raw TEXT,source TEXT);
CREATE TABLE IF NOT EXISTS inventory_original(tid INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS fallback_sources(tid INTEGER PRIMARY KEY,shard TEXT);
CREATE TABLE IF NOT EXISTS discovery_shards(id TEXT PRIMARY KEY,fid INTEGER,seconds INTEGER,before_flag TEXT,state TEXT DEFAULT 'pending',page_url TEXT,attempts INTEGER DEFAULT 0,retry_at REAL DEFAULT 0,error TEXT,seen_tids TEXT DEFAULT '[]');'''

def prepare(store):
    prepare_inventory(store);store.db.executescript(SCHEMA)
    columns={r[1] for r in store.db.execute('PRAGMA table_info(discovery_shards)')}
    for name in ('special','refinement'):
        if name not in columns:store.db.execute('ALTER TABLE discovery_shards ADD COLUMN '+name+' INTEGER NOT NULL DEFAULT 0')
    store.db.commit()
    if not store.setting('fallback_baseline'):
        with store.db:
            store.db.execute('INSERT OR IGNORE INTO inventory_original SELECT tid FROM inventory')
            store.db.execute("INSERT INTO settings VALUES('fallback_baseline','1')")

def form_fields(content):
    soup=soup_of(content);author=soup.select_one('input[name="srchuname"]')
    if author is None:raise ParseError('author_search_form_missing')
    form=author.find_parent('form');fields={}
    for n in form.select('input[name]'):
        if n.get('type')=='hidden' or n.has_attr('checked'):fields[n['name']]=n.get('value','')
    forums=[int(o['value']) for o in form.select('select[name="srchfid[]"] option[value]') if o['value'].isdigit()]
    ranges=[int(o['value']) for o in form.select('select[name="srchfrom"] option[value]') if o['value'].isdigit() and int(o['value'])>0]
    if not forums:raise ParseError('forum_sharding_not_supported')
    return fields,forums,sorted(set(ranges),reverse=True)

def add_shard(store,fid,seconds=0,before='',special=0,refinement=False):
    key='%s:%s:%s'%(fid,seconds,before or 'recent')+(':special'+str(special) if special else '')
    store.db.execute('INSERT OR IGNORE INTO discovery_shards(id,fid,seconds,before_flag,special,refinement) VALUES(?,?,?,?,?,?)',(key,fid,seconds,before,special,int(refinement)))
    return key

class Transport(Fetcher):
    def __init__(self,store):super().__init__(store);self.daily_now=True
    def search(self,url=FORM,data=None):
        self.policy()
        for _ in range(4):
            u=urlsplit(url)
            if u.scheme!='https' or u.netloc!='www.oursteps.com.au' or u.path!='/bbs/search.php':raise Blocked('search_redirect_outside_normal_search')
            if not longest_rule_allowed(self.rules,url):raise Blocked('robots_disallow')
            pace=max(5,self.delay,random.uniform(2,5),float(self.store.setting('adaptive_delay','0')))
            self.sleep(max(0,pace-(time.time()-float(self.store.setting('last_request','0')))))
            start=time.time();self.store.set_setting('last_request',start)
            try:r=self.open_response(Request(url,data=urlencode(data).encode() if data else None,headers={'User-Agent':AGENT}),timeout=30)
            except HTTPError as e:r=e
            with r:body=self.read_response(r,5*1024*1024+1);code=r.code;headers=r.headers
            self.store.snapshot(url,body,status=code,mode=self.mode)
            self.store.set_setting('adaptive_delay',min(120,max((time.time()-start)*2,float(self.store.setting('adaptive_delay','0'))*.9)))
            if code==429:raise VerificationIssue('retry_later','search_http_429',True,retry_after(headers.get('Retry-After')))
            if code>=500:raise Busy('search_http_'+str(code))
            if code in (401,403):raise VerificationIssue('permission_denied','search_http_'+str(code))
            if code in (301,302,303):url=urljoin(url,headers.get('Location',''));data=None;continue
            if code!=200 or len(body)>5*1024*1024:raise ParseError('search_response_invalid')
            guard(soup_of(body))
            return body,url
        raise ParseError('search_redirect_limit')

def results(content,url):
    soup=soup_of(content);guard(soup);text=soup.get_text(' ',strip=True)
    if any(x in text for x in ('搜索结果已过期','搜索已过期','搜索ID不存在','请重新搜索')):raise SearchExpired('search_id_expired')
    if b'</html>' not in content.lower():raise Busy('search_incomplete_html')
    if any(x in text for x in ('搜索间隔','稍后再进行搜索','两次搜索','搜索次数')):raise Busy('search_frequency_limit')
    records=[]
    for h in soup.select('h3 a[href]'):
        q=query(h['href']);tid=q.get('tid')
        if not tid:
            m=re.search(r'thread-(\d+)-',h['href']);tid=m.group(1) if m else None
        if not tid or not tid.isdigit():continue
        row=h.find_parent('li')
        if row is None:raise ParseError('search_result_layout_changed')
        owner=any(query(a['href']).get('uid')==str(UID) and a.get_text(strip=True).lower()==USERNAME.lower() for a in row.select('a[href]'))
        if not owner:raise ParseError('search_result_owner_not_verified')
        dated=re.search(r'(?:发表于|发布时间)\s*[:：]?\s*(20\d\d-\d{1,2}-\d{1,2}(?: \d{1,2}:\d{2})?)',row.get_text(' ',strip=True))
        records.append(dict(tid=int(tid),title=h.get_text(' ',strip=True),created_at_raw=dated.group(1) if dated else None))
    if not records and not any(x in text for x in ('没有找到','没有搜索到','无相关结果','没有符合')):raise ParseError('search_result_layout_or_empty_unconfirmed')
    next_url=None
    for a in soup.select('a[href]'):
        if a.get_text(strip=True)=='下一页':next_url=urljoin(url,a['href'])
    capped=any(x in text for x in ('只显示前','最多显示','超过搜索','结果上限'))
    return records,next_url,capped

def merge(store,shard,records):
    for r in records:
        store.db.execute("INSERT OR IGNORE INTO threads(tid,title,discovered_via) VALUES(?,?,'fallback-search')",(r['tid'],r['title']))
        store.db.execute('INSERT INTO inventory VALUES(?,?,?) ON CONFLICT(tid) DO UPDATE SET last_seen=excluded.last_seen',(r['tid'],time.time(),time.time()))
        if r.get('created_at_raw'):
            store.db.execute('INSERT OR REPLACE INTO fallback_dates VALUES(?,?,?)',(r['tid'],r['created_at_raw'],'search_explicit_publication_label'))
        store.db.execute('INSERT OR IGNORE INTO fallback_sources VALUES(?,?)',(r['tid'],shard))

def split(store,shard,ranges,specials=(1,2,3,4,5)):
    smaller=[r for r in ranges if shard['seconds']==0 or r<shard['seconds']]
    if shard['before_flag']=='1' or not smaller:
        # Supported special-thread filters provide narrower subsets, but omit
        # ordinary threads. Keep the parent unresolved: these are NOT full coverage.
        if not shard['special']:
            for special in specials:
                add_shard(store,shard['fid'],shard['seconds'],shard['before_flag'],special,True)
        store.db.execute("UPDATE discovery_shards SET state='unresolved_limited',refinement=1,error='No supported exhaustive split; ordinary-thread remainder unresolved' WHERE id=?",(shard['id'],))
        return
    seconds=max(smaller)
    add_shard(store,shard['fid'],seconds,'',shard['special'],shard['refinement'])
    add_shard(store,shard['fid'],seconds,'1',shard['special'],shard['refinement'])
    store.db.execute("UPDATE discovery_shards SET state='split' WHERE id=?",(shard['id'],))

def refine_limited(store,ranges,specials):
    limited=store.db.execute("SELECT * FROM discovery_shards WHERE state='limited'").fetchall()
    if limited:store.set_setting('fallback_refine_only','1')
    with store.db:
        for shard in limited:
            store.db.execute('UPDATE discovery_shards SET refinement=1 WHERE id=?',(shard['id'],))
            shard=store.db.execute('SELECT * FROM discovery_shards WHERE id=?',(shard['id'],)).fetchone()
            split(store,shard,ranges,specials)
    return store.setting('fallback_refine_only')=='1'

def run(store):
    prepare(store)
    if max(float(store.setting('fallback_pause_until','0')),float(store.setting('pause_until','0')))>time.time():return 'retry_later'
    transport=Transport(store);active=None
    try:
        content,_=transport.search();fields,forums,ranges=form_fields(content)
        specials=sorted({int(n['value']) for n in soup_of(content).select('input[name="special[]"][value]') if n['value'] in ('1','2','3','4','5')})
        refining=refine_limited(store,ranges,specials)
        if not refining:
            with store.db:
                for fid in forums:add_shard(store,fid)
        scope=' AND refinement=1' if refining else ''
        while True:
            shard=store.db.execute("SELECT * FROM discovery_shards WHERE state IN ('pending','retry_later') AND retry_at<=?"+scope+" ORDER BY fid,seconds LIMIT 1",(time.time(),)).fetchone()
            if shard is None:break
            active=shard
            if shard['page_url']:
                content,url=transport.search(shard['page_url'])
            else:
                # Fresh form tokens stay in memory and are never logged.
                content,_=transport.search();fields,_,_=form_fields(content)
                fields.update(srchuname=USERNAME,srchtxt='',srchfilter='all',srchfrom=str(shard['seconds']),before=shard['before_flag'],orderby='dateline',ascdesc='desc',searchsubmit='yes')
                fields['srchfid[]']=str(shard['fid'])
                if shard['special']:
                    if shard['special'] not in specials:raise ParseError('special_filter_no_longer_supported')
                    fields['special[]']=str(shard['special'])
                content,url=transport.search(SEARCH,fields)
            seen=set(json.loads(shard['seen_tids']));visited=set()
            while True:
                if url in visited:raise ParseError('search_pagination_loop')
                visited.add(url);rows,nxt,capped=results(content,url)
                seen.update(r['tid'] for r in rows)
                # Conservatively treat a full-size result ceiling as incomplete.
                capped=capped or len(seen)>=500 or len(visited)>=100
                with store.db:
                    merge(store,shard['id'],rows)
                    store.db.execute('UPDATE discovery_shards SET page_url=?,seen_tids=?,retry_at=0,error=NULL WHERE id=?',(nxt,json.dumps(sorted(seen)),shard['id']))
                    if capped:split(store,shard,ranges,specials)
                    elif not nxt:store.db.execute("UPDATE discovery_shards SET state='complete' WHERE id=?",(shard['id'],))
                if capped or not nxt:break
                content,url=transport.search(nxt)
        return 'finished_available_shards; check limited/failed shards before claiming completeness'
    except (VerificationIssue,Busy,Blocked,ParseError,URLError,TimeoutError,ConnectionError,OSError) as e:
        retry=isinstance(e,(SearchExpired,Busy,URLError,TimeoutError,ConnectionError,OSError)) or (isinstance(e,VerificationIssue) and e.retry)
        state='retry_later' if retry else ('permission_or_challenge' if isinstance(e,Blocked) else (e.category if isinstance(e,VerificationIssue) else 'parse_error'))
        delay=max(getattr(e,'retry_after',0),min(3600,300*2**min(active['attempts'] if active else 0,4))*random.uniform(1,1.3))
        if retry:
            store.set_setting('fallback_pause_until',time.time()+delay)
            store.set_setting('pause_until',max(float(store.setting('pause_until','0')),time.time()+delay))
        with store.db:
            if active and isinstance(e,SearchExpired):store.db.execute('UPDATE discovery_shards SET page_url=NULL WHERE id=?',(active['id'],))
            if active:store.db.execute('UPDATE discovery_shards SET state=?,attempts=attempts+1,retry_at=?,error=? WHERE id=?',(state,time.time()+delay if retry else 0,str(e),active['id']))
        atomic_write(store.root/'fallback-error.json',json.dumps(dict(state=state,error=str(e),shard=active['id'] if active else None)).encode())
        return state

def status(store):
    if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='discovery_shards'").fetchone():return dict(original_personal_tids=store.db.execute('SELECT count(*) FROM inventory').fetchone()[0],additional_fallback_tids=0,shards_complete=0,shards_remaining=0,completeness="NO")
    original=store.db.execute('SELECT count(*) FROM inventory_original').fetchone()[0]
    additional=store.db.execute('SELECT count(*) FROM fallback_sources f WHERE NOT EXISTS(SELECT 1 FROM inventory_original o WHERE o.tid=f.tid)').fetchone()[0]
    states=dict(store.db.execute('SELECT state,count(*) FROM discovery_shards GROUP BY state'))
    return dict(original_personal_tids=original,additional_fallback_tids=additional,shards_complete=states.get('complete',0),shards_remaining=sum(n for k,n in states.items() if k not in ('complete','split')),shard_states=states,completeness='YES' if states.get('complete',0)>0 and all(k in ('complete','split') for k in states) else 'NO')
