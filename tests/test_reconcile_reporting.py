import _test_config  # Configure synthetic identity before application imports.
import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'.deps'),str(Path(__file__).resolve().parents[1]/'scripts')]
from oursteps import reconcile as r
from oursteps.store import Store
from oursteps.history import prepare
from oursteps.parser import Busy,query
from test_reconcile import Threads
from reconcile_oursteps import arguments

class ReportingTests(unittest.TestCase):
    def test_distinct_budgets_and_compatible_cli(self):
        args=arguments(['--backfill-missing','--max-backfill','10'])
        self.assertEqual((args.max_backfill,args.max_requests,args.max_seconds),(10,40,300))
        self.assertEqual(arguments(['--status','--max-logical-requests','12']).max_requests,12)
        transport=object.__new__(r.LimitedTransport)
        transport.deadline=200;transport.remaining=0;transport.requests=0
        with patch.object(r.time,'time',return_value=100),self.assertRaises(r.BudgetReached):transport.budget()
        self.assertEqual(transport.exhausted_reason,'logical_request_budget')
        transport.deadline=50;transport.remaining=20
        with patch.object(r.time,'time',return_value=100),self.assertRaises(r.BudgetReached):transport.budget()
        self.assertEqual(transport.exhausted_reason,'time_budget');self.assertEqual(transport.requests,0)
    def test_worker_busy_counted_once_without_changing_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);live=Store(root/'data');prepare(live);live.db.close()
            state=r.State(root);cache=Store(root/'data/reconcile-cache')
            try:
                with state.db:state.add_candidate(dict(tid=1902000),'fixture')
                class BusyPage(Threads):
                    def get(self,url):
                        if query(url).get('page')=='2':raise Busy('fixture_busy')
                        return super().get(url)
                transport=BusyPage(cache)
                self.assertEqual(r.recover_one(state,cache,transport,1902000),'retry_later')
                self.assertEqual(int(state.setting('tea_busy_pauses')),1)
                job=dict(cache.db.execute("SELECT * FROM jobs WHERE page=2").fetchone())
                self.assertEqual(job['error'],'transient:Busy')
                pause=state.setting('pause_until')
                # Revisiting the same persisted error must not count a new Busy event.
                with state.db:r.count_worker_busy(state,[job])
                self.assertEqual(int(state.setting('tea_busy_pauses')),1)
                self.assertEqual(dict(cache.db.execute("SELECT * FROM jobs WHERE page=2").fetchone()),job)
                self.assertEqual(state.setting('pause_until'),pause)
                job['attempts']+=1
                with state.db:r.count_worker_busy(state,[job])
                self.assertEqual(int(state.setting('tea_busy_pauses')),2)
            finally:cache.db.close();state.db.close()
