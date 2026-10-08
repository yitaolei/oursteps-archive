"""Launcher wiring via mocked subprocesses; never execute SSH/browser/publish."""
import _test_config
import contextlib
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


class LauncherStreamTests(unittest.TestCase):
    def launch(self,candidate_code=0,candidate_output=None,fallback_code=0,flush_code=0,
               runtime_available=True,worker_code=0,mode='backfill',fallback_error=None):
        events=[];runtime_checks=[]
        urls=['https://www.oursteps.com.au/bbs/forum.php?mod=viewthread&tid=%s&page=1'%tid for tid in range(1,81)]
        def exists(path):
            runtime_checks.append(str(path))
            # Auth runtime exists initially; it may be unavailable after worker.
            return True if len(runtime_checks)==1 else runtime_available
        def auth_run_group(argv,**kwargs):
            events.append(('auth',argv))
            return subprocess.CompletedProcess(argv,0,stdout='',stderr='')
        def run(argv,**kwargs):
            if 'browser_fallback_candidates.py' in argv[-1]:
                events.append(('candidates',argv))
                return subprocess.CompletedProcess(argv,candidate_code,stdout=json.dumps({'urls':urls}) if candidate_output is None else candidate_output)
            if any(arg.endswith('/browser_fallback.py') for arg in argv):
                events.append(('browser',argv))
                if fallback_error:raise fallback_error
                return subprocess.CompletedProcess(argv,fallback_code)
            raise AssertionError('Unexpected subprocess: '+repr(argv))
        def call(argv,**kwargs):
            if 'history_worker.py' in argv[-1]:
                events.append(('worker',argv));return worker_code
            if '--historical-pending' in argv[-1]:
                events.append(('flush',argv));return flush_code
            if 'publish_public.py' in argv[-1]:
                events.append(('publish',argv));return 0
            raise AssertionError('Unexpected subprocess: '+repr(argv))
        with patch.object(sys,'argv',['history_launcher.py',mode]),patch('pathlib.Path.exists',exists),patch('scripts.process_utils.run_group',side_effect=auth_run_group),patch('subprocess.run',side_effect=run),patch('subprocess.call',side_effect=call),patch('oursteps.deployment.ssh_argv',return_value=['ssh-mock']),contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as stopped:
                runpy.run_path(str(ROOT/'scripts/history_launcher.py'),run_name='__main__')
        return stopped.exception.code,events

    def test_single_query_80_single_session_then_flush_then_publish(self):
        code,events=self.launch()
        self.assertEqual(code,0)
        self.assertEqual([e[0] for e in events],['auth','worker','candidates','browser','flush','publish'])
        self.assertIn('--defer-preview',events[1][1][-1])
        self.assertIn('--historical --limit 80',events[2][1][-1])
        browser=events[3][1]
        self.assertIn('--historical',browser)
        self.assertEqual(len(browser[browser.index('--historical')+1:]),80)
        self.assertIn('archive.py build-preview --historical-pending',events[4][1][-1])

    def test_candidate_failure_malformed_or_runtime_missing_still_flushes(self):
        for kwargs in (dict(candidate_code=1),dict(candidate_output=''),dict(candidate_output='null'),
                       dict(candidate_output='{}'),dict(candidate_output='{"urls":null}'),dict(runtime_available=False)):
            with self.subTest(kwargs=kwargs):
                code,events=self.launch(**kwargs)
                self.assertEqual(code,0)
                self.assertEqual([e[0] for e in events],['auth','worker','candidates','flush','publish'])

    def test_browser_stop_handoff_error_or_timeout_still_flushes(self):
        for kwargs in (dict(fallback_code=2),dict(fallback_error=subprocess.TimeoutExpired('fixture',1)),dict(fallback_error=OSError('unavailable'))):
            with self.subTest(kwargs=kwargs):
                code,events=self.launch(**kwargs)
                self.assertEqual(code,0)
                self.assertEqual([e[0] for e in events][-2:],['flush','publish'])

    def test_flush_failure_blocks_publish(self):
        code,events=self.launch(flush_code=2)
        self.assertEqual(code,2)
        self.assertEqual(events[-1][0],'flush')
        self.assertNotIn('publish',[e[0] for e in events])

    def test_worker_failure_blocks_browser_flush_publish(self):
        code,events=self.launch(worker_code=2)
        self.assertEqual(code,2)
        self.assertEqual([e[0] for e in events],['auth','worker'])

    def test_other_modes_do_not_defer_preview(self):
        code,events=self.launch(mode='guide-discover')
        self.assertEqual(code,0)
        self.assertEqual([e[0] for e in events],['auth','worker'])
        self.assertNotIn('--defer-preview',events[-1][1][-1])


if __name__=='__main__':unittest.main()
