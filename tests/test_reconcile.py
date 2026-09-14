import _test_config  # Configure synthetic identity before application imports.
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'.deps')]
from oursteps.store import Store
from oursteps.history import prepare
from oursteps import reconcile as r
from oursteps.manual_missing import extract_tid,refresh,archive_db
from oursteps.parser import thread_url,query,Busy,ParseError
from oursteps.auth_verify import VerificationIssue
ROOT=Path(__file__).resolve().parents[1]
FORM=b'''<html><form action="search.php?mod=forum"><input name="srchuname"><input name="srchtxt">
<input type="hidden" name="searchsubmit" value="yes"><input type="radio" name="srchfilter" value="all" checked>
<input type="radio" name="before" value="" checked><input type="radio" name="before" value="1">
<input type="radio" name="ascdesc" value="desc" checked><select name="orderby"><option value="dateline">date</option></select>
<select name="srchfrom"><option value="0">all</option><option value="31536000">year</option><option value="86400">day</option></select>
<select name="srchfid[]"><option value="all">all</option><option value="43">news</option><option value="44">other</option></select></form></html>'''
P1=r.SEARCH+'&searchid=fixture&page=1';P2=r.SEARCH+'&searchid=fixture&page=2'

def page(tids,next_url=None,total=None,uid=424242):
    body='<html><p>共 %s 个结果</p>'%(len(tids) if total is None else total)
    for tid in tids:
        body+='<li><h3><a href="forum.php?mod=viewthread&tid=%s">林家血案 %s</a></h3><a href="home.php?uid=%s">archive_test_user</a><p>发表于 2017-1-2</p><a href="forum.php?mod=forumdisplay&fid=43">新闻</a></li>'%(tid,tid,uid)
    if not tids:body+='没有找到'
    if next_url:body+='<a href="'+next_url+'">下一页</a>'
    return (body+'</html>').encode()

class Search:
    def __init__(self,pages):self.pages=pages;self.calls=[]
    def search(self,url=r.FORM,data=None):
        self.calls.append((url,data))
        if url==r.FORM:return FORM,url
        value=self.pages[P1 if data else url]
        if isinstance(value,Exception):raise value
        return value,P1 if data else url

class Threads:
    def __init__(self,cache,wrong=False,budget=False):self.cache=cache;self.calls=[];self.wrong=wrong;self.budget=budget
    def wait_window(self):pass
    def get(self,url):
        self.calls.append(url);q=query(url);n=int(q.get('page',1))
        if n==2 and self.budget:raise r.BudgetReached()
        body=(ROOT/'tests/fixtures'/('thread-first.html' if n==1 else 'thread-second.html')).read_bytes()
        body=body.replace(b'</body>',b'<div id="ft">GMT +10</div></body>')
        if self.wrong:body=body.replace(b'uid=424242',b'uid=9999')
        return self.cache.snapshot(thread_url(q['tid'],n),body,mode='anonymous')

