import inspect
import unittest
from scripts.browser_fallback_candidates import historical_queue_audit
class QueueAuditTests(unittest.TestCase):
 def test_read_only_and_excludes_denied_from_browser_candidates(self):
  source=inspect.getsource(historical_queue_audit)
  self.assertIn('mode=ro',source)
  self.assertIn('blocked_or_manual',source)
  self.assertIn('eligible_incomplete_html',source)
  self.assertNotIn('UPDATE ',source)
