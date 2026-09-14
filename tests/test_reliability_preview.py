import _test_config  # Configure synthetic identity before application imports.
import http.cookiejar
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.deps'))
from oursteps.store import Store
from oursteps.fetch import Fetcher,RateLimited,NotFound,retry_after,session
from oursteps.parser import Blocked,parse_thread,thread_url
from oursteps.cli import run
from oursteps.preview import build,validate,body_html
FIX=Path(__file__).parent/'fixtures'

class Checks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.s=Store(Path(self.tmp.name)/'data')
        self.s.seed([1902000],'test')
    def tearDown(self):
        self.s.db.close();self.tmp.cleanup()
    def job(self): return self.s.db.execute('SELECT * FROM jobs LIMIT 1').fetchone()
    def test_retry_survives_restart_and_pause(self):
        with patch.object(Fetcher,'wait_window'),patch.object(Fetcher,'get',side_effect=RateLimited(1800)):
            self.assertEqual(run(self.s),'retry_later')
        self.assertEqual(self.job()['state'],'retry_later')
        self.assertGreater(self.job()['retry_at'],time.time()+1700)
        self.s.db.close();self.s=Store(Path(self.tmp.name)/'data')
        with patch.object(Fetcher,'get') as get:
            self.assertEqual(run(self.s),'retry_pending');get.assert_not_called()
    def test_auth_halt(self):
        with patch.object(Fetcher,'wait_window'),patch.object(Fetcher,'get',side_effect=Blocked('session_identity_unverified_or_expired')):
            self.assertEqual(run(self.s),'halted')
        self.assertEqual(self.job()['state'],'auth_required')
        with patch.object(Fetcher,'get') as get:
            self.assertEqual(run(self.s),'halted');get.assert_not_called()
    def test_not_found(self):
        with patch.object(Fetcher,'wait_window'),patch.object(Fetcher,'get',side_effect=NotFound('http_404')):
            run(self.s)
        self.assertEqual(self.job()['state'],'not_found')
        self.assertFalse(self.s.setting('halt'))
    def test_unlimited_persistent_retry(self):
        self.s.db.execute('UPDATE jobs SET attempts=25');self.s.db.commit()
        self.s.fail(self.job(),'transient:TimeoutError',retry=True)
        self.assertEqual(self.job()['state'],'retry_later')
    def test_retry_after(self):
        self.assertEqual(retry_after('150'),150)
        self.assertEqual(retry_after('invalid'),300)
        self.assertEqual(retry_after('Wed, 01 Jan 2020 00:00:00 GMT'),0)
    def test_explicit_cookie_transport_and_permissions(self):
        path=Path(self.tmp.name)/'session.txt'
        path.write_text('# Netscape HTTP Cookie File\n.oursteps.com.au\tTRUE\t/bbs\tTRUE\t2147483647\tprefix_auth\ttest-secret\n')
        path.chmod(0o600)
        with patch.dict(os.environ,{'OURSTEPS_COOKIE_FILE':str(path)}):
            jar,mode=session(self.s)
            self.assertEqual(mode,'authenticated_private')
            import urllib.request
            req=urllib.request.Request(thread_url(1902000));jar.add_cookie_header(req)
            self.assertIsNotNone(req.get_header('Cookie'))
            req=urllib.request.Request('https://example.com/');jar.add_cookie_header(req)
            self.assertIsNone(req.get_header('Cookie'))
            path.chmod(0o644)
            with self.assertRaises(Blocked): session(self.s)
    def test_preview_and_local_validation(self):
        for page,name in [(1,'thread-first.html'),(2,'thread-second.html')]:
            url=thread_url(1902000,page);raw=(FIX/name).read_bytes()
            snap=self.s.snapshot(url,raw);self.s.save_page(url,parse_thread(raw,url),snap)
        self.assertEqual(build(self.s)['articles'],1)
        self.assertTrue(validate(self.s)['ok'])
        text=(self.s.root/'preview'/'1902000.html').read_text()
        self.assertIn('post-103',text);self.assertIn('post-104',text)
        self.assertNotIn('<img',text)
        (self.s.root/'preview'/'1902000.html').write_text('broken')
        self.assertFalse(validate(self.s)['ok'])
    def test_sanitizer(self):
        text=body_html('<script>alert(1)</script><img src="https://example.com/pixel"><a href="javascript:bad()" onclick="bad()">x</a>')
        for bad in ['<script','<img','javascript:','onclick']: self.assertNotIn(bad,text)
    def test_http_classification_and_session_expiry(self):
        import io
        from email.message import Message
        class Response(io.BytesIO):
            def __init__(self,code,body=b'<html>OK</html>'):
                super().__init__(body);self.code=code;self.headers=Message()
                self.headers['Content-Type']='text/html';self.headers['Retry-After']='600'
        fetch=Fetcher(self.s)
        for code,error in [(429,RateLimited),(503,__import__('oursteps.parser',fromlist=['Busy']).Busy),(403,Blocked),(404,NotFound)]:
            with patch.object(fetch,'wait_window'),patch('oursteps.fetch.time.sleep'),patch.object(fetch.opener,'open',return_value=Response(code)):
                with self.assertRaises(error): fetch.request(thread_url(1902000))
        fetch.mode='authenticated_private'
        with patch.object(fetch,'wait_window'),patch('oursteps.fetch.time.sleep'),patch.object(fetch.opener,'open',return_value=Response(200)):
            from oursteps.auth_verify import VerificationIssue
            with self.assertRaises(VerificationIssue) as caught: fetch.request(thread_url(1902000))
            self.assertEqual(caught.exception.category,'unexpected_layout')
        with patch.object(fetch,'wait_window'),patch('oursteps.fetch.time.sleep'),patch.object(fetch.opener,'open',return_value=Response(200,'<html>请先登录</html>'.encode())):
            with self.assertRaises(VerificationIssue) as caught: fetch.request(thread_url(1902000))
            self.assertEqual(caught.exception.category,'auth_required')
