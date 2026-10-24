#!/usr/bin/env python3
"""Small sequential batch driver for the guarded Studio historical browser lane."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.process_utils import run_group,install_signal_cleanup
install_signal_cleanup()

def burst(max_runs=3,max_pages=5):
    if not 1<=max_runs<=5 or not 1<=max_pages<=12:raise ValueError('invalid burst limits')
    runtime=Path.home()/'.local/share/oursteps-runtime/bin/python'
    results=[]
    for _ in range(max_runs):
        run=run_group([str(runtime),str(ROOT/'scripts/historical_browser_lane.py'),'--max-pages',str(max_pages)],text=True,timeout=1500)
        try:report=json.loads(run.stdout.strip().splitlines()[-1])
        except (ValueError,IndexError):
            report={'status':'invalid_child_result'}
        results.append(report)
        if run.returncode or report.get('status')!='completed':break
    return {'runs':len(results),'pages_imported':sum(int(x.get('pages_imported',0)) for x in results),
            'threads_completed':sum(int(x.get('completed_pages',0)) for x in results),
            'results':results}

def main():
    p=argparse.ArgumentParser();p.add_argument('--max-runs',type=int,default=3);p.add_argument('--max-pages',type=int,default=5);a=p.parse_args()
    data=burst(a.max_runs,a.max_pages)
    print(json.dumps(data,separators=(',',':')))
    if any(x.get('status') in ('held','invalid_child_result','site_hold') for x in data['results']):raise SystemExit(2)
if __name__=='__main__':main()
