#!/usr/bin/env python3
"""Localhost-only, one-action Studio sync queue. Auth enforced by Apache owner location."""
import json,os,subprocess,sys,time,fcntl
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'data/studio-manual-sync.json'
LOCK=ROOT/'data/studio-manual-api.lock'
def state():
 try:return json.loads(STATE.read_text())
 except (OSError,ValueError):return {'state':'idle'}
def save(value):
 tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(value));os.replace(tmp,STATE)
class Handler(BaseHTTPRequestHandler):
 def send(self,code,data):
  b=json.dumps(data).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def do_GET(self):
  if self.path!='/studio-action/status':return self.send(404,{'ok':False})
  self.send(200,{'ok':True,'job':state()})
 def do_POST(self):
  action=self.path.removeprefix('/studio-action/')
  if action not in ('incremental-sync','guide-discovery','historical-backfill','publish-release') or self.path!='/studio-action/'+action:return self.send(404,{'ok':False})
  if self.headers.get('X-Oursteps-Action-Request')!='1' or self.headers.get('Content-Length','0')!='0':return self.send(403,{'ok':False})
  with LOCK.open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX)
   existing=state()
   if existing.get('state') in ('queued','running'):
    try:os.kill(int(existing['pid']),0);return self.send(200,{'ok':True,'created':False,'job':existing})
    except (OSError,KeyError,ValueError):pass
   env=os.environ.copy();env['OURSTEPS_STUDIO_WRITER_HANDOVER_APPROVED']='1';env['OURSTEPS_STUDIO_PRIMARY_EXPERIMENT']='1'
   out=ROOT/'data/logs/studio-manual-sync.log';out.parent.mkdir(exist_ok=True)
   with out.open('ab') as stream:
    argv=[sys.executable,str(ROOT/'scripts/studio_manual_sync_worker.py')] if action=='incremental-sync' else [sys.executable,str(ROOT/'scripts/studio_other_action_worker.py'),action]
    p=subprocess.Popen(argv,cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
   record={'state':'running','action':action,'pid':p.pid,'started_at':time.time(),'message':'Studio '+action+' started'}
   save(record);self.send(202,{'ok':True,'created':True,'job':record})
 def log_message(self,*args):pass
HTTPServer(('127.0.0.1',18752),Handler).serve_forever()
