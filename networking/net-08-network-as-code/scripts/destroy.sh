#!/usr/bin/env bash
# Tear the lab down and prove nothing survived (containers labelled with the lab name).
set -uo pipefail
LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$LAB_DIR"
LAB_NAME="${LAB_NAME:-net08}"

if [ -f build/topology.clab.yml ]; then
  sudo containerlab destroy --topo build/topology.clab.yml --cleanup || echo "containerlab destroy returned non-zero; checking survivors anyway"
fi
survivors="$(docker ps -a --filter "label=containerlab=${LAB_NAME}" --format '{{.Names}}')"
if [ -n "$survivors" ]; then
  echo "SURVIVORS after destroy:"; echo "$survivors"
  docker rm -f $survivors >/dev/null 2>&1 || true
  exit 1
fi
echo "destroy: 0 containers left for lab ${LAB_NAME}"
