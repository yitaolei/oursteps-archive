import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('archive_worker_status',ROOT/'scripts/archive_worker_status.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class ArchiveWorkerStatusTests(unittest.TestCase):
    def test_elapsed_parser(self):
        self.assertEqual(m._elapsed_seconds('05:30'),330)
        self.assertEqual(m._elapsed_seconds('01:02:03'),3723)
        self.assertEqual(m._elapsed_seconds('2-03:04:05'),183845)
        self.assertIsNone(m._elapsed_seconds('bad'))

    def test_local_backfill_pipeline_keeps_status_running(self):
        sample='123 05:30 /usr/bin/python3 /repo/scripts/history_launcher.py backfill --best-effort --now\n'
        with patch.object(m.subprocess,'check_output',return_value=sample),patch.object(m.time,'time',return_value=1000):
            data=m.local_pipeline()
        self.assertEqual(data['kind'],'historical_backfill')
        self.assertEqual(data['pid'],123)
        self.assertEqual(data['started_at'],670)

    def test_local_incremental_pipeline_detected(self):
        sample='456 00:42 /usr/bin/python3 /repo/scripts/update_now.py --now\n'
        with patch.object(m.subprocess,'check_output',return_value=sample),patch.object(m.time,'time',return_value=1000):
            data=m.local_pipeline()
        self.assertEqual(data['kind'],'incremental_sync')
        self.assertEqual(data['started_at'],958)

    def test_browser_lane_is_visible_without_changing_nas_guard(self):
        sample='789 01:20 /usr/bin/python3 /repo/scripts/historical_browser_burst.py --max-runs 3\n'
        with patch.object(m.subprocess,'check_output',return_value=sample),patch.object(m.time,'time',return_value=1000):
            browser=m.browser_lane_pipeline()
        self.assertEqual(browser['kind'],'historical_backfill')
        self.assertEqual(browser['started_at'],920)
        with patch.object(m,'browser_lane_pipeline',return_value=browser),patch.object(m,'probe',side_effect=AssertionError('should not query NAS')):
            result=m.public_status()
        self.assertEqual(result['state'],'RUNNING')
        self.assertEqual(result['task'],'historical_backfill')

if __name__=='__main__':
    unittest.main()
