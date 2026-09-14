#!/usr/bin/env python3
"""Offline validation of stored successful pilot data, not the all-20 acceptance gate."""
import sys
import fcntl
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'.deps'))
from oursteps.store import Store
from oursteps.preview import validate
if sys.platform=='darwin' and str(ROOT).startswith('/Volumes/'):
    raise SystemExit('Run on NAS native path; never open SQLite over SMB.')
with open(ROOT/'data'/'worker.lock','a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    result=validate(Store(ROOT/'data'))
    print(json.dumps(result,ensure_ascii=False))
    sys.exit(0 if result['ok'] else 1)
