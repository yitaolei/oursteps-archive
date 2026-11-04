#!/usr/bin/env python3
"""Opt-in, single-page Studio daily recovery; never connect to NAS."""
import argparse
import fcntl
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from urllib.robotparser import RobotFileParser

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.deps')]
from scripts.studio_local_authority import allow_local_authority
from scripts.browser_fallback import saved_session, page_content
from scripts.import_browser_snapshot import save_snapshot
from oursteps.parser import BASE, thread_url
from oursteps.store import Store
from oursteps.fetch import (AGENT, ROBOTS_AGENT, in_window, longest_rule_allowed,
                            next_adaptive_delay, session, validate_url)


def cached_policy(store, url, now):
    snap = store.latest(BASE + '/robots.txt')
    if not snap or snap['status'] != 200 or not 0 <= now - snap['fetched_at'] <= 86400:
        raise ValueError('fresh_local_robots_snapshot_required')
    lines = store.raw(snap).decode('utf-8', 'strict').splitlines()
    if not any(re.match(r'^\s*user-agent\s*:', line, re.I) for line in lines):
        raise ValueError('invalid_robots_response')
    windows = [line.split(':', 1)[1].strip() for line in lines
               if re.match(r'^\s*visit-time\s*:', line, re.I)]
    if windows and set(windows) != {'1400-2200'}:
        raise ValueError('visit_time_changed_review_required')
    if not longest_rule_allowed(lines, url):
        raise ValueError('robots_disallow')
    robot = RobotFileParser()
    robot.parse(lines)
    rate = robot.request_rate(ROBOTS_AGENT)
    return max(5., robot.crawl_delay(ROBOTS_AGENT) or 0,
               rate.seconds / rate.requests if rate else 0)


def pacing_wait(store, delay, now):
    last = float(store.setting('last_request', '0'))
    adaptive = float(store.setting('adaptive_delay', '0'))
    if any(not math.isfinite(value) or value < 0 for value in (last, adaptive, delay, now)):
        raise ValueError('invalid_pacing_state')
    return max(0., max(delay, adaptive) - (now - last))


def run(tid):
    if type(tid) is not int or tid <= 0:
        raise ValueError('invalid_tid')
    if os.environ.get('OURSTEPS_STUDIO_DAILY_BROWSER') != '1':
        raise ValueError('daily_browser_default_off')
    data = allow_local_authority(ROOT / 'data')
    url = thread_url(tid)
    validate_url(url)
    with (data / 'worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store = Store(data)
        try:
            delay = cached_policy(store, url, time.time())
            saved = saved_session()
            session(store, saved_override=saved)
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel='chrome', headless=True)
                try:
                    context = browser.new_context(storage_state={'cookies': saved['cookies'], 'origins': []}, user_agent=AGENT, service_workers='block')
                    page = context.new_page()
                    navigation_allowed = True
                    def route_once(route):
                        nonlocal navigation_allowed
                        request = route.request
                        if (navigation_allowed and request.is_navigation_request()
                                and request.url == url and request.frame == page.main_frame):
                            navigation_allowed = False
                            route.continue_()
                        else:
                            route.abort()
                    page.route('**/*', route_once)
                    if not in_window():
                        raise ValueError('outside_visit_window')
                    wait = pacing_wait(store, delay, time.time())
                    if wait:
                        time.sleep(wait)
                    if not in_window():
                        raise ValueError('outside_visit_window')
                    delay = cached_policy(store, url, time.time())
                    if pacing_wait(store, delay, time.time()) > 0:
                        raise ValueError('shared_rate_changed')
                    started = time.time()
                    store.set_setting('last_request', started)
                    try:
                        response = page.goto(url, wait_until='domcontentloaded', timeout=60000)
                        if response is None or response.status != 200:
                            raise ValueError('browser_http_denied_or_unexpected')
                        content = page_content(page, url, response)
                    finally:
                        adaptive = float(store.setting('adaptive_delay', '0'))
                        store.set_setting('adaptive_delay', next_adaptive_delay(adaptive, max(0, time.time() - started), False))
                finally:
                    browser.close()
            out = save_snapshot(store, url, content)
            return {'tid': tid, 'result': out['status'], 'thread_status': out['thread_status']}
        finally:
            store.db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('tid', type=int)
    args = parser.parse_args()
    print(json.dumps(run(args.tid)))


if __name__ == '__main__':
    main()
