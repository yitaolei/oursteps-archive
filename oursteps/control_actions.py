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
