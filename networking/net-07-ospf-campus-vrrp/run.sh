#!/usr/bin/env bash
# Lab N7 runner: build -> deploy -> baseline assertions -> kill the VRRP master (core1) under traffic -> measure
# -> power core1 back on, re-cable it, prove preemption/failback -> report -> destroy.
#
#   ./run.sh               everything (needs containerlab + Docker + sudo for containerlab)
#   KEEP=1 ./run.sh        leave the lab running (destroy: sudo containerlab destroy -t topology.clab.yml --cleanup)
#
# Traffic during the scenario: 10 ms UDP probes both ways between srv and each user host (h10 on VLAN 10, whose
# gateway master is core1; h20 on VLAN 20, whose master is core2 - the control group).
# The fault is `docker kill` of core1 = power loss: its processes die and every cable to it goes dark. The repair
# is `docker start` + re-creating its cables (`containerlab tools veth create`, because a container's veths die
# with it): first the trunk to the users + VLAN interfaces, then keepalived, and only GATE_S seconds later the
# backbone links. Until OSPF has a route to the servers the uplink track script must keep core1 from taking the
# gateway back - the lab checks that it did, sample by sample.
#
# Evidence: $EVIDENCE (default evidence/scratch-<ts>/). Exit code = measure.py report's (0 only if all checks pass).
set -euo pipefail

LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOPO="$LAB_DIR/topology.clab.yml"
LAB=net07
CORE_IMAGE=net07-core:local
RUN_ID="${RUN_ID:-local-$(date -u +%Y%m%dT%H%M%SZ)}"
EVIDENCE="${EVIDENCE:-$LAB_DIR/evidence/scratch-$RUN_ID}"
PROBE_INTERVAL_MS="${PROBE_INTERVAL_MS:-10}"
PRE_S="${PRE_S:-5}"
HOLD_S="${HOLD_S:-5}"
POST_S="${POST_S:-5}"
GATE_S="${GATE_S:-4}" # how long the returning core1 runs keepalived with no route to the servers (track-script test)
KEEP="${KEEP:-0}"
BUILD="${BUILD:-1}"
PY="${PYTHON:-python3}"
CLAB="${CLAB:-containerlab}"
# containerlab needs root. Export SUDO= (empty) if your containerlab already runs without sudo.
if [ -z "${SUDO+set}" ]; then
  SUDO=""
  [ "$(id -u)" -eq 0 ] || SUDO="sudo"
fi

mkdir -p "$EVIDENCE"
EVIDENCE="$(cd "$EVIDENCE" && pwd)"

SUMMARY=10.10.0.0/16
SRV_IP=10.100.0.10
H10_IP=10.10.10.10
H20_IP=10.10.20.10
VIP10=10.10.10.1
VIP20=10.10.20.1
DC_VIA_CORE1=10.0.11.1
DC_VIA_CORE2=10.0.12.1

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$EVIDENCE/run.log" >&2; }
die() { log "ERROR: $*"; exit 1; }
node() { echo "clab-$LAB-$1"; }
dx() { local n=$1; shift; docker exec "$(node "$n")" "$@"; }
vt() {
  local n=$1; shift
  local args=()
  for c in "$@"; do args+=(-c "$c"); done
  docker exec "$(node "$n")" vtysh "${args[@]}"
}
now() { date +%s.%N; }
measure() { "$PY" "$LAB_DIR/measure.py" "$@"; }
cap() { local f=$1 n=$2; shift 2; vt "$n" "$@" >"$f" 2>>"$EVIDENCE/run.log" || log "WARN: capture failed: $n: $*"; }
capx() { local f=$1 n=$2; shift 2; dx "$n" "$@" >"$f" 2>>"$EVIDENCE/run.log" || log "WARN: capture failed: $n: $*"; }
trace() { dx "$1" traceroute -n -q 1 -w 1 -m 8 "$2" >"$3" 2>&1 || true; }
running() { docker inspect -f '{{.State.Running}}' "$(node "$1")" 2>/dev/null || echo missing; }

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

