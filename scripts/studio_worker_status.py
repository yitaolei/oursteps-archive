#!/usr/bin/env python3
"""Sanitized local Studio worker status; no DB or NAS writes."""
import json,subprocess,time
from pathlib import Path
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
NOW=time.time()
def running():
 try: lines=subprocess.check_output(['ps','-axo','pid=,etime=,command='],text=True,timeout=3).splitlines()
 except (OSError,subprocess.SubprocessError):return None
 types=(('scripts/studio_manual_sync_worker.py','incremental_sync'),('scripts/studio_primary_cycle.py --execute','incremental_sync'),('scripts/history_worker.py backfill','historical_backfill'),('scripts/studio_primary_backfill_cycle.py','historical_backfill'),('scripts/history_worker.py guide-discover','guide_discovery'))
 matches=[]
 for line in lines:
  bits=line.strip().split(None,2)
  if len(bits)!=3:continue
  pid,elapsed,cmd=bits
  if '/Python' not in cmd and '/python' not in cmd:continue
  if ' -c ' in cmd or 'zsh' in cmd or 'bash' in cmd:continue
  if '/OurSteps/studio-primary-runtime/' not in cmd and 'scripts/history_worker.py' not in cmd:continue
  for match,kind in types:
   if match in cmd:
    t=0;parts=elapsed.split('-');day=int(parts[0]) if len(parts)==2 else 0
    try:
     clock=[int(x) for x in parts[-1].split(':')]
     t=day*86400+sum(x*60**i for i,x in enumerate(reversed(clock)))
    except ValueError:continue
    matches.append((0 if kind=='incremental_sync' else 1,{'pid':int(pid),'task':kind,'elapsed_seconds':t,'started_at':NOW-t}));break
 return sorted(matches,key=lambda x:x[0])[0][1] if matches else None

def next_backfill():
 now=datetime.now(ZoneInfo('Australia/Sydney'))
 choices=[]
 for offset in (0,1):
  date=(now+timedelta(days=offset)).date()
  for h,m in ((2,15),(4,15),(6,15),(7,45)):
   d=datetime(date.year,date.month,date.day,h,m,tzinfo=ZoneInfo('Australia/Sydney'))
   if d>now:choices.append(d.timestamp())
 return min(choices) if choices else None
job=running()
output={'state':'RUNNING' if job else 'IDLE','task':job['task'] if job else 'idle','elapsed_seconds':job['elapsed_seconds'] if job else None,'started_at':job['started_at'] if job else None,'next_historical_backfill':next_backfill(),'sampled_at':NOW}
print(json.dumps({'worker':output},separators=(',',':')))
