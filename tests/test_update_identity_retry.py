"""Pure launcher retry selection, extracted without executing the scheduler."""
import ast
import json
from pathlib import Path
import unittest

SOURCE=Path(__file__).resolve().parents[1]/'scripts/update_now.py'
url='https://www.oursteps.com.au/bbs/forum.php?mod=viewthread&tid=1904434&page=1'

class IdentityRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree=ast.parse(SOURCE.read_text())
        fn=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='identity_retry_urls')
        scope={'json':json}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),str(SOURCE),'exec'),scope)
        cls.choose=staticmethod(scope['identity_retry_urls'])

    def report(self,**extra):
        item=dict(stop_reason=None,handoff_error=None,handoff_status='success',failures=[
            dict(url=url,error='RuntimeError:browser_fallback_unresolved:identity_not_confirmed')])
        item.update(extra)
        return json.dumps(item)

    def test_only_failed_identity_is_rechecked_once(self):
        self.assertEqual(self.choose(self.report(),[url]),[url])
        self.assertEqual(self.choose(self.report(),['https://example.org/']),[])
        self.assertEqual(self.choose('not json',[url]),[])

    def test_stop_and_handoff_are_never_rechecked(self):
        self.assertEqual(self.choose(self.report(stop_reason='manual_required:HTTP 403'),[url]),[])
        self.assertEqual(self.choose(self.report(handoff_error='timeout'),[url]),[])
        self.assertEqual(self.choose(self.report(handoff_status='failed'),[url]),[])

    def test_no_retry_for_security_or_other_failures(self):
        for detail in ('manual_required:captcha','RuntimeError:browser_fallback_unresolved:site_busy',
                       'RuntimeError:browser_navigation_failed', 'RuntimeError:http_429'):
            with self.subTest(detail=detail):
                self.assertEqual(self.choose(self.report(failures=[dict(url=url,error=detail)]),[url]),[])

    def test_max_one_and_no_unknown_urls(self):
        second=url.replace('1904434','1904435')
        self.assertEqual(self.choose(self.report(failures=[dict(url=url,error='RuntimeError:browser_fallback_unresolved:identity_not_confirmed'),dict(url=second,error='RuntimeError:browser_fallback_unresolved:identity_not_confirmed')]),[url,second]),[url])
