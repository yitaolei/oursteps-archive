import _test_config
import json
from pathlib import Path
import tempfile
import unittest
from oursteps.store import Store
from oursteps.control_center import OWNER_FILES, files, snapshot

class ControlCenterTests(unittest.TestCase):
    def test_snapshot_is_sanitized_and_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); store=Store(root/'data'); store.seed([1,2],'fixture')
            store.db.execute("UPDATE threads SET status='complete' WHERE tid=1")
            store.db.executescript("CREATE TABLE guide_progress(id INTEGER PRIMARY KEY,state TEXT,next_page INTEGER,last_end INTEGER,new_tids INTEGER,last_success REAL,retry_at REAL,error TEXT); INSERT INTO guide_progress VALUES(1,'budget_reached',55,54,92,123.0,0,'private path /secret'); CREATE TABLE guide_tids(tid INTEGER PRIMARY KEY); INSERT INTO guide_tids VALUES(1); INSERT INTO guide_tids VALUES(2);")
            store.db.commit(); store.db.close()
            (root/'config').mkdir(); (root/'config/tool_registry.json').write_text(json.dumps({'tools':[{'id':'guide_discovery_v2','label':'Guide','schedule':'03:15 daily'}]}))
            (root/'data/manual-missing-tids.txt').write_text('2\n2\n')
            one=snapshot(root,1,1); two=snapshot(root,1,1)
            self.assertEqual(one,two)
            self.assertEqual(one['archive']['active_missing'],1)
            self.assertEqual(one['guide']['next_page'],55)
            self.assertEqual(one['guide']['discovered_tids'],2)
            self.assertTrue(one['guide']['has_error'])
            text=json.dumps(one)
            self.assertNotIn('/secret',text)
            self.assertNotIn('error": "private',text)
            rendered=files(root,1,1)
            self.assertEqual(set(rendered),OWNER_FILES)
            self.assertNotIn(b'<script>',rendered['control-center.html'])
            self.assertIn(b'Archive Worker',rendered['control-center.html'])
            self.assertIn(b'/control-action/worker-status',rendered['control-center.js'])
