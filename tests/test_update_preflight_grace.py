import ast
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'scripts/update_now.py').read_text()

class PreflightGraceTests(unittest.TestCase):
    def test_transient_errors_may_use_only_verified_session(self):
        self.assertIn("latest.get('reason') in ('cloudfront_403','network_or_browser_transport')",SOURCE)
        self.assertIn('trusted_session_for_cloudfront(session)',SOURCE)
        self.assertNotIn("latest.get('category')=='auth_required'",SOURCE)
        self.assertIn("else:\n            print((auth.stdout+auth.stderr).strip())\n            raise SystemExit(auth.returncode)",SOURCE)
    def test_syntax(self):
        ast.parse(SOURCE)
