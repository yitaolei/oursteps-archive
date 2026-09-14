import _test_config  # Configure synthetic identity before application imports.
import sys,json,time,datetime as dt
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.deps'))
import unittest
from unittest.mock import patch,Mock
from oursteps.auth_verify import identity,inspect_response,VerificationIssue
from oursteps.auth import checked_get
from oursteps.parser import soup_of,directory_url
from oursteps.dates import cached_display_offset
HEADER='<div id="um"><a href="?uid=424242">archive_test_user</a></div>'
class VerifyTests(unittest.TestCase):
    def classify(self,body,status=200,url=None):
        with self.assertRaises(VerificationIssue) as caught:inspect_response(body.encode(),status,url)
        return caught.exception
    def test_positive_expiry_only(self):
        self.assertEqual(self.classify('<html><body>请先登录</body></html>').category,'auth_required')
        self.assertEqual(self.classify('<html><body>尚未登录，没有权限访问</body></html>').category,'auth_required')
        self.assertEqual(self.classify('<html><body>new valid layout</body></html>').category,'unexpected_layout')
    def test_permission_challenge_busy(self):
        for body,status,kind in [('x',403,'permission_denied'),('CAPTCHA',200,'manual_required'),('系统暂时繁忙',200,'retry_later'),('x',503,'retry_later'),('x',429,'retry_later')]:
            self.assertEqual(self.classify(body,status).category,kind)
    def test_truncated_vs_layout(self):
        self.assertTrue(self.classify(HEADER,directory_url() and 200,directory_url()).retry)
        self.assertEqual(self.classify('<html>'+HEADER+'</html>',200,directory_url()).category,'unexpected_layout')
    def test_fallback_requires_account_navigation(self):
        base='<a href="?uid=424242">archive_test_user</a>'
        self.assertFalse(identity(soup_of(base.encode())))
        self.assertTrue(identity(soup_of((base+'<a href="?mod=logging&action=logout">退出</a>').encode())))
    def test_stale_profile_does_not_invalidate_cached_offset(self):
        now=dt.datetime.fromisoformat('2026-09-09T00:00:00+00:00').timestamp()
        saved=dict(display_offset_hours=10,display_offset_verified_at=now-7200)
        self.assertEqual(cached_display_offset(saved,now),10)
        with self.assertRaises(VerificationIssue) as c:cached_display_offset(saved,dt.datetime.fromisoformat('2026-12-09T00:00:00+00:00').timestamp())
        self.assertEqual(c.exception.category,'timezone_required')
    def test_retry_preserves_session_and_logs_raw(self):
        import tempfile
        from oursteps import auth
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)/'session.json';state.write_text('known good')
            page=Mock();page.goto.return_value.status=200;page.goto.return_value.headers={}
            page.goto.return_value.body.side_effect=[b'<html>partial',('<html>'+HEADER+'</html>').encode()]
            with patch.object(auth,'STATE',state),patch.object(auth,'diagnostic') as diag,patch.object(auth.time,'sleep') as sleep:
                self.assertTrue(checked_get(page,'https://example.invalid'))
                self.assertEqual(diag.call_count,1);self.assertEqual(sleep.call_count,1)
                self.assertGreaterEqual(sleep.call_args.args[0],30)
                self.assertEqual(state.read_text(),'known good')
    def test_layout_bug_does_not_retry_or_prompt(self):
        page=Mock();page.goto.return_value.status=200;page.goto.return_value.body.return_value=b'<html>layout</html>'
        with patch('oursteps.auth.diagnostic'),patch('oursteps.auth.credentials',side_effect=AssertionError),patch('oursteps.auth.time.sleep') as sleep:
            with self.assertRaises(VerificationIssue):checked_get(page,'https://example.invalid')
            sleep.assert_not_called()
    def test_bootstrap_keeps_known_good_state_on_layout_failure(self):
        import tempfile
        from unittest.mock import MagicMock
        from oursteps import auth
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir();state=root/'session.json';state.write_text(json.dumps(dict(cookies=[],uid=424242)))
            original=state.read_bytes()
            with patch.object(auth,'ROOT',root),patch.object(auth,'STATE',state),patch('playwright.sync_api.sync_playwright',return_value=MagicMock()),patch.object(auth,'checked_get',side_effect=VerificationIssue('unexpected_layout','new_layout')),patch.object(auth,'credentials',side_effect=AssertionError) as credentials,patch.object(auth.subprocess,'run') as transport:
                with self.assertRaisesRegex(SystemExit,'unexpected_layout'):auth.bootstrap(False)
                self.assertEqual(state.read_bytes(),original)
                credentials.assert_not_called();transport.assert_not_called()
    def test_directory_with_records_but_truncated_is_not_complete(self):
        content=HEADER+'<table><tr><th><a href="forum.php?mod=viewthread&tid=123">test</a></th></tr></table>'
        self.assertEqual(self.classify(content,200,directory_url()).reason,'incomplete_html')
