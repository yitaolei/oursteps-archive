#!/usr/bin/env python3
"""One-shot authenticated read-only historical page status probe; no HTML persistence."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.browser_fallback import saved_session,canonical_thread
from oursteps.fetch import AGENT
from oursteps.parser import query,parse_thread
from playwright.sync_api import sync_playwright

def main():
    if sys.platform!='darwin':raise SystemExit('Mac only')
    if len(sys.argv)!=2:raise SystemExit('one canonical thread URL required')
    url=canonical_thread(sys.argv[1])
    saved=saved_session()
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        try:
            context=browser.new_context(storage_state={'cookies':saved.get('cookies',[]),'origins':[]},user_agent=AGENT)
            page=context.new_page()
            response=page.goto(url,wait_until='domcontentloaded',timeout=30000)
            try:
                article=parse_thread(page.content().encode('utf-8'),url)
                parse={'valid':True,'parsed_tid_matches':article.get('tid')==int(query(url)['tid']),'page_matches':article.get('page')==1}
            except Exception as exc:
                parse={'valid':False,'parse_error':type(exc).__name__+':'+str(exc)[:70]}
            print(json.dumps({**parse,'status':response.status if response else None,
                'same_url':page.url==url,'destination_mod':query(page.url).get('mod'),
                'same_tid':query(page.url).get('tid')==query(url).get('tid'),
                'destination_page':query(page.url).get('page','1'),
                'site_request_count':1,'body_saved':False},separators=(',',':')))
        finally:browser.close()
if __name__=='__main__':main()