# conditions (live state; measure.py cond does the parsing)
c_ospf_full() { local n=$1; shift; vt "$n" "show ip ospf neighbor json" | measure cond ospf-full "$@"; }
c_nexthop() { local n=$1 pfx=$2; shift 2; vt "$n" "show ip route $pfx json" | measure cond nexthop "$pfx" "$@"; }
c_vip() { dx "$1" ip -j -4 addr show dev "$2" | measure cond vip "$3" "$4"; } # c_vip <node> <dev> <ip> present|absent
c_addr() { dx "$1" ip -4 -o addr show dev "$2" | grep -q " $3/"; }
c_ospf_route() { dx "$1" ip -4 route show 10.100.0.0/24 proto 188 | grep -q via; }
c_both_ospf_routes() { c_ospf_route core1 && c_ospf_route core2; }
c_vlan_addrs() { c_addr "$1" eth1.10 "10.10.10.$2" && c_addr "$1" eth1.20 "10.10.20.$2"; }
c_roles() { # VLAN 10 on core1 only, VLAN 20 on core2 only
  c_vip core1 eth1.10 "$VIP10" present && c_vip core2 eth1.10 "$VIP10" absent &&
    c_vip core2 eth1.20 "$VIP20" present && c_vip core1 eth1.20 "$VIP20" absent
}
c_converged() {
  c_ospf_full dc 10.255.0.1 10.255.0.2 && c_ospf_full core1 10.255.0.2 10.255.0.3 &&
    c_ospf_full core2 10.255.0.1 10.255.0.3 && c_nexthop dc "$SUMMARY" "$DC_VIA_CORE1" "$DC_VIA_CORE2"
}
c_failed_over() { c_vip core2 eth1.10 "$VIP10" present && c_nexthop dc "$SUMMARY" "$DC_VIA_CORE2"; }
c_failed_back() { c_roles && c_nexthop dc "$SUMMARY" "$DC_VIA_CORE1" "$DC_VIA_CORE2"; }

