#!/usr/bin/env python3
"""Explicitly scoped, offline Studio cache reparse; preview is the default."""
import argparse
import fcntl
import json
import re
from contextlib import ExitStack
import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.deps')]
from scripts.studio_local_authority import allow_local_authority
from oursteps.config import UID
from oursteps.parser import VERSION, parse_thread, thread_url, soup_of

MAX_PAGES = 20
CANDIDATES = frozenset((843944, page) for page in (4, 17, 19, 49, 51, 52))


def identity(url, strict=True):
    u = urlsplit(url)
    q = parse_qs(u.query, keep_blank_values=True)
    if (u.scheme != 'https' or u.netloc != 'www.oursteps.com.au' or
            u.path != '/bbs/forum.php' or u.fragment or
            (strict and (set(q) - {'mod', 'tid', 'page', 'extra'} or
                         ('extra' in q and q['extra'] != ['']))) or
            any(len(v) != 1 for v in q.values()) or q.get('mod') != ['viewthread']):
        raise ValueError('invalid_source_url')
    tid, page = q.get('tid', [''])[0], q.get('page', ['1'])[0]
    if not tid.isascii() or not tid.isdigit() or not page.isascii() or not page.isdigit() or int(tid) < 1 or int(page) < 1:
        raise ValueError('invalid_source_identity')
    if strict and (set(q) not in ({'mod', 'tid', 'page'}, {'mod', 'tid', 'page', 'extra'}) or tid != str(int(tid)) or page != str(int(page))):
        raise ValueError('invalid_source_identity')
    return int(tid), int(page)


def inspect(db, root, requested):
    import hashlib
    db.row_factory = sqlite3.Row
    if db.execute("SELECT 1 FROM jobs WHERE state IN ('done','failed') LIMIT 1").fetchone():
        raise ValueError('legacy_job_states_require_separate_migration')
    results = []
    planned_pids = {}
    planned_hashes = set()
    snapshots = db.execute('SELECT * FROM snapshots WHERE status=200').fetchall()
    for tid, page in requested:
        owner = db.execute('''SELECT 1 FROM pages p JOIN posts o ON o.tid=p.tid
            WHERE p.tid=? AND p.page=1 AND p.result='parsed' AND o.page=1
            AND o.floor='1#' AND o.author_uid=? LIMIT 1''', (tid, UID)).fetchone()
        if owner is None:
            raise ValueError('authenticated_page_one_required:%s' % tid)
        jobs = db.execute('SELECT * FROM jobs WHERE tid=? AND page=?', (tid, page)).fetchall()
        if len(jobs) != 1:
            raise ValueError('expected_one_job:%s:%s' % (tid, page))
        job = jobs[0]
        if (job['kind'] != 'thread' or job['url'] != thread_url(tid, page) or
                job['state'] != 'parse_error' or
                not (job['error'] or '').startswith('missing_author_identity:')):
            raise ValueError('ineligible_job:%s:%s' % (tid, page))
        matches = []
        for snap in snapshots:
            try:
                if identity(snap['url'], strict=False) == (tid, page):
                    matches.append(snap)
            except ValueError:
                continue
        if len(matches) != 1:
            raise ValueError('expected_one_200_snapshot:%s:%s' % (tid, page))
        snap = matches[0]
        if identity(snap['url']) != (tid, page):
            raise ValueError('invalid_source_identity')
        existing = db.execute('SELECT * FROM pages WHERE tid=? AND page=?', (tid, page)).fetchall()
        if len(existing) > 1 or any(row['url'] != job['url'] or
                row['snapshot_id'] != snap['id'] or row['parser_version'] != VERSION
                for row in existing):
            raise ValueError('page_metadata_mismatch:%s:%s' % (tid, page))
        path = (root / snap['path']).resolve(strict=True)
        if not path.is_relative_to(root / 'raw') or not path.is_file():
            raise ValueError('invalid_snapshot_path')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != snap['stored_sha256']:
            raise ValueError('raw_archive_checksum_mismatch')
        if not re.search(rb'</html\s*>\s*$', raw, re.I):
            raise ValueError('incomplete_html:%s:%s' % (tid, page))
        soup = soup_of(raw)
        if (not soup.html or not soup.body or soup.select('meta[http-equiv="refresh" i], form[action*="login" i], form[action*="logging" i], input[type="password" i]') or
                any(re.search(r'\b(?:window\.|document\.)?location\s*(?:\.href\s*=|\.replace\s*\(|=)', tag.get_text(), re.I) for tag in soup.select('script'))):
            raise ValueError('redirect_or_auth_html:%s:%s' % (tid, page))
        markers = soup.select('.pg > strong, .pg input[name="custompage"]')
        values = [n.get('value', '').strip() if n.name == 'input' else n.get_text(strip=True) for n in markers]
        if not values or any(not re.fullmatch(r'[0-9]+', v) or int(v) != page for v in values):
            raise ValueError('missing_or_conflicting_page_indicators:%s:%s' % (tid, page))
        parsed = parse_thread(raw, job['url'])
        if (parsed['tid'], parsed['page'], parsed['parser_version']) != (tid, page, VERSION):
            raise ValueError('parser_metadata_mismatch')
        if snap['stored_sha256'] in planned_hashes:
            raise ValueError('snapshot_hash_used_by_other_planned_page')
        planned_hashes.add(snap['stored_sha256'])
        pids = set(parsed['all_pids'])
        if pids & planned_pids.setdefault(tid, set()):
            raise ValueError('planned_cross_page_pid_conflict:%s:%s' % (tid, page))
        planned_pids[tid].update(pids)
        for other in db.execute("SELECT page,all_pids FROM pages WHERE tid=? AND page!=? AND result='parsed'", (tid, page)):
            if pids & set(json.loads(other['all_pids'])):
                raise ValueError('existing_cross_page_pid_conflict:%s:%s' % (tid, page))
        if db.execute("SELECT 1 FROM pages p JOIN snapshots s ON s.id=p.snapshot_id WHERE p.result='parsed' AND (p.tid!=? OR p.page!=?) AND s.stored_sha256=? LIMIT 1", (tid, page, snap['stored_sha256'])).fetchone():
            raise ValueError('snapshot_hash_used_by_other_page:%s:%s' % (tid, page))
        for post in parsed['posts']:
            if db.execute('SELECT 1 FROM posts WHERE pid=? AND (tid!=? OR page!=?)', (post['pid'], tid, page)).fetchone():
                raise ValueError('owner_pid_conflict:%s' % post['pid'])
        # Do not discover or alter other jobs as a side effect of recovery.
        parsed['next_pages'] = []
        results.append((job, snap, parsed))
    return results


