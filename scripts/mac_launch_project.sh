#!/bin/zsh
set -eu
setopt NULL_GLOB

mode="${1:-}"
script_rel="${2:-}"
if [[ -z "$mode" || -z "$script_rel" ]]; then
  echo "usage: mac_launch_project.sh <plain|clean-nas-env> <relative-script> [args...]" >&2
  exit 64
fi
shift 2

find_root() {
  local candidate
  for candidate in /Volumes/Newhome/docker/oursteps-archive /Volumes/Newhome-*/docker/oursteps-archive; do
    if [[ -d "$candidate/.git" && -f "$candidate/$script_rel" ]]; then
      print -r -- "$candidate"
      return 0
    fi
  done
  return 1
}

root="$(find_root || true)"
if [[ -z "$root" ]]; then
  lock="/tmp/oursteps-smb-remount.lock"
  if mkdir "$lock" 2>/dev/null; then
    trap 'rmdir "$lock" 2>/dev/null || true' EXIT
    /usr/bin/osascript <<'APPLESCRIPT' >/dev/null 2>&1 || true
with timeout of 15 seconds
  mount volume "smb://DS923SOPAC.local/Newhome"
end timeout
APPLESCRIPT
    rmdir "$lock" 2>/dev/null || true
    trap - EXIT
  else
    sleep 3
  fi
  root="$(find_root || true)"
fi

if [[ -z "$root" ]]; then
  echo "OurSteps NAS share/project not available after remount attempt" >&2
  exit 78
fi

cd "$root"
python="/opt/homebrew/bin/python3"
if [[ ! -x "$python" ]]; then
  python="/opt/homebrew/opt/python@3.12/bin/python3.12"
fi

case "$mode" in
  plain)
    exec "$python" "$root/$script_rel" "$@"
    ;;
  clean-nas-env)
    exec /usr/bin/env -u OURSTEPS_NAS_HOST -u OURSTEPS_NAS -u OURSTEPS_NAS_PROJECT -u OURSTEPS_NAS_ROOT "$python" "$root/$script_rel" "$@"
    ;;
  *)
    echo "unknown launch mode: $mode" >&2
    exit 64
    ;;
esac
