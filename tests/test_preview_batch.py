import _test_config  # Configure synthetic identity before application imports.
import re
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'.deps')]
from oursteps.store import Store
from oursteps.parser import parse_thread,thread_url
from oursteps import preview,preview_batch as batch
ROOT=Path(__file__).resolve().parents[1]

class PreviewBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.store=Store(self.root/'data');self.tids=[1902000,1902001,1902002]
        self.store.seed(self.tids,'reconciliation')
        for offset,tid in enumerate(self.tids):
            for page,name in [(1,'thread-first.html'),(2,'thread-second.html')]:
                raw=(ROOT/'tests/fixtures'/name).read_bytes().replace(b'1902000',str(tid).encode())
                raw=re.sub(rb'(?<![0-9])(101|102|103|104)(?![0-9])',lambda m:str(int(m[0])+offset*1000).encode(),raw)
                url=thread_url(tid,page);snap=self.store.snapshot(url,raw)
                self.store.save_page(url,parse_thread(raw,url),snap)
        preview.build(self.store)  # Tiny local fixture only, never production.
        self.out=self.store.root/'preview'
    def tearDown(self):self.store.db.close();self.tmp.cleanup()
    def change(self,tids):
        with self.store.db:
            for tid in tids:
                self.store.db.execute("UPDATE posts SET body_html=body_html||'<p>changed</p>',body_text=body_text||' changed' WHERE tid=?",(tid,))
    def contents(self):return {p.name:p.read_bytes() for p in self.out.iterdir()}
    def test_one_and_multiple_equivalence_no_unrelated_rewrite(self):
        for tids in ([self.tids[0]],self.tids[:2]):
            self.change(tids)
            untouched=self.out/('%s.html'%self.tids[2]);stamp=untouched.stat().st_mtime_ns;old=untouched.read_bytes()
            with patch.object(preview,'build',side_effect=AssertionError('full rebuild forbidden')):
                result=batch.build_batch(self.store,tids)
            self.assertEqual(result['rendered_articles'],len(tids));self.assertFalse(result['full_rebuild'])
            self.assertEqual(untouched.stat().st_mtime_ns,stamp);self.assertEqual(untouched.read_bytes(),old)
            rendered={t:(self.out/('%s.html'%t)).read_bytes() for t in tids}
            search=json.loads((self.out/'search-index.json').read_text())
            preview.build(self.store)
            self.assertEqual(rendered,{t:(self.out/('%s.html'%t)).read_bytes() for t in tids})
            self.assertEqual(search,json.loads((self.out/'search-index.json').read_text()))
    def test_invalid_nonexistent_incomplete_no_writes(self):
        before=self.contents()
        for tids in ([0],[-1],['bad'],[True],[999],[self.tids[0],999]):
            with self.assertRaises(ValueError):batch.build_batch(self.store,tids)
            self.assertEqual(self.contents(),before)
        with self.store.db:self.store.db.execute("UPDATE threads SET status='partial' WHERE tid=?",(self.tids[0],))
        with self.assertRaises(ValueError):batch.build_batch(self.store,[self.tids[0]])
        self.assertEqual(self.contents(),before)
    def test_failed_render_changes_no_live_files_and_retry(self):
        self.change(self.tids[:2]);before=self.contents();real=preview.body_html;calls=[]
        def fail(*a,**kw):
            calls.append(1)
            if len(calls)==4:raise ValueError('fixture render failure')
            return real(*a,**kw)
        with patch.object(preview,'body_html',side_effect=fail),self.assertRaises(ValueError):
            batch.build_batch(self.store,self.tids[:2])
        self.assertEqual(self.contents(),before)
        self.assertEqual(batch.build_batch(self.store,self.tids[:2])['rendered_articles'],2)
    def test_failed_commit_keeps_retry_marker(self):
        self.change(self.tids[:2]);real=batch.os.replace
        def fail(src,dst):
            if Path(dst)==self.out/'index.html':raise OSError('fixture commit failure')
            return real(src,dst)
        with patch.object(batch.os,'replace',side_effect=fail),self.assertRaises(OSError):
            batch.build_batch(self.store,self.tids[:2])
        self.assertTrue((self.store.root/'preview-incremental-pending.json').exists())
        report=batch.build_batch(self.store,[self.tids[2]])
        self.assertEqual(report['rendered_articles'],3)
        self.assertFalse((self.store.root/'preview-incremental-pending.json').exists())
    def test_no_silent_full_fallback(self):
        (self.out/'index.html').unlink()
        with patch.object(preview,'build',side_effect=AssertionError('full build forbidden')),self.assertRaises(ValueError):
            batch.build_batch(self.store,self.tids[:1])
    def test_cache_mismatch_no_full_fallback(self):
        (self.out/'index.html').write_text('<html></html>');before=self.contents()
        with patch.object(preview,'build',side_effect=AssertionError('full build forbidden')),self.assertRaises(ValueError):
            batch.build_batch(self.store,self.tids[:1])
        self.assertEqual(before,self.contents())
    def test_automatic_selection_and_dry_run(self):
        current=self.root/'public-site/current';current.mkdir(parents=True)
        (current/('%s.html'%self.tids[0])).write_text('published')
        before=self.contents();report=batch.build_batch(self.store,dry_run=True)
        self.assertEqual(report['tids'],self.tids[1:]);self.assertEqual(report['rendered_articles'],0)
        self.assertEqual(before,self.contents())
        report=batch.build_batch(self.store,self.tids[:1]*2);self.assertEqual(report['rendered_articles'],1)
    def test_empty_batch_no_rewrite(self):
        before=self.contents();self.assertEqual(batch.build_batch(self.store,[])['rendered_articles'],0)
        self.assertEqual(before,self.contents())
