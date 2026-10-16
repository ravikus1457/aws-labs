#!/usr/bin/env bash
# Dump raw state from every node for post-mortem (runs on failure; never fails itself).
set -uo pipefail
OUT="${1:-evidence/local/diag}"
LAB_NAME="${LAB_NAME:-net08}"
mkdir -p "$OUT"
docker ps -a --filter "label=containerlab=${LAB_NAME}" > "$OUT/docker-ps.txt" 2>&1
for c in $(docker ps --filter "label=containerlab=${LAB_NAME}" --format '{{.Names}}'); do
  node="${c#clab-${LAB_NAME}-}"
  docker logs --tail 200 "$c" > "$OUT/$node.docker-logs.txt" 2>&1
  if docker exec "$c" test -x /usr/bin/vtysh 2>/dev/null; then
    {
      for cmd in "show running-config" "show bgp summary" "show ip ospf neighbor" "show ip route" "show interface brief"; do
        echo "### $cmd"; docker exec "$c" vtysh -c "$cmd" 2>&1
      done
    } > "$OUT/$node.frr.txt"
    pid="$(docker inspect -f '{{.State.Pid}}' "$c")"
    sudo -n nsenter -t "$pid" -n iptables-save -c > "$OUT/$node.iptables.txt" 2>&1
  else
    { docker exec "$c" ip -br addr; docker exec "$c" ip route; docker exec "$c" ss -tln; } > "$OUT/$node.host.txt" 2>&1
  fi
done
echo "diagnostics in $OUT"
