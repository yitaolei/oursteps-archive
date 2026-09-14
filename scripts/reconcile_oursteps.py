#!/usr/bin/env python3
"""NAS-only, manually invoked reconciliation. Audit never imports or publishes."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]
from oursteps.reconcile import State,locked,LimitedTransport,audit,verify_pending,backfill
from oursteps.store import Store,atomic_write
from oursteps.manual_missing import archive_db


def arguments(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--keyword');mode.add_argument('--sweep',action='store_true')
    mode.add_argument('--status',action='store_true');mode.add_argument('--backfill-missing',action='store_true')
    p.add_argument('--limit',type=int,default=200,help='maximum search results processed this run')
    p.add_argument('--max-backfill',type=int,default=10,help='Maximum candidate threads attempted, not guaranteed imports; also bounded by request/time budgets')
    p.add_argument('--verify-limit',type=int,default=4,help='actual first-page owner probes during audit; never imports')
    p.add_argument('--max-seconds',type=int,default=300,help='Run time budget in seconds (default 300); in-flight requests/pacing may finish afterward')
    p.add_argument('--max-requests','--max-logical-requests',dest='max_requests',type=int,default=40,help='Logical search/get call budget (default 40), not thread count; robots/redirects can add network requests')
    p.add_argument('--wait',action='store_true',help='wait within time budget for persisted retry deadlines')
    a=p.parse_args(argv)
    for name in ('limit','max_backfill','max_seconds','max_requests'):
        if getattr(a,name)<=0:p.error(name+' must be positive')
    if a.verify_limit<0:p.error('verify-limit must be nonnegative')
    if a.keyword is not None and not a.keyword.strip():p.error('keyword must not be empty')
    return a


def main(argv=None):
    args=arguments(argv)
    if sys.platform=='darwin' or str(ROOT).startswith('/Volumes/'):
        raise SystemExit('Run via reconcile-oursteps (SSH to NAS); never open NAS SQLite over SMB')
    os.umask(0o077)
    with locked(ROOT/'data/reconcile.lock'):
        state=State(ROOT)
        try:
            if args.status:
                state.compare();print(json.dumps(state.summary(),ensure_ascii=False,indent=2));return
            started_at=time.time()
            retry_wait_seconds=0
            deadline=started_at+args.max_seconds
            # Shared worker exclusion respects daily/publisher work; never clear their halt/backoff.
            with locked(ROOT/'data/worker.lock'):
                db=archive_db(ROOT)
                try:shared=dict(db.execute("SELECT key,value FROM settings WHERE key IN ('halt','pause_until','last_request','adaptive_delay')"))
                finally:db.close()
                if shared.get('halt'):
                    print(json.dumps(dict(run_state='existing_archive_halt_requires_review',live_requests=0)));return
                until=max(float(shared.get('pause_until','0')),float(state.setting('pause_until')))
                if until>time.time():
                    if not args.wait or until>=deadline:
                        print(json.dumps(dict(run_state='retry_later',retry_at=until,live_requests=0)));return
                    wait_start=time.monotonic()
                    while time.time()<until:time.sleep(min(60,until-time.time()))
                    retry_wait_seconds+=time.monotonic()-wait_start
                cache=Store(ROOT/'data/reconcile-cache')
                try:
                    # Reuse pacing evidence without changing main archive settings.
                    for key in ('last_request','adaptive_delay'):
                        cache.set_setting(key,max(float(cache.setting(key,'0')),float(shared.get(key,'0'))))
                    transport=LimitedTransport(cache,deadline,args.max_requests)
                    initial_recovered=state.db.execute("SELECT count(*) FROM candidates WHERE recovery='recovered'").fetchone()[0]
                    run_state='complete'
                    mode='keyword' if args.keyword is not None else 'sweep'
                    keyword=args.keyword or ''
                    remaining=args.limit
                    while True:
                        if args.backfill_missing:
                            run_state=backfill(state,cache,transport,args.max_backfill)
                            break
                        before=state.summary(mode,keyword)['search_results_checked']
                        run_state=audit(state,transport,mode,keyword,remaining)
                        remaining-=state.summary(mode,keyword)['search_results_checked']-before
                        if run_state=='retry_later' and args.wait and remaining>0:
                            until=float(state.setting('pause_until'))
                            if until>=deadline:break
                            wait_start=time.monotonic()
                            while time.time()<until:time.sleep(min(60,until-time.time()))
                            retry_wait_seconds+=time.monotonic()-wait_start
                            continue
                        if run_state not in ('retry_later','review_required'):
                            verify_pending(state,cache,transport,args.verify_limit,mode,keyword)
                        break
                    state.compare()
                    report=state.summary(None if args.backfill_missing else mode,keyword)
                    report.update(run_state=run_state,logical_requests=transport.requests,public_changed=False,
                                  thread_attempts_this_run=transport.thread_attempts,
                                  budget_stop_reason=transport.exhausted_reason,
                                  elapsed_seconds=round(time.time()-started_at,3),
                                  limits=dict(max_backfill_threads=args.max_backfill,max_logical_requests=args.max_requests,max_seconds=args.max_seconds),
                                  imported_this_run=max(0,report['recovered']-initial_recovered))
                    report['performance']=dict(scope='backfill_thread_attempts',threads=transport.performance_threads, totals={key:round(sum(row.get(key,0) for row in transport.performance_threads),6) for key in ('logical_requests','network_fetches','thread_page_fetches','repeated_page_fetches','successful_page_refetches','network_errors','network_timeouts','prior_snapshot_fetches','network_seconds','throttle_seconds','parse_seconds','persistence_seconds','import_seconds','total_seconds','cached_page_uses','fetch_seconds')})
                    for row in transport.performance_threads:
                        for key,value in row.items():
                            if key.startswith('persistence_') and key!='persistence_seconds':
                                totals=report['performance']['totals'];totals[key]=totals.get(key,0)+value
                    report['performance']['totals']['retry_wait_seconds']=round(retry_wait_seconds,6)
                    report['performance']['totals']['outside_threads_seconds']=round(max(0,report['elapsed_seconds']-report['performance']['totals']['total_seconds']),6)
                    if args.backfill_missing:
                        report['next_publish']='Incremental preview: python3 archive.py build-preview --recovered-unpublished; then python3 scripts/publish_public.py (run separately when ready)'
                    atomic_write(ROOT/'data/reconcile-last-report.json',json.dumps(report,ensure_ascii=False,indent=2).encode())
                    print(json.dumps(report,ensure_ascii=False,indent=2))
                finally:cache.db.close()
        finally:state.db.close()

if __name__=='__main__':
    try:main(json.load(sys.stdin) if sys.argv[1:]==['--stdin-args'] else None)
    except BlockingIOError:raise SystemExit('Another worker is active; committed checkpoints are preserved. Retry later.')
    except KeyboardInterrupt:raise SystemExit('Paused; rerun the same command to resume committed checkpoints.')
    except Exception as error:raise SystemExit('Stopped safely: '+type(error).__name__+'; no credentials logged. Use --status for persisted item errors.')
