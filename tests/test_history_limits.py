import _test_config
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
