#!/usr/bin/env bash
# Lab N6 runner: deploy -> baseline assertions -> 3 fault scenarios (inject, measure, repair) -> report -> destroy.
#
#   ./run.sh                      everything (needs containerlab + Docker + sudo for containerlab)
#   KEEP=1 ./run.sh               leave the lab running afterwards (destroy: sudo containerlab destroy -t topology.clab.yml --cleanup)
#   SCENARIOS="bfd" ./run.sh      a subset (the report then marks the others MISSING = FAIL, on purpose)
#
# Scenarios (all break ISP-A while 10 ms UDP probes flow both ways between h1 and srv):
#   bfd        the ISP-A access circuit (NID bridge) stops forwarding; both routers' ports stay UP; BFD on
#   holdtimer  same failure, BFD removed from the ISP-A session on both ends: only the BGP hold timer (30 s) can see it
#   linkdown   ent's own ISP-A port goes down (carrier loss); BFD on
# Each scenario ends by repairing the fault and proving failback (routes and traffic back on ISP-A).
#
# Evidence: $EVIDENCE (default evidence/scratch-<ts>/): raw probe logs, poller samples, vtysh JSON, traceroutes,
# FRR logs, per-scenario result.json, report.md. The exit code is measure.py report's: 0 only if every check passed.
set -euo pipefail

LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOPO="$LAB_DIR/topology.clab.yml"
LAB=net06
RUN_ID="${RUN_ID:-local-$(date -u +%Y%m%dT%H%M%SZ)}"
EVIDENCE="${EVIDENCE:-$LAB_DIR/evidence/scratch-$RUN_ID}"
SCENARIOS="${SCENARIOS:-bfd holdtimer linkdown}"
PROBE_INTERVAL_MS="${PROBE_INTERVAL_MS:-10}"
PRE_S="${PRE_S:-5}"      # clean traffic before the fault
HOLD_S="${HOLD_S:-5}"    # traffic on the backup path after convergence, before the repair
POST_S="${POST_S:-5}"    # traffic after failback
KEEP="${KEEP:-0}"
PY="${PYTHON:-python3}"
CLAB="${CLAB:-containerlab}"
# containerlab needs root. Export SUDO= (empty) if your containerlab already runs without sudo.
if [ -z "${SUDO+set}" ]; then
  SUDO=""
  [ "$(id -u)" -eq 0 ] || SUDO="sudo"
fi

mkdir -p "$EVIDENCE"
EVIDENCE="$(cd "$EVIDENCE" && pwd)"

SRV_IP=198.51.100.10
H1_IP=203.0.113.10
SRV_NET=198.51.100.0/24
OWN_NET=203.0.113.0/24

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$EVIDENCE/run.log" >&2; }
die() { log "ERROR: $*"; exit 1; }
node() { echo "clab-$LAB-$1"; }
dx() { local n=$1; shift; docker exec "$(node "$n")" "$@"; }
vt() { # vt <node> "cmd 1" ["cmd 2" ...]  -> vtysh -c ... -c ...
  local n=$1; shift
  local args=()
  for c in "$@"; do args+=(-c "$c"); done
  docker exec "$(node "$n")" vtysh "${args[@]}"
}
now() { date +%s.%N; }
measure() { "$PY" "$LAB_DIR/measure.py" "$@"; }
cap() { # cap <file> <node> "vtysh cmd": capture, never abort the run (a missing file becomes a FAIL in measure.py)
  local f=$1 n=$2; shift 2
  vt "$n" "$@" >"$f" 2>>"$EVIDENCE/run.log" || log "WARN: capture failed: $n: $*"
}
trace() { # trace <node> <dst> <file>
  dx "$1" traceroute -n -q 1 -w 1 -m 8 "$2" >"$3" 2>&1 || true
}

