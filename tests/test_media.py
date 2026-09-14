import _test_config  # Configure synthetic identity before application imports.
import sys,tempfile,unittest,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.deps'))
from unittest.mock import patch
from oursteps.store import Store
from oursteps.media import discover,run,sniff,irrelevant,image_url,MediaFailure
from oursteps.preview import body_html
PNG=b'\x89PNG\r\n\x1a\n'+b'test'
class MediaTests(unittest.TestCase):
 def test_source_precedence_and_filter(self):
  self.assertEqual(image_url({'file':'https://example.com/a.jpg','src':'https://example.com/placeholder.gif'}),'https://example.com/a.jpg')
  self.assertTrue(irrelevant('https://www.oursteps.com.au/bbs/static/image/smiley/a.gif',{}))
 def test_formats(self):
  for data,mime in [(PNG,'image/png'),(b'\xff\xd8\xffX','image/jpeg'),(b'GIF89a','image/gif'),(b'RIFFxxxxWEBP','image/webp')]:self.assertEqual(sniff(data)[0],mime)
  with self.assertRaises(ValueError):sniff(b'<html>captcha</html>')
 def test_local_render_and_order(self):
  url='https://example.com/photo.png';path='media/ab/'+'a'*64+'.png'
  html=body_html('<p>before</p><img file="'+url+'" src="https://example.com/blank.gif"><p>after</p>',{url:path})
  self.assertIn('src="/'+path+'"',html);self.assertNotIn('example.com',html)
  self.assertLess(html.index('before'),html.index('<img'));self.assertLess(html.index('<img'),html.index('after'))
  self.assertNotIn('<img',body_html('<img src="'+url+'">'))
 def test_idempotence_and_failure_isolation(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=Store(tmp)
   with s.db:
    s.db.execute("INSERT INTO threads(tid,status,discovered_via) VALUES(1,'complete','test')")
    s.db.execute('INSERT INTO posts(pid,tid,author_uid) VALUES(1,1,424242)')
    for pos,url in enumerate(['https://example.com/a.png','https://example.com/a.png','https://example.com/fail.png']):
     s.db.execute("INSERT INTO assets(pid,position,kind,url,attributes) VALUES(1,?,'image',?,?)",(pos,url,json.dumps(dict(src=url))))
   class Fake:
    def __init__(self,*a):pass
    def get(self,url):
     calls.append(url)
     if 'fail' in url:raise MediaFailure('retry_later','timeout',3600)
     return PNG,'image/jpeg'  # CDN mislabeled header; signature remains authoritative
   calls=[]
   with patch('oursteps.media.Transport',Fake):
    result=run(s);self.assertEqual(result['detected'],2);self.assertEqual(result['references'],3)
    self.assertEqual(result['states'],{'retry_later':1,'success':1})
    calls.clear();second=run(s);self.assertEqual(calls,[]);self.assertEqual(second['downloaded_this_run'],0)
   self.assertEqual(s.db.execute('SELECT count(*) FROM posts').fetchone()[0],1)
   self.assertEqual(tuple(s.db.execute("SELECT mime,reported_mime FROM media_files WHERE state='success'").fetchone()),('image/png','image/jpeg'))
   s.db.close()
