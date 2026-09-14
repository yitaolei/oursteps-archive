"""Strict, staged access to the existing daily incremental renderer. No network/publish."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from .preview import build_incremental
from .store import atomic_write

SHARED=('index.html','style.css','search.js','reader.js','search-index.json')


def selected_tids(store,tids=None):
    if tids is None:
        current=store.root.parent/'public-site/current'
        tids=[r[0] for r in store.db.execute("SELECT tid FROM threads WHERE status='complete' AND discovered_via='reconciliation' ORDER BY tid") if not (current/('%s.html'%r[0])).is_file()]
    values=set()
    for tid in tids:
        if isinstance(tid,bool) or not str(tid).isdigit() or not 0<int(tid)<2**63:
            raise ValueError('invalid preview TID')
        values.add(int(tid))
    pending=store.root/'preview-incremental-pending.json'
    if pending.exists():
        # A failed commit is always retried, even if the operator selects a new batch.
        values.update(json.loads(pending.read_text())['tids'])
    for tid in values:
        if not isinstance(tid,int) or not 0<tid<2**63:
            raise ValueError('invalid pending preview TID')
        row=store.db.execute('SELECT status FROM threads WHERE tid=?',(tid,)).fetchone()
        if row is None or row[0]!='complete':
            raise ValueError('preview TID is nonexistent or incomplete: %s'%tid)
    return sorted(values)


def sync_dir(path):
    fd=os.open(str(path),os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def stage_batch(store,tids,destination):
    """Also usable for an isolated NAS benchmark with a read-only archive connection."""
    destination.mkdir()
    index=store.root/'preview/index.html'
    if not index.is_file():raise ValueError('preview index missing; full rebuild refused')
    shutil.copyfile(index,destination/'index.html')
    return build_incremental(store,tids,allow_full_fallback=False,out=destination)


def build_batch(store,tids=None,dry_run=False):
    # Caller holds the existing worker.lock, shared with daily sync and publishing.
    start=time.monotonic();tids=selected_tids(store,tids)
    report=dict(tids=tids,selected_articles=len(tids),rendered_articles=0,
                full_rebuild=False,dry_run=dry_run)
    if dry_run or not tids:
        return dict(report,elapsed_seconds=round(time.monotonic()-start,3))
    out=store.root/'preview'
    pending=store.root/'preview-incremental-pending.json'
    with tempfile.TemporaryDirectory(prefix='.preview-batch-',dir=str(store.root)) as temp:
        stage=Path(temp)/'preview'
        stage_batch(store,tids,stage)
        # No live preview file has been changed if rendering/indexing raises above.
        names=[str(t)+'.html' for t in tids]+list(SHARED)
        if {p.name for p in stage.iterdir()}!=set(names):
            raise ValueError('unexpected incremental output set')
        atomic_write(pending,json.dumps(dict(tids=tids)).encode())
        sync_dir(store.root)
        for name in names:
            os.replace(str(stage/name),str(out/name))
        sync_dir(out)
        pending.unlink();sync_dir(store.root)
    return dict(report,rendered_articles=len(tids),elapsed_seconds=round(time.monotonic()-start,3))
