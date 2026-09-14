#!/usr/bin/env python3
"""Explicitly generate private nginx configuration; never invoked by publishing."""
import argparse
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from oursteps.deployment import public_access

TOKENS = ('__OURSTEPS_RECENT_USER__', '__OURSTEPS_FULL_USER__')


def render_text(template):
    users = public_access()
    for token, user in zip(TOKENS, users):
        if template.count(token) != 1:
            raise ValueError('Missing or duplicate nginx placeholder: ' + token)
        template = template.replace(token, user)
    if re.search(r'__[A-Z][A-Z0-9_]*__', template):
        raise ValueError('Unresolved nginx placeholder')
    return template


def render(output=None, template=None):
    source = Path(template) if template is not None else ROOT / 'nginx-public-stable.conf.template'
    target = Path(output) if output is not None else ROOT / 'nginx-public-stable.conf'
    content = render_text(source.read_text())
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent, delete=False) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o644)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    render(output=args.output)
