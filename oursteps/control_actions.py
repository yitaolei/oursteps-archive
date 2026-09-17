"""Safe Control Center action queue primitives. No command execution here."""
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
import uuid

WEB_ACTIONS = {
    'incremental_sync',
    'guide_discovery_v2',
    'historical_backfill',
    'publish_public',
}
FINAL = {'succeeded', 'failed'}
ACTIVE = {'pending', 'running'}


def queue_dir(root):
    path = Path(root) / 'data/control-actions'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _atomic(path, data):
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            os.fchmod(handle.fileno(), 0o666)
            json.dump(data, handle, sort_keys=True, separators=(',', ':'))
            handle.write('\n')
            handle.flush(); os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def _jobs(root):
    result=[]
    for path in queue_dir(root).glob('*.json'):
        try:
            data=json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get('id') == path.stem:
            result.append(data)
    return sorted(result, key=lambda x: (x.get('created_at',0), x.get('id','')))


def status(root, limit=20):
    safe=[]
    for job in _jobs(root)[-limit:]:
        if job.get('action') not in WEB_ACTIONS: continue
        safe.append({k:job.get(k) for k in ('id','action','state','created_at','updated_at','message')})
    return safe


def request(root, action):
    if action not in WEB_ACTIONS: raise ValueError('unsupported action')
    q=queue_dir(root)
    lock=q/'queue.lock'
    with lock.open('a') as handle:
        st=os.fstat(handle.fileno())
        if st.st_uid == os.geteuid(): os.fchmod(handle.fileno(), 0o666)
        fcntl.flock(handle, fcntl.LOCK_EX)
        for job in _jobs(root):
            if job.get('action') == action and job.get('state') in ACTIVE:
                return job, False
        now=int(time.time()); ident=uuid.uuid4().hex
        job={'id':ident,'action':action,'state':'pending','created_at':now,'updated_at':now,'message':'Queued'}
        _atomic(q/(ident+'.json'), job)
        return job, True


def claim(root):
    q=queue_dir(root)
    with (q/'queue.lock').open('a') as handle:
        st=os.fstat(handle.fileno())
        if st.st_uid == os.geteuid(): os.fchmod(handle.fileno(), 0o666)
        fcntl.flock(handle, fcntl.LOCK_EX)
        for job in _jobs(root):
            if job.get('action') in WEB_ACTIONS and job.get('state') == 'pending':
                job['state']='running';job['updated_at']=int(time.time());job['message']='Running on Mac mini'
                _atomic(q/(job['id']+'.json'), job)
                return job
    return None


def finish(root, ident, success, message):
    if not isinstance(ident,str) or len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident):
        raise ValueError('invalid job id')
    path=queue_dir(root)/(ident+'.json')
    data=json.loads(path.read_text())
    if data.get('action') not in WEB_ACTIONS or data.get('state') != 'running': raise ValueError('job not running')
    text=' '.join(str(message).split())[:240]
    data.update(state='succeeded' if success else 'failed', updated_at=int(time.time()), message=text or ('Completed' if success else 'Failed'))
    _atomic(path,data)
    return data


def worker_status(root, now=None):
    now=int(time.time() if now is None else now)
    path=queue_dir(root)/'worker-status.json'
    fallback={'state':'UNKNOWN','task':'unknown','started_at':None,'elapsed_seconds':None,'next_historical_backfill':None,'sampled_at':None}
    try:
        data=json.loads(path.read_text())
    except (OSError,ValueError):
        return fallback
    allowed_states={'RUNNING','IDLE','UNKNOWN'}
    allowed_tasks={'historical_backfill','incremental_sync','guide_discovery','archive_worker','manual','idle','unknown'}
    state=data.get('state');task=data.get('task');sampled=data.get('sampled_at')
    if state not in allowed_states or task not in allowed_tasks or not isinstance(sampled,int): return fallback
    if now-sampled>90: state='UNKNOWN';task='unknown'
    def ivalue(key):
        value=data.get(key);return value if isinstance(value,int) and value>=0 else None
    return {'state':state,'task':task,'started_at':ivalue('started_at'),'elapsed_seconds':ivalue('elapsed_seconds'),'next_historical_backfill':ivalue('next_historical_backfill'),'sampled_at':sampled}


def write_worker_status(root, data):
    allowed_states={'RUNNING','IDLE','UNKNOWN'}
    allowed_tasks={'historical_backfill','incremental_sync','guide_discovery','archive_worker','manual','idle','unknown'}
    if not isinstance(data,dict) or data.get('state') not in allowed_states or data.get('task') not in allowed_tasks:
        raise ValueError('invalid worker status')
    clean={'state':data['state'],'task':data['task']}
    for key in ('started_at','elapsed_seconds','next_historical_backfill','sampled_at'):
        value=data.get(key)
        clean[key]=value if isinstance(value,int) and value>=0 else None
    if clean['sampled_at'] is None: raise ValueError('worker status timestamp required')
    q=queue_dir(root);path=q/'worker-status.json'
    _atomic(path,clean)
    path.chmod(0o644)
    return clean
