#!/usr/bin/env python3
"""Studio-local bounded history batch; shares lock with daily sync."""
import fcntl, os, subprocess, sys, sqlite3, json, time
from pathlib import Path
from studio_local_authority import checked_local_data
from studio_backfill_performance import classify_performance
ROOT=Path(__file__).resolve().parents[1]
DATA=checked_local_data(ROOT/'data')
if os.environ.get('OURSTEPS_STUDIO_WRITER_HANDOVER_APPROVED')!='1':
    raise SystemExit('Studio authority switch required')
if not (DATA/'archive.sqlite3').is_file() or (DATA/'archive.sqlite3').is_symlink():
    raise SystemExit('Local SQLite missing')
def metrics():
    db=sqlite3.connect((DATA/'archive.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    try:
        return {'complete':db.execute("SELECT count(*) FROM threads WHERE status='complete'").fetchone()[0],
                'snapshots':db.execute('SELECT count(*) FROM snapshots').fetchone()[0],
                'inventory':db.execute('SELECT count(*) FROM inventory').fetchone()[0],
                'first_page_only':db.execute("SELECT count(*) FROM threads t WHERE t.status!='complete' AND EXISTS (SELECT 1 FROM jobs j WHERE j.tid=t.tid AND j.page=1 AND j.state='success')").fetchone()[0]}
    finally:db.close()
with (DATA/'studio-primary-cycle.lock').open('a+') as lock:
    try: fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError: raise SystemExit('Another Studio production cycle active')
    env=os.environ.copy();env['OURSTEPS_STUDIO_PRIMARY_EXPERIMENT']='1'
    env.pop('OURSTEPS_NAS_HOST',None);env.pop('OURSTEPS_NAS_PROJECT',None)
    before=metrics();started_at=time.time()
    args=[sys.executable,'scripts/history_worker.py','backfill','--data',str(DATA),'--best-effort','--max-threads','60','--max-minutes','45','--per-thread-page-budget','5']
    worker_error=None
    try:
        rc=subprocess.run(args,cwd=ROOT,env=env,timeout=3000).returncode
    except subprocess.TimeoutExpired:
        rc=None;worker_error='worker_timeout'
    except OSError as error:
        rc=None;worker_error='worker_launch_failed'
        worker_error_detail=type(error).__name__ + ': ' + str(error)
    returned_at=time.time()
    try:
        after=metrics()
    except (OSError,sqlite3.Error) as error:
        if not worker_error: raise
        after=None;metrics_error=type(error).__name__ + ': ' + str(error)
    try:
        perf=json.loads((DATA/'backfill-performance.json').read_text())
    except (OSError,ValueError,TypeError):
        perf=None
    if worker_error:
        reason,stats=worker_error,{}
    else:
        reason,stats=classify_performance(perf,started_at,returned_at,rc)
    receipt={'started_at':started_at,'elapsed_seconds':round(time.time()-started_at,2),'before':before,'after':after,'new_complete':after['complete']-before['complete'] if after else None,'new_snapshots':after['snapshots']-before['snapshots'] if after else None,'worker_exit':rc,'worker_state':reason}
    receipt.update(stats)
    if worker_error=='worker_launch_failed':receipt['worker_error_detail']=worker_error_detail
    if after is None:receipt['metrics_error']=metrics_error
    with (DATA/'studio-backfill-runs.jsonl').open('a',encoding='utf-8') as history:
        history.write(json.dumps(receipt,sort_keys=True)+'\n');history.flush();os.fsync(history.fileno())
    print('BACKFILL_METRICS',json.dumps(receipt),flush=True)
    if worker_error or rc: raise SystemExit(rc or 1)
    if reason in ('invalid_current_performance','no_work','halted','interrupted','historical_js_challenge_hold'):
        print('STUDIO_BACKFILL_ALERT',reason,flush=True)
        raise SystemExit(1)
    if reason=='manual_priority_yield':
        print('STUDIO_BACKFILL_YIELDED_TO_MANUAL',flush=True)
        raise SystemExit(0)
    if reason=='waiting_window' and after==before:
        print('STUDIO_BACKFILL_DEFERRED_WINDOW_NO_PUBLISH',flush=True)
        raise SystemExit(0)
    rc=subprocess.run([sys.executable,'scripts/publish_public.py'],cwd=ROOT,env=env,timeout=900).returncode
    if rc: raise SystemExit(rc)
    subprocess.run([sys.executable,'scripts/studio_first_page_preview.py'],cwd=ROOT,env=env,check=True,timeout=180)
    # Reuse the verified daily-cycle website refresh path after publication.
    # It also repeats daily discovery safely; the shared cycle lock prevents overlap.
    # Rather than recursively acquire that lock, refresh Apache directly here.
    import re, shutil, tempfile
    release=(ROOT/'public-site/current').resolve(strict=True)
    if release.parent!=(ROOT/'public-site/releases').resolve(strict=True):
        raise SystemExit('Release path escaped Studio')
    for filename in ('httpd.conf','httpd-auth-test.conf'):
        conf=Path.home()/'Library/Application Support/OurSteps/studio-apache-private'/filename
        if not conf.is_file(): continue
        current=conf.read_text()
        new=re.sub(r'(?<=DocumentRoot ")[^"]+(?=")',str(release),current)
        new=re.sub(r'(?<=<Directory ")[^"]*/public-site/releases/[0-9a-f]{64}(?=">)',str(release),new)
        if new==current: continue
        fd,tmp=tempfile.mkstemp(dir=conf.parent,prefix='.backfill-apache-',suffix='.conf')
        try:
            with os.fdopen(fd,'w') as stream:stream.write(new);stream.flush();os.fsync(stream.fileno())
            subprocess.run(['/usr/sbin/httpd','-f',tmp,'-t'],check=True,capture_output=True)
            shutil.copy2(conf,conf.with_name(filename+'.before-backfill-refresh'))
            os.replace(tmp,conf)
            subprocess.run(['/usr/sbin/httpd','-f',str(conf),'-k','graceful'],check=True,timeout=15)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
print('STUDIO_BACKFILL_CYCLE_OK')
