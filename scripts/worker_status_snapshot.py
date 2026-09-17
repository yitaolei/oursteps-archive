#!/usr/bin/env python3
"""Publish a sanitized worker-status snapshot to NAS-native private state."""
import json, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.archive_worker_status import public_status
from oursteps.deployment import deployment, remote_python

if __name__=='__main__':
    payload=json.dumps(public_status(),sort_keys=True,separators=(',',':'))
    host,_=deployment()
    run=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',host,remote_python('scripts/control_action_queue.py','worker-status-write')],input=payload,text=True,capture_output=True,timeout=20)
    if run.returncode:
        print((run.stderr or run.stdout).strip()[-300:],file=sys.stderr)
        raise SystemExit(run.returncode)
