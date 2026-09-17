#!/usr/bin/env python3
"""Internal queue-only HTTP API. It never executes commands or reads secrets."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
ROOT=Path(os.environ.get('OURSTEPS_ACTION_ROOT','/state'))
sys.path.insert(0, os.environ.get('OURSTEPS_APP','/app'))
from oursteps.control_actions import WEB_ACTIONS, request, status, worker_status

def enabled_actions():
    try:
        data=json.loads(Path('/app/config/tool_registry.json').read_text())
        configured={str(t.get('id')) for t in data.get('tools',[]) if t.get('web_action') is True}
        return WEB_ACTIONS & configured
    except (OSError,ValueError,TypeError):
        return set()

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body=(json.dumps(payload,separators=(',',':'))+'\n').encode()
        self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def _owner(self): return self.headers.get('X-Oursteps-Scope') == 'full'
    def do_GET(self):
        if not self._owner(): return self._send(404,{'ok':False})
        if self.path == '/control-action/status': return self._send(200,{'ok':True,'jobs':status(ROOT)})
        if self.path == '/control-action/worker-status': return self._send(200,{'ok':True,'worker':worker_status(ROOT)})
        return self._send(404,{'ok':False})
    def do_POST(self):
        if not self._owner(): return self._send(404,{'ok':False})
        if self.headers.get('X-Oursteps-Action-Request') != '1': return self._send(403,{'ok':False,'error':'action header required'})
        if int(self.headers.get('Content-Length','0') or '0') != 0: return self._send(400,{'ok':False,'error':'request body not allowed'})
        prefix='/control-action/request/'
        if not self.path.startswith(prefix): return self._send(404,{'ok':False})
        action=self.path[len(prefix):]
        if action not in enabled_actions(): return self._send(404,{'ok':False})
        try: job,created=request(ROOT,action)
        except Exception: return self._send(500,{'ok':False,'error':'queue unavailable'})
        self._send(202 if created else 200,{'ok':True,'created':created,'job':{k:job.get(k) for k in ('id','action','state','created_at','updated_at','message')}})
    def log_message(self,*args): pass

if __name__=='__main__': ThreadingHTTPServer(('0.0.0.0',8081),Handler).serve_forever()