class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        live=Store(self.root/'data');prepare(live);live.db.close()
        self.state=r.State(self.root);self.cache=Store(self.root/'data/reconcile-cache')
    def tearDown(self):self.cache.db.close();self.state.db.close();self.tmp.cleanup()
    def test_tid_inputs(self):
        inputs=['123','tid=123','https://www.oursteps.com.au/bbs/forum.php?mod=viewthread&tid=123&extra=&page=2','https://oursteps.com.au/bbs/thread-123-1-1.html']
        self.assertEqual([extract_tid(x) for x in inputs],[123]*4)
        for bad in ['0','-1','https://oursteps.com.au.evil/bbs/?tid=123','https://evil/?tid=123','hello']:
            with self.assertRaises(ValueError):extract_tid(bad)
    def test_missing_add_dedupe_remove(self):
        first=refresh(self.root,[1,2,1]);self.assertEqual(first['added'],[1,2])
        self.assertEqual(refresh(self.root,[1])['added'],[])
        (self.root/'public-site/current').mkdir(parents=True);(self.root/'public-site/current/1.html').write_text('full')
        result=refresh(self.root);self.assertEqual(result['active'],[2]);self.assertEqual(result['resolved'],[1])
        self.assertEqual(len((self.root/'data/manual-missing-history.tsv').read_text().splitlines()),4)
    def test_parser_owner_metadata_and_total(self):
        rows,nxt,cap,total=r.search_page(page([1],P2,74),P1)
        self.assertEqual(total,74);self.assertFalse(cap);self.assertEqual(nxt,P2)
        self.assertEqual(rows[0]['forum'],'新闻');self.assertEqual(rows[0]['created_at_raw'],'2017-1-2')
        with self.assertRaises(ParseError):r.search_page(page([1],uid=999),P1)
    def test_live_result_header_and_display_date(self):
        body=page([1],total=74).decode().replace('共 74 个结果','结果: 找到 “ 林家血案 ” 相关内容 74 个')
        body=body.replace('发表于 2017-1-2','2017-1-2 12:30 - <a href="home.php?uid=424242">archive_test_user</a>')
        rows,_,_,total=r.search_page(body.encode(),P1)
        self.assertEqual(total,74);self.assertEqual(rows[0]['displayed_date'],'2017-1-2 12:30')
    def test_retry_after_not_shortened(self):
        with self.state.db:self.state.shard('sweep','')
        r.record_error(self.state,'shards',1,VerificationIssue('retry_later','http_429',True,10000),clock=lambda:100)
        self.assertEqual(float(self.state.setting('pause_until')),10100)
    def test_expired_search_keeps_discoveries(self):
        search=Search({P1:page([1],P2),P2:r.SearchExpired('search_id_expired')})
        self.assertEqual(r.audit(self.state,search,'sweep',limit=20),'retry_later')
        self.assertIsNone(self.state.db.execute('SELECT page_url FROM shards').fetchone()[0])
        self.assertEqual(self.state.db.execute('SELECT tid FROM candidates').fetchone()[0],1)
    def test_audit_verification_never_imports(self):
        self.add();r.verify_pending(self.state,self.cache,Threads(self.cache),1)
        self.assertEqual(self.state.db.execute('SELECT ownership FROM candidates').fetchone()[0],'verified')
        db=archive_db(self.root);self.assertEqual(db.execute('SELECT count(*) FROM threads').fetchone()[0],0);db.close()
    def test_tea_and_retry(self):
        body='<html>新足迹友情提示: 现在是喝茶时间, 请冲杯茶，马上就好. 网页将于15秒后自动刷新</html>'.encode()
        with self.assertRaises(Busy):r.search_page(body,P1)
        with self.state.db:self.state.shard('keyword','林家血案')
        for expected in [1060,1120,1240]:
            r.record_error(self.state,'shards',1,Busy('site_busy'),clock=lambda:1000)
            self.assertEqual(float(self.state.setting('pause_until')),expected)
        self.assertEqual(int(self.state.setting('tea_busy_pauses')),3)
        self.assertTrue(r.transient(TimeoutError()));self.assertTrue(r.transient(ConnectionResetError()))
        self.assertTrue(r.transient(VerificationIssue('retry_later','http_429',True,300)))
    def test_multipage_dedupe_limit_and_resume(self):
        search=Search({P1:page([1,2],P2,4),P2:page([2,3],None,4)})
        self.assertEqual(r.audit(self.state,search,'keyword','林家血案',1),'result_limit_reached')
        self.assertEqual(self.state.db.execute('SELECT offset FROM shards').fetchone()[0],1)
        self.state.db.close();self.state=r.State(self.root)
        r.audit(self.state,search,'keyword','林家血案',10)
        self.state.compare();report=self.state.summary('keyword','林家血案')
        self.assertEqual(report['tids'],[1,2,3]);self.assertEqual(report['search_results_checked'],4)
        self.assertTrue(report['coverage_complete'])
        calls=len(search.calls);r.audit(self.state,search,'keyword','林家血案',10);self.assertEqual(len(search.calls),calls)
        db=archive_db(self.root)
        self.assertEqual(db.execute('SELECT count(*) FROM threads').fetchone()[0],0);db.close()
    def test_retry_resumes_next_page(self):
        search=Search({P1:page([1],P2),P2:Busy('site_busy')})
        self.assertEqual(r.audit(self.state,search,'sweep',limit=20),'retry_later')
        self.assertEqual(self.state.db.execute('SELECT page_url FROM shards').fetchone()[0],P2)
        with self.state.db:self.state.set('pause_until',0);self.state.db.execute('UPDATE shards SET retry_at=0')
        search.pages[P2]=page([2]);r.audit(self.state,search,'sweep',limit=20)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM candidates').fetchone()[0],2)
        self.assertEqual(sum(data is not None for _,data in search.calls),1)
    def test_supported_split_and_unresolved_remainder(self):
        with self.state.db:
            self.state.shard('sweep','')
            r.split_shard(self.state,self.state.db.execute('SELECT * FROM shards WHERE id=1').fetchone(),[43,44],[31536000,86400])
            child=self.state.db.execute("SELECT * FROM shards WHERE fid='43'").fetchone()
            r.split_shard(self.state,child,[43,44],[31536000,86400])
            older=self.state.db.execute("SELECT * FROM shards WHERE before_flag='1'").fetchone()
            r.split_shard(self.state,older,[43,44],[31536000,86400])
        self.assertEqual(self.state.db.execute('SELECT state FROM shards WHERE id=?',(older['id'],)).fetchone()[0],'unresolved_limited')
        self.assertFalse(self.state.summary()['coverage_complete'])
    def test_form_options_not_guessed(self):
        with self.state.db:self.state.shard('sweep','')
        shard=self.state.db.execute('SELECT * FROM shards').fetchone()
        fields,forums,ranges=r.search_fields(FORM,shard)
        self.assertEqual(fields['srchuname'],'archive_test_user');self.assertEqual(fields['srchfid[]'],'all')
        with self.assertRaises(ParseError):r.search_fields(FORM.replace(b'value="dateline"',b'value="unknown"'),shard)
    def test_locks(self):
        with r.locked(self.root/'data/reconcile.lock'):
            with self.assertRaises(BlockingIOError):
                with r.locked(self.root/'data/reconcile.lock'):pass
    def add(self,tid=1902000):
        with self.state.db:self.state.add_candidate(dict(tid=tid),'fixture')
    def test_wrong_owner_never_imported(self):
        self.add()
        with self.assertRaisesRegex(ParseError,'thread_not_owned'):
            r.recover_one(self.state,self.cache,Threads(self.cache,wrong=True),1902000)
        db=archive_db(self.root);self.assertEqual(db.execute('SELECT count(*) FROM threads').fetchone()[0],0);db.close()
    def test_recovery_reuses_pages_no_images_and_idempotent(self):
        self.add();transport=Threads(self.cache)
        with patch('oursteps.media.Transport',side_effect=AssertionError('no binary downloads')):
            self.assertEqual(r.recover_one(self.state,self.cache,transport,1902000),'recovered')
        db=archive_db(self.root)
        self.assertEqual(db.execute('SELECT status FROM threads').fetchone()[0],'complete')
        self.assertEqual({x[0] for x in db.execute('SELECT pid FROM posts')},{101,103,104})
        self.assertEqual(db.execute('SELECT count(*) FROM publication_times').fetchone()[0],1);db.close()
        calls=len(transport.calls);self.assertEqual(r.recover_one(self.state,self.cache,transport,1902000),'already_archived');self.assertEqual(len(transport.calls),calls)
        self.assertFalse((self.root/'public-site').exists())
    def test_recovery_budget_checkpoint(self):
        self.add();transport=Threads(self.cache,budget=True)
        with self.assertRaises(r.BudgetReached):r.recover_one(self.state,self.cache,transport,1902000)
        db=archive_db(self.root);self.assertEqual(db.execute('SELECT count(*) FROM threads').fetchone()[0],0);db.close()
        transport.budget=False;self.assertEqual(r.recover_one(self.state,self.cache,transport,1902000),'recovered')
    def test_existing_incomplete_not_retried(self):
        self.add();live=Store(self.root/'data');live.seed([1902000],'old-history');live.db.close()
        transport=Threads(self.cache)
        self.assertEqual(r.recover_one(self.state,self.cache,transport,1902000),'existing_incomplete');self.assertEqual(transport.calls,[])
    def test_backfill_limit_and_rejected_state(self):
        self.add();r.backfill(self.state,self.cache,Threads(self.cache,wrong=True),1)
        self.assertEqual(self.state.db.execute('SELECT ownership FROM candidates').fetchone()[0],'rejected')
        for tid in (2,3,4):self.add(tid)
        with patch.object(r,'recover_one',return_value='existing_incomplete') as recover:
            r.backfill(self.state,self.cache,Threads(self.cache),2)
            self.assertEqual(recover.call_count,2)
    def test_capped_search_splits_without_unlimited_fetch(self):
        search=Search({P1:page([1,2],P2,500)})
        r.audit(self.state,search,'sweep',limit=2)
        self.assertEqual(self.state.db.execute('SELECT state FROM shards WHERE id=1').fetchone()[0],'split')
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM shards').fetchone()[0],3)
        self.assertEqual(len(search.calls),2)
    def test_portable_install_and_safe_argument_transport(self):
        out=self.root/'bin';env=dict(os.environ,OURSTEPS_MAC_BIN=str(out))
        subprocess.run(['sh',str(ROOT/'scripts/mac-tools/install_mac_tools.sh')],env=env,check=True,capture_output=True)
        for name in ['check-oursteps','show-missing','reconcile-oursteps']:
            self.assertFalse((out/name).is_symlink());self.assertEqual((out/name).read_bytes(),(ROOT/'scripts/mac-tools'/name).read_bytes())
        import runpy
        args=['123','https://www.oursteps.com.au/bbs/forum.php?mod=viewthread&tid=234','$(must-not-run)']
        for name,values in [('check-oursteps',args),('reconcile-oursteps',['--keyword','林家 血案'])]:
            loaded=runpy.run_path(str(out/name),run_name='fixture')
            with patch.object(sys,'argv',[str(out/name)]+values),patch('subprocess.run') as run:
                run.return_value.returncode=0;self.assertEqual(loaded['main'](),0)
                payload=json.loads(run.call_args.kwargs['input'])
                self.assertEqual(payload['args'] if name=='check-oursteps' else payload,values)
                self.assertNotIn('$(must-not-run)',run.call_args.args[0][-1])

if __name__=='__main__':unittest.main()
