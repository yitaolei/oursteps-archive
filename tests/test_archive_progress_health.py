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
 def test_browser_recovery_does_not_hide_http_halt(self):
  body='{"awaiting_backfill": 20075, "run_state": "halted: inspect existing halt before retrying"}'
  browser={'last_result':{'status':'completed','at':1000,'tid':1801055,'published':True}}
  x=report(body,'',browser,now=1200)
  self.assertEqual(x['health'],'BROWSER_RECOVERY_PARTIAL')
  self.assertTrue(x['history_halt_seen'])
  self.assertTrue(x['browser_lane_recent'])
 def test_old_browser_recovery_cannot_mask_halt(self):
  body='{"run_state": "halted: inspect existing halt before retrying"}'
  browser={'last_result':{'status':'completed','at':1000}}
  self.assertEqual(report(body,'',browser,now=50000)['health'],'BLOCKED')
 def test_inventory_counts_override_stale_history_report(self):
  body='{"awaiting_backfill": 20075, "run_state": "halted: inspect existing halt before retrying"}'
  browser={'inventory_counts':{'incomplete':20073,'complete':14250}}
  x=report(body,'',browser,now=2000)
  self.assertEqual(x['awaiting_backfill'],20073)
  self.assertEqual(x['historical_complete'],14250)
 def test_browser_site_hold_is_visible_even_if_http_log_is_missing(self):
  x=report('', '',{'site_hold':{'reason':'browser_site_access_restricted'}},now=2000)
  self.assertEqual(x['health'],'BROWSER_SITE_HOLD')
  self.assertTrue(x['browser_site_hold'])
