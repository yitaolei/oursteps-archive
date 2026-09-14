import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'.deps'))
from oursteps.store import Store
from oursteps.sync import publication_time
s=Store(ROOT/'data');issues=[];good=0
for row in s.db.execute('SELECT p.tid,p.posted_at_raw,s.* FROM posts p JOIN snapshots s ON p.snapshot_id=s.id WHERE p.floor="1#"'):
    try: publication_time(row['posted_at_raw'],s.raw(row));good+=1
    except ValueError as e: issues.append((row['tid'],str(e)))
print(dict(verified=good,issues=issues))
