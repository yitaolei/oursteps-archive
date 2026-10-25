import _test_config
import unittest
from oursteps.fetch import safe_denial_record

class DenialRecordTests(unittest.TestCase):
    def test_record_restricts_sensitive_fields(self):
        url='https://www.oursteps.com.au/bbs/forum.php?mod=viewthread&tid=1801791&page=2&_dsign=private'
        record=safe_denial_record(403,url,{'Server':'test','Retry-After':'120','Set-Cookie':'secret','Location':'https://example.com/token'},b'sensitive HTML')
        self.assertEqual((record['status'],record['tid'],record['page']),(403,'1801791','2'))
        self.assertEqual(record['headers'],{'server':'test','retry_after':'120'})
        self.assertNotIn('private',str(record))
        self.assertNotIn('secret',str(record))
        self.assertNotIn('sensitive',str(record))

    def test_browser_fallback_diagnostic_is_allowlisted(self):
        from pathlib import Path
        source=(Path(__file__).resolve().parents[1]/'scripts/browser_fallback.py').read_text()
        self.assertIn('safe_denial_record(',source)
        self.assertIn('response.status in (401,403,407,429)',source)

    def test_cloudfront_refusal_classification_precedes_generic_block(self):
        from pathlib import Path
        source=(Path(__file__).resolve().parents[1]/'oursteps/fetch.py').read_text()
        start=source.index('            if status == 403:',source.index('class Fetcher:'))
        stop=source.index('            if status in (403,401,407)',start)
        self.assertIn('inspect_response(body,status)',source[start:stop])
