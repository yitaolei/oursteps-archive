#!/usr/bin/env python3
"""NAS-only importer; legacy --url or one locked, binary-framed --stream session."""
import argparse
import fcntl
import json
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]

from oursteps.auth_verify import inspect_response
from oursteps.fetch import validate_url
from oursteps.parser import parse_thread, query, thread_url
from oursteps.store import Store
from scripts.browser_snapshot_stream import MAX_BYTES, MAX_LINE, PROTOCOL, decode_line, json_line


def canonical_thread(url):
    if not isinstance(url,str):
        raise ValueError('browser_snapshot_requires_thread_url')
    validate_url(url)
    q=query(url)
    if q.get('mod')!='viewthread' or not q.get('tid','').isdigit() or not q.get('page','1').isdigit():
        raise ValueError('browser_snapshot_requires_thread_url')
    canonical=thread_url(int(q['tid']),int(q.get('page',1)))
    if canonical!=url:
        raise ValueError('browser_snapshot_requires_canonical_url')
    return canonical


def save_snapshot(store,url,content,historical_pending=False):
    if not content or len(content)>MAX_BYTES:
        raise ValueError('browser_snapshot_size_invalid')
    canonical=canonical_thread(url)
    inspect_response(content,200)
    parsed=parse_thread(content,canonical)
    tid=int(query(canonical)['tid'])
    if parsed['tid']!=tid or parsed['page']!=int(query(canonical).get('page',1)):
        raise ValueError('browser_snapshot_thread_identity_mismatch')
    if historical_pending:
        # Streaming is historical only: refuse anything outside Inventory/jobs.
        if not store.db.execute(
                "SELECT 1 FROM jobs j JOIN inventory i ON i.tid=j.tid "
                "WHERE j.url=? AND j.tid=? AND j.page=? AND j.state='retry_later' "
                "AND j.error='retry_later:incomplete_html'",
                (canonical,tid,parsed['page'])).fetchone():
            raise ValueError('browser_snapshot_not_due_historical_challenge')
        from oursteps.history import _historical_preview_pending, _save_historical_preview_pending
        # Commit pending BEFORE save_page (which owns its transaction). A crash
        # after page commit, even before ACK, cannot lose preview eligibility.
        with store.db:
            pending=_historical_preview_pending(store)
            pending.add(tid)
            _save_historical_preview_pending(store,pending)
    previous=getattr(store,'historical_mode',False)
    try:
        store.historical_mode=True
        snap=store.snapshot(canonical,content,status=200,mode='authenticated_private')
        store.save_page(canonical,parsed,snap)
        status=store.db.execute('SELECT status FROM threads WHERE tid=?',(tid,)).fetchone()[0]
        return {'status':'imported','url':canonical,'snapshot_id':snap['id'],
                'bytes':len(content),'thread_status':status}
    finally:
        store.historical_mode=previous


def import_snapshot(root,url,content):
    if sys.platform=='darwin':
        raise RuntimeError('Browser snapshot import must run on NAS native filesystem')
    store=Store(Path(root)/'data')
    try:
        return save_snapshot(store,url,content)
    finally:
        store.db.close()


def serve_stream(store,source,sink):
    """Caller holds worker.lock for this entire Store lifetime. EOF between frames
    closes cleanly. Any malformed/failed item terminates without reading another.
    ACK is flushed only after Store.save_page commits. Lost ACKs are recovered by
    the next scheduler's SQLite candidate query, never by replaying here.
    """
    def emit(value):
        sink.write(json_line(dict(protocol=PROTOCOL,**value)))
        sink.flush()
    seq=1
    try:
        emit(dict(status='ready',max_bytes=MAX_BYTES))
        while True:
            raw=source.readline(MAX_LINE+1)
            if not raw:
                return 0
            header=decode_line(raw)
            if set(header)!={'protocol','seq','url','length'}:
                raise ValueError('invalid_frame_header')
            if type(header['seq']) is not int or header['seq']!=seq:
                raise ValueError('invalid_frame_seq')
            url=canonical_thread(header['url'])
            length=header['length']
            if type(length) is not int or not 1<=length<=MAX_BYTES:
                raise ValueError('invalid_frame_length')
            content=bytearray()
            while len(content)<length:
                chunk=source.read(length-len(content))
                if not chunk:
                    raise ValueError('truncated_frame')
                content.extend(chunk)
            started=time.monotonic()
            result=save_snapshot(store,url,bytes(content),historical_pending=True)
            emit(dict(result,seq=seq,import_seconds=round(time.monotonic()-started,6)))
            seq+=1
    except Exception as exc:
        # A broken ACK pipe can itself fail here; all earlier commits survive.
        try:
            emit(dict(status='error',seq=seq,error=type(exc).__name__+':'+str(exc)[:200]))
        except OSError:
            pass
        return 2


def main(argv=None):
    p=argparse.ArgumentParser()
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--url')
    mode.add_argument('--stream',action='store_true')
    a=p.parse_args(argv)
    if sys.platform=='darwin':
        raise SystemExit('Browser snapshot import must run on NAS native filesystem')
    try:
        with open(ROOT/'data/worker.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if a.stream:
                store=Store(ROOT/'data')
                try:
                    return serve_stream(store,sys.stdin.buffer,sys.stdout.buffer)
                finally:
                    store.db.close()
            content=sys.stdin.buffer.read(MAX_BYTES+1)
            if len(content)>MAX_BYTES:
                raise SystemExit('browser_snapshot_size_invalid')
            print(json.dumps(import_snapshot(ROOT,a.url,content),separators=(',',':')))
            return 0
    except Exception as exc:
        if a.stream:
            sys.stdout.buffer.write(json_line(dict(protocol=PROTOCOL,status='error',error=type(exc).__name__+':'+str(exc)[:200])))
            sys.stdout.buffer.flush()
            return 2
        if isinstance(exc,BlockingIOError):
            raise SystemExit('worker_active; browser snapshot import deferred') from None
        raise

if __name__=='__main__':
    sys.exit(main())
