import _test_config  # Configure synthetic identity before application imports.
import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.deps'))
from oursteps.store import Store
from oursteps.fallback import prepare,add_shard,merge,split,status,results
class FallbackTests(unittest.TestCase):
 def test_merge_baseline_and_supported_splits(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=Store(tmp)
   from oursteps.history import prepare as inventory_prepare
   inventory_prepare(s)
   with s.db:
    s.db.execute("INSERT INTO threads(tid,discovered_via) VALUES(1,'test')")
    s.db.execute('INSERT INTO inventory VALUES(1,0,0)')
   prepare(s)
   with s.db:
    key=add_shard(s,43);merge(s,key,[dict(tid=1,title='old'),dict(tid=2,title='new')]);merge(s,key,[dict(tid=2,title='new')])
    split(s,s.db.execute('SELECT * FROM discovery_shards WHERE id=?',(key,)).fetchone(),[31536000,86400])
   prepare(s);self.assertEqual(status(s)['original_personal_tids'],1);self.assertEqual(status(s)['additional_fallback_tids'],1)
   self.assertEqual(status(s)['shards_remaining'],2)
   self.assertEqual(s.db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
   s.db.close()
 def test_result_requires_target_owner(self):
  body='<html><li><h3><a href="forum.php?mod=viewthread&tid=2">title</a></h3><a href="home.php?uid=424242">archive_test_user</a></li></html>'
  self.assertEqual(results(body.encode(),'https://www.oursteps.com.au/bbs/search.php?mod=forum')[0][0]['tid'],2)
  from oursteps.parser import ParseError
  with self.assertRaises(ParseError):results(body.replace('424242','999').encode(),'https://www.oursteps.com.au/bbs/search.php?mod=forum')
 def test_limited_refinement_preserves_completed_and_inventory(self):
  from oursteps.fallback import refine_limited
  with tempfile.TemporaryDirectory() as tmp:
   s=Store(tmp);prepare(s)
   with s.db:
    done=add_shard(s,43,86400)
    limited=add_shard(s,43,31536000,'1')
    s.db.execute("UPDATE discovery_shards SET state='complete' WHERE id=?",(done,))
    s.db.execute("UPDATE discovery_shards SET state='limited' WHERE id=?",(limited,))
    merge(s,limited,[dict(tid=i,title='kept') for i in range(1,5439)])
   self.assertTrue(refine_limited(s,[31536000,86400],[1,2,3,4,5]))
   self.assertEqual(s.db.execute('SELECT count(*) FROM inventory').fetchone()[0],5438)
   self.assertEqual(s.db.execute('SELECT state FROM discovery_shards WHERE id=?',(done,)).fetchone()[0],'complete')
   self.assertEqual(status(s)['completeness'],'NO')
   self.assertEqual(s.db.execute("SELECT count(*) FROM discovery_shards WHERE state='pending' AND refinement=1").fetchone()[0],5)
   refine_limited(s,[31536000,86400],[1,2,3,4,5])
   self.assertEqual(s.db.execute('SELECT count(*) FROM discovery_shards').fetchone()[0],7)
   child=s.db.execute('SELECT * FROM discovery_shards WHERE special=1').fetchone()
   with s.db:split(s,child,[31536000,86400])
   self.assertEqual(s.db.execute('SELECT state FROM discovery_shards WHERE id=?',(child['id'],)).fetchone()[0],'unresolved_limited')
   s.db.close()
