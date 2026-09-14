#!/usr/bin/env python3
"""Read-only public release and expected-count validation; run on NAS."""
from public_release import ROOT, config_check, expected_tids, pointer, validate, validate_saved, publication_dates, recent_policy
try:
    config_check(ROOT)
    site=ROOT/'public-site'; current=pointer(site,'current'); previous=pointer(site,'previous')
    if not current or not previous: raise ValueError('current/previous not initialized')
    result=validate(site/current,expected_tids(ROOT),recent_policy(publication_dates(ROOT)))
    validate_saved(ROOT,current);validate_saved(ROOT,previous)
    print('Recent: %s; cutoff: %s; excluded missing dates: %s' % (result['recent_articles'], result['recent']['cutoff'], result['recent']['excluded_missing_dates']))
    print('PASS: %s articles/cards/search entries; allowlist, private markers, 644/755, robots, search contracts, mounts, current/previous and checksums' % result['articles'])
except Exception as error:
    print('FAIL: '+str(error));raise SystemExit(1)
