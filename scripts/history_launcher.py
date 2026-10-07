#!/usr/bin/env python3
"""Local Mac launcher. No hosted AI or scheduling dependency."""
import argparse,json,os,subprocess,sys,shlex
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('mode',choices=['discover','backfill','status','fallback','guide-discover']);p.add_argument('--check',action='store_true');p.add_argument('--now',action='store_true',help='Start manual discovery/backfill outside the normal window');p.add_argument('--best-effort',action='store_true');p.add_argument('--max-threads',type=int);p.add_argument('--max-minutes',type=int);p.add_argument('--max-pages',type=int);a=p.parse_args()
if any(v is not None and v<=0 for v in (a.max_threads,a.max_minutes,a.max_pages)):p.error('backfill limits must be positive')
if a.max_threads is not None and a.mode!='backfill':p.error('--max-threads requires backfill')
if a.max_pages is not None and a.mode!='guide-discover':p.error('--max-pages requires guide-discover')
if a.max_minutes is not None and a.mode not in ('backfill','guide-discover'):p.error('--max-minutes requires backfill or guide-discover')
if a.now and a.mode not in ('discover','backfill','guide-discover'):p.error('--now requires discovery or backfill')
if a.best_effort and a.mode!='backfill':p.error('--best-effort requires backfill')
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from oursteps.deployment import deployment, remote_python, ssh_argv
from scripts.process_utils import install_signal_cleanup, run_group
install_signal_cleanup()
host,remote=deployment()
if a.check:
 assert (ROOT/'scripts/history_worker.py').is_file()
 print(a.mode+': launcher ready (no authentication, network or database opened)');raise SystemExit(0)
if a.mode=='status':
 raise SystemExit(subprocess.call([sys.executable,str(ROOT/'scripts/backfill_live_status.py'),'--once']))
if a.mode!='status' and not (a.mode in ('discover','fallback') and (ROOT/'.secrets/session.json').exists()):
 runtime=Path.home()/'.local/share/oursteps-runtime/bin/python'
 if not runtime.exists():raise SystemExit('Run Authenticate OurSteps.command once to install the authentication runtime')
 try:
  result=run_group([str(runtime),str(ROOT/'archive.py'),'auth'],
                   stdin=subprocess.DEVNULL,text=True,timeout=180)
 except subprocess.TimeoutExpired:
  print('Historical authentication timed out after 180 seconds; saved session preserved.',file=sys.stderr)
  raise SystemExit(75)
 if result.stdout:print(result.stdout,end='')
 if result.stderr:print(result.stderr,end='',file=sys.stderr)
 if result.returncode:raise SystemExit(result.returncode)
command='cd '+shlex.quote(remote)+' && python3 scripts/history_worker.py '+a.mode+(' --now' if a.now else '')+(' --best-effort' if a.best_effort else '')
if a.mode=='backfill':command+=' --defer-preview'
if a.max_pages is not None:command+=' --max-pages '+str(a.max_pages)
if a.max_threads is not None:command+=' --max-threads '+str(a.max_threads)
if a.max_minutes is not None:command+=' --max-minutes '+str(a.max_minutes)
try:
    code=subprocess.call(ssh_argv(host)+[command])
    if code:raise SystemExit(code)
    if a.mode=='backfill':
        # A single unique candidate query and one serial, long-lived Chrome/SSH
        # session. SQLite success state is the checkpoint, including lost ACKs.
        runtime=Path.home()/'.local/share/oursteps-runtime/bin/python'
        try:
            candidate=subprocess.run(
                ssh_argv(host)+[remote_python(
                    'scripts/browser_fallback_candidates.py','--historical','--limit','80')],
                capture_output=True,text=True,timeout=60)
            if candidate.returncode==0:
                payload=json.loads(candidate.stdout.strip().splitlines()[-1])
                urls=payload['urls']
                if not isinstance(urls,list) or any(not isinstance(u,str) for u in urls) or len(urls)>80:
                    raise ValueError('invalid historical candidate list')
                if urls and runtime.exists():
                    fallback=subprocess.run(
                        [str(runtime),str(ROOT/'scripts/browser_fallback.py'),'--historical',*urls],
                        text=True,timeout=14400)
                    if fallback.returncode:
                        print('Historical browser fallback stopped safely; flushing committed preview before publish.',file=sys.stderr)
            else:
                print('Historical browser candidate query failed; flushing pending preview.',file=sys.stderr)
        except (OSError,subprocess.SubprocessError,ValueError,KeyError,IndexError,TypeError) as exc:
            print('Historical browser fallback unavailable: '+type(exc).__name__,file=sys.stderr)
        # Browser process has finished; importer closes its Store and worker.lock
        # first. If it is still alive remotely, this nonblocking lock fails closed.
        code=subprocess.call(ssh_argv(host)+[remote_python('archive.py','build-preview','--historical-pending')])
        if code:raise SystemExit(code)  # Never publish after a failed flush.
        publish='cd '+shlex.quote(remote)+' && python3 scripts/publish_public.py'
        code=subprocess.call(ssh_argv(host)+[publish])
    raise SystemExit(code)
except KeyboardInterrupt:raise SystemExit('Stopped locally; check status before restarting if the NAS worker is still finishing.')
