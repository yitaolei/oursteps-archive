import _test_config  # Configure synthetic identity before application imports.
import argparse
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import batch_operations as ops
loader = importlib.machinery.SourceFileLoader('batch_tool', str(ROOT / 'scripts/mac-tools/run-oursteps-batch'))
spec = importlib.util.spec_from_loader(loader.name, loader)
batch = importlib.util.module_from_spec(spec); loader.exec_module(batch)


class BatchTests(unittest.TestCase):
    def fake(self, ready=0, states=None, pending=False, fail=None):
        self.calls=[]; self.ready=ready; self.sleeps=[]
        states=iter(states or [])
        def call(action, **values):
            self.calls.append((action, values))
            if action == fail: raise RuntimeError('test failure')
            if action == 'status': return dict(unpublished=list(range(1,self.ready+1)), pending=pending)
            if action == 'reconcile':
                r=next(states, dict(run_state='bounded_backfill_finished'))
                self.ready+=r.get('imported_this_run',0)
                return r
            if action == 'preview':return dict(tids=list(range(1,self.ready+1)),rendered_articles=self.ready,full_rebuild=False,elapsed_seconds=1,hashes={'1.html':'abc'})
            return dict(output='ok')
        return call
    def run_it(self, call, no_publish=False, rounds=40):
        return batch.run_batch(argparse.Namespace(target=10,max_rounds=rounds,no_publish=no_publish),call=call,sleep=self.sleeps.append,now=lambda:100,emit=lambda x:None)
    def test_ready_batch_exact_tids_and_order(self):
        result=self.run_it(self.fake(ready=11))
        actions=[a for a,v in self.calls]
        self.assertNotIn('reconcile',actions)
        self.assertLess(actions.index('preview'),actions.index('publish'))
        self.assertLess(actions.index('publish'),actions.index('healthcheck'))
        self.assertEqual(next(v['tids'] for a,v in self.calls if a=='check'),result['tids'])
    def test_retry_deadline_and_budgets(self):
        self.run_it(self.fake(states=[dict(run_state='retry_later',retry_at=300),dict(run_state='budget_reached',imported_this_run=10)]))
        self.assertEqual(self.sleeps,[215])
    def test_no_progress_bounded_review_skipped(self):
        result=self.run_it(self.fake(states=[dict(run_state='partial')]*5))
        self.assertEqual(sum(a=='reconcile' for a,v in self.calls),3)
        self.assertEqual(result['status'],'no_progress')
    def test_round_limit_publishes_small_complete_batch(self):
        result=self.run_it(self.fake(states=[dict(run_state='budget_reached',imported_this_run=1)]),rounds=1)
        self.assertEqual(result['tids'],[1]);self.assertEqual(self.sleeps,[])
    def test_no_publish(self):
        self.assertEqual(self.run_it(self.fake(ready=10),no_publish=True)['status'],'preview_only')
        self.assertFalse(any(a in ('publish','healthcheck','check') for a,v in self.calls))
    def test_marker_fail_closed(self):
        with self.assertRaises(RuntimeError):self.run_it(self.fake(ready=10,pending=True))
        self.assertFalse(any(a=='publish' for a,v in self.calls))
    def test_failure_stops_following_steps(self):
        for failure,forbidden in [('preview','publish'),('publish','healthcheck'),('healthcheck','check')]:
            with self.assertRaises(RuntimeError):self.run_it(self.fake(ready=10,fail=failure))
            self.assertFalse(any(a==forbidden for a,v in self.calls))
            self.assertEqual(self.calls[-1][1]['result']['status'],'failed')
    def test_bad_preview_receipt_and_post_render_marker(self):
        for bad in ('full', 'pending'):
            base=self.fake(ready=10); rendered=False
            def call(action, **values):
                nonlocal rendered
                result=base(action, **values)
                if action=='preview':
                    rendered=True
                    if bad=='full':result['full_rebuild']=True
                if action=='status' and rendered and bad=='pending':result['pending']=True
                return result
            with self.assertRaises(RuntimeError):self.run_it(call)
            self.assertFalse(any(a=='publish' for a,v in self.calls))
    def test_halt_not_ignored(self):
        with self.assertRaises(RuntimeError):self.run_it(self.fake(states=[dict(run_state='existing_archive_halt_requires_review')]))
        self.assertFalse(any(a=='preview' for a,v in self.calls))
    def test_writer_wait_bounded(self):
        with patch.object(batch,'rpc',side_effect=[BlockingIOError(),{'ok':1}]),patch.object(batch.time,'sleep') as sleep:
            self.assertEqual(batch.wait_call('preview'),{'ok':1});sleep.assert_called_once_with(60)
        with patch.object(batch,'rpc',side_effect=BlockingIOError()),patch.object(batch.time,'monotonic',side_effect=[0,1801]):
            with self.assertRaises(RuntimeError):batch.wait_call('publish')
    def test_early_reconcile_report_is_current_stdout(self):
        with patch.object(ops,'call',return_value='{"run_state":"retry_later","retry_at":123}'):
            self.assertEqual(ops.operate({'action':'reconcile'})['retry_at'],123)


class StatusTests(unittest.TestCase):
    def test_read_only_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'data').mkdir();current=root/'public-site/current';current.mkdir(parents=True);(current/'recent-1y').mkdir()
            (current/'1.html').write_text('test');(current/'recent-1y/1.html').write_text('test')
            (root/'data/manual-missing-tids.txt').write_text('2\n')
            for name,ddl in [('reconcile.sqlite3',"CREATE TABLE candidates(tid,recovery,error);INSERT INTO candidates VALUES(2,'review_required','partial');"),('archive.sqlite3',"CREATE TABLE threads(tid,status,discovered_via);INSERT INTO threads VALUES(3,'complete','reconciliation');")]:
                c=sqlite3.connect(root/'data'/name);c.executescript(ddl);c.close()
            def snapshot():return {str(p.relative_to(root)):(p.stat().st_mtime_ns,hashlib.sha256(p.read_bytes()).hexdigest()) for p in root.rglob('*') if p.is_file()}
            before=snapshot();d=ops.status(root)
            self.assertEqual(snapshot(),before);self.assertEqual(d['unpublished'],[3]);self.assertEqual(d['full_public'],1)
            self.assertIn('2  partial / review_required',batch.dashboard(d))


if __name__=='__main__':unittest.main()
