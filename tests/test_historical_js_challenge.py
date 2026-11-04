import _test_config
import json
import tempfile
import unittest
from pathlib import Path
import contextlib
import io
import runpy
import sys
from unittest.mock import patch

from oursteps.fetch import Fetcher, HistoricalJSChallenge, historical_js_challenge
from oursteps.history import backfill, prepare
from oursteps.cli import run
from oursteps.store import Store
from oursteps.parser import thread_url

BODY = b'<script>' + b'var token="abcdefghij";\n'*100 + b'window.location = "/challenge";</script>'

class ChallengeTests(unittest.TestCase):
    def test_http_response_detected_before_parser(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp)
            self.addCleanup(s.db.close)
            s.historical_mode=True
            class Response:
                code=200
                headers={'Content-Type':'text/html'}
                def __enter__(self): return self
                def __exit__(self,*args): pass
                def read(self,limit): return BODY
            with patch('oursteps.fetch.session',return_value=(None,'anonymous')):
                f=Fetcher(s)
            with patch.object(f,'wait_window'), patch.object(f,'sleep'), patch.object(f,'open_response',return_value=Response()):
                with self.assertRaises(HistoricalJSChallenge): f.request(thread_url(1))

    def test_conservative_shapes(self):
        self.assertTrue(historical_js_challenge(BODY))
        self.assertTrue(historical_js_challenge(b'<script>' + b'var x=1; '*250 + b'var target=window.location;</script>'))
        for body in (b'<html><head><script>location.href="/";</script></head><body>truncated',
                     b'<script>location="/";</script>',
                     b'<script>/* window.location="/"; ' + b'x'*2200 + b' */</script>',
                     b'<script>' + b'var x=1; '*250 + b'/* location ' + b'x'*1100 + b' */</script>',
                     b'<script>' + b'var x=1; '*250 + b'var s="window.location=123";</script>',
                     b'<script>' + b'var x=1; '*250 + b'window.location.href="/";',
                     b'<script>location.href="/";', b'<script>location</script>outside',
                     b'<script>location</script><p>text</p>', b'<script>location</script><script>x</script>',
                     b'<script>relocation</script>', b'<script>location' + b'x'*8192 + b'</script>'):
            with self.subTest(body=body[:100]): self.assertFalse(historical_js_challenge(body))

    def test_batch_hold_restart_and_daily(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp); self.addCleanup(s.db.close); prepare(s)
            s.seed([1,2,3,4,5], 'test')
            with s.db:
                for tid in (1,2,3,4): s.db.execute('INSERT INTO inventory VALUES(?,0,0)',(tid,))
            calls=[]
            class Fetch:
                daily_now=False
                def wait_window(self): pass
                def get(self,url):
                    calls.append(url)
                    raise HistoricalJSChallenge(BODY)
            with patch('oursteps.history.Fetcher',return_value=Fetch()):
                self.assertEqual(backfill(s,now=True,defer_preview=True), 'historical_js_challenge_hold')
            self.assertEqual(len(calls),3)
            evidence=json.loads(s.setting('historical_js_challenge_hold'))['evidence']
            self.assertEqual(len({e['tid'] for e in evidence}),3)
            self.assertEqual(s.db.execute("SELECT count(*) FROM jobs WHERE error='historical_js_challenge' AND state='retry_later'").fetchone()[0],3)
            self.assertFalse(s.setting('halt'))
            perf=json.loads((s.root/'backfill-performance.json').read_text())
            self.assertEqual(perf['worker_state'],'historical_js_challenge_hold')
            with patch('oursteps.history.Fetcher',side_effect=AssertionError('network setup')):
                self.assertEqual(backfill(s,now=True,defer_preview=True),'historical_js_challenge_hold')
            self.assertEqual(len(calls),3)
            class DailyFetch:
                def wait_window(self): pass
                def get(self,url): calls.append(url); raise TimeoutError()
            self.assertEqual(run(s,tids=[5],fetcher_override=DailyFetch()),'retry_later')
            self.assertEqual(len(calls),4)

    def test_evidence_survives_small_batches_and_expires(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp); self.addCleanup(s.db.close); prepare(s)
            s.seed([1,2,3], 'test')
            with s.db:
                for tid in (1,2,3): s.db.execute('INSERT INTO inventory VALUES(?,0,0)',(tid,))
            class Fetch:
                daily_now=False
                def wait_window(self): pass
                def get(self,url): raise HistoricalJSChallenge(BODY)
            with patch('oursteps.history.Fetcher',return_value=Fetch()):
                for _ in range(2):
                    self.assertNotEqual(backfill(s,now=True,defer_preview=True,max_threads=1),'historical_js_challenge_hold')
                self.assertEqual(len(json.loads(s.setting('historical_js_challenge_evidence'))),2)
                self.assertEqual(backfill(s,now=True,defer_preview=True,max_threads=1),'historical_js_challenge_hold')

    def test_old_evidence_does_not_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp); self.addCleanup(s.db.close); prepare(s)
            s.seed([1], 'test')
            with s.db: s.db.execute('INSERT INTO inventory VALUES(1,0,0)')
            s.set_setting('historical_js_challenge_evidence',json.dumps([{'tid':2,'at':1},{'tid':3,'at':1}]))
            class Fetch:
                daily_now=False
                def wait_window(self): pass
                def get(self,url): raise HistoricalJSChallenge(BODY)
            with patch('oursteps.history.Fetcher',return_value=Fetch()):
                self.assertNotEqual(backfill(s,now=True,defer_preview=True,max_threads=1),'historical_js_challenge_hold')
            self.assertEqual(len(json.loads(s.setting('historical_js_challenge_evidence'))),1)

    def test_parsed_article_resets_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp); self.addCleanup(s.db.close)
            s.seed([1902000], 'test'); s.historical_mode=True
            s.set_setting('historical_js_challenge_evidence','[{"tid":1,"at":1}]')
            raw=(Path(__file__).parent/'fixtures/thread-first.html').read_bytes()
            s.snapshot(thread_url(1902000),raw,mode='imported_private')
            run(s,tids=[1902000],fetcher_override=object(),per_thread_page_budget=1)
            self.assertFalse(s.setting('historical_js_challenge_evidence'))

    def test_worker_hold_exit_is_nonzero_only_for_backfill(self):
        root=Path(__file__).resolve().parents[1]
        for hold in ('historical_js_challenge_hold','historical_first_page_site_hold'):
            with self.subTest(hold=hold), tempfile.TemporaryDirectory() as tmp, patch.object(sys,'platform','linux'), \
                 patch.object(sys,'argv',['history_worker.py','backfill','--data',tmp]), \
                 patch('oursteps.history.backfill',return_value=hold), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as stopped:
                    runpy.run_path(str(root/'scripts/history_worker.py'),run_name='__main__')
                self.assertEqual(stopped.exception.code,2)
