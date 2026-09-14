#!/bin/sh
# One bounded run, exits after verification. Not a historical/daily scheduler.
set -eu
umask 077
cd "$(dirname "$0")/.."
trap 'code=$?; echo "$code" > data/worker.exit' EXIT
python3 archive.py pilot --wait-window
python3 archive.py pilot --offline
python3 archive.py reparse
python3 scripts/verify_pilot.py
