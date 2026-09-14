import _test_config  # Configure synthetic identity before application imports.
import sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from oursteps.sqlite_retry import WriterConnection,locked_retry
class LockTests(unittest.TestCase):
 def test_writer_lock_and_commit_retry(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=str(Path(tmp)/'test.db')
   db=sqlite3.connect(p,timeout=0.001,factory=WriterConnection)
   db.executescript('CREATE TABLE t(x); INSERT INTO t VALUES(1);')
   other=sqlite3.connect(p,timeout=0.001)
   other.execute('BEGIN IMMEDIATE');other.execute('INSERT INTO t VALUES(2)')
   with patch('oursteps.sqlite_retry.time.sleep',side_effect=lambda _:other.commit()) as wait:
    with db:db.execute('INSERT INTO t VALUES(3)')
    self.assertEqual(wait.call_count,1)
   other.execute('BEGIN');other.execute('SELECT * FROM t').fetchall()
   with patch('oursteps.sqlite_retry.time.sleep',side_effect=lambda _:other.rollback()) as wait:
    with db:db.executemany('INSERT INTO t VALUES(?)',[(4,),(5,)])
    self.assertEqual(wait.call_count,1)
   self.assertEqual(db.execute('SELECT x FROM t').fetchall(),[(1,),(2,),(3,),(4,),(5,)])
   db.close();other.close()
 def test_persistent_and_nonlock_errors(self):
  call=Mock(side_effect=sqlite3.OperationalError('database is locked'))
  with patch('oursteps.sqlite_retry.time.sleep'),self.assertRaises(sqlite3.OperationalError):locked_retry(call)
  self.assertEqual(call.call_count,5)
  call=Mock(side_effect=sqlite3.OperationalError('disk I/O error'))
  with self.assertRaises(sqlite3.OperationalError):locked_retry(call)
  self.assertEqual(call.call_count,1)
