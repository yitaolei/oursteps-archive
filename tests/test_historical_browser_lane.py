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

class HandoffResultTests(unittest.TestCase):
    def test_confirmed_import_is_not_lost_on_handoff_failure(self):
        import json
        from subprocess import CompletedProcess
        done={'imported':1,'complete':1,'failed':0,'stop_reason':None,'handoff_error':'NAS verifier unavailable'}
        with patch.object(lane,'run_group',return_value=CompletedProcess([],2,json.dumps(done)+'\n','')):
            result=lane.batch(['https://example.org/test'])
        self.assertEqual(result['complete'],1)
        self.assertTrue(result['handoff_warning'])

class MultiTidPlanningTests(unittest.TestCase):
    def test_selects_distinct_tids_excluding_failures(self):
        rows=[{'tid':t,'page':1,'url':f'u{t}'} for t in (8,7,6,5)]
        with patch.object(lane,'remote_json',return_value={'items':rows}):
            chosen=lane.choose_multi({'failed':{'7':{'reason':'parse'}}},limit=3)
        self.assertEqual([x['tid'] for x in chosen],[8,6,5])
    def test_refuses_multi_when_active_or_site_hold(self):
        with patch.object(lane,'remote_json') as remote:
            self.assertEqual(lane.choose_multi({'active_tid':9}),[])
            self.assertEqual(lane.choose_multi({'site_hold':{'reason':'access'}}),[])
        remote.assert_not_called()
    def test_isolated_per_tid_committed_results(self):
        from oursteps.parser import thread_url
        chosen=[{'tid':101},{'tid':102}]
        state={'failed':{}}
        result={'items':[{'url':thread_url(101,1),'thread_status':'complete'}],
                'failures':[{'url':thread_url(102,1),'error':'ParseError'}]}
        with patch.object(lane,'write_state') as save:
            outcome=lane.apply_multi_result(state,chosen,result)
        self.assertEqual(outcome['complete'],1)
        self.assertIn('102',state['failed'])
        self.assertTrue(state['publish_pending'])
        save.assert_called_once()
    def test_rejects_unselected_ack(self):
        from oursteps.parser import thread_url
        with self.assertRaisesRegex(RuntimeError,'unselected_tid'):
            lane.apply_multi_result({},[{'tid':101}],{'items':[{'url':thread_url(999,1)}]})

class MultiTidPilotExecutionTests(unittest.TestCase):
    def test_multiple_completed_articles_one_browser_call(self):
        from oursteps.parser import thread_url
        entries=[{'tid':101,'url':thread_url(101,1)},{'tid':102,'url':thread_url(102,1)}]
        report={'items':[{'url':thread_url(101,1),'thread_status':'complete'},
                         {'url':thread_url(102,1),'thread_status':'complete'}],
                'failures':[]}
        state={'failed':{}}
        with patch.object(lane,'choose_multi',return_value=entries),patch.object(lane,'batch',return_value=report) as browser,patch.object(lane,'write_state'),patch.object(lane,'remote_json',return_value={'counts':{'complete':2,'incomplete':0}}):
            output=lane.multi_pilot(state,2)
        browser.assert_called_once_with([x['url'] for x in entries],allow_partial=True)
        self.assertEqual(output['completed_pages'],2)
        self.assertTrue(state['publish_pending'])
    def test_access_denial_sets_persistent_hold_without_next_thread(self):
        state={'failed':{}}
        entries=[{'tid':101,'url':'url1'},{'tid':102,'url':'url2'}]
        with patch.object(lane,'choose_multi',return_value=entries),patch.object(lane,'batch',side_effect=lane.BrowserSiteHold('denied')) as browser,patch.object(lane,'write_state') as save:
            with self.assertRaises(lane.BrowserSiteHold):lane.multi_pilot(state,2)
        self.assertEqual(browser.call_count,1)
        self.assertEqual(state['site_hold']['reason'],'browser_site_access_restricted')
        save.assert_called_once()
