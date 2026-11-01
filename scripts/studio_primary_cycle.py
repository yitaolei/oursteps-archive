#!/usr/bin/env python3
"""Studio-only scheduled sync/publish; refuses NAS/mounted data and production 8448 changes."""
import argparse
import fcntl
import os
import shutil
from pathlib import Path
import subprocess
import sys
from studio_local_authority import checked_local_data

ROOT=Path.home()/"Library/Application Support/OurSteps/studio-primary-runtime"
DATA=ROOT/'data'

def cycle(*, execute=False):
    local=checked_local_data(DATA)
    if (local/'archive.sqlite3').is_symlink() or not (local/'archive.sqlite3').is_file():
        raise ValueError('local SQLite missing or symlink')
    if not (ROOT/'config.local.json').is_file() or not (ROOT/'.deps').is_dir():
        raise ValueError('local runtime dependencies missing')
    if not (ROOT/'.secrets/session.json').is_file():
        raise ValueError('local authentication session missing')
    actions=[['archive.py','--data',str(local),'sync-today','--now'],['scripts/publish_public.py']]
    if not execute:
        return {'execution':False,'root':str(ROOT),'actions':actions}
    if os.environ.get('OURSTEPS_STUDIO_WRITER_HANDOVER_APPROVED')!='1':
        raise ValueError('explicit Studio writer authority required')
    incomplete=False
    with (local/'studio-primary-cycle.lock').open('a+') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('Studio primary cycle already running')
        env=os.environ.copy()
        env['OURSTEPS_STUDIO_PRIMARY_EXPERIMENT']='1'
        env.pop('OURSTEPS_NAS_HOST',None)
        env.pop('OURSTEPS_NAS_PROJECT',None)
        for args in actions:
            completed=subprocess.run([sys.executable,*args],cwd=ROOT,env=env,check=False,timeout=1200)
            if completed.returncode: return completed.returncode
            if args[0]=='archive.py':
                # A successful scan can defer a new post behind a JS challenge.
                with __import__('sqlite3').connect((local/'archive.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as db:
                    deferred=db.execute("SELECT count(*) FROM threads t JOIN jobs j ON j.tid=t.tid AND j.page=1 WHERE t.status!='complete' AND t.discovered_via='daily-challenge' AND j.state IN ('retry_later','auth_required')").fetchone()[0]
                incomplete=deferred>0

        subprocess.run([sys.executable,'scripts/studio_first_page_preview.py'],cwd=ROOT,env=env,check=True,timeout=180)
    # Refresh both Studio HTTPS endpoints after a successful manual/automatic sync.
    import re
    import tempfile
    release=(ROOT/'public-site/current').resolve(strict=True)
    releases=(ROOT/'public-site/releases').resolve(strict=True)
    if release.parent!=releases or not (release/'index.html').is_file():
        raise ValueError('new release escaped local releases directory')
    base=Path.home()/"Library/Application Support/OurSteps/studio-apache-private"
    for name in ('httpd.conf','httpd-auth-test.conf'):
        conf=base/name
        if not conf.is_file(): continue
        current=conf.read_text()
        revised=re.sub(r'(?<=DocumentRoot ")[^"]+/public-site/releases/[0-9a-f]{64}(?=")',str(release),current)
        revised=re.sub(r'(?<=<Directory ")[^"]+/public-site/releases/[0-9a-f]{64}(?=">)',str(release),revised)
        if revised==current: continue
        fd,tmp=tempfile.mkstemp(dir=conf.parent,prefix='.primary-httpd-',suffix='.conf')
        try:
            with os.fdopen(fd,'w') as f:f.write(revised);f.flush();os.fsync(f.fileno())
            test=subprocess.run(['/usr/sbin/httpd','-f',tmp,'-t'],capture_output=True,text=True)
            if test.returncode:raise ValueError('Apache release config syntax rejected')
            shutil.copy2(conf,conf.with_name(name+'.before-cycle-update'))
            os.replace(tmp,conf)
            subprocess.run(['/usr/sbin/httpd','-f',str(conf),'-k','graceful'],check=True,timeout=15)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    return 3 if incomplete else 0

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--execute',action='store_true')
    args=p.parse_args()
    try: print(cycle(execute=args.execute),flush=True)
    except (ValueError,OSError,subprocess.TimeoutExpired) as e:
        print('STUDIO_CYCLE_FAILED '+str(e),file=sys.stderr)
        raise SystemExit(2)
if __name__=='__main__':main()