wait_until() { # wait_until <timeout_s> <description> <command...>
  local timeout=$1 desc=$2
  shift 2
  local start=$SECONDS
  until "$@" >/dev/null 2>&1; do
    if ((SECONDS - start >= timeout)); then
      log "TIMEOUT after ${timeout}s: $desc"
      return 1
    fi
    sleep 0.5
  done
  log "ok: $desc ($((SECONDS - start))s)"
}

# conditions (each reads live router state; measure.py cond does the parsing)
c_established() { local n=$1; shift; vt "$n" "show bgp ipv4 unicast summary json" | measure cond established "$@"; }
c_nexthop() { local n=$1 pfx=$2; shift 2; vt "$n" "show ip route $pfx json" | measure cond nexthop "$pfx" "$@"; }
c_bfd() { vt ent "show bfd peers json" | measure cond bfd "$1" "$2"; }
c_failed_over() { c_nexthop ent "$SRV_NET" 10.0.2.1 && c_nexthop inet "$OWN_NET" 10.1.2.1; }
c_primary() {
  c_established ent 10.0.1.1 10.0.2.1 10.255.0.2 && c_established inet 10.1.1.1 10.1.2.1 &&
    c_nexthop ent "$SRV_NET" 10.0.1.1 && c_nexthop inet "$OWN_NET" 10.1.1.1
}

collect_logs() {
  mkdir -p "$EVIDENCE/logs"
  for n in ent core ispa ispb inet; do
    docker cp "$(node "$n"):/tmp/frr.log" "$EVIDENCE/logs/$n-frr.log" >/dev/null 2>&1 || true
    vt "$n" "show running-config" >"$EVIDENCE/logs/$n-running.conf" 2>/dev/null || true
  done
}

cleanup() {
  local rc=$?
  set +e
  collect_logs
  if [ ! -f "$EVIDENCE/report.md" ]; then # aborted part-way: still produce the report (missing parts = FAIL)
    measure report "$EVIDENCE" "$EVIDENCE/report.md" >>"$EVIDENCE/run.log" 2>&1
    [ "$rc" -ne 0 ] || rc=1
  fi
  if [ "$KEEP" = "1" ]; then
    log "KEEP=1: lab left running. Destroy with: $SUDO $CLAB destroy -t $TOPO --cleanup"
  else
    log "destroying the lab"
    $SUDO "$CLAB" destroy -t "$TOPO" --cleanup >>"$EVIDENCE/run.log" 2>&1 || log "WARN: destroy failed"
  fi
  exit "$rc"
}

deploy() {
  command -v "$CLAB" >/dev/null || die "containerlab not found"
  command -v docker >/dev/null || die "docker not found"
  log "deploying $TOPO (run $RUN_ID, evidence $EVIDENCE)"
  trap cleanup EXIT
  $SUDO "$CLAB" deploy -t "$TOPO" --reconfigure 2>&1 | tee -a "$EVIDENCE/deploy.log"
  for n in ent core ispa ispb inet; do
    wait_until 90 "$n: FRR answers vtysh" vt "$n" "show version" || die "$n FRR did not start"
  done
}

setup_nodes() {
  # Routers: drop Docker's management default route so no lab traffic can escape through eth0.
  for n in ent core ispa ispb inet; do dx "$n" ip route del default 2>/dev/null || true; done
  # ISP-A's access circuit: a transparent L2 bridge between ent and ISP-A.
  dx nida sh -c 'ip link add br0 type bridge stp_state 0 && ip link set eth1 master br0 && ip link set eth2 master br0 &&
                 ip link set eth1 up && ip link set eth2 up && ip link set br0 up'
  dx h1 sh -c "ip addr add $H1_IP/24 dev eth1 && ip link set eth1 up && ip route replace default via 203.0.113.1 dev eth1"
  dx srv sh -c "ip addr add $SRV_IP/24 dev eth1 && ip link set eth1 up && ip route replace default via 198.51.100.1 dev eth1"
  # Preflight: everything the run needs exists where it runs (fail here, not halfway through a scenario).
  for n in h1 srv; do
    dx "$n" python3 -c 'import socket' || die "python3 missing in $n"
    dx "$n" sh -c 'command -v traceroute >/dev/null' || die "traceroute missing in $n"
  done
  for n in ent inet; do dx "$n" python3 -c 'import json' || die "python3 missing in $n"; done
}

