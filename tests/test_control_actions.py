import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from oursteps.control_actions import WEB_ACTIONS,claim,finish,request,status,worker_status,write_worker_status
from scripts.control_action_runner import action_spec

class ControlActionQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def test_allowlist_dedupe_claim_finish(self):
        self.assertEqual(WEB_ACTIONS,{'incremental_sync','guide_discovery_v2','historical_backfill','publish_public'})
        with self.assertRaises(ValueError): request(self.root,'rollback_public')
        first,created=request(self.root,'incremental_sync');self.assertTrue(created)
        same,created=request(self.root,'incremental_sync');self.assertFalse(created);self.assertEqual(first['id'],same['id'])
        job=claim(self.root);self.assertEqual(job['state'],'running')
        self.assertIsNone(claim(self.root))
        done=finish(self.root,job['id'],True,' ok\nsecret-free ');self.assertEqual(done['state'],'succeeded');self.assertEqual(done['message'],'ok secret-free')
        again,created=request(self.root,'incremental_sync');self.assertTrue(created);self.assertNotEqual(again['id'],first['id'])
    def test_status_is_sanitized(self):
        job,_=request(self.root,'publish_public')
        data=status(self.root)[0]
        self.assertEqual(set(data),{'id','action','state','created_at','updated_at','message'})
        self.assertNotIn('command',json.dumps(data))
    def test_worker_status_is_sanitized_and_stale_safe(self):
        payload={'state':'RUNNING','task':'historical_backfill','started_at':100,'elapsed_seconds':20,'next_historical_backfill':200,'sampled_at':120,'pid':999,'command':'secret','path':'/private'}
        written=write_worker_status(self.root,payload)
        self.assertEqual(set(written),{'state','task','started_at','elapsed_seconds','next_historical_backfill','sampled_at'})
        self.assertEqual((self.root/'data/control-actions/worker-status.json').stat().st_mode & 0o777,0o644)
        data=worker_status(self.root,now=150)
        self.assertEqual(data['state'],'RUNNING');self.assertEqual(data['task'],'historical_backfill')
        self.assertEqual(set(data),{'state','task','started_at','elapsed_seconds','next_historical_backfill','sampled_at'})
        self.assertNotIn('pid',json.dumps(data));self.assertNotIn('/private',json.dumps(data))
        stale=worker_status(self.root,now=211)
        self.assertEqual(stale['state'],'UNKNOWN');self.assertEqual(stale['task'],'unknown')

    def test_runner_allowlist_is_independent(self):
        for action in WEB_ACTIONS: self.assertIsNotNone(action_spec(action))
        self.assertIn('20', action_spec('historical_backfill')[0])
        for action in ('rollback_public','authenticate','anything'):
            with self.assertRaises(ValueError): action_spec(action)
