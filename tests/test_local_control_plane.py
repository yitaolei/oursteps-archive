"""Offline tests for the APFS Mac controller bundle; no NAS/site access."""
import _test_config
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
INSTALLER_PATH=ROOT/'scripts/install_mac_launchagents.py'
spec=importlib.util.spec_from_file_location('install_mac_launchagents_local',INSTALLER_PATH)
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


class LocalControlPlaneTests(unittest.TestCase):
    def test_wrapper_local_mode_bypasses_smb_and_cleans_nas_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)
            script=home/'Library/Application Support/OurSteps/control-plane/scripts/fixture.py'
            script.parent.mkdir(parents=True)
            script.write_text(
                "import os,sys\n"
                "print(os.environ.get('OURSTEPS_NAS_HOST'))\n"
                "print(sys.argv[1])\n"
            )
            env=dict(os.environ,HOME=str(home),OURSTEPS_NAS_HOST='must-be-cleared')
            run=subprocess.run(
                ['zsh',str(ROOT/'scripts/mac_launch_project.sh'),
                 'local-clean-nas-env','scripts/fixture.py','hello'],
                capture_output=True,text=True,env=env,timeout=5)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertEqual(run.stdout.splitlines(),['None','hello'])

    def test_sync_control_plane_stages_required_runtime_and_secret_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);source=base/'source';target=base/'target/control-plane'
            source.mkdir()
            for name in ('archive.py','requirements-auth.txt','config.local.json'):
                (source/name).write_text(name)
            for name in ('oursteps','scripts','.deps','.secrets'):
                (source/name).mkdir()
                (source/name/'fixture.txt').write_text(name)
            (source/'.secrets/session.json').write_text('{}')
            with patch.object(installer,'ROOT',source),patch.object(installer,'CONTROL_TARGET',target),contextlib.redirect_stdout(io.StringIO()):
                installer.sync_control_plane()
            self.assertEqual((target/'archive.py').read_text(),'archive.py')
            self.assertTrue((target/'oursteps/fixture.txt').is_file())
            self.assertTrue((target/'.deps/fixture.txt').is_file())
            self.assertEqual((target/'.secrets').stat().st_mode & 0o777,0o700)
            self.assertEqual((target/'.secrets/session.json').stat().st_mode & 0o777,0o600)
            self.assertEqual((target/'config.local.json').stat().st_mode & 0o777,0o600)

    def test_incremental_creates_nested_local_log_directories(self):
        text=(ROOT/'scripts/update_now.py').read_text()
        self.assertIn("auth_log.parent.mkdir(parents=True,exist_ok=True)",text)
        self.assertIn("log.parent.mkdir(parents=True,exist_ok=True)",text)

    def test_browser_handoff_persists_verified_session_locally(self):
        from scripts import browser_fallback
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'.secrets/session.json'
            saved=dict(uid=424242,cookies=[dict(domain='.oursteps.com.au',name='old',value='1')])
            cookies=[dict(domain='.oursteps.com.au',name='new',value='2')]
            ok=Mock(returncode=0,stdout='{}',stderr='')
            with patch.object(browser_fallback,'ROOT',root), \
                 patch.object(browser_fallback,'STATE',state), \
                 patch.object(browser_fallback,'deployment',return_value=('fixture','/fixture')), \
                 patch.object(browser_fallback,'ssh_argv',return_value=['ssh-mock']), \
                 patch.object(browser_fallback,'remote_python',return_value='remote'), \
                 patch.object(browser_fallback,'run_group',return_value=ok):
                browser_fallback.install_candidate(saved,cookies)
            installed=json.loads(state.read_text())
            self.assertEqual(installed['uid'],424242)
            self.assertEqual(installed['cookies'],cookies)
            self.assertGreater(installed['verified_at'],0)
            self.assertTrue((root/'data/auth-status.json').is_file())


if __name__=='__main__':
    unittest.main()