wait_primary() {
  wait_until "${1:-150}" "converged on ISP-A (sessions up, ent and inet FIBs via ISP-A)" c_primary
}

capture_baseline() {
  local b="$EVIDENCE/baseline"
  mkdir -p "$b"
  wait_until 60 "ent: BFD to ISP-A up" c_bfd 10.0.1.1 up || true
  wait_until 60 "ent: BFD to ISP-B up" c_bfd 10.0.2.1 up || true
  wait_until 60 "core: Internet route installed via ent" c_nexthop core "$SRV_NET" 10.0.0.1 || true
  sleep 3
  cap "$b/ent_summary.json" ent "show bgp ipv4 unicast summary json"
  cap "$b/inet_summary.json" inet "show bgp ipv4 unicast summary json"
  cap "$b/core_summary.json" core "show bgp ipv4 unicast summary json"
  cap "$b/ent_adv_ispa.json" ent "show bgp ipv4 unicast neighbors 10.0.1.1 advertised-routes json"
  cap "$b/ent_adv_ispb.json" ent "show bgp ipv4 unicast neighbors 10.0.2.1 advertised-routes json"
  cap "$b/ent_rcv_ispa.json" ent "show bgp ipv4 unicast neighbors 10.0.1.1 received-routes json"
  cap "$b/ent_rcv_ispb.json" ent "show bgp ipv4 unicast neighbors 10.0.2.1 received-routes json"
  cap "$b/ent_rib.json" ent "show bgp ipv4 unicast json"
  cap "$b/ent_bgp_srv.json" ent "show bgp ipv4 unicast $SRV_NET json"
  cap "$b/ent_bgp_default.json" ent "show bgp ipv4 unicast 0.0.0.0/0 json"
  cap "$b/inet_bgp_own.json" inet "show bgp ipv4 unicast $OWN_NET json"
  cap "$b/ent_bfd.json" ent "show bfd peers json"
  cap "$b/ispa_bfd.json" ispa "show bfd peers json"
  cap "$b/ent_route_srv.json" ent "show ip route $SRV_NET json"
  cap "$b/core_route_srv.json" core "show ip route $SRV_NET json"
  trace h1 "$SRV_IP" "$b/trace_h1_srv.txt"
  trace srv "$H1_IP" "$b/trace_srv_h1.txt"
  # human-readable copies for the reader of the evidence
  vt ent "show bgp ipv4 unicast" "show bgp ipv4 unicast summary" "show bfd peers" "show ip route" >"$b/ent_show.txt" 2>&1 || true
  vt inet "show bgp ipv4 unicast" "show bgp ipv4 unicast $OWN_NET" >"$b/inet_show.txt" 2>&1 || true
  log "baseline checks:"
  measure baseline "$b" | tee -a "$EVIDENCE/run.log"
}

set_bfd() { # set_bfd on|off  - BFD on the ent<->ISP-A session, BOTH ends (ISP-A first)
  if [ "$1" = on ]; then
    vt ispa "configure terminal" "router bgp 64501" "neighbor 10.0.1.2 bfd" "neighbor 10.0.1.2 bfd profile fast"
    vt ent "configure terminal" "router bgp 65010" "neighbor 10.0.1.1 bfd" "neighbor 10.0.1.1 bfd profile fast"
  else
    vt ispa "configure terminal" "router bgp 64501" "no neighbor 10.0.1.2 bfd"
    vt ent "configure terminal" "router bgp 65010" "no neighbor 10.0.1.1 bfd"
  fi
}

