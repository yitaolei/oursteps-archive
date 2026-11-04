"""Isolated, offline integration coverage for daily browser recovery."""
import _test_config
import contextlib
import fcntl
import os
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from oursteps.parser import BASE, thread_url
from oursteps.store import Store
from scripts import studio_browser_recover_one as daily
from test_browser_stream import content, TID


class DailyRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.store = Store(self.data)
        self.store.snapshot(BASE + '/robots.txt', b'User-agent: *\nAllow: /\nCrawl-delay: 5\n', status=200)
        self.now = time.time()
        self.store.set_setting('last_request', self.now - 2)
        self.store.set_setting('adaptive_delay', 10)
        self.clock = types.SimpleNamespace(time=lambda: self.now, sleep=self.sleep)
        self.sleeps = []
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {'OURSTEPS_STUDIO_DAILY_BROWSER': '1'}))
        for name, value in [('allow_local_authority', lambda _: self.data), ('Store', lambda _: self.store),
                            ('time', self.clock), ('saved_session', lambda: {'cookies': []}),
                            ('session', lambda *a, **k: None), ('in_window', lambda: True),
                            ('page_content', lambda *a: content(1))]:
            self.stack.enter_context(patch.object(daily, name, value))
        self.page = MagicMock()
        self.response = self.page.goto.return_value
        self.response.status = 200
        browser = MagicMock()
        browser.new_context.return_value.new_page.return_value = self.page
        playwright = MagicMock()
        playwright.chromium.launch.return_value = browser
        module = types.ModuleType('playwright.sync_api')
        module.sync_playwright = MagicMock()
        module.sync_playwright.return_value.__enter__.return_value = playwright
        self.stack.enter_context(patch.dict('sys.modules', {'playwright': types.ModuleType('playwright'), 'playwright.sync_api': module}))

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def locked(self):
        with (self.data / 'worker.lock').open('a') as probe:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_lock_pacing_and_actual_page_one_import(self):
        original = daily.save_snapshot
        def navigate(*args, **kwargs):
            self.locked()
            self.assertEqual(float(self.store.setting('last_request')), self.now)
            return self.response
        def save(*args, **kwargs):
            self.locked()
            return original(*args, **kwargs)
        self.page.goto.side_effect = navigate
        with patch.object(daily, 'save_snapshot', side_effect=save):
            result = daily.run(TID)
        self.assertEqual(self.sleeps, [8])
        self.assertEqual(result['result'], 'imported')
        check = Store(self.data)
        try:
            self.assertEqual(check.db.execute('SELECT page FROM pages WHERE tid=?', (TID,)).fetchone()[0], 1)
            self.assertEqual(float(check.setting('adaptive_delay')), 9)
        finally:
            check.db.close()
        route = self.page.route.call_args.args[1]
        request = MagicMock()
        request.request.is_navigation_request.return_value = True
        request.request.url = thread_url(TID)
        request.request.frame = self.page.main_frame
        route(request); route(request)
        request.continue_.assert_called_once(); request.abort.assert_called_once()

    def test_lock_contention_stops_before_navigation(self):
        with (self.data / 'worker.lock').open('a') as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError): daily.run(TID)
        self.page.goto.assert_not_called()
        self.store.db.close()

    def test_fail_closed(self):
        cases = ('disallow', 'stale', 'outside', 'default_off', 'denied_http', 'rate_limited')
        for case in cases:
            with self.subTest(case=case):
                with contextlib.ExitStack() as stack:
                    if case == 'disallow':
                        stack.enter_context(patch.object(daily, 'longest_rule_allowed', return_value=False))
                    if case == 'stale': self.now += 86401
                    if case == 'outside': stack.enter_context(patch.object(daily, 'in_window', return_value=False))
                    if case == 'default_off': stack.enter_context(patch.dict(os.environ, {'OURSTEPS_STUDIO_DAILY_BROWSER': '0'}))
                    if case in ('denied_http', 'rate_limited'): self.response.status = 403 if case == 'denied_http' else 429
                    with patch.object(daily, 'save_snapshot') as save:
                        with self.assertRaises(ValueError): daily.run(TID)
                        save.assert_not_called()
                    if case not in ('denied_http', 'rate_limited'): self.page.goto.assert_not_called()
                if case == 'stale': self.now -= 86401
                # Each run closes its store; reopen for the next case.
                try: self.store.db.close()
                except Exception: pass
                self.store = Store(self.data)
        self.store.db.close()


if __name__ == '__main__': unittest.main()
