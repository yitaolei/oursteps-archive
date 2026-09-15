"""Bounded, authenticated guide inventory passes; no thread-body fetching."""
import json
import random
import re
import time
from urllib.parse import parse_qs, urlsplit
from urllib.error import URLError
from .parser import soup_of, query, guard, ParseError, Blocked, Busy
from .auth_verify import inspect_response, VerificationIssue


def guide_url(page):
    return 'https://www.oursteps.com.au/bbs/forum.php?mod=guide&view=my&type=thread&page='+str(page)


def is_guide_url(url):
    u=urlsplit(url);q=parse_qs(u.query,keep_blank_values=True)
    return (u.scheme=='https' and u.netloc=='www.oursteps.com.au' and u.path=='/bbs/forum.php'
            and not u.fragment and set(q)=={'mod','view','type','page'}
            and q['mod']==['guide'] and q['view']==['my'] and q['type']==['thread']
            and len(q['page'])==1 and re.fullmatch(r'[1-9][0-9]*',q['page'][0]) is not None)


def parse_guide(content,url):
    if not is_guide_url(url):raise ParseError('not_owner_guide_directory')
    inspect_response(content)
    s=soup_of(content);guard(s)
    if b'</html>' not in content.lower():raise ParseError('guide_incomplete_html')
    if s.select_one('#messagetext, #main_message, .alert_error, .alert_info'):
        raise ParseError('guide_error_layout')
    # Require the authenticated guide/my/thread layout.
    nav=any(query(a.get('href','')).get('mod')=='guide'
            and query(a.get('href','')).get('view')=='my'
            and query(a.get('href','')).get('type')=='thread'
            for a in s.select('a[href]'))
    root=s.select_one('#threadlist')
    if not nav or root is None:
        raise ParseError('guide_layout_unrecognized')

    current=[n.get_text(strip=True) for n in s.select('.pg > strong')]
    if current and (len(set(current))!=1 or current[0]!=query(url)['page']):
        raise ParseError('guide_page_identity_mismatch')

    # Real OurSteps guide pages contain a separate header/filter table.
    # Thread rows are identified by tbody id="normalthread_<tid>".
    rows=root.select('tbody[id^="normalthread_"]')
    threads={}

    for row in rows:
        rid=row.get('id','')
        m=re.fullmatch(r'normalthread_([1-9][0-9]*)',rid)
        if not m:
            raise ParseError('guide_invalid_thread_row')

        tid=int(m.group(1))
        title_link=None

        # A row can contain several viewthread links:
        # icon, title, page-number links, reply/view links.
        # Use the textual page-1 link inside the subject <th>.
        for a in row.select('th a[href]'):
            q=query(a.get('href',''))
            if q.get('mod')!='viewthread' or q.get('tid')!=str(tid):
                continue
            if q.get('page') not in (None,'1'):
                continue
            if not a.get_text(' ',strip=True):
                continue
            title_link=a
            break

        if title_link is None:
            raise ParseError('guide_thread_title_missing')

        fid=None
        for a in row.select('a[href]'):
            q=query(a.get('href',''))
            if (q.get('mod')=='forumdisplay'
                    and q.get('fid','').isdigit()
                    and int(q['fid'])>0):
                fid=int(q['fid'])
                break

        threads.setdefault(
            tid,
            dict(
                tid=tid,
                title=title_link.get_text(' ',strip=True) or None,
                fid=fid,
            )
        )

    if not threads:
        # A normal authenticated EOF page has no normalthread rows and
        # contains the standard guide empty marker in #threadlist.
        empty=root.select_one('p.emp, .emp')
        text=empty.get_text(' ',strip=True) if empty is not None else ''
        if rows or empty is None or not re.search(r'没有帖子|没有相关主题|暂无',text):
            raise ParseError('guide_empty_unconfirmed')

    return dict(threads=list(threads.values()),eof=not threads)



SCHEMA='''CREATE TABLE IF NOT EXISTS guide_progress(
 id INTEGER PRIMARY KEY CHECK(id=1),pass INTEGER DEFAULT 1,next_page INTEGER DEFAULT 1,
 state TEXT DEFAULT 'pending',last_start INTEGER,last_end INTEGER,new_tids INTEGER DEFAULT 0,
 last_success REAL,retry_at REAL DEFAULT 0,attempts INTEGER DEFAULT 0,error TEXT,
 last_nonempty INTEGER DEFAULT 0,eof_page INTEGER);
 INSERT OR IGNORE INTO guide_progress(id) VALUES(1);
 CREATE TABLE IF NOT EXISTS guide_pages(page INTEGER PRIMARY KEY,tids TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS guide_tids(tid INTEGER PRIMARY KEY);
'''


