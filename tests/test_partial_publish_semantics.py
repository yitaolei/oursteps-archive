"""Guard against losing completed articles when a later TID fails."""
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]

class PartialPublishPolicyTests(unittest.TestCase):
    def test_incremental_publishes_even_on_partial(self):
        source=(ROOT/'scripts/update_now.py').read_text()
        self.assertIn("partial=(result.returncode!=0",source)
        self.assertIn("remote_python('scripts/publish_public.py')",source)
        self.assertIn('ARCHIVE_PARTIAL_PUBLISHED: success',source)
        self.assertNotIn("if summary['status']=='success' and not summary['failures']:",source)
    def test_operator_reports_partial_distinctly(self):
        source=(ROOT/'scripts/control_action_runner.py').read_text()
        self.assertIn("run.returncode==3",source)
        self.assertIn('Partial: verified articles published',source)
        self.assertIn('success=(run.returncode==0 or partial)',source)
