"""Shared private missing-list tracking; no network and read-only archive access."""
import fcntl
import sqlite3
import re
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from .store import atomic_write


def extract_tid(value):
    value=value.strip()
    if re.fullmatch(r'(?:tid=)?[0-9]+',value,re.I):
        tid=int(value.split('=')[-1])
    else:
        u=urlparse(value);host=(u.hostname or '').lower()
        if u.scheme not in ('https','http') or not (host=='oursteps.com.au' or host.endswith('.oursteps.com.au')) or u.username or u.password:
            raise ValueError('invalid OurSteps TID/URL')
        values=parse_qs(u.query).get('tid',[])
        m=re.search(r'(?:^|/)thread-([0-9]+)(?:-|\.html|/|$)',u.path,re.I)
        text=values[0] if values else (m[1] if m else '')
        if not re.fullmatch('[0-9]+',text):raise ValueError('TID missing from URL')
        tid=int(text)
    if not 0<tid<2**63:raise ValueError('invalid TID range')
    return tid


def archive_db(root):
    db=sqlite3.connect((root/'data/archive.sqlite3').resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
    db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
    return db


def local_state(root,db,tid):
    inv=db.execute('SELECT 1 FROM inventory WHERE tid=?',(tid,)).fetchone()
    t=db.execute('SELECT status,discovered_via FROM threads WHERE tid=?',(tid,)).fetchone()
    posts=db.execute('SELECT count(*) FROM posts WHERE tid=?',(tid,)).fetchone()[0]
    preview=(root/'data/preview'/('%s.html'%tid)).is_file()
    full=(root/'public-site/current'/('%s.html'%tid)).is_file()
    recent=(root/'public-site/current/recent-1y'/('%s.html'%tid)).is_file()
    category='archived' if full else ('complete_unpublished' if t and t['status']=='complete' else ('incomplete' if inv or t or posts or preview else 'missing'))
    return dict(tid=tid,inventory=bool(inv),status=t['status'] if t else '-',posts=posts,preview=preview,full=full,recent=recent,local_state=category,provenance=t['discovered_via'] if t else None)


def active_tids(root):
    path=root/'data/manual-missing-tids.txt'
    return {int(x) for x in path.read_text().splitlines() if x.isdigit()} if path.exists() else set()


def refresh(root,tids=None):
    """All inputs are validated before this function. Serialize add/resolve operations."""
    folder=root/'data'
    with (folder/'.manual-missing.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        active=active_tids(root);original=set(active)
        requested=list(dict.fromkeys(tids)) if tids is not None else sorted(active)
        db=archive_db(root)
        try:
            states=[local_state(root,db,t) for t in requested]
            # Always reconcile old active entries too, even when checking a new TID.
            all_states={s['tid']:s for s in states}
            all_states.update({t:local_state(root,db,t) for t in active-set(all_states)})
        finally:db.close()
        for tid,s in all_states.items():
            if s['full']:active.discard(tid)
            else:active.add(tid)
        added=active-original;resolved=original-active
        if added or resolved:
            history=folder/'manual-missing-history.tsv'
            old=history.read_text() if history.exists() else 'timestamp\taction\ttid\tstate\n'
            now=datetime.now().astimezone().isoformat(timespec='seconds')
            lines=''.join('%s\t%s\t%s\t%s\n'%(now,action,tid,all_states[tid]['local_state']) for action,items in [('ADD',added),('RESOLVED',resolved)] for tid in sorted(items))
            atomic_write(history,(old+lines).encode())
        atomic_write(folder/'manual-missing-tids.txt',''.join('%s\n'%t for t in sorted(active)).encode())
    return dict(entries=states,active=sorted(active),added=sorted(added),resolved=sorted(resolved))
