import tempfile
import unittest
from pathlib import Path
from oursteps.analytics import counts,payload

class ArticleAnalyticsTests(unittest.TestCase):
    def test_counts_only_successful_article_get_log_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);log=root/'data/public-analytics/article-views.log';log.parent.mkdir(parents=True)
            log.write_text('1.0\t/123.html\t200\n2.0\t/123.html\t200\n3.0\t/456.html\t404\n4.0\t/abc.html\t200\nmalformed\n')
            self.assertEqual(counts(root),{'123':2})
            self.assertEqual(counts(root,{'123','456'}),{'123':2})
            self.assertEqual(payload(root,{'123'}),b'{"123":2}\n')
    def test_missing_log_is_zero_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(counts(Path(tmp)),{})
