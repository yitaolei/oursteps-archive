#!/usr/bin/env python3
import argparse,sys,json,fcntl,logging
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('mode',choices=['discover','backfill','status','fallback','guide-discover']);p.add_argument('--data',type=Path);p.add_argument('--now',action='store_true',help='Start manual discovery/backfill outside the normal window');p.add_argument('--best-effort',action='store_true');p.add_argument('--max-threads',type=int);p.add_argument('--max-minutes',type=int);p.add_argument('--max-pages',type=int);a=p.parse_args()
if any(v is not None and v<=0 for v in (a.max_threads,a.max_minutes,a.max_pages)):p.error('backfill limits must be positive')
if a.max_threads is not None and a.mode!='backfill':p.error('--max-threads requires backfill')
if a.max_pages is not None and a.mode!='guide-discover':p.error('--max-pages requires guide-discover')
if a.max_minutes is not None and a.mode not in ('backfill','guide-discover'):p.error('--max-minutes requires backfill or guide-discover')
if a.now and a.mode not in ('discover','backfill','guide-discover'):p.error('--now requires discovery or backfill')
if a.best_effort and a.mode!='backfill':p.error('--best-effort requires backfill')
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]
if sys.platform=='darwin':raise SystemExit('SQLite worker must run on NAS native filesystem')
from oursteps.store import Store
from oursteps.history import discover,backfill,report
from oursteps.fallback import run as fallback_run
root=a.data or ROOT/'data';root.mkdir(parents=True,exist_ok=True)
(root/'logs').mkdir(exist_ok=True);logging.basicConfig(filename=str(root/'logs/history.log'),level=logging.INFO)
try:
 with open(root/'worker.lock','a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);s=Store(root)
  try:
   if a.mode=='guide-discover':
    from oursteps.guide_discovery import discover as guide_discover, progress
    state=guide_discover(s,now=a.now,max_pages=a.max_pages,max_minutes=a.max_minutes)
    print(json.dumps(dict(guide=progress(s),run_state=state),ensure_ascii=False,indent=2))
   else:
    state=discover(s,now=a.now) if a.mode=='discover' else (backfill(s,now=a.now,best_effort=a.best_effort,max_threads=a.max_threads,max_minutes=a.max_minutes) if a.mode=='backfill' else (fallback_run(s) if a.mode=='fallback' else 'status'))
    print(json.dumps(dict(report(s),run_state=state),ensure_ascii=False,indent=2))
  finally:s.db.close()
except KeyboardInterrupt:print('Paused. Committed progress is preserved; run the same command again.')
except BlockingIOError:raise SystemExit('Another archive worker is running. Try again later.')
except Exception as e:
 logging.exception('history worker stopped');raise SystemExit('Stopped: '+type(e).__name__+'; see data/logs/history.log')