def progress(store):
    return dict(store.db.execute('SELECT * FROM guide_progress WHERE id=1').fetchone())


def discover(store,now=False,max_pages=None,max_minutes=None):
    from .fetch import Fetcher, OutsideWindow
    from .history import prepare
    if any(v is not None and (type(v) is not int or v<=0) for v in (max_pages,max_minutes)):
        raise ValueError('guide limits must be positive integers')
    deadline=time.monotonic()+60*max_minutes if max_minutes is not None else None
    prepare(store);store.db.executescript(SCHEMA);state=progress(store)
    if state['state'] in ('pass_complete','anchor_mismatch'):return state['state']
    if store.setting('halt'):return 'halted'
    if max(state['retry_at'],float(store.setting('pause_until','0')))>time.time():return 'retry_later'
    frontier=state['next_page'];page=max(1,frontier-2)
    anchors=set()
    for row in store.db.execute('SELECT tids FROM guide_pages WHERE page>=? AND page<?',(page,frontier)):
        anchors.update(json.loads(row[0]))
    observed=set();count=0;new=0
    with store.db:store.db.execute("UPDATE guide_progress SET last_start=?,last_end=NULL,new_tids=0,state='running' WHERE id=1",(page,))
    def stop(category,error=None,retry=0):
        with store.db:store.db.execute('UPDATE guide_progress SET state=?,error=?,retry_at=? WHERE id=1',(category,error,retry))
        return category
    try:
        fetch=Fetcher(store);fetch.daily_now=now
        if fetch.mode!='authenticated_private':raise Blocked('guide_requires_authenticated_session')
        while True:
            if (max_pages is not None and count>=max_pages) or (deadline is not None and time.monotonic()>=deadline):
                return stop('budget_reached')
            if page>=frontier and frontier>1 and (not anchors or not (anchors & observed)):
                return stop('anchor_mismatch','prior overlap anchors missing; manual resync required')
            url=guide_url(page);snap=fetch.get(url);parsed=parse_guide(store.raw(snap),url)
            tids=[t['tid'] for t in parsed['threads']];observed.update(tids)
            if parsed['eof'] and (not state['last_nonempty'] and not observed):
                raise ParseError('guide_empty_without_history')
            if parsed['eof'] and page<frontier:
                return stop('anchor_mismatch','empty overlap before prior frontier')
            stamp=time.time()
            with store.db:
                for t in parsed['threads']:
                    store.db.execute("INSERT OR IGNORE INTO threads(tid,title,fid,discovered_via) VALUES(?,?,?,'guide-v2')",(t['tid'],t['title'],t['fid']))
                    new+=store.db.execute('INSERT OR IGNORE INTO inventory VALUES(?,?,?)',(t['tid'],stamp,stamp)).rowcount
                    store.db.execute('UPDATE inventory SET last_seen=? WHERE tid=?',(stamp,t['tid']))
                    store.db.execute('INSERT OR IGNORE INTO guide_tids VALUES(?)',(t['tid'],))
                # Freeze prior anchors until continuity is proven; interrupted overlap is replayed.
                if page>=frontier:
                    store.db.execute('INSERT OR REPLACE INTO guide_pages VALUES(?,?)',(page,json.dumps(tids)))
                store.db.execute('UPDATE guide_progress SET next_page=MAX(next_page,?),last_end=?,new_tids=?,last_success=?,attempts=0,retry_at=0,error=NULL,last_nonempty=MAX(last_nonempty,?),eof_page=? WHERE id=1',
                                 (page+1,page,new,stamp,page if tids else 0,page if parsed['eof'] else None))
                if parsed['eof']:store.db.execute("UPDATE guide_progress SET state='pass_complete' WHERE id=1")
            count+=1
            if parsed['eof']:return 'pass_complete'
            page+=1
    except OutsideWindow:return stop('waiting_window')
    except (Busy,URLError,TimeoutError,ConnectionError,OSError,VerificationIssue,ParseError) as error:
        transient=isinstance(error,(Busy,URLError,TimeoutError,ConnectionError,OSError)) or isinstance(error,VerificationIssue) and error.retry
        category='retry_later' if transient else (error.category if isinstance(error,VerificationIssue) else 'parse_error')
        attempts=state['attempts']+1
        delay=max(getattr(error,'retry_after',0),min(3600,60*2**min(attempts,6))*random.uniform(1,1.3))
        with store.db:store.db.execute('UPDATE guide_progress SET attempts=? WHERE id=1',(attempts,))
        if transient:store.set_setting('pause_until',time.time()+delay)
        paused=transient or isinstance(error,Blocked) or category in ('permission_denied','manual_required')
        if paused and not transient:delay=max(delay,3600)
        return stop(category,str(error),time.time()+delay if paused else 0)
