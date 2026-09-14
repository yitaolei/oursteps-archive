#!/usr/bin/env python3
"""Authenticated directory verification only. No thread fetch, queue changes or sync."""
import fcntl
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]
from oursteps.config import UID, USERNAME
from oursteps.store import Store, atomic_write
from oursteps.fetch import Fetcher
from oursteps.parser import directory_url, parse_discovery, soup_of
from oursteps.auth import identity
from oursteps.sync import dated_listing, sydney_today

def verify(candidate=None):
    if sys.platform=='darwin': raise RuntimeError('Run verification on NAS native filesystem')
    with open(ROOT/'data/worker.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        store=Store(ROOT/'data')
        try:
            fetch=Fetcher(store,saved_session=candidate);fetch.daily_now=True
            exact=f'https://www.oursteps.com.au/bbs/home.php?mod=space&uid={UID}&do=thread&view=me&from=space'
            counts=[]
            for url in (exact,directory_url()):
                snap=fetch.get(url)
                content=store.raw(snap)
                from oursteps.auth_verify import inspect_response
                inspect_response(content,200,url)
                parsed=dated_listing(content,url,sydney_today())
                if not parsed['threads']: raise RuntimeError('No threads enumerated')
                counts.append(len(parsed['threads']))
            result=dict(authentication='success',personal_page_accessible=True,uid=UID,username=USERNAME,first_page_threads=counts[0],sync_directory_threads=counts[1],today_enumeration='verification only; run sync-today for date-bounded enumeration')
            if candidate is not None:
                from scripts.session_handoff import install
                install(json.dumps(candidate).encode())
            atomic_write(ROOT/'data/auth-verification.json',json.dumps(result,ensure_ascii=False,indent=2).encode())
            return result
        finally: store.db.close()

if __name__=='__main__':
    try: print(json.dumps(verify(json.load(sys.stdin) if '--candidate' in sys.argv else None),ensure_ascii=False))
    except Exception as e:
        from oursteps.auth_verify import VerificationIssue
        from oursteps.parser import Busy,Blocked,ParseError
        from urllib.error import URLError
        category=e.category if isinstance(e,VerificationIssue) else ('retry_later' if isinstance(e,(Busy,URLError,TimeoutError,ConnectionError)) else ('permission_denied' if isinstance(e,Blocked) else 'unexpected_layout'))
        print(json.dumps(dict(authentication='verification_failed',category=category,error=type(e).__name__,reason=str(e))))
        sys.exit(2)
