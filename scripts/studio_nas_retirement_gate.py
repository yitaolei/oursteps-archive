#!/usr/bin/env python3
"""Read-only fail-closed gate for retiring legacy NAS 8448. Never changes NAS."""
import json,sqlite3,subprocess,sys
from pathlib import Path
from urllib.request import urlopen
from urllib.error import HTTPError,URLError
ROOT=Path(__file__).resolve().parents[1]
checks={};details={}
def test(name,value):checks[name]=bool(value)
with sqlite3.connect((ROOT/'data/archive.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as db:
 test('studio_db_integrity',db.execute('pragma quick_check').fetchone()[0]=='ok')
 test('studio_today_complete',db.execute("SELECT count(*) FROM daily_members d JOIN threads t ON t.tid=d.tid WHERE d.day='2026-11-02' AND t.status='complete'").fetchone()[0]>=16)
release=(ROOT/'public-site/current').resolve(strict=True)
test('studio_current_release',release.parent==(ROOT/'public-site/releases').resolve() and (release/'index.html').is_file())
for name,command in [('daily_acceptance',[sys.executable,str(ROOT/'scripts/studio_incremental_acceptance.py')]),('production_regression',[sys.executable,str(ROOT/'scripts/studio_production_regression.py')])]:
 try:test(name,subprocess.run(command,cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30).returncode==0)
 except (OSError,subprocess.TimeoutExpired):test(name,False)
# These operational checks must be supported by separate network, credentials and rollback receipts.
test('studio_external_replacement_verified',False)
test('nas_8448_reverse_proxy_retirement_verified',False)
test('nas_web_service_target_confirmed',False)
test('owner_and_tester_auth_live_verified_after_cutover',False)
test('tested_reversible_nas_web_only_shutdown',False)
print(json.dumps({'studio_checks':checks,'safe_to_retire_8448':all(checks.values()),'unresolved':[k for k,v in checks.items() if not v], 'nas_modified':False},ensure_ascii=False,indent=2))
sys.exit(0 if all(checks.values()) else 2)
