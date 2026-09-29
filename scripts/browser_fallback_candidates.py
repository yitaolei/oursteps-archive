#!/usr/bin/env python3
"""NAS-only read-only list of directory threads needing browser fallback."""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]

from oursteps.parser import directory_url, thread_url
from oursteps.sync import dated_listing, sydney_today

MAX_CANDIDATES=20

def candidates():
    if sys.platform=='darwin':
        raise RuntimeError('Browser fallback candidates must run on NAS')
    db_path=ROOT/'data/archive.sqlite3'
    db=sqlite3.connect('file:'+str(db_path)+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    try:
        url=directory_url()
        snap=db.execute(
            'SELECT * FROM snapshots WHERE url=? ORDER BY id DESC LIMIT 1',
            (url,)).fetchone()
        if snap is None:
            return []
        raw=(ROOT/'data'/snap['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=snap['stored_sha256']:
            raise ValueError('raw_archive_checksum_mismatch')
        page=dated_listing(raw,url,sydney_today())
        result=[]
        for row in page['threads']:
            known=db.execute(
                'SELECT created_at_raw FROM threads WHERE tid=?',
                (row['tid'],)).fetchone()
            if known is None or not known['created_at_raw']:
                result.append(thread_url(row['tid']))
            if len(result)>=MAX_CANDIDATES:
                break
        return result
    finally:
        db.close()

if __name__=='__main__':
    print(json.dumps({'urls':candidates()},separators=(',',':')))
