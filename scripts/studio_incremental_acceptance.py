#!/usr/bin/env python3
"""Read-only production acceptance for Studio current-day incremental sync."""
import json,sqlite3,sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'
TODAY=datetime.now(ZoneInfo('Australia/Sydney')).date().isoformat()
with sqlite3.connect((DATA/'archive.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as db:
    check=db.execute('pragma quick_check').fetchone()[0]
    members=db.execute('SELECT count(*) FROM daily_members WHERE day=?',(TODAY,)).fetchone()[0]
    complete=db.execute("SELECT count(*) FROM daily_members d JOIN threads t ON t.tid=d.tid WHERE d.day=? AND t.status='complete'",(TODAY,)).fetchone()[0]
    incomplete=[r[0] for r in db.execute("SELECT d.tid FROM daily_members d JOIN threads t ON t.tid=d.tid WHERE d.day=? AND t.status!='complete' ORDER BY d.tid DESC",(TODAY,))]
    challenges=[r[0] for r in db.execute("SELECT tid FROM threads WHERE discovered_via='daily-challenge' AND status!='complete' ORDER BY tid DESC")]
summary=json.loads((DATA/'sync-summary.json').read_text())
release=(ROOT/'public-site/current').resolve(strict=True)
releases=(ROOT/'public-site/releases').resolve(strict=True)
if release.parent!=releases:raise SystemExit('FAIL release escaped Studio directory')
index=(release/'index.html').read_text()
with sqlite3.connect((DATA/'archive.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as db:
    today_ids=[r[0] for r in db.execute("SELECT d.tid FROM daily_members d JOIN threads t ON t.tid=d.tid WHERE d.day=? AND t.status='complete'",(TODAY,))]
missing_in_release=[tid for tid in today_ids if str(tid) not in index]
result={'date':TODAY,'sqlite_integrity':check,'discovered_today':members,'complete_today':complete,'incomplete_today':incomplete,'challenge_queue':challenges,'sync_status':summary.get('status'),'discovery_complete':summary.get('discovery_complete'),'summary_count_matches':summary.get('discovered_today')==members,'release':release.name,'missing_in_release':missing_in_release}
passed=(check=='ok' and members>0 and members==complete and not challenges and summary.get('status')=='success' and summary.get('discovery_complete') is True and result['summary_count_matches'] and not missing_in_release)
result['acceptance']='PASS' if passed else 'FAIL'
print(json.dumps(result,ensure_ascii=False,indent=2))
sys.exit(0 if passed else 1)
