"""NAS offline checks: hashes, MIME signatures, owner-only references and local HTML."""
import sys,json,hashlib,fcntl
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]
from oursteps.config import UID
from oursteps.store import Store,atomic_write
from oursteps.media import sniff
from oursteps.preview import validate
from oursteps.parser import soup_of
if sys.platform=='darwin':raise SystemExit('NAS native only')
with open(ROOT/'data/worker.lock','a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);s=Store(ROOT/'data')
 try:
  failures=[];success=list(s.db.execute("SELECT * FROM media_files WHERE state='success'"));paths={r['path'] for r in success}
  for row in success:
   raw=(s.root/row['path']).read_bytes()
   assert len(raw)==row['size'] and hashlib.sha256(raw).hexdigest()==row['sha256']
   assert sniff(raw)[0]==row['mime']
  assert not s.db.execute('SELECT 1 FROM media_refs r LEFT JOIN posts p ON p.pid=r.pid WHERE p.author_uid!=? OR p.pid IS NULL', (UID,)).fetchone()
  images=0;articles=0
  for page in (s.root/'preview').glob('*.html'):
   soup=soup_of(page.read_bytes());imgs=soup.select('.reader-body img')
   if imgs:articles+=1
   for img in imgs:
    assert img['src'].startswith('/media/') and img['src'][1:] in paths
    images+=1
  archive=validate(s);assert archive['ok'],archive['failures']
  result=dict(ok=True,downloaded=len(success),unique_files=len(paths),rendered_images=images,image_articles=articles,archive_ok=True)
  atomic_write(s.root/'media-validation.json',json.dumps(result,indent=2).encode());print(json.dumps(result))
 finally:s.db.close()
