import _test_config  # Configure synthetic identity before application imports.
import unittest
from unittest.mock import patch
from oursteps.auth import identity, credentials, security_check
from oursteps.parser import soup_of

class AuthTests(unittest.TestCase):
    def test_identity_both_headers(self):
        for header in ('um','toptb'):
            self.assertTrue(identity(soup_of(('<div id="%s"><a href="home.php?mod=space&uid=424242">archive_test_user</a></div>'%header).encode())))
        self.assertFalse(identity(soup_of(b'<div id="um"><a href="?uid=999">archive_test_user</a></div>')))
    def test_keychain_reuse_no_prompt(self):
        with patch('keyring.backends.macOS.Keyring') as backend, patch('builtins.input',side_effect=AssertionError):
            backend.return_value.get_password.return_value='test-only'
            self.assertEqual(credentials(False),('archive_test_user','test-only'))
    def test_missing_noninteractive_stops(self):
        with patch('keyring.backends.macOS.Keyring') as backend:
            backend.return_value.get_password.return_value=None
            with self.assertRaisesRegex(RuntimeError,'credentials_required'): credentials(False)
    def test_http_security_no_page_read(self):
        from unittest.mock import Mock
        for status in (401,403,429):
            with self.assertRaisesRegex(RuntimeError,str(status)): security_check(Mock(),Mock(status=status))

class HandoffTests(unittest.TestCase):
    def test_native_session_permissions(self):
        import json
        import tempfile
        from pathlib import Path
        from scripts import session_handoff
        with tempfile.TemporaryDirectory() as tmp, patch.object(session_handoff,'ROOT',Path(tmp)), patch.object(session_handoff.sys,'platform','linux'):
            session_handoff.install(json.dumps(dict(uid=424242,cookies=[])).encode())
            self.assertEqual((Path(tmp)/'.secrets').stat().st_mode & 0o777,0o700)
            self.assertEqual((Path(tmp)/'.secrets/session.json').stat().st_mode & 0o777,0o600)
    def test_exact_directory_allowlist(self):
        from oursteps.fetch import validate_url
        validate_url('https://www.oursteps.com.au/bbs/home.php?mod=space&uid=424242&do=thread&view=me&from=space')
        with self.assertRaises(Exception):
            validate_url('https://www.oursteps.com.au/bbs/home.php?mod=space&uid=999&do=thread&view=me&from=space')
