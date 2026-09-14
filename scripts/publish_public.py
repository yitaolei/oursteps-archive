#!/usr/bin/env python3
"""Run on NAS as the configured deployment user. No Docker/sudo/network required."""
import argparse
import json
import sys
from public_release import publish
p=argparse.ArgumentParser();p.add_argument('--rollback',action='store_true');a=p.parse_args()
try:
    print(json.dumps(publish(rollback=a.rollback)))
except Exception as error:
    print('Public publish FAIL: '+str(error),file=sys.stderr)
    raise SystemExit(1)
