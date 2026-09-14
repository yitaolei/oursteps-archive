"""NAS-local SQLite plus private, content-addressed HTML snapshots."""
from .config import UID
from .performance import measured
from . import persistence_diagnostics as diag
import hashlib
import json
import os
import re
import sqlite3
import time
from .sqlite_retry import WriterConnection
from pathlib import Path

SCHEMA = f'''
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS threads(
 tid INTEGER PRIMARY KEY,title TEXT,fid INTEGER,forum TEXT,created_at_raw TEXT,
 status TEXT NOT NULL DEFAULT 'pending',discovered_via TEXT NOT NULL,
 publish_state TEXT NOT NULL DEFAULT 'excluded');
CREATE TABLE IF NOT EXISTS jobs(
 url TEXT PRIMARY KEY,kind TEXT NOT NULL,tid INTEGER REFERENCES threads(tid),
 page INTEGER,state TEXT NOT NULL DEFAULT 'pending',attempts INTEGER DEFAULT 0,
 retry_at REAL DEFAULT 0,error TEXT);
CREATE TABLE IF NOT EXISTS snapshots(
 id INTEGER PRIMARY KEY,url TEXT NOT NULL,stored_sha256 TEXT NOT NULL,
 original_sha256 TEXT NOT NULL,path TEXT NOT NULL,fetched_at REAL NOT NULL,
 status INTEGER NOT NULL,mode TEXT NOT NULL,UNIQUE(url,stored_sha256,mode));
CREATE TABLE IF NOT EXISTS pages(
 url TEXT PRIMARY KEY,snapshot_id INTEGER REFERENCES snapshots(id),
 tid INTEGER,page INTEGER,parser_version TEXT,all_pids TEXT,declared_pages INTEGER,
 result TEXT NOT NULL DEFAULT 'unparsed',error TEXT);
CREATE TABLE IF NOT EXISTS posts(
 pid INTEGER PRIMARY KEY,tid INTEGER NOT NULL REFERENCES threads(tid),
 author_uid INTEGER NOT NULL CHECK(author_uid={UID}),author_name TEXT,
 floor TEXT,page INTEGER,posted_at_raw TEXT,edited_at_raw TEXT,
 body_html TEXT,body_text TEXT,content_sha256 TEXT,snapshot_id INTEGER,
 visibility TEXT NOT NULL DEFAULT 'unknown',publish_state TEXT NOT NULL DEFAULT 'excluded');
CREATE TABLE IF NOT EXISTS revisions(
 pid INTEGER,content_sha256 TEXT,parser_version TEXT,snapshot_id INTEGER,
 body_html TEXT,body_text TEXT,PRIMARY KEY(pid,content_sha256,parser_version));
CREATE TABLE IF NOT EXISTS links(pid INTEGER,url TEXT,label TEXT,
 PRIMARY KEY(pid,url,label));
CREATE TABLE IF NOT EXISTS assets(pid INTEGER,position INTEGER,kind TEXT,url TEXT,
 attributes TEXT,status TEXT DEFAULT 'not_downloaded',visibility TEXT DEFAULT 'unknown',
 PRIMARY KEY(pid,position));
CREATE TABLE IF NOT EXISTS metrics(tid INTEGER,snapshot_id INTEGER,views INTEGER,replies INTEGER,
 PRIMARY KEY(tid,snapshot_id));
CREATE TABLE IF NOT EXISTS gaps(url TEXT,pid INTEGER,reason TEXT,
 PRIMARY KEY(url,pid,reason));
CREATE TABLE IF NOT EXISTS publication_times(tid INTEGER PRIMARY KEY,sydney_time TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS daily_members(day TEXT,tid INTEGER REFERENCES threads(tid),PRIMARY KEY(day,tid));
CREATE TABLE IF NOT EXISTS discovery_state(day TEXT PRIMARY KEY,url TEXT,state TEXT,retry_at REAL DEFAULT 0,attempts INTEGER DEFAULT 0,error TEXT);
CREATE TABLE IF NOT EXISTS listing_metrics(tid INTEGER PRIMARY KEY,views INTEGER,replies INTEGER,observed_at REAL);
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY,started_at REAL,finished_at REAL,
 status TEXT,summary TEXT);
CREATE INDEX IF NOT EXISTS idx_jobs_tid ON jobs(tid);
CREATE INDEX IF NOT EXISTS idx_pages_tid ON pages(tid);
PRAGMA user_version=3;
'''


