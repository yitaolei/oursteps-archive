#!/usr/bin/env python3
"""Mac-only Playwright fallback for JS-rendered OurSteps thread pages."""
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from oursteps.auth import STATE, security_check
from oursteps.auth_verify import identity
from oursteps.config import UID
from oursteps.deployment import deployment, remote_python
from oursteps.fetch import validate_url
from oursteps.parser import ParseError, parse_thread, query, soup_of, thread_url

WAIT_SECONDS=20

def canonical_thread(url):
    validate_url(url)
    q=query(url)
    if q.get('mod')!='viewthread' or not q.get('tid','').isdigit() or not q.get('page','1').isdigit():
        raise ValueError('browser_fallback_requires_thread_url')
    canonical=thread_url(int(q['tid']),int(q.get('page',1)))
    if canonical!=url:
        raise ValueError('browser_fallback_requires_canonical_url')
    return canonical

def saved_session():
    saved=json.loads(STATE.read_text())
    if saved.get('uid')!=UID or not isinstance(saved.get('cookies'),list):
        raise RuntimeError('invalid_project_session; run archive.py auth')
    return saved

def page_content(page,url,response):
    from playwright.sync_api import Error
    deadline=time.monotonic()+WAIT_SECONDS
    last='not_ready'
    while time.monotonic()<deadline:
        try:
            security_check(page,response)
            current=urlsplit(page.url)
            if current.scheme!='https' or current.netloc!='www.oursteps.com.au':
                raise RuntimeError('browser_fallback_off_origin_navigation')
            content=page.content().encode('utf-8')
            soup=soup_of(content)
            if identity(soup):
                try:
                    parse_thread(content,url)
                    return content
                except ParseError as exc:
                    last=str(exc)
            else:
                last='identity_not_confirmed'
        except Error:
            last='browser_navigation_in_progress'
        page.wait_for_timeout(500)
    raise RuntimeError('browser_fallback_unresolved:'+last)

def install_candidate(saved,cookies):
    candidate=dict(saved,
        cookies=[c for c in cookies if c.get('domain','').lstrip('.') in ('oursteps.com.au','www.oursteps.com.au')],
        origins=[],uid=UID,verified_at=time.time())
    host,_=deployment()
    check=subprocess.run(
        ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',host,
         remote_python('scripts/verify_auth.py','--candidate')],
        input=json.dumps(candidate),text=True,capture_output=True,timeout=120)
    if check.returncode:
        detail=(check.stderr or check.stdout).strip().splitlines()[-1:] or ['unknown']
        raise RuntimeError('browser_session_handoff_failed:'+detail[0][:200])

def import_snapshot(url,content):
    host,_=deployment()
    run=subprocess.run(
        ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',host,
         remote_python('scripts/import_browser_snapshot.py','--url',url)],
        input=content,capture_output=True,timeout=60)
    if run.returncode:
        detail=(run.stderr or run.stdout).decode('utf-8','replace').strip().splitlines()[-1:] or ['unknown']
        raise RuntimeError('browser_snapshot_import_failed:'+detail[0][:200])
    return json.loads(run.stdout.decode('utf-8').strip().splitlines()[-1])

def main():
    if sys.platform!='darwin':
        raise SystemExit('browser_fallback_must_run_on_mac')
    if not 2<=len(sys.argv)<=21:
        raise SystemExit('usage: browser_fallback.py URL [URL ... up to 20]')
    urls=list(dict.fromkeys(canonical_thread(value) for value in sys.argv[1:]))
    saved=saved_session()
    from playwright.sync_api import sync_playwright, Error
    resolved=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        try:
            context=browser.new_context(storage_state={'cookies':saved.get('cookies',[]),'origins':[]})
            page=context.new_page()
            for index,url in enumerate(urls):
                response=None
                try:
                    response=page.goto(url,wait_until='domcontentloaded',timeout=60000)
                except Error:
                    pass
                content=page_content(page,url,response)
                imported=import_snapshot(url,content)
                resolved.append({'url':url,'snapshot_id':imported['snapshot_id']})
                if index+1<len(urls):
                    page.wait_for_timeout(2500)
            install_candidate(saved,context.cookies())
            print(json.dumps({'status':'resolved','count':len(resolved),'items':resolved},separators=(',',':')))
        finally:
            browser.close()

if __name__=='__main__':
    main()
