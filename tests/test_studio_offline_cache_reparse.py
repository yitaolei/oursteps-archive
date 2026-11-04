"""Synthetic offline recovery safety tests."""
import _test_config
import tempfile
import sqlite3
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from oursteps.parser import ParseError, parse_thread, thread_url
from oursteps.store import Store
from scripts.studio_offline_cache_reparse import identity, run

FIX = Path(__file__).parent / 'fixtures'
FIRST = (FIX / 'thread-first.html').read_bytes()
SECOND = (FIX / 'thread-second.html').read_bytes().replace(b' 2 / 2', b'<strong>2</strong> 2 / 2')
URL = thread_url(1902000, 2)
DELETED = '<div class="pi"><em>该用户已被删除</em></div>'.encode()
AUTHOR = b'<div class="authi"><a href="home.php?mod=space&uid=424242">archive_test_user</a></div>'


class SourceIdentityTests(unittest.TestCase):
    def test_empty_extra_and_rejected_parameters(self):
        url = 'https://www.oursteps.com.au/bbs/forum.php?mod=viewthread&tid=843944&page=17'
        self.assertEqual(identity(url), (843944, 17))
        self.assertEqual(identity(url + '&extra='), (843944, 17))
        for suffix in ('&extra=page%3D9', '&extra=&extra=', '&authorid=1', '&other='):
            with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, 'invalid_source_url'):
                identity(url + suffix)


def deleted_post(pid):
    return (b'<div id="post_' + str(pid).encode() + b'"><div class="pls">' + DELETED +
            b'</div><div class="plc"><div id="postmessage_' + str(pid).encode() +
            b'">secret <blockquote><a href="home.php?uid=424242">spoof</a></blockquote></div></div></div>')


