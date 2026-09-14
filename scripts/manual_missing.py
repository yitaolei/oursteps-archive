#!/usr/bin/env python3
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]
from oursteps.manual_missing import extract_tid,refresh

def main():
    if sys.platform=='darwin' or str(ROOT).startswith('/Volumes/'):
        raise SystemExit('Run on NAS native filesystem only')
    request=json.load(sys.stdin)
    if request['command']=='check-oursteps':
        if not request['args']:raise ValueError('Usage: check-oursteps TID [TID or OurSteps URL ...]')
        tids=[extract_tid(x) for x in request['args']]
    elif request['command']=='show-missing':
        if request['args']:raise ValueError('Usage: show-missing')
        tids=None
    else:raise ValueError('unsupported command')
    result=refresh(ROOT,tids)
    print('%-11s %-5s %-12s %-7s %-8s %-6s %-6s %s'%('TID','INV','STATUS','POSTS','PREVIEW','FULL','1Y','RESULT'))
    for s in result['entries']:
        print('%-11s %-5s %-12s %-7s %-8s %-6s %-6s %s'%(s['tid'],'YES' if s['inventory'] else 'NO',s['status'],s['posts'],'YES' if s['preview'] else 'NO','YES' if s['full'] else 'NO','YES' if s['recent'] else 'NO',s['local_state']))
    print('Unique checked: %s | Added: %s | Resolved: %s | Active missing: %s'%(len(result['entries']),len(result['added']),len(result['resolved']),len(result['active'])))
if __name__=='__main__':
    try:main()
    except ValueError as e:raise SystemExit(str(e))
