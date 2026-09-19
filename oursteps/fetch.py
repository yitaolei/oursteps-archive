"""Allowlisted transport with optional explicitly provided private cookie session."""
from .config import UID
import datetime as dt
import http.cookiejar
import json
import random
import stat
from pathlib import Path
from email.utils import parsedate_to_datetime
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit, unquote, urljoin
from urllib.robotparser import RobotFileParser
from .parser import BASE, Blocked, Busy, query, soup_of, guard, NotFound

AGENT = 'OurStepsPersonalArchive/0.1 (bounded archive)'

class RateLimited(Busy):
    def __init__(self, delay):
        super().__init__('http_429')
        self.retry_after = delay

def retry_after(value):
    try:
        return max(0, float(value))
    except (TypeError, ValueError):
        try:
            return max(0, parsedate_to_datetime(value).timestamp()-time.time())
        except (TypeError, ValueError, OverflowError):
            return 300

def session(store, saved_override=None):
    """Explicit private Netscape cookie export; never inspect browser credential databases."""
    jar = http.cookiejar.MozillaCookieJar()
    path = os.environ.get('OURSTEPS_COOKIE_FILE')
    if not path or saved_override is not None:
        state = Path(__file__).resolve().parents[1]/'.secrets'/'session.json'
        if saved_override is None and not state.exists():
            return jar, 'anonymous'
        if saved_override is None and stat.S_IMODE(state.stat().st_mode) & 0o077:
            raise Blocked('auth_required: session permissions; run archive.py auth')
        try:
            saved=saved_override if saved_override is not None else json.loads(state.read_text())
            if saved.get('uid')!=UID: raise ValueError()
            for c in saved['cookies']:
                if c['domain'].lstrip('.') not in ('oursteps.com.au','www.oursteps.com.au'): continue
                expiry=c.get('expires',-1)
                if expiry>0 and expiry<=time.time(): continue
                jar.set_cookie(http.cookiejar.Cookie(0,c['name'],c['value'],None,False,c['domain'],True,c['domain'].startswith('.'),c.get('path','/'),True,True,int(expiry) if expiry>0 else None,expiry<=0,None,None,{},False))
        except (KeyError,ValueError,TypeError):
            raise Blocked('auth_required: invalid session; run archive.py auth') from None
        if not any(c.name.endswith('_auth') and c.value for c in jar):
            raise Blocked('auth_required: expired session; run archive.py auth')
        return jar, 'authenticated_private'
    path = Path(path).expanduser().resolve()
    project = Path(__file__).resolve().parent.parent
    if project == path or project in path.parents or store.root == path or store.root in path.parents:
        raise Blocked('cookie_file_must_be_outside_project_and_archive')
    if not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise Blocked('cookie_file_requires_owner_only_permissions')
    try:
        jar.load(str(path), ignore_discard=True, ignore_expires=False)
    except (OSError, http.cookiejar.LoadError):
        raise Blocked('invalid_cookie_file') from None
    for cookie in list(jar):
        if cookie.domain.lstrip('.') not in ('oursteps.com.au', 'www.oursteps.com.au'):
            jar.clear(cookie.domain, cookie.path, cookie.name)
        else:
            cookie.secure = True
    if not any(c.name.endswith('_auth') and c.value for c in jar):
        raise Blocked('session_auth_cookie_missing_or_expired')
    return jar, 'authenticated_private'

class OutsideWindow(Exception):
    pass

def in_window(now=None, window=('1400', '2200')):
    now = now or dt.datetime.now(dt.timezone.utc)
    minutes = now.hour*60 + now.minute
    start, end = [int(t[:2])*60+int(t[2:]) for t in window]
    return start <= minutes < end if start < end else minutes >= start or minutes < end

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def longest_rule_allowed(lines, url):
    """Longest applicable rule, including Disallow after Allow: /."""
    groups, agents, rules = [], [], []
    for line in lines + ['User-agent: __end__']:
        line = line.split('#',1)[0].strip()
        if ':' not in line:
            continue
        key,value = [v.strip() for v in line.split(':',1)]
        key = key.lower()
        if key == 'user-agent':
            if rules:
                groups.append((agents,rules))
                agents,rules = [],[]
            agents.append(value.lower())
        elif key in ('allow','disallow') and agents:
            rules.append((key,value))
    matching=[]
    best=-1
    for names,entries in groups:
        sizes=[0 if name=='*' else len(name) for name in names if name=='*' or name in AGENT.lower()]
        if not sizes:
            continue
        score=max(sizes)
        if score>best:
            matching,best=[],score
        if score==best:
            matching.extend(entries)
    u=urlsplit(url)
    path=unquote(u.path+('?' + u.query if u.query else ''))
    hits=[]
    for key,value in matching:
        if not value:
            continue
        pattern=re.escape(unquote(value)).replace(r'\*','.*')
        if pattern.endswith(r'\$'):
            pattern=pattern[:-2]+'$'
        if re.match(pattern,path):
            hits.append((len(value.replace('*','').rstrip('$')),key=='allow'))
    return max(hits)[1] if hits else True

