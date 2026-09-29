#!/usr/bin/env python3
"""NAS-only importer for a browser-rendered authenticated thread snapshot."""
import argparse
import fcntl
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]

from oursteps.auth_verify import inspect_response
from oursteps.fetch import validate_url
from oursteps.parser import parse_thread, query, thread_url
from oursteps.store import Store

MAX_BYTES=5*1024*1024

def canonical_thread(url):
    validate_url(url)
    q=query(url)
    if q.get('mod')!='viewthread' or not q.get('tid','').isdigit() or not q.get('page','1').isdigit():
        raise ValueError('browser_snapshot_requires_thread_url')
    canonical=thread_url(int(q['tid']),int(q.get('page',1)))
    if canonical!=url:
        raise ValueError('browser_snapshot_requires_canonical_url')
    return canonical

def import_snapshot(root,url,content):
    if sys.platform=='darwin':
        raise RuntimeError('Browser snapshot import must run on NAS native filesystem')
    if not content or len(content)>MAX_BYTES:
        raise ValueError('browser_snapshot_size_invalid')
    canonical=canonical_thread(url)
    inspect_response(content,200)
    parsed=parse_thread(content,canonical)
    if parsed['tid']!=int(query(canonical)['tid']):
        raise ValueError('browser_snapshot_thread_identity_mismatch')
    store=Store(Path(root)/'data')
    try:
        snap=store.snapshot(canonical,content,status=200,mode='authenticated_private')
        return {'status':'imported','url':canonical,'snapshot_id':snap['id'],'bytes':len(content)}
    finally:
        store.db.close()

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--url',required=True)
    a=p.parse_args()
    content=sys.stdin.buffer.read(MAX_BYTES+1)
    if len(content)>MAX_BYTES:
        raise SystemExit('browser_snapshot_size_invalid')
    with open(ROOT/'data/worker.lock','a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('worker_active; browser snapshot import deferred')
        print(json.dumps(import_snapshot(ROOT,a.url,content),separators=(',',':')))

if __name__=='__main__':
    main()