collect_logs() {
  mkdir -p "$EVIDENCE/logs"
  for n in core1 core2 dc; do
    docker cp "$(node "$n"):/tmp/frr.log" "$EVIDENCE/logs/$n-frr.log" >/dev/null 2>&1 || true
    vt "$n" "show running-config" >"$EVIDENCE/logs/$n-running.conf" 2>/dev/null || true
  done
  for n in core1 core2; do
    docker cp "$(node "$n"):/tmp/keepalived.log" "$EVIDENCE/logs/$n-keepalived.log" >/dev/null 2>&1 || true
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

build() {
  if [ "$BUILD" = "1" ]; then
    log "building $CORE_IMAGE (FRR + keepalived)"
    docker build -q -t "$CORE_IMAGE" "$LAB_DIR" >>"$EVIDENCE/run.log" 2>&1 || die "image build failed"
  fi
  docker image inspect "$CORE_IMAGE" >/dev/null 2>&1 || die "$CORE_IMAGE missing (BUILD=1 builds it)"
}

deploy() {
  command -v "$CLAB" >/dev/null || die "containerlab not found"
  log "deploying $TOPO (run $RUN_ID, evidence $EVIDENCE)"
  trap cleanup EXIT
  $SUDO "$CLAB" deploy -t "$TOPO" --reconfigure 2>&1 | tee -a "$EVIDENCE/deploy.log"
  for n in core1 core2 dc; do
    wait_until 90 "$n: FRR answers vtysh" vt "$n" "show version" || die "$n FRR did not start"
  done
}

sw_port() { # sw_port <port> trunk | sw_port <port> access <vid>
  local p=$1
  dx sw ip link set "$p" master br0
  dx sw ip link set "$p" up
  if [ "$2" = trunk ]; then
    dx sw bridge vlan add dev "$p" vid 10
    dx sw bridge vlan add dev "$p" vid 20
  else
    dx sw bridge vlan add dev "$p" vid "$3" pvid untagged
  fi
  dx sw bridge vlan del dev "$p" vid 1 2>/dev/null || true
}

setup_sw() {
  dx sw sh -c 'ip link add br0 type bridge vlan_filtering 1 stp_state 0 && ip link set br0 up'
  sw_port eth1 trunk
  sw_port eth2 trunk
  sw_port eth3 access 10
  sw_port eth4 access 20
}

setup_core() { # 802.1Q sub-interfaces on the trunk; zebra then applies the addresses from frr.conf
  local n=$1 last=$2
  dx "$n" sh -c 'ip route del default 2>/dev/null
    ip link show eth1.10 >/dev/null 2>&1 || ip link add link eth1 name eth1.10 type vlan id 10
    ip link show eth1.20 >/dev/null 2>&1 || ip link add link eth1 name eth1.20 type vlan id 20
    ip link set eth1 up && ip link set eth1.10 up && ip link set eth1.20 up'
  wait_until 30 "$n: VLAN interfaces addressed" c_vlan_addrs "$n" "$last" || die "$n: zebra did not address eth1.10/eth1.20"
}

start_keepalived() {
  docker exec -d "$(node "$1")" sh -c 'exec keepalived -n -l -D -f /etc/keepalived/keepalived.conf >>/tmp/keepalived.log 2>&1'
}

setup_nodes() {
  setup_sw
  setup_core core1 2
  setup_core core2 3
  dx dc ip route del default 2>/dev/null || true
  dx h10 sh -c "ip addr add $H10_IP/24 dev eth1 && ip link set eth1 up && ip route replace default via $VIP10 dev eth1"
  dx h20 sh -c "ip addr add $H20_IP/24 dev eth1 && ip link set eth1 up && ip route replace default via $VIP20 dev eth1"
  dx srv sh -c "ip addr add $SRV_IP/24 dev eth1 && ip link set eth1 up && ip route replace default via 10.100.0.1 dev eth1"
  for n in h10 h20 srv; do
    dx "$n" python3 -c 'import socket' || die "python3 missing in $n"
    dx "$n" sh -c 'command -v traceroute >/dev/null' || die "traceroute missing in $n"
  done
  for n in core1 core2 dc; do dx "$n" python3 -c 'import json' || die "python3 missing in $n"; done
  dx core1 sh -c 'command -v keepalived >/dev/null' || die "keepalived missing in the core image"
}

capture_baseline() {
  local b="$EVIDENCE/baseline"
  mkdir -p "$b"
  dx h10 ping -c 2 -W 1 "$VIP10" >/dev/null 2>&1 || true # resolve the gateway (ARP) before reading it
  dx h20 ping -c 2 -W 1 "$VIP20" >/dev/null 2>&1 || true
  sleep 2
  for n in dc core1 core2; do cap "$b/${n}_ospf_nbr.json" "$n" "show ip ospf neighbor json"; done
  cap "$b/dc_routes.json" dc "show ip route json"
  cap "$b/dc_route_summary.json" dc "show ip route $SUMMARY json"
  : >"$b/dc_ecmp_flows.txt"
  for sp in $(seq 40001 40016); do
    dx dc ip route get "$H10_IP" ipproto udp sport "$sp" dport 33434 >>"$b/dc_ecmp_flows.txt" 2>&1 || true
  done
  for n in core1 core2; do
    for v in 10 20; do
      capx "$b/${n}_addr$v.json" "$n" ip -j -4 addr show dev "eth1.$v"
      capx "$b/${n}_link$v.json" "$n" ip -j link show dev "eth1.$v"
    done
  done
  capx "$b/h10_neigh.json" h10 ip -j neigh show "$VIP10" dev eth1
  capx "$b/h20_neigh.json" h20 ip -j neigh show "$VIP20" dev eth1
  trace h10 "$SRV_IP" "$b/trace_h10_srv.txt"
  trace h20 "$SRV_IP" "$b/trace_h20_srv.txt"
  trace srv "$H10_IP" "$b/trace_srv_h10.txt"
  dx core1 keepalived --version >"$b/keepalived_version.txt" 2>&1 || true
  vt dc "show ip ospf database" "show ip route" >"$b/dc_show.txt" 2>&1 || true
  vt core1 "show ip ospf" "show ip ospf database" "show ip route" >"$b/core1_show.txt" 2>&1 || true
  log "baseline checks:"
  measure baseline "$b" | tee -a "$EVIDENCE/run.log"
}

start_pollers() {
  dx dc sh -c 'rm -rf /tmp/netlab && mkdir -p /tmp/netlab'
  dx core2 sh -c 'rm -rf /tmp/netlab && mkdir -p /tmp/netlab'
  docker exec -d "$(node dc)" python3 /lab/tools/poll.py --out /tmp/netlab/poll.jsonl --stop /tmp/netlab/stop \
    --cmd "vtysh -c 'show ip route $SUMMARY json' -c 'show ip ospf neighbor json'"
  docker exec -d "$(node core2)" python3 /lab/tools/poll.py --out /tmp/netlab/poll.jsonl --stop /tmp/netlab/stop \
    --cmd "ip -j -4 addr show dev eth1.10"
}

FLOWS="h10_to_srv:h10:$SRV_IP:5010 srv_to_h10:srv:$H10_IP:5011 h20_to_srv:h20:$SRV_IP:5020 srv_to_h20:srv:$H20_IP:5021"

start_probes() {
  for n in h10 h20 srv; do dx "$n" sh -c 'rm -rf /tmp/netlab && mkdir -p /tmp/netlab'; done
  local f flow src dst port rnode
  for f in $FLOWS; do
    IFS=: read -r flow src dst port <<<"$f"
    case $flow in h10_to_srv | h20_to_srv) rnode=srv ;; srv_to_h10) rnode=h10 ;; srv_to_h20) rnode=h20 ;; esac
    docker exec -d "$(node "$rnode")" python3 /lab/tools/probe.py recv --port "$port" --out "/tmp/netlab/$flow.recv" --stop /tmp/netlab/stop-recv
  done
  sleep 1
  for f in $FLOWS; do
    IFS=: read -r flow src dst port <<<"$f"
    docker exec -d "$(node "$src")" python3 /lab/tools/probe.py send --dst "$dst" --port "$port" --flow "$flow" \
      --interval-ms "$PROBE_INTERVAL_MS" --out "/tmp/netlab/$flow.sent" --stop /tmp/netlab/stop-send
  done
}

