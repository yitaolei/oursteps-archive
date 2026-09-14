"""Offline listing date audit and precise migration of the saved page-101 stop."""
import sys,json,re,time,hashlib,fcntl
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]
from oursteps.config import UID
from oursteps.store import Store,atomic_write
from oursteps.parser import soup_of,query,directory_url,guard,PaginationLimit
from oursteps.history import report
if sys.platform=='darwin':raise SystemExit('Run on NAS native filesystem')
with open(ROOT/'data/worker.lock','a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);s=Store(ROOT/'data')
 try:
  def ids():return hashlib.sha256(json.dumps([r[0] for r in s.db.execute('SELECT tid FROM inventory ORDER BY tid')]).encode()).hexdigest()
  before=ids();session=ROOT/'.secrets/session.json';session_sha=hashlib.sha256(session.read_bytes()).hexdigest()
  pages={};headers=set();dated_rows=0;last_reply_dates=0
  for r in s.db.execute("SELECT * FROM snapshots WHERE url LIKE '%mod=space%' ORDER BY id"):
   q=query(r['url'])
   if q.get('do')=='thread' and q.get('uid')==str(UID) and r['status']==200:pages[int(q.get('page',1))]=r
  valid=0
  for page,r in pages.items():
   soup=soup_of(s.raw(r));rows=soup.select('table tr')
   if not soup.select('table th a[href*="viewthread"]'):continue
   valid+=1
   if rows:headers.add(rows[0].get_text(' ',strip=True))
   for row in rows:
    if not row.select_one('th a[href*="viewthread"]'):continue
    if re.search(r'20\d\d-\d+-\d+',row.get_text(' ',strip=True)):dated_rows+=1
    if any(query(a.get('href','')).get('goto')=='lastpost' and re.search(r'20\d\d-\d+-\d+',a.get_text()) for a in row.select('a[href]')):last_reply_dates+=1
  snap=s.latest(directory_url(101));cause=None
  try:guard(soup_of(s.raw(snap)))
  except PaginationLimit as e:cause=str(e)
  assert cause=='pagination_out_of_allowed_range'
  prior=dict(s.db.execute('SELECT * FROM inventory_progress').fetchone())
  atomic_write(s.root/'inventory-stop-before.json',json.dumps(prior).encode())
  with s.db:
   s.db.execute("UPDATE inventory_progress SET state='pagination_limited',error=?,retry_at=max(retry_at,?) WHERE id=1 AND next_page=101 AND state='permission_or_challenge'",(cause,time.time()+86400))
  assert ids()==before and hashlib.sha256(session.read_bytes()).hexdigest()==session_sha
  result=dict(valid_listing_pages=valid,headers=sorted(headers),rows_with_dates=dated_rows,rows_with_last_reply_dates=last_reply_dates,creation_dates_available=False,reason='Listing time column is last reply, not thread creation',page101_cause=cause,inventory_checksum_unchanged=True,session_unchanged=True,report=report(s))
  atomic_write(s.root/'inventory-local-audit.json',json.dumps(result,ensure_ascii=False,indent=2).encode());print(json.dumps(result,ensure_ascii=False))
 finally:s.db.close()
