import _test_config  # Configure synthetic identity before application imports.
import json
import os
from pathlib import Path
import runpy
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from oursteps.deployment import deployment, remote_python


class DeploymentTests(unittest.TestCase):
    def test_config_aliases_precedence_and_quoting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            remote = "/srv/archive with 'quotes'; echo bad"
            path.write_text(json.dumps({'deployment': {'nas_host': 'owner@nas.example', 'nas_project': remote}}))
            with patch.dict(os.environ, {'OURSTEPS_CONFIG': str(path)}, clear=True):
                self.assertEqual(deployment(), ('owner@nas.example', remote))
                self.assertEqual(remote_python('scripts/test.py', 'a b'),
                                 'cd ' + shlex.quote(remote) + " && python3 scripts/test.py 'a b'")
                with patch.dict(os.environ, OURSTEPS_NAS='alias', OURSTEPS_NAS_ROOT='/alias'):
                    self.assertEqual(deployment(), ('alias', '/alias'))
                    with patch.dict(os.environ, OURSTEPS_NAS_HOST='primary', OURSTEPS_NAS_PROJECT='/primary'):
                        self.assertEqual(deployment(), ('primary', '/primary'))

    def test_missing_invalid_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'missing.json'
            with patch.dict(os.environ, {'OURSTEPS_CONFIG': str(path)}, clear=True):
                with self.assertRaisesRegex(ValueError, 'deployment config'):
                    deployment()
                for content in ('{', '{}', '{"deployment":[]}'):
                    path.write_text(content)
                    with self.assertRaises(ValueError):
                        deployment()
            for host, root in (('-oProxyCommand=bad', '/safe'), ('user@host', 'relative'), ('', '/safe')):
                with patch.dict(os.environ, {'OURSTEPS_NAS': host, 'OURSTEPS_NAS_ROOT': root}, clear=True):
                    with self.assertRaises(ValueError):
                        deployment()

    def test_portable_install_and_rpc_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, OURSTEPS_MAC_BIN=tmp, OURSTEPS_NAS_HOST='owner@nas.example',
                       OURSTEPS_NAS_PROJECT='/srv/archive with spaces', PYTHONDONTWRITEBYTECODE='1')
            result = subprocess.run(['sh', str(ROOT / 'scripts/mac-tools/install_mac_tools.sh')],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            private = Path(tmp) / 'oursteps-config.local.json'
            self.assertEqual(private.stat().st_mode & 0o777, 0o600)
            self.assertEqual(set(json.loads(private.read_text())), {'deployment'})
            with patch.dict(os.environ, {}, clear=True):
                module = runpy.run_path(str(Path(tmp) / 'check-oursteps'))
                with patch.object(sys, 'argv', ['check-oursteps']), patch('subprocess.run') as run:
                    run.return_value.returncode = 0
                    self.assertEqual(module['main'](), 0)
                    args = run.call_args.args[0]
                    self.assertEqual(args[1], 'owner@nas.example')
                    self.assertEqual(args[2], "cd '/srv/archive with spaces' && python3 scripts/manual_missing.py")

    def test_generic_security_markers_and_personal_literal_removal(self):
        import ast
        for name, variable in [('scripts/public_release.py', 'MARKERS'), ('scripts/export_public.py', 'PRIVATE_MARKERS')]:
            tree = ast.parse((ROOT / name).read_text())
            assignment = next(n for n in tree.body if isinstance(n, ast.Assign)
                              and any(isinstance(t, ast.Name) and t.id == variable for t in n.targets))
            markers = ast.literal_eval(assignment.value)
            for value in (b'/volume2/private/raw', b'/Volumes/Private/raw', b'.secrets/session.json'):
                self.assertTrue(any(marker in value for marker in markers))
