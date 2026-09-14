import _test_config  # Configure synthetic identity before application imports.
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.deps'))
import datetime as dt
import unittest
from oursteps.dates import sydney_today,publication_time,infer_display_offset
from oursteps.parser import ParseError
class DateTests(unittest.TestCase):
    def test_midnight_winter(self):
        for utc,day in [('2026-09-08T13:59:59+00:00','2026-09-08'),('2026-09-08T14:00:00+00:00','2026-09-09')]:
            self.assertEqual(sydney_today(dt.datetime.fromisoformat(utc)),day)
    def test_midnight_summer(self):
        self.assertEqual(sydney_today(dt.datetime.fromisoformat('2026-12-01T13:00:00+00:00')),'2026-12-02')
        self.assertEqual(publication_time('发表于 2026-12-1 23:30',b'',10),'2026-12-02 00:30')
    def test_dst_transition(self):
        for value,expected in [('2026-10-3 15:59','2026-10-04 01:59'),('2026-10-3 16:00','2026-10-04 03:00'),('2026-4-4 15:59','2026-04-05 02:59'),('2026-4-4 16:00','2026-04-05 02:00')]:
            self.assertEqual(publication_time(value,b'',0),expected)
    def test_profile_clock_inference(self):
        now=dt.datetime.fromisoformat('2026-09-08T19:30:00+00:00').timestamp()
        self.assertEqual(infer_display_offset('最后访问 2026-9-9 05:29'.encode(),now,now+2),10)
        with self.assertRaises(ParseError):infer_display_offset('最后访问 2026-9-7 05:29'.encode(),now,now+2)
    def test_missing_evidence_stops(self):
        with self.assertRaises(ParseError):publication_time('2026-9-9 00:00',b'')
