#!/usr/bin/env python3
import json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];STATE=ROOT/'data/studio-manual-sync.json'
env_priority=os.environ.copy();env_priority['OURSTEPS_MANUAL_PRIORITY']='1'
result=subprocess.run([sys.executable,str(ROOT/'scripts/studio_primary_cycle.py'),'--execute'],cwd=ROOT,env=env_priority,timeout=1800)
# Recover a bounded batch of verified current-day challenge pages, not just one.
# Every page uses the existing authorized browser importer and validation.
if result.returncode in (2,3):
 try:
  import re,sqlite3
  env=os.environ.copy();env['OURSTEPS_STUDIO_PRIMARY_EXPERIMENT']='1'
  browser=Path.home()/'.local/share/oursteps-runtime/bin/python'
  attempted=set();recovered=0
  for round_number in range(3):
   receipt=json.loads((ROOT/'data/sync-summary.json').read_text())
   db=sqlite3.connect((ROOT/'data/archive.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
   try:
    ids=[r[0] for r in db.execute("SELECT t.tid FROM threads t JOIN jobs j ON j.tid=t.tid AND j.page=1 WHERE t.status!='complete' AND t.discovered_via='daily-challenge' AND j.state='retry_later' ORDER BY t.tid DESC LIMIT 40")]
   finally:db.close()
   match=re.search(r'[?&]tid=(\d+)(?:&|$)',receipt.get('browser_fallback_url',''))
   if match:ids.append(int(match.group(1)))
   targets=[tid for tid in dict.fromkeys(ids) if tid not in attempted][:20]
   if not targets:break
   for tid in targets:
    attempted.add(tid)
    attempt=subprocess.run([str(browser),str(ROOT/'scripts/studio_browser_recover_one.py'),str(tid)],cwd=ROOT,env=env,timeout=120)
    if attempt.returncode==0:recovered+=1
   if recovered:
    subprocess.run([sys.executable,str(ROOT/'scripts/studio_flush_browser_preview.py')],cwd=ROOT,env=env,timeout=180,check=True)
   result=subprocess.run([sys.executable,str(ROOT/'scripts/studio_primary_cycle.py'),'--execute'],cwd=ROOT,env={**env,'OURSTEPS_MANUAL_PRIORITY':'1'},timeout=1800)
   print('STUDIO_BATCH_BROWSER_RECOVERY',{'round':round_number+1,'attempted':len(attempted),'recovered':recovered,'cycle_exit':result.returncode},flush=True)
   if result.returncode==0:break
 except (OSError,ValueError,subprocess.TimeoutExpired,subprocess.CalledProcessError) as error:
  print('STUDIO_RECOVERY_DEFERRED',type(error).__name__,flush=True)
# When the current-day listing is partially challenged, publish all already verified
# local articles rather than hiding them behind the next blocked page.
if result.returncode in (2,3):
 try:
  receipt=json.loads((ROOT/'data/sync-summary.json').read_text())
  if receipt.get('status') in ('retry_later','unexpected_layout'):
   env=os.environ.copy();env['OURSTEPS_STUDIO_PRIMARY_EXPERIMENT']='1'
   published=subprocess.run([sys.executable,str(ROOT/'scripts/publish_public.py')],cwd=ROOT,env=env,timeout=900)
   if published.returncode==0:
    subprocess.run([sys.executable,str(ROOT/'scripts/studio_refresh_https.py')],cwd=ROOT,env=env,timeout=60,check=True)
 except (OSError,ValueError,subprocess.TimeoutExpired,subprocess.CalledProcessError) as error:
  print('STUDIO_PARTIAL_PUBLISH_ERROR',type(error).__name__,flush=True)
record={'state':'partial' if result.returncode in (2,3) and json.loads((ROOT/'data/sync-summary.json').read_text()).get('status') in ('retry_later','unexpected_layout') else ('succeeded' if result.returncode==0 else 'failed'),'finished_at':time.time(),'exit_code':result.returncode,'message':'Some new articles deferred due to access challenge; retry scheduled' if result.returncode==3 else ('Studio sync and publish completed' if result.returncode==0 else 'See Studio manual sync log')}
current=json.loads(STATE.read_text()) if STATE.exists() else {}
if current.get('pid')==os.getpid():
 current.update(record);tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(current));os.replace(tmp,STATE)
print(json.dumps(record),flush=True)
