#!/usr/bin/env python3
"""NAS-native read-only selection of never-attempted inventory and due continuation pages."""
import argparse,json,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]
from oursteps.parser import thread_url

def first_unstarted(db,limit):
    rows=db.execute('''SELECT i.tid FROM inventory i JOIN threads t ON t.tid=i.tid
        WHERE t.status='pending' AND NOT EXISTS(SELECT 1 FROM jobs j WHERE j.tid=i.tid)
        ORDER BY i.tid DESC LIMIT ?''',(limit,)).fetchall()
    return [dict(tid=tid,url=thread_url(tid,1),page=1) for (tid,) in rows]

def continuations(db,tid,limit):
    rows=db.execute('''SELECT j.page FROM jobs j JOIN inventory i ON i.tid=j.tid
        JOIN threads t ON t.tid=i.tid
        WHERE j.tid=? AND t.status!='complete' AND j.state='pending'
        AND j.error IS NULL AND j.page>1
        AND NOT EXISTS(SELECT 1 FROM jobs denied WHERE denied.tid=j.tid
          AND (denied.state IN ('auth_required','permanently_skipped')
               OR denied.error LIKE '%http_403%' OR denied.error LIKE '%http_429%'))
        ORDER BY j.page ASC LIMIT ?''',(tid,limit)).fetchall()
    return [dict(tid=tid,url=thread_url(tid,page),page=page) for (page,) in rows]

def resumable(db,limit):
    rows=db.execute("""SELECT DISTINCT j.tid FROM jobs j
        JOIN inventory i ON i.tid=j.tid JOIN threads t ON t.tid=i.tid
        WHERE t.status='partial' AND j.state='pending' AND j.page>1 AND j.error IS NULL
        AND NOT EXISTS(SELECT 1 FROM jobs denied WHERE denied.tid=j.tid
          AND (denied.state='auth_required' OR denied.state='permanently_skipped'
               OR denied.error LIKE '%http_403%' OR denied.error LIKE '%http_429%'))
        ORDER BY j.tid DESC LIMIT ?""",(limit,)).fetchall()
    return [dict(tid=tid,url=thread_url(tid,1),page=0) for (tid,) in rows]

def counts(db):
    rows=db.execute("""SELECT t.status,count(*) FROM inventory i
        JOIN threads t ON t.tid=i.tid GROUP BY t.status""").fetchall()
    values=dict(rows)
    return {'complete':int(values.get('complete',0)),
            'incomplete':sum(value for name,value in rows if name!='complete')}

def main():
    if sys.platform=='darwin':raise SystemExit('Historical candidate query must run on NAS')
    p=argparse.ArgumentParser();p.add_argument('--tid',type=int);p.add_argument('--resume',action='store_true');p.add_argument('--counts',action='store_true');p.add_argument('--limit',type=int,default=10);a=p.parse_args()
    if not 1<=a.limit<=100:p.error('limit 1..100')
    db=sqlite3.connect('file:'+str(ROOT/'data/archive.sqlite3')+'?mode=ro',uri=True)
    try:
        if a.counts:
            print(json.dumps({'counts':counts(db),'read_only':True},separators=(',',':')))
        else:
            rows=continuations(db,a.tid,a.limit) if a.tid else (resumable(db,a.limit) if a.resume else first_unstarted(db,a.limit))
            print(json.dumps({'items':rows,'read_only':True},separators=(',',':')))
    finally:db.close()
if __name__=='__main__':main()
