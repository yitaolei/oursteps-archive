#!/usr/bin/env python3
"""Inspect local triage without writes or network access."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path
import os
import subprocess

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.studio_local_authority import checked_local_data
from oursteps.triage import migrate

def require_local_mount(path):
    mounts = subprocess.run(['/sbin/mount'], capture_output=True, text=True, check=True).stdout
    matches = []
    for line in mounts.splitlines():
        try:
            _, rest = line.split(' on ', 1)
            mountpoint, options = rest.rsplit(' (', 1)
            mountpoint = Path(mountpoint)
            if path == mountpoint or mountpoint in path.parents:
                matches.append((len(mountpoint.parts), options.rstrip(')').split(', ')))
        except ValueError:
            continue
    if not matches:
        raise ValueError('cannot verify local filesystem')
    options = max(matches, key=lambda item: item[0])[1]
    if options[0] not in ('apfs', 'hfs') or 'local' not in options:
        raise ValueError('network or unsupported filesystem forbidden')

def checked_db_path(path):
    path = Path(path).expanduser()
    if not path.is_absolute() or path.name != 'archive.sqlite3' or '..' in path.parts:
        raise ValueError('absolute archive.sqlite3 path required')
    root = checked_local_data(path.parent)
    if path.is_symlink() or not path.is_file():
        raise ValueError('existing regular database required; symlinks refused')
    resolved = path.resolve(strict=True)
    if resolved.parent != root or resolved.stat().st_uid != os.getuid():
        raise ValueError('database must belong to the local data owner')
    if root.stat().st_dev != Path.home().resolve().stat().st_dev or resolved.stat().st_dev != root.stat().st_dev:
        raise ValueError('mounted data roots forbidden')
    require_local_mount(root)
    return resolved

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True, help='Existing local SQLite database')
    args = parser.parse_args()
    try:
        path = checked_db_path(args.db)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.error(str(error))
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')  # One consistent snapshot across all evidence queries.
        print(json.dumps(migrate(db), indent=2))
    finally:
        db.close()

if __name__ == '__main__':
    main()
