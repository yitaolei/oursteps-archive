"""Bounded writer lock retries; keep transaction/commit semantics explicit."""
import logging
import random
import sqlite3
import time
from . import persistence_diagnostics as diag


def locked_retry(operation):
    for attempt in range(5):
        try:
            return operation()
        except sqlite3.OperationalError as error:
            if 'database is locked' not in str(error).lower() or attempt == 4:
                raise
            logging.warning('SQLite busy; retry %s/4', attempt+1)
            diag.add('sqlite_lock_retries')
            diag.call('sqlite_retry_sleep_seconds',time.sleep,min(8, 0.5*2**attempt)*random.uniform(1,1.3))


class DiagnosticCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=()):
        diag.sql(sql)
        before=self.connection.in_transaction
        try:return diag.call('sqlite_execute_seconds',super().execute,sql,parameters)
        finally:
            if not before and self.connection.in_transaction:diag.add('transactions_begun')

    def fetchone(self):return diag.call('sqlite_fetch_seconds',super().fetchone)
    def fetchall(self):return diag.call('sqlite_fetch_seconds',super().fetchall)
    def fetchmany(self, *args):return diag.call('sqlite_fetch_seconds',super().fetchmany,*args)
    def __next__(self):return diag.call('sqlite_fetch_seconds',super().__next__)


class WriterConnection(sqlite3.Connection):
    def cursor(self, *args, **kwargs):
        if diag.ACTIVE.get() is not None and not args and 'factory' not in kwargs:
            kwargs['factory']=DiagnosticCursor
        return super().cursor(*args,**kwargs)

    def execute(self, sql, parameters=()):
        if diag.ACTIVE.get() is not None:
            cursor=self.cursor()
            return locked_retry(lambda:cursor.execute(sql,parameters))
        return locked_retry(lambda: super(WriterConnection,self).execute(sql,parameters))

    def executemany(self, sql, parameters):
        # Retry the failed statement only, never replay an already applied prefix.
        cursor = self.cursor()
        for values in parameters:
            locked_retry(lambda: cursor.execute(sql,values))
        return cursor

    def executescript(self, script):
        # Match sqlite3's initial commit, then execute complete statements individually.
        # This avoids replaying a partially executed schema script after a lock.
        self.commit()
        isolation = self.isolation_level
        self.isolation_level = None
        try:
            pending = ''
            for char in script:
                pending += char
                if char == ';' and sqlite3.complete_statement(pending):
                    self.execute(pending)
                    pending = ''
            if pending.strip():
                self.execute(pending)
        finally:
            self.isolation_level = isolation
        return self.cursor()

    def commit(self):
        diag.add('commit_calls')
        active=self.in_transaction
        result=diag.call('sqlite_commit_seconds',locked_retry,lambda: super(WriterConnection,self).commit())
        if active:diag.add('commits')
        return result

    def rollback(self):
        active=self.in_transaction
        result=diag.call('sqlite_rollback_seconds',super().rollback)
        if active:diag.add('rollbacks')
        return result

    def __exit__(self, kind, value, traceback):
        if kind is not None:
            self.rollback()
        else:
            try:
                self.commit()
            except BaseException:
                self.rollback()
                raise
        return False
