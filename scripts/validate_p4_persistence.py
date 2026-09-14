#!/usr/bin/env python3
"""NAS-only offline P4 equivalence + old/new replay on disposable database copies."""
import argparse
import hashlib
import importlib.util
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
from diagnose_persistence import readonly,pragmas,sizes


def fingerprint(db, selected):
    """All table values, excluding selected threads; index/schema layout is separate."""
    result={}
    for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        name=row[0];quoted='"'+name.replace('"','""')+'"';h=hashlib.sha256()
        cursor=db.execute('SELECT * FROM '+quoted+' ORDER BY rowid')
        columns=[x[0] for x in cursor.description]
        for record in cursor:
            if name=='threads' and record[columns.index('tid')] in selected:continue
            h.update(json.dumps(tuple(record),ensure_ascii=False,separators=(',',':')).encode());h.update(b'\n')
        result[name]=h.hexdigest()
    return result


def selected_rows(db,tids):
    return [tuple(db.execute('SELECT * FROM threads WHERE tid=?',(tid,)).fetchone()) for tid in tids]


def copy_store(source,destination,cls):
    destination.mkdir();db=sqlite3.connect(str(destination/'archive.sqlite3'));source.backup(db);db.close()
    store=cls(destination);store.historical_mode=True
    return store


def plans(db):
    return {key:[r[3] for r in db.execute('EXPLAIN QUERY PLAN '+sql,(1046174,))] for key,sql in {
        'jobs':'SELECT state FROM jobs WHERE tid=?',
        'pages':'SELECT * FROM pages WHERE tid=? AND result="parsed"',
        'gaps':'SELECT count(*) FROM gaps g JOIN jobs j ON g.url=j.url WHERE j.tid=?'}.items()}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--old-store',type=Path,required=True);a=p.parse_args()
    if sys.platform=='darwin' or str(ROOT).startswith('/Volumes/'):raise SystemExit('NAS native filesystem only')
    spec=importlib.util.spec_from_file_location('oursteps._p4_old_store',str(a.old_store));old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    original=ROOT/'data/archive.sqlite3'
    before=dict(sizes=sizes(original),mtime_ns=original.stat().st_mtime_ns)
    source=readonly(original);before['indexes']=[tuple(r) for r in source.execute("SELECT name,sql FROM sqlite_master WHERE type='index'")]
    categories={}
    for label,sql in {
        'one_page_complete':"SELECT t.tid FROM threads t WHERE t.status='complete' AND (SELECT count(*) FROM pages p WHERE p.tid=t.tid)=1 LIMIT 1",
        'multi_page_complete':"SELECT tid FROM threads WHERE tid=1046174 AND status='complete'",
        'incomplete':"SELECT tid FROM threads WHERE status!='complete' LIMIT 1",
        'with_gap':"SELECT j.tid FROM jobs j JOIN gaps g ON j.url=g.url WHERE j.tid IS NOT NULL LIMIT 1"}.items():
        row=source.execute(sql).fetchone();categories[label]=row[0] if row else None
    tids=sorted({t for t in categories.values() if t is not None})
    samples=source.execute('SELECT p.url,p.page,s.* FROM pages p JOIN snapshots s ON s.id=p.snapshot_id WHERE p.tid=1046174 ORDER BY p.page LIMIT 2').fetchall()
    assert len(samples)==2
    result=dict(categories=categories,selected=tids,network_requests=0,benchmark={})
    with tempfile.TemporaryDirectory(prefix='.p4-validation-',dir=str(ROOT/'data')) as temp:
        temp=Path(temp)
        # Independent restored copies for full and scoped comparison, then fresh copies for replay.
        expected=None
        for label,cls in [('old_full',old.Store),('new_scoped',Store)]:
            store=copy_store(source,temp/label,cls)
            # Exercise transitions instead of only comparing already-correct statuses.
            with store.db:
                for tid in tids:store.db.execute("UPDATE threads SET status='pending' WHERE tid=?",(tid,))
            prior=fingerprint(store.db,tids)
            start=time.monotonic()
            if label=='old_full':store.refresh_status()
            else:store.refresh_status(tids)
            elapsed=time.monotonic()-start
            actual=selected_rows(store.db,tids)
            if expected is None:expected=actual
            else:
                assert actual==expected,'Selected thread fields differ from old full refresh'
                assert fingerprint(store.db,tids)==prior,'Unselected data changed'
                result['scoped_full_equal']=True;result['unselected_unchanged']=True
            result[label]=dict(seconds=elapsed,selected_rows=actual)
            store.db.close();print(label+' comparison complete',flush=True)
        for label,cls in [('old',old.Store),('new',Store)]:
            store=copy_store(source,temp/('benchmark_'+label),cls)
            entry=dict(pragmas=pragmas(store.db),plans=plans(store.db),pages=[],indexes=[tuple(r) for r in store.db.execute("SELECT name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")])
            for sample in samples:
                body=(ROOT/'data'/sample['path']).read_bytes();assert hashlib.sha256(body).hexdigest()==sample['stored_sha256']
                store.performance=Performance()
                parsed=measure(store,'parse_seconds',parse_thread,body,sample['url'])
                snap=store.snapshot(sample['url'],body,status=sample['status'],mode=sample['mode'])
                store.save_page(sample['url'],parsed,snap)
                entry['pages'].append(dict(page=sample['page'],metrics=store.performance.report()))
                print(label+' page '+str(sample['page'])+' persisted',flush=True)
            entry['totals']={key:sum(x['metrics'].get(key,0) for x in entry['pages']) for key in set().union(*(x['metrics'] for x in entry['pages']))}
            store.db.close();result['benchmark'][label]=entry
    after=dict(sizes=sizes(original),mtime_ns=original.stat().st_mtime_ns,indexes=[tuple(r) for r in source.execute("SELECT name,sql FROM sqlite_master WHERE type='index'")]);source.close()
    result['production_before']=before;result['production_after']=after;result['production_unchanged']=before==after
    assert result['production_unchanged'],'Production changed during offline validation; investigate concurrent activity'
    out=ROOT/'data/p4-persistence-validation.json';out.write_text(json.dumps(result,indent=2));out.chmod(0o600)
    print(str(out),flush=True)

if __name__=='__main__':main()
