"""Mac-only Keychain credentials; isolated Playwright login, no browser-profile access."""
from .deployment import deployment, remote_python
from .config import UID, USERNAME
import getpass
import json
import os
import sys
import subprocess
import random
import time
from pathlib import Path
from .parser import directory_url, query, soup_of, parse_discovery
from .store import atomic_write, redact_raw
ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'.secrets'/'session.json'
SERVICE='oursteps-archive'
ACCOUNT=USERNAME

from .auth_verify import identity, inspect_response, VerificationIssue

def credentials(interactive=False):
    # Explicit backend prevents accidental fallback to a plaintext keyring.
    from keyring.backends.macOS import Keyring
    ring=Keyring()
    password=ring.get_password(SERVICE,ACCOUNT)
    if password: return ACCOUNT,password
    if not interactive: raise RuntimeError('credentials_required: run Authenticate OurSteps.command in Terminal')
    username=input(f'OurSteps username [{ACCOUNT}]: ').strip() or ACCOUNT
    if username.lower()!=ACCOUNT.lower(): raise RuntimeError(f'Expected {USERNAME} / UID {UID}')
    password=getpass.getpass('OurSteps password (hidden): ')
    if not password: raise RuntimeError('Empty password; nothing saved')
    ring.set_password(SERVICE,ACCOUNT,password)
    return ACCOUNT,password

def security_check(page,response=None):
    if response and response.status in (401,403,429):
        raise RuntimeError('manual_required: HTTP %s; stopped without retry'%response.status)
    body=page.locator('body').inner_text()
    if any(s in body.lower() for s in ('captcha','security verification','验证码','安全验证','两步验证','动态口令','验证问题','访问过于频繁')):
        raise RuntimeError('manual_required: site security verification; automatic login stopped')
    if page.locator('input[name="seccodeverify"]:visible, input[autocomplete="one-time-code"]:visible').count():
        raise RuntimeError('manual_required: CAPTCHA/MFA; automatic login stopped')

def diagnostic(stage,content,issue,status=None):
    record=dict(stage=stage,category=issue.category,reason=issue.reason,status=status,at=time.time(),html=content.decode('utf-8','replace'))
    result=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',deployment()[0],remote_python('scripts/auth_diagnostic.py')],input=json.dumps(record),capture_output=True,text=True)
    if result.returncode: raise VerificationIssue('diagnostic_io_error','Could not preserve verification diagnostic')
    return result.stdout.strip()

def checked_get(page,url,directory=False):
    from playwright.sync_api import Error
    import random
    from .fetch import retry_after
    for attempt in range(2):
        content=b'';response=None
        try:
            response=page.goto(url,wait_until='domcontentloaded',timeout=60000)
            if response is None:raise VerificationIssue('retry_later','missing_response',True)
            content=response.body()
            inspect_response(content,response.status,url if directory else None)
            return content
        except Error:
            issue=VerificationIssue('retry_later','network_or_browser_transport',True)
        except VerificationIssue as e:issue=e
        except Exception as e:issue=VerificationIssue('parser_bug',type(e).__name__)
        diagnostic(url,content,issue,response.status if response else None)
        if not issue.retry:raise issue
        delay=max(30*2**attempt*random.uniform(1,1.3),retry_after(response.headers.get('retry-after')) if response and response.status==429 else 0)
        if attempt==1 or (response and response.status==429):
            issue.retry_after=delay
            raise issue
        time.sleep(delay)

def bootstrap(interactive=None):
    from playwright.sync_api import sync_playwright
    from .dates import cached_display_offset
    if interactive is None:interactive=sys.stdin.isatty()
    # Never chmod the shared directory through SMB, or discard a known-good state.
    os.umask(0o077)
    status_path=ROOT/'data/auth-status.json'
    previous=json.loads(status_path.read_text()) if status_path.exists() else {}
    if previous.get('retry_at',0)>time.time():
        raise SystemExit('retry_later: persistent verification backoff active; session preserved')
    try:
        saved=json.loads(STATE.read_text()) if STATE.exists() else {}
        session_reused=True
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            try:
                context=browser.new_context(storage_state={'cookies':saved.get('cookies',[]),'origins':[]})
                page=context.new_page()
                try:checked_get(page,directory_url(),directory=True)
                except VerificationIssue as issue:
                    # Only positive expiry evidence can cause credential retrieval.
                    if issue.category!='auth_required':raise
                    session_reused=False
                    username,password=credentials(interactive)
                    time.sleep(5)
                    response=page.goto('https://www.oursteps.com.au/bbs/member.php?mod=logging&action=login',wait_until='domcontentloaded',timeout=60000)
                    security_check(page,response)
                    page.locator('input[name="username"]:visible').fill(username)
                    page.locator('input[name="password"]:visible').fill(password);password=None
                    page.locator('button[name="loginsubmit"]:visible, input[name="loginsubmit"]:visible').first.click()
                    time.sleep(5)
                    checked_get(page,directory_url(),directory=True)
                # Last-visit is historical, not a server clock. Reuse corroborated
                # timezone evidence separately; never infer a new offset from its age.
                cached_display_offset(saved)
                candidate=dict(saved,cookies=[c for c in context.cookies() if c['domain'].lstrip('.') in ('oursteps.com.au','www.oursteps.com.au')],origins=[],uid=UID,verified_at=time.time())
                # Verify candidate with the real NAS transport before replacing saved state.
                check=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',deployment()[0],remote_python('scripts/verify_auth.py', '--candidate')],input=json.dumps(candidate),capture_output=True,text=True)
                if check.returncode:
                    try:failure=json.loads(check.stdout.strip().splitlines()[-1])
                    except (ValueError,IndexError):raise VerificationIssue('retry_later','nas_verification_transport',True,retry_after=60)
                    raise VerificationIssue(failure.get('category','unexpected_layout'),failure.get('reason','nas_verification_failed'),failure.get('category')=='retry_later',60)
                result=json.loads(check.stdout.strip().splitlines()[-1])
                atomic_write(status_path,json.dumps(dict(category='success',at=time.time(),retry_at=0,attempts=0,session_reused=session_reused)).encode())
                print(json.dumps(result,ensure_ascii=False),flush=True)
            finally:browser.close()
    except VerificationIssue as e:
        attempts=previous.get('attempts',0)+1
        delay=max(e.retry_after,min(3600,30*2**min(attempts,7))*random.uniform(1,1.3))
        atomic_write(status_path,json.dumps(dict(category=e.category,reason=e.reason,at=time.time(),retry_at=time.time()+delay if e.retry else 0,attempts=attempts)).encode())
        raise SystemExit(e.category+': '+e.reason+'; saved session preserved') from None
    except Exception as e:
        atomic_write(status_path,json.dumps(dict(category='parser_bug',reason=type(e).__name__,at=time.time(),retry_at=0)).encode())
        raise SystemExit('verification_error: '+type(e).__name__+'; saved session preserved') from None
