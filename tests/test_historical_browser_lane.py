import sqlite3
import unittest
from unittest.mock import patch
from scripts.historical_browser_candidates import first_unstarted,continuations,counts
from scripts import historical_browser_lane as lane

class HistoricalBrowserLaneTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.executescript('''CREATE TABLE inventory(tid INTEGER PRIMARY KEY);
        CREATE TABLE threads(tid INTEGER PRIMARY KEY,status TEXT);
        CREATE TABLE jobs(tid INTEGER,page INTEGER,state TEXT,error TEXT);''')
        self.db.executemany('INSERT INTO inventory VALUES(?)',[(1,),(2,),(3,)])
        self.db.executemany('INSERT INTO threads VALUES(?,?)',[(1,'pending'),(2,'complete'),(3,'partial')])
        self.db.execute("INSERT INTO jobs VALUES(3,2,'pending',NULL)")
    def tearDown(self):self.db.close()
    def test_unstarted_inventory_only(self):
        result=first_unstarted(self.db,20)
        self.assertEqual([r['tid'] for r in result],[1])
    def test_continuation_is_due_pending_page(self):
        self.assertEqual([r['page'] for r in continuations(self.db,3,20)],[2])
        self.db.execute("UPDATE jobs SET state='auth_required',error='http_403' WHERE tid=3")
        self.assertEqual(continuations(self.db,3,20),[])
    def test_continuation_rejects_tid_with_denied_sibling(self):
        self.db.execute("INSERT INTO jobs VALUES(3,1,'auth_required','http_403')")
        self.assertEqual(continuations(self.db,3,20),[])

    def test_failed_tids_skipped(self):
        with patch.object(lane,'remote_json',return_value={'items':[{'tid':10,'page':1,'url':'one'}, {'tid':11,'page':1,'url':'two'}]}):
            self.assertEqual(lane.choose({'failed':{'10':{'reason':'held'}}})['tid'],11)
    def test_batch_maximum(self):
        with self.assertRaises(ValueError):lane.batch(['u']*13)
    def test_browser_denial_trips_site_circuit_breaker(self):
        from subprocess import CompletedProcess
        result=CompletedProcess([],2,'{"stop_reason":"RuntimeError:manual_required:http_403","failed":1}\n','')
        with patch.object(lane,'run_group',return_value=result):
            with self.assertRaises(lane.BrowserSiteHold):lane.batch(['https://example.org/'])
    def test_inventory_counts(self):
        self.assertEqual(counts(self.db),{'complete':1,'incomplete':2})