def validate_url(url):
    u, q = urlsplit(url), query(url)
    if u.scheme != 'https' or u.netloc != 'www.oursteps.com.au' or u.username or u.password:
        raise Blocked('off_origin_url')
    from .guide_discovery import is_guide_url
    if is_guide_url(url):
        return
    if u.path == '/bbs/search.php' and q.get('mod')=='forum' and q.get('adv')=='yes' and set(q)<={'mod','adv'}:
        return
    if u.path == '/robots.txt' and not u.query:
        return
    if (u.path == '/bbs/forum.php' and q.get('mod') == 'viewthread' and
        q.get('tid', '').isdigit() and q.get('page', '1').isdigit() and
        set(q) <= {'mod', 'tid', 'page'}):
        return
    if (u.path == '/bbs/home.php' and q.get('mod')=='space' and q.get('uid')==str(UID)
            and q.get('do')=='thread' and q.get('view')=='me' and q.get('order','dateline')=='dateline'
            and q.get('page','1').isdigit() and set(q)<={'mod','uid','do','view','order','from','page'}):
        return
    raise Blocked('network_route_not_enabled_in_pilot')

def next_adaptive_delay(current, latency, healthy):
    decay = 0.8 if healthy else 0.9
    return min(120, max(latency*2, current*decay))

