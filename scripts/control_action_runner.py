#!/usr/bin/env python3
"""Mac-only safe action runner. Fixed allowlist; registry text is never executed."""
import fcntl,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from oursteps.deployment import deployment,remote_python

def action_spec(action):
    mapping={
      'incremental_sync':([sys.executable,str(ROOT/'scripts/update_now.py'),'--now'],3600),
      'guide_discovery_v2':([sys.executable,str(ROOT/'scripts/history_launcher.py'),'guide-discover','--now','--max-pages','25','--max-minutes','20'],1800),
      'historical_backfill':([sys.executable,str(ROOT/'scripts/history_launcher.py'),'backfill','--best-effort','--now','--max-threads','20','--max-minutes','45'],3600),
    }
    if action=='publish_public': return ('remote_publish',1200)
    if action not in mapping: raise ValueError('action not in runner allowlist')
    return mapping[action]

def main():
    lock_path=Path.home()/'.local/share/oursteps-runtime/control-action-runner.lock';lock_path.parent.mkdir(parents=True,exist_ok=True)
    with lock_path.open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return 0
        host,_=deployment()
        def remote(*args,input=None,timeout=30):
            return subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',host,remote_python(*args)],input=input,text=True,capture_output=True,timeout=timeout)
        got=remote('scripts/control_action_queue.py','claim')
        if got.returncode: return got.returncode
        job=json.loads(got.stdout).get('job')
        if not job: return 0
        action=job.get('action')
        try:
            spec,timeout=action_spec(action)
            if spec=='remote_publish':
                run=remote('scripts/publish_public.py',timeout=timeout)
                if run.returncode==0: run=remote('scripts/public_healthcheck.py',timeout=timeout)
            else:
                run=subprocess.run(spec,capture_output=True,text=True,timeout=timeout)
            success=run.returncode==0;message='Completed successfully' if success else 'Action failed; inspect operator logs'
        except subprocess.TimeoutExpired:
            success=False;message='Action timed out; inspect operator status before retrying'
        except Exception:
            success=False;message='Action runner error; inspect operator logs'
        payload=json.dumps({'id':job['id'],'success':success,'message':message})
        done=remote('scripts/control_action_queue.py','finish',input=payload)
        return 0 if success and done.returncode==0 else 1

if __name__=='__main__': raise SystemExit(main())
