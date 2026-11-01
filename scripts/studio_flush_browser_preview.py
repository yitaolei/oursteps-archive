#!/usr/bin/env python3
"""Flush verified Studio browser imports to incremental preview, without NAS."""
import fcntl,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]
from scripts.studio_local_authority import allow_local_authority
from oursteps.store import Store
from oursteps.history import flush_historical_previews
DATA=allow_local_authority(ROOT/'data')
with (DATA/'worker.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 store=Store(DATA)
 try: print('STUDIO_PREVIEW_FLUSH',flush_historical_previews(store),flush=True)
 finally:store.db.close()
