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

def tail(path,amount=1500000):
    if not path.exists():return ''
    with path.open('rb') as f:
        f.seek(max(0,path.stat().st_size-amount))
        return f.read().decode('utf-8','replace')

def report(history,sync):
    recent=history[-150000:]
    history_halt=bool(re.search(r'"run_state"\s*:\s*"halted: inspect existing halt',recent))
    def last_number(key):
        vals=re.findall(r'"'+re.escape(key)+r'"\s*:\s*(\d+)',recent)
        return int(vals[-1]) if vals else None
    pending=last_number('awaiting_backfill')
    missing=last_number('shards_remaining')
    last_sync=None
    for line in reversed(sync.splitlines()):
        if line.startswith('OurSteps Sync '):
            last_sync=line;break
    health='BLOCKED' if history_halt else 'UNKNOWN' if pending is None else 'ACTIVE_OR_UNVERIFIED'
    return dict(checked_at=int(time.time()),health=health,history_halt_seen=history_halt,
                awaiting_backfill=pending,discovery_shards_remaining=missing,last_sync=last_sync)

def main():
    data=report(tail(LOG),tail(SYNC))
    tmp=OUT.with_suffix('.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    tmp.replace(OUT)
    print(json.dumps(data,ensure_ascii=False))
    return 2 if data['health']=='BLOCKED' else 0

if __name__=='__main__':sys.exit(main())