stop_and_collect() {
  local d=$1 f flow src dst port rnode
  for n in h10 h20 srv; do dx "$n" touch /tmp/netlab/stop-send; done
  sleep 1
  for n in h10 h20 srv; do dx "$n" touch /tmp/netlab/stop-recv; done
  for n in dc core2 core1; do dx "$n" touch /tmp/netlab/stop 2>/dev/null || true; done
  sleep 1
  for f in $FLOWS; do
    IFS=: read -r flow src dst port <<<"$f"
    case $flow in h10_to_srv | h20_to_srv) rnode=srv ;; srv_to_h10) rnode=h10 ;; srv_to_h20) rnode=h20 ;; esac
    docker cp "$(node "$src"):/tmp/netlab/$flow.sent" "$d/probes/" || log "WARN: no $flow.sent"
    docker cp "$(node "$rnode"):/tmp/netlab/$flow.recv" "$d/probes/" || log "WARN: no $flow.recv"
  done
  for n in dc core2 core1; do
    docker cp "$(node "$n"):/tmp/netlab/poll.jsonl" "$d/poll/$n.jsonl" || log "WARN: no $n poll"
  done
  gzip -9f "$d"/probes/* "$d"/poll/* 2>/dev/null || true
}

write_meta() {
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

cable_access() { # core1's veths died with it. Step 1: the user side only (trunk to sw + VLAN interfaces)
  $SUDO "$CLAB" tools veth create -a "$(node core1):eth1" -b "$(node sw):eth1" >>"$EVIDENCE/run.log" 2>&1
  sw_port eth1 trunk
  setup_core core1 2
}

cable_backbone() { # step 2: the links to dc and core2 (OSPF). Verify zebra re-addressed both ends of each.
  $SUDO "$CLAB" tools veth create -a "$(node core1):eth2" -b "$(node dc):eth1" >>"$EVIDENCE/run.log" 2>&1
  $SUDO "$CLAB" tools veth create -a "$(node core1):eth3" -b "$(node core2):eth3" >>"$EVIDENCE/run.log" 2>&1
  local spec n ifc ip
  for spec in dc:eth1:10.0.11.2/30 core2:eth3:10.0.0.2/30 core1:eth2:10.0.11.1/30 core1:eth3:10.0.0.1/30; do
    IFS=: read -r n ifc ip <<<"$spec"
    dx "$n" ip link set "$ifc" up || true
    if ! wait_until 10 "$n:$ifc readdressed by zebra" c_addr "$n" "$ifc" "${ip%/*}"; then
      log "WARN: $n:$ifc had no address after re-cabling; adding it by hand"
      dx "$n" ip addr add "$ip" dev "$ifc" || true
    fi
  done
}

run_scenario() {
  local s=master_kill
  local d="$EVIDENCE/$s"
  mkdir -p "$d"/{pre,probes,poll,trace,during,after}
  log "=== scenario $s: kill core1 (VLAN 10 gateway master) under traffic"
  wait_until 60 "converged (OSPF full, dc ECMP)" c_converged || log "WARN: not converged before the fault"
  wait_until 30 "VRRP roles: core1 = VLAN 10 master, core2 = VLAN 20 master" c_roles || log "WARN: roles not as designed"
  for n in core1 core2; do capx "$d/pre/${n}_addr10.json" "$n" ip -j -4 addr show dev eth1.10; done
  cap "$d/pre/dc_route_summary.json" dc "show ip route $SUMMARY json"

  start_pollers
  start_probes
  sleep "$PRE_S"

  local t_fault t_restore t_wired t_keepalived ok_fail=false ok_back=false run_after_kill run_before_restore
  t_fault=$(now)
  docker kill "$(node core1)" >/dev/null
  log "fault: core1 killed at $t_fault"
  sleep 0.2
  run_after_kill=$(running core1)
  wait_until 30 "failed over (core2 owns $VIP10, dc routes via core2 only)" c_failed_over && ok_fail=true
  sleep "$HOLD_S"
  trace h10 "$SRV_IP" "$d/trace/during_h10_srv.txt"
  trace srv "$H10_IP" "$d/trace/during_srv_h10.txt"
  capx "$d/during/h10_neigh.json" h10 ip -j neigh show "$VIP10" dev eth1
  capx "$d/during/core2_link10.json" core2 ip -j link show dev eth1.10
  run_before_restore=$(running core1)

  t_restore=$(now)
  docker start "$(node core1)" >/dev/null
  log "repair: core1 powered on at $t_restore"
  cable_access # (setup_core inside waits for zebra to address the VLAN interfaces, i.e. for FRR to be up)
  wait_until 60 "core1: FRR answers vtysh" vt core1 "show version" || log "WARN: core1 FRR not answering"
  dx core1 sh -c 'rm -rf /tmp/netlab && mkdir -p /tmp/netlab'
  docker exec -d "$(node core1)" python3 /lab/tools/poll.py --out /tmp/netlab/poll.jsonl --stop /tmp/netlab/stop \
    --cmd "ip -j -4 addr show dev eth1.10" --cmd "ip -4 route show 10.100.0.0/24 proto 188"
  sleep 0.5
  # keepalived starts while core1 can see its users but has NO route to the servers yet (backbone still dark):
  # the uplink track script must keep it from taking the gateway back. GATE_S seconds of that, then the backbone.
  t_keepalived=$(now)
  start_keepalived core1
  sleep "$GATE_S"
  t_wired=$(now)
  cable_backbone
  log "core1: keepalived started at $t_keepalived, backbone re-cabled at $t_wired"
  wait_until 120 "failed back (core1 owns $VIP10 again, dc ECMP restored)" c_failed_back && ok_back=true
  sleep "$POST_S"
  trace h10 "$SRV_IP" "$d/trace/after_h10_srv.txt"
  capx "$d/after/h10_neigh.json" h10 ip -j neigh show "$VIP10" dev eth1
  capx "$d/after/core1_link10.json" core1 ip -j link show dev eth1.10
  stop_and_collect "$d"

  write_meta "$d/meta.json" scenario="$s" t_fault="$t_fault" t_restore="$t_restore" t_wired="$t_wired" \
    t_keepalived="$t_keepalived" gate_s="$GATE_S" core1_running_after_kill="$run_after_kill" \
    core1_running_before_restore="$run_before_restore" probe_interval_ms="$PROBE_INTERVAL_MS" \
    run_saw_failover="$ok_fail" run_saw_failback="$ok_back" \
    vrrp='{"advert_int_s":0.2,"priority_master":200,"priority_backup":100,"track_weight":-150}' \
    ospf_timers='{"hello_s":1,"dead_s":4}'
  log "scenario $s checks:"
  measure scenario "$d" | tee -a "$EVIDENCE/run.log"
}

main() {
  build
  deploy
  setup_nodes
  write_meta "$EVIDENCE/meta.json" lab=net07 run_id="$RUN_ID" started="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    frr_image="$(grep -m1 -o 'quay.io/frrouting/frr:[^ ]*' "$TOPO")" core_image="$CORE_IMAGE" \
    keepalived="$(dx core1 keepalived --version 2>&1 | head -n1)" \
    host_image="$(grep -m1 -o 'nicolaka/netshoot:[^ ]*' "$TOPO")" \
    clab_version="$($CLAB version 2>/dev/null | awk '/version:/{print $2; exit}')" kernel="$(uname -r)" \
    probe_interval_ms="$PROBE_INTERVAL_MS"
  wait_until 120 "OSPF converged (all adjacencies Full, dc ECMP to $SUMMARY)" c_converged ||
    log "WARN: OSPF not converged (baseline checks will show what is missing)"
  wait_until 30 "core1 + core2 have the OSPF route to the servers (track script will pass)" c_both_ospf_routes || true
  start_keepalived core1
  start_keepalived core2
  wait_until 60 "VRRP roles settled (core1 VLAN 10, core2 VLAN 20)" c_roles || log "WARN: VRRP roles not as designed"
  sleep 2
  capture_baseline
  run_scenario
  log "report:"
  local rc=0
  measure report "$EVIDENCE" "$EVIDENCE/report.md" | tee -a "$EVIDENCE/run.log" || rc=$?
  log "report: $EVIDENCE/report.md"
  return "$rc"
}

main "$@"
