#!/bin/zsh
cd "${0:A:h}" || exit 1
python3 scripts/history_launcher.py fallback
printf "\nPress Return to close.\n"
read -r reply
