import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from studio_backfill_performance import classify_performance


def report(recorded=100, status='complete', started=1, fetches=2):
    return {'recorded_at': recorded, 'status': status, 'started_threads': started,
            'performance': {'network_fetches': fetches}}


class ClassificationTests(unittest.TestCase):
    def test_bounds_and_bad_reports(self):
        for value in (None, {}, report(99.99), report(112.01), report(float('nan')),
                      report(status='unknown'), report(started=True), report(fetches=-1),
                      report(fetches=None)):
            with self.subTest(value=value):
                self.assertEqual(classify_performance(value, 100, 110, 0), ('invalid_current_performance', {}))
        for stamp in (100, 112):
            self.assertEqual(classify_performance(report(stamp), 100, 110, 0)[0], 'complete')

    def test_failure_no_work_and_exceptions(self):
        self.assertEqual(classify_performance(report(), 100, 110, 9), ('worker_failed_exit_9', {}))
        self.assertEqual(classify_performance(report(started=0, fetches=0), 100, 110, 0)[0], 'no_work')
        for state in ('waiting_window', 'manual_priority_yield', 'halted', 'interrupted'):
            self.assertEqual(classify_performance(report(status=state, started=0, fetches=0), 100, 110, 0)[0], state)


class CycleTests(unittest.TestCase):
    def run_cycle(self, result, perf):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            db = sqlite3.connect(data / 'archive.sqlite3')
            db.executescript('CREATE TABLE threads(tid INTEGER, status TEXT); CREATE TABLE snapshots(id INTEGER); CREATE TABLE inventory(id INTEGER); CREATE TABLE jobs(tid INTEGER, page INTEGER, state TEXT);')
            db.close()
            if perf is not None:
                (data / 'backfill-performance.json').write_text(json.dumps(perf))
            calls = []
            def worker(*args, **kwargs):
                calls.append(args[0])
                if isinstance(result, Exception):
                    raise result
                return subprocess.CompletedProcess(args[0], result if len(calls) == 1 else 23)
            with patch('studio_local_authority.checked_local_data', return_value=data), \
                 patch.dict(os.environ, {'OURSTEPS_STUDIO_WRITER_HANDOVER_APPROVED': '1'}), \
                 patch('subprocess.run', side_effect=worker), contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(SystemExit) as exit_code:
                    runpy.run_path(str(ROOT / 'scripts/studio_primary_backfill_cycle.py'), run_name='__main__')
            receipt = json.loads((data / 'studio-backfill-runs.jsonl').read_text())
            return exit_code.exception.code, receipt, calls, output.getvalue()

    def test_stale_and_failed_worker_do_not_publish_or_attach_stats(self):
        for rc, expected in ((0, 'invalid_current_performance'), (7, 'worker_failed_exit_7')):
            code, receipt, calls, _ = self.run_cycle(rc, report(recorded=time.time()-60))
            self.assertNotEqual(code, 0)
            self.assertEqual(receipt['worker_state'], expected)
            self.assertNotIn('network_fetches', receipt)
            self.assertNotIn('started_threads', receipt)
            self.assertEqual(len(calls), 1)

    def test_timeout_and_launch_failure_are_recorded(self):
        for error, expected in ((subprocess.TimeoutExpired('worker', 3000), 'worker_timeout'), (OSError('launch failed'), 'worker_launch_failed')):
            code, receipt, calls, _ = self.run_cycle(error, report(recorded=time.time()))
            self.assertNotEqual(code, 0)
            self.assertEqual(receipt['worker_state'], expected)
            self.assertIsNone(receipt['worker_exit'])
            self.assertNotIn('network_fetches', receipt)
            self.assertEqual(len(calls), 1)

    def test_current_work_reaches_publish(self):
        code, receipt, calls, _ = self.run_cycle(0, report(recorded=time.time()+1))
        self.assertEqual(code, 23)
        self.assertEqual(receipt['worker_state'], 'complete')
        self.assertEqual(receipt['network_fetches'], 2)
        self.assertEqual(len(calls), 2)
        self.assertIn('scripts/publish_public.py', calls[1])

    def test_no_work_alert_and_explicit_deferrals(self):
        for state, expected_code in (('complete', 1), ('waiting_window', 0), ('manual_priority_yield', 0)):
            code, receipt, calls, output = self.run_cycle(0, report(recorded=time.time()+1, status=state, started=0, fetches=0))
            self.assertEqual(code, expected_code)
            self.assertEqual(receipt['worker_state'], 'no_work' if state == 'complete' else state)
            self.assertEqual(len(calls), 1)
            if state == 'complete': self.assertIn('STUDIO_BACKFILL_ALERT', output)

    def test_historical_challenge_hold_never_publishes(self):
        code, receipt, calls, _ = self.run_cycle(0, report(recorded=time.time()+1, status='historical_js_challenge_hold', started=0, fetches=0))
        self.assertEqual(code, 1)
        self.assertEqual(receipt['worker_state'], 'historical_js_challenge_hold')
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
