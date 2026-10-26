#!/usr/bin/env python3
"""Conservative real-Chrome historical lane; never touches the HTTP 403 halt.

Source selection is NAS read-only and inventory-scoped. Browser fallback checks
navigation identity, authenticated content, robots and per-page denial before a
NAS-native import. A failed TID is held locally, not re-tried in a loop.
"""
import argparse,fcntl,json,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from oursteps.deployment import deployment,remote_python,ssh_argv
from oursteps.parser import query
from scripts.process_utils import run_group,install_signal_cleanup
install_signal_cleanup()
class BrowserSiteHold(RuntimeError):
    pass

STATE=Path.home()/'Library/Application Support/OurSteps/control-plane/data/historical-browser-lane-state.json'
LOCK=Path.home()/'.local/share/oursteps-runtime/historical-browser-lane.lock'

def remote_json(*args,timeout=60):
    host,_=deployment()
    result=run_group(ssh_argv(host)+[remote_python(*args)],text=True,timeout=timeout)
    if result.returncode:raise RuntimeError('NAS candidate query failed')
    return json.loads(result.stdout.strip().splitlines()[-1])

def saved():
    try:return json.loads(STATE.read_text())
    except (FileNotFoundError,ValueError):return {'failed':{}}

def write_state(state):
    STATE.parent.mkdir(parents=True,exist_ok=True)
    tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(state,sort_keys=True)+'\n');tmp.replace(STATE)

def record(state,tid,reason):
    state.setdefault('failed',{})[str(tid)]={'at':int(time.time()),'reason':str(reason)[:120]}
    state.pop('active_tid',None)
    write_state(state)

def batch(urls,follow_tid=None,follow_limit=0):
    if not urls:return {'status':'empty','complete':0,'imported':0}
    if len(urls)>12:raise ValueError('pilot batch limit')
    runtime=Path.home()/'.local/share/oursteps-runtime/bin/python'
    cmd=[str(runtime),str(ROOT/'scripts/browser_fallback.py')]
    if follow_tid is not None:cmd+=['--follow-tid',str(follow_tid),'--follow-limit',str(follow_limit)]
    result=run_group(cmd+list(urls),text=True,timeout=900)
    try:report=json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError,ValueError):raise RuntimeError('browser result not valid JSON')
    stop=str(report.get('stop_reason') or '').lower()
    if 'manual_required' in stop or 'http_403' in stop or 'http_429' in stop:
        raise BrowserSiteHold('browser_site_access_restricted')
    if report.get('handoff_error') and report.get('imported')==len(urls) and not report.get('stop_reason') and not report.get('failed'):
        # Import ACKs are durable; an unrelated session handoff must not hide them.
        report['handoff_warning']=True
    elif result.returncode or report.get('stop_reason') or report.get('handoff_error') or report.get('failed'):
        detail={'stage':'browser_batch','exit_code':result.returncode,'imported':report.get('imported',0),'failed':report.get('failed',0),'stop_kind':str(report.get('stop_reason') or '')[:100], 'handoff':bool(report.get('handoff_error')), 'failure_kinds':[str(x.get('error','')).split(':',1)[0][:50] for x in report.get('failures',[]) if isinstance(x,dict)][:12]}
        raise RuntimeError('browser_batch_diagnostic:'+json.dumps(detail,separators=(',',':')))
    if report.get('imported',0)<len(urls):raise RuntimeError('browser batch incomplete')
    return report

def choose(state,limit=100):
    active=state.get('active_tid')
    if isinstance(active,int) and str(active) not in state.get('failed',{}):
        pending=remote_json('scripts/historical_browser_candidates.py','--tid',str(active),'--limit','1')['items']
        if pending:return {'tid':active,'page':0,'url':pending[0]['url']}
    # New inventory is the reliable throughput lane; older partial review
    # cases are a fallback and must not monopolize every scheduled run.
    for mode in (None,'--resume'):
        args=['scripts/historical_browser_candidates.py','--limit',str(limit)]
        if mode:args.append(mode)
        for item in remote_json(*args)['items']:
            if str(item['tid']) not in state.get('failed',{}):return item
    return None

def choose_multi(state,limit=5):
    """Select distinct never-started TIDs for one bounded Chrome visit sequence."""
    if not 1<=limit<=5:raise ValueError('multi batch limit 1..5')
    if state.get('active_tid') or state.get('site_hold'):return []
    entries=remote_json('scripts/historical_browser_candidates.py','--limit','100')['items']
    result=[];seen=set()
    for entry in entries:
        tid=entry['tid']
        if tid in seen or str(tid) in state.get('failed',{}):continue
        if entry['page']!=1:continue
        result.append(entry);seen.add(tid)
        if len(result)==limit:break
    return result

