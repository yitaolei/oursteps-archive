import _test_config  # Configure synthetic identity before application imports.
import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.deps'))
from unittest.mock import patch
from oursteps.store import Store
from oursteps.history import prepare,inventory_page,report,backfill
from oursteps.parser import directory_url
PAGE=b'<html><table><tr><th><a href="forum.php?mod=viewthread&tid=123">title</a></th></tr></table></html>'
class HistoryTests(unittest.TestCase):
 def test_inventory_idempotent_no_body_jobs(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=Store(tmp);prepare(s)
   self.assertIsNone(inventory_page(s,PAGE,directory_url()))
   inventory_page(s,PAGE,directory_url())
   self.assertEqual(report(s)['total_discovered'],1)
   self.assertEqual(s.db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
   self.assertEqual(report(s)['date_unknown'],1)
   with s.db:s.db.execute("UPDATE threads SET status='complete',title='full title' WHERE tid=123")
   inventory_page(s,PAGE,directory_url())
   self.assertEqual(s.db.execute('SELECT title FROM threads').fetchone()[0],'full title')
   s.db.close()
 def test_empty_inventory_never_fetches(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=Store(tmp)
   with patch('oursteps.history.Fetcher',side_effect=AssertionError):self.assertIn('inventory_empty',backfill(s))
   s.db.close()
 def test_exact_resume_and_pagination_limit(self):
  from oursteps.history import discover
  from oursteps.parser import PaginationLimit,guard,soup_of
  raw='<html><div id="messagetext">抱歉，分页数不在允许的范围内</div></html>'.encode()
  with self.assertRaises(PaginationLimit):guard(soup_of(raw))
  with tempfile.TemporaryDirectory() as tmp:
   s=Store(tmp);prepare(s)
   with s.db:s.db.execute("UPDATE inventory_progress SET next_page=101,state='permission_or_challenge'")
   calls=[]
   class Fake:
    delay=5
    def __init__(self,*args):pass
    def get(self,url):
     calls.append(url);return s.snapshot(url,raw)
   with patch('oursteps.history.Fetcher',Fake):
    self.assertEqual(discover(s,now=True),'pagination_limited')
    self.assertEqual(calls,[directory_url(101)])
    discover(s,now=True);self.assertEqual(len(calls),1)
   self.assertEqual(s.db.execute('SELECT next_page FROM inventory_progress').fetchone()[0],101)
   s.db.close()
 def test_best_effort_now_scoped_to_incomplete_inventory(self):
  from unittest.mock import Mock
  from oursteps.fallback import prepare as fallback_prepare,add_shard
  from oursteps.media import SCHEMA as MEDIA_SCHEMA
  with tempfile.TemporaryDirectory() as tmp:
   s=Store(tmp);fallback_prepare(s);s.db.executescript(MEDIA_SCHEMA)
   with s.db:
    for tid,state in [(1,'complete'),(2,'pending'),(3,'pending')]:
     s.db.execute('INSERT INTO threads(tid,status,discovered_via) VALUES(?,?,?)',(tid,state,'test'))
    s.db.executemany('INSERT INTO inventory VALUES(?,0,0)',[(1,),(2,)])
    key=add_shard(s,43);s.db.execute("UPDATE discovery_shards SET state='unresolved_limited'")
   fetch=Mock()
   with patch('oursteps.history.Fetcher',return_value=fetch),patch('oursteps.cli.run',return_value='complete') as work,patch('oursteps.media.run') as media:
    self.assertEqual(backfill(s,now=True,best_effort=True),'best_effort_pass_finished')
    self.assertTrue(fetch.daily_now);self.assertEqual(work.call_count,1)
    self.assertEqual(work.call_args.kwargs['tids'],[2]);media.assert_not_called()
   r=report(s);self.assertEqual(r['archive_mode'],'BEST_EFFORT');self.assertEqual(r['archive_scope'],'INCOMPLETE_DISCOVERY');self.assertEqual(r['completeness'],'NO')
   self.assertEqual(s.db.execute("SELECT count(*) FROM discovery_shards WHERE state='unresolved_limited'").fetchone()[0],1)
   self.assertEqual(s.db.execute('SELECT count(*) FROM inventory').fetchone()[0],2)
   s.db.close()
