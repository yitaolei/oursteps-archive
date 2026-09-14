import _test_config  # Configure synthetic identity before application imports.
import datetime
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.deps'))
from oursteps.store import Store
from oursteps.parser import thread_url,directory_url
from oursteps.sync import sync,dated_listing,sydney_today
from oursteps.fetch import validate_url
FIX=Path(__file__).parent/'fixtures'

IDENT='<div id="um"><a href="home.php?mod=space&amp;uid=424242">archive_test_user</a></div>'
def listing(tids):
    return ('<meta charset="utf-8">'+IDENT+'<table>'+''.join('<tr><th><a href="forum.php?mod=viewthread&amp;tid=%d">文章</a></th><td class="num"><a>3</a><em>1250</em></td><td class="by"><em>2099-1-1</em></td></tr>'%t for t in tids)+'</table>').encode()
def article(tid,day,pid):
    text=(FIX/'thread-first.html').read_text()
    import re
    text=text.replace('1902000',str(tid))
    text=re.sub(r'20\d\d-\d+-\d+',day,text)
    for old,new in [('101',str(pid)),('102',str(pid+1)),('103',str(pid+2))]:text=re.sub(r'(?<!\d)'+old+r'(?!\d)',new,text)
    text=re.sub(r'<div class="pg">.*?</div>','',text,flags=re.S)
    return (text+'<div id="ft">GMT +10, 2026-9-8</div>').encode()

class DailyTests(unittest.TestCase):
    def setUp(self):
        import json,time
        from unittest.mock import Mock
        self.state=patch('oursteps.auth.STATE',Mock(exists=lambda:True,read_text=lambda:json.dumps(dict(verified_at=time.time(),display_offset_hours=10,display_offset_verified_at=time.time()))))
        self.state.start();self.addCleanup(self.state.stop)

    def test_last_reply_never_publication(self):
        p=dated_listing(listing([10]),directory_url(),'2026-09-08')
        self.assertIsNone(p['threads'][0]['day'])
    def test_route(self):
        validate_url(directory_url(2))
        from oursteps.parser import Blocked
        with self.assertRaises(Blocked):validate_url(directory_url().replace('424242','2'))
    def test_incremental_multiple_same_day(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(temp)
            pages={directory_url():listing([1902001,1902000]),thread_url(1902001):article(1902001,'2026-9-8',201),thread_url(1902000):article(1902000,'2026-9-7',301)}
            class Fake:
                mode='authenticated_private'
                def __init__(self,*a,**k):pass
                def get(self,url):
                    calls.append(url)
                    return store.snapshot(url,pages[url],mode='authenticated_private')
                def wait_window(self):pass
            calls=[]
            with patch('oursteps.sync.Fetcher',Fake),patch('oursteps.sync.sydney_today',return_value='2026-09-08'):
                first=sync(store,now=True)
                self.assertEqual(first['newly_archived'],1,first)
                self.assertEqual(first['discovered_today'],1,first)
                calls.clear();second=sync(store,now=True)
                self.assertEqual(second['newly_archived'],0,second)
                self.assertEqual(calls,[directory_url()])
                pages[directory_url()]=listing([1902002,1902001,1902000]);pages[thread_url(1902002)]=article(1902002,'2026-9-8',401)
                calls.clear();third=sync(store,now=True)
                self.assertEqual(third['newly_archived'],1,third)
                self.assertEqual(third['already_archived'],1,third)
                self.assertEqual(calls,[directory_url(),thread_url(1902002)])
                self.assertEqual(store.db.execute('SELECT count(*) FROM threads').fetchone()[0],2)
            store.db.close()
    def test_timezone_crosses_sydney_midnight(self):
        from oursteps.sync import publication_time
        self.assertEqual(publication_time('发表于 2026-9-7 16:30',b'<div id="ft">GMT +0</div>'),'2026-09-08 02:30')
        self.assertEqual(publication_time('发表于 2026-12-1 14:30',b'<div id="ft">GMT +0</div>'),'2026-12-02 01:30')
    def test_today_not_paginated_above_twenty(self):
        from oursteps.preview import build
        from oursteps.parser import parse_thread
        from oursteps.parser import soup_of
        with tempfile.TemporaryDirectory() as temp:
            store=Store(temp)
            for i in range(31):
                tid=2000000+i
                with store.db:
                    store.db.execute('INSERT INTO threads(tid,discovered_via) VALUES(?,?)',(tid,'test'))
                    store.db.execute('INSERT INTO daily_members VALUES(?,?)',('2026-09-08',tid))
                    store.db.execute('INSERT INTO jobs(url,kind,tid,page) VALUES(?,?,?,1)',(thread_url(tid),'thread',tid))
                raw=article(tid,'2026-9-8',1000+i*10)
                snap=store.snapshot(thread_url(tid),raw)
                store.save_page(thread_url(tid),parse_thread(raw,thread_url(tid)),snap)
            with patch('oursteps.sync.sydney_today',return_value='2026-09-08'):
                build(store)
            soup=soup_of((store.root/'preview/index.html').read_bytes())
            self.assertEqual(len(soup.select('.today .card')),31)
            store.db.close()
