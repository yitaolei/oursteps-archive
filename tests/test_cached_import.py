import _test_config  # Configure synthetic identity before application imports.
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parents[1]/'.deps')]
import test_reconcile as fixture
from oursteps import reconcile as r
from oursteps.store import Store
from oursteps.parser import parse_thread,thread_url
from oursteps.dates import publication_time

class CachedImportTest(unittest.TestCase):
    setUp=fixture.ReconcileTests.setUp
    tearDown=fixture.ReconcileTests.tearDown
    def test_multipage_import_equals_raw_parse_without_import_reparse(self):
        fixture.ReconcileTests.add(self)
        transport=fixture.Threads(self.cache)
        first,probe=r.verify_candidate(self.state,self.cache,transport,1902000)
        self.cache.seed([1902000],'reconciliation')
        self.cache.historical_mode=True
        bodies=[]
        for page in (1,2):
            url=thread_url(1902000,page)
            snap=probe if page==1 else transport.get(url)
            raw=self.cache.raw(snap);parsed=parse_thread(raw,url)
            self.cache.save_page(url,parsed,snap);bodies.append((url,raw,parsed))
        self.assertEqual(self.cache.db.execute('SELECT status FROM threads').fetchone()[0],'complete')
        with tempfile.TemporaryDirectory() as temp,patch('oursteps.store.time.time',return_value=123456):
            expected=Store(Path(temp));expected.historical_mode=True
            expected.seed([1902000],'reconciliation')
            for url,raw,parsed in bodies:
                snap=expected.snapshot(url,raw,mode='anonymous')
                expected.save_page(url,parsed,snap)
            with expected.db:
                expected.db.execute('INSERT INTO publication_times VALUES(?,?)',
                    (1902000,publication_time(first['posts'][0]['posted_at_raw'],bodies[0][1])))
            with patch.object(transport,'get',side_effect=AssertionError('network forbidden')),patch.object(r,'parse_thread',wraps=parse_thread) as parse:
                self.assertEqual(r.recover_one(self.state,self.cache,transport,1902000),'recovered')
                # Only the unchanged owner/publication probe parse remains; no import-page parse.
                self.assertEqual(parse.call_count,1)
            live=r.archive_db(self.root)
            for table in ('threads','jobs','pages','posts','revisions','links','assets','gaps','metrics','snapshots','publication_times'):
                actual=[tuple(row) for row in live.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                old=[tuple(row) for row in expected.db.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                self.assertEqual(actual,old,table)
            for snap in live.execute('SELECT path FROM snapshots'):
                self.assertEqual((self.root/'data'/snap['path']).read_bytes(),(expected.root/snap['path']).read_bytes())
            self.assertEqual(live.execute('SELECT status FROM threads').fetchone()[0],'complete')
            live.close();expected.db.close()
