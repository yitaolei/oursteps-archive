"""Offline fixtures only: no Chrome, SSH, NAS or site requests."""
import _test_config
import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from oursteps import preview
from oursteps.history import backfill, flush_historical_previews, prepare
from oursteps.parser import thread_url
from oursteps.store import Store
from scripts import browser_fallback as browser
from scripts import browser_fallback_candidates as candidates
from scripts import import_browser_snapshot as importer
from scripts.browser_snapshot_stream import InfrastructureStop, MAX_BYTES, PROTOCOL, StreamImporter, json_line

TID=1902000
URLS=[thread_url(TID,page) for page in (1,2)]


def content(page):
    name='thread-first.html' if page==1 else 'thread-second.html'
    raw=(Path(__file__).parent/'fixtures'/name).read_bytes()
    header=b'<div id="um"><a href="home.php?mod=space&amp;uid=424242">archive_test_user</a></div>'
    return raw.replace(b'<body>',b'<body>'+header)


def frame(number,page,**changes):
    raw=content(page)
    header=dict(protocol=PROTOCOL,seq=number,url=URLS[page-1],length=len(raw))
    header.update(changes)
    return json_line(header)+raw


class StoreStreamTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=Store(self.root/'data');self.addCleanup(self.store.db.close)
        prepare(self.store)
        self.store.seed([TID],'fixture')
        with self.store.db:
            self.store.db.execute('INSERT INTO inventory VALUES(?,0,0)',(TID,))
            self.store.db.execute("INSERT INTO jobs(url,kind,tid,page) VALUES(?,'thread',?,2)",(URLS[1],TID))
            self.store.db.execute("UPDATE jobs SET state='retry_later',error='retry_later:incomplete_html'")

    def run_stream(self,raw,sink=None):
        sink=sink or io.BytesIO()
        code=importer.serve_stream(self.store,io.BytesIO(raw),sink)
        return code,[json.loads(line) for line in sink.getvalue().splitlines()]

    def test_multiple_binary_frames_ack_only_after_commit(self):
        db_path=self.store.root/'archive.sqlite3'
        class CommittedSink(io.BytesIO):
            def flush(sink):
                ack=json.loads(sink.getvalue().splitlines()[-1])
                if ack['status']=='imported':
                    with sqlite3.connect(db_path) as db:
                        self.assertEqual(db.execute('SELECT state FROM jobs WHERE url=?',(ack['url'],)).fetchone()[0],'success')
                        self.assertEqual(db.execute('SELECT count(*) FROM pages').fetchone()[0],ack['seq'])
        code,lines=self.run_stream(frame(1,1)+frame(2,2),CommittedSink())
        self.assertEqual(code,0)
        self.assertEqual([x['status'] for x in lines],['ready','imported','imported'])
        self.assertEqual([x['thread_status'] for x in lines[1:]],['partial','complete'])
        self.assertEqual(self.store.setting('historical_preview_pending_tids'),f'[{TID}]')
        self.assertFalse(self.store.db.in_transaction)

    def test_invalid_frames_fail_closed_after_prior_commit(self):
        invalid=[frame(2,2,seq=True),frame(3,2),frame(2,2,protocol=2),
                 frame(2,2,protocol=True),frame(2,2,url='https://example.org/'),
                 frame(2,2,url=URLS[1]+'&extra='),frame(2,2,url=5),
                 frame(2,2,length=MAX_BYTES+1),frame(2,2,length=0),
                 frame(2,2,length=True),frame(2,2,length=1.5),
                 b'{bad}\n',b'x'*8193+b'\n',frame(2,2)[:-5]]
        for bad in invalid:
            with self.subTest(bad=bad[:120]):
                # Each protocol case starts from the same due challenge state.
                with self.store.db:
                    self.store.db.execute("UPDATE jobs SET state='retry_later',error='retry_later:incomplete_html' WHERE url=?",(URLS[0],))
                code,lines=self.run_stream(frame(1,1)+bad)
                self.assertEqual(code,2)
                self.assertEqual(lines[-1]['status'],'error')
                self.assertEqual(self.store.db.execute('SELECT count(*) FROM pages').fetchone()[0],1)
        with self.store.db:
            self.store.db.execute("UPDATE jobs SET state='retry_later',error='retry_later:incomplete_html' WHERE url=?",(URLS[0],))
        code,lines=self.run_stream(frame(1,1)+frame(2,2,length=MAX_BYTES+1)+frame(3,2))
        self.assertEqual(code,2)
        self.assertEqual(len(lines),3)  # Does not consume a later valid frame.

    def test_import_failures_stop_without_ack_or_later_page(self):
        for error in (sqlite3.OperationalError('fixture database failure'),OSError('fixture disk failure')):
            with self.subTest(error=type(error).__name__),patch.object(self.store,'save_page',side_effect=error):
                code,lines=self.run_stream(frame(1,1)+frame(2,2))
                self.assertEqual(code,2)
                self.assertEqual([x['status'] for x in lines],['ready','error'])
                self.assertEqual(self.store.db.execute('SELECT count(*) FROM pages').fetchone()[0],0)

    def test_inventory_scope_and_identity_remain_required(self):
        with self.store.db:self.store.db.execute('DELETE FROM inventory')
        code,lines=self.run_stream(frame(1,1))
        self.assertEqual(code,2)
        self.assertIn('not_due_historical_challenge',lines[-1]['error'])
        with self.store.db:
            self.store.db.execute('INSERT INTO inventory VALUES(?,0,0)',(TID,))
            self.store.db.execute("UPDATE jobs SET state='pending',error=NULL WHERE url=?",(URLS[0],))
        code,lines=self.run_stream(frame(1,1))
        self.assertEqual(code,2)
        self.assertIn('not_due_historical_challenge',lines[-1]['error'])
        with self.assertRaises(Exception):
            importer.save_snapshot(self.store,URLS[0],b'<html>no account</html>')

    def test_lost_ack_uses_committed_jobs_not_second_checkpoint(self):
        class BrokenAck(io.BytesIO):
            def write(sink,raw):
                if json.loads(raw)['status']=='imported':
                    raise BrokenPipeError('SSH lost after commit')
                return super().write(raw)
        code,_=self.run_stream(frame(1,1)+frame(2,2),BrokenAck())
        self.assertEqual(code,2)
        # Same query the next scheduler uses: committed page 1 is excluded.
        with patch.object(candidates,'ROOT',self.root),patch.object(sys,'platform','linux'):
            self.assertEqual(candidates.candidates(historical=True,limit=80),[URLS[1]])
        self.assertEqual(self.store.setting('historical_preview_pending_tids'),f'[{TID}]')

    def test_incremental_browser_import_creates_missing_parent_before_posts(self):
        with self.store.db:
            self.store.db.execute('DELETE FROM jobs')
            self.store.db.execute('DELETE FROM inventory')
            self.store.db.execute('DELETE FROM threads')
        result=importer.save_snapshot(self.store,URLS[0],content(1))
        self.assertEqual(result['status'],'imported')
        self.assertEqual(self.store.db.execute('SELECT discovered_via FROM threads WHERE tid=?',(TID,)).fetchone()[0],'browser-fallback')
        self.assertEqual(self.store.db.execute('SELECT state FROM jobs WHERE url=?',(URLS[0],)).fetchone()[0],'success')
        self.assertEqual(self.store.db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_legacy_single_page_import(self):
        with patch.object(sys,'platform','linux'):
            result=importer.import_snapshot(self.root,URLS[0],content(1))
        self.assertEqual(result['status'],'imported')
        self.assertEqual(result['thread_status'],'partial')

    def test_stream_cli_one_store_and_lock_for_entire_session(self):
        store=self.store
        class LockedInput(io.BytesIO):
            def readline(source,*args):
                with open(store.root/'worker.lock','a') as probe:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
                return super().readline(*args)
        sink=io.BytesIO()
        source=LockedInput(frame(1,1)+frame(2,2))
        with patch.object(importer,'ROOT',self.root),patch.object(sys,'platform','linux'),patch.object(sys,'stdin',types.SimpleNamespace(buffer=source)),patch.object(sys,'stdout',types.SimpleNamespace(buffer=sink)),patch.object(importer,'Store',wraps=Store) as opened:
            self.assertEqual(importer.main(['--stream']),0)
            opened.assert_called_once_with(self.root/'data')
        with open(store.root/'worker.lock','a') as probe:
            fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)

    def test_stream_cli_busy_lock_fails_before_store_or_ready(self):
        sink=io.BytesIO()
        with open(self.store.root/'worker.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with patch.object(importer,'ROOT',self.root),patch.object(sys,'platform','linux'),patch.object(sys,'stdout',types.SimpleNamespace(buffer=sink)),patch.object(importer,'Store') as opened:
                self.assertEqual(importer.main(['--stream']),2)
                opened.assert_not_called()
        self.assertEqual(json.loads(sink.getvalue())['status'],'error')

    def test_explicit_preview_cli_refuses_active_worker(self):
        from oursteps import cli
        with open(self.store.root/'worker.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with patch.object(sys,'platform','linux'),patch.object(sys,'argv',['archive.py','--data',str(self.store.root),'build-preview','--historical-pending']),patch.object(cli,'Store') as opened,contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):cli.main()
                opened.assert_not_called()

    def test_local_client_and_real_importer_end_to_end(self):
        # Explicitly bypass NAS platform guard ONLY for this synthetic temp Store.
        # The child runs no SSH, Chrome, Fetcher, auth or other network operation.
        code="""import sys
from pathlib import Path
from scripts import import_browser_snapshot as importer
importer.ROOT=Path(sys.argv[1])
sys.platform='linux'
sys.exit(importer.main(['--stream']))
"""
        client=StreamImporter([sys.executable,'-B','-u','-c',code,str(self.root)],timeout=5).start()
        try:
            with open(self.store.root/'worker.lock','a') as probe:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
            for page in (1,2):
                ack=client.import_page(URLS[page-1],content(page))
                self.assertEqual(ack['seq'],page)
            self.assertEqual(ack['thread_status'],'complete')
            client.close()
            with open(self.store.root/'worker.lock','a') as probe:
                fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertEqual(self.store.db.execute("SELECT count(*) FROM jobs WHERE state='success'").fetchone()[0],2)
        finally:client.abort()

    def assert_held_open_timeout(self,phase):
        # A local importer child holds the real worker lock. The parent keeps
        # its pipe writer open until AFTER the child exits on its own deadline.
        code="""import sys
from pathlib import Path
from scripts import import_browser_snapshot as importer
importer.ROOT=Path(sys.argv[1])
sys.platform='linux'
serve=importer.serve_stream
importer.serve_stream=lambda *args: serve(*args,read_timeout=0.05)
sys.exit(importer.main(['--stream']))
"""
        partial=(b'{"protocol":' if phase=='header' else
                 json_line(dict(protocol=PROTOCOL,seq=2,url=URLS[1],length=10))+b'\x00\n\xff')
        read_fd,write_fd=os.pipe()
        try:
            with os.fdopen(read_fd,'rb') as source:
                proc=subprocess.Popen([sys.executable,'-B','-u','-c',code,str(self.root)],
                                      stdin=source,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                pending=memoryview(frame(1,1)+partial)
                while pending:
                    pending=pending[os.write(write_fd,pending):]
                stdout,stderr=proc.communicate(timeout=5)
                os.fstat(write_fd)  # Still open: EOF did not unblock the child.
                self.assertEqual(proc.returncode,2,stderr.decode())
                lines=[json.loads(line) for line in stdout.splitlines()]
                self.assertEqual([line['status'] for line in lines],['ready','imported','error'])
                self.assertEqual(lines[-1]['seq'],2)
                self.assertIn('stream_'+phase+'_idle_timeout',lines[-1]['error'])
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.communicate()
        finally:
            os.close(write_fd)
        with open(self.store.root/'worker.lock','a') as probe:
            fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM pages').fetchone()[0],1)
        self.assertEqual([row[0] for row in self.store.db.execute(
            'SELECT state FROM jobs ORDER BY page')],['success','retry_later'])
        self.assertEqual(self.store.setting('historical_preview_pending_tids'),f'[{TID}]')

    def test_partial_header_held_open_times_out_and_releases_lock(self):
        self.assert_held_open_timeout('header')

    def test_partial_body_held_open_times_out_and_releases_lock(self):
        self.assert_held_open_timeout('body')

    def test_flush_includes_older_complete_pending_and_retains_incomplete(self):
        with self.store.db:
            for tid,status in ((TID,'complete'),(TID+1,'complete'),(TID+2,'partial')):
                self.store.db.execute("INSERT INTO threads(tid,status,discovered_via) VALUES(?,?,'fixture') ON CONFLICT(tid) DO UPDATE SET status=excluded.status",(tid,status))
        self.store.set_setting('historical_preview_pending_tids',json.dumps([TID+2,TID+1,TID]))
        with patch('oursteps.preview_batch.build_batch',return_value=dict(tids=[TID,TID+1],rendered_articles=2)) as build:
            flush_historical_previews(self.store)
            build.assert_called_once_with(self.store,[TID,TID+1])
        self.assertEqual(self.store.setting('historical_preview_pending_tids'),f'[{TID+2}]')

    def test_defer_then_explicit_flush_renders_all_pending(self):
        preview.build(self.store)
        def work(store,**kwargs):
            for page in (1,2):
                importer.save_snapshot(store,URLS[page-1],content(page))
            return 'complete'
        with patch('oursteps.history.Fetcher'),patch('oursteps.cli.run',side_effect=work),patch('oursteps.preview_batch.build_batch',side_effect=AssertionError('must defer')):
            backfill(self.store,now=True,defer_preview=True)
        # Reopen to prove durable state rather than just in-memory tracking.
        other=Store(self.store.root)
        try:
            self.assertEqual(other.setting('historical_preview_pending_tids'),f'[{TID}]')
            result=flush_historical_previews(other)
            self.assertEqual(result['tids'],[TID])
            self.assertEqual(other.setting('historical_preview_pending_tids'),'[]')
            self.assertTrue((other.root/'preview'/f'{TID}.html').is_file())
        finally:other.db.close()

    def test_default_backfill_still_renders(self):
        preview.build(self.store)
        def work(store,**kwargs):
            for page in (1,2):importer.save_snapshot(store,URLS[page-1],content(page))
            return 'complete'
        with patch('oursteps.history.Fetcher'),patch('oursteps.cli.run',side_effect=work):
            backfill(self.store,now=True)
        self.assertEqual(self.store.setting('historical_preview_pending_tids'),'[]')
        self.assertTrue((self.store.root/'preview'/f'{TID}.html').is_file())


class StreamClientTests(unittest.TestCase):
    def client(self):
        client=StreamImporter(['unused'])
        client.proc=Mock();client.proc.poll.return_value=None
        client.proc.stdin.fileno.return_value=1
        return client

    def test_ready_mismatch_eof_timeout(self):
        for value in ({'protocol':PROTOCOL,'status':'wrong'},{'protocol':PROTOCOL,'status':'error','error':'worker_active'},InfrastructureStop('eof'),InfrastructureStop('ack_timeout')):
            client=StreamImporter(['unused'])
            read=Mock(side_effect=value) if isinstance(value,Exception) else Mock(return_value=value)
            with patch('scripts.browser_snapshot_stream._ACTIVE_ROOTS',set()),patch('scripts.browser_snapshot_stream.subprocess.Popen'),patch('scripts.browser_snapshot_stream.os.set_blocking'),patch.object(client,'_read',read),patch.object(client,'abort') as abort:
                with self.assertRaises(InfrastructureStop):client.start()
                abort.assert_called_once()
            # Mocked abort deliberately did not clean up this test's tempfile.
            client.stderr.close()

    def test_ack_mismatch_error_and_eof_are_infrastructure(self):
        base=dict(protocol=PROTOCOL,status='imported',seq=1,url=URLS[0],bytes=3,snapshot_id=1,thread_status='partial')
        values=[dict(base,seq=2),dict(base,seq=True),dict(base,url=URLS[1]),dict(base,import_seconds='bad'),dict(base,import_seconds=float('nan')),dict(base,status='error',error='SQLite failed'),InfrastructureStop('eof'),InfrastructureStop('ack_timeout')]
        for value in values:
            with self.subTest(value=value):
                client=self.client()
                read=Mock(side_effect=value) if isinstance(value,Exception) else Mock(return_value=value)
                with patch.object(client,'ensure_alive'),patch.object(client,'_read',read),patch('scripts.browser_snapshot_stream.select.select',return_value=([],[1],[])),patch('scripts.browser_snapshot_stream.os.write',side_effect=lambda fd,b:len(b)):
                    with self.assertRaises(InfrastructureStop):client.import_page(URLS[0],b'abc')
                    self.assertEqual(client.seq,1)  # Never resend.

    def test_real_local_pipes_multiple_acks_and_eof(self):
        # Local synthetic Python child only; exercises actual nonblocking pipes.
        code='''import sys,json
print(json.dumps(dict(protocol=1,status='ready',max_bytes=5242880)),flush=True)
for line in sys.stdin.buffer:
 h=json.loads(line);raw=sys.stdin.buffer.read(h['length'])
 print(json.dumps(dict(protocol=1,status='imported',seq=h['seq'],url=h['url'],bytes=len(raw),snapshot_id=h['seq'],thread_status='partial')),flush=True)
'''
        client=StreamImporter([sys.executable,'-u','-c',code],timeout=3).start()
        try:
            for url in URLS:self.assertEqual(client.import_page(url,b'raw\n\x00\xff')['status'],'imported')
            client.close()
        finally:client.abort()
        for code,reason in (("pass",'eof'),("import time;time.sleep(2)",'timeout'),("print('{bad}',flush=True)",'protocol')):
            client=StreamImporter([sys.executable,'-u','-c',code],timeout=.05)
            with self.assertRaises(InfrastructureStop):client.start()
            self.assertIsNone(client.proc)


class BrowserSessionTests(unittest.TestCase):
    def setUp(self):
        fake=types.ModuleType('playwright.sync_api');fake.Error=type('BrowserError',(Exception,),{})
        fake.TimeoutError=type('BrowserTimeoutError',(fake.Error,),{})
        self.browser_error=fake.Error;self.browser_timeout=fake.TimeoutError
        self.real_page_content=browser.page_content
        self.playwright=patch.dict(sys.modules,{'playwright':types.ModuleType('playwright'),'playwright.sync_api':fake})
        self.playwright.start();self.addCleanup(self.playwright.stop)
        self.page=Mock();self.context=Mock()
        self.client=Mock();self.client.timings={}
        self.client.import_page.side_effect=lambda url,raw:dict(snapshot_id=1,thread_status='complete')
        self.patches=[patch.object(browser,'StreamImporter',return_value=self.client),patch.object(browser,'deployment',return_value=('fixture','/fixture')),
            patch.object(browser,'ssh_argv',return_value=['ssh-mock']),patch.object(browser,'remote_python',return_value='unused'),
            patch.object(browser,'page_content',return_value=b'fixture'),patch.object(browser,'install_candidate'),patch.object(browser,'build_previews')]
        self.mocks=[p.start() for p in self.patches]
        for p in self.patches:self.addCleanup(p.stop)

    def session(self):return browser.session_result(self.page,self.context,{},URLS,historical=True)

    def test_one_session_serial_delay_close_then_handoff_and_no_preview(self):
        events=[]
        self.client.close.side_effect=lambda:events.append('close')
        self.mocks[-2].side_effect=lambda *a:events.append('handoff')
        result=self.session()
        self.assertEqual(result['attempted'],2);self.assertEqual(result['imported'],2)
        self.assertEqual(result['protocol_version'],PROTOCOL)
        self.mocks[0].assert_called_once();self.client.start.assert_called_once()
        self.page.wait_for_timeout.assert_called_once_with(2500)
        self.assertEqual(events,['close','handoff']);self.mocks[-1].assert_not_called()

    def test_infrastructure_failure_stops_site_and_handoff(self):
        self.client.import_page.side_effect=InfrastructureStop('ack_timeout')
        result=self.session()
        self.assertEqual(self.page.goto.call_count,1)
        self.assertEqual(result['attempted'],1)
        self.assertIn('ack_timeout',result['stop_reason'])
        self.page.wait_for_timeout.assert_not_called();self.mocks[-2].assert_not_called()
        self.client.close.assert_called_once()

    def assert_browser_stop(self,result,reason):
        self.assertEqual(result['status'],'stopped')
        self.assertEqual(result['attempted'],1)
        self.assertEqual(result['imported'],0)
        self.assertEqual(result['handoff_status'],'skipped_stop')
        self.assertIn('InfrastructureStop:',result['stop_reason'])
        self.assertIn(reason,result['stop_reason'])
        self.page.goto.assert_called_once()
        self.client.import_page.assert_not_called()
        self.client.close.assert_called_once()
        self.mocks[-2].assert_not_called()
        self.context.cookies.assert_not_called()
        self.assertNotIn(((2500,),{}),self.page.wait_for_timeout.call_args_list)

    def test_navigation_protocol_error_stops_before_content_or_second_url(self):
        self.page.goto.side_effect=self.browser_error('Protocol error (Page.navigate): target closed')
        result=self.session()
        self.assert_browser_stop(result,'browser_navigation_failed:Protocol error')
        self.mocks[4].assert_not_called()

    def test_navigation_timeout_can_inspect_content_and_succeed(self):
        def timeout(url,**kwargs):
            self.page.url=url
            raise self.browser_timeout('navigation timed out')
        self.page.goto.side_effect=timeout
        self.page.content.side_effect=[content(1).decode(),content(2).decode()]
        self.mocks[4].side_effect=self.real_page_content
        with patch.object(browser,'security_check'):
            result=self.session()
        self.assertEqual(result['status'],'resolved')
        self.assertEqual(result['imported'],2)
        self.assertIsNone(result['stop_reason'])
        self.assertEqual(self.page.content.call_count,2)
        self.assertTrue(all(call.args[2] is None for call in self.mocks[4].call_args_list))
        self.page.wait_for_timeout.assert_called_once_with(2500)
        self.mocks[-2].assert_called_once()

    def test_exhausted_navigation_in_progress_stops_session(self):
        self.page.url=URLS[0]
        self.page.content.side_effect=self.browser_error('navigation in progress')
        self.mocks[4].side_effect=self.real_page_content
        # Advance a fake clock at each poll; exercise the real readiness loop.
        with patch.object(browser,'security_check'),patch.object(browser,'WAIT_SECONDS',3),patch.object(browser.time,'monotonic',side_effect=range(100)):
            result=self.session()
        self.page.content.assert_called()
        self.assert_browser_stop(result,'browser_fallback_unresolved:browser_navigation_in_progress')

    def test_body_page_identity_mismatch_stops_before_second_url(self):
        self.page.url=URLS[0]
        markers=b'<div class="pg"><strong>2</strong><input name="custompage" value="2"></div>'
        self.page.content.return_value=content(1).replace(b'</body>',markers+b'</body>').decode()
        self.mocks[4].side_effect=self.real_page_content
        with patch.object(browser,'security_check'):
            result=self.session()
        self.assert_browser_stop(result,'page_identity_mismatch')
        self.page.content.assert_called_once()
        self.page.wait_for_timeout.assert_not_called()

    def test_ready_failure_requests_no_pages(self):
        self.client.start.side_effect=InfrastructureStop('ready_mismatch')
        self.assertIsNotNone(self.session()['stop_reason'])
        self.page.goto.assert_not_called();self.mocks[-2].assert_not_called()

    def test_manual_origin_and_navigation_stop_without_handoff(self):
        for reason in ('manual_required: challenge','browser_fallback_off_origin_navigation','browser_fallback_navigation_identity_mismatch'):
            with self.subTest(reason=reason):
                self.page.reset_mock();self.mocks[4].side_effect=RuntimeError(reason)
                result=self.session()
                self.assertEqual(self.page.goto.call_count,1)
                self.assertIsNotNone(result['stop_reason']);self.mocks[-2].assert_not_called()

    def test_identity_unconfirmed_continues_and_handoff_failure_keeps_import(self):
        self.mocks[4].side_effect=[RuntimeError('browser_fallback_unresolved:identity_not_confirmed'),b'fixture']
        self.mocks[-2].side_effect=RuntimeError('handoff failed')
        result=self.session()
        self.assertEqual(result['attempted'],2);self.assertEqual(result['imported'],1)
        self.assertIsNone(result['stop_reason']);self.assertIn('handoff failed',result['handoff_error'])
        self.mocks[-2].assert_called_once()

    def test_limits_preserve_incremental_20(self):
        urls=[thread_url(tid) for tid in range(1,82)]
        self.assertEqual(len(browser.arguments(['--historical',*urls[:80]]).urls),80)
        self.assertEqual(len(browser.arguments(urls[:20]).urls),20)
        with self.assertRaises(SystemExit):browser.arguments(urls[:21])
        with self.assertRaises(SystemExit):browser.arguments(['--historical',*urls])

    def test_incremental_keeps_single_page_import_and_preview(self):
        with patch.object(browser,'import_snapshot',return_value=dict(snapshot_id=1,thread_status='complete')) as single:
            result=browser.session_result(self.page,self.context,{},URLS)
        self.assertEqual(result['imported'],2)
        self.assertEqual(single.call_count,2)
        self.mocks[0].assert_not_called()
        self.mocks[-1].assert_called_once_with([TID,TID])
        self.mocks[-2].assert_called_once()

    def test_incremental_import_failure_remains_per_page(self):
        with patch.object(browser,'import_snapshot',side_effect=[RuntimeError('single import failed'),dict(snapshot_id=2,thread_status='complete')]) as single:
            result=browser.session_result(self.page,self.context,{},URLS)
        self.assertEqual(result['attempted'],2)
        self.assertEqual(result['imported'],1)
        self.assertIsNone(result['stop_reason'])
        self.assertEqual(single.call_count,2)
        self.page.wait_for_timeout.assert_called_once_with(2500)
        self.mocks[-1].assert_called_once_with([TID])
        self.mocks[-2].assert_called_once()

    def test_close_failure_keeps_imports_and_stops_handoff(self):
        self.client.close.side_effect=InfrastructureStop('importer exit failed')
        result=self.session()
        self.assertEqual(result['imported'],2)
        self.assertIn('importer exit failed',result['stop_reason'])
        self.mocks[-2].assert_not_called()


if __name__=='__main__':unittest.main()
