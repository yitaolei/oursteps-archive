import unittest
from scripts.archive_progress_health import report
class ProgressHealthTests(unittest.TestCase):
 def test_halt_is_exposed(self):
  body='{"awaiting_backfill": 20075, "shards_remaining": 8, "run_state": "halted: inspect existing halt before retrying"}'
  x=report(body,'OurSteps Sync 2026-10-25')
  self.assertEqual(x['health'],'BLOCKED')
  self.assertEqual(x['awaiting_backfill'],20075)
  self.assertEqual(x['discovery_shards_remaining'],8)
 def test_missing_evidence_is_unknown(self):
  self.assertEqual(report('', '')['health'],'UNKNOWN')
 def test_no_halt_is_not_claimed_healthy(self):
  self.assertEqual(report('{"awaiting_backfill": 25}', '')['health'],'ACTIVE_OR_UNVERIFIED')
