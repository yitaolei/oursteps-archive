import _test_config
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from oursteps.deployment import public_access
from scripts.render_public_nginx import ROOT, TOKENS, render, render_text
from scripts.public_release import config_check


class PublicAccessTests(unittest.TestCase):
    def test_load_validation(self):
        self.assertEqual(public_access(), ('recent_reader', 'archive_owner'))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            with patch.dict(os.environ, OURSTEPS_CONFIG=str(path)):
                with self.assertRaises(ValueError): public_access()
                for data in ({}, {'public_access': {}}, {'public_access': []},
                             {'public_access': {'recent_user': 'same', 'full_user': 'same'}},
                             *({'public_access': {'recent_user': value, 'full_user': 'owner'}}
                               for value in ('x.*', 'x$; full;', 'x y', 'x\n', '', None))):
                    path.write_text(json.dumps(data))
                    with self.subTest(data=data), self.assertRaises(ValueError): public_access()
                path.write_text('{')
                with self.assertRaises(ValueError): public_access()

    def test_render_and_contract(self):
        template = (ROOT / 'nginx-public-stable.conf.template').read_text()
        expected = template.replace(TOKENS[0], 'recent_reader').replace(TOKENS[1], 'archive_owner')
        self.assertEqual(render_text(template), expected)
        self.assertNotIn('__OURSTEPS_', expected)
        self.assertIn('~^recent_reader$ recent-1y;', expected)
        self.assertIn('~^archive_owner$ full;', expected)
        self.assertNotIn('~*', expected)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copy2(ROOT / 'compose.public.yaml', root / 'compose.public.yaml')
            output = render(output=root / 'nginx-public-stable.conf')
            self.assertEqual(output.read_text(), expected)
            config_check(root)
            output.write_text(expected.replace('control-center\\.json', 'control-center-blocked.json'))
            with self.assertRaises(ValueError): config_check(root)
            output.write_text(expected.replace('~^recent_reader$', '~*^recent_reader$'))
            with self.assertRaises(ValueError): config_check(root)

    def test_invalid_template_leaves_output_unchanged(self):
        template = (ROOT / 'nginx-public-stable.conf.template').read_text()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root/'template'; output = root/'output'
            output.write_text('unchanged')
            for bad in (template.replace(TOKENS[0], ''), template + TOKENS[1], template + '__UNKNOWN__'):
                source.write_text(bad)
                with self.assertRaises(ValueError): render(output=output, template=source)
                self.assertEqual(output.read_text(), 'unchanged')
