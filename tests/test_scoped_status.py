import _test_config  # Configure synthetic identity before application imports.
import sqlite3
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.deps'))
from oursteps.store import Store
from oursteps.sqlite_retry import WriterConnection
from oursteps.parser import parse_thread,thread_url
from oursteps.performance import Performance

ROOT=Path(__file__).resolve().parents[1]
class ScopedStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name));self.s.seed([1902000,999],'fixture')
        self.raw=(ROOT/'tests/fixtures/thread-first.html').read_bytes()
        self.url=thread_url(1902000);self.parsed=parse_thread(self.raw,self.url)
        self.snap=self.s.snapshot(self.url,self.raw)
    def tearDown(self):self.s.db.close();self.tmp.cleanup()
    def status(self,tid=1902000):return self.s.db.execute('SELECT status FROM threads WHERE tid=?',(tid,)).fetchone()[0]
    def page(self,n):
        if n==1:self.s.save_page(self.url,self.parsed,self.snap)
        else:
            url=thread_url(1902000,2);raw=(ROOT/'tests/fixtures/thread-second.html').read_bytes()
            self.s.save_page(url,parse_thread(raw,url),self.s.snapshot(url,raw))
    def test_save_page_only_own_tid_and_transition(self):
        with self.s.db:self.s.db.execute("UPDATE threads SET status='sentinel' WHERE tid=999")
        self.s.performance=Performance();self.page(1)
        self.assertEqual(self.status(),'partial');self.assertEqual(self.status(999),'sentinel')
        self.assertEqual(self.s.performance.values['persistence_refresh_threads_visited'],1)
        self.page(2);self.assertEqual(self.status(),'complete');self.assertEqual(self.status(999),'sentinel')
    def test_multi_matches_full_and_none_preserved(self):
        self.page(1)
        self.s.refresh_status([1902000,999,999])
        scoped=[tuple(r) for r in self.s.db.execute('SELECT * FROM threads ORDER BY tid')]
        self.s.refresh_status(None)
        self.assertEqual(scoped,[tuple(r) for r in self.s.db.execute('SELECT * FROM threads ORDER BY tid')])
        with self.s.db:self.s.db.execute("UPDATE threads SET status='sentinel' WHERE tid=999")
        self.s.refresh_status();self.assertEqual(self.status(999),'pending')
    def test_gap_fail_and_empty_invalid_unknown(self):
        self.page(1);self.page(2)
        with self.s.db:self.s.db.execute('INSERT INTO gaps VALUES(?,0,?)',(self.url,'fixture'))
        self.s.refresh_status([1902000]);self.assertEqual(self.status(),'partial')
        with self.s.db:self.s.db.execute('DELETE FROM gaps')
        self.s.refresh_status([1902000]);self.assertEqual(self.status(),'complete')
        self.s.refresh_status([]);self.s.refresh_status([123456]);self.assertEqual(self.status(),'complete')
        for bad in [[0],[-1],[True],['1902000']]:
            with self.assertRaises(ValueError):self.s.refresh_status(bad)
        with self.s.db:self.s.db.execute("UPDATE threads SET status='sentinel' WHERE tid=999")
        job=self.s.db.execute('SELECT * FROM jobs WHERE url=?',(self.url,)).fetchone()
        self.s.fail(job,'fixture',state='parse_error')
        self.assertEqual(self.status(),'partial');self.assertEqual(self.status(999),'sentinel')
    def test_sql_page_failure_rolls_back(self):
        self.s.db.execute("CREATE TEMP TRIGGER abort_page BEFORE INSERT ON pages BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.page(1)
        self.assertEqual(self.status(),'pending')
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM posts').fetchone()[0],0)
        self.assertFalse(self.s.db.in_transaction)
    def test_commit_failure_rolls_back(self):
        with patch.object(WriterConnection,'commit',side_effect=sqlite3.OperationalError('fixture commit')):
            with self.assertRaises(sqlite3.OperationalError):self.page(1)
        self.assertEqual(self.status(),'pending');self.assertFalse(self.s.db.in_transaction)
        self.page(1);self.assertEqual(self.status(),'partial')
    def test_refresh_sql_failure_keeps_partial_and_retries(self):
        self.page(1)
        self.s.db.execute("CREATE TEMP TRIGGER abort_complete BEFORE UPDATE OF status ON threads WHEN NEW.status='complete' BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.page(2)
        self.assertEqual(self.status(),'partial');self.assertFalse(self.s.db.in_transaction)
        self.s.db.execute('DROP TRIGGER abort_complete')
        self.s.refresh_status([1902000]);self.assertEqual(self.status(),'complete')
    def test_refresh_commit_failure_keeps_partial(self):
        self.page(1)
        url=thread_url(1902000,2);raw=(ROOT/'tests/fixtures/thread-second.html').read_bytes();parsed=parse_thread(raw,url);snap=self.s.snapshot(url,raw)
        original=WriterConnection.commit;calls=[]
        def commit(db):
            calls.append(1)
            if len(calls)==2:raise sqlite3.OperationalError('fixture status commit')
            return original(db)
        with patch.object(WriterConnection,'commit',commit):
            with self.assertRaises(sqlite3.OperationalError):self.s.save_page(url,parsed,snap)
        self.assertEqual(self.status(),'partial');self.assertFalse(self.s.db.in_transaction)
        self.s.refresh_status([1902000]);self.assertEqual(self.status(),'complete')
    def test_raw_failure_no_snapshot_row(self):
        before=self.s.db.execute('SELECT count(*) FROM snapshots').fetchone()[0]
        with patch('oursteps.store.os.replace',side_effect=OSError('fixture raw')):
            with self.assertRaises(OSError):self.s.snapshot(self.url,self.raw+b'new')
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM snapshots').fetchone()[0],before)
        self.assertEqual(self.status(),'pending')
    def test_locked_refresh_retries(self):
        self.s.db.execute('PRAGMA busy_timeout=1') # fixture only, never a production setting
        other=sqlite3.connect(str(self.s.root/'archive.sqlite3'),timeout=.001)
        try:
            other.execute('BEGIN IMMEDIATE');other.execute("UPDATE threads SET status='pending' WHERE tid=999")
            with patch('oursteps.sqlite_retry.time.sleep',side_effect=lambda _:other.commit()) as sleep:
                self.s.refresh_status([1902000])
            self.assertEqual(sleep.call_count,1);self.assertFalse(self.s.db.in_transaction)
        finally:other.close()
    def test_indexes_idempotent_and_queries_use_them(self):
        self.s.db.close();self.s=Store(Path(self.tmp.name))
        names={r[1] for r in self.s.db.execute("PRAGMA index_list('jobs')")}
        self.assertIn('idx_jobs_tid',names)
        for sql,index in [('SELECT state FROM jobs WHERE tid=?','idx_jobs_tid'),('SELECT * FROM pages WHERE tid=? AND result="parsed"','idx_pages_tid'),('SELECT count(*) FROM gaps g JOIN jobs j ON g.url=j.url WHERE j.tid=?','idx_jobs_tid')]:
            plan=' '.join(r[3] for r in self.s.db.execute('EXPLAIN QUERY PLAN '+sql,(1902000,)))
            self.assertIn(index,plan)
