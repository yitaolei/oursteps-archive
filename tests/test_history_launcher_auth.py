"""Offline launcher auth timeout/wiring tests; no SSH, browser or site access."""
import _test_config
import contextlib
import io
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


class HistoryLauncherAuthTests(unittest.TestCase):
    def run_launcher(self,auth_result=None,auth_error=None):
        calls=[]
        def fake_run_group(args,**kwargs):
            calls.append((args,kwargs))
            if auth_error is not None:
                raise auth_error
            return auth_result or subprocess.CompletedProcess(args,0,'auth ok\n','')
        def fake_call(args,**kwargs):
            calls.append((args,kwargs))
            return 2  # Stop after auth; never enter browser/publish path.
        with patch.object(sys,'argv',['history_launcher.py','backfill']), \
             patch('pathlib.Path.exists',return_value=True), \
             patch('scripts.process_utils.run_group',side_effect=fake_run_group), \
             patch('subprocess.call',side_effect=fake_call), \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as stopped:
                runpy.run_path(str(ROOT/'scripts/history_launcher.py'),run_name='__main__')
        return stopped.exception.code,calls

    def test_auth_uses_bounded_process_group(self):
        code,calls=self.run_launcher()
        self.assertEqual(code,2)
        args,kwargs=calls[0]
        self.assertTrue(str(args[0]).endswith('/.local/share/oursteps-runtime/bin/python'))
        self.assertEqual(args[-1],'auth')
        self.assertEqual(kwargs['timeout'],180)
        self.assertIs(kwargs['stdin'],subprocess.DEVNULL)
        self.assertTrue(kwargs['text'])

    def test_auth_timeout_exits_75_before_remote_worker(self):
        code,calls=self.run_launcher(
            auth_error=subprocess.TimeoutExpired(['fixture'],180))
        self.assertEqual(code,75)
        self.assertEqual(len(calls),1)


if __name__=='__main__':
    unittest.main()
