from .auth_verify import VerificationIssue
import argparse
import http.client
import fcntl
import json
import logging
import os
import re
import socket
import sys
import time
from pathlib import Path
from urllib.error import URLError
from .fetch import Fetcher, OutsideWindow, NotFound, RateLimited
from .parser import Blocked, Busy, parse_discovery, parse_thread, query
from .store import Store, atomic_write

ROOT = Path(__file__).resolve().parent.parent

def compact(report):
    return {k:report[k] for k in ('threads','complete_threads','author_posts',
                                 'parsed_pages','pending_jobs','failures','gaps','halt')}

def run(store, offline=False, wait=False, tids=None, fetcher_override=None, deadline=None):
    if store.setting('halt') and not offline:
        return 'halted'
    with store.db:
        run_id = store.db.execute('INSERT INTO runs(started_at,status) VALUES(?,"running")',
                                  (time.time(),)).lastrowid
    fetcher = fetcher_override
    status = 'complete'
    atomic_write(store.root/'run-status.json',json.dumps(dict(
        status='running_or_waiting_window',updated_at=time.time(),
        **compact(store.report())),ensure_ascii=False,indent=2).encode())
    try:
        while True:
            if deadline is not None and time.monotonic()>=deadline:
                status='budget_reached';break
            clause = ' AND url IN (SELECT url FROM snapshots)' if offline else ''
            if tids is not None:
                clause += ' AND tid IN ('+','.join(str(int(t)) for t in tids or [0])+')'
            job = store.db.execute('SELECT * FROM jobs WHERE state IN ("pending","retry_later")'+clause+' ORDER BY retry_at,tid,page LIMIT 1').fetchone()
            if not job:
                if offline and store.db.execute('SELECT count(*) FROM jobs WHERE state="pending"').fetchone()[0]:
                    status = 'offline_missing_raw'
                break
            due = max(job['retry_at'], float(store.setting('pause_until', '0')))
            if not offline and due > time.time():
                if not wait:
                    status = 'retry_pending'
                    break
                delay=min(60,due-time.time())
                if deadline is not None:
                    remaining=deadline-time.monotonic()
                    if remaining<=0 or due-time.time()>=remaining:
                        status='budget_reached';break
                    delay=min(delay,remaining)
                time.sleep(delay)
                continue
            snap = store.latest(job['url'])
            transport_url = job['url']
            try:
                if snap is None or snap['status'] != 200 or job['state']=='retry_later' or job['error']=='reauth_fetch':
                    if offline:
                        status = 'offline_missing_raw'
                        break
                    if fetcher is None:
                        fetcher = Fetcher(store,wait=wait)
                    fetcher.wait_window()
                    if deadline is not None and time.monotonic()>=deadline:
                        status='budget_reached';break
                    with store.db:
                        store.db.execute('UPDATE jobs SET attempts=attempts+1 WHERE url=?',(job['url'],))
                    transport_url = job['url']
                    if (job['kind'] == 'thread'
                            and int(job['page'] or 1) > 1
                            and '&extra=' not in transport_url):
                        transport_url = transport_url.replace(
                            '&page=', '&extra=&page=', 1
                        )
                    snap = fetcher.get(transport_url)
                elif getattr(store,'performance',None) is not None:
                    store.performance.add('cached_page_uses')
                if snap['status'] != 200:
                    raise Blocked('cached_http_%s' % snap['status'])
                canonical = snap['url'] if getattr(store,'historical_mode',False) else transport_url
                parse_url = job['url']
                if canonical != transport_url:
                    requested_q = query(transport_url)
                    canonical_q = query(canonical)

                    requested_tid = requested_q.get('tid', '')
                    requested_page = requested_q.get('page', '1')
                    canonical_tid = canonical_q.get('tid', '')
                    canonical_page = canonical_q.get('page', '1')

                    requested_identity = (
                        int(requested_tid),
                        int(requested_page),
                    ) if requested_tid.isdigit() and requested_page.isdigit() else None

                    canonical_identity = (
                        int(canonical_tid),
                        int(canonical_page),
                    ) if canonical_tid.isdigit() and canonical_page.isdigit() else None

                    # Page-1 URL normalization is fine, but a redirect from
                    # requested page N to a different logical page must never
                    # be silently stored as page N.
                    if requested_identity is None or canonical_identity != requested_identity:
                        raise Busy('canonical_page_mismatch')

                from .performance import measure
                parsed = measure(store, 'parse_seconds', parse_thread, store.raw(snap),parse_url)
                store.save_page(parse_url,parsed,snap)
                if canonical != transport_url:
                    with store.db:
                        store.db.execute("UPDATE jobs SET state='success',error=? WHERE url=?",
                                         ('canonical_redirect:'+canonical,job['url']))
                        store.db.execute('DELETE FROM gaps WHERE url=? AND pid=0',(job['url'],))
                    store.refresh_status([job['tid']])
                if not offline:
                    store.set_setting('error_streak', 0)
                    store.historical_auth_streak = 0
                logging.info('parsed tid=%s page=%s author_posts=%s',job['tid'],job['page'],len(parsed['posts']))
            except VerificationIssue as e:
                state='retry_later' if e.retry else ('auth_required' if e.category=='auth_required' else 'parse_error')
                store.fail(job,e.category+':'+e.reason,retry=e.retry,state=state,retry_after=e.retry_after)
                status=e.category
                if getattr(store,'historical_mode',False):
                    if e.category in ('auth_required','permission_denied','manual_required'):
                        store.historical_auth_streak = getattr(store,'historical_auth_streak',0)+1
                        if store.historical_auth_streak >= 3:
                            store.set_setting('halt','historical_global_auth_or_challenge')
                            status='halted'
                    else:
                        store.historical_auth_streak = 0
                    if e.retry:
                        streak=int(store.setting('error_streak','0'))+1
                        store.set_setting('error_streak',streak)
                        if streak >= 3:store.set_setting('pause_until',time.time()+max(900,e.retry_after))
                break
            except OutsideWindow:
                status = 'waiting_window'
                break
            except (Busy,URLError,TimeoutError,socket.timeout,ConnectionError,http.client.HTTPException) as e:
                store.historical_auth_streak = 0
                error = 'transient:%s' % type(e).__name__
                store.fail(job,error,retry=True,retry_after=getattr(e,'retry_after',0))
                streak = int(store.setting('error_streak','0'))+1
                store.set_setting('error_streak',streak)
                store.set_setting('adaptive_delay',min(120, max(10,float(store.setting('adaptive_delay','0'))*2)))
                if isinstance(e,RateLimited) or streak >= 3:
                    pause = max(getattr(e,'retry_after',0),900 if streak < 6 else 3600)
                    store.set_setting('pause_until',time.time()+pause)
                logging.warning('%s tid=%s page=%s',error,job['tid'],job['page'])
                status = 'retry_later'
                break
            except NotFound as e:
                store.historical_auth_streak = 0
                store.fail(job,str(e),state='not_found')
            except Blocked as e:
                state = 'permanently_skipped' if any(x in str(e) for x in ('robots_', 'off_origin', 'network_route', 'visit_time', 'response_size', 'content_type')) else 'auth_required'
                store.fail(job,str(e),state=state)
                if getattr(store,'historical_mode',False):
                    store.historical_auth_streak = getattr(store,'historical_auth_streak',0)+1 if state=='auth_required' else 0
                    status = state
                    if store.historical_auth_streak >= 3:
                        store.set_setting('halt','historical_global_auth_or_challenge')
                        status='halted'
                else:
                    store.set_setting('halt',str(e))
                    status = 'halted'
                logging.error('page blocked tid=%s page=%s: %s',job['tid'],job['page'],e)
                break
            except ValueError as e:
                store.historical_auth_streak = 0
                store.fail(job,str(e))
                logging.exception('parse failure tid=%s page=%s',job['tid'],job['page'])
                status = 'parse_failed'
                break
            store.report()
    finally:
        report = store.report()
        if status == 'complete' and ((tids is None and report['complete_threads'] != report['threads']) or (tids is not None and any(store.db.execute('SELECT status FROM threads WHERE tid=?',(t,)).fetchone()[0]!='complete' for t in tids))):
            status = 'partial'
        with store.db:
            store.db.execute('UPDATE runs SET finished_at=?,status=?,summary=? WHERE id=?',
                             (time.time(),status,json.dumps(report),run_id))
        atomic_write(store.root/'run-status.json',json.dumps(dict(
            status=status,updated_at=time.time(),**compact(report)),ensure_ascii=False,indent=2).encode())
    return status

