#!/usr/bin/env python3
"""Portable entry point; dependencies may be installed into .deps on NAS."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / '.deps'))
from oursteps.cli import main

if __name__ == '__main__':
    main()
