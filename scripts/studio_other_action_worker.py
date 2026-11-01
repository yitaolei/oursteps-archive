#!/usr/bin/env python3
"""Bounded Studio-only manual Guide, Backfill, Publish actions."""
import fcntl,json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];DATA=ROOT/'data';STATE=DATA/'studio-manual-sync.json'
from studio_local_authority import checked_local_data
checked_local_data(DATA)
if os.environ.get('OURSTEPS_STUDIO_WRITER_HANDOVER_APPROVED')!='1':raise SystemExit('Studio authority required')
action=sys.argv[1]
if action not in ('guide-discovery','historical-backfill','publish-release'):raise SystemExit('Action not allowed')
env=os.environ.copy();env['OURSTEPS_STUDIO_PRIMARY_EXPERIMENT']='1';env.pop('OURSTEPS_NAS_HOST',None);env.pop('OURSTEPS_NAS_PROJECT',None)
args={'guide-discovery':['scripts/history_worker.py','guide-discover','--data',str(DATA),'--now','--max-pages','5','--max-minutes','5'], 'historical-backfill':['scripts/studio_primary_backfill_cycle.py'], 'publish-release':['scripts/publish_public.py']}[action]
try:
 if action=='publish-release':
  with (DATA/'studio-primary-cycle.lock').open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   rc=subprocess.run([sys.executable,*args],cwd=ROOT,env=env,timeout=900).returncode
   if rc==0:rc=subprocess.run([sys.executable,'scripts/studio_refresh_https.py'],cwd=ROOT,env=env,timeout=60).returncode
 elif action=='guide-discovery':
  with (DATA/'studio-primary-cycle.lock').open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   rc=subprocess.run([sys.executable,*args],cwd=ROOT,env=env,timeout=420).returncode
 else:rc=subprocess.run([sys.executable,*args],cwd=ROOT,env=env,timeout=1800).returncode
except (BlockingIOError,subprocess.TimeoutExpired):rc=2
waiting=False
if action=='historical-backfill' and rc==0:
 try:
  outcome=json.loads((DATA/'backfill-performance.json').read_text())
  waiting=outcome.get('status')=='waiting_window'
 except (OSError,ValueError,TypeError):pass
result={'state':'deferred' if waiting else ('succeeded' if rc==0 else 'failed'),'finished_at':time.time(),'exit_code':rc,'message':action+' waiting for permitted access window' if waiting else (action+' completed' if rc==0 else action+' failed or busy; check log')}
try:
 current=json.loads(STATE.read_text())
 if current.get('pid')==os.getpid():
  current.update(result);tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(current));os.replace(tmp,STATE)
except (OSError,ValueError):pass
print(json.dumps(result),flush=True)
