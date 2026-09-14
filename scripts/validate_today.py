"""Offline daily completion and homepage membership checks on NAS native storage."""
import sys,json,fcntl
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]
from oursteps.store import Store,atomic_write
from oursteps.parser import soup_of
from oursteps.dates import sydney_today
from oursteps.preview import validate
if sys.platform=='darwin': raise SystemExit('Run on NAS native filesystem')
with open(ROOT/'data/worker.lock','a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 s=Store(ROOT/'data')
 try:
  summary=json.loads((s.root/'sync-summary.json').read_text());day=summary['date']
  assert day==sydney_today(), 'Summary date no longer today'
  assert summary['status']=='success' and summary['discovery_complete'], 'Discovery incomplete'
  members={r[0] for r in s.db.execute('SELECT tid FROM daily_members WHERE day=?',(day,))}
  complete={r[0] for r in s.db.execute("SELECT tid FROM threads WHERE status='complete'")}
  assert members<=complete,'Incomplete daily threads'
  soup=soup_of((s.root/'preview/index.html').read_bytes());section=soup.select_one('section')
  assert 'today' in section.get('class',[]) and day in section.h2.get_text(),'Today section not first'
  shown={int(a['href'].split('.')[0].strip('/')) for a in section.select('.card a.title')}
  assert shown==members,'Homepage membership differs from complete discovered list'
  baseline=validate(s)
  assert baseline['ok'], 'Archive integrity/rendering validation failed'
  result=dict(ok=True,date=day,discovered=len(members),homepage_today=len(shown),all_complete=True,archive_validation=baseline)
  atomic_write(s.root/'today-validation.json',json.dumps(result,ensure_ascii=False,indent=2).encode())
  print(json.dumps({k:v for k,v in result.items() if k!='archive_validation'}))
 finally:s.db.close()
