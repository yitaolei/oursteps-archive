"""NAS-native redacted verification diagnostics received on SSH stdin."""
import sys,json,os,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]
from oursteps.store import atomic_write,redact_raw
from oursteps.parser import soup_of
if __name__=='__main__':
 if sys.platform=='darwin':raise SystemExit('NAS native only')
 os.umask(0o077)
 data=json.load(sys.stdin)
 directory=ROOT/'.secrets/diagnostics';directory.mkdir(parents=True,exist_ok=True)
 os.chmod(ROOT/'.secrets',0o700);os.chmod(directory,0o700)
 name=uuid.uuid4().hex
 soup=soup_of(data.pop('html','').encode())
 for n in soup.select('input,textarea,script'):n.decompose()
 atomic_write(directory/(name+'.html'),redact_raw(str(soup).encode()))
 atomic_write(directory/(name+'.json'),json.dumps(data,ensure_ascii=False,indent=2).encode())
 print(name)
