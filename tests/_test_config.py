"""Shared unittest/pytest bootstrap; never read private installation config."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.deps')]
os.environ['OURSTEPS_CONFIG'] = str(Path(__file__).with_name('config.test.json'))
for name in ('OURSTEPS_NAS_HOST', 'OURSTEPS_NAS_PROJECT', 'OURSTEPS_NAS', 'OURSTEPS_NAS_ROOT'):
    os.environ.pop(name, None)
