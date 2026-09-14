#!/bin/zsh
cd "${0:A:h}" || exit 1
python3 scripts/history_launcher.py backfill --best-effort --now
printf "\nPress Return to close.\n"
read -r reply
