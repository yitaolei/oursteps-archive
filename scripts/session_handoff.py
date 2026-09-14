#!/usr/bin/env python3
"""NAS-native private session installation; JSON arrives over SSH stdin, never argv."""
import json
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]
from oursteps.config import UID
from oursteps.store import atomic_write

def install(payload):
    if sys.platform=='darwin': raise RuntimeError('Session installation must run on NAS native filesystem')
    saved=json.loads(payload)
    if saved.get('uid')!=UID or not isinstance(saved.get('cookies'),list):
        raise ValueError('Invalid session identity')
    os.umask(0o077)
    folder=ROOT/'.secrets'
    folder.mkdir(mode=0o700,exist_ok=True)
    os.chmod(folder,0o700)
    atomic_write(folder/'session.json',payload)
    os.chmod(folder/'session.json',0o600)
    if (folder/'session.json').stat().st_mode & 0o077:
        raise RuntimeError('NAS session permissions remain unsafe')

if __name__=='__main__':
    try: install(sys.stdin.buffer.read(1024*1024))
    except Exception as e: raise SystemExit('Session handoff failed: '+type(e).__name__) from None
