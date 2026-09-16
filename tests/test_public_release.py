import _test_config  # Configure synthetic identity before application imports.
import fcntl
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request,urlopen
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'scripts'),str(Path(__file__).resolve().parents[1]/'.deps')]
import public_release as pub
import preview
from oursteps.store import Store
from oursteps.parser import parse_thread,thread_url
from oursteps.preview import build

class PublicTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        from scripts.render_public_nginx import render
        render(output=self.root/'nginx-public-stable.conf')
        for name in ('compose.public.yaml',):
            shutil.copy2(pub.ROOT/name,self.root/name)
        store=Store(self.root/'data');store.seed([1902000],'test')
        for page,name in ((1,'thread-first.html'),(2,'thread-second.html')):
            url=thread_url(1902000,page);raw=(pub.ROOT/'tests/fixtures'/name).read_bytes()
            snap=store.snapshot(url,raw);store.save_page(url,parse_thread(raw,url),snap)
        build(store);store.db.close()
    def tearDown(self): self.tmp.cleanup()
    def publish(self,**kw): return pub.publish(self.root,expected={'1902000'},dates={'1902000':'2026-09-12 00:00'},today='2026-09-12',**kw)
    def test_publish_idempotency_rollback_parent_stable(self):
        first=self.publish();site=self.root/'public-site';inode=site.stat().st_ino
        self.assertEqual(self.publish()['status'],'unchanged')
        page=self.root/'data/preview/1902000.html';page.write_text(page.read_text()+'\n')
        second=self.publish();self.assertNotEqual(first['release'],second['release'])
        self.assertEqual(pub.pointer(site,'previous'),first['release'])
        self.assertEqual(site.stat().st_ino,inode)
        self.assertEqual((site/first['release']/'style.css').stat().st_ino,(site/second['release']/'style.css').stat().st_ino)
        self.publish(rollback=True);self.assertEqual(pub.pointer(site,'current'),first['release'])
        pub.validate_saved(self.root,first['release'])
    def test_validation_fail_keeps_live(self):
        first=self.publish();p=self.root/'data/preview/1902000.html';p.write_text(p.read_text()+'session.json')
        with self.assertRaises(ValueError):self.publish()
        self.assertEqual(pub.pointer(self.root/'public-site','current'),first['release'])
    def test_count_inline_body_and_missing_data_tid(self):
        p=self.root/'data/preview/index.html';original=p.read_text()
        for old,new in [('data-tid="1902000"','data-other="1902000"'),('data-search="','data-search="full body payload ' )]:
            p.write_text(original.replace(old,new))
            with self.assertRaises(ValueError):self.publish()
        p.write_text(original)
        with self.assertRaises(ValueError):pub.publish(self.root,expected={'1902000','2'})
    def test_worker_lock_refused_and_symlink_refused(self):
        with (self.root/'data/worker.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):self.publish()
        (self.root/'data/preview/evil').symlink_to('/tmp')
        with self.assertRaises(ValueError):self.publish()
    def test_failed_current_switch_keeps_old(self):
        first=self.publish();p=self.root/'data/preview/1902000.html';p.write_text(p.read_text()+'\n')
        switch=pub.switch
        def fail(site,name,target):
            if name=='current':raise OSError('simulated switch failure')
            switch(site,name,target)
        with patch.object(pub,'switch',side_effect=fail),self.assertRaises(OSError):self.publish()
        self.assertEqual(pub.pointer(self.root/'public-site','current'),first['release'])
    def test_permissions_and_forbidden_extra(self):
        result=self.publish();dest=self.root/'public-site'/result['release']
        (dest/'archive.sqlite3').write_text('no')
        with self.assertRaises(ValueError):pub.validate(dest,{'1902000'})
    def test_native_readonly_expected_and_unsafe_mount(self):
        with patch.object(pub.sys,'platform','linux'):
            self.assertEqual(pub.expected_tids(self.root),{'1902000'})
        p=self.root/'compose.public.yaml'
        p.write_text(p.read_text().replace('    read_only:', '      - ./data:/private:rw\n\n    read_only:'))
        with self.assertRaises(ValueError):pub.config_check(self.root)
    def test_preview_head(self):
        with patch.object(preview,'ROOT',self.root/'data/preview'):
            server=preview.ThreadingHTTPServer(('127.0.0.1',0),preview.Handler)
            t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
            try:
                with urlopen(Request('http://127.0.0.1:%s/'%server.server_port,method='HEAD')) as response:
                    self.assertEqual(response.status,200);self.assertEqual(response.read(),b'')
                    self.assertEqual(int(response.headers['Content-Length']),(self.root/'data/preview/index.html').stat().st_size)
            finally:server.shutdown();server.server_close();t.join()

class ScopeTests(PublicTests):
    def test_boundary_missing_and_sydney(self):
        import datetime
        from oursteps.dates import sydney_today
        day=sydney_today(datetime.datetime(2026,9,11,15,tzinfo=datetime.timezone.utc))
        self.assertEqual(day,'2026-09-12')
        r=pub.recent_policy({'1':'2025-09-12 00:00','2':'2025-09-11 23:59','3':None,'4':'invalid','5':'2026-09-13 00:00'},day)
        self.assertEqual(r['allowed'],['1']);self.assertEqual(r['excluded_missing_dates'],2)
    def test_scopes_and_rolling_hash(self):
        first=self.publish();dest=self.root/'public-site'/first['release']
        self.assertTrue((dest/'recent-1y/1902000.html').exists())
        for name in pub.OWNER_FILES:
            self.assertTrue((dest/name).is_file())
            self.assertFalse((dest/'recent-1y'/name).exists())
        control=json.loads((dest/'control-center.json').read_text())
        self.assertEqual(control['mode'],'safe_action_control_center')
        self.assertEqual(control['archive']['full_public'],1)
        self.assertEqual(control['archive']['recent_1y'],1)
        for name in pub.FIXED-{'index.html','search-index.json'} | {'1902000.html'}:
            full=dest/name;recent=dest/'recent-1y'/name
            self.assertEqual(full.stat().st_ino,recent.stat().st_ino)
            self.assertEqual(recent.stat().st_mode & 0o777,0o644)
            private=self.root/'data/preview'/name
            if private.exists():self.assertNotEqual(private.stat().st_ino,recent.stat().st_ino)
        self.assertTrue((dest/'article-views.json').is_file())
        self.assertTrue((dest/'recent-1y/article-views.json').is_file())
        for name in ('index.html','search-index.json'):
            self.assertNotEqual((dest/name).stat().st_ino,(dest/'recent-1y'/name).stat().st_ino)
        second=pub.publish(self.root,expected={'1902000'},dates={'1902000':None},today='2026-09-12')
        self.assertNotEqual(first['release'],second['release'])
        dest=self.root/'public-site'/second['release']
        self.assertTrue((dest/'1902000.html').exists())
        self.assertFalse((dest/'recent-1y/1902000.html').exists())
        self.assertEqual(json.loads((dest/'recent-1y/search-index.json').read_text()),{})
        report=pub.validate_saved(self.root,second['release'])
        self.assertEqual(report['recent']['excluded_missing_dates'],1)
        third=pub.publish(self.root,expected={'1902000'},dates={'1902000':'2026-09-12 00:00'},today='2027-09-13')
        self.assertEqual(third['recent_articles'],0)
        self.assertEqual(third['release'],second['release'])
        (dest/'recent-1y/style.css').write_text('tampered')
        with self.assertRaises(ValueError):pub.validate_saved(self.root,second['release'])
    def test_canonical_precedence(self):
        import sqlite3
        db=sqlite3.connect(str(self.root/'data/archive.sqlite3'))
        db.execute("INSERT INTO publication_times VALUES(1902000,'2025-09-12 00:00')");db.commit();db.close()
        with patch.object(pub.sys,'platform','linux'):
            self.assertEqual(pub.publication_dates(self.root),{'1902000':'2025-09-12 00:00'})
