#!/usr/bin/env python3
"""Explicit, single-page Studio historical recovery; no scheduler or network policy fetch."""
import argparse
import fcntl
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib.robotparser import RobotFileParser

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.deps')]
from oursteps.fetch import AGENT, ROBOTS_AGENT, in_window, longest_rule_allowed, next_adaptive_delay, session
from oursteps.auth_verify import inspect_response, VerificationIssue
from oursteps.parser import BASE, thread_url
from oursteps.store import Store, atomic_write
from scripts.browser_fallback import page_content, saved_session
from scripts.import_browser_snapshot import save_snapshot
from scripts.studio_local_authority import allow_local_authority

LEGACY_STATE = Path.home() / 'Library/Application Support/OurSteps/control-plane/data/historical-browser-lane-state.json'


def eligible(db, tid, page, now):
    """Read-only candidate decision, including all sibling denial states."""
    if type(tid) is not int or type(page) is not int or tid < 1 or page < 1:
        return False
    url = thread_url(tid, page)
    row = db.execute("""SELECT 1 FROM jobs j JOIN inventory i ON i.tid=j.tid
        JOIN threads t ON t.tid=j.tid WHERE j.url=? AND j.tid=? AND j.page=?
        AND j.kind='thread' AND t.status!='complete' AND COALESCE(j.retry_at,0)<=?
        AND ((j.state='retry_later' AND j.error='retry_later:incomplete_html')
          OR (j.state='pending' AND j.page>1 AND j.error IS NULL))
        AND NOT EXISTS (SELECT 1 FROM jobs d WHERE d.tid=j.tid AND
          (d.state IN ('auth_required','permanently_skipped') OR lower(COALESCE(d.error,'')) LIKE '%403%'
           OR lower(COALESCE(d.error,'')) LIKE '%429%' OR lower(COALESCE(d.error,'')) LIKE '%robots%'
           OR lower(COALESCE(d.error,'')) LIKE '%auth%' OR lower(COALESCE(d.error,'')) LIKE '%site_hold%'))""", (url, tid, page, now)).fetchone()
    return bool(row)


def pacing_wait(now, last, adaptive, robot_delay):
    values = (now, last, adaptive, robot_delay)
    import math
    if any(not math.isfinite(x) or x < 0 for x in values):
        raise ValueError('invalid_pacing_state')
    return max(0., max(5., adaptive, robot_delay) - (now - last))


def policy(lines, url):
    if not any(re.match(r'^\s*user-agent\s*:', line, re.I) for line in lines):
        raise ValueError('invalid_robots_response')
    windows = re.findall(r'(?im)^visit-time:\s*(\d{4})-(\d{4})\s*$', '\n'.join(lines))
    if windows and set(windows) != {('1400', '2200')}:
        raise ValueError('visit_time_changed_review_required')
    if not longest_rule_allowed(lines, url):
        raise ValueError('robots_disallow')
    robot = RobotFileParser(); robot.parse(lines)
    rate = robot.request_rate(ROBOTS_AGENT)
    return max(2., robot.crawl_delay(ROBOTS_AGENT) or 0, rate.seconds / rate.requests if rate else 0)


def check_hold(data):
    for path in (LEGACY_STATE, data / 'studio-historical-browser-hold.json'):
        if path.exists():
            state = json.loads(path.read_text())
            if not isinstance(state, dict) or state.get('site_hold'):
                raise ValueError('site_hold')


def check_db(db, tid, page, now):
    if not eligible(db, tid, page, now):
        raise ValueError('candidate_not_due_or_denied')
    settings = dict(db.execute('SELECT key,value FROM settings'))
    if settings.get('halt') or any(float(settings.get(k, '0')) > now for k in ('pause_until', 'historical_pause_until')):
        raise ValueError('halt_or_pause')
    return settings


def require_shared_rate_coordination():
    # studio_browser_recover_one.py navigates before taking worker.lock and
    # does not reserve last_request. Until that path coordinates, refuse.
    raise RuntimeError('shared_rate_coordination_unavailable')


