#!/usr/bin/env python3
"""Serve only the generated preview on loopback; never expose database/raw files."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import re

ROOT=Path(__file__).resolve().parent/'data'/'preview'
class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path=urlsplit(self.path).path
        name='index.html' if path=='/' else path.lstrip('/')
        media=bool(re.fullmatch(r'media/[0-9a-f]{2}/[0-9a-f]{64}\.(jpg|png|gif|webp)',name))
        if not media and not re.fullmatch(r'(index|[0-9]+)\.html|style\.css|search\.js|reader\.js|search-index\.json',name):
            self.send_error(404); return
        base=ROOT.parent/'media' if media else ROOT
        target=(base/(name[6:] if media else name)).resolve()
        if base.resolve() not in target.parents:
            self.send_error(404);return
        try: content=target.read_bytes()
        except OSError: self.send_error(404); return
        self.send_response(200)
        self.send_header('Content-Type',{'html':'text/html; charset=utf-8','css':'text/css; charset=utf-8','js':'application/javascript; charset=utf-8','jpg':'image/jpeg','png':'image/png','gif':'image/gif','webp':'image/webp','json':'application/json; charset=utf-8'}[name.rsplit('.',1)[1]])
        self.send_header('Content-Length',str(len(content)))
        self.send_header('Content-Security-Policy',"default-src 'none'; img-src 'self' https:; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Cache-Control','no-store')
        self.end_headers()
        if self.command != 'HEAD': self.wfile.write(content)
    def log_message(self,*args): pass
if __name__=='__main__':
    if not (ROOT/'index.html').exists(): raise SystemExit('Preview not built: run archive.py build-preview on NAS first.')
    print('Local preview: http://localhost:8080',flush=True)
    ThreadingHTTPServer(('127.0.0.1',8080),Handler).serve_forever()
