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
  with patch.object(b,'run_group',return_value=res) as call:
   result=b.burst(3,5)
  self.assertEqual(call.call_count,1)
  self.assertEqual(result['runs'],1)
