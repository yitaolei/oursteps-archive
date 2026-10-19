#!/usr/bin/env python3
"""Mac-only Playwright fallback for JS-rendered OurSteps thread pages."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from oursteps.auth import STATE, security_check, session_digest
from oursteps.auth_verify import identity
from oursteps.config import UID
from oursteps.deployment import deployment, remote_python, ssh_argv
from oursteps.fetch import validate_url
from oursteps.parser import ParseError, parse_thread, query, soup_of, thread_url
from oursteps.fetch import AGENT
from oursteps.store import atomic_write
from scripts.browser_snapshot_stream import PROTOCOL, InfrastructureStop, StreamImporter
from scripts.process_utils import install_signal_cleanup,run_group
install_signal_cleanup()

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
            expected_q,current_q=query(url),query(page.url)
            if (current.path!='/bbs/forum.php'
                    or current_q.get('mod')!='viewthread'
                    or current_q.get('tid')!=expected_q.get('tid')
                    or current_q.get('page','1')!=expected_q.get('page','1')):
                raise RuntimeError('browser_fallback_navigation_identity_mismatch')
            content=page.content().encode('utf-8')
            soup=soup_of(content)
            if identity(soup):
                try:
                    parse_thread(content,url)
                    return content
                except ParseError as exc:
                    if str(exc)=='page_identity_mismatch':
                        raise InfrastructureStop(str(exc)) from exc
                    last=str(exc)
            else:
                last='identity_not_confirmed'
        except Error:
            last='browser_navigation_in_progress'
        page.wait_for_timeout(500)
    error='browser_fallback_unresolved:'+last
    if last=='browser_navigation_in_progress':
        raise InfrastructureStop(error)
    raise RuntimeError(error)

def install_candidate(saved,cookies):
    candidate=dict(saved,
        cookies=[c for c in cookies if c.get('domain','').lstrip('.') in ('oursteps.com.au','www.oursteps.com.au')],
        origins=[],uid=UID,verified_at=time.time())
    host,_=deployment()
    check=run_group(
        ssh_argv(host)+[remote_python('scripts/verify_auth.py','--candidate')],
        input=json.dumps(candidate),text=True,timeout=120)
    if check.returncode:
        detail=(check.stderr or check.stdout).strip().splitlines()[-1:] or ['unknown']
        raise RuntimeError('browser_session_handoff_failed:'+detail[0][:200])
    # NAS verify_auth --candidate installs the session with mode 0600.
    # NEVER write STATE via the Mac SMB mount: SMB can create it as mode 0777.
    atomic_write(ROOT/'data/auth-status.json',json.dumps(dict(
        category='success',at=time.time(),retry_at=0,attempts=0,session_reused=True,
        uid=UID,session_sha256=session_digest(candidate))).encode())

def import_snapshot(url,content):
    host,_=deployment()
    run=run_group(
        ssh_argv(host)+[remote_python('scripts/import_browser_snapshot.py','--url',url)],
        input=content,text=False,timeout=60)
    if run.returncode:
        detail=(run.stderr or run.stdout).decode('utf-8','replace').strip().splitlines()[-1:] or ['unknown']
        raise RuntimeError('browser_snapshot_import_failed:'+detail[0][:200])
    return json.loads(run.stdout.decode('utf-8').strip().splitlines()[-1])

def build_previews(tids):
    tids=sorted(set(int(tid) for tid in tids))
    if not tids:
        return
    host,_=deployment()
    run=run_group(
        ssh_argv(host)+[remote_python('archive.py','build-preview','--tids',*[str(tid) for tid in tids])],
        text=True,timeout=300)
    if run.returncode:
        detail=(run.stderr or run.stdout).strip().splitlines()[-1:] or ['unknown']
        raise RuntimeError('browser_preview_build_failed:'+detail[0][:200])

def session_result(page,context,saved,urls,historical=False):
    """One serial Chrome session. Only the launcher flushes historical preview."""
    from playwright.sync_api import Error, TimeoutError as PlaywrightTimeoutError
    started=time.monotonic()
    resolved=[];failures=[];stop_reason=None;handoff_error=None
    attempted=0;previewed=0;importer=None;navigation_seconds=0;handoff_seconds=0
    handoff_status='skipped_stop';importer_timings={};importer_commit_seconds=0
    def failure(url,exc):
        detail=type(exc).__name__+':'+str(exc)[:200]
        failures.append({'url':url,'error':detail})
        return detail
    try:
        if historical:
            host,_=deployment()
            importer=StreamImporter(ssh_argv(host)+[remote_python('scripts/import_browser_snapshot.py','--stream')])
            importer.start()  # Require ready before requesting any site page.
        for index,url in enumerate(urls):
            response=None
            try:
                if importer:
                    importer.ensure_alive()
                attempted+=1
                navigation_started=time.monotonic()
                try:
                    try:
                        response=page.goto(url,wait_until='domcontentloaded',timeout=60000)
                    except PlaywrightTimeoutError:
                        pass
                    except Error as exc:
                        raise InfrastructureStop('browser_navigation_failed:'+str(exc)) from exc
                    content=page_content(page,url,response)
                finally:
                    navigation_seconds+=time.monotonic()-navigation_started
                if importer:
                    try:
                        imported=importer.import_page(url,content)
                    except Exception as exc:
                        # A streaming ACK/EOF/protocol outcome can be uncertain;
                        # historical recovery must stop rather than request more pages.
                        raise InfrastructureStop(str(exc)) from exc
                else:
                    # Preserve the legacy incremental behavior: a one-page import
                    # failure is recorded below and the next URL may still proceed.
                    imported=import_snapshot(url,content)
                importer_commit_seconds+=imported.get('import_seconds',0)
                resolved.append({'url':url,'snapshot_id':imported['snapshot_id'],
                                 'thread_status':imported.get('thread_status')})
            except Exception as exc:
                detail=failure(url,exc)
                text=str(exc).lower()
                if (isinstance(exc,InfrastructureStop) or 'manual_required:' in text
                        or 'off_origin_navigation' in text
                        or 'navigation_identity_mismatch' in text):
                    stop_reason=detail
                    break
            if index+1<len(urls):
                page.wait_for_timeout(2500)
    except Exception as exc:
        stop_reason=failure(None,exc)
    finally:
        if importer:
            try:
                # Wait for process exit/lock release before the launcher flushes.
                importer.close()
            except Exception as exc:
                detail=failure(None,exc)
                stop_reason=stop_reason or detail
            importer_timings=importer.timings
    complete_tids=[int(query(item['url'])['tid']) for item in resolved
                   if item.get('thread_status')=='complete']
    if not historical:
        try:
            build_previews(complete_tids)
            previewed=len(set(complete_tids))
        except Exception as exc:
            detail=failure(None,exc)
            stop_reason=stop_reason or detail
    # A stop forbids further site requests, including session verification. For
    # a normally processed session, hand off exactly once, after all page ACKs.
    if stop_reason is None:
        handoff_started=time.monotonic()
        try:
            install_candidate(saved,context.cookies())
            handoff_status='success'
        except Exception as exc:
            handoff_error=type(exc).__name__+':'+str(exc)[:200]
            handoff_status='failed'
        finally:
            handoff_seconds=time.monotonic()-handoff_started
    return {'status':'stopped' if stop_reason else ('resolved' if resolved else 'unresolved'),
            'protocol_version':PROTOCOL if historical else None,
            'elapsed_seconds':round(time.monotonic()-started,6),
            'attempted':attempted,'imported':len(resolved),
            'complete':sum(item.get('thread_status')=='complete' for item in resolved),
            'partial':sum(item.get('thread_status')=='partial' for item in resolved),
            'count':len(resolved),'failed':len(failures),'previewed':previewed,
            'preview_deferred':historical,'stop_reason':stop_reason,
            'handoff_error':handoff_error,'handoff_status':handoff_status,
            'timings':dict(navigation_seconds=round(navigation_seconds,6),
                           handoff_seconds=round(handoff_seconds,6),
                           importer_commit_seconds=round(importer_commit_seconds,6),
                           importer=importer_timings),
            'items':resolved,'failures':failures}


def arguments(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--historical',action='store_true',help='One streaming session of up to 80 URLs; defer preview to launcher')
    parser.add_argument('urls',nargs='+')
    args=parser.parse_args(argv)
    if len(args.urls)>(80 if args.historical else 20):
        parser.error('too many URLs (historical: 80; incremental: 20)')
    args.urls=list(dict.fromkeys(canonical_thread(value) for value in args.urls))
    return args


def main():
    if sys.platform!='darwin':
        raise SystemExit('browser_fallback_must_run_on_mac')
    args=arguments()
    started=time.monotonic()
    saved=saved_session()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        try:
            context=browser.new_context(storage_state={'cookies':saved.get('cookies',[]),'origins':[]},user_agent=AGENT)
            page=context.new_page()
            result=session_result(page,context,saved,args.urls,historical=args.historical)
        finally:
            browser.close()
    result['elapsed_seconds']=round(time.monotonic()-started,6)
    print(json.dumps(result,separators=(',',':')))
    if result['stop_reason'] or result['handoff_error']:
        raise SystemExit(2)

if __name__=='__main__':
    main()
