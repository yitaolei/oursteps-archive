#!/usr/bin/env python3
"""Portable entry point; dependencies may be installed into .deps on NAS."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEPS = str(ROOT / '.deps')
AUTH_RUNTIME = Path.home() / '.local/share/oursteps-runtime'
# NAS/system Python keeps the original pinned project dependency precedence.
# The dedicated Mac auth venv already carries Playwright/bs4 locally; put the
# NAS .deps bundle last there so it only supplies a genuinely missing package
# (currently pytz) instead of forcing every third-party import across SMB.
if Path(sys.prefix) == AUTH_RUNTIME:
    sys.path.append(DEPS)
else:
    sys.path.insert(0, DEPS)

from oursteps.cli import main

if __name__ == '__main__':
    main()
