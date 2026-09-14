"""One normal advanced search form only; metadata inspection, no result crawl."""
import sys,json,fcntl
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]
from oursteps.store import Store,atomic_write
from oursteps.fetch import Fetcher
from oursteps.parser import soup_of
URL='https://www.oursteps.com.au/bbs/search.php?mod=forum&adv=yes'
if sys.platform=='darwin':raise SystemExit('NAS native only')
with open(ROOT/'data/worker.lock','a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);s=Store(ROOT/'data')
 try:
  f=Fetcher(s);f.daily_now=True;r=f.get(URL);soup=soup_of(s.raw(r));forms=[]
  for form in soup.select('form'):
   fields=[]
   for n in form.select('input,select,button'):
    if not n.get('name') or n.get('type') in ('hidden','password'):continue
    fields.append(dict(name=n.get('name'),type=n.get('type',n.name),options=[dict(value=o.get('value'),label=o.get_text(' ',strip=True)) for o in n.select('option')]))
   forms.append(dict(action=form.get('action'),method=form.get('method'),fields=fields))
  atomic_write(s.root/'search-form-capabilities.json',json.dumps(forms,ensure_ascii=False,indent=2).encode());print(json.dumps(forms,ensure_ascii=False))
 finally:s.db.close()
