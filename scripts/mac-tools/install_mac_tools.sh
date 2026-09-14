#!/bin/sh
set -eu
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
destination=${OURSTEPS_MAC_BIN:-"$HOME/.local/bin"}
mkdir -p "$destination"
# Copy the shared loader and deployment-only private config, never identity/session data.
helper=$(mktemp "$destination/.deployment.XXXXXX")
private_config=$(mktemp "$destination/.deployment-config.XXXXXX")
trap 'rm -f "$helper" "$private_config"' EXIT HUP INT TERM
python3 -B - "$source_dir/../../oursteps/deployment.py" > "$private_config" <<'PYCONFIG'
import json, runpy, sys
host, project = runpy.run_path(sys.argv[1])['deployment']()
print(json.dumps({'deployment': {'nas_host': host, 'nas_project': project}}))
PYCONFIG
chmod 600 "$private_config"
install -m 644 "$source_dir/../../oursteps/deployment.py" "$helper"
mv -f "$helper" "$destination/_oursteps_deployment.py"
mv -f "$private_config" "$destination/oursteps-config.local.json"
for name in check-oursteps show-missing reconcile-oursteps run-oursteps-batch oursteps-status; do
    temporary=$(mktemp "$destination/.${name}.XXXXXX")
    install -m 755 "$source_dir/$name" "$temporary"
    mv -f "$temporary" "$destination/$name"
done
printf 'Installed check-oursteps, show-missing, reconcile-oursteps, run-oursteps-batch and oursteps-status into %s\n' "$destination"
