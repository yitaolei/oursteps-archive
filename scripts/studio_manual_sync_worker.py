#!/usr/bin/env python3
import json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];STATE=ROOT/'data/studio-manual-sync.json'
env_priority=os.environ.copy();env_priority['OURSTEPS_MANUAL_PRIORITY']='1'
result=subprocess.run([sys.executable,str(ROOT/'scripts/studio_primary_cycle.py'),'--execute'],cwd=ROOT,env=env_priority,timeout=1800)
# One bounded, authenticated local browser recovery for newly deferred first pages.
if result.returncode in (2,3):
 try:
  # CLI uses exit 2 for retry_later before it reaches the publish step.
  # Only attempt authorized browser recovery when the structured receipt
  # explicitly supplies a fallback thread URL.
  summary=json.loads((ROOT/'data/sync-summary.json').read_text())
  if result.returncode==2 and (summary.get('status') not in ('retry_later','unexpected_layout') or not summary.get('browser_fallback_url')):
   raise ValueError('no_browser_recovery_target')
  import sqlite3
  d=sqlite3.connect((ROOT/'data/archive.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
  ids=[r[0] for r in d.execute("SELECT t.tid FROM threads t JOIN jobs j ON j.tid=t.tid AND j.page=1 WHERE t.status!='complete' AND t.discovered_via='daily-challenge' AND j.state='retry_later' ORDER BY t.tid DESC LIMIT 1")]
  d.close()
  if not ids:
   import re
   summary=json.loads((ROOT/'data/sync-summary.json').read_text())
   match=re.search(r'[?&]tid=(\d+)(?:&|$)',summary.get('browser_fallback_url',''))
   if match: ids=[int(match.group(1))]
  if ids:
   browser=Path.home()/'.local/share/oursteps-runtime/bin/python'
   env=os.environ.copy();env['OURSTEPS_STUDIO_PRIMARY_EXPERIMENT']='1'
   attempt=subprocess.run([str(browser),str(ROOT/'scripts/studio_browser_recover_one.py'),str(ids[0])],cwd=ROOT,env=env,timeout=120)
   if attempt.returncode==0:
    flushed=subprocess.run([sys.executable,str(ROOT/'scripts/studio_flush_browser_preview.py')],cwd=ROOT,env=env,timeout=180)
    if flushed.returncode==0:
     result=subprocess.run([sys.executable,str(ROOT/'scripts/studio_primary_cycle.py'),'--execute'],cwd=ROOT,env={**env,'OURSTEPS_MANUAL_PRIORITY':'1'},timeout=1800)
     print('STUDIO_BROWSER_RECOVERY_RESULT',result.returncode,flush=True)
 except (OSError,ValueError,subprocess.TimeoutExpired) as error:
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
