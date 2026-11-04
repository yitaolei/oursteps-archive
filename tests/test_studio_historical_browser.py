import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from oursteps.parser import thread_url
from scripts import studio_historical_browser as lane


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE inventory(tid INTEGER); CREATE TABLE threads(tid INTEGER,status TEXT);
          CREATE TABLE jobs(url TEXT,kind TEXT,tid INTEGER,page INTEGER,state TEXT,retry_at REAL,error TEXT);
          CREATE TABLE settings(key TEXT,value TEXT); INSERT INTO inventory VALUES(7);
          INSERT INTO threads VALUES(7,'partial');''')
    def add(self, page, state, error=None, retry=0, tid=7):
        self.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?)', (thread_url(tid,page),'thread',tid,page,state,retry,error))
    def test_due_challenge_and_partial_continuation(self):
        self.add(1,'retry_later','retry_later:incomplete_html',10)
        self.add(2,'pending')
        self.assertTrue(lane.eligible(self.db,7,1,10))
        self.assertTrue(lane.eligible(self.db,7,2,10))
        self.assertFalse(lane.eligible(self.db,7,1,9))
        self.assertFalse(lane.eligible(self.db,7,3,10))
    def test_denials_and_membership(self):
        self.add(2,'pending'); self.add(1,'auth_required','http_403')
        self.assertFalse(lane.eligible(self.db,7,2,100))
        self.db.execute('DELETE FROM jobs WHERE page=1'); self.add(1,'retry_later','http_429')
        self.assertFalse(lane.eligible(self.db,7,2,100))
        self.db.execute('DELETE FROM jobs WHERE page=1'); self.db.execute('DELETE FROM inventory')
        self.assertFalse(lane.eligible(self.db,7,2,100))
    def test_only_exact_challenge_and_pending_continuation(self):
        self.add(1,'pending'); self.add(2,'retry_later','retry_later:incomplete_html_js_challenge')
        self.assertFalse(lane.eligible(self.db,7,1,100)); self.assertFalse(lane.eligible(self.db,7,2,100))
    def test_pacing_and_robots(self):
        self.assertEqual(lane.pacing_wait(100,98,12,8),10)
        self.assertEqual(lane.pacing_wait(100,1,0,0),0)
        with self.assertRaises(ValueError): lane.pacing_wait(100,1,float('nan'),0)
        url=thread_url(7,2)
        self.assertEqual(lane.policy(['User-agent: *','Crawl-delay: 15','Allow: /'],url),15)
        with self.assertRaisesRegex(ValueError,'robots_disallow'): lane.policy(['User-agent: *','Disallow: /bbs/'],url)
        with self.assertRaisesRegex(ValueError,'visit_time'): lane.policy(['User-agent: *','Visit-time: 0000-0100'],url)
    def test_holds_and_pause(self):
        self.add(2,'pending'); self.db.execute("INSERT INTO settings VALUES('halt','http_403')")
        with self.assertRaisesRegex(ValueError,'halt_or_pause'): lane.check_db(self.db,7,2,100)
        with tempfile.TemporaryDirectory() as tmp, patch.object(lane,'LEGACY_STATE',Path(tmp)/'legacy'):
            lane.LEGACY_STATE.write_text(json.dumps({'site_hold':{'reason':'browser_site_access_restricted'}}))
            with self.assertRaisesRegex(ValueError,'site_hold'): lane.check_hold(Path(tmp))

if __name__ == '__main__': unittest.main()

class LockingTests(unittest.TestCase):
    def setUp(self):
        import contextlib
        import sys
        import types
        from unittest.mock import MagicMock
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        (self.data / 'archive.sqlite3').touch()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        def mock(target, **kw):
            return self.stack.enter_context(patch.object(lane, target, **kw))
        mock('allow_local_authority', return_value=self.data)
        mock('LEGACY_STATE', new=self.data / 'legacy')
        self.stack.enter_context(patch.dict('os.environ', {'OURSTEPS_STUDIO_HISTORICAL_BROWSER':'1'}))
        self.store = MagicMock()
        self.store.latest.return_value = {'status':200, 'fetched_at':lane.time.time()}
        self.store.raw.return_value = b'User-agent: *\nAllow: /\n'
        self.store.setting.return_value = '0'
        mock('Store', return_value=self.store)
        mock('check_db', return_value={})
        mock('saved_session', return_value={'cookies':[]})
        mock('session')
        mock('in_window', return_value=True)
        mock('pacing_wait', return_value=0)
        mock('page_content', return_value=b'validated')
        self.browser_page = MagicMock()
        self.response = self.browser_page.goto.return_value
        self.response.status = 200
        self.response.body.return_value = b'body'
        mock('inspect_response')
        browser = MagicMock()
        browser.new_context.return_value.new_page.return_value = self.browser_page
        playwright = MagicMock()
        playwright.chromium.launch.return_value = browser
        module = types.ModuleType('playwright.sync_api')
        module.sync_playwright = MagicMock()
        module.sync_playwright.return_value.__enter__.return_value = playwright
        self.stack.enter_context(patch.dict(sys.modules, {'playwright':types.ModuleType('playwright'), 'playwright.sync_api':module}))

    def assert_worker_locked(self):
        import fcntl
        with (self.data / 'worker.lock').open('a') as probe:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_default_off(self):
        import os
        with patch.dict(os.environ, {'OURSTEPS_STUDIO_HISTORICAL_BROWSER':'0'}):
            with self.assertRaisesRegex(ValueError, 'historical_browser_default_off'): lane.run(7,2)
        self.browser_page.goto.assert_not_called()

    def test_contention_stops_before_navigation(self):
        import fcntl
        with (self.data / 'worker.lock').open('a') as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError): lane.run(7,2)
        self.browser_page.goto.assert_not_called()
        self.store.latest.assert_not_called()

    def test_uncoordinated_browser_remains_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, 'shared_rate_coordination_unavailable'):
            lane.run(7,2)
        self.browser_page.goto.assert_not_called()

    def test_owner_holds_lock_through_reservation_navigation_and_import(self):
        self.stack.enter_context(patch.object(lane, 'require_shared_rate_coordination'))
        def locked(*args, **kwargs): self.assert_worker_locked()
        self.store.set_setting.side_effect = locked
        self.browser_page.goto.side_effect = lambda *a, **k: (self.assert_worker_locked(), self.response)[1]
        def save(*args, **kwargs):
            self.assert_worker_locked()
            import subprocess, sys
            probe = subprocess.run([sys.executable, '-c',
                'import fcntl,sys; f=open(sys.argv[1],"a"); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)',
                str(self.data / 'worker.lock')], capture_output=True, text=True)
            self.assertNotEqual(probe.returncode, 0)
            self.assertIn('BlockingIOError', probe.stderr)
            self.assertEqual(args[1], thread_url(7,2))
            return {'snapshot_id':1,'thread_status':'partial'}
        self.stack.enter_context(patch.object(lane, 'save_snapshot', side_effect=save))
        result = lane.run(7,2)
        self.assertEqual(result['status'],'imported')
        self.browser_page.goto.assert_called_once_with(thread_url(7,2), wait_until='domcontentloaded', timeout=60000)
        route_handler = self.browser_page.route.call_args.args[1]
        from unittest.mock import MagicMock
        route = MagicMock()
        route.request.is_navigation_request.return_value = True
        route.request.url = thread_url(7,2)
        route.request.frame = self.browser_page.main_frame
        route_handler(route); route_handler(route)
        route.continue_.assert_called_once(); route.abort.assert_called_once()

    def test_denied_response_never_imports(self):
        self.stack.enter_context(patch.object(lane, 'require_shared_rate_coordination'))
        save = self.stack.enter_context(patch.object(lane, 'save_snapshot'))
        self.response.status = 403
        with self.assertRaisesRegex(ValueError, 'browser_site_access_restricted'): lane.run(7,2)
        save.assert_not_called()
        self.assertTrue(json.loads((self.data/'studio-historical-browser-hold.json').read_text())['site_hold'])
