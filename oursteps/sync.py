"""Date-bounded incremental discovery. No history traversal or model calls."""
import datetime as dt
import http.client
import json
import logging
import os
import random
import re
import time
import pytz
from urllib.error import URLError
from .parser import parse_thread, parse_discovery, soup_of, directory_url, query, thread_url, ParseError, Blocked, Busy
from .fetch import Fetcher, OutsideWindow, RateLimited, NotFound
from .store import atomic_write
from .auth_verify import VerificationIssue

from .dates import SYDNEY, sydney_today, publication_time

def listing_day(value, today):
    match=re.search(r'(20\d\d)-(\d{1,2})-(\d{1,2})',value)
    if match: return dt.date(*map(int,match.groups())).isoformat()
    if '昨天' in value: return (dt.date.fromisoformat(today)-dt.timedelta(days=1)).isoformat()
    if any(x in value for x in ('今天','分钟前','小时前','秒前','刚刚')): return today
    raise ParseError('directory_publication_date_unrecognized')

def dated_listing(content,url,today):
    parsed=parse_discovery(content,url)
    soup=soup_of(content)
    rows={}
    for a in soup.select('table th a[href]'):
        tid=query(a['href']).get('tid')
        if not tid or not tid.isdigit(): continue
        row=a.find_parent('tr')
        day=None  # Personal list's date is LAST REPLY, never publication.
        num=row.select_one('td.num')
        replies=views=None
        if num:
            ra=num.find('a');ve=num.find('em')
            def number(n):
                v=n.get_text(strip=True).replace(',','') if n else ''
                return int(v) if v.isdigit() else None
            replies,views=number(ra),number(ve)
        rows[int(tid)]=dict(day=day,views=views,replies=replies)
    for record in parsed['threads']:
        record.update(rows[record['tid']])
    return parsed

