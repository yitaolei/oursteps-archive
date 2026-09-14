#!/bin/zsh
set -eu
cd "${0:A:h}"
python3 scripts/update_now.py --now
printf '\nPress Return to close.\n'
read -r reply
