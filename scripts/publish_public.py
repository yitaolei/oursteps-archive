#!/usr/bin/env python3
"""Run on NAS as the configured deployment user. No Docker/sudo/network required."""
import argparse
import json
import sys
import time
from public_release import ROOT, publish, post_publish_check
p=argparse.ArgumentParser();p.add_argument('--rollback',action='store_true');a=p.parse_args()
try:
    result=publish(rollback=a.rollback)
    print(json.dumps(result))
    if not a.rollback:
        started=time.monotonic()
        print(json.dumps({'post_publish_check':post_publish_check(ROOT,result),
                          'post_publish_check_seconds':round(time.monotonic()-started,6)}))
except Exception as error:
    print('Public publish FAIL: '+str(error),file=sys.stderr)
    raise SystemExit(1)