def verify_owner_schema(db):
    """Reject incompatible owner constraints without migrating an existing DB."""
    row = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='posts'").fetchone()
    if row is None:
        return
    match = re.search(r'CHECK\s*\(\s*author_uid\s*=\s*(\d+)\s*\)', row[0], re.I)
    if not match or int(match.group(1)) != UID:
        raise ValueError('database_owner_config_mismatch: use the original identity config or a new database; migration is not automatic')


@diag.section('atomic_file')
def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp.%s' % os.getpid())
    with open(tmp, 'wb') as f:
        os.chmod(tmp, 0o600)
        diag.add('raw_bytes_written',len(content))
        diag.call('file_write_seconds',f.write,content)
        diag.call('file_flush_seconds',f.flush)
        diag.add('python_fsync_calls')
        diag.call('file_fsync_seconds',os.fsync,f.fileno())
    diag.add('atomic_renames')
    diag.call('rename_seconds',os.replace,tmp,path)


def redact_raw(content):
    # Preserve body/template HTML, redact volatile security values only.
    content = re.sub(rb'(?i)(formhash=)[^&\s"\'<>]+', rb'\1REDACTED', content)
    content = re.sub(rb'(?is)<input\b(?=[^>]*\bname=["\'](?:formhash|password|pwd)["\'])[^>]*>',
                     b'<!-- security input redacted -->', content)
    return content


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.db = sqlite3.connect(str(self.root / 'archive.sqlite3'), timeout=30, factory=WriterConnection)
        try:
            verify_owner_schema(self.db)
        except Exception:
            self.db.close()
            raise
        os.chmod(self.root / 'archive.sqlite3', 0o600)
        self.db.execute('PRAGMA busy_timeout=30000')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('PRAGMA synchronous=FULL')
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1, 2, 3):
            raise ValueError('unsupported_database_version')
        self.db.executescript(SCHEMA)
        with self.db:
            self.db.execute("UPDATE jobs SET state='success' WHERE state='done'")
            self.db.execute("UPDATE jobs SET state=CASE WHEN error LIKE 'transient:%' THEN 'retry_later' WHEN error LIKE '%permission%' OR error LIKE '%登录%' OR error LIKE '%权限%' OR error LIKE '%Blocked%' THEN 'auth_required' ELSE 'parse_error' END WHERE state='failed'")

    def setting(self, key, default=None):
        r = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return r[0] if r else default

    def set_setting(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, str(value)))

    def seed(self, tids, provenance):
        tids = list(dict.fromkeys(int(x) for x in tids))
        existing = {r[0] for r in self.db.execute('SELECT tid FROM threads')}
        if len(existing | set(tids)) > 20 or any(x <= 0 for x in tids):
            raise ValueError('pilot_hard_limit_20_threads')
        from .parser import thread_url
        with self.db:
            for tid in tids:
                self.db.execute('INSERT OR IGNORE INTO threads(tid,discovered_via) VALUES(?,?)',
                                (tid, provenance))
                self.db.execute('INSERT OR IGNORE INTO jobs(url,kind,tid,page) VALUES(?,?,?,1)',
                                (thread_url(tid), 'thread', tid))

    @measured('persistence_seconds')
    def snapshot(self, url, content, status=200, mode='anonymous'):
        original_hash = hashlib.sha256(content).hexdigest()
        stored = redact_raw(content)
        digest = hashlib.sha256(stored).hexdigest()
        rel = 'raw/%s/%s.html' % (digest[:2], digest)
        if not (self.root / rel).exists():
            diag.add('raw_html_writes')
            atomic_write(self.root / rel, stored)
        with self.db:
            self.db.execute('''INSERT OR IGNORE INTO snapshots
                (url,stored_sha256,original_sha256,path,fetched_at,status,mode)
                VALUES(?,?,?,?,?,?,?)''',
                (url, digest, original_hash, rel, time.time(), status, mode))
        return self.db.execute('SELECT * FROM snapshots WHERE url=? AND stored_sha256=? AND mode=?',
                               (url, digest, mode)).fetchone()

    def latest(self, url):
        return self.db.execute('SELECT * FROM snapshots WHERE url=? ORDER BY id DESC LIMIT 1',
                               (url,)).fetchone()

    def raw(self, snapshot):
        data = (self.root / snapshot['path']).read_bytes()
        if hashlib.sha256(data).hexdigest() != snapshot['stored_sha256']:
            raise ValueError('raw_archive_checksum_mismatch')
        return data

    @measured('persistence_seconds')
    def save_page(self, url, parsed, snap):
        from .parser import VERSION, thread_url
        p = parsed
        # Repeated page content under different page numbers must not imply completion.
        for row in self.db.execute('SELECT page,all_pids FROM pages WHERE tid=? AND result="parsed"', (p['tid'],)):
            if row['page'] != p['page'] and json.loads(row['all_pids']) == p['all_pids']:
                raise ValueError('repeated_page_pid_set')
        limit = 1000 if getattr(self,'historical_mode',False) or self.db.execute('SELECT 1 FROM daily_members WHERE tid=?',(p['tid'],)).fetchone() else 10
        if any(n > limit for n in p['next_pages']) or (p['declared_pages'] or 0) > limit:
            raise ValueError('pilot_page_limit_10_per_thread')
        with self.db:
            self.db.execute('UPDATE threads SET title=?,fid=?,forum=?,status="partial" WHERE tid=?',
                            (p['title'], p['fid'], p['forum'], p['tid']))
            self.db.execute('DELETE FROM gaps WHERE url=?', (url,))
            for gap in p['gaps']:
                self.db.execute('INSERT OR IGNORE INTO gaps VALUES(?,?,?)',
                                (url, gap['pid'], gap['reason']))
            for post in p['posts']:
                self.db.execute('''INSERT OR REPLACE INTO posts
                    (pid,tid,author_uid,author_name,floor,page,posted_at_raw,edited_at_raw,
                    body_html,body_text,content_sha256,snapshot_id,visibility,publish_state)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (post['pid'], p['tid'], post['author_uid'], post['author_name'], post['floor'],
                     p['page'], post['posted_at_raw'], post['edited_at_raw'], post['html'], post['text'],
                     post['hash'], snap['id'], 'anonymous_observed' if snap['mode']=='anonymous' else 'unknown',
                     'excluded'))
                self.db.execute('INSERT OR IGNORE INTO revisions VALUES(?,?,?,?,?,?)',
                                (post['pid'], post['hash'], VERSION, snap['id'], post['html'], post['text']))
                self.db.execute('DELETE FROM links WHERE pid=?', (post['pid'],))
                for link in post['links']:
                    self.db.execute('INSERT OR IGNORE INTO links VALUES(?,?,?)',
                                    (post['pid'], link['url'], link['label']))
                self.db.execute('DELETE FROM assets WHERE pid=?', (post['pid'],))
                for pos, asset in enumerate(post['assets']):
                    self.db.execute('INSERT INTO assets(pid,position,kind,url,attributes) VALUES(?,?,?,?,?)',
                                    (post['pid'], pos, asset['kind'], asset['url'], json.dumps(asset['attributes'])))
                if p['page'] == 1 and post['floor'] == '1#':
                    self.db.execute('UPDATE threads SET created_at_raw=? WHERE tid=?',
                                    (post['posted_at_raw'], p['tid']))
            self.db.execute('INSERT OR REPLACE INTO metrics VALUES(?,?,?,?)',
                            (p['tid'], snap['id'], p['views'], p['replies']))
            self.db.execute('''INSERT OR REPLACE INTO pages
                (url,snapshot_id,tid,page,parser_version,all_pids,declared_pages,result)
                VALUES(?,?,?,?,?,?,?,"parsed")''',
                (url, snap['id'], p['tid'], p['page'], VERSION, json.dumps(p['all_pids']), p['declared_pages']))
            for n in p['next_pages']:
                self.db.execute('INSERT OR IGNORE INTO jobs(url,kind,tid,page) VALUES(?,?,?,?)',
                                (thread_url(p['tid'], n), 'thread', p['tid'], n))
            self.db.execute('UPDATE jobs SET state="success",error=NULL WHERE url=?', (url,))
        self.refresh_status([p['tid']])

    @diag.section('refresh_status')
    def refresh_status(self, tids=None):
        """Same completeness rules; None explicitly retains full-archive maintenance."""
        sql='SELECT tid FROM threads';params=()
        if tids is not None:
            params=tuple(tids)
            if any(type(tid) is not int or not 0<tid<2**63 for tid in params):
                raise ValueError('invalid status TID')
            params=tuple(sorted(set(params)))
            if not params:return
            sql+=' WHERE tid IN ('+','.join('?' for _ in params)+')'
        with self.db:
            for row in self.db.execute(sql,params).fetchall():
                diag.add('refresh_threads_visited')
                tid = row['tid']
                jobs = self.db.execute('SELECT state FROM jobs WHERE tid=?', (tid,)).fetchall()
                pages = self.db.execute('SELECT * FROM pages WHERE tid=? AND result="parsed"', (tid,)).fetchall()
                observed = {r['page'] for r in pages}
                total = max([r['declared_pages'] or r['page'] for r in pages] or [1])
                gaps = self.db.execute('SELECT count(*) FROM gaps g JOIN jobs j ON g.url=j.url WHERE j.tid=?', (tid,)).fetchone()[0]
                complete = (jobs and all(j['state']=='success' for j in jobs) and
                            observed == set(range(1, total+1)) and not gaps)
                self.db.execute('UPDATE threads SET status=? WHERE tid=?',
                                ('complete' if complete else ('partial' if pages else 'pending'), tid))

    def fail(self, job, error, retry=False, state=None, retry_after=0):
        import random
        state = state or ('retry_later' if retry else 'parse_error')
        attempts = self.db.execute('SELECT attempts FROM jobs WHERE url=?', (job['url'],)).fetchone()[0]
        delay = max(retry_after, min(3600, 60 * 2**min(attempts, 6)) * random.uniform(1, 1.3))
        with self.db:
            self.db.execute('UPDATE jobs SET state=?,error=?,retry_at=? WHERE url=?',
                            (state, error, time.time()+delay if state=='retry_later' else (time.time() if state=='auth_required' else 0), job['url']))
            self.db.execute('DELETE FROM gaps WHERE url=? AND pid=0', (job['url'],))
            self.db.execute('INSERT OR IGNORE INTO gaps VALUES(?,0,?)', (job['url'], error))
        self.refresh_status([job['tid']])

    def report(self):
        result = dict(job_states={r[0]:r[1] for r in self.db.execute("SELECT state,count(*) FROM jobs GROUP BY state")}, threads=self.db.execute('SELECT count(*) FROM threads').fetchone()[0],
                      complete_threads=self.db.execute('SELECT count(*) FROM threads WHERE status="complete"').fetchone()[0],
                      author_posts=self.db.execute('SELECT count(*) FROM posts').fetchone()[0],
                      raw_snapshots=self.db.execute('SELECT count(*) FROM snapshots').fetchone()[0],
                      parsed_pages=self.db.execute('SELECT count(*) FROM pages WHERE result="parsed"').fetchone()[0],
                      assets_identified=self.db.execute('SELECT count(*) FROM assets').fetchone()[0],
                      failures=[dict(r) for r in self.db.execute("SELECT url,state,error FROM jobs WHERE state IN ('auth_required','not_found','parse_error','permanently_skipped')")],
                      gaps=[dict(r) for r in self.db.execute('SELECT * FROM gaps')],
                      pending_jobs=self.db.execute('SELECT count(*) FROM jobs WHERE state="pending"').fetchone()[0],
                      halt=self.setting('halt'), database=str(self.root/'archive.sqlite3'),
                      raw_directory=str(self.root/'raw'), publish_state='excluded')
        atomic_write(self.root/'pilot-report.json', json.dumps(result, ensure_ascii=False, indent=2).encode())
        return result
