#!/bin/zsh
set -eu
cd "${0:A:h}"
# Prevent manual archive/auth work from colliding with an active NAS writer.
if python3 scripts/archive_worker_status.py --guard; then
  :
else
  rc=$?
  printf '\nPress Return to close.\n'
  read -r reply
  exit "$rc"
fi
python3 scripts/update_now.py --now
printf '\nPress Return to close.\n'
read -r reply
