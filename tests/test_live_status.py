import _test_config  # Configure synthetic identity before application imports.
import importlib.util
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
spec=importlib.util.spec_from_file_location('monitor',Path(__file__).resolve().parents[1]/'scripts/backfill_live_status.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class MonitorTests(unittest.TestCase):
 def test_read_only_wal_snapshot_and_rates(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'data').mkdir();p=root/'data/archive.sqlite3'
   writer=sqlite3.connect(p);writer.execute('PRAGMA journal_mode=WAL')
   writer.executescript('''CREATE TABLE inventory(tid INTEGER); CREATE TABLE threads(tid INTEGER,status TEXT);
CREATE TABLE jobs(tid INTEGER,state TEXT); CREATE TABLE posts(pid INTEGER);
CREATE TABLE pages(snapshot_id INTEGER,result TEXT); CREATE TABLE snapshots(id INTEGER,fetched_at REAL);
CREATE TABLE settings(key TEXT,value TEXT); CREATE TABLE media_files(state TEXT);
INSERT INTO inventory VALUES(1); INSERT INTO threads VALUES(1,'complete');
INSERT INTO snapshots VALUES(1,100); INSERT INTO pages VALUES(1,'parsed');''')
   before=(p.read_bytes(),Path(str(p)+'-wal').read_bytes())
   with patch.object(m.sys,'platform','linux'):
    r=m.snapshot(root)
   self.assertEqual((r['completed'],r['total'],r['pages'],r['worker']),(1,1,1,'STOPPED'))
   self.assertEqual(before,(p.read_bytes(),Path(str(p)+'-wal').read_bytes()))
   self.assertFalse((root/'data/worker.lock').exists())
   self.assertEqual(m.rates([(0,0),(300,1),(900,3)])[1],12)
   self.assertIsNone(m.rates([(0,0),(300,1)])[1])
   writer.close()
