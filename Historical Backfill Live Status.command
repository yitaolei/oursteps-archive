#!/bin/zsh
cd "${0:A:h}" || exit 1
exec python3 scripts/backfill_live_status.py
