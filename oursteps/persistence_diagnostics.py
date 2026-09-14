"""Opt-in submetrics inside snapshot/save_page only. No SQL text or data retained."""
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
import time

ACTIVE = ContextVar('persistence_diagnostic', default=None)


def add(key, value=1):
    perf=ACTIVE.get()
    if perf is not None:perf.add('persistence_'+key,value)


@contextmanager
def timed(key):
    if ACTIVE.get() is None:
        yield
        return
    start=time.monotonic()
    try:yield
    finally:add(key,time.monotonic()-start)


def call(key, fn, *args, **kwargs):
    with timed(key):return fn(*args,**kwargs)


def section(name):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            add(name+'_calls')
            with timed(name+'_seconds'):return fn(*args,**kwargs)
        return wrapped
    return decorate


def sql(sql):
    # Categorize fixed SQL structure only; never retain interpolated statement contents.
    words=sql.strip().upper().split()
    op=words[0] if words else 'EMPTY'
    add('sql_statements')
    add('sql_'+op.lower()+'_statements')
    if op in ('INSERT','UPDATE','DELETE','REPLACE'):add('dml_statements')
    if op in ('CREATE','DROP','REINDEX') and ('INDEX' in words or op=='REINDEX'):add('index_ddl_statements')
    if op=='PRAGMA' and 'WAL_CHECKPOINT' in sql.upper():add('explicit_wal_checkpoints')
