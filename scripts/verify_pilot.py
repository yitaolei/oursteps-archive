#!/usr/bin/env python3
"""Aggregate validation only. Never reads pages through a model or fetches URLs."""
import fcntl
import json
import re
import sqlite3
from pathlib import Path

root = Path(__file__).resolve().parents[1]
data = root/'data'
with open(data/'worker.lock', 'a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    db = sqlite3.connect('file:%s?mode=ro' % (data/'archive.sqlite3'), uri=True)
    spec = (root/'SPEC.md').read_text()
    expected = set()
    for row in re.findall(r'^\| \[\d+ .+$', spec, re.M):
        expected.update(int(pid) for pid in re.findall(r'(\d+)\([123]\)', row))
    actual = {r[0] for r in db.execute('SELECT pid FROM posts')}
    integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
    complete = db.execute('SELECT count(*) FROM threads WHERE status="complete"').fetchone()[0]
    failures = list(db.execute('SELECT url,error FROM jobs WHERE state="failed"'))
    public = db.execute('SELECT count(*) FROM posts WHERE publish_state!="excluded"').fetchone()[0]
    result = dict(integrity=integrity, complete_threads=complete, author_posts=len(actual),
                  expected_spec_pids=len(expected), missing_spec_pids=sorted(expected-actual),
                  additional_pids=sorted(actual-expected), failures=failures,
                  public_posts=public)
    result['passed'] = (integrity=='ok' and complete==20 and not result['missing_spec_pids']
                        and not failures and public==0)
    (data/'pilot-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(0 if result['passed'] else 2)
