import _test_config  # Configure synthetic identity before application imports.
import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.deps'))
from unittest.mock import Mock,patch
from oursteps.store import Store
from oursteps.parser import thread_url,Blocked
from oursteps.fetch import Fetcher
from oursteps.history import prepare,backfill
from oursteps.cli import run
class PolicyTests(unittest.TestCase):
 def test_redirect_codes_same_thread(self):
  for code in (301,302,303,307,308):
   with tempfile.TemporaryDirectory() as tmp:
    s=Store(tmp);s.historical_mode=True
    target=thread_url(123,3);saved=s.snapshot(target,b'<html>cached</html>')
    f=Fetcher.__new__(Fetcher);f.store=s;f.daily_now=True;f.delay=0;f.mode='anonymous';f.rules=[]
    response=Mock();response.code=code;response.headers={'Location':target};response.read.return_value=b''
    response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
    f.opener=Mock();f.opener.open.return_value=response
    with patch('oursteps.fetch.time.sleep'):
     self.assertEqual(f.request(thread_url(123,4))['id'],saved['id'])
    self.assertEqual(f.opener.open.call_count,1);s.db.close()
 def test_isolated_block_then_global_threshold(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=Store(tmp);s.historical_mode=True
   for tid in (1,2,3):
    with s.db:
     s.db.execute("INSERT INTO threads(tid,discovered_via) VALUES(?,'test')",(tid,))
     s.db.execute("INSERT INTO jobs(url,kind,tid,page) VALUES(?,'thread',?,1)",(thread_url(tid),tid))
    f=Mock();f.get.side_effect=Blocked('http_403')
    self.assertEqual(run(s,tids=[tid],fetcher_override=f),'auth_required' if tid<3 else 'halted')
    if tid<3:self.assertFalse(s.setting('halt'))
   self.assertEqual(s.db.execute("SELECT count(*) FROM jobs WHERE state='auth_required'").fetchone()[0],3)
   s.db.close()
 def test_next_tid_after_failure(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=Store(tmp);prepare(s)
   with s.db:
    for tid in (1,2):
     s.db.execute("INSERT INTO threads(tid,discovered_via) VALUES(?,'test')",(tid,))
     s.db.execute('INSERT INTO inventory VALUES(?,0,0)',(tid,))
   def failed(store,**kw):
    job=store.db.execute('SELECT * FROM jobs WHERE tid=?',(kw['tids'][0],)).fetchone()
    store.fail(job,'fixture',retry=True);return 'retry_later'
   from oursteps.media import SCHEMA
   s.db.executescript(SCHEMA)
   with patch('oursteps.history.Fetcher'),patch('oursteps.cli.run',side_effect=failed) as work,patch('oursteps.media.run',return_value={'downloaded_this_run':0}),patch('oursteps.preview.build'):
    backfill(s,now=True,best_effort=True)
   self.assertEqual(work.call_count,2)
   self.assertTrue((s.root/'backfill-problems.json').exists());s.db.close()
