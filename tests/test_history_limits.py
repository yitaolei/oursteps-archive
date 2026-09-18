import _test_config
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from oursteps.store import Store
from oursteps.history import prepare, backfill
from oursteps.cli import run
from oursteps.parser import thread_url


class HistoryLimitsTests(unittest.TestCase):
    def store(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        store=Store(tmp.name);self.addCleanup(store.db.close);prepare(store)
        return store

    def test_thread_limit_and_unlimited(self):
        for limit,expected in ((2,2),(None,3)):
            with self.subTest(limit=limit):
                s=self.store()
                with s.db:
                    for tid in (1,2,3):
                        s.db.execute("INSERT INTO threads(tid,discovered_via) VALUES(?,'test')",(tid,))
                        s.db.execute('INSERT INTO inventory VALUES(?,0,0)',(tid,))
                with patch('oursteps.history.Fetcher'),patch('oursteps.preview.build'),patch('oursteps.cli.run',return_value='complete') as work:
                    result=backfill(s,now=True,max_threads=limit)
                self.assertEqual(work.call_count,expected)
                self.assertEqual(len({c.kwargs['tids'][0] for c in work.call_args_list}),expected)
                self.assertEqual(result,'budget_reached' if limit else 'complete')
                self.assertTrue(all('deadline' not in c.kwargs for c in work.call_args_list))

    def test_pause_does_not_sleep_past_deadline(self):
        s=self.store()
        with s.db:
            s.db.execute("INSERT INTO threads(tid,discovered_via) VALUES(1,'test')")
            s.db.execute('INSERT INTO inventory VALUES(1,0,0)')
        def work(*args,**kwargs):
            self.assertEqual(kwargs['deadline'],60)
            s.set_setting('pause_until',10000)
            return 'retry_pending'
        with patch('oursteps.history.Fetcher'),patch('oursteps.preview.build'),patch('oursteps.cli.run',side_effect=work),patch('oursteps.history.time.monotonic',return_value=0),patch('oursteps.history.time.time',return_value=100),patch('oursteps.history.time.sleep') as sleep:
            self.assertEqual(backfill(s,max_minutes=1),'budget_reached')
            sleep.assert_not_called()

    def test_backfill_incremental_preview_success_clears_durable_pending(self):
        s=self.store()
        with s.db:
            s.db.execute("INSERT INTO threads(tid,discovered_via) VALUES(1,'test')")
            s.db.execute('INSERT INTO inventory VALUES(1,0,0)')
        def work(store,**kwargs):
            with store.db:store.db.execute("UPDATE threads SET status='complete' WHERE tid=1")
            return 'complete'
        fake=dict(tids=[1],rendered_articles=1,full_rebuild=False,dry_run=False,elapsed_seconds=0.01)
        with patch('oursteps.history.Fetcher'),patch('oursteps.cli.run',side_effect=work),patch('oursteps.preview_batch.build_batch',return_value=fake) as incremental:
            backfill(s,now=True,max_threads=1)
        incremental.assert_called_once_with(s,[1])
        self.assertEqual(s.setting('historical_preview_pending_tids'),'[]')

    def test_backfill_partial_tid_stays_durable_pending_and_is_not_rendered(self):
        s=self.store()
        with s.db:
            s.db.execute("INSERT INTO threads(tid,discovered_via) VALUES(1,'test')")
            s.db.execute('INSERT INTO inventory VALUES(1,0,0)')
        with patch('oursteps.history.Fetcher'),patch('oursteps.cli.run',return_value='partial'),patch('oursteps.preview_batch.build_batch') as incremental:
            backfill(s,now=True,max_threads=1)
        incremental.assert_not_called()
        self.assertEqual(s.setting('historical_preview_pending_tids'),'[1]')

    def test_backfill_incremental_failure_keeps_pending_and_fails_closed(self):
        s=self.store()
        with s.db:
            s.db.execute("INSERT INTO threads(tid,discovered_via) VALUES(1,'test')")
            s.db.execute('INSERT INTO inventory VALUES(1,0,0)')
        def work(store,**kwargs):
            with store.db:store.db.execute("UPDATE threads SET status='complete' WHERE tid=1")
            return 'complete'
        with patch('oursteps.history.Fetcher'),patch('oursteps.cli.run',side_effect=work),patch('oursteps.preview_batch.build_batch',side_effect=ValueError('fixture stage failure')):
            with self.assertRaisesRegex(ValueError,'fixture stage failure'):
                backfill(s,now=True,max_threads=1)
        self.assertEqual(s.setting('historical_preview_pending_tids'),'[1]')
        perf=json.loads((s.root/'backfill-performance.json').read_text())
        self.assertEqual(perf['preview_mode'],'failed')
        self.assertIn('fixture stage failure',perf['preview_error'])

    def test_backfill_recovers_completed_pending_without_recrawl(self):
        s=self.store()
        with s.db:
            s.db.execute("INSERT INTO threads(tid,status,discovered_via) VALUES(1,'complete','test')")
            s.db.execute('INSERT INTO inventory VALUES(1,0,0)')
            s.db.execute("INSERT OR REPLACE INTO settings VALUES('historical_preview_pending_tids','[1]')")
        fake=dict(tids=[1],rendered_articles=1,full_rebuild=False,dry_run=False,elapsed_seconds=0.01)
        with patch('oursteps.history.Fetcher'),patch('oursteps.cli.run',side_effect=AssertionError('must not recrawl')),patch('oursteps.preview_batch.build_batch',return_value=fake) as incremental:
            backfill(s,now=True,max_threads=1)
        incremental.assert_called_once_with(s,[1])
        self.assertEqual(s.setting('historical_preview_pending_tids'),'[]')

    def test_deadline_preserves_committed_page_and_leaves_next_pending(self):
        s=self.store();tid=1902000;s.seed([tid],'test')
        fixtures=Path(__file__).parent/'fixtures'
        for page,name in ((1,'thread-first.html'),(2,'thread-second.html')):
            s.snapshot(thread_url(tid,page),(fixtures/name).read_bytes())
        clock=[0];save=s.save_page
        def saved(*args,**kwargs):
            save(*args,**kwargs);clock[0]=60
        with patch('oursteps.cli.time.monotonic',side_effect=lambda:clock[0]),patch.object(s,'save_page',side_effect=saved):
            self.assertEqual(run(s,tids=[tid],deadline=60),'budget_reached')
        self.assertEqual(s.db.execute('SELECT count(*) FROM pages').fetchone()[0],1)
        self.assertEqual(s.db.execute('SELECT state FROM jobs WHERE page=2').fetchone()[0],'pending')
        self.assertEqual(s.db.execute('SELECT status FROM threads').fetchone()[0],'partial')
