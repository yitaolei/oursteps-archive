import _test_config  # Configure synthetic identity before application imports.
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.deps')]
from oursteps.parser import ParseError, parse_thread, thread_url


class PageIdentityTests(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / 'tests/fixtures/thread-second.html').read_text()

    def with_markers(self, markers):
        return self.html.replace('</body>', markers + '</body>')

    def test_mismatch(self):
        markers = '<div class="pg"><strong>11</strong><input name="custompage" value="11"> / 11 页</div>'
        with self.assertRaisesRegex(ParseError, '^page_identity_mismatch$'):
            parse_thread(self.with_markers(markers * 2), thread_url(1902000, 12))

    def test_matching(self):
        markers = '<div class="pg"><strong>2</strong><input name="custompage" value="2"></div>'
        url = thread_url(1902000, 2)
        self.assertEqual(parse_thread(self.with_markers(markers * 2), url),
                         parse_thread(self.html, url))

    def test_ambiguous_or_missing_preserves_behavior(self):
        url = thread_url(1902000, 12)
        expected = parse_thread(self.html, url)
        self.assertEqual(expected['page'], 12)
        self.assertEqual(expected['declared_pages'], 2)
        for markers in ('',
                        '<div class="pg"><strong>11</strong><input name="custompage" value="12"></div>',
                        '<div class="pg"><strong>11</strong></div><div class="pg"><strong>12</strong></div>',
                        '<div class="pg"><strong>11</strong><input name="custompage" value="?"></div>'):
            with self.subTest(markers=markers):
                self.assertEqual(parse_thread(self.with_markers(markers), url), expected)