fault_inject() {
  case $1 in
    bfd | holdtimer) dx nida ip link set br0 down ;; # circuit stops forwarding; both routers' ports stay UP
    linkdown) dx ent ip link set eth1 down ;;        # ent's own port loses carrier
  esac
}
fault_restore() {
  case $1 in
    bfd | holdtimer) dx nida ip link set br0 up ;;
    linkdown) dx ent ip link set eth1 up ;;
  esac
}

start_pollers() {
  for n in ent inet; do dx "$n" sh -c 'rm -rf /tmp/netlab && mkdir -p /tmp/netlab'; done
  docker exec -d "$(node ent)" python3 /lab/tools/poll.py --out /tmp/netlab/poll.jsonl --stop /tmp/netlab/stop \
    --cmd "vtysh -c 'show bgp ipv4 unicast summary json' -c 'show ip route $SRV_NET json'"
  docker exec -d "$(node inet)" python3 /lab/tools/poll.py --out /tmp/netlab/poll.jsonl --stop /tmp/netlab/stop \
    --cmd "vtysh -c 'show ip route $OWN_NET json'"
}

start_probes() {
  for n in h1 srv; do dx "$n" sh -c 'rm -rf /tmp/netlab && mkdir -p /tmp/netlab'; done
  docker exec -d "$(node srv)" python3 /lab/tools/probe.py recv --port 5001 --out /tmp/netlab/h1_to_srv.recv --stop /tmp/netlab/stop-recv
  docker exec -d "$(node h1)" python3 /lab/tools/probe.py recv --port 5002 --out /tmp/netlab/srv_to_h1.recv --stop /tmp/netlab/stop-recv
  sleep 1
  docker exec -d "$(node h1)" python3 /lab/tools/probe.py send --dst "$SRV_IP" --port 5001 --flow h1_to_srv \
    --interval-ms "$PROBE_INTERVAL_MS" --out /tmp/netlab/h1_to_srv.sent --stop /tmp/netlab/stop-send
  docker exec -d "$(node srv)" python3 /lab/tools/probe.py send --dst "$H1_IP" --port 5002 --flow srv_to_h1 \
    --interval-ms "$PROBE_INTERVAL_MS" --out /tmp/netlab/srv_to_h1.sent --stop /tmp/netlab/stop-send
}