def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='Private archive: hard-capped 20-thread pilot')
    parser.add_argument('--data',type=Path,default=ROOT/'data')
    sub = parser.add_subparsers(dest='command',required=True)
    daily = sub.add_parser('sync-today')
    daily.add_argument('--date')
    daily.add_argument('--now',action='store_true')
    sub.add_parser('auth')
    sub.add_parser('init')
    sub.add_parser('seed-spec')
    crawl = sub.add_parser('pilot')
    crawl.add_argument('--offline',action='store_true')
    crawl.add_argument('--wait-window',action='store_true')
    sub.add_parser('report')
    sub.add_parser('resume-auth')
    preview = sub.add_parser('build-preview',help='Full maintenance rebuild unless an incremental selector is supplied')
    selection = preview.add_mutually_exclusive_group()
    selection.add_argument('--tids',nargs='+',type=int,help='Incrementally render these complete TIDs; never fall back to full')
    selection.add_argument('--recovered-unpublished',action='store_true',help='Select complete reconciliation imports absent from public current')
    preview.add_argument('--dry-run',action='store_true',help='List the incremental batch without rendering')
    sub.add_parser('reparse')
    imp = sub.add_parser('import-list')
    imp.add_argument('html',type=Path)
    imp.add_argument('--url',required=True)
    imported = sub.add_parser('import-page')
    imported.add_argument('html',type=Path)
    imported.add_argument('--url',required=True)
    retry = sub.add_parser('retry-parsing')
    retry.add_argument('--reason',required=True)
    args = parser.parse_args()
    if args.command == 'auth':
        from .auth import bootstrap
        bootstrap()
        return
    if sys.platform == 'darwin' and str(args.data.resolve()).startswith('/Volumes/'):
        parser.error('Run on NAS local path, or --data on local Mac disk (test data only).')
    args.data.mkdir(parents=True,exist_ok=True)
    with open(args.data/'worker.lock','a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error('worker active; read pilot-report.json without opening SQLite')
        store = Store(args.data)
        (args.data/'logs').mkdir(exist_ok=True)
        logging.basicConfig(filename=str(args.data/'logs'/'archive.log'),level=logging.INFO,
                            format='%(asctime)s %(levelname)s %(message)s')
        if args.command == 'sync-today':
            from .sync import sync
            result=sync(store,args.date,now=args.now)
            print(json.dumps(result,ensure_ascii=False))
            sys.exit(0 if result['status']=='success' else 2)
        elif args.command == 'build-preview':
            if args.tids is not None or args.recovered_unpublished:
                from .preview_batch import build_batch
                result=build_batch(store,args.tids,dry_run=args.dry_run)
            else:
                if args.dry_run:parser.error('--dry-run requires --tids or --recovered-unpublished')
                from .preview import build
                result=build(store)
            print(json.dumps(result,ensure_ascii=False))
            return
        elif args.command == 'resume-auth':
            from .fetch import session
            halt = store.setting('halt')
            if halt and not store.db.execute('SELECT 1 FROM jobs WHERE state="auth_required" AND error=?',(halt,)).fetchone():
                parser.error('halt is not an authentication stop; review required')
            _, mode = session(store)
            if mode != 'authenticated_private':
                parser.error('configure protected OURSTEPS_COOKIE_FILE first')
            with store.db:
                store.db.execute('UPDATE jobs SET state="pending",error="reauth_fetch",retry_at=0 WHERE state="auth_required"')
            store.set_setting('halt','')
        elif args.command == 'seed-spec':
            tids = re.findall(r'^\| \[(\d+) ',(ROOT/'SPEC.md').read_text(),flags=re.M)
            if len(tids) != 20:
                parser.error('SPEC must contain exactly 20 sample threads')
            store.seed(tids,'SPEC.md section 5')
        elif args.command == 'import-list':
            content = args.html.read_bytes()
            discovered = parse_discovery(content,args.url)
            store.seed([x['tid'] for x in discovered['threads']],'imported_personal_list')
            store.snapshot(args.url,content,mode='imported_private')
            atomic_write(store.root/'discovery.json',json.dumps(discovered,ensure_ascii=False,indent=2).encode())
        elif args.command == 'import-page':
            from .parser import query,thread_url
            q = query(args.url)
            tid,page = int(q['tid']),int(q.get('page',1))
            canonical = thread_url(tid,page)
            store.seed([tid],'imported_page')
            with store.db:
                store.db.execute('INSERT OR IGNORE INTO jobs(url,kind,tid,page) VALUES(?,?,?,?)',
                                 (canonical,'thread',tid,page))
            store.snapshot(canonical,args.html.read_bytes(),mode='imported_private')
        elif args.command == 'retry-parsing':
            if store.setting('halt'):
                parser.error('persistent site halt requires review; no automatic override')
            with store.db:
                store.db.execute('UPDATE jobs SET state="pending",error=NULL WHERE state="parse_error"')
            logging.info('explicit parser retry: %s',args.reason)
        elif args.command in ('pilot','reparse'):
            if args.command == 'reparse':
                # Reparse successful cached pages without erasing completed data or retrying auth pages.
                with store.db:
                    store.db.execute('UPDATE jobs SET state="pending",error=NULL,retry_at=0 WHERE url IN (SELECT url FROM pages WHERE result="parsed")')
            status = run(store,offline=args.command=='reparse' or args.offline,
                         wait=getattr(args,'wait_window',False))
            print(json.dumps(dict(status=status,**compact(store.report())),ensure_ascii=False))
            sys.exit(0 if status=='complete' else 2)
        print(json.dumps(compact(store.report()),ensure_ascii=False))
