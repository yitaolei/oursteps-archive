import json
import unittest
from subprocess import CompletedProcess
from unittest.mock import patch
from scripts import historical_browser_burst as b
class BrowserBurstTests(unittest.TestCase):
 def test_three_successful_runs(self):
  res=CompletedProcess([],0,json.dumps({'status':'completed','pages_imported':2,'completed_pages':1})+'\n','')
  with patch.object(b,'run_group',return_value=res) as call:
   result=b.burst(3,5)
  self.assertEqual(call.call_count,3)
  self.assertEqual(result['pages_imported'],6)
  self.assertEqual(result['threads_completed'],3)
 def test_access_hold_stops_remaining_runs(self):
  res=CompletedProcess([],2,json.dumps({'status':'held','pages_imported':0,'completed_pages':0})+'\n','')
  with patch.object(b,'run_group',return_value=res) as call,patch('scripts.historical_browser_lane.saved',return_value={'site_hold':{'reason':'denied'}}):
   result=b.burst(3,5)
  self.assertEqual(call.call_count,1)
  self.assertEqual(result['runs'],1)

class DeferredPublishTests(unittest.TestCase):
 def test_one_publish_for_five_completed_threads(self):
  res=CompletedProcess([],0,json.dumps({'status':'completed','pages_imported':2,'completed_pages':1,'publish_pending':True})+'\n','')
  with patch.object(b,'run_group',return_value=res), patch('scripts.historical_browser_lane.publish_release') as publish:
   result=b.burst(5,5)
  self.assertEqual(result['threads_completed'],5)
  publish.assert_called_once_with()
 def test_publish_after_partial_failure(self):
  good=CompletedProcess([],0,json.dumps({'status':'completed','pages_imported':1,'completed_pages':1,'publish_pending':True})+'\n','')
  bad=CompletedProcess([],2,json.dumps({'status':'held','pages_imported':0})+'\n','')
  with patch.object(b,'run_group',side_effect=[good,bad]),patch('scripts.historical_browser_lane.publish_release') as publish,patch('scripts.historical_browser_lane.saved',return_value={'site_hold':{'reason':'denied'}}):
   result=b.burst(5,5)
  self.assertEqual(result['runs'],2)
  publish.assert_called_once_with()

 def test_timeout_after_success_still_publishes(self):
  from subprocess import TimeoutExpired
  good=CompletedProcess([],0,json.dumps({'status':'completed','pages_imported':1,'completed_pages':1,'publish_pending':True})+'\n','')
  with patch.object(b,'run_group',side_effect=[good,TimeoutExpired('lane',1500)]),patch('scripts.historical_browser_lane.publish_release') as publish:
   with self.assertRaises(TimeoutExpired):b.burst(5,5)
  publish.assert_called_once_with()

class PersistentPublishRecoveryTests(unittest.TestCase):
 def test_stale_publication_is_recovered_before_new_browser_navigation(self):
  from scripts import historical_browser_lane as lane
  state={'publish_pending':True,'failed':{}}
  with patch.object(lane,'saved',return_value=state),patch.object(lane,'write_state') as save,patch.object(lane,'publish_release') as publish,patch.object(b,'run_group',return_value=CompletedProcess([],0,'{"status":"empty"}\n','')) as run:
   b.burst(1,5)
  publish.assert_called_once_with()
  save.assert_called_once()
  self.assertFalse(save.call_args.args[0]['publish_pending'])
  run.assert_called_once()
 def test_failed_publication_remains_pending(self):
  from scripts import historical_browser_lane as lane
  state={'publish_pending':True}
  with patch.object(lane,'saved',return_value=state),patch.object(lane,'write_state') as save,patch.object(lane,'publish_release',side_effect=RuntimeError('publish failed')),patch.object(b,'run_group') as run:
   with self.assertRaisesRegex(RuntimeError,'publish failed'):b.burst(1,5)
  save.assert_not_called()
  run.assert_not_called()

class FailureIsolationTests(unittest.TestCase):
 def test_isolated_failure_continues_to_next_thread(self):
  from scripts import historical_browser_lane as lane
  held=CompletedProcess([],2,json.dumps({'status':'held','tid':42,'pages_imported':0})+'\n','')
  good=CompletedProcess([],0,json.dumps({'status':'completed','tid':41,'pages_imported':1,'completed_pages':1})+'\n','')
  with patch.object(lane,'saved',return_value={'failed':{'42':{'reason':'parse'}}}),patch.object(b,'run_group',side_effect=[held,good]) as run:
   out=b.burst(2,5)
  self.assertEqual(out['runs'],2)
  self.assertEqual(out['threads_completed'],1)
 def test_site_hold_stops_the_burst(self):
  from scripts import historical_browser_lane as lane
  held=CompletedProcess([],2,json.dumps({'status':'held','tid':42})+'\n','')
  with patch.object(lane,'saved',return_value={'site_hold':{'reason':'denied'}}),patch.object(b,'run_group',return_value=held) as run:
   out=b.burst(5,5)
  self.assertEqual(out['runs'],1)
  run.assert_called_once()
