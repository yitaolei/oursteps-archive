#!/usr/bin/env python3
"""Fail-closed Studio-local source guard, isolated from live NAS."""
import os
import sys
from pathlib import Path

def checked_local_data(path):
    if sys.platform != 'darwin':
        raise ValueError('Studio primary mode requires macOS')
    p = Path(path).expanduser()
    if not p.is_absolute() or not p.is_dir():
        raise ValueError('absolute existing local data root required')
    if any(part == '..' for part in p.parts):
        raise ValueError('traversal refused')
    if str(p).startswith(('/Volumes/', '/Network/', '/net/')):
        raise ValueError('mounted/network data roots forbidden')
    real = p.resolve(strict=True)
    if str(real).startswith(('/Volumes/', '/Network/', '/net/')):
        raise ValueError('resolved network data root forbidden')
    if not str(real).startswith(str(Path.home().resolve()) + '/'):
        raise ValueError('only local user-owned data root permitted')
    if real.is_symlink() or real.stat().st_uid != os.getuid():
        raise ValueError('local owner mismatch')
    return real

def allow_local_authority(data_root):
    if os.environ.get('OURSTEPS_STUDIO_PRIMARY_EXPERIMENT') != '1':
        raise ValueError('explicit isolated Studio experiment switch required')
    return checked_local_data(data_root)
