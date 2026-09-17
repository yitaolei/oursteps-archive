#!/usr/bin/env python3
"""Read-only Mac-side probe for the NAS archive worker lock and active worker type."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from oursteps.deployment import deployment

REMOTE = r'''import fcntl,json,os,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve()
lock=root/'data/worker.lock'
busy=False
if lock.exists():
    f=lock.open('r')
    try:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        busy=True
kind='idle'
pid=None
if busy:
    kind='archive_worker'
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            args=[a.decode(errors='replace') for a in (proc/'cmdline').read_bytes().split(b'\\0') if a]
            if not args: continue
            cwd=(proc/'cwd').resolve()
            if cwd != root: continue
            joined=' '.join(args)
            if any(Path(a).name=='history_worker.py' for a in args) and 'backfill' in args:
                kind='historical_backfill';pid=int(proc.name);break
            if 'archive.py sync-today' in joined:
                kind='incremental_sync';pid=int(proc.name)
        except (OSError,UnicodeError,ValueError):
            continue
print(json.dumps({'busy':busy,'kind':kind,'pid':pid},separators=(',',':')))
'''

def probe():
    host, remote = deployment()
    cmd = 'python3 - ' + shlex.quote(remote)
    result = subprocess.run(
        ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',host,cmd],
        input=REMOTE,text=True,capture_output=True,timeout=20,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip()[-300:] or 'status probe failed')
    data=json.loads(result.stdout.strip().splitlines()[-1])
    if not isinstance(data,dict) or not isinstance(data.get('busy'),bool):
        raise RuntimeError('invalid worker status response')
    return data

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--guard',action='store_true',help='exit non-zero when another archive worker is active or status is unavailable')
    p.add_argument('--json',action='store_true')
    a=p.parse_args()
    try:
        data=probe()
    except Exception as exc:
        if a.json: print(json.dumps({'state':'UNKNOWN','error':type(exc).__name__},separators=(',',':')))
        else:
            print('OurSteps worker status: UNKNOWN')
            print('Could not safely confirm the NAS worker state. Manual write action not started.' if a.guard else 'Could not read NAS worker state.')
        return 2 if a.guard else 1
    if a.json:
        print(json.dumps(data,separators=(',',':')))
    elif data['busy']:
        label={'historical_backfill':'Historical Backfill','incremental_sync':'Incremental Sync'}.get(data.get('kind'),'Archive worker')
        print('OurSteps worker status: RUNNING')
        print(label+' is currently active on the NAS.')
        if a.guard: print('Manual command skipped. Please try again after the worker finishes.')
    else:
        print('OurSteps worker status: IDLE')
        print('No archive writer currently holds the NAS worker lock.')
    return 75 if a.guard and data['busy'] else 0

if __name__=='__main__':
    raise SystemExit(main())
