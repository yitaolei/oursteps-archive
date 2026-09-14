"""Synthetic regression fixtures, NEVER represented as downloaded OurSteps articles."""
import _test_config  # Configure synthetic identity before application imports.
import datetime as dt
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.deps'))
from oursteps.parser import (Blocked,Busy,ParseError,parse_thread,parse_discovery,
                             thread_url,directory_url)
from oursteps.store import Store
from oursteps.cli import run
from oursteps.fetch import in_window,validate_url,Fetcher,OutsideWindow,longest_rule_allowed

FIX = Path(__file__).parent/'fixtures'
FIRST = (FIX/'thread-first.html').read_bytes()
SECOND = (FIX/'thread-second.html').read_bytes()

class ParserTests(unittest.TestCase):
    def test_identity_body_assets(self):
        p = parse_thread(FIRST,thread_url(1902000))
        self.assertEqual(p['fid'],160)
        self.assertEqual((p['views'],p['replies']),(1250,3))
        self.assertEqual([x['pid'] for x in p['posts']],[101,103])
        self.assertEqual(p['next_pages'],[2])
        body = p['posts'][0]
        self.assertNotIn('签名',body['text'])
        self.assertNotIn('evil()',body['html'])
        self.assertNotIn('onerror',body['html'])
        self.assertEqual(body['assets'][0]['attributes']['aid'],'42')
        self.assertEqual(body['assets'][1]['kind'],'attachment')
        self.assertIn('2025-9-8',body['edited_at_raw'])
        self.assertEqual(body['links'][0]['url'],'https://example.org/source?x=1&y=2')

    def test_discovery_scoped_dedup(self):
        p = parse_discovery((FIX/'directory.html').read_bytes(),directory_url())
        self.assertEqual([x['tid'] for x in p['threads']],[1902000,1882306])
        self.assertEqual(p['next_url'],directory_url(2))
        with self.assertRaises(ParseError):
            parse_discovery((FIX/'directory.html').read_bytes(),directory_url()+'&type=reply')

    def test_error_pages(self):
        for text in ('请先登录后才能继续浏览','CAPTCHA','抱歉，分页数不在允许的范围内'):
            with self.assertRaises(Blocked):
                parse_thread(('<div id="messagetext">'+text+'</div>').encode(),thread_url(1902000))
        with self.assertRaises(Busy):
            parse_thread('系统暂时繁忙 喝茶时间'.encode(),thread_url(1902000))
        with self.assertRaises(ParseError):
            parse_thread(b'<html>unexpected</html>',thread_url(1902000))

    def test_missing_body_is_gap(self):
        p = parse_thread(FIRST.replace(b'postmessage_103',b'missing_103'),thread_url(1902000))
        self.assertEqual(p['gaps'][0]['pid'],103)

    def test_wrong_owner_rejected(self):
        with self.assertRaises(ParseError):
            parse_thread(FIRST.replace(b'uid=424242',b'uid=55',1),thread_url(1902000))

    def test_windows_and_route_allowlist(self):
        for hour,expected in ((13,False),(14,True),(21,True),(22,False)):
            self.assertEqual(in_window(dt.datetime(2026,9,8,hour,tzinfo=dt.timezone.utc)),expected)
        for url in ('https://example.org/',thread_url(1)+'&action=edit',
                    'https://www.oursteps.com.au/bbs/forum.php?mod=topicadmin'):
            with self.assertRaises(Blocked):
                validate_url(url)

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.s=Store(self.tmp.name)
    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def test_resume_and_reparse_without_network(self):
        self.s.seed([1902000],'synthetic_fixture')
        first=self.s.snapshot(thread_url(1902000),FIRST,mode='fixture')
        self.assertNotIn(b'formhash=abcd',self.s.raw(first))
        with patch('oursteps.fetch.Fetcher.get',side_effect=AssertionError('network forbidden')):
            self.assertEqual(run(self.s,offline=True),'offline_missing_raw')
            self.assertEqual(self.s.report()['author_posts'],2)
            self.s.snapshot(thread_url(1902000,2),SECOND,mode='fixture')
            self.assertEqual(run(self.s,offline=True),'complete')
            self.assertEqual(run(self.s,offline=True),'complete')
        self.assertEqual(self.s.report()['author_posts'],3)
        self.assertEqual(self.s.report()['complete_threads'],1)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM posts WHERE publish_state!="excluded"').fetchone()[0],0)
        self.s.db.execute('UPDATE jobs SET state="pending"')
        self.s.db.commit()
        with patch('oursteps.fetch.Fetcher.get',side_effect=AssertionError('network forbidden')):
            self.assertEqual(run(self.s,offline=True),'complete')
        self.assertEqual(self.s.report()['author_posts'],3)

    def test_pilot_hard_cap(self):
        with self.assertRaises(ValueError):
            self.s.seed(range(1,22),'fixture')
        self.assertEqual(self.s.report()['threads'],0)

    def test_raw_integrity(self):
        snap=self.s.snapshot(thread_url(1902000),FIRST,mode='fixture')
        (self.s.root/snap['path']).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            self.s.raw(snap)

    def test_outside_window_no_requests(self):
        with patch('oursteps.fetch.in_window',return_value=False),patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('network forbidden')):
            with self.assertRaises(OutsideWindow):
                Fetcher(self.s).get(thread_url(1902000))

    def test_specific_robots_rule_after_allow_all(self):
        lines=['User-agent: *','Allow: /','Disallow: /bbs/private','Allow: /bbs/private/allowed']
        self.assertFalse(longest_rule_allowed(lines,'https://www.oursteps.com.au/bbs/private/a'))
        self.assertTrue(longest_rule_allowed(lines,'https://www.oursteps.com.au/bbs/private/allowed'))

    def test_halt_persists_and_no_further_fetch(self):
        self.s.seed([1902000],'fixture')
        with patch('oursteps.fetch.Fetcher.wait_window'),patch('oursteps.fetch.Fetcher.get',side_effect=Blocked('http_429')) as fetch:
            self.assertEqual(run(self.s),'halted')
            self.assertEqual(run(self.s),'halted')
            self.assertEqual(fetch.call_count,1)

    def test_repeated_page_not_complete(self):
        self.s.seed([1902000],'fixture')
        snap=self.s.snapshot(thread_url(1902000),FIRST,mode='fixture')
        parsed=parse_thread(FIRST,thread_url(1902000))
        self.s.save_page(thread_url(1902000),parsed,snap)
        parsed['page']=2
        with self.assertRaises(ValueError):
            self.s.save_page(thread_url(1902000,2),parsed,snap)
        self.assertEqual(self.s.report()['complete_threads'],0)

    def test_explicit_run_now_is_limited_to_twenty(self):
        with patch.dict('os.environ', {'OURSTEPS_PILOT_NOW':'1'}), patch('oursteps.fetch.in_window',return_value=False):
            with self.assertRaises(Blocked):
                Fetcher(self.s).wait_window()
            self.s.seed(range(1,21),'fixture')
            Fetcher(self.s).wait_window()
            self.assertEqual(Fetcher(self.s).delay,10.0)

    def test_twenty_thread_offline_pilot(self):
        tids=range(2000001,2000021)
        self.s.seed(tids,'SYNTHETIC TEST ONLY')
        for tid in tids:
            for page,template in ((1,FIRST),(2,SECOND)):
                body=template.replace(b'1902000',str(tid).encode())
                # Replace DOM post IDs only, never UID 424242 or dates.
                body=re.sub(rb'(post_|postnum|authorposton|postmessage_)(10[1-4])',
                            lambda m:m[1]+str(tid*1000+int(m[2])).encode(),body)
                self.s.snapshot(thread_url(tid,page),body,mode='fixture')
        with patch('oursteps.fetch.Fetcher.get',side_effect=AssertionError('network forbidden')):
            self.assertEqual(run(self.s,offline=True),'complete')
            self.assertEqual(run(self.s,offline=True),'complete')
        r=self.s.report()
        self.assertEqual((r['threads'],r['complete_threads'],r['author_posts'],r['parsed_pages']),(20,20,60,40))
        self.assertFalse(r['failures'])

if __name__=='__main__':
    unittest.main()
