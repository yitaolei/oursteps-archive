import _test_config
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from oursteps.guide_discovery import guide_url,parse_guide,discover,progress
from oursteps.fetch import validate_url
from oursteps.parser import Blocked
from oursteps.store import Store


def fixture(tids,page=1):
    rows=''.join(
        (
            '<tbody id="normalthread_%s"><tr>'
            '<td class="icn"><a href="forum.php?mod=viewthread&amp;tid=%s&amp;extra="></a></td>'
            '<th class="common">'
            '<a href="forum.php?mod=viewthread&amp;tid=%s&amp;extra=">Thread %s</a>'
            '<span class="tps"><a href="forum.php?mod=viewthread&amp;tid=%s&amp;extra=&amp;page=2">2</a></span>'
            '</th>'
            '<td class="by"><a href="forum.php?mod=forumdisplay&amp;fid=43">Forum</a></td>'
            '<td class="num"><a href="forum.php?mod=viewthread&amp;tid=%s&amp;extra=">41</a></td>'
            '</tr></tbody>'
        ) % (t,t,t,t,t,t)
        for t in tids
    )

    body = rows or (
        '<tbody class="bw0_all"><tr><th colspan="5">'
        '<p class="emp">暂时还没有帖子</p>'
        '</th></tr></tbody>'
    )

    return (
        '<html>'
        '<div id="um"><a href="home.php?uid=424242">archive_test_user</a></div>'
        '<a href="forum.php?mod=guide&amp;view=my&amp;type=thread">My threads</a>'
        '<div id="threadlist">'
        '<div class="th"><table><tr><th>'
        '<a href="forum.php?mod=guide&amp;view=my&amp;type=thread">Filter</a>'
        '</th></tr></table></div>'
        '<div class="bm_c"><table>' + body + '</table></div>'
        '</div>'
        '<div class="pg"><strong>' + str(page) + '</strong></div>'
        '<div>32949</div>'
        '</html>'
    ).encode()


class GuideTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name);self.addCleanup(self.store.db.close)
        self.calls=[];self.pages={};self.clock=0
        outer=self
        class Fetch:
            mode='authenticated_private'
            def __init__(self,*args):pass
            def get(self,url):
                outer.calls.append(url)
                body=outer.pages[url]
                if callable(body):body=body()
                return outer.store.snapshot(url,body,mode=self.mode)
        self.patch=patch('oursteps.fetch.Fetcher',Fetch);self.patch.start();self.addCleanup(self.patch.stop)
    def fill(self,groups):
        self.pages={guide_url(i):fixture(tids,i) for i,tids in enumerate(groups,1)}
    def test_routes(self):
        validate_url(guide_url(688))
        for url in (guide_url(0),guide_url(-1),guide_url(1)+'&x=',guide_url(1)+'&page=2',guide_url(1).replace('view=my','view=new'),guide_url(1).replace('type=thread','type=reply'),guide_url(1).replace('www.oursteps.com.au','example.org')):
            with self.subTest(url=url),self.assertRaises(Blocked):validate_url(url)
    def test_parser_full_partial_empty_and_errors(self):
        for n in (50,23,0):
            p=parse_guide(fixture(range(1,n+1)),guide_url(1))
            self.assertEqual(len(p['threads']),n);self.assertEqual(p['eof'],n==0)
        self.assertEqual(len(parse_guide(fixture([1,1]),guide_url(1))['threads']),1)
        for body in (b'<html>login</html>',fixture([]).replace('暂时还没有帖子'.encode(),b''),fixture([]).replace(b'</html>',b''),fixture([]).replace(b'</html>',b'<div id="messagetext">error</div></html>')):
            with self.assertRaises(Exception):parse_guide(body,guide_url(1))
        for text in ('请先登录','CAPTCHA','系统暂时繁忙'):
            with self.assertRaises(Exception):parse_guide(('<html><div id="messagetext">'+text+'</div></html>').encode(),guide_url(1))
    def test_bounds_overlap_idempotency_eof(self):
        self.fill([[101],[102],[103],[104],[]])
        self.assertEqual(discover(self.store,max_pages=3),'budget_reached')
        self.assertEqual(progress(self.store)['next_page'],4)
        self.calls.clear()
        self.assertEqual(discover(self.store,max_pages=4),'pass_complete')
        self.assertEqual(self.calls,[guide_url(i) for i in (2,3,4,5)])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM inventory').fetchone()[0],4)
        state=progress(self.store);self.assertEqual(state['eof_page'],5);self.assertEqual(state['last_nonempty'],4)
        self.assertEqual(state['new_tids'],1)
        self.calls.clear();self.assertEqual(discover(self.store,max_pages=5),'pass_complete');self.assertEqual(self.calls,[])
    def test_anchor_overlap_allows_normal_shift(self):
        self.fill([[101],[102],[103],[104],[105],[]])
        self.assertEqual(discover(self.store,max_pages=3),'budget_reached')
        # Simulate new threads pushing the old directory entries one page deeper.
        # Some prior anchors remain visible, so continuity is still proven.
        self.pages[guide_url(2)]=fixture([101],2)
        self.pages[guide_url(3)]=fixture([102],3)
        self.pages[guide_url(4)]=fixture([103],4)
        self.pages[guide_url(5)]=fixture([104],5)
        self.pages[guide_url(6)]=fixture([],6)
        self.calls.clear()
        self.assertEqual(discover(self.store,max_pages=5),'pass_complete')
        self.assertEqual(self.calls,[guide_url(i) for i in (2,3,4,5,6)])

    def test_anchor_missing_preserves_frontier(self):
        self.fill([[101],[102],[103],[104]])
        discover(self.store,max_pages=3)
        self.pages[guide_url(2)]=fixture([999],2)
        self.pages[guide_url(3)]=fixture([998],3)
        self.calls.clear()
        self.assertEqual(discover(self.store,max_pages=5),'anchor_mismatch')
        self.assertEqual(progress(self.store)['next_page'],4)
        self.assertEqual(self.calls,[guide_url(2),guide_url(3)])
        self.assertIsNotNone(self.store.db.execute('SELECT 1 FROM inventory WHERE tid=999').fetchone())
    def test_monotonic_commit_and_resume(self):
        self.fill([[101],[102]])
        def fetched():self.clock=60;return fixture([101])
        self.pages[guide_url(1)]=fetched
        with patch('oursteps.guide_discovery.time.monotonic',side_effect=lambda:self.clock):
            self.assertEqual(discover(self.store,max_minutes=1),'budget_reached')
        self.assertEqual(self.calls,[guide_url(1)])
        self.assertEqual(progress(self.store)['next_page'],2)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM inventory').fetchone()[0],1)
    def test_existing_complete_metadata_unchanged(self):
        with self.store.db:self.store.db.execute("INSERT INTO threads(tid,title,fid,discovered_via,status) VALUES(101,'saved',7,'test','complete')")
        self.fill([[101]])
        discover(self.store,max_pages=1)
        row=self.store.db.execute('SELECT title,fid,status,discovered_via FROM threads WHERE tid=101').fetchone()
        self.assertEqual(tuple(row),('saved',7,'complete','test'))

    def test_cli_limits_validation(self):
        import sys
        for filename in ('history_launcher.py','history_worker.py'):
            source=(Path(__file__).resolve().parents[1]/'scripts'/filename).read_text().split('ROOT=')[0]
            with patch.object(sys,'argv',[filename,'guide-discover','--now','--max-pages','5','--max-minutes','10']):
                scope={};exec(source,scope)
                self.assertEqual((scope['a'].max_pages,scope['a'].max_minutes),(5,10))
            for args in (['backfill','--max-pages','5'],['guide-discover','--max-threads','5'],['guide-discover','--max-pages','0'],['discover','--max-minutes','1']):
                with patch.object(sys,'argv',[filename]+args),patch('sys.stderr'),self.assertRaises(SystemExit) as caught:
                    exec(source,{})
                self.assertEqual(caught.exception.code,2)

    def test_busy_backoff_preserves_progress(self):
        from oursteps.parser import Busy
        self.fill([[101]])
        def busy():raise Busy('site_busy')
        self.pages[guide_url(1)]=busy
        self.assertEqual(discover(self.store,max_pages=5),'retry_later')
        self.assertEqual(progress(self.store)['next_page'],1)
        self.calls.clear()
        self.assertEqual(discover(self.store,max_pages=5),'retry_later')
        self.assertEqual(self.calls,[])