def run(data, requested, apply=False, backup=None):
    data = allow_local_authority(data)
    if not requested or len(requested) > MAX_PAGES or len(set(requested)) != len(requested):
        raise ValueError('allowlist must contain 1..%s unique pages' % MAX_PAGES)
    if any(tid <= 0 or page <= 1 for tid, page in requested):
        raise ValueError('continuation pages only')
    if not set(requested).issubset(CANDIDATES):
        raise ValueError('page_not_allowlisted')
    if apply and backup is None:
        raise ValueError('--apply requires --backup directory')
    # The JS challenge hold prohibits network work; verified offline reparse is
    # explicitly allowed and does not clear or change that hold or publish.
    with ExitStack() as stack:
        for name in (('studio-primary-cycle.lock', 'worker.lock') if apply else ('worker.lock',)):
            lock = stack.enter_context((data / name).open('rb'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        dbpath = data / 'archive.sqlite3'
        db = sqlite3.connect(dbpath.resolve().as_uri() + '?mode=ro', uri=True)
        try:
            items = inspect(db, data, requested)
            data_version = db.execute('PRAGMA data_version').fetchone()[0]
            if apply:
                dest = allow_local_authority(backup)
                st = dest.stat()
                if dest == data or dest.is_relative_to(data) or st.st_uid != os.getuid() or st.st_mode & 0o077:
                    raise ValueError('backup requires existing owner-only directory outside data root')
                target = dest / 'archive-before-offline-reparse.sqlite3'
                fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
                try:
                    copy = sqlite3.connect(str(target))
                    try:
                        db.backup(copy)
                        if copy.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                            raise ValueError('backup_quick_check_failed')
                    finally:
                        copy.close()
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
        except BaseException:
            db.close()
            raise
        if apply:
            from oursteps.store import Store, verify_owner_schema
            # Avoid Store initialization: its schema and migration work commit independently.
            store = Store.__new__(Store)
            store.root = data
            store.historical_mode = True
            store.db = sqlite3.connect(str(dbpath), timeout=30)
            store.db.row_factory = sqlite3.Row
            try:
                verify_owner_schema(store.db)
                store.db.execute('PRAGMA foreign_keys=ON')
                store.db.execute('BEGIN IMMEDIATE')
                if db.execute('PRAGMA data_version').fetchone()[0] != data_version:
                    raise ValueError('database_changed_since_backup')
                # Revalidate under the write reservation before changing anything.
                items = inspect(store.db, data, requested)
                for job, snap, parsed in items:
                    store.save_page(job['url'], parsed, snap, external_transaction=True)
                store.db.commit()
            except BaseException:
                store.db.rollback()
                raise
            finally:
                store.db.close()
                db.close()
        else:
            db.close()
        return [{'tid': p['tid'], 'page': p['page'], 'snapshot_id': s['id'],
                 'source_url': s['url'], 'job_url': j['url'], 'gaps': p['gaps'],
                 'owner_posts': len(p['posts'])} for j, s, p in items]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data', required=True)
    p.add_argument('--page', action='append', required=True, metavar='TID:PAGE')
    p.add_argument('--apply', action='store_true')
    p.add_argument('--backup')
    args = p.parse_args()
    try:
        requested = [tuple(map(int, x.split(':'))) for x in args.page]
        if any(len(x) != 2 for x in requested):
            raise ValueError('each page must be TID:PAGE')
        result = run(args.data, requested, args.apply, args.backup)
    except (ValueError, OSError, sqlite3.Error) as exc:
        p.exit(1, 'refused: %s\n' % exc)
    print(json.dumps({'mode': 'apply' if args.apply else 'dry_run',
                      'preview': 'deferred', 'items': result}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
