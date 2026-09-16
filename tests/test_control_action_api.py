import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import scripts.control_action_api as api

class ControlActionApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.patch=patch.object(api,'ROOT',self.root);self.patch.start();self.allowed=patch.object(api,'enabled_actions',return_value={'incremental_sync'});self.allowed.start()
        self.server=api.ThreadingHTTPServer(('127.0.0.1',0),api.Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.allowed.stop();self.patch.stop();self.tmp.cleanup()
    def req(self,method,path,headers=None,body=None):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=2);c.request(method,path,body=body,headers=headers or {});r=c.getresponse();data=json.loads(r.read());status=r.status;c.close();return status,data
    def test_owner_only_custom_header_and_allowlist(self):
        self.assertEqual(self.req('GET','/control-action/status')[0],404)
        owner={'X-Oursteps-Scope':'full'}
        self.assertEqual(self.req('GET','/control-action/status',owner)[0],200)
        self.assertEqual(self.req('POST','/control-action/request/incremental_sync',owner)[0],403)
        h={**owner,'X-Oursteps-Action-Request':'1'}
        self.assertEqual(self.req('POST','/control-action/request/rollback_public',h)[0],404)
        status,data=self.req('POST','/control-action/request/incremental_sync',h);self.assertEqual(status,202);self.assertTrue(data['ok'])
        status,data=self.req('POST','/control-action/request/incremental_sync',h);self.assertEqual(status,200);self.assertFalse(data['created'])
        self.assertEqual(self.req('POST','/control-action/request/incremental_sync',h,body='x')[0],400)
