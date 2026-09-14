#!/usr/bin/env python3
"""Read-only NAS snapshot + Mac monitor. No Store, worker lock or archive writes."""
import argparse
import datetime
import json
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import time
import sys


def snapshot(root):
    root = Path(root).resolve()
    if sys.platform == 'darwin' or str(root).startswith('/Volumes/'):
        raise RuntimeError('Live database queries must run on NAS native storage via SSH')
    # Native NAS filesystem only. mode=ro participates safely in SQLite locking/WAL;
    # never use immutable=1 on a database that a worker is updating.
    db = sqlite3.connect((root/'data/archive.sqlite3').as_uri()+'?mode=ro', uri=True, timeout=5.0)
    try:
        db.execute('PRAGMA query_only=ON')
        def scalar(sql):
            row = db.execute(sql).fetchone()
            return row[0] if row else None
        counts = db.execute("SELECT count(*),COALESCE(sum(t.status='complete'),0) FROM inventory i JOIN threads t USING(tid)").fetchone()
        result = dict(total=counts[0],completed=counts[1])
        result['failures_retry_pending'] = scalar("SELECT count(DISTINCT j.tid) FROM jobs j JOIN inventory i USING(tid) WHERE j.state NOT IN ('success','pending')")
        result['remaining'] = result['total']-result['completed']
        result['archive_scope'] = 'BEST_EFFORT / INCOMPLETE_DISCOVERY'
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'discovery_shards' in tables:
            result['shard_states'] = dict(db.execute('SELECT state,count(*) FROM discovery_shards GROUP BY state'))
        result['posts'] = scalar('SELECT count(*) FROM posts')
        result['pages'] = scalar("SELECT count(*) FROM pages WHERE result='parsed'")
        result['media_files'] = scalar("SELECT count(*) FROM media_files WHERE state='success'")
        result['last_progress'] = scalar("SELECT max(s.fetched_at) FROM pages p JOIN snapshots s ON s.id=p.snapshot_id WHERE p.result='parsed'")
        result['halt'] = scalar("SELECT value FROM settings WHERE key='halt'")
        result['pause_until'] = scalar("SELECT value FROM settings WHERE key='pause_until'")
    finally:
        db.close()
    running = False
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            args = (proc/'cmdline').read_bytes().split(b'\0')
            if (any(Path(a.decode()).name == 'history_worker.py' for a in args if a)
                    and b'backfill' in args and (proc/'cwd').resolve() == root):
                running = True
                break
        except (OSError,UnicodeError):
            continue
    result['worker'] = 'HALTED' if result['halt'] else ('RUNNING' if running else 'STOPPED')
    result['sampled_at'] = time.time()
    return result


def rates(samples):
    now, completed = samples[-1]
    window = [s for s in samples if s[0] >= now-7200]
    def speed(points, minimum):
        elapsed = now-points[0][0]
        return max(0,completed-points[0][1])*3600/elapsed if elapsed >= minimum else None
    recent = [s for s in window if s[0] >= now-1800]
    return speed(recent,240), speed(window,900), (now-window[0][0])/60


def monitor(once=False):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from oursteps.deployment import deployment
    host, root = deployment()
    command = 'python3 - --snapshot '+shlex.quote(root)
    source = Path(__file__).read_text()
    samples = []
    while True:
        try:
            response = subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',host,command],
                                      input=source,text=True,capture_output=True,timeout=45)
            if response.returncode:
                raise RuntimeError(response.stderr.strip()[-300:])
            r = json.loads(response.stdout)
            if once:
                print(json.dumps(r,ensure_ascii=False,indent=2))
                return
            now = r['sampled_at']; samples.append((now,r['completed']))
            samples = [s for s in samples if s[0] >= now-7200]
            recent, stable, minutes = rates(samples)
            remaining = r['total']-r['completed']
            pct = 100*r['completed']/r['total'] if r['total'] else 0
            eta = ('%.1f hours' % (remaining/stable) if stable else 'warming up (15 min minimum) / no recent completions')
            if r['worker'] != 'RUNNING': eta = 'unavailable: worker '+r['worker']
            if not remaining: eta = 'known thread bodies complete; media may still be pending'
            stamp = datetime.datetime.fromtimestamp(r['last_progress']).astimezone().isoformat(timespec='seconds') if r['last_progress'] else 'unknown'
            print('\033[2J\033[H',end='')
            print('Historical Backfill — READ ONLY — refresh 5 minutes — Ctrl-C exits monitor')
            print('Sample:',datetime.datetime.now().astimezone().isoformat(timespec='seconds'))
            print('Known inventory: %d / %d (%.2f%%); remaining: %d' % (r['completed'],r['total'],pct,remaining))
            print('Discovery remains BEST_EFFORT; percentage is not full historical coverage.')
            print('Failures/retry pending (threads):',r['failures_retry_pending'])
            print('Posts: %d | Parsed pages: %d | Downloaded media files: %d' % (r['posts'],r['pages'],r['media_files']))
            print('Worker:',r['worker'], '| pause_until:',r['pause_until'] or 'none')
            print('Last archived-page source timestamp:',stamp)
            print('Recent speed (up to 30 min):', '%.1f threads/hour' % recent if recent is not None else 'warming up')
            print('Rolling speed (%.0f min, up to 2 hours):' % minutes, '%.1f threads/hour' % stable if stable is not None else 'warming up')
            print('Thread-body ETA:',eta)
            print('ETA excludes media/retries; rolling history stays in monitor memory only.',flush=True)
        except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as e:
            print('Monitor sample unavailable (worker untouched):',str(e),flush=True)
        if once:
            return
        time.sleep(300)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot',type=Path)
    parser.add_argument('--once',action='store_true')
    args = parser.parse_args()
    try:
        if args.snapshot:
            print(json.dumps(snapshot(args.snapshot)))
        else:
            monitor(once=args.once)
    except KeyboardInterrupt:
        print('\nMonitor stopped; backfill untouched.')
