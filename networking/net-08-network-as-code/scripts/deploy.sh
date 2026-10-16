#!/usr/bin/env bash
# Render intent -> deploy with containerlab -> post-deploy setup (forwarding, ACLs, host
# addressing, listeners). The generator refuses invalid intent (exit 2), so nothing that
# failed validation can reach `containerlab deploy`.
#
# Needs: Docker, containerlab, passwordless sudo (containerlab + nsenter), python deps.
# Env:   PYTHON (default python3), EVIDENCE (default evidence/local)
set -euo pipefail
LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$LAB_DIR"
PY="${PYTHON:-python3}"
EVIDENCE="${EVIDENCE:-evidence/local}"

"$PY" -m netaac generate --out build
start=$(date +%s)
sudo containerlab deploy --topo build/topology.clab.yml --reconfigure
echo "containerlab deploy took $(( $(date +%s) - start )) s"
"$PY" -m netaac post-deploy --evidence "$EVIDENCE"