class DeletedParserTests(unittest.TestCase):
    def test_multiple_deleted_and_owner(self):
        p = parse_thread(SECOND + b''.join(deleted_post(pid) for pid in range(105, 109)), URL)
        self.assertEqual(p['all_pids'], [104, 105, 106, 107, 108])
        self.assertEqual([x['pid'] for x in p['posts']], [104])
        self.assertEqual(p['gaps'], [{'pid': x, 'reason': 'deleted_author_identity_unknown'} for x in range(105, 109)])

    def test_page_one_and_spoof_and_invalid_uid_fail(self):
        with self.assertRaisesRegex(ParseError, 'missing_author_identity'):
            parse_thread(FIRST.replace(AUTHOR, DELETED), thread_url(1902000))
        with self.assertRaisesRegex(ParseError, 'missing_author_identity'):
            parse_thread(SECOND.replace(AUTHOR, b'<div class="authi">unknown</div>') + deleted_post(105).replace(DELETED, b'<div class="authi">unknown</div>'), URL)
        for spoof in (b'<div class="authi"><em>' + '该用户已被删除'.encode() + b'</em></div>',
                      b'<div class="pi">' + '该用户已被删除'.encode() + b'</div>',
                      b'<div class="pi"><em>' + '该用户已被删除 plus'.encode() + b'</em></div>',
                      b'<div class="pi">unknown</div></div><div class="plc"><blockquote>' + DELETED + b'</blockquote>'):
            with self.subTest(spoof=spoof), self.assertRaisesRegex(ParseError, 'missing_author_identity:105'):
                parse_thread(SECOND + deleted_post(105).replace(DELETED, spoof), URL)
        with self.assertRaisesRegex(ParseError, 'invalid_author_uid'):
            parse_thread(SECOND.replace(b'uid=424242', b'uid=invalid'), URL)
        with self.assertRaisesRegex(ParseError, 'duplicate_pid'):
            parse_thread(SECOND + deleted_post(105) + deleted_post(105), URL)


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.backup = self.root / 'backup'
        self.backup.mkdir(mode=0o700)
        self.data.mkdir()
        (self.data / 'worker.lock').touch()
        (self.data / 'studio-primary-cycle.lock').touch()
        s = Store(self.data)
        s.seed([1902000], 'fixture')
        snap = s.snapshot(thread_url(1902000), FIRST, mode='fixture')
        s.save_page(thread_url(1902000), parse_thread(FIRST, thread_url(1902000)), snap)
        self.source = 'https://www.oursteps.com.au/bbs/forum.php?page=2&tid=1902000&mod=viewthread'
        s.snapshot(self.source, SECOND.replace(b'</body></html>', deleted_post(105) + b'</body></html>'), mode='fixture')
        s.db.execute("UPDATE jobs SET state='parse_error',error='missing_author_identity:105' WHERE url=?", (URL,))
        s.db.commit()
        s.db.close()
        self.authority = patch('scripts.studio_offline_cache_reparse.allow_local_authority', side_effect=lambda p: Path(p).resolve())
        self.authority.start()
        self.candidates = patch('scripts.studio_offline_cache_reparse.CANDIDATES', {(1902000, 2)})
        self.candidates.start()

    def tearDown(self):
        self.candidates.stop()
        self.authority.stop()
        self.temp.cleanup()

    def test_dry_run_then_apply_preserves_snapshot_and_gap_blocks_complete(self):
        items = run(self.data, [(1902000, 2)])
        self.assertEqual(items[0]['source_url'], self.source)
        s = Store(self.data)
        self.assertEqual(s.db.execute('SELECT state FROM jobs WHERE url=?', (URL,)).fetchone()[0], 'parse_error')
        before = [tuple(x) for x in s.db.execute('SELECT * FROM snapshots')]
        s.db.close()
        run(self.data, [(1902000, 2)], True, self.backup)
        self.assertTrue((self.backup / 'archive-before-offline-reparse.sqlite3').is_file())
        s = Store(self.data)
        self.assertEqual([tuple(x) for x in s.db.execute('SELECT * FROM snapshots')], before)
        self.assertEqual(s.db.execute('SELECT url FROM pages WHERE page=2').fetchone()[0], URL)
        self.assertEqual(s.db.execute('SELECT reason FROM gaps WHERE pid=105').fetchone()[0], 'deleted_author_identity_unknown')
        self.assertEqual(s.db.execute('SELECT status FROM threads').fetchone()[0], 'partial')
        self.assertIsNone(s.db.execute('SELECT * FROM posts WHERE pid=105').fetchone())
        s.db.close()

    def test_read_only_preview_missing_lock_and_no_side_effects(self):
        lock = self.data / 'worker.lock'
        lock.unlink()
        before = {str(p.relative_to(self.data)): p.read_bytes() for p in self.data.rglob('*') if p.is_file()}
        with self.assertRaises(FileNotFoundError):
            run(self.data, [(1902000, 2)])
        self.assertEqual(before, {str(p.relative_to(self.data)): p.read_bytes() for p in self.data.rglob('*') if p.is_file()})
        lock.touch()
        before = {str(p.relative_to(self.data)): p.read_bytes() for p in self.data.rglob('*') if p.is_file()}
        run(self.data, [(1902000, 2)])
        self.assertEqual(before, {str(p.relative_to(self.data)): p.read_bytes() for p in self.data.rglob('*') if p.is_file()})

    def test_bad_markers(self):
        import hashlib
        for marker in (b'', b'<strong>3</strong>', b'<strong>2</strong><strong>3</strong>', b'<strong>2</strong><strong>bad</strong>'):
            raw = SECOND.replace(b'<strong>2</strong>', marker) + deleted_post(105)
            # Keep a complete document for the independent completeness check.
            raw = raw.replace(b'</body></html>', b'') + b'</body></html>'
            digest = hashlib.sha256(raw).hexdigest()
            path = self.data / 'raw' / (digest + '.html')
            path.write_bytes(raw)
            db = sqlite3.connect(self.data / 'archive.sqlite3')
            db.execute('UPDATE snapshots SET path=?,stored_sha256=? WHERE url=?', (str(path.relative_to(self.data)), digest, self.source))
            db.commit(); db.close()
            with self.subTest(marker=marker), self.assertRaisesRegex(ValueError, 'page_indicators'):
                run(self.data, [(1902000, 2)])

    def test_existing_page_pid_and_global_owner_conflicts(self):
        db = sqlite3.connect(self.data / 'archive.sqlite3')
        db.execute("UPDATE pages SET all_pids=? WHERE tid=1902000 AND page=1", (json.dumps([104]),))
        db.commit(); db.close()
        with self.assertRaisesRegex(ValueError, 'existing_cross_page_pid_conflict'):
            run(self.data, [(1902000, 2)])
        db = sqlite3.connect(self.data / 'archive.sqlite3')
        db.execute("UPDATE pages SET all_pids='[101]' WHERE tid=1902000 AND page=1")
        db.execute("INSERT INTO threads(tid,discovered_via) VALUES(9,'fixture')")
        db.execute("INSERT INTO posts(pid,tid,author_uid,page) VALUES(104,9,424242,1)")
        db.commit(); db.close()
        with self.assertRaisesRegex(ValueError, 'owner_pid_conflict'):
            run(self.data, [(1902000, 2)])

    def test_duplicate_pid_across_planned_pages(self):
        s = Store(self.data)
        page3 = SECOND.replace(b'<strong>2</strong>', b'<strong>3</strong>')
        s.snapshot(thread_url(1902000, 3), page3, mode='fixture')
        s.db.execute("INSERT INTO jobs(url,kind,tid,page,state,error) VALUES(?, 'thread', 1902000, 3, 'parse_error', 'missing_author_identity:1')", (thread_url(1902000, 3),))
        s.db.commit(); s.db.close()
        with patch('scripts.studio_offline_cache_reparse.CANDIDATES', {(1902000, 2), (1902000, 3)}), self.assertRaisesRegex(ValueError, 'planned_cross_page_pid_conflict'):
            run(self.data, [(1902000, 2), (1902000, 3)])

    def test_unlisted_and_invalid_requests_refused(self):
        for apply in (False, True):
            with self.subTest(apply=apply), self.assertRaisesRegex(ValueError, 'page_not_allowlisted'):
                run(self.data, [(1902000, 3)], apply, self.backup)
        for requested in ([], [(1902000, 2)] * 2, [(1902000, 2)] * 21):
            with self.subTest(requested=requested), self.assertRaisesRegex(ValueError, 'unique pages'):
                run(self.data, requested, True, self.backup)

    def test_hold_remains_after_offline_apply(self):
        db = sqlite3.connect(self.data / 'archive.sqlite3')
        db.execute("INSERT INTO settings VALUES('historical_js_challenge_hold','active')")
        db.commit(); db.close()
        run(self.data, [(1902000, 2)], True, self.backup)
        db = sqlite3.connect(self.data / 'archive.sqlite3')
        self.assertEqual(db.execute("SELECT value FROM settings WHERE key='historical_js_challenge_hold'").fetchone()[0], 'active')
        db.close()

    def test_external_transaction_requires_begin_and_rolls_back(self):
        s = Store(self.data)
        snap = s.latest(self.source)
        parsed = parse_thread(SECOND, URL)
        with self.assertRaisesRegex(ValueError, 'requires_active_transaction'):
            s.save_page(URL, parsed, snap, external_transaction=True)
        s.db.execute('BEGIN IMMEDIATE')
        s.save_page(URL, parsed, snap, external_transaction=True)
        self.assertTrue(s.db.in_transaction)
        s.db.rollback()
        self.assertEqual(s.db.execute('SELECT state FROM jobs WHERE url=?', (URL,)).fetchone()[0], 'parse_error')
        s.db.close()

    def make_six_candidates(self):
        db = sqlite3.connect(self.data / 'archive.sqlite3')
        # Six independent threads use the same synthetic content with unique IDs.
        db.close()
        s = Store(self.data)
        for tid in range(1902001, 1902006):
            first = FIRST.replace(b'1902000', str(tid).encode()).replace(b'101', str(tid * 10 + 1).encode())
            second = SECOND.replace(b'1902000', str(tid).encode()).replace(b'104', str(tid * 10 + 4).encode())
            s.seed([tid], 'fixture')
            a = s.snapshot(thread_url(tid), first, mode='fixture')
            s.save_page(thread_url(tid), parse_thread(first, thread_url(tid)), a)
            s.snapshot(thread_url(tid, 2), second, mode='fixture')
            s.db.execute("UPDATE jobs SET state='parse_error',error='missing_author_identity:1' WHERE url=?", (thread_url(tid, 2),))
        s.db.commit(); s.db.close()
        return [(tid, 2) for tid in range(1902000, 1902006)]

    def test_five_page_subset_applies(self):
        candidates = self.make_six_candidates()
        with patch('scripts.studio_offline_cache_reparse.CANDIDATES', set(candidates)):
            items = run(self.data, candidates[:5], True, self.backup)
        self.assertEqual([(x['tid'], x['page']) for x in items], candidates[:5])
        with sqlite3.connect(self.data / 'archive.sqlite3') as db:
            self.assertEqual(db.execute("SELECT count(*) FROM jobs WHERE page=2 AND state='parse_error'").fetchone()[0], 1)

    def test_allowed_page_collision_blocks_whole_apply(self):
        candidates = self.make_six_candidates()
        with sqlite3.connect(self.data / 'archive.sqlite3') as db:
            db.execute("UPDATE pages SET all_pids=? WHERE tid=? AND page=1", (json.dumps([1902005 * 10 + 4]), 1902005))
        with patch('scripts.studio_offline_cache_reparse.CANDIDATES', set(candidates)), self.assertRaisesRegex(ValueError, 'existing_cross_page_pid_conflict:1902005:2'):
            run(self.data, candidates, True, self.backup)
        with sqlite3.connect(self.data / 'archive.sqlite3') as db:
            self.assertEqual(db.execute("SELECT count(*) FROM jobs WHERE page=2 AND state='parse_error'").fetchone()[0], 6)
        self.assertFalse((self.backup / 'archive-before-offline-reparse.sqlite3').exists())

    def test_page_49_collision_with_parsed_page_50_refused(self):
        s = Store(self.data)
        page49 = SECOND.replace(b'<strong>2</strong>', b'<strong>49</strong>')
        page50 = SECOND.replace(b'<strong>2</strong>', b'<strong>50</strong>')
        url49, url50 = thread_url(1902000, 49), thread_url(1902000, 50)
        s.snapshot(url49, page49, mode='fixture')
        snap50 = s.snapshot(url50, page50, mode='fixture')
        s.save_page(url50, parse_thread(page50, url50), snap50)
        s.db.execute("INSERT INTO jobs(url,kind,tid,page,state,error) VALUES(?, 'thread', 1902000, 49, 'parse_error', 'missing_author_identity:1')", (url49,))
        s.db.commit(); s.db.close()
        with patch('scripts.studio_offline_cache_reparse.CANDIDATES', {(1902000, 2), (1902000, 49)}), self.assertRaisesRegex(ValueError, 'existing_cross_page_pid_conflict:1902000:49'):
            run(self.data, [(1902000, 49)], True, self.backup)
        self.assertFalse((self.backup / 'archive-before-offline-reparse.sqlite3').exists())

    def test_fault_last_of_five_rolls_back_and_backup_is_stable(self):
        candidates = self.make_six_candidates()
        requested = candidates[:5]
        def dump(path):
            db = sqlite3.connect(path)
            out = {t: db.execute('SELECT * FROM ' + t + ' ORDER BY rowid').fetchall() for t in ('jobs','pages','posts','gaps','threads','revisions','links','assets','metrics')}
            db.close(); return out
        before = dump(self.data / 'archive.sqlite3')
        original = Store.save_page
        calls = []
        def fail_last(store, *args, **kwargs):
            calls.append(1)
            result = original(store, *args, **kwargs)
            if len(calls) == 5: raise RuntimeError('injected')
            return result
        with patch('scripts.studio_offline_cache_reparse.CANDIDATES', set(candidates)), patch.object(Store, 'save_page', fail_last), self.assertRaisesRegex(RuntimeError, 'injected'):
            run(self.data, requested, True, self.backup)
        self.assertEqual(len(calls), 5)
        self.assertEqual(before, dump(self.data / 'archive.sqlite3'))
        self.assertEqual(before, dump(self.backup / 'archive-before-offline-reparse.sqlite3'))
        with sqlite3.connect(self.backup / 'archive-before-offline-reparse.sqlite3') as db:
            self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0], 'ok')

    def test_duplicate_snapshot_and_missing_backup_refused(self):
        with self.assertRaisesRegex(ValueError, 'backup'):
            run(self.data, [(1902000, 2)], True)
        s = Store(self.data)
        s.snapshot(URL, SECOND, mode='fixture')
        s.db.close()
        with self.assertRaisesRegex(ValueError, 'expected_one_200_snapshot'):
            run(self.data, [(1902000, 2)])


if __name__ == '__main__':
    unittest.main()
