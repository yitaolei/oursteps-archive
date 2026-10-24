#!/usr/bin/env python3
"""NAS-only read-only list of directory threads needing browser fallback."""
import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]

from oursteps.parser import directory_url, thread_url
from oursteps.sync import dated_listing, sydney_today

MAX_CANDIDATES=80

def candidates(historical=False,limit=20):
    if sys.platform=='darwin':
        raise RuntimeError('Browser fallback candidates must run on NAS')
    if type(limit) is not int or not 1<=limit<=MAX_CANDIDATES:
        raise ValueError('candidate limit must be 1..%s'%MAX_CANDIDATES)
    db_path=ROOT/'data/archive.sqlite3'
    db=sqlite3.connect('file:'+str(db_path)+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    try:
        if historical:
            # Browser recovery is an alternate transport for a fresh JS challenge,
            # so it may run before urllib retry_at. One unique session inventory is
            # queried per pipeline; the next scheduler run is the next retry.
            # A page committed before an SSH/ACK loss is already state=success
            # and is excluded here: SQLite is the only recovery checkpoint.
            # The historical site-wide halt is authoritative for all browser
            # requests as well as urllib. Do not seek a different transport
            # around an active access denial.
            halted=db.execute("SELECT value FROM settings WHERE key='halt'").fetchone()
            if halted and halted[0]:
                return []
            rows=db.execute(
                "SELECT j.tid,j.page FROM jobs j "
                "JOIN inventory i ON i.tid=j.tid "
                "JOIN threads t ON t.tid=j.tid "
                "WHERE j.state IN ('pending','retry_later') "
                "AND (j.error IS NULL OR j.error='retry_later:incomplete_html') "
                "AND t.status!='complete' "
                "ORDER BY CASE WHEN t.status='partial' THEN 0 ELSE 1 END,"
                "j.attempts ASC,j.retry_at ASC,j.tid DESC,j.page ASC LIMIT ?",
                (limit,)).fetchall()
            return [thread_url(row['tid'],row['page']) for row in rows]
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
            if len(result)>=limit:
                break
        return result
    finally:
        db.close()

def historical_queue_audit():
    """Read-only breakdown; auth-denied pages are NOT runnable candidates."""
    db=sqlite3.connect('file:'+str(ROOT/'data/archive.sqlite3')+'?mode=ro',uri=True)
    try:
        rows=db.execute("""SELECT j.state,coalesce(j.error,''),count(*)
            FROM jobs j JOIN inventory i ON i.tid=j.tid
            JOIN threads t ON t.tid=j.tid
            WHERE t.status!='complete' AND j.state!='success'
            GROUP BY j.state,j.error""").fetchall()
        result={'eligible_incomplete_html':0,'blocked_or_manual':0,'other':0,'total_jobs':0}
        for state,error,count in rows:
            result['total_jobs']+=count
            if state=='retry_later' and error=='retry_later:incomplete_html':
                result['eligible_incomplete_html']+=count
            elif state in ('auth_required','permission_denied') or 'http_403' in error or 'http_429' in error:
                result['blocked_or_manual']+=count
            else:result['other']+=count
        return result
    finally:db.close()

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--historical',action='store_true')
    p.add_argument('--limit',type=int,default=20)
    p.add_argument('--audit-historical',action='store_true',help='Read-only summary of incomplete historical queue; never fetch')
    a=p.parse_args()
    if a.audit_historical:
        print(json.dumps({'audit':historical_queue_audit()},separators=(',',':')))
    else:
        print(json.dumps({'urls':candidates(a.historical,a.limit)},separators=(',',':')))
