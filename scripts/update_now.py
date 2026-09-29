#!/usr/bin/env python3
"""Mac launcher: keep SQLite on NAS local filesystem, no model/service dependency."""
import argparse
import fcntl
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from oursteps.deployment import deployment, remote_python

# oursteps-update-lock-v1
LOCK_PATH = Path.home()/'.local/share/oursteps-runtime/update-now.lock'
LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)

_LOCK_FILE = LOCK_PATH.open('a+')

try:
    fcntl.flock(
        _LOCK_FILE.fileno(),
        fcntl.LOCK_EX | fcntl.LOCK_NB
    )
except BlockingIOError:
    print('OurSteps update already running; skipping this run.')
    raise SystemExit(0)

_LOCK_FILE.seek(0)
_LOCK_FILE.truncate()
_LOCK_FILE.write(str(os.getpid()) + '\n')
_LOCK_FILE.flush()

p=argparse.ArgumentParser()
p.add_argument('--date');p.add_argument('--now',action='store_true');p.add_argument('--nightly',action='store_true')
a=p.parse_args()
runtime=Path.home()/'.local/share/oursteps-runtime/bin/python'
if not runtime.exists(): raise SystemExit('Run Authenticate OurSteps.command once to install the local runtime')
auth=subprocess.run([str(runtime),str(ROOT/'archive.py'),'auth'],stdin=subprocess.DEVNULL,capture_output=True,text=True)
auth_log=ROOT/'data/logs/auth-launcher.log'
auth_log.parent.mkdir(exist_ok=True)
with auth_log.open('a') as f:f.write(auth.stdout+auth.stderr)
if auth.returncode:
    print((auth.stdout+auth.stderr).strip())
    raise SystemExit(auth.returncode)
print('Authentication: success (saved session verified)',flush=True)
# CLI is intentionally fixed, all date inputs validated before shell transport.
dates=[dt.date.fromisoformat(a.date).isoformat()] if a.date else [None]
if a.nightly:
    sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'.deps'))
    from oursteps.sync import sydney_today
    today=dt.date.fromisoformat(sydney_today())
    dates=[(today-dt.timedelta(days=1)).isoformat(),today.isoformat()]
code=0
MAX_BROWSER_BATCHES=2
log=ROOT/'data/logs/update-launcher.log';log.parent.mkdir(exist_ok=True)

def remote_sync(day):
    command=remote_python('archive.py','sync-today')
    if day:command+=' --date '+day
    if a.now:command+=' --now'
    return subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',deployment()[0],command],capture_output=True,text=True)

def summary_from(result):
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError,IndexError):
        return None

def remote_candidates(seed):
    host,_=deployment()
    run=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',host,
        remote_python('scripts/browser_fallback_candidates.py')],capture_output=True,text=True,timeout=30)
    if run.returncode:
        return [seed]
    try:
        urls=json.loads(run.stdout.strip().splitlines()[-1]).get('urls',[])
    except (ValueError,IndexError,AttributeError):
        urls=[]
    urls=[u for u in urls if isinstance(u,str)]
    if seed not in urls: urls.insert(0,seed)
    return list(dict.fromkeys(urls))[:20]

def show(summary):
    print('OurSteps Sync · '+summary['date'])
    for label,key in [('Discovered today','discovered_today'),('Already archived','already_archived'),('Newly archived','newly_archived'),('Updated metadata','updated_metadata'),('Total archive','total_archive')]:
        print('%s: %s'%(label,summary.get(key,0)))
    print('Status: '+summary['status'])
    print('Failed: '+str(len(summary['failures'])))
    if summary['failures']: print(json.dumps(summary['failures'],ensure_ascii=False))
    elif summary['newly_archived']==0:print('No new threads found.')
    else:print('Done.')

for day in dates:
    attempted=set()
    result=None
    summary=None
    for _ in range(MAX_BROWSER_BATCHES+1):
        result=remote_sync(day)
        with log.open('a') as f:f.write(result.stderr)
        summary=summary_from(result)
        if summary is None:
            print('Sync could not complete. Check NAS connection and data/logs/update-launcher.log.')
            break
        if result.returncode==0:
            break
        fallback=summary.get('browser_fallback_url')
        if not fallback or fallback in attempted or len(attempted)>=20:
            break
        urls=[u for u in remote_candidates(fallback) if u not in attempted]
        if not urls:
            break
        attempted.update(urls)
        print('Browser fallback: resolving %d thread(s) in one Chrome session.'%len(urls),flush=True)
        fb=subprocess.run([str(runtime),str(ROOT/'scripts/browser_fallback.py'),*urls],
                          capture_output=True,text=True,timeout=600)
        with log.open('a') as f:
            f.write(fb.stdout)
            f.write(fb.stderr)
        if fb.returncode:
            print('Browser fallback failed; see data/logs/update-launcher.log.')
            break
        print('Browser fallback: batch resolved; retrying incremental sync.',flush=True)
    if summary is None or result is None:
        code=max(code,2)
        break
    show(summary)
    code=max(code,result.returncode)
    if result.returncode:
        break
    if summary['status']=='success' and not summary['failures']:
        publish=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',deployment()[0],
            remote_python('scripts/publish_public.py')],capture_output=True,text=True)
        with log.open('a') as f:f.write(publish.stdout+publish.stderr)
        print('Public publish: '+('success' if publish.returncode==0 else 'FAILED; private sync preserved; see update-launcher.log'))
        code=max(code,publish.returncode)

sys.exit(code)
