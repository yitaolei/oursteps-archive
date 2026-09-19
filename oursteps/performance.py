"""Opt-in reconciliation timing. Reports contain counts/TIDs, never URLs or content."""
from contextlib import contextmanager
from functools import wraps
import time


class Performance:
    def __init__(self):
        self.values = {}
        self.pages = set()

    def add(self, key, value=1):
        self.values[key] = self.values.get(key, 0) + value

    @contextmanager
    def timed(self, key):
        start = time.monotonic()
        try:
            yield
        finally:
            self.add(key, time.monotonic() - start)

    def report(self):
        keys = ('network_fetches', 'thread_page_fetches', 'repeated_page_fetches',
                'successful_page_refetches', 'network_errors', 'network_timeouts', 'prior_snapshot_fetches', 'network_seconds',
                'throttle_seconds', 'pacing_base_seconds', 'pacing_adaptive_seconds', 'backoff_seconds',
                'parse_seconds', 'persistence_seconds', 'cached_page_uses', 'fetch_seconds')
        result={key: round(self.values.get(key, 0), 6) for key in keys}
        result.update({key:round(value,6) for key,value in self.values.items() if key.startswith('persistence_')})
        # Disjoint timed components; inclusive refresh/snapshot/save_page are separate views.
        if 'persistence_sqlite_execute_seconds' in result:
            used=sum(result.get(key,0) for key in ('persistence_sqlite_execute_seconds','persistence_sqlite_fetch_seconds','persistence_sqlite_commit_seconds','persistence_sqlite_rollback_seconds','persistence_atomic_file_seconds'))
            result['persistence_other_seconds']=round(max(0,result['persistence_seconds']-used),6)
        return result


def measured(key):
    def decorate(fn):
        @wraps(fn)
        def wrapped(store, *args, **kwargs):
            if key=='persistence_seconds' and getattr(store,'performance',None) is not None:
                from .persistence_diagnostics import ACTIVE, add, timed
                token=ACTIVE.set(store.performance)
                try:
                    add(fn.__name__+'_calls')
                    with timed(fn.__name__+'_seconds'):
                        return measure(store,key,fn,store,*args,**kwargs)
                finally:ACTIVE.reset(token)
            return measure(store, key, fn, store, *args, **kwargs)
        return wrapped
    return decorate


def measure(store, key, fn, *args, **kwargs):
    perf = getattr(store, 'performance', None)
    if perf is None:
        return fn(*args, **kwargs)
    with perf.timed(key):
        return fn(*args, **kwargs)
