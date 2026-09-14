"""Reproduce parser checks with the saved session; never log in or replace it."""
import sys,json,time,subprocess,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]
from playwright.sync_api import sync_playwright
from oursteps.deployment import deployment, remote_python
from oursteps.config import UID
from oursteps.auth import STATE,identity
from oursteps.parser import directory_url,parse_discovery,soup_of
from oursteps.dates import infer_display_offset
saved=json.loads(STATE.read_text())
with sync_playwright() as p:
 b=p.chromium.launch(channel='chrome',headless=True)
 c=b.new_context(storage_state={'cookies':saved['cookies'],'origins':[]});page=c.new_page()
 for stage,url in [('directory',directory_url()),('profile',f'https://www.oursteps.com.au/bbs/home.php?mod=space&uid={UID}&do=profile')]:
  before=time.time();r=page.goto(url,wait_until='domcontentloaded',timeout=60000);after=time.time();raw=r.body();meta=dict(stage=stage,status=r.status,before=before,after=after,identity=identity(soup_of(raw)))
  try:
   meta['result']=len(parse_discovery(raw,url)['threads']) if stage=='directory' else infer_display_offset(raw,before,after)
  except Exception as e:meta['error']=str(e)
  if stage=='profile':
   meta['clock_text']=re.findall(r'最后访问\s*\d{4}-\d+-\d+ \d+:\d+',soup_of(raw).get_text(' ',strip=True))
   meta['saved_offset']=saved.get('display_offset_hours');meta['evidence_age_seconds']=int(after-saved.get('display_offset_verified_at',0))
  diagnostic=subprocess.run(['ssh','-o','BatchMode=yes',deployment()[0],remote_python('scripts/auth_diagnostic.py')],input=json.dumps(dict(meta,html=raw.decode('utf-8','replace'))),text=True,capture_output=True)
  meta['diagnostic']=diagnostic.stdout.strip();print(json.dumps(meta,ensure_ascii=False),flush=True)
  if r.status in (403,429) or not meta['identity']:break
  time.sleep(5)
 b.close()
