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
runtime="$HOME/.local/share/oursteps-runtime"
if [[ ! -x "$runtime/bin/python" ]]; then
  python3 -m venv "$runtime"
  "$runtime/bin/pip" install -r requirements-auth.txt
fi
"$runtime/bin/python" -c "import keyring" 2>/dev/null || "$runtime/bin/pip" install -r requirements-auth.txt
"$runtime/bin/python" archive.py auth
printf '\nPress Return to close.\n'
read -r reply
