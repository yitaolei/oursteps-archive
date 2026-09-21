#!/bin/bash
set -euo pipefail

# Reuse the existing TypeSafe credential already stored for the sibling 8449 project.
# Override only if a separate Keychain service is intentionally created later.
SERVICE="${OURSTEPS_TYPESAFE_KEYCHAIN_SERVICE:-grocery-promo-optimizer.typesafe}"
ACCOUNT="${USER:-oursteps}"

usage() {
  cat <<'EOF'
Usage:
  scripts/with_typesafe_keychain.sh set
  scripts/with_typesafe_keychain.sh status
  scripts/with_typesafe_keychain.sh run <command> [args...]
  scripts/with_typesafe_keychain.sh optional-run <command> [args...]

The key stays in macOS Keychain and is exported only to the child process.
TypeSafe diagnostics are optional and never control crawler behavior.
EOF
}

load_key() {
  security find-generic-password -a "$ACCOUNT" -s "$SERVICE" -w 2>/dev/null
}

case "${1:-}" in
  set)
    printf 'Paste TypeSafe API key (input hidden): '
    IFS= read -r -s key
    printf '\n'
    [[ -n "$key" ]] || { echo "No key entered." >&2; exit 2; }
    security add-generic-password -a "$ACCOUNT" -s "$SERVICE" -w "$key" -U >/dev/null
    unset key
    echo "Stored TypeSafe API key in macOS Keychain."
    ;;
  status)
    if security find-generic-password -a "$ACCOUNT" -s "$SERVICE" >/dev/null 2>&1; then
      echo "TypeSafe API key is present in macOS Keychain."
    else
      echo "TypeSafe API key is not present in macOS Keychain."
      exit 1
    fi
    ;;
  run)
    shift
    [[ $# -gt 0 ]] || { usage; exit 2; }
    export TYPESAFE_API_KEY
    TYPESAFE_API_KEY="$(load_key)"
    export OURSTEPS_TYPESAFE_ENABLED=true
    exec "$@"
    ;;
  optional-run)
    shift
    [[ $# -gt 0 ]] || { usage; exit 2; }
    if TYPESAFE_API_KEY="$(load_key)"; then
      export TYPESAFE_API_KEY
      export OURSTEPS_TYPESAFE_ENABLED=true
      echo "TypeSafe diagnostics enabled for this run." >&2
    else
      unset TYPESAFE_API_KEY || true
      export OURSTEPS_TYPESAFE_ENABLED=false
      echo "TypeSafe key absent; deterministic telemetry only." >&2
    fi
    exec "$@"
    ;;
  *)
    usage
    exit 2
    ;;
esac
