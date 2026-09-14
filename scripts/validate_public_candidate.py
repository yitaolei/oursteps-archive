#!/usr/bin/env python3
"""Validate a full-size isolated candidate on NAS; never changes live pointers."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import public_release as p

def main():
    dates=p.publication_dates(p.ROOT)
    with tempfile.TemporaryDirectory(prefix='.scope-candidate-', dir=str(p.ROOT)) as tmp:
        root=Path(tmp)
        for name in ['compose.public.yaml','nginx-public-stable.conf']:
            shutil.copyfile(p.ROOT/name,root/name)
        (root/'data').mkdir()
        # Private preview hardlinks stay private; publish copies into public staging.
        shutil.copytree(p.ROOT/'data/preview',root/'data/preview',copy_function=os.link)
        first=p.publish(root,expected=set(dates),dates=dates)
        p.validate_saved(root,first['release'])
        second=p.publish(root,expected=set(dates),dates=dates)
        p.require(second['status']=='unchanged','candidate not idempotent')
        p.publish(root,rollback=True)
        for name in ['current','previous']:
            p.validate_saved(root,p.pointer(root/'public-site',name))
        print(json.dumps(dict(first,validation='PASS',idempotent=True,pointers='PASS',live_changed=False)))
if __name__=='__main__':main()
