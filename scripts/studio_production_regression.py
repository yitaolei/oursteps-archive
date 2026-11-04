#!/usr/bin/env python3
"""Read-only Studio owner controls / scheduler regression gate."""
import json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CONF=Path.home()/'Library/Application Support/OurSteps/studio-apache-private/httpd-auth-test.conf'
results={}
def check(name,value):results[name]=bool(value)
text=CONF.read_text()
check('owner_action_proxy', 'ProxyPass /studio-action/ http://127.0.0.1:18752/studio-action/' in text)
check('worker_status_authorized_route', 'worker-status|' in text and 'Require user OSowner' in text)
check('next_backfill_implemented', 'def next_backfill():' in (ROOT/'scripts/studio_worker_status.py').read_text())
check('manual_priority_signal', "studio-manual-priority.json" in (ROOT/'scripts/studio_manual_sync_api.py').read_text())
check('history_yield', "status='manual_priority_yield'" in (ROOT/'oursteps/history.py').read_text())
check('manual_lock_wait', 'OURSTEPS_MANUAL_PRIORITY' in (ROOT/'scripts/studio_primary_cycle.py').read_text())
check('daytime_page_limit', 'page_limit=None if is_overnight else 10' in (ROOT/'oursteps/history.py').read_text())
check('multi_recovery', 'for round_number in range(3)' in (ROOT/'scripts/studio_manual_sync_worker.py').read_text())
worker=(ROOT/'scripts/history_worker.py').read_text()
check('studio_history_guard', 'allow_local_authority(a.data)' in worker and 'SQLite worker must run on NAS native filesystem' not in worker)
check('history_page_budget_wired', '--per-thread-page-budget' in (ROOT/'scripts/studio_primary_backfill_cycle.py').read_text() and 'per_thread_page_budget=a.per_thread_page_budget' in worker)
check('backfill_failure_truthful', "worker_failed_exit_" in (ROOT/'scripts/studio_backfill_performance.py').read_text() and "classify_performance(perf,started_at,returned_at,rc)" in (ROOT/'scripts/studio_primary_backfill_cycle.py').read_text())
try:
 result=subprocess.run(['/usr/sbin/httpd','-f',str(CONF),'-t'],capture_output=True,text=True,timeout=10)
 check('apache_syntax',result.returncode==0)
except (OSError,subprocess.SubprocessError):check('apache_syntax',False)
try:
 from urllib.request import urlopen
 with urlopen('http://127.0.0.1:18752/studio-action/worker-status',timeout=4) as r:
  status=json.load(r)['worker']
 check('worker_status_live',status['state'] in ('RUNNING','IDLE') and status.get('sampled_at'))
except (OSError,ValueError,KeyError):check('worker_status_live',False)
print(json.dumps({'checks':results,'pass':sum(results.values()),'total':len(results),'status':'PASS' if all(results.values()) else 'FAIL'},indent=2))
sys.exit(0 if all(results.values()) else 1)
