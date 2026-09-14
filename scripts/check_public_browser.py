#!/usr/bin/env python3
"""Offline fixture-only lazy-search/sort/range regression. No OurSteps requests."""
import copy,json,sys,threading
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]
from tests.test_public_release import PublicTests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, expect
import preview
case=PublicTests();case.setUp()
server=None
try:
    source=case.root/'data/preview';index=source/'index.html'
    soup=BeautifulSoup(index.read_text(),'html.parser');first=soup.select_one('.card')
    first.select_one('.views').string='100';second=copy.deepcopy(first)
    second['data-tid']='2';second['data-search']='zzzz forum';second.select_one('.title').string='ZZZZ'
    second.select_one('.views').string='200';first.insert_after(second)
    index.write_text(str(soup));payload=json.loads((source/'search-index.json').read_text())
    payload['2']='zzzz forum unique-body-only-needle';(source/'search-index.json').write_text(json.dumps(payload))
    with patch.object(preview,'ROOT',source):
        server=preview.ThreadingHTTPServer(('127.0.0.1',0),preview.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='chrome',headless=True)
            page=browser.new_page();errors=[];fetches=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda route: route.continue_() if route.request.url.startswith('http://127.0.0.1:') else route.abort())
            page.on('request',lambda r:fetches.append(r.url) if r.url.endswith('/search-index.json') else None)
            page.goto('http://127.0.0.1:%s/'%server.server_port)
            page.locator('.range-controls').wait_for()
            assert not fetches
            page.get_by_role('button',name='全部',exact=True).click()
            page.locator('.global-results [data-sort="views"]').click()
            assert page.locator('.global-results .card').first.get_attribute('data-tid')=='2'
            assert not fetches
            page.locator('#search').fill('unique-body-only-needle')
            expect(page.locator('#count')).to_have_text('1 篇')
            assert len(fetches)==1
            assert page.locator('.global-results .card:visible').get_attribute('data-tid')=='2'
            page.locator('#search').fill('zzzz')
            expect(page.locator('#count')).to_have_text('1 篇')
            assert len(fetches)==1 and not errors,errors
            browser.close()
    print('PASS: offline browser lazy body/title search, cached index, date-range and numeric sorting; no external requests')
finally:
    if server:server.shutdown();server.server_close();thread.join()
    case.tearDown()