def run(tid, page):
    data = allow_local_authority(ROOT / 'data')
    if os.environ.get('OURSTEPS_STUDIO_HISTORICAL_BROWSER') != '1':
        raise ValueError('historical_browser_default_off')
    if not (data / 'archive.sqlite3').is_file():
        raise ValueError('local_database_missing')
    receipt_path = data / 'studio-historical-browser-receipts' / (str(tid) + '.json')
    receipt = {'tid': tid, 'page': page, 'at': time.time(), 'status': 'started'}
    atomic_write(receipt_path, json.dumps(receipt).encode())
    store = None
    try:
        with (data / 'worker.lock').open('a') as worker_lock:
            fcntl.flock(worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            check_hold(data)
            store = Store(data)
            url = thread_url(tid, page)
            check_db(store.db, tid, page, time.time())
            snap = store.latest(BASE + '/robots.txt')
            if not snap or snap['status'] != 200 or not 0 <= time.time() - snap['fetched_at'] <= 86400:
                raise ValueError('fresh_local_robots_snapshot_required')
            lines = store.raw(snap).decode('utf-8', 'strict').splitlines()
            delay = policy(lines, url)
            saved = saved_session()
            session(store, saved_override=saved)  # validate project UID and unexpired auth cookie
            require_shared_rate_coordination()
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel='chrome', headless=True)
                try:
                    context = browser.new_context(storage_state={'cookies': saved['cookies'], 'origins': []}, user_agent=AGENT)
                    browser_page = context.new_page()
                    # Block redirects, subresources and scripts: no unpaced extra requests.
                    navigation_allowed = True
                    def route_once(route):
                        nonlocal navigation_allowed
                        request = route.request
                        if (navigation_allowed and request.is_navigation_request()
                                and request.url == url and request.frame == browser_page.main_frame):
                            navigation_allowed = False
                            route.continue_()
                        else:
                            route.abort()
                    browser_page.route('**/*', route_once)
                    check_hold(data)
                    settings = check_db(store.db, tid, page, time.time())
                    if not in_window(): raise ValueError('outside_visit_window')
                    wait = pacing_wait(time.time(), float(settings.get('last_request', '0')), float(settings.get('adaptive_delay', '0')), delay)
                    if wait: time.sleep(wait)
                    check_hold(data)
                    settings = check_db(store.db, tid, page, time.time())
                    if not in_window(): raise ValueError('outside_visit_window')
                    if pacing_wait(time.time(), float(settings.get('last_request', '0')), float(settings.get('adaptive_delay', '0')), delay) > 0:
                        raise ValueError('shared_rate_changed')
                    started = time.time()
                    store.set_setting('last_request', started)
                    response = browser_page.goto(url, wait_until='domcontentloaded', timeout=60000)
                    if response is None: raise ValueError('missing_response')
                    if response.status in (401, 403, 407, 429):
                        atomic_write(data / 'studio-historical-browser-hold.json', json.dumps({'site_hold': {'at': time.time(), 'reason': 'browser_site_access_restricted'}}).encode())
                        raise ValueError('browser_site_access_restricted')
                    if response.status != 200: raise ValueError('unexpected_http_status')
                    try:
                        inspect_response(response.body(), response.status)
                    except VerificationIssue as exc:
                        if exc.category in ('auth_required', 'permission_or_challenge') or any(x in str(exc).lower() for x in ('403', '429', 'auth', 'login')):
                            atomic_write(data / 'studio-historical-browser-hold.json', json.dumps({'site_hold': {'at': time.time(), 'reason': 'browser_site_access_restricted'}}).encode())
                        raise
                    content = page_content(browser_page, url, response)
                    store.set_setting('adaptive_delay', next_adaptive_delay(float(store.setting('adaptive_delay', '0')), time.time()-started, True))
                finally:
                    browser.close()
            check_hold(data)
            check_db(store.db, tid, page, time.time())
            result = save_snapshot(store, url, content, historical_pending=True)
            receipt.update(status='imported', snapshot_id=result['snapshot_id'], thread_status=result['thread_status'])
    except Exception as exc:
        if 'manual_required' in str(exc).lower():
            atomic_write(data / 'studio-historical-browser-hold.json', json.dumps({'site_hold': {'at': time.time(), 'reason': 'browser_site_access_restricted'}}).encode())
        receipt.update(status='stopped', reason=type(exc).__name__ + ':' + str(exc)[:160])
        raise
    finally:
        if store is not None: store.db.close()
        receipt['finished_at'] = time.time()
        atomic_write(receipt_path, json.dumps(receipt, sort_keys=True).encode())
    return receipt


def main():
    p = argparse.ArgumentParser(); p.add_argument('--tid', type=int, required=True); p.add_argument('--page', type=int, required=True)
    args = p.parse_args()
    if args.tid < 1 or args.page < 1: p.error('tid and page must be positive')
    print(json.dumps(run(args.tid, args.page), sort_keys=True))

if __name__ == '__main__': main()
