"""Offline real nginx tests; uses only temporary auth, content and loopback port."""
import _test_config  # Configure synthetic identity before application imports.
import base64
import http.client
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest

@unittest.skipUnless(shutil.which('nginx'), 'native nginx unavailable')
class NginxScopeTests(unittest.TestCase):
    def test_auth_scopes_and_traversal(self):
        import crypt
        with tempfile.TemporaryDirectory(prefix='oursteps-nginx-') as tmp:
            root=Path(tmp);current=root/'public/current';recent=current/'recent-1y';recent.mkdir(parents=True)
            for folder,ids in [(current,['1','2']),(recent,['2'])]:
                (folder/'index.html').write_text(','.join(ids))
                (folder/'search-index.json').write_text(','.join(ids))
                (folder/'robots.txt').write_text('User-agent: *\nDisallow: /\n')
                for tid in ids:(folder/(tid+'.html')).write_text('article '+tid)
            for name in ('control-center.html','control-center.css','control-center.js','control-center.json'):(current/name).write_text('owner control')
            password='temporary-fixture-only'
            auth=root/'fixture.htpasswd'
            auth.write_text(''.join(user+':'+crypt.crypt(password,crypt.mksalt(crypt.METHOD_SHA512))+'\n' for user in ['recent_reader','archive_owner','Unmapped','RECENT_READER','ARCHIVE_OWNER']))
            sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
            from scripts.render_public_nginx import render_text
            config=render_text((Path(__file__).resolve().parents[1]/'nginx-public-stable.conf.template').read_text())
            config=config.replace('/tmp/',str(root)+'/').replace('/srv/public',str(root/'public')).replace('/etc/nginx/oursteps.htpasswd',str(auth)).replace('listen 8080;', 'listen 127.0.0.1:%s;'%port).replace('/dev/stdout',str(root/'access.log')).replace('/dev/stderr',str(root/'error.log'))
            config = 'error_log '+str(root/'error.log')+';\n'+config
            conf=root/'nginx.conf';conf.write_text(config)
            result=subprocess.run(['nginx','-e',str(root/'error.log'),'-t','-p',tmp,'-c',str(conf)],capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr.decode())
            process=subprocess.Popen(['nginx','-e',str(root/'error.log'),'-p',tmp,'-c',str(conf),'-g','daemon off;'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                for attempt in range(50):
                    try:
                        with socket.create_connection(('127.0.0.1',port),timeout=.1):break
                    except OSError:time.sleep(.1)
                def get(path,user=None,secret=password,method='GET'):
                    headers={}
                    if user:headers['Authorization']='Basic '+base64.b64encode((user+':'+secret).encode()).decode()
                    c=http.client.HTTPConnection('127.0.0.1',port,timeout=3);c.request(method,path,headers=headers);r=c.getresponse();body=r.read();status=r.status
                    if status != 400:
                        self.assertEqual(r.getheader('Cache-Control'),'no-store')
                        self.assertEqual(r.getheader('X-Content-Type-Options'),'nosniff')
                        self.assertIn("default-src 'none'",r.getheader('Content-Security-Policy',''))
                    c.close();return status,body
                self.assertEqual(get('/')[0],401)
                self.assertEqual(get('/','recent_reader','wrong')[0],401,(root/'error.log').read_text())
                for path in ['/','/1.html','/search-index.json','/.secrets','/recent-1y/../index.html']:
                    self.assertEqual(get(path,'Unmapped')[0],403)
                self.assertEqual(get('/','RECENT_READER')[0],403)
                self.assertEqual(get('/','ARCHIVE_OWNER')[0],403)
                self.assertEqual(get('/','recent_reader'),(200,b'2'))
                self.assertEqual(get('/','archive_owner'),(200,b'1,2'))
                self.assertEqual(get('/search-index.json','recent_reader'),(200,b'2'))
                self.assertEqual(get('/search-index.json','archive_owner'),(200,b'1,2'))
                self.assertEqual(get('/2.html','recent_reader'),(200,b'article 2'))
                self.assertEqual(get('/1.html','archive_owner'),(200,b'article 1'))
                self.assertEqual(get('/1.html','recent_reader')[0],404)
                self.assertEqual(get('/control-center.html','archive_owner'),(200,b'owner control'))
                self.assertEqual(get('/control-center.json','archive_owner'),(200,b'owner control'))
                self.assertEqual(get('/control-center.html','recent_reader')[0],404)
                self.assertEqual(get('/control-center.json','recent_reader')[0],404)
                for path in ['/../1.html','/%2e%2e/1.html','/recent-1y/../1.html','/recent-1y/%2e%2e/1.html','/full/1.html','/current/1.html','/releases/1.html','/%252e%252e/1.html','/..%2f1.html']:
                    status,body=get(path,'recent_reader');self.assertIn(status,[400,403,404]);self.assertNotIn(b'article 1',body)
                self.assertEqual(get('/','recent_reader',method='HEAD'),(200,b''))
                self.assertEqual(get('/','recent_reader',method='POST')[0],403)
            finally:
                process.terminate();process.wait(timeout=5)
