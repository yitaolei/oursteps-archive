import _test_config  # Configure synthetic identity before application imports.
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.deps'))
from oursteps.store import Store
from oursteps.parser import parse_thread,thread_url
from oursteps.performance import Performance
from oursteps.persistence_diagnostics import ACTIVE

ROOT=Path(__file__).resolve().parents[1]

class DiagnosticTests(unittest.TestCase):
    def test_snapshot_page_counts_and_scope(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d));s.seed([1902000],'fixture');s.performance=Performance()
            raw=(ROOT/'tests/fixtures/thread-first.html').read_bytes();url=thread_url(1902000)
            snap=s.snapshot(url,raw);s.save_page(url,parse_thread(raw,url),snap)
            p=s.performance.report()
            self.assertEqual(p['persistence_snapshot_calls'],1)
            self.assertEqual(p['persistence_save_page_calls'],1)
            self.assertEqual(p['persistence_refresh_status_calls'],1)
            self.assertEqual(p['persistence_refresh_threads_visited'],1)
            self.assertEqual(p['persistence_commits'],3)
            self.assertEqual(p['persistence_transactions_begun'],3)
            self.assertEqual(p['persistence_raw_html_writes'],1)
            self.assertEqual(p['persistence_python_fsync_calls'],1)
            self.assertEqual(p['persistence_atomic_renames'],1)
            before=dict(s.performance.values);s.set_setting('outside','1')
            self.assertEqual(before,s.performance.values)
            s.snapshot(url,raw)
            self.assertEqual(s.performance.values['persistence_raw_html_writes'],1)
            self.assertIsNone(ACTIVE.get());s.db.close()
    def test_set_setting_skips_durable_noop_write(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d));p=Performance();token=ACTIVE.set(p)
            try:
                self.assertTrue(s.set_setting('error_streak',0))
                commits=p.values.get('persistence_commits',0)
                commit_calls=p.values.get('persistence_commit_calls',0)
                self.assertFalse(s.set_setting('error_streak',0))
                self.assertEqual(p.values.get('persistence_commits',0),commits)
                self.assertEqual(p.values.get('persistence_commit_calls',0),commit_calls)
                self.assertEqual(s.setting('error_streak'),'0')
            finally:ACTIVE.reset(token);s.db.close()

    def test_cursor_commit_rollback_semantics(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d));p=Performance();token=ACTIVE.set(p)
            try:
                with self.assertRaises(RuntimeError):
                    with s.db:
                        s.db.execute("INSERT INTO settings VALUES('rolledback','1')")
                        raise RuntimeError('fixture')
                self.assertIsNone(s.db.execute("SELECT value FROM settings WHERE key='rolledback'").fetchone())
                with s.db:s.db.executemany('INSERT INTO settings VALUES(?,?)',[('a','1'),('b','2')])
                self.assertEqual(len(list(s.db.execute("SELECT * FROM settings WHERE key IN ('a','b')"))),2)
                self.assertEqual(p.values['persistence_rollbacks'],1)
                self.assertEqual(p.values['persistence_commits'],1)
            finally:ACTIVE.reset(token);s.db.close()
    def test_diagnostics_preserve_database_and_raw_bytes(self):
        dumps=[];raw_files=[]
        with tempfile.TemporaryDirectory() as d, patch('oursteps.store.time.time',return_value=123456):
            for enabled in (False,True):
                s=Store(Path(d)/str(enabled));s.seed([1902000],'fixture')
                if enabled:s.performance=Performance()
                url=thread_url(1902000);raw=(ROOT/'tests/fixtures/thread-first.html').read_bytes()
                snap=s.snapshot(url,raw);s.save_page(url,parse_thread(raw,url),snap)
                dumps.append(list(s.db.iterdump()))
                raw_files.append((s.root/snap['path']).read_bytes());s.db.close()
        self.assertEqual(dumps[0],dumps[1]);self.assertEqual(raw_files[0],raw_files[1])

    def test_failed_write_resets_diagnostic_context(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d));s.performance=Performance()
            with patch('oursteps.store.os.replace',side_effect=OSError('fixture')):
                with self.assertRaises(OSError):s.snapshot(thread_url(1),b'fixture')
            self.assertIsNone(ACTIVE.get())
            self.assertEqual(s.db.execute('select count(*) from snapshots').fetchone()[0],0)
            s.db.close()