def apply_multi_result(state,chosen,report):
    """Checkpoint per-TID ACKs, without calling this a completed batch on failure."""
    chosen_ids={int(x['tid']) for x in chosen}
    succeeded=set();completed=set()
    for item in report.get('items',[]):
        tid=int(query(item['url'])['tid'])
        if tid not in chosen_ids:raise RuntimeError('multi_result_unselected_tid')
        succeeded.add(tid)
        if item.get('thread_status')=='complete':completed.add(tid)
    for item in report.get('failures',[]):
        url=item.get('url')
        if not url:continue
        tid=int(query(url)['tid'])
        if tid not in chosen_ids:raise RuntimeError('multi_failure_unselected_tid')
        state.setdefault('failed',{})[str(tid)]={'at':int(time.time()),'reason':'browser_page_failed'}
    if completed:state['publish_pending']=True
    write_state(state)
    return {'imported':len(succeeded),'complete':len(completed),'failed':len(state.get('failed',{}))}

def publish_release():
    host,_=deployment()
    for command in ('scripts/publish_public.py','scripts/public_healthcheck.py'):
        result=run_group(ssh_argv(host)+[remote_python(command)],text=True,timeout=1200)
        if result.returncode:raise RuntimeError('public release or healthcheck failed')


def main():
    if sys.platform!='darwin':raise SystemExit('Browser lane must run on Mac Studio')
    p=argparse.ArgumentParser();p.add_argument('--max-pages',type=int,default=5);p.add_argument('--defer-publish',action='store_true');a=p.parse_args()
    if not 1<=a.max_pages<=12:p.error('max pages must be 1..12')
    LOCK.parent.mkdir(parents=True,exist_ok=True)
    with LOCK.open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'status':'skipped','reason':'lane_already_running'}));return
        state=saved()
        if state.get('site_hold'):
            print(json.dumps({'status':'site_hold','reason':'prior_browser_access_denial'}));return
        # Do not start a browser importer while any other NAS writer owns the lock.
        probe=run_group([sys.executable,str(ROOT/'scripts/archive_worker_status.py'),'--guard','--json'],text=True,timeout=45)
        if probe.returncode==75:
            print(json.dumps({'status':'skipped','reason':'archive_worker_busy'}));return
        if probe.returncode:
            raise RuntimeError('archive worker state unavailable')
        item=choose(state)
        if item is None:
            print(json.dumps({'status':'no_eligible_inventory'}));return
        tid=item['tid'];count=0;complete=0
        try:
            if item['page']==1:
                r=batch([item['url']],follow_tid=tid,follow_limit=a.max_pages-1);count+=r['imported'];complete+=r['complete']
            if count<a.max_pages and not complete:
                following=remote_json('scripts/historical_browser_candidates.py','--tid',str(tid),'--limit',str(a.max_pages-count))['items']
                if following:
                    r=batch([x['url'] for x in following]);count+=r['imported'];complete+=r['complete']
            if complete:
                state.pop('active_tid',None)
                state['publish_pending']=True
                write_state(state)
                if not a.defer_publish:
                    publish_release()
                    state['publish_pending']=False
                    write_state(state)
            if not complete:
                state['active_tid']=tid
                write_state(state)
            summary={'status':'completed','tid':tid,'pages_imported':count,'completed_pages':complete,'published':bool(complete and not a.defer_publish),'publish_pending':bool(complete and a.defer_publish),'at':int(time.time())}
            state['last_result']=summary
            try:
                state['inventory_counts']=remote_json('scripts/historical_browser_candidates.py','--counts')['counts']
            except (RuntimeError,ValueError,KeyError):
                pass  # The committed import and release remain durable.
            write_state(state)
            print(json.dumps(summary,separators=(',',':')))
        except Exception as exc:
            if isinstance(exc,BrowserSiteHold):
                state['site_hold']={'at':int(time.time()),'reason':'browser_site_access_restricted'}
            record(state,tid,type(exc).__name__+':'+str(exc))
            print('HISTORICAL_LANE_ERROR '+json.dumps({'tid':tid,'pages_committed_before_error':count,'exception':str(exc)[:700]},separators=(',',':')),file=sys.stderr,flush=True)
            print(json.dumps({'status':'held','tid':tid,'imported_before_error':count,'reason':type(exc).__name__},separators=(',',':')))
            raise SystemExit(2)
if __name__=='__main__':main()
