#!/usr/bin/env python3
"""Offline NAS-only diagnostic: read-only SQLite backup, replay into private temp copies."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]
from oursteps.store import Store
from oursteps.parser import parse_thread
from oursteps.performance import Performance,measure


def readonly(path):
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=30)
    db.row_factory=sqlite3.Row
    return db


def sizes(path):
    return {suffix:Path(str(path)+suffix).stat().st_size if Path(str(path)+suffix).exists() else None for suffix in ('','-wal','-shm')}


def pragmas(db):
    return {key:db.execute('PRAGMA '+key).fetchone()[0] for key in ('journal_mode','synchronous','wal_autocheckpoint','busy_timeout')}


def run():
    if sys.platform=='darwin' or str(ROOT).startswith('/Volumes/'):
        raise SystemExit('NAS native filesystem only')
    production=[ROOT/'data'/p for p in ('archive.sqlite3','reconcile-cache/archive.sqlite3','reconcile.sqlite3')]
    before={str(p):dict(sizes=sizes(p),mtime_ns=p.stat().st_mtime_ns) for p in production}
    batch=json.loads((ROOT/'data/auto-batch-last.json').read_text())
    # Pick an already imported TID; benchmark at most two cached pages, no global worker.
    tid=batch['tids'][0]
    results=[]
    with tempfile.TemporaryDirectory(prefix='.persistence-diagnostic-',dir=str(ROOT/'data')) as temp:
        temp=Path(temp)
        source=readonly(production[0])
        samples=source.execute('SELECT p.url,p.page,s.* FROM pages p JOIN snapshots s ON s.id=p.snapshot_id WHERE p.tid=? ORDER BY p.page LIMIT 2',(tid,)).fetchall()
        if not samples:raise ValueError('No cached pages')
        for database in production[:2]:
            original=readonly(database)
            dest=temp/('main' if database==production[0] else 'cache');dest.mkdir()
            copy=sqlite3.connect(str(dest/'archive.sqlite3'))
            started=time.monotonic();original.backup(copy);backup_seconds=time.monotonic()-started
            source_info=dict(path=str(database),pragmas=pragmas(original),sizes=sizes(database))
            original.close();copy.close()
            store=Store(dest);store.historical_mode=True
            # No production mutation: seed this already-known TID on the isolated cache copy if needed.
            with store.db:
                store.db.execute("INSERT OR IGNORE INTO threads(tid,discovered_via) VALUES(?,'reconciliation')",(tid,))
                store.db.execute("INSERT OR IGNORE INTO jobs(url,kind,tid,page) VALUES(?,'thread',?,1)",(samples[0]['url'],tid))
            info=dict(source=source_info,copy_path=str(dest/'archive.sqlite3'),writer_pragmas=pragmas(store.db),backup_seconds=backup_seconds,
                      threads=store.db.execute('SELECT count(*) FROM threads').fetchone()[0],
                      indexes=[dict(r) for r in store.db.execute("SELECT name,tbl_name,sql FROM sqlite_master WHERE type='index'")],
                      virtual_tables=[r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE sql LIKE '%VIRTUAL TABLE%'")],
                      query_plans={key:[r[3] for r in store.db.execute('EXPLAIN QUERY PLAN '+sql,(tid,))] for key,sql in {
                          'jobs':'SELECT state FROM jobs WHERE tid=?',
                          'pages':'SELECT * FROM pages WHERE tid=? AND result="parsed"',
                          'gaps':'SELECT count(*) FROM gaps g JOIN jobs j ON g.url=j.url WHERE j.tid=?'}.items()},
                      pages=[],copy_sizes_before=sizes(dest/'archive.sqlite3'))
            for sample in samples:
                body=(ROOT/'data'/sample['path']).read_bytes()
                assert hashlib.sha256(body).hexdigest()==sample['stored_sha256']
                store.performance=Performance();start=time.monotonic()
                parsed=measure(store,'parse_seconds',parse_thread,body,sample['url'])
                snap=store.snapshot(sample['url'],body,status=sample['status'],mode=sample['mode'])
                store.save_page(sample['url'],parsed,snap)
                row=dict(tid=tid,page=sample['page'],author_posts=len(parsed['posts']),total_seconds=time.monotonic()-start,metrics=store.performance.report())
                info['pages'].append(row)
                print(json.dumps(dict(progress=info['copy_path'],page=sample['page'],seconds=row['total_seconds'])),flush=True)
            info['totals']={key:sum(p['metrics'].get(key,0) for p in info['pages']) for key in set().union(*(p['metrics'] for p in info['pages']))}
            info['copy_sizes_after']=sizes(dest/'archive.sqlite3');store.db.close();results.append(info)
        source.close()
    after={str(p):dict(sizes=sizes(p),mtime_ns=p.stat().st_mtime_ns) for p in production}
    result=dict(tid=tid,network_requests=0,production_before=before,production_after=after,production_unchanged=before==after,results=results)
    out=ROOT/'data/persistence-diagnostic-report.json'
    # Diagnostic report only; never publish or touch source DBs. Temp databases already removed.
    out.write_text(json.dumps(result,indent=2));out.chmod(0o600)
    print('Diagnostic report: '+str(out),flush=True)

if __name__=='__main__':run()
