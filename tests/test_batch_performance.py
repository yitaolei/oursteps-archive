import _test_config  # Configure synthetic identity before application imports.
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_batch_tools as fixtures
batch=fixtures.batch;ops=fixtures.ops

class PerformanceBatchTests(unittest.TestCase):
    fake=fixtures.BatchTests.fake
    run_it=fixtures.BatchTests.run_it
    def test_budget_no_fixed_sleep(self):
        call=self.fake(states=[dict(run_state='budget_reached'),dict(run_state='budget_reached',imported_this_run=10)])
        self.run_it(call)
        self.assertEqual(self.sleeps,[])
        values=next(v for a,v in self.calls if a=='reconcile')
        self.assertEqual(values,dict(max_seconds=1800,max_requests=240))
    def test_local_budget_with_site_deadline_still_waits(self):
        call=self.fake(states=[dict(run_state='budget_reached',retry_at=300),dict(run_state='recovered',imported_this_run=10)])
        self.run_it(call);self.assertEqual(self.sleeps,[215])
    def test_auto_budget_rpc_and_manual_defaults(self):
        with patch.object(ops,'call',return_value='{}') as run:
            ops.operate(dict(action='reconcile',max_seconds=900,max_requests=120))
            self.assertEqual(run.call_args[0][1][-4:],['--max-seconds','900','--max-logical-requests','120'])
        with self.assertRaises(ValueError):ops.operate(dict(action='reconcile',max_seconds=0))
        import reconcile_oursteps
        a=reconcile_oursteps.arguments(['--backfill-missing'])
        self.assertEqual((a.max_seconds,a.max_requests),(300,40))
    def test_batch_metric_totals(self):
        result=self.run_it(self.fake(states=[dict(run_state='budget_reached',performance=dict(totals=dict(network_fetches=2))),dict(run_state='recovered',imported_this_run=10,performance=dict(totals=dict(network_fetches=3)))]))
        self.assertEqual(result['performance']['totals']['network_fetches'],5)
        self.assertEqual(set(result['performance']['phases_seconds']),{'preview','publish','healthcheck'})
