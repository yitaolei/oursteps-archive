"""Single recent-day normal search smoke test; never merge or follow pagination."""
import sys,json,fcntl,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]
from oursteps.config import USERNAME
from oursteps.store import Store,atomic_write
from oursteps.fallback import Transport,form_fields,results,SEARCH
if sys.platform=='darwin':raise SystemExit('NAS native only')
with open(ROOT/'data/worker.lock','a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);s=Store(ROOT/'data')
 try:
  before=[r[0] for r in s.db.execute('SELECT tid FROM inventory ORDER BY tid')]
  t=Transport(s);body,url=t.search();fields,forums,ranges=form_fields(body)
  fields.update(srchuname=USERNAME,srchtxt='',srchfilter='all',srchfrom='86400',before='',orderby='dateline',ascdesc='desc',searchsubmit='yes');fields['srchfid[]']='43'
  body,url=t.search(SEARCH,fields);rows,nxt,capped=results(body,url)
  result=dict(author_forum_search=True,arbitrary_date_range=False,recent_day_results=len(rows),next_page_available=bool(nxt),inventory_unchanged=before==[r[0] for r in s.db.execute('SELECT tid FROM inventory ORDER BY tid')],original_count=len(before))
  atomic_write(s.root/'fallback-smoke.json',json.dumps(result,indent=2).encode());print(json.dumps(result))
 finally:s.db.close()
