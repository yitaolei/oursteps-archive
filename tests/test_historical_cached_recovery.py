import unittest
from scripts.historical_cached_recovery import candidates
class QueryTests(unittest.TestCase):
 def test_offline_sql_uses_successful_cached_snapshot_only(self):
  import inspect
  source=inspect.getsource(candidates)
  self.assertIn('s2.status=200',source)
  self.assertIn("j.state!='success'",source)
  self.assertIn("t.status!='complete'",source)
