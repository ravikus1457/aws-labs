#!/usr/bin/env bash
# Run every lab in order, each one fully (apply -> exercise -> destroy).
# Stops on the first failure unless --keep-going.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

KEEP_GOING=0
[ "${1:-}" = "--keep-going" ] && KEEP_GOING=1

fail=0
for lab in "$ROOT"/labs/*/; do
  name="$(basename "$lab")"
  echo
  echo "############################################################"
  echo "# RUNNING LAB: $name"
  echo "############################################################"
  if ! "$HERE/run-lab.sh" "labs/$name"; then
    echo "LAB FAILED: $name" >&2
    fail=1
    [ "$KEEP_GOING" -eq 1 ] || exit 1
  fi
done
exit "$fail"
