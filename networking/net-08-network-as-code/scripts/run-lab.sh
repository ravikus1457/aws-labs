#!/usr/bin/env bash
# The whole lab on any Linux box with Docker + containerlab + sudo - the same phases CI runs.
#   scripts/run-lab.sh            run everything, destroy at the end (even on failure)
#   KEEP=1 scripts/run-lab.sh     leave the lab up to poke at it (docker exec -it clab-net08-br1-r1 vtysh)
set -uo pipefail
LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$LAB_DIR"
PY="${PYTHON:-python3}"
export EVIDENCE="${EVIDENCE:-evidence/local-$(date -u +%Y%m%dT%H%M%SZ)}"
REQUIRED="validate,change-demo,generate,post-deploy,converge,test-initial,drift-demo,converge-post-heal,test-post-heal"

cleanup() { [ "${KEEP:-0}" = "1" ] || scripts/destroy.sh; }
trap cleanup EXIT

rc=0
"$PY" -m netaac validate --evidence "$EVIDENCE" || rc=1
"$PY" -m netaac change-demo --evidence "$EVIDENCE" || rc=1
"$PY" -m netaac check-rendered --evidence "$EVIDENCE" || rc=1
if [ "$rc" = 0 ] && scripts/deploy.sh; then
  "$PY" -m netaac wait-converged --timeout 180 --evidence "$EVIDENCE" || rc=1
  "$PY" -m netaac test --phase test-initial --evidence "$EVIDENCE" || rc=1
  "$PY" -m netaac drift-demo --evidence "$EVIDENCE" || rc=1
  "$PY" -m netaac wait-converged --timeout 120 --phase converge-post-heal --evidence "$EVIDENCE" || rc=1
  "$PY" -m netaac test --phase test-post-heal --evidence "$EVIDENCE" || rc=1
else
  rc=1
fi
"$PY" -m netaac report --evidence "$EVIDENCE" --require "$REQUIRED" || rc=1
echo "evidence: $EVIDENCE/SUMMARY.md"
exit "$rc"
