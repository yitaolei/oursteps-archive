#!/usr/bin/env python3
"""NAS-native, offline-only recovery of already saved historical HTML pages."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]
from oursteps.store import Store
from oursteps.parser import parse_thread,ParseError

def candidates(store,limit):
    rows=store.db.execute('''SELECT j.url,j.tid,j.page,j.state,j.error,s.id,s.status
        FROM jobs j JOIN inventory i ON i.tid=j.tid JOIN threads t ON t.tid=j.tid
        JOIN snapshots s ON s.id=(SELECT s2.id FROM snapshots s2 WHERE s2.url=j.url AND s2.status=200 ORDER BY s2.id DESC LIMIT 1)
        WHERE j.state!='success' AND t.status!='complete'
        ORDER BY j.tid DESC,j.page ASC LIMIT ?''',(limit,)).fetchall()
    return rows

def audit(store,limit):
    result=[]
    for row in candidates(store,limit):
        item={'tid':row['tid'],'page':row['page'],'state':row['state'],'snapshot_id':row['id']}
        try:
            snap=store.db.execute('SELECT * FROM snapshots WHERE id=?',(row['id'],)).fetchone()
            raw=store.raw(snap)
            parsed=parse_thread(raw,row['url'])
            item['valid']=parsed['tid']==row['tid'] and parsed['page']==row['page']
            if not item['valid']:item['reason']='identity_mismatch'
        except (ValueError,ParseError,OSError) as e:
            item['valid']=False;item['reason']=type(e).__name__+':'+str(e)[:80]
        result.append(item)
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--limit',type=int,default=100)
    a=p.parse_args()
    if not 1<=a.limit<=500: p.error('limit must be 1..500')
    if sys.platform=='darwin':p.error('NAS-native read-only inspection only')
    s=Store(ROOT/'data')
    try:
        rows=audit(s,a.limit)
        print(json.dumps({'checked':len(rows),'valid':sum(x['valid'] for x in rows),'items':rows},separators=(',',':')))
    finally:s.db.close()
if __name__=='__main__':main()
