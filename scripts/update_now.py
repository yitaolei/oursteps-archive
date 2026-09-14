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
for day in dates:
    command=remote_python('archive.py', 'sync-today')
    if day:command+=' --date '+day
    if a.now:command+=' --now'
    result=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',deployment()[0],command],capture_output=True,text=True)
    code=max(code,result.returncode)
    log=ROOT/'data/logs/update-launcher.log';log.parent.mkdir(exist_ok=True)
    with log.open('a') as f:f.write(result.stderr)
    try:
        summary=json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError,IndexError):
        print('Sync could not complete. Check NAS connection and data/logs/update-launcher.log.');break
    print('OurSteps Sync · '+summary['date'])
    for label,key in [('Discovered today','discovered_today'),('Already archived','already_archived'),('Newly archived','newly_archived'),('Updated metadata','updated_metadata'),('Total archive','total_archive')]:
        print('%s: %s'%(label,summary.get(key,0)))
    print('Status: '+summary['status'])
    print('Failed: '+str(len(summary['failures'])))
    if summary['failures']: print(json.dumps(summary['failures'],ensure_ascii=False))
    elif summary['newly_archived']==0:print('No new threads found.')
    else:print('Done.')
    if result.returncode:break
    if summary['status']=='success' and not summary['failures']:
        publish=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',deployment()[0],
            remote_python('scripts/publish_public.py')],capture_output=True,text=True)
        with log.open('a') as f:f.write(publish.stdout+publish.stderr)
        print('Public publish: '+('success' if publish.returncode==0 else 'FAILED; private sync preserved; see update-launcher.log'))
        code=max(code,publish.returncode)

sys.exit(code)
