#!/bin/zsh
cd "${0:A:h}" || exit 1
python3 scripts/backfill_live_status.py --once
printf "\nPress Return to close.\n"
read -r reply
