#!/bin/zsh
cd "${0:A:h}" || exit 1
python3 scripts/archive_worker_status.py
printf '\nPress Return to close.\n'
read -r reply