class Fetcher:
    def __init__(self, store, wait=False, saved_session=None):
        self.store, self.wait = store, wait
        self.robot, self.delay, self.window = None, 10.0, ('1400', '2200')
        jar, self.mode = session(store,saved_session)
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPCookieProcessor(jar))

    def sleep(self, seconds, detail_key='pacing_base_seconds'):
        from .performance import measure
        def _sleep():
            started=time.monotonic()
            try:
                return time.sleep(seconds)
            finally:
                perf=getattr(self.store,'performance',None)
                if perf is not None:
                    perf.add(detail_key,time.monotonic()-started)
        return measure(self.store, 'throttle_seconds', _sleep)

    def open_response(self, request, **kwargs):
        from .performance import measure
        perf = getattr(self.store, 'performance', None)
        if perf is not None:
            perf.add('network_fetches')
            if self.store.db.execute('SELECT 1 FROM snapshots WHERE url=? LIMIT 1',(request.full_url,)).fetchone():perf.add('prior_snapshot_fetches')
            from urllib.parse import parse_qs
            q = parse_qs(urlsplit(request.full_url).query)
            if q.get('tid') and q.get('page'):
                identity = (q['tid'][0], q['page'][0])
                perf.add('thread_page_fetches')
                if identity in perf.pages: perf.add('repeated_page_fetches')
                perf.pages.add(identity)
                if self.store.db.execute("SELECT 1 FROM pages WHERE tid=? AND page=? AND result='parsed'", identity).fetchone():
                    perf.add('successful_page_refetches')
        try:
            return measure(self.store, 'network_seconds', self.opener.open, request, **kwargs)
        except Exception as error:
            if perf is not None:
                perf.add('network_errors')
                if isinstance(error,TimeoutError) or isinstance(getattr(error,'reason',None),TimeoutError):perf.add('network_timeouts')
            raise

    def read_response(self, response, limit):
        from .performance import measure
        try:
            return measure(self.store, 'network_seconds', response.read, limit)
        except Exception as error:
            perf=getattr(self.store,'performance',None)
            if perf is not None:
                perf.add('network_errors')
                if isinstance(error,TimeoutError):perf.add('network_timeouts')
            raise

    def wait_window(self):
        if getattr(self,'daily_now',False):
            return
        # Explicit, process-local override requested for the existing 20-thread pilot.
        # Does not change robots path rules, pacing, retries, or stop safeguards.
        if os.environ.get('OURSTEPS_PILOT_NOW') == '1':
            count = self.store.db.execute('SELECT count(*) FROM threads').fetchone()[0]
            if count != 20:
                raise Blocked('run_now_requires_exactly_20_pilot_threads')
            return
        while not in_window(window=self.window):
            if not self.wait:
                raise OutsideWindow('outside_1400_2200_UTC; no request made')
            time.sleep(60)

    def request(self, url, _redirects=()):
        validate_url(url)
        self.wait_window()
        base_pace=max(self.delay, random.uniform(2,5))
        adaptive=float(self.store.setting('adaptive_delay', '0'))
        pace=max(base_pace,adaptive)
        self.sleep(max(0, pace-(time.time()-float(self.store.setting('last_request', '0')))),
                   'pacing_adaptive_seconds' if adaptive>base_pace else 'pacing_base_seconds')
        self.wait_window()
        self.store.set_setting('last_request', time.time())
        req = urllib.request.Request(url, headers={'User-Agent':AGENT, 'Accept':'text/html,text/plain'})
        try:
            response = self.open_response(req, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body, status = self.read_response(response, 5*1024*1024+1), response.code
            snap = self.store.snapshot(url, body, status=status, mode=self.mode)
            latency = time.time()-float(self.store.setting('last_request', '0'))
            adaptive = float(self.store.setting('adaptive_delay', '0'))
            healthy = status == 200 and self.store.setting('error_streak','0') == '0'
            self.store.set_setting('adaptive_delay', next_adaptive_delay(adaptive, latency, healthy))
            if status == 429:
                raise RateLimited(retry_after(response.headers.get('Retry-After')))
            if status in (404,410):
                raise NotFound('http_%s' % status)
            if status in (301,302,303,307,308) and getattr(self.store,'historical_mode',False):
                target = urljoin(url,response.headers.get('Location',''))
                source_q, target_q = query(url), query(target)
                if (source_q.get('mod') == target_q.get('mod') == 'viewthread'
                        and source_q.get('tid') == target_q.get('tid')
                        and source_q.get('tid','').isdigit()):
                    validate_url(target)
                    if target in _redirects or target == url or len(_redirects) >= 4:
                        raise ValueError('canonical_redirect_loop')
                    if not longest_rule_allowed(self.rules,target):
                        raise Blocked('robots_disallow')
                    cached = self.store.latest(target)
                    if cached is not None and cached['status'] == 200:
                        return cached
                    return self.request(target,_redirects+(url,))
                from .auth_verify import VerificationIssue
                login = target_q.get('mod') == 'logging' or target_q.get('action') == 'login'
                raise VerificationIssue('auth_required' if login else 'unexpected_layout',
                                        'login_redirect' if login else 'noncanonical_redirect')
            if status in (403,401,407) or 300 <= status < 400:
                raise Blocked('http_%s; redirect/auth/rate-limit not followed' % status)
            if status >= 500:
                raise Busy('http_%s' % status)
            if status != 200:
                raise Blocked('http_%s' % status)
            if len(body) > 5*1024*1024:
                raise Blocked('response_size_limit')
            ct = response.headers.get('Content-Type', '')
            if not ('html' in ct or 'text/plain' in ct):
                raise Blocked('unexpected_content_type')
            if urlsplit(url).path != '/robots.txt':
                from .performance import measure
                soup = measure(self.store,'parse_seconds',soup_of,body)
                if self.mode == 'authenticated_private':
                    from .auth_verify import inspect_response
                    measure(self.store,'parse_seconds',inspect_response,body,status)
                else:
                    measure(self.store,'parse_seconds',guard,soup)
            return snap

    def policy(self):
        if self.robot is not None:
            return
        self.wait_window()
        snap = self.request(BASE+'/robots.txt')
        lines = self.store.raw(snap).decode('utf-8','replace').splitlines()
        if not any(line.lower().startswith('user-agent:') for line in lines):
            raise Blocked('invalid_robots_response')
        robot = RobotFileParser()
        robot.parse(lines)
        rate = robot.request_rate(AGENT)
        self.delay = max(2.0, robot.crawl_delay(AGENT) or 0, rate.seconds/rate.requests if rate else 0)
        windows = re.findall(r'(?im)^visit-time:\s*(\d{4})-(\d{4})\s*$', '\n'.join(lines))
        if windows and set(windows) != {('1400','2200')}:
            raise Blocked('visit_time_changed_review_required')
        self.robot = robot
        self.rules = lines

    def get(self, url):
        self.policy()
        if not longest_rule_allowed(self.rules,url):
            raise Blocked('robots_disallow')
        return self.request(url)
