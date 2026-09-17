#!/usr/bin/env python3
"""NAS-side queue helper for the Mac runner; JSON only, no command execution."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from oursteps.control_actions import claim,finish,status,write_worker_status
p=argparse.ArgumentParser();p.add_argument('mode',choices=['claim','finish','status','worker-status-write']);a=p.parse_args()
if a.mode=='claim': print(json.dumps({'job':claim(ROOT)},separators=(',',':')))
elif a.mode=='status': print(json.dumps({'jobs':status(ROOT)},separators=(',',':')))
elif a.mode=='worker-status-write':
    payload=json.load(sys.stdin)
    print(json.dumps({'worker':write_worker_status(ROOT,payload)},separators=(',',':')))
else:
    payload=json.load(sys.stdin)
    job=finish(ROOT,payload.get('id'),bool(payload.get('success')),payload.get('message',''))
    print(json.dumps({'job':job},separators=(',',':')))