def sync(store,day=None,now=False):
    from .auth import identity
    from .cli import run
    from .preview import build
    today=sydney_today();day=day or today
    # Only current/yesterday or the explicitly requested first sync day, never arbitrary history.
    if day not in (today,(dt.date.fromisoformat(today)-dt.timedelta(days=1)).isoformat(),'2026-09-08') and not store.db.execute('SELECT 1 FROM discovery_state WHERE day=?',(day,)).fetchone():
        raise ValueError('historical_backfill_not_authorized')
    result=dict(date=day,status='success',discovered_today=0,already_archived=0,newly_archived=0,updated_metadata=0,failures=[])
    initially={r[0] for r in store.db.execute("SELECT tid FROM threads WHERE status='complete'")}
    existing_state=store.db.execute('SELECT * FROM discovery_state WHERE day=?',(day,)).fetchone()
    if existing_state and existing_state['state']=='auth_required':
        # A freshly bootstrapped session is required before an auth-failed directory can retry.
        from .auth import STATE
        saved=json.loads(STATE.read_text()) if STATE.exists() else {}
        if saved.get('verified_at',0)<=existing_state['retry_at']:
            result.update(status='auth_required',failures=['Run archive.py auth again']);return finish(store,result,day,initially)
    due=max(float(store.setting('pause_until','0')),existing_state['retry_at'] if existing_state and existing_state['state']=='retry_later' else 0)
    if due>time.time():
        result.update(status='retry_later',failures=['Persistent backoff active']);return finish(store,result,day,initially)
    url=directory_url()  # Re-scan front pages on every run; new threads may have shifted pagination.
    seen=set();discovery_complete=False
    try:
        from .auth import STATE
        saved=json.loads(STATE.read_text()) if STATE.exists() else {}
        verified=saved.get('verified_at',0)
        from .dates import cached_display_offset
        display_offset=cached_display_offset(saved)
        halt=store.setting('halt')
        stopped=store.db.execute('SELECT retry_at FROM jobs WHERE state="auth_required" AND error=?',(halt,)).fetchone() if halt else None
        if stopped and stopped['retry_at']>=verified:
            raise Blocked('auth_required: run archive.py auth again before retry')
        fetch=Fetcher(store);fetch.daily_now=now
        if fetch.mode!='authenticated_private': raise Blocked('auth_required: run archive.py auth')
        # Old pilot auth halt may clear only after a newly verified real directory response.
        while url:
            if url in seen or len(seen)>=100: raise ParseError('directory_pagination_loop_or_safety_limit')
            seen.add(url)
            with store.db:
                store.db.execute('INSERT OR REPLACE INTO discovery_state VALUES(?,?,?,0,?,NULL)',(day,url,'pending',(existing_state['attempts'] if existing_state else 0)+1))
            snap=fetch.get(url);content=store.raw(snap)
            if not identity(soup_of(content)): raise Blocked('auth_required: directory identity mismatch')
            page=dated_listing(content,url,today)
            # Refresh known threads from this listing, including rows beyond the date cutoff.
            # Require a valid pair so incomplete metadata cannot erase known values.
            with store.db:
                for row in page['threads']:
                    if row['fid'] and row['fid'] > 0 and row.get('forum'):
                        store.db.execute('UPDATE threads SET fid=?,forum=? WHERE tid=?',
                                         (row['fid'],row['forum'],row['tid']))
            older=False
            for row in page['threads']:
                tid=row['tid'];first=None;first_snap=None
                known=store.db.execute('SELECT * FROM threads WHERE tid=?',(tid,)).fetchone()
                if known and known['created_at_raw']:
                    source=store.db.execute('SELECT s.* FROM snapshots s JOIN posts p ON p.snapshot_id=s.id WHERE p.tid=? AND p.floor="1#"',(tid,)).fetchone()
                    row['published']=publication_time(known['created_at_raw'],store.raw(source),display_offset)
                    row['day']=row['published'][:10]
                else:
                    first_url=thread_url(tid)
                    first_snap=store.latest(first_url)
                    if first_snap is None or first_snap['status']!=200:
                        first_snap=fetch.get(first_url)
                    try:
                        first=parse_thread(store.raw(first_snap),first_url)
                    except Busy:
                        first_snap=fetch.get(first_url)
                        first=parse_thread(store.raw(first_snap),first_url)
                    except Blocked:
                        if first_snap['fetched_at']>=verified: raise
                        first_snap=fetch.get(first_url)
                        first=parse_thread(store.raw(first_snap),first_url)
                    owner=next((p for p in first['posts'] if p['floor']=='1#'),None)
                    if owner is None: raise ParseError('first_author_floor_missing')
                    row['published']=publication_time(owner['posted_at_raw'],store.raw(first_snap),display_offset)
                    row['day']=row['published'][:10]
                if row['day']<day:
                    older=True;break
                if row['day']!=day: continue
                with store.db:
                    store.db.execute('INSERT OR IGNORE INTO threads(tid,discovered_via,title,fid) VALUES(?,?,?,?)',(tid,'daily:'+day,row['title'],row['fid']))
                    store.db.execute('INSERT OR IGNORE INTO daily_members VALUES(?,?)',(day,tid))
                    store.db.execute('INSERT OR REPLACE INTO publication_times VALUES(?,?)',(tid,row['published']))
                    store.db.execute('INSERT OR IGNORE INTO jobs(url,kind,tid,page) VALUES(?,?,?,1)',(thread_url(tid),'thread',tid))
                    if row['views'] is not None and row['replies'] is not None:
                        store.db.execute('INSERT OR REPLACE INTO listing_metrics VALUES(?,?,?,?)',(tid,row['views'],row['replies'],time.time()))
                        if tid in initially: result['updated_metadata']+=1
                if first is not None:
                    store.save_page(thread_url(tid),first,first_snap)
            if older or not page['next_url']:
                discovery_complete=True;break
            url=page['next_url']
        with store.db: store.db.execute("UPDATE discovery_state SET state='success',error=NULL WHERE day=?",(day,))
        halt=store.setting('halt')
        if halt:
            if not store.db.execute('SELECT 1 FROM jobs WHERE state="auth_required" AND error=?',(halt,)).fetchone():
                raise Blocked('existing_non_auth_halt_requires_review')
            store.set_setting('halt','')
        tids=[r[0] for r in store.db.execute('SELECT tid FROM daily_members WHERE day=?',(day,))]
        # Fresh identity verification allows only this day's failed authentication jobs to resume.
        with store.db:
            for tid in tids:
                store.db.execute('UPDATE jobs SET state="pending",error="reauth_fetch",retry_at=0 WHERE tid=? AND state="auth_required" AND retry_at<?',(tid,verified))
        status=run(store,tids=tids,fetcher_override=fetch)
        if status not in ('complete','success'): result['status']=status
    except VerificationIssue as e:
        result.update(status=e.category,failures=[e.reason])
    except OutsideWindow:
        result.update(status='waiting_window',failures=['Outside permitted UTC window'])
    except (Busy,URLError,TimeoutError,ConnectionError,http.client.HTTPException) as e:
        attempts=(existing_state['attempts'] if existing_state else 0)+1
        delay=max(getattr(e,'retry_after',0),min(3600,60*2**min(attempts,6))*random.uniform(1,1.3))
        streak=int(store.setting('error_streak','0'))+1;store.set_setting('error_streak',streak)
        if isinstance(e,RateLimited) or streak>=3: delay=max(delay,900 if streak<6 else 3600)
        store.set_setting('pause_until',time.time()+delay)
        store.set_setting('adaptive_delay',min(120,max(10,float(store.setting('adaptive_delay','0'))*2)))
        with store.db: store.db.execute('INSERT OR REPLACE INTO discovery_state VALUES(?,?,?,?,?,?)',(day,url,'retry_later',time.time()+delay,attempts,type(e).__name__))
        result.update(status='retry_later',failures=[type(e).__name__])
    except Blocked as e:
        with store.db: store.db.execute('INSERT OR REPLACE INTO discovery_state VALUES(?,?,?,?,0,?)',(day,url,'auth_required',time.time(),str(e)))
        result.update(status='auth_required',failures=[str(e)])
    except (ValueError,NotFound) as e:
        with store.db: store.db.execute('INSERT OR REPLACE INTO discovery_state VALUES(?,?,?,0,0,?)',(day,url,'parse_error',str(e)))
        result.update(status='parse_error',failures=[str(e)])
    result['discovery_complete']=discovery_complete
    return finish(store,result,day,initially)

def finish(store,result,day,initially):
    from .preview import build_incremental
    members={r[0] for r in store.db.execute('SELECT tid FROM daily_members WHERE day=?',(day,))}
    complete={r[0] for r in store.db.execute("SELECT tid FROM threads WHERE status='complete'")}
    result.update(discovered_today=len(members),already_archived=len(members&initially),newly_archived=len((members&complete)-initially),total_archive=len(complete))
    result['failures'] += [dict(r) for r in store.db.execute('SELECT j.tid,j.page,j.state,j.error FROM jobs j JOIN daily_members d ON d.tid=j.tid WHERE d.day=? AND j.state NOT IN ("success","pending")',(day,))]
    build_incremental(store,members);store.report()
    atomic_write(store.root/'sync-summary.json',json.dumps(result,ensure_ascii=False,indent=2).encode())
    logging.info('daily sync summary %s',json.dumps(result,ensure_ascii=False))
    return result
