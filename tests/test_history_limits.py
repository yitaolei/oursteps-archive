import _test_config
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from oursteps.store import Store
from oursteps.history import prepare, backfill, _historical_candidates
from oursteps.cli import run
from oursteps.parser import thread_url,Busy,query


class HistoryLimitsTests(unittest.TestCase):
    def store(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        store=Store(tmp.name);self.addCleanup(store.db.close);prepare(store)
        return store

    def test_fair_lanes_due_refill_and_unique_threads(self):
        s=self.store()
        with s.db:
            for tid in range(1,13):
                s.db.execute("INSERT INTO threads(tid,discovered_via) VALUES(?,'test')",(tid,))
                s.db.execute('INSERT INTO inventory VALUES(?,0,0)',(tid,))
            for tid,state,due in ((5,'pending',0),(6,'pending',0),(7,'retry_later',0),
                                  (8,'retry_later',0),(9,'pending',200),(10,'retry_later',200),
                                  (11,'auth_required',0),(12,'success',0)):
                s.db.execute('INSERT INTO jobs(url,kind,tid,page,state,retry_at) VALUES(?,?,?,?,?,?)',
                             (str(tid),'thread',tid,1,state,due))
            # One thread with both states belongs only to the pending lane.
            s.db.execute("INSERT INTO jobs(url,kind,tid,page,state) VALUES('extra','thread',5,2,'retry_later')")
        tids=[tid for tid,_ in _historical_candidates(s,100)]
        self.assertEqual(tids,[4,3,6,8,2,1,5,7])
        self.assertEqual(len(tids),len(set(tids)))
        with s.db:s.db.execute("DELETE FROM jobs WHERE tid IN (7,8)")
        tids=[tid for tid,_ in _historical_candidates(s,100)]
        self.assertEqual(set(tids),{1,2,3,4,5,6,7,8})
        self.assertEqual(len(tids),len(set(tids)))

    def test_short_runs_resume_lane_rotation(self):
        s=self.store()
        with s.db:
            for tid in (1,2,3,4):
                s.db.execute("INSERT INTO threads(tid,discovered_via) VALUES(?,'test')",(tid,))
                s.db.execute('INSERT INTO inventory VALUES(?,0,0)',(tid,))
            for tid,state in ((3,'pending'),(4,'retry_later')):
                s.db.execute('INSERT INTO jobs(url,kind,tid,page,state) VALUES(?,?,?,?,?)',
                             (str(tid),'thread',tid,1,state))
        selected=[]
        def work(store,**kwargs):
            tid=kwargs['tids'][0];selected.append(tid)
            with store.db:store.db.execute("UPDATE threads SET status='complete' WHERE tid=?",(tid,))
            return 'complete'
        with patch('oursteps.history.Fetcher'),patch('oursteps.cli.run',side_effect=work),patch('oursteps.preview_batch.build_batch',side_effect=lambda store,tids:dict(tids=tids,rendered_articles=len(tids),full_rebuild=False,dry_run=False,elapsed_seconds=0)):
            for _ in range(4):backfill(s,now=True,max_threads=1)
        self.assertEqual(selected,[2,1,3,4])

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
            s.set_setting('historical_pause_until',10000)
            return 'retry_pending'
        with patch('oursteps.history.Fetcher'),patch('oursteps.preview.build'),patch('oursteps.cli.run',side_effect=work),patch('oursteps.history.time.monotonic',return_value=0),patch('oursteps.history.time.time',return_value=100),patch('oursteps.history.time.sleep') as sleep:
            self.assertEqual(backfill(s,max_minutes=1),'budget_reached')
            sleep.assert_not_called()


    def test_historical_backoff_does_not_pollute_daily_backoff(self):
        s=self.store();tid=1902000;s.seed([tid],'test')
        s.historical_mode=True
        s.set_setting('historical_error_streak',2)
        s.set_setting('error_streak',7)
        s.set_setting('pause_until',12345)
        class Fetch:
            def wait_window(self): pass
            def get(self,url): raise Busy('site_busy')
        result=run(s,tids=[tid],fetcher_override=Fetch())
        self.assertEqual(result,'retry_later')
        self.assertEqual(s.setting('historical_error_streak'),'3')
        self.assertEqual(float(s.setting('historical_pause_until','0')),0)
        self.assertEqual(s.setting('error_streak'),'7')
        self.assertEqual(s.setting('pause_until'),'12345')

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

    def test_page_slice_yields_to_other_thread_and_resumes_after_restart(self):
        s=self.store();long_tid=1902000;short_tid=1902001
        s.seed([long_tid,short_tid],'test')
        with s.db:
            for tid in (long_tid,short_tid):s.db.execute('INSERT INTO inventory VALUES(?,0,0)',(tid,))
        for tid,total in ((long_tid,8),(short_tid,1)):
            for page in range(1,total+1):s.snapshot(thread_url(tid,page),b'synthetic')
        def parse(raw,url):
            q=query(url);tid=int(q['tid']);page=int(q.get('page',1));total=8 if tid==long_tid else 1
            return dict(tid=tid,page=page,title='synthetic',fid=1,forum='test',gaps=[],posts=[],
                        all_pids=[tid*100+page],declared_pages=total,
                        next_pages=[page+1] if page<total else [],views=0,replies=0)
        with patch('oursteps.history.Fetcher'),patch('oursteps.cli.parse_thread',side_effect=parse):
            backfill(s,now=True,per_thread_page_budget=5,defer_preview=True)
            self.assertEqual(s.db.execute('SELECT status FROM threads WHERE tid=?',(long_tid,)).fetchone()[0],'partial')
            self.assertEqual(s.db.execute('SELECT status FROM threads WHERE tid=?',(short_tid,)).fetchone()[0],'complete')
            self.assertEqual([tuple(r) for r in s.db.execute('SELECT page,state FROM jobs WHERE tid=? ORDER BY page',(long_tid,))],
                             [(1,'success'),(2,'success'),(3,'success'),(4,'success'),(5,'success'),(6,'pending')])
            s.db.close();s.db=Store(s.root).db
            backfill(s,now=True,per_thread_page_budget=5,defer_preview=True)
        self.assertEqual(s.db.execute('SELECT status FROM threads WHERE tid=?',(long_tid,)).fetchone()[0],'complete')
        self.assertEqual([tuple(r) for r in s.db.execute('SELECT page,state FROM jobs WHERE tid=? ORDER BY page',(long_tid,))],
                         [(page,'success') for page in range(1,9)])

    def test_page_slice_respects_daytime_page_limit(self):
        s=self.store();tid=1902000;s.seed([tid],'test');s.historical_mode=True
        for page in range(1,12):s.snapshot(thread_url(tid,page),b'synthetic')
        def parse(raw,url):
            page=int(query(url).get('page',1))
            return dict(tid=tid,page=page,title='synthetic',fid=1,forum='test',gaps=[],posts=[],
                        all_pids=[page],declared_pages=11,next_pages=[page+1] if page<11 else [],views=0,replies=0)
        with patch('oursteps.cli.parse_thread',side_effect=parse):
            self.assertEqual(run(s,tids=[tid],page_limit=10,per_thread_page_budget=5),'thread_page_budget_reached')
            self.assertEqual(run(s,tids=[tid],page_limit=10,per_thread_page_budget=5),'thread_page_budget_reached')
            self.assertEqual(run(s,tids=[tid],page_limit=10,per_thread_page_budget=5),'page_limit_reached')
        self.assertEqual(s.db.execute('SELECT state FROM jobs WHERE tid=? AND page=11',(tid,)).fetchone()[0],'pending')
        self.assertEqual(s.db.execute('SELECT status FROM threads WHERE tid=?',(tid,)).fetchone()[0],'partial')
