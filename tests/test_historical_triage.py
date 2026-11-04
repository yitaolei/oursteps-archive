import _test_config
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import contextlib
import io
from pathlib import Path
from unittest.mock import patch, Mock
from oursteps.auth_verify import VerificationIssue

from oursteps.store import Store
from oursteps.history import prepare, _historical_candidates, backfill
from oursteps.triage import migrate, quarantined
from oursteps.cli import run
from oursteps.parser import thread_url


class TriageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.db = self.store.db
        prepare(self.store)
        for tid in (1, 2, 3, 4):
            self.db.execute("INSERT INTO threads(tid,discovered_via) VALUES(?,'inventory')", (tid,))
            self.db.execute('INSERT INTO inventory(tid) VALUES(?)', (tid,))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_699_pages_and_completion_remain_incomplete(self):
        url = thread_url(1)
        self.db.execute("INSERT INTO jobs(url,kind,tid,page,state) VALUES(?,'thread',1,1,'success')", (url,))
        self.db.execute("INSERT INTO pages(url,tid,page,all_pids,declared_pages,result) VALUES(?,1,1,'[42]',699,'parsed')", (url,))
        self.db.commit()
        self.store.refresh_status([1])
        self.assertEqual(self.db.execute('SELECT status FROM threads WHERE tid=1').fetchone()[0], 'partial')
        self.assertIn(1, quarantined(self.db))
        self.assertNotIn(1, [tid for tid, _ in _historical_candidates(self.store, 1, True)])
        self.assertEqual(self.db.execute('SELECT declared_pages FROM pages WHERE tid=1').fetchone()[0], 699)

    def test_reasons_persist_clear_and_dry_run(self):
        url = thread_url(2)
        self.db.execute("INSERT INTO jobs(url,kind,tid,page,state,error) VALUES(?,'thread',2,1,'parse_error','repeated_page_pid_set')", (url,))
        self.db.execute("INSERT INTO gaps VALUES(?,123,'deleted_author_identity_unknown')", (url,))
        self.db.commit()
        before = self.db.total_changes
        report = migrate(self.db)
        self.assertEqual(self.db.total_changes, before)
        self.assertEqual(report['items'][0]['tid'], 2)
        self.assertEqual(len(report['items'][0]['reasons']), 2)
        for options in ({'apply': True}, {'clear': True}):
            with self.assertRaises(ValueError):
                migrate(self.db, **options)
        self.assertEqual(self.db.total_changes, before)
        self.assertEqual(self.db.execute('SELECT count(*) FROM gaps').fetchone()[0], 1)

    def test_incomplete_html_is_not_quarantine_evidence(self):
        url = thread_url(2)
        self.db.execute("INSERT INTO jobs(url,kind,tid,page,state,error) VALUES(?,'thread',2,1,'retry_later','retry_later:incomplete_html')", (url,))
        self.db.execute("INSERT INTO pages(url,tid,page,result,error) VALUES(?,2,1,'unparsed','incomplete_html')", (url,))
        self.db.execute("INSERT INTO gaps VALUES(?,0,'retry_later:incomplete_html')", (url,))
        self.db.commit()
        self.assertNotIn(2, quarantined(self.db))

    def test_two_passes_never_retry_failed_first_pages(self):
        fetch = Mock()
        fetch.get.side_effect = VerificationIssue('retry_later', 'incomplete_html', True)
        with patch('oursteps.history.Fetcher', return_value=fetch):
            backfill(self.store, first_page_sweep=True, defer_preview=True)
            self.db.execute("UPDATE jobs SET retry_at=0")
            self.db.commit()
            backfill(self.store, first_page_sweep=True, defer_preview=True)
        self.assertEqual(fetch.get.call_count, 4)
        self.assertEqual(self.db.execute('SELECT count(*) FROM jobs WHERE page=1 AND attempts=1').fetchone()[0], 4)

    def test_cached_parse_attempt_is_recorded_and_complete_thread_untouched(self):
        url = thread_url(1)
        self.db.execute("INSERT INTO jobs(url,kind,tid,page,state) VALUES(?,'thread',1,1,'pending')", (url,))
        self.store.snapshot(url, b'<html>fixture</html>')
        self.db.execute("UPDATE threads SET status='complete',title='preserve' WHERE tid=2")
        self.db.execute("UPDATE threads SET status='complete' WHERE tid IN (3,4)")
        self.db.commit()
        with patch('oursteps.history.Fetcher') as fetch, patch('oursteps.cli.parse_thread', side_effect=ValueError('fixture')):
            backfill(self.store, first_page_sweep=True, defer_preview=True, max_threads=1)
            self.assertEqual(fetch.return_value.get.call_count, 0)
        self.assertEqual(self.db.execute('SELECT attempts FROM jobs WHERE url=?', (url,)).fetchone()[0], 1)
        self.assertEqual(tuple(self.db.execute('SELECT status,title FROM threads WHERE tid=2').fetchone()), ('complete', 'preserve'))

    def test_global_holds_prohibit_fetch(self):
        for key, value in [('halt', 'http_302; redirect/auth/rate-limit not followed'), ('historical_js_challenge_hold', 'held')]:
            self.store.set_setting(key, value)
            with patch('oursteps.history.Fetcher') as fetch:
                backfill(self.store, first_page_sweep=True, defer_preview=True)
                fetch.assert_not_called()
            self.store.set_setting(key, '')

    def test_first_denial_stops_sweep(self):
        for denial in ('auth_required', 'permission_denied', 'manual_required'):
            with self.subTest(denial=denial):
                self.store.set_setting('historical_first_page_site_hold', '')
                self.store.set_setting('halt', '')
                self.store.historical_auth_streak = 0
                fetch = Mock()
                fetch.get.side_effect = VerificationIssue(denial, 'security_challenge')
                with patch('oursteps.history.Fetcher', return_value=fetch):
                    result = backfill(self.store, first_page_sweep=True, defer_preview=True)
                self.assertEqual(result, 'historical_first_page_site_hold')
                self.assertEqual(fetch.get.call_count, 1)
                evidence = json.loads(self.store.setting('historical_first_page_site_hold'))
                self.assertEqual(set(evidence), {'tid', 'at', 'status'})
                self.assertEqual(evidence['status'], denial)
                with patch('oursteps.history.Fetcher') as blocked:
                    self.assertEqual(backfill(self.store, first_page_sweep=True, defer_preview=True), result)
                    self.assertEqual(backfill(self.store, defer_preview=True), result)
                    blocked.assert_not_called()
                self.store.historical_mode = True
                try:
                    self.assertEqual(run(self.store), result)
                finally:
                    self.store.historical_mode = False
                self.store.seed([99], 'test')
                with patch.object(self.store, 'latest', side_effect=RuntimeError('daily reached job')):
                    with self.assertRaisesRegex(RuntimeError, 'daily reached job'):
                        run(self.store)

    def test_cli_dry_run_does_not_write(self):
        path = Path(self.tmp.name) / 'archive.sqlite3'
        before = path.read_bytes()
        from scripts import historical_triage
        with patch.object(historical_triage, 'checked_local_data', return_value=path.parent.resolve()), patch.object(historical_triage, 'require_local_mount'), patch.object(sys, 'argv', ['historical_triage.py', '--db', str(path)]), contextlib.redirect_stdout(io.StringIO()) as output:
            historical_triage.main()
        self.assertTrue(json.loads(output.getvalue())['dry_run'])
        self.assertEqual(path.read_bytes(), before)
        for option in ('--apply', '--clear'):
            result = subprocess.run([sys.executable, 'scripts/historical_triage.py', '--db', str(path), option], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(path.read_bytes(), before)

    def test_cli_rejects_untrusted_paths(self):
        from scripts import historical_triage
        path = Path(self.tmp.name) / 'archive.sqlite3'
        for bad in (Path('archive.sqlite3'), path.with_name('other.sqlite3'), Path('/Volumes/share/archive.sqlite3'), Path('/Network/share/archive.sqlite3')):
            with self.subTest(path=bad), self.assertRaises((ValueError, OSError)):
                historical_triage.checked_db_path(bad)
        link = path.parent / 'link'
        link.mkdir()
        (link / 'archive.sqlite3').symlink_to(path)
        with patch.object(historical_triage, 'checked_local_data', return_value=link), self.assertRaises(ValueError):
            historical_triage.checked_db_path(link / 'archive.sqlite3')
        with patch.object(historical_triage, 'checked_local_data', side_effect=ValueError('nonlocal')), self.assertRaises(ValueError):
            historical_triage.checked_db_path(path)
        with patch.object(historical_triage.subprocess, 'run', return_value=Mock(stdout='/dev/a on / (apfs, local)\nserver on /Users/example (smbfs, nodev)\n')):
            with self.assertRaises(ValueError):
                historical_triage.require_local_mount(Path('/Users/example/data'))

    def test_sweep_runs_one_first_page_per_candidate(self):
        for page in (1, 2):
            self.db.execute("INSERT INTO jobs(url,kind,tid,page,state,retry_at) VALUES(?,'thread',1,?,'pending',0)", (thread_url(1, page), page))
        self.db.commit()
        calls = []
        def fake_run(store, **kwargs):
            calls.append(kwargs)
            return 'thread_page_budget_reached'
        with patch('oursteps.history.Fetcher'), patch('oursteps.cli.run', side_effect=fake_run):
            backfill(self.store, first_page_sweep=True, defer_preview=True)
        self.assertEqual(len(calls), 4)
        self.assertEqual(len({call['tids'][0] for call in calls}), 4)
        self.assertTrue(all(call['first_page_only'] and call['per_thread_page_budget'] == 1 and call['page_limit'] == 1 for call in calls))

    def test_sweep_candidates_and_daily_run_isolation(self):
        for page in (1, 2):
            self.db.execute("INSERT INTO jobs(url,kind,tid,page,state,retry_at) VALUES(?,'thread',1,?,'pending',?)", (thread_url(1,page), page, 10 if page == 1 else 0))
        self.db.execute("INSERT INTO daily_members VALUES('2026-01-01',1)")
        self.db.commit()
        self.assertEqual([tid for tid, _ in _historical_candidates(self.store, 20, True)], [4, 3, 2, 1])
        self.db.execute("CREATE TABLE historical_triage(tid INTEGER PRIMARY KEY,state TEXT,reasons TEXT)")
        self.db.execute("INSERT INTO historical_triage VALUES(1,'quarantine','[]')")
        self.db.commit()
        with patch.object(self.store, 'latest', side_effect=RuntimeError('daily reached job')):
            with self.assertRaisesRegex(RuntimeError, 'daily reached job'):
                run(self.store, tids=[1])