stop_and_collect() { # stop_and_collect <scenario dir>
  local d=$1
  for n in h1 srv; do dx "$n" touch /tmp/netlab/stop-send; done
  sleep 1 # in-flight packets land before the receivers stop
  for n in h1 srv; do dx "$n" touch /tmp/netlab/stop-recv; done
  for n in ent inet; do dx "$n" touch /tmp/netlab/stop; done
  sleep 1
  docker cp "$(node h1):/tmp/netlab/h1_to_srv.sent" "$d/probes/" || log "WARN: no h1_to_srv.sent"
  docker cp "$(node srv):/tmp/netlab/h1_to_srv.recv" "$d/probes/" || log "WARN: no h1_to_srv.recv"
  docker cp "$(node srv):/tmp/netlab/srv_to_h1.sent" "$d/probes/" || log "WARN: no srv_to_h1.sent"
  docker cp "$(node h1):/tmp/netlab/srv_to_h1.recv" "$d/probes/" || log "WARN: no srv_to_h1.recv"
  docker cp "$(node ent):/tmp/netlab/poll.jsonl" "$d/poll/ent.jsonl" || log "WARN: no ent poll"
  docker cp "$(node inet):/tmp/netlab/poll.jsonl" "$d/poll/inet.jsonl" || log "WARN: no inet poll"
  gzip -9f "$d"/probes/* "$d"/poll/* 2>/dev/null || true # ~20x smaller; measure.py reads .gz directly
}

write_meta() { # write_meta <file> key=value ...  (values that parse as JSON stay typed)
  "$PY" - "$@" <<'EOF'
import json, sys
out, meta = sys.argv[1], {}
for kv in sys.argv[2:]:
    k, _, v = kv.partition("=")
    try:
        meta[k] = json.loads(v)
    except ValueError:
        meta[k] = v
with open(out, "w") as fh:
    json.dump(meta, fh, indent=2)
EOF
}

run_scenario() {
  local s=$1
  local d="$EVIDENCE/$s"
  mkdir -p "$d"/{pre,probes,poll,trace}
  log "=== scenario $s"
  if [ "$s" = holdtimer ]; then set_bfd off; else set_bfd on; fi
  sleep 2
  wait_primary 150 || log "WARN: not converged on ISP-A before $s (the scenario's checks will show it)"
  if [ "$s" = holdtimer ]; then
    wait_until 30 "ent: no BFD session to ISP-A" c_bfd 10.0.1.1 absent || true
  else
    wait_until 60 "ent: BFD to ISP-A up" c_bfd 10.0.1.1 up || true
  fi
  sleep 3 # settle: no session flap in progress when the clock starts
  cap "$d/pre/ent_summary.json" ent "show bgp ipv4 unicast summary json"
  cap "$d/pre/ent_bfd.json" ent "show bfd peers json"

  start_pollers
  start_probes
  sleep "$PRE_S"

  local t_fault t_restore timeout ok_fail=false ok_back=false
  t_fault=$(now)
  fault_inject "$s"
  log "fault injected ($s) at $t_fault"
  timeout=20
  [ "$s" = holdtimer ] && timeout=60
  wait_until "$timeout" "failed over to ISP-B (ent + inet FIBs)" c_failed_over && ok_fail=true
  sleep "$HOLD_S"
  trace h1 "$SRV_IP" "$d/trace/during_h1_srv.txt"
  trace srv "$H1_IP" "$d/trace/during_srv_h1.txt"

  t_restore=$(now)
  fault_restore "$s"
  log "fault repaired at $t_restore"
  wait_primary 150 && ok_back=true
  sleep "$POST_S"
  trace h1 "$SRV_IP" "$d/trace/after_h1_srv.txt"
  trace srv "$H1_IP" "$d/trace/after_srv_h1.txt"
  stop_and_collect "$d"

  write_meta "$d/meta.json" scenario="$s" t_fault="$t_fault" t_restore="$t_restore" \
    probe_interval_ms="$PROBE_INTERVAL_MS" run_saw_failover="$ok_fail" run_saw_failback="$ok_back" \
    bgp_timers='{"keepalive_s":10,"hold_s":30,"connect_s":5}' bfd_profile='{"tx_ms":100,"rx_ms":100,"multiplier":3}'
  log "scenario $s checks:"
  measure scenario "$d" | tee -a "$EVIDENCE/run.log"
}

main() {
  deploy
  setup_nodes
  write_meta "$EVIDENCE/meta.json" lab=net06 run_id="$RUN_ID" started="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    frr_image="$(grep -m1 -o 'quay.io/frrouting/frr:[^ ]*' "$TOPO")" \
    host_image="$(grep -m1 -o 'nicolaka/netshoot:[^ ]*' "$TOPO")" \
    clab_version="$($CLAB version 2>/dev/null | awk '/version:/{print $2; exit}')" kernel="$(uname -r)" \
    probe_interval_ms="$PROBE_INTERVAL_MS" scenarios="$SCENARIOS"
  wait_primary 180 || log "WARN: initial convergence incomplete (baseline checks will show what is missing)"
  capture_baseline
  for s in $SCENARIOS; do run_scenario "$s"; done
  log "report:"
  local rc=0
  measure report "$EVIDENCE" "$EVIDENCE/report.md" | tee -a "$EVIDENCE/run.log" || rc=$?
  log "report: $EVIDENCE/report.md"
  return "$rc"
}

main "$@"
