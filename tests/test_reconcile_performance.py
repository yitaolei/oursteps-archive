import _test_config  # Configure synthetic identity before application imports.
import http.cookiejar
import sys
import time
from pathlib import Path
from unittest.mock import patch, Mock
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_reconcile as fixtures
ROOT=fixtures.ROOT
from oursteps import reconcile as r
from oursteps.parser import query
from oursteps.performance import Performance
from oursteps.fetch import Fetcher


class ResumePerformanceTests(unittest.TestCase):
    setUp=fixtures.ReconcileTests.setUp
    tearDown=fixtures.ReconcileTests.tearDown
    add=fixtures.ReconcileTests.add
    # Reuse fixture setup only; avoid inheriting/rerunning unrelated tests.
    def transport(self, limit):
        with patch('oursteps.fetch.session',return_value=(http.cookiejar.CookieJar(),'anonymous')):
            t=r.LimitedTransport(self.cache,time.time()+1000,limit)
        t.robot=object();t.rules=['User-agent: *','Allow: /'];t.delay=0
        def open_response(request, **kw):
            page=int(query(request.full_url).get('page',1));self.network.append(page)
            if limit==1 and getattr(self,'time_budget',False):t.deadline=0
            raw=(ROOT/'tests/fixtures'/('thread-first.html' if page==1 else 'thread-second.html')).read_bytes().replace(b'</body>',b'<div id="ft">GMT +10</div></body>')
            response=Mock(code=200,headers={'Content-Type':'text/html'})
            response.read.return_value=raw;response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
            return response
        t.opener.open=Mock(side_effect=open_response)
        return t
    def test_real_transport_budget_resume_never_refetches_success(self):
        self.add();self.network=[];first=self.transport(1)
        with patch('oursteps.fetch.time.sleep'):
            with self.assertRaises(r.BudgetReached):r.recover_one(self.state,self.cache,first,1902000)
            second=self.transport(10)
            self.assertEqual(r.recover_one(self.state,self.cache,second,1902000),'recovered')
        self.assertEqual(self.network,[1,2])
        self.assertEqual(first.exhausted_reason,'time_budget' if getattr(self,'time_budget',False) else 'logical_request_budget')
        a,b=first.performance_threads[0],second.performance_threads[0]
        self.assertEqual(a['network_fetches'],1);self.assertEqual(b['network_fetches'],1)
        self.assertEqual(b['successful_pages_at_start'],1);self.assertGreaterEqual(b['cached_page_uses'],1)
        self.assertEqual(b['successful_page_refetches'],0);self.assertEqual(b['parsed_pages_after'],2)
        self.assertGreater(b['parse_seconds'],0);self.assertGreater(b['persistence_seconds'],0)
        self.assertGreater(b['import_seconds'],0)
        self.assertNotIn('http',str(b))
    def test_time_budget_resume(self):
        self.time_budget=True
        self.test_real_transport_budget_resume_never_refetches_success()

    def test_timeout_and_throttle_measurement_preserves_exception(self):
        self.network=[];t=self.transport(10);self.cache.performance=Performance()
        t.opener.open=Mock(side_effect=TimeoutError('fixture'))
        from urllib.request import Request
        with self.assertRaises(TimeoutError):t.open_response(Request(r.thread_url(1902000)))
        report=self.cache.performance.report()
        self.assertEqual(report['network_timeouts'],1);self.assertEqual(report['network_fetches'],1)
        with patch('oursteps.fetch.time.sleep') as sleep:
            t.sleep(12);sleep.assert_called_once_with(12)
        self.assertIn('throttle_seconds',self.cache.performance.values)
