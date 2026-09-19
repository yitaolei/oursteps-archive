#!/usr/bin/env python3
"""Read-only public release and expected-count validation; run on NAS."""
import time
from public_release import ROOT, ARTICLE, config_check, expected_tids, pointer, require, validate_saved, publication_dates, recent_policy
started=time.monotonic()
try:
    config_check(ROOT)
    site=ROOT/'public-site'; current=pointer(site,'current'); previous=pointer(site,'previous')
    if not current or not previous: raise ValueError('current/previous not initialized')
    expected=expected_tids(ROOT); recent=recent_policy(publication_dates(ROOT))
    result=validate_saved(ROOT,current)
    actual_tids={ARTICLE.fullmatch(name)[1] for name in result['hashes'] if ARTICLE.fullmatch(name)}
    require(actual_tids == expected, 'current release differs from expected complete threads')
    require(result.get('recent') == recent, 'current recent policy differs from database policy')
    validate_saved(ROOT,previous)
    print('Recent: %s; cutoff: %s; excluded missing dates: %s' % (result['recent_articles'], result['recent']['cutoff'], result['recent']['excluded_missing_dates']))
    print('PASS: %s articles/cards/search entries; allowlist, private markers, 644/755, robots, search contracts, mounts, current/previous and checksums' % result['articles'])
except Exception as error:
    print('FAIL: '+str(error));raise SystemExit(1)
finally:
    print('HEALTHCHECK_SECONDS=%.6f' % (time.monotonic()-started))
