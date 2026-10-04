#!/usr/bin/env python3
"""Mac launcher: keep SQLite on NAS local filesystem, no model/service dependency."""
import argparse
import fcntl
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from oursteps.deployment import deployment, remote_python
from oursteps.config import UID
from scripts.process_utils import install_signal_cleanup,run_group
install_signal_cleanup()

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
auth_log=ROOT/'data/logs/auth-launcher.log'
auth_log.parent.mkdir(exist_ok=True)
auth_status=ROOT/'data/auth-status.json'
session_state=ROOT/'.secrets/session.json'
AUTH_REUSE_SECONDS=15*60
AUTH_CLOUDFRONT_GRACE_SECONDS=36*60*60
status={}
session={}
recent_auth=False

def trusted_session_for_cloudfront(saved,now=None):
    now=time.time() if now is None else now
    try:
        age=now-float(saved.get('verified_at',0))
    except (TypeError,ValueError):
        return False
    return (
        saved.get('uid')==UID and
        isinstance(saved.get('cookies'),list) and bool(saved.get('cookies')) and
        saved.get('display_timezone_name')=='Australia/Sydney' and
        0<=age<=AUTH_CLOUDFRONT_GRACE_SECONDS
    )

try:
    status=json.loads(auth_status.read_text()) if auth_status.exists() else {}
    session=json.loads(session_state.read_text()) if session_state.exists() else {}
    payload=json.dumps(session,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    digest=hashlib.sha256(payload).hexdigest()
    age=time.time()-float(status.get('at',0))
    recent_auth=(status.get('category')=='success' and session.get('uid')==status.get('uid') and
                 status.get('session_sha256')==digest and 0<=age<=AUTH_REUSE_SECONDS)
except (OSError,ValueError,TypeError):
    recent_auth=False
if recent_auth:
    print('Authentication: recent verified session reused',flush=True)
else:
    try:
        auth=run_group([str(runtime),str(ROOT/'archive.py'),'auth'],
            stdin=subprocess.DEVNULL,text=True,timeout=180)
    except subprocess.TimeoutExpired:
        with auth_log.open('a') as f:
            f.write(dt.datetime.now().isoformat()+' auth timeout after 180 seconds; saved session preserved\n')
        print('Authentication timed out after 180 seconds; saved session preserved.')
        raise SystemExit(75)
    with auth_log.open('a') as f:f.write(auth.stdout+auth.stderr)
    if auth.returncode:
        try:
            latest=json.loads(auth_status.read_text()) if auth_status.exists() else {}
        except (OSError,ValueError):
            latest={}
        cloudfront_grace=(
            latest.get('category')=='retry_later' and
            latest.get('reason')=='cloudfront_403' and
            trusted_session_for_cloudfront(session)
        )
        if cloudfront_grace:
            print('Authentication preflight: CloudFront 403; using last verified session and continuing to NAS verification.',flush=True)
        else:
            print((auth.stdout+auth.stderr).strip())
            raise SystemExit(auth.returncode)
    else:
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
    return run_group(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',deployment()[0],command],text=True,timeout=900)

def summary_from(result):
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError,IndexError):
        return None

def remote_candidates(seed):
    host,_=deployment()
    run=run_group(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',host,
        remote_python('scripts/browser_fallback_candidates.py')],text=True,timeout=30)
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
        fb=run_group([str(runtime),str(ROOT/'scripts/browser_fallback.py'),*urls],
                     text=True,timeout=600)
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
        publish=run_group(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',deployment()[0],
            remote_python('scripts/publish_public.py')],text=True,timeout=300)
        with log.open('a') as f:f.write(publish.stdout+publish.stderr)
        print('Public publish: '+('success' if publish.returncode==0 else 'FAILED; private sync preserved; see update-launcher.log'))
        code=max(code,publish.returncode)

sys.exit(code)
