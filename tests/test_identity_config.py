import _test_config  # Configure synthetic identity before application imports.
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.deps')]
from oursteps import config


class IdentityConfigTests(unittest.TestCase):
    def test_default_and_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = {'uid': 12345, 'username': 'example_user', 'display_name': 'Example'}
            second = dict(first, uid=67890)
            (root / 'config.local.json').write_text(json.dumps(first))
            override = root / 'other.json'
            override.write_text(json.dumps(second))
            with patch.object(config, 'PROJECT_ROOT', root), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(config.load_identity(), (12345, 'example_user', 'Example'))
                with patch.dict(os.environ, OURSTEPS_CONFIG=str(override)):
                    self.assertEqual(config.load_identity()[0], 67890)

    def test_missing_invalid_and_no_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'identity.json'
            with patch.dict(os.environ, OURSTEPS_CONFIG=str(path)):
                with self.assertRaises(config.ConfigError):
                    config.load_identity()
                for data in ('{', '[]', '{}', '{"uid":true,"username":"x","display_name":"x"}',
                             '{"uid":0,"username":"x","display_name":"x"}',
                             '{"uid":12345,"username":"","display_name":"x"}'):
                    path.write_text(data)
                    with self.subTest(data=data), self.assertRaises(config.ConfigError):
                        config.load_identity()
            with patch.dict(os.environ, OURSTEPS_CONFIG=''):
                with self.assertRaises(config.ConfigError):
                    config.load_identity()

    def test_alternate_identity_and_schema(self):
        # Isolated process loads an unrelated owner; SQLite is memory-only.
        script = r'''
import re, sqlite3
from pathlib import Path
from oursteps.config import UID, USERNAME, DISPLAY_NAME
from oursteps.parser import parse_thread, thread_url, directory_url, soup_of, ParseError
from oursteps.auth_verify import identity
from oursteps.preview import document
from oursteps.store import SCHEMA, verify_owner_schema
assert UID == 67890
assert 'uid=67890' in directory_url()
raw = Path('tests/fixtures/thread-first.html').read_text()
raw = re.sub(r'uid=\d+', 'uid=67890', raw)
p = parse_thread(raw, thread_url(1902000))
assert p['owner_uid'] == UID and p['posts']
assert all(post['author_uid'] == UID for post in p['posts'])
try:
    parse_thread(raw.replace('uid=67890', 'uid=12345'), thread_url(1902000))
except ParseError as e:
    assert str(e) == 'thread_not_owned_by_configured_owner'
else:
    raise AssertionError('wrong owner accepted')
assert identity(soup_of('<div id="um"><a href="home.php?uid=67890">Example_User</a></div>'))
assert not identity(soup_of('<div id="um"><a href="home.php?uid=12345">Example_User</a></div>'))
assert 'Example &lt;Author&gt;' in document('test', '')
with sqlite3.connect(':memory:') as db:
    db.executescript(SCHEMA)
    verify_owner_schema(db)
    schema = db.execute("SELECT sql FROM sqlite_master WHERE name='posts'").fetchone()[0]
    db.execute('INSERT INTO posts(pid,tid,author_uid) VALUES(1,1,?)', (UID,))
    try:
        db.execute('INSERT INTO posts(pid,tid,author_uid) VALUES(2,1,12345)')
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError('wrong owner inserted')
    verify_owner_schema(db)
    assert db.execute("SELECT sql FROM sqlite_master WHERE name='posts'").fetchone()[0] == schema
with sqlite3.connect(':memory:') as db:
    db.execute('CREATE TABLE posts(author_uid INTEGER CHECK(author_uid=12345))')
    before = db.execute("SELECT sql FROM sqlite_master WHERE name='posts'").fetchone()[0]
    try:
        verify_owner_schema(db)
    except ValueError as e:
        assert 'database_owner_config_mismatch' in str(e)
    else:
        raise AssertionError('incompatible schema accepted')
    assert db.execute("SELECT sql FROM sqlite_master WHERE name='posts'").fetchone()[0] == before
'''
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'identity.json'
            path.write_text(json.dumps(dict(uid=67890, username='example_user', display_name='Example <Author>')))
            env = dict(os.environ, OURSTEPS_CONFIG=str(path), PYTHONDONTWRITEBYTECODE='1',
                       PYTHONPATH=os.pathsep.join((str(ROOT), str(ROOT / '.deps'))))
            result = subprocess.run([sys.executable, '-B', '-c', script], cwd=ROOT,
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
