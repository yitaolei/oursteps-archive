#!/usr/bin/env python3
import sys,json,fcntl,argparse,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]
from oursteps.store import Store
from oursteps.media import run
from oursteps.preview import build
parser=argparse.ArgumentParser(description='Archive images from existing complete threads only')
parser.add_argument('--retry-url',help='Requeue an unsupported/not-found image after correcting its cause; permission/robots blocks require review')
args=parser.parse_args()
if sys.platform=='darwin':raise SystemExit('Run on NAS native filesystem')
with open(ROOT/'data/worker.lock','a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 s=Store(ROOT/'data')
 try:
  if args.retry_url:
   row=s.db.execute('SELECT state,retry_at FROM media_files WHERE url=?',(args.retry_url,)).fetchone()
   if not row or row['state'] not in ('unsupported','not_found','retry_later') or row['retry_at']>time.time():
    raise SystemExit('Not eligible: preserve success, permission/robots blocks and retry backoff')
   with s.db:s.db.execute("UPDATE media_files SET state='pending',error=NULL WHERE url=?",(args.retry_url,))
  result=run(s);build(s);print(json.dumps({k:v for k,v in result.items() if k!='failures'}))
 finally:s.db.close()
