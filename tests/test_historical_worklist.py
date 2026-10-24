import sqlite3
import unittest
from scripts.historical_worklist import worklist,classify
class WorklistTests(unittest.TestCase):
 def test_access_holds(self):
  self.assertEqual(classify('auth_required','http_403'),'access_hold')
  self.assertEqual(classify('retry_later','incomplete_html'),'transient_hold')
  self.assertEqual(classify('not_found','thread_not_found'),'review')
 def test_reproducible_read_only_queue(self):
  db=sqlite3.connect(':memory:')
  db.executescript('CREATE TABLE inventory(tid INTEGER PRIMARY KEY); CREATE TABLE threads(tid INTEGER PRIMARY KEY,status TEXT); CREATE TABLE jobs(tid INTEGER,page INTEGER,state TEXT,error TEXT);')
  db.executemany('INSERT INTO inventory VALUES(?)',[(1,),(2,),(3,)])
  db.executemany('INSERT INTO threads VALUES(?,?)',[(1,'partial'),(2,'complete'),(3,'pending')])
  db.execute('INSERT INTO jobs VALUES(1,1,?,?)',('auth_required','http_403'))
  report=worklist(db)
  self.assertEqual(report['total_incomplete'],2)
  self.assertEqual([v['tid'] for v in report['items']],[1,3])
  self.assertEqual(report['items'][0]['work_state'],'access_hold')
  self.assertEqual(report['site_requests'],0)
