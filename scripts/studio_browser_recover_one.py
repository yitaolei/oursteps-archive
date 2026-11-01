#!/usr/bin/env python3
"""Recover one authorized page into Studio only; never connect to NAS."""
import fcntl,json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]
from scripts.studio_local_authority import allow_local_authority
from scripts.browser_fallback import saved_session,page_content
from scripts.import_browser_snapshot import save_snapshot
from oursteps.parser import thread_url
from oursteps.store import Store
from oursteps.fetch import AGENT
from playwright.sync_api import sync_playwright
DATA=allow_local_authority(ROOT/'data')
tid=int(sys.argv[1]);url=thread_url(tid)
if tid<=0:raise SystemExit('Invalid TID')
saved=saved_session()
with sync_playwright() as playwright:
 browser=playwright.chromium.launch(channel='chrome',headless=True)
 try:
  context=browser.new_context(storage_state={'cookies':saved.get('cookies',[]),'origins':[]},user_agent=AGENT)
  page=context.new_page()
  response=page.goto(url,wait_until='domcontentloaded',timeout=60000)
  content=page_content(page,url,response)
 finally:browser.close()
with (DATA/'worker.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 store=Store(DATA)
 try:out=save_snapshot(store,url,content,defer_preview=True)
 finally:store.db.close()
print(json.dumps({'tid':tid,'result':out['status'],'thread_status':out['thread_status']}))
