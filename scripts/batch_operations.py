#!/usr/bin/env python3
"""Small NAS RPC for portable operators. Status uses only read-only connections."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.deps')]


def read_json(path):
    return json.loads(path.read_text()) if path.is_file() else {}


def rows(root, database, sql):
    with sqlite3.connect((root / database).resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        return [dict(row) for row in db.execute(sql)]


def status(root=ROOT):
    current = root / 'public-site/current'
    candidates = rows(root, 'data/reconcile.sqlite3', 'SELECT tid,recovery,error FROM candidates')
    complete = rows(root, 'data/archive.sqlite3', "SELECT tid FROM threads WHERE status='complete' AND discovered_via='reconciliation'")
    missing = root / 'data/manual-missing-tids.txt'
    return dict(full_public=sum(p.stem.isdigit() for p in current.glob('*.html')),
                recent_1y=sum(p.stem.isdigit() for p in (current / 'recent-1y').glob('*.html')),
                active_missing=len({x for x in missing.read_text().splitlines() if x.isdigit()}),
                recovered=sum(r['recovery'] == 'recovered' for r in candidates),
                unpublished=[r['tid'] for r in complete if not (current / ('%s.html' % r['tid'])).is_file()],
                review=[r for r in candidates if r['recovery'] == 'review_required'],
                reconciliation=read_json(root / 'data/reconcile-last-report.json'),
                batch=read_json(root / 'data/auto-batch-last.json'),
                preview=read_json(root / 'data/auto-batch-preview.json'),
                current=os.readlink(current) if current.is_symlink() else None,
                previous=os.readlink(root / 'public-site/previous') if (root / 'public-site/previous').is_symlink() else None,
                pending=(root / 'data/preview-incremental-pending.json').exists())


def call(root, argv, payload=None):
    result = subprocess.run([sys.executable] + argv, cwd=str(root), input=None if payload is None else json.dumps(payload), text=True, capture_output=True)
    if result.returncode:
        if 'worker active;' in result.stderr or 'Another worker is active;' in result.stderr:
            raise BlockingIOError('NAS worker active')
        raise RuntimeError('Command failed: %s\n%s\n%s' % (argv[0], result.stdout, result.stderr))
    return result.stdout


def operate(request, root=ROOT):
    action = request['action']
    if action == 'status':
        return status(root)
    from oursteps.store import atomic_write
    if action == 'reconcile':
        # Consume THIS invocation's output, including early retry/halt returns, not a stale report.
        seconds=request.get('max_seconds',1800); requests=request.get('max_requests',240)
        if type(seconds) is not int or not 0<seconds<=3600 or type(requests) is not int or not 0<requests<=1000:
            raise ValueError('Auto budgets must be 1..3600 seconds and 1..1000 logical requests')
        return json.loads(call(root, ['scripts/reconcile_oursteps.py', '--backfill-missing', '--max-backfill', '10', '--max-seconds', str(seconds), '--max-logical-requests', str(requests)]))
    if action == 'preview':
        from oursteps.reconcile import locked
        from oursteps.store import Store
        from oursteps.preview_batch import build_batch, SHARED
        # Exact implementation behind archive.py build-preview --recovered-unpublished.
        # Hold its existing worker lock through receipt creation to prevent a daily-writer race.
        with locked(root / 'data/worker.lock'):
            store = Store(root / 'data')
            try:
                result = build_batch(store)
            finally:
                store.db.close()
            tids = result['tids']
            if result['full_rebuild'] is not False or result['rendered_articles'] != len(tids) or not tids:
                raise ValueError('Unexpected preview result')
            if (root / 'data/preview-incremental-pending.json').exists():
                raise ValueError('Preview pending marker: do not publish')
            names = [str(t) + '.html' for t in tids] + list(SHARED)
            result['hashes'] = {name: hashlib.sha256((root / 'data/preview' / name).read_bytes()).hexdigest() for name in names}
            atomic_write(root / 'data/auto-batch-preview.json', json.dumps(result).encode())
            return result
    if action == 'publish':
        from public_release import publish
        result = publish(root, preview_hashes=request['hashes'])
        return result
    if action == 'healthcheck':
        return dict(output=call(root, ['scripts/public_healthcheck.py']))
    if action in ('check', 'missing'):
        tids = request.get('tids', [])
        if any(type(t) is not int or t <= 0 for t in tids):
            raise ValueError('Invalid TIDs')
        return dict(output=call(root, ['scripts/manual_missing.py'], dict(command='check-oursteps' if action == 'check' else 'show-missing', args=[str(t) for t in tids])))
    if action == 'checkpoint':
        atomic_write(root / 'data/auto-batch-last.json', json.dumps(request['result']).encode())
        return dict(saved=True)
    raise ValueError('Unsupported action')


if __name__ == '__main__':
    if sys.platform == 'darwin' or str(ROOT).startswith('/Volumes/'):
        raise SystemExit('NAS native filesystem only')
    try:
        print(json.dumps(dict(ok=True, result=operate(json.load(sys.stdin))), ensure_ascii=False))
    except BlockingIOError:
        print(json.dumps(dict(ok=False, busy=True)))
    except Exception as error:
        print(json.dumps(dict(ok=False, error=str(error))))
        sys.exit(1)
