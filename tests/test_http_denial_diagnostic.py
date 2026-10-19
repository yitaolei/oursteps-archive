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
