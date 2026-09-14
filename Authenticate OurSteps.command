#!/bin/zsh
set -eu
cd "${0:A:h}"
runtime="$HOME/.local/share/oursteps-runtime"
if [[ ! -x "$runtime/bin/python" ]]; then
  python3 -m venv "$runtime"
  "$runtime/bin/pip" install -r requirements-auth.txt
fi
"$runtime/bin/python" -c "import keyring" 2>/dev/null || "$runtime/bin/pip" install -r requirements-auth.txt
"$runtime/bin/python" archive.py auth
printf '\nPress Return to close.\n'
read -r reply
