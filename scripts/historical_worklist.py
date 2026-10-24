#!/usr/bin/env python3
"""Read-only, deterministic NAS inventory worklist; never contacts the forum."""
import argparse
import json
import sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def classify(state,error):
    error=error or ''
    if state in ('auth_required','permission_denied','permanently_skipped') or any(x in error for x in ('http_403','http_401','http_407','http_429','robots_disallow')):
        return 'access_hold'
    if state=='retry_later':
        return 'transient_hold'
    if state in ('parse_error','not_found'):
        return 'review'
    return 'unscheduled'


def worklist(db,limit=80):
    if not isinstance(limit,int) or not 1<=limit<=500:raise ValueError('limit out of range')
    rows=db.execute('''SELECT i.tid,t.status,j.state,j.error,j.page
       FROM inventory i JOIN threads t ON t.tid=i.tid
       LEFT JOIN jobs j ON j.rowid=(SELECT j2.rowid FROM jobs j2 WHERE j2.tid=i.tid AND j2.state!='success' ORDER BY j2.page ASC LIMIT 1)
       WHERE t.status!='complete'
       ORDER BY CASE WHEN t.status='partial' THEN 0 ELSE 1 END,i.tid DESC
       LIMIT ?''',(limit,)).fetchall()
    items=[{'tid':int(tid),'thread_status':status,'work_state':classify(jstate,error),'page':int(page or 1)}
           for tid,status,jstate,error,page in rows]
    counts=dict(db.execute('''SELECT t.status,count(*) FROM inventory i
         JOIN threads t ON t.tid=i.tid GROUP BY t.status''').fetchall())
    return {'total_incomplete':sum(v for k,v in counts.items() if k!='complete'),
            'total_complete':counts.get('complete',0),'items':items,
            'site_requests':0,'writes':0}


def main():
    p=argparse.ArgumentParser();p.add_argument('--limit',type=int,default=80);a=p.parse_args()
    db=sqlite3.connect('file:'+str(ROOT/'data/archive.sqlite3')+'?mode=ro',uri=True)
    try:print(json.dumps(worklist(db,a.limit),separators=(',',':')))
    finally:db.close()
if __name__=='__main__':main()
