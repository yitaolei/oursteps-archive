#!/usr/bin/env python3
"""Local, read-only periodic check of archive progress, separate from worker liveness."""
import json
import re
import sys
import time
from pathlib import Path

HOME=Path.home()
LOG=HOME/'Library/Logs/oursteps-history-backfill.log'
SYNC=HOME/'Library/Logs/oursteps-update-launchd.log'
OUT=HOME/'Library/Logs/oursteps-progress-health.json'
BROWSER_STATE=HOME/'Library/Application Support/OurSteps/control-plane/data/historical-browser-lane-state.json'

def tail(path,amount=1500000):
    if not path.exists():return ''
    with path.open('rb') as f:
        f.seek(max(0,path.stat().st_size-amount))
        return f.read().decode('utf-8','replace')

def report(history,sync,browser=None,now=None):
    now=int(time.time() if now is None else now)
    browser=browser if isinstance(browser,dict) else {}
    recent=history[-150000:]
    history_halt=bool(re.search(r'"run_state"\s*:\s*"halted: inspect existing halt',recent))
    def last_number(key):
        vals=re.findall(r'"'+re.escape(key)+r'"\s*:\s*(\d+)',recent)
        return int(vals[-1]) if vals else None
    pending=last_number('awaiting_backfill')
    missing=last_number('shards_remaining')
    inventory_counts=browser.get('inventory_counts') if isinstance(browser.get('inventory_counts'),dict) else {}
    if isinstance(inventory_counts.get('incomplete'),int) and inventory_counts.get('incomplete')>=0:
        pending=inventory_counts['incomplete']
    last_sync=None
    for line in reversed(sync.splitlines()):
        if line.startswith('OurSteps Sync '):
            last_sync=line;break
    browser_result=browser.get('last_result') if isinstance(browser.get('last_result'),dict) else {}
    recent_browser=(browser_result.get('status')=='completed' and isinstance(browser_result.get('at'),int)
                    and 0<=now-browser_result['at']<=6*3600)
    browser_site_hold=bool(browser.get('site_hold'))
    failures=browser.get('failed') if isinstance(browser.get('failed'),dict) else {}
    latest_failure=max((v.get('at',0) for v in failures.values() if isinstance(v,dict) and isinstance(v.get('at'),int)),default=0)
    recent_failure=bool(latest_failure and 0<=now-latest_failure<=6*3600 and latest_failure>browser_result.get('at',0))
    if recent_failure:recent_browser=False
    health=('BROWSER_SITE_HOLD' if browser_site_hold else
            ('BROWSER_RECOVERY_PARTIAL' if recent_browser else 'BROWSER_LANE_FAILED' if recent_failure else 'BLOCKED') if history_halt else
            'UNKNOWN' if pending is None else 'ACTIVE_OR_UNVERIFIED')
    return dict(checked_at=now,health=health,history_halt_seen=history_halt,
                browser_lane_recent=recent_browser,browser_lane_recent_failure=recent_failure,browser_site_hold=browser_site_hold,
                browser_lane_last_result=browser_result or None,
                historical_complete=inventory_counts.get('complete'),
                awaiting_backfill=pending,discovery_shards_remaining=missing,last_sync=last_sync)

def main():
    try:browser=json.loads(BROWSER_STATE.read_text())
    except (FileNotFoundError,ValueError):browser={}
    data=report(tail(LOG),tail(SYNC),browser)
    tmp=OUT.with_suffix('.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    tmp.replace(OUT)
    print(json.dumps(data,ensure_ascii=False))
    return 2 if data['history_halt_seen'] or data['browser_site_hold'] else 0

if __name__=='__main__':sys.exit(main())
