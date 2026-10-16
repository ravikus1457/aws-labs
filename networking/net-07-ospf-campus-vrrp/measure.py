#!/usr/bin/env python3
"""Lab N7 evidence: raw probe logs + FRR/iproute2 JSON -> PASS/FAIL checks, measured milliseconds, report.

Standard library only (runs on a bare GitHub runner and on a laptop).

    measure.py baseline <evidence>/baseline           campus design checks on the converged lab -> result.json
    measure.py scenario <evidence>/master_kill        the gateway-master power-loss scenario -> result.json
    measure.py report   <evidence> <out.md>           results.json + markdown; exit 1 on any FAIL or anything missing
    measure.py cond <name> [args] < output            exit 0 when a condition holds (run.sh's wait loops)

Two independent kinds of evidence, both on the runner's single kernel clock:

  * DATA PLANE - tools/probe.py: a UDP packet every 10 ms in each direction between srv and each user host.
    h10 (VLAN 10) is the user whose gateway master dies; h20 (VLAN 20, gateway master core2) is the control.
    "Gap" = send time of the first packet delivered after the last packet lost in the fault window, minus the
    send time of the first packet lost in it.
  * CONTROL PLANE - tools/poll.py on dc (OSPF route to the campus summary + neighbours), on core2 (who holds the
    VLAN 10 gateway address) and, after it is powered back on, on core1 (gateway address + its OSPF route to the
    servers). Each sample is timestamped before and after; a change is bracketed by (previous t0, this t1].
"""

import gzip
import json
import os
import re
import sys

# --------------------------------------------------------------------------------------------
# lab facts (must match configs/ and run.sh)
# --------------------------------------------------------------------------------------------
SUMMARY = "10.10.0.0/16"
SPECIFICS = ("10.10.10.0/24", "10.10.20.0/24")
VIP10, VIP20 = "10.10.10.1", "10.10.20.1"
CORE1_V10, CORE2_V10 = "10.10.10.2", "10.10.10.3"
CORE1_V20, CORE2_V20 = "10.10.20.2", "10.10.20.3"
DC_VIA_CORE1, DC_VIA_CORE2 = "10.0.11.1", "10.0.12.1"
H10, SRV = "10.10.10.10", "10.100.0.10"
RID = {"core1": "10.255.0.1", "core2": "10.255.0.2", "dc": "10.255.0.3"}
OSPF_EXPECTED = {
    "dc": {RID["core1"], RID["core2"]},
    "core1": {RID["core2"], RID["dc"]},
    "core2": {RID["core1"], RID["dc"]},
}
FLOWS = {
    "h10_to_srv": ("h10", "VLAN 10 h10->srv (its gateway master dies)"),
    "srv_to_h10": ("h10", "VLAN 10 srv->h10 (return path)"),
    "h20_to_srv": ("h20", "VLAN 20 h20->srv (control: gateway master survives)"),
    "srv_to_h20": ("h20", "VLAN 20 srv->h20 (control, return path)"),
}
SCENARIOS = ("master_kill",)
WARMUP_S = 0.3
DEFAULT_THRESHOLDS = {
    "h10_gap_max_ms": 2000,  # VRRP master-down = 3 x 200 ms + skew ~ 720 ms; GARP; margin for a busy runner
    "h20_gap_max_ms": 1000,  # control VLAN: only ECMP return flows hashed to the dead core can be hit
    "failback_loss_max_ms": 1000,
    "vrrp_takeover_max_ms": 2000,
    "ospf_reconverge_max_ms": 2000,
    "failback_max_s": 120,
}

# --------------------------------------------------------------------------------------------
# generic helpers
# --------------------------------------------------------------------------------------------


def load_json(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def read_text(path):
    """Plain file, or <path>.gz (run.sh gzips the bulky probe/poll logs)."""
    try:
        if not os.path.exists(path) and os.path.exists(path + ".gz"):
            with gzip.open(path + ".gz", "rt") as fh:
                return fh.read()
        with open(path) as fh:
            return fh.read()
    except OSError:
        return None


def parse_docs(text):
    """Commands run back to back print JSON documents back to back; return them as a list."""
    dec = json.JSONDecoder()
    out, i, n = [], 0, len(text or "")
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        obj, i = dec.raw_decode(text, i)
        out.append(obj)
    return out


def ms(seconds):
    return None if seconds is None else round(seconds * 1000.0, 1)


def check(cid, ok, detail, value=None):
    return {"id": cid, "status": "PASS" if ok else "FAIL", "detail": detail, "value": value}


# --------------------------------------------------------------------------------------------
# readers: FRR JSON + iproute2 JSON (shapes verified against real output from this lab)
# --------------------------------------------------------------------------------------------


def ospf_full(doc):
    """show ip ospf neighbor json -> set of neighbour router-ids whose adjacency is Full."""
    full = set()
    for rid, entries in ((doc or {}).get("neighbors") or {}).items():
        for e in entries if isinstance(entries, list) else [entries]:
            state = str(e.get("converged") or e.get("nbrState") or "")
            if state.startswith("Full"):
                full.add(rid)
    return full


def route_nexthops(route_doc, prefix):
    """show ip route <prefix> json -> frozenset of next-hop IPs installed in the FIB (None = no route)."""
    if not isinstance(route_doc, dict):
        return None
    entries = route_doc.get(prefix)
    if not entries:
        return None
    chosen = [e for e in entries if e.get("selected")] or [e for e in entries if e.get("installed")]
    if not chosen:
        return None
    ips = set()
    for e in chosen:
        nhs = e.get("nexthops") or []
        # A next hop counts only if FRR marks it ACTIVE (and, when FRR reports it, in the FIB). First CI run
        # (38954476813): after core1 was killed, dc still LISTED 10.0.11.1 with "fib":true but no "active",
        # interfaceName "unknown" and internalNextHopActiveNum 1 -- traffic had already moved to core2 only,
        # but counting listed/fib next hops reported ECMP for the whole 35 s window (reconverge = None).
        fib = [nh for nh in nhs if nh.get("active") and nh.get("fib", True)]
        ips.update(nh["ip"] for nh in fib if nh.get("ip"))
    return frozenset(ips)


def ipv4_addrs(ip_j):
    """ip -j -4 addr show dev X -> set of IPv4 addresses on it."""
    out = set()
    for link in ip_j if isinstance(ip_j, list) else []:
        for a in link.get("addr_info") or []:
            if a.get("family", "inet") == "inet" and a.get("local"):
                out.add(a["local"])
    return out


def link_mac(ip_j):
    for link in ip_j if isinstance(ip_j, list) else []:
        if link.get("address"):
            return link["address"].lower()
    return None


def neigh_mac(ip_j):
    for n in ip_j if isinstance(ip_j, list) else []:
        if n.get("lladdr"):
            return n["lladdr"].lower()
    return None


_VIA = re.compile(r"\bvia (\d{1,3}(?:\.\d{1,3}){3})\b")


def ecmp_choices(text):
    """`ip route get ... sport N` lines -> the next hop the kernel picked for each flow."""
    return [m.group(1) for m in (_VIA.search(line) for line in (text or "").splitlines()) if m]


_HOP = re.compile(r"^\s*(\d+)\s+(.*)$")
_IP = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


def parse_traceroute(text):
    hops = []
    for line in (text or "").splitlines():
        m = _HOP.match(line)
        if not m:
            continue
        ip = _IP.search(m.group(2))
        hops.append(ip.group(1) if ip else None)
    return hops


# --------------------------------------------------------------------------------------------
# probe logs (same contract as tools/probe.py)
# --------------------------------------------------------------------------------------------


def load_sent(path):
    rows = []
    for line in (read_text(path) or "").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 3:
            try:
                rows.append((int(parts[0]), float(parts[1]), parts[2]))
            except ValueError:
                pass
    rows.sort()
    return rows


def load_recv(path):
    got = {}
    for line in (read_text(path) or "").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 3:
            try:
                got.setdefault(int(parts[0]), (float(parts[1]), float(parts[2])))
            except ValueError:
                pass
    return got


def _loss_window(sent, delivered, lost_part, until):
    if not lost_part:
        return {"lost": 0, "window_ms": 0.0, "longest_run_ms": 0.0, "recovered": True, "start": None, "end": None}
    first_t = lost_part[0][1]
    last_seq = lost_part[-1][0]
    resume = next((s for s in sent if s[0] > last_seq and s[0] in delivered), None)
    end_t = resume[1] if resume else None
    pos = {s[0]: i for i, s in enumerate(sent)}
    idx = sorted(pos[s[0]] for s in lost_part)
    longest, i = 0.0, 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and idx[j + 1] == idx[j] + 1:
            j += 1
        stop_t = sent[idx[j] + 1][1] if idx[j] + 1 < len(sent) else sent[idx[j]][1]
        longest = max(longest, stop_t - sent[idx[i]][1])
        i = j + 1
    return {
        "lost": len(lost_part),
        "window_ms": ms(end_t - first_t) if end_t is not None else None,
        "longest_run_ms": ms(longest),
        "recovered": end_t is not None and (until is None or end_t < until),
        "start": first_t,
        "end": end_t,
    }


def analyze_flow(sent, recv, t_fault, t_restore, warmup_s=WARMUP_S):
    if not sent:
        return {"error": "sender log is empty"}
    t_first = sent[0][1]
    delivered = {s[0] for s in sent if s[0] in recv}
    counted = [s for s in sent if s[1] >= t_first + warmup_s]
    lost = [s for s in counted if s[0] not in delivered]
    pre = [s for s in lost if s[1] < t_fault]
    fault = [s for s in lost if t_fault <= s[1] < t_restore]
    post = [s for s in lost if s[1] >= t_restore]
    gaps = [b[1] - a[1] for a, b in zip(sent, sent[1:])]
    outage = _loss_window(sent, delivered, fault, t_restore)
    resumed_at = outage["end"] if fault else t_fault
    return {
        "sent": len(sent),
        "received": len(delivered),
        "lost": len(lost),
        "send_errors": sum(1 for s in sent if s[2] != "ok"),
        "sender_max_gap_ms": ms(max(gaps)) if gaps else None,
        "pre_fault_lost": len(pre),
        "pre_fault_sent": sum(1 for s in counted if s[1] < t_fault),
        "outage": outage,
        "delivered_during_fault_after_outage": sum(
            1 for s in sent if s[0] in delivered and resumed_at is not None and resumed_at <= s[1] < t_restore
        ),
        "failback": _loss_window(sent, delivered, post, None),
        "delivered_after_restore": sum(1 for s in sent if s[0] in delivered and s[1] >= t_restore),
    }


# --------------------------------------------------------------------------------------------
# poller samples
# --------------------------------------------------------------------------------------------


def load_poll(path):
    samples = []
    for line in (read_text(path) or "").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if "t0" in row and "out" in row:
            samples.append(row)
    samples.sort(key=lambda r: r["t0"])
    return samples


def sample_series(samples, extract):
    out = []
    for s in samples:
        try:
            value = extract(s["out"])
        except (ValueError, KeyError, TypeError, IndexError):
            continue
        out.append((s["t0"], s["t1"], value))
    return out


def transition(series, t_from, pred, t_until=None, stable=True):
    """First sample started at/after t_from whose value satisfies pred (and, if stable, every later sample
    before t_until does too). Returns ms after t_from (upper bound) + the bracket, or None."""
    window = [s for s in series if s[0] >= t_from and (t_until is None or s[0] < t_until)]
    if not window:
        return None
    start = 0
    if stable:
        start = max((i for i, s in enumerate(window) if not pred(s[2])), default=-1) + 1
    for i in range(start, len(window)):
        if pred(window[i][2]):
            prev_t0 = window[i - 1][0] if i > 0 else t_from
            return {"ms": ms(window[i][1] - t_from), "bracket_ms": [ms(prev_t0 - t_from), ms(window[i][1] - t_from)],
                    "at": window[i][1]}
    return None


def dc_extract(outs):
    nh, full = None, set()
    for d in parse_docs(outs[0]):
        if isinstance(d, dict) and SUMMARY in d:
            nh = route_nexthops(d, SUMMARY)
        elif isinstance(d, dict) and "neighbors" in d:
            full = ospf_full(d)
    return {"nh": nh, "full": full}


def vip10_extract(outs):
    """core poller: first output = ip -j -4 addr show dev eth1.10; optional second = OSPF route to the servers."""
    docs = parse_docs(outs[0])
    has_vip = VIP10 in ipv4_addrs(docs[0] if docs else [])
    route = ("via" in outs[1]) if len(outs) > 1 else None
    return {"vip": has_vip, "route": route}


# --------------------------------------------------------------------------------------------
# baseline
# --------------------------------------------------------------------------------------------


def evaluate_baseline(d):
    j = lambda name: load_json(os.path.join(d, name))  # noqa: E731
    checks = []
    for n, want in OSPF_EXPECTED.items():
        full = ospf_full(j(f"{n}_ospf_nbr.json"))
        checks.append(check(f"baseline.ospf_full.{n}", want <= full,
                            f"{n}: OSPF adjacencies Full with {sorted(full)} (want {sorted(want)})", sorted(full)))

    routes = j("dc_routes.json") or {}
    ospf_specifics = [p for p in SPECIFICS if any(e.get("protocol") == "ospf" for e in routes.get(p) or [])]
    has_summary = any(e.get("protocol") == "ospf" for e in routes.get(SUMMARY) or [])
    checks.append(check("baseline.area1_summarised", has_summary and not ospf_specifics,
                        f"dc sees area 1 as {SUMMARY}: {'yes' if has_summary else 'NO'}; leaked /24 specifics: "
                        f"{ospf_specifics or 'none'}", {"summary": has_summary, "specifics": ospf_specifics}))

    nh = route_nexthops(j("dc_route_summary.json"), SUMMARY)
    want = frozenset({DC_VIA_CORE1, DC_VIA_CORE2})
    checks.append(check("baseline.ecmp_fib", nh == want,
                        f"dc FIB next hops for {SUMMARY}: {sorted(nh) if nh else 'none'} (want both cores {sorted(want)})",
                        sorted(nh) if nh else None))
    choices = ecmp_choices(read_text(os.path.join(d, "dc_ecmp_flows.txt")))
    used = {c: choices.count(c) for c in sorted(set(choices))}
    checks.append(check("baseline.ecmp_dataplane", len(choices) >= 8 and set(choices) == set(want),
                        f"kernel next hop per flow over {len(choices)} flows (L4 hash): {used} (want both cores used)", used))

    a = {f"{n}_{v}": ipv4_addrs(j(f"{n}_addr{v}.json")) for n in ("core1", "core2") for v in (10, 20)}
    roles_ok = VIP10 in a["core1_10"] and VIP10 not in a["core2_10"] and VIP20 in a["core2_20"] and VIP20 not in a["core1_20"]
    checks.append(check("baseline.vrrp_roles", roles_ok,
                        f"gateway VIPs: {VIP10} on core1={VIP10 in a['core1_10']} core2={VIP10 in a['core2_10']}; "
                        f"{VIP20} on core1={VIP20 in a['core1_20']} core2={VIP20 in a['core2_20']} "
                        "(want VLAN 10 on core1 only, VLAN 20 on core2 only)"))

    for host, link, vip in (("h10", "core1_link10.json", VIP10), ("h20", "core2_link20.json", VIP20)):
        mac, want_mac = neigh_mac(j(f"{host}_neigh.json")), link_mac(j(link))
        checks.append(check(f"baseline.gateway_arp.{host}", mac is not None and mac == want_mac,
                            f"{host} resolves gateway {vip} to {mac} (the master's interface is {want_mac})"))

    for name, want_first, label in (("trace_h10_srv.txt", CORE1_V10, "h10->srv"), ("trace_h20_srv.txt", CORE2_V20, "h20->srv")):
        hops = parse_traceroute(read_text(os.path.join(d, name)))
        checks.append(check(f"baseline.first_hop.{label}", bool(hops) and hops[0] == want_first and hops[-1] == SRV,
                            f"traceroute {label}: first hop {hops[0] if hops else None} (want {want_first}), "
                            f"reaches {hops[-1] if hops else None} (hops {hops})", hops))
    hops = parse_traceroute(read_text(os.path.join(d, "trace_srv_h10.txt")))
    checks.append(check("baseline.reach.srv->h10", bool(hops) and hops[-1] == H10,
                        f"traceroute srv->h10 reaches {hops[-1] if hops else None} via {hops}", hops))
    return {"kind": "baseline", "checks": checks}


# --------------------------------------------------------------------------------------------
# the scenario
# --------------------------------------------------------------------------------------------


def evaluate_scenario(d):
    meta = load_json(os.path.join(d, "meta.json"), {}) or {}
    name = meta.get("scenario") or os.path.basename(os.path.normpath(d))
    thr = dict(DEFAULT_THRESHOLDS)
    thr.update(meta.get("thresholds") or {})
    t_fault, t_restore = meta.get("t_fault"), meta.get("t_restore")
    t_keep = meta.get("t_keepalived")
    checks, summary = [], {"scenario": name}
    if t_fault is None or t_restore is None or t_keep is None or name not in SCENARIOS:
        checks.append(check(f"{name}.meta", False, f"meta.json incomplete or unknown scenario: {meta}"))
        return {"kind": "scenario", "scenario": name, "checks": checks, "summary": summary}

    # preconditions + the fault really happened
    c1 = ipv4_addrs(load_json(os.path.join(d, "pre", "core1_addr10.json")))
    c2 = ipv4_addrs(load_json(os.path.join(d, "pre", "core2_addr10.json")))
    checks.append(check(f"{name}.pre.core1_is_master", VIP10 in c1 and VIP10 not in c2,
                        f"before the fault {VIP10} is on core1={VIP10 in c1}, core2={VIP10 in c2} (want core1 only)"))
    nh = route_nexthops(load_json(os.path.join(d, "pre", "dc_route_summary.json")), SUMMARY)
    checks.append(check(f"{name}.pre.ecmp", nh == frozenset({DC_VIA_CORE1, DC_VIA_CORE2}),
                        f"before the fault dc reaches {SUMMARY} via {sorted(nh) if nh else 'nothing'} (want both cores)"))
    dead = [meta.get("core1_running_after_kill"), meta.get("core1_running_before_restore")]
    checks.append(check(f"{name}.fault_effective", all(str(x).lower() == "false" for x in dead),
                        f"core1 running right after the kill / just before the repair: {dead} (want false/false: "
                        "nothing restarted it behind the lab's back)"))

    # data plane, per flow
    for flow, (host, label) in FLOWS.items():
        r = analyze_flow(load_sent(os.path.join(d, "probes", f"{flow}.sent")),
                         load_recv(os.path.join(d, "probes", f"{flow}.recv")), t_fault, t_restore)
        summary[flow] = r
        if "error" in r:
            checks.append(check(f"{name}.{flow}.probe", False, f"{label}: {r['error']}"))
            continue
        checks.append(check(f"{name}.{flow}.baseline_clean", r["pre_fault_lost"] == 0,
                            f"{label}: {r['pre_fault_lost']}/{r['pre_fault_sent']} packets lost BEFORE the fault"))
        o = r["outage"]
        ok = o["recovered"] and r["delivered_during_fault_after_outage"] > 0
        checks.append(check(f"{name}.{flow}.failover", ok,
                            f"{label}: traffic {'resumed' if ok else 'did NOT resume'} while core1 was still dead "
                            f"({r['delivered_during_fault_after_outage']} packets delivered)"))
        lim = thr[f"{host}_gap_max_ms"]
        ok = o["window_ms"] is not None and o["recovered"] and o["window_ms"] <= lim
        checks.append(check(f"{name}.{flow}.gap_ms", ok,
                            f"{label}: gap {o['window_ms']} ms ({o['lost']} packets lost; limit {lim} ms)", o["window_ms"]))
        fb = r["failback"]
        flim = thr["failback_loss_max_ms"]
        ok = fb["recovered"] and r["delivered_after_restore"] > 0 and fb["window_ms"] is not None and fb["window_ms"] <= flim
        checks.append(check(f"{name}.{flow}.failback_loss_ms", ok,
                            f"{label}: loss while core1 came back and took over again: {fb['window_ms']} ms "
                            f"({fb['lost']} packets; limit {flim} ms)", fb["window_ms"]))

    # control plane
    dc = sample_series(load_poll(os.path.join(d, "poll", "dc.jsonl")), dc_extract)
    c2s = sample_series(load_poll(os.path.join(d, "poll", "core2.jsonl")), vip10_extract)
    c1s = sample_series(load_poll(os.path.join(d, "poll", "core1.jsonl")), vip10_extract)
    both = frozenset({DC_VIA_CORE1, DC_VIA_CORE2})

    tk = transition(c2s, t_fault, lambda v: v["vip"], t_restore)
    ok = tk is not None and tk["ms"] <= thr["vrrp_takeover_max_ms"]
    checks.append(check(f"{name}.vrrp_takeover_ms", ok,
                        f"core2 took over gateway {VIP10} {tk and tk['ms']} ms after the kill "
                        f"(bracket {tk and tk['bracket_ms']}; limit {thr['vrrp_takeover_max_ms']} ms)", tk and tk["ms"]))
    nbr = transition(dc, t_fault, lambda v: RID["core1"] not in v["full"], t_restore, stable=False)
    checks.append(check(f"{name}.ospf_neighbor_down_ms", nbr is not None,
                        f"dc dropped its OSPF adjacency to core1 {nbr and nbr['ms']} ms after the kill", nbr and nbr["ms"]))
    rc = transition(dc, t_fault, lambda v: v["nh"] == frozenset({DC_VIA_CORE2}), t_restore)
    ok = rc is not None and rc["ms"] <= thr["ospf_reconverge_max_ms"]
    checks.append(check(f"{name}.ospf_reconverge_ms", ok,
                        f"dc's route to {SUMMARY} collapsed from ECMP to core2 only {rc and rc['ms']} ms after the kill "
                        f"(limit {thr['ospf_reconverge_max_ms']} ms)", rc and rc["ms"]))
    fb = transition(dc, t_restore, lambda v: v["nh"] == both)
    ok = fb is not None and fb["ms"] <= thr["failback_max_s"] * 1000
    checks.append(check(f"{name}.ecmp_restored_ms", ok,
                        f"dc back to ECMP over both cores {fb and fb['ms']} ms after core1 was powered on", fb and fb["ms"]))
    t_wired = meta.get("t_wired")
    rel = transition(c2s, t_restore, lambda v: not v["vip"])
    pre = transition(c1s, t_wired, lambda v: v["vip"]) if t_wired else None
    ok = rel is not None and pre is not None
    checks.append(check(f"{name}.preemption", ok,
                        f"core1 (priority 200) took {VIP10} back {pre and pre['ms']} ms after its backbone links were "
                        f"re-cabled; core2 released it {rel and rel['ms']} ms after core1 was powered on", pre and pre["ms"]))
    gate = [s for s in c1s if t_wired and t_keep <= s[0] < t_wired]
    held = [s for s in gate if not s[2]["vip"]]
    early = [round(s[0] - t_keep, 3) for s in c1s if s[2]["vip"] and not s[2]["route"]]
    ok = len(gate) >= 3 and len(held) == len(gate) and not any(s[2]["route"] for s in gate) and not early
    checks.append(check(f"{name}.preempt_waits_for_routing", ok,
                        f"while core1 ran keepalived with no route to the servers ({len(gate)} samples) it held the gateway "
                        f"in {len(gate) - len(held)}; samples with gateway but no route: {len(early)} "
                        "(want a tested gate: >= 3 samples, 0 and 0)", early[:5]))
    summary.update({"vrrp_takeover": tk, "ospf_neighbor_down": nbr, "ospf_reconverge": rc, "ecmp_restored": fb,
                    "preempt_after_wired": pre, "core2_release": rel})

    # paths and the hosts' ARP view
    for when, flow, want_ok, desc in (
        ("during", "h10_srv", lambda h: bool(h) and h[0] == CORE2_V10 and h[-1] == SRV, f"first hop {CORE2_V10} (core2)"),
        ("during", "srv_h10", lambda h: bool(h) and DC_VIA_CORE2 in h and DC_VIA_CORE1 not in h and h[-1] == H10, "via core2 only"),
        ("after", "h10_srv", lambda h: bool(h) and h[0] == CORE1_V10 and h[-1] == SRV, f"first hop {CORE1_V10} (core1 again)"),
    ):
        hops = parse_traceroute(read_text(os.path.join(d, "trace", f"{when}_{flow}.txt")))
        checks.append(check(f"{name}.path_{when}.{flow}", want_ok(hops),
                            f"traceroute {flow.replace('_', '->')} {when} the fault: {hops} (want {desc})", hops))
    for when, link in (("during", "core2_link10.json"), ("after", "core1_link10.json")):
        mac = neigh_mac(load_json(os.path.join(d, when, "h10_neigh.json")))
        want_mac = link_mac(load_json(os.path.join(d, when, link)))
        checks.append(check(f"{name}.gateway_arp_{when}", mac is not None and mac == want_mac,
                            f"{when} the fault h10 resolves {VIP10} to {mac}; the {link.split('_')[0]} gateway interface "
                            f"is {want_mac} (gratuitous ARP moved the host)"))
    return {"kind": "scenario", "scenario": name, "checks": checks, "summary": summary, "meta": meta}


# --------------------------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------------------------


def _fmt(v):
    return "n/a" if v is None else f"{v:,.1f}" if isinstance(v, float) else str(v)


def build_report(evidence):
    results, checks = {}, []
    base = load_json(os.path.join(evidence, "baseline", "result.json"))
    if base is None:
        checks.append(check("baseline.result", False, "baseline/result.json missing - the baseline never ran"))
    else:
        results["baseline"] = base
        checks.extend(base["checks"])
    for s in SCENARIOS:
        r = load_json(os.path.join(evidence, s, "result.json"))
        if r is None:
            checks.append(check(f"{s}.result", False, f"{s}/result.json missing - scenario did not run to completion"))
            continue
        results[s] = r
        checks.extend(r["checks"])
    return results, checks, sum(1 for c in checks if c["status"] == "FAIL")


def render_markdown(evidence, results, checks, n_fail):
    meta = load_json(os.path.join(evidence, "meta.json"), {}) or {}
    lines = ["# Lab N7 - OSPF campus + VRRP gateway failover: measured results", ""]
    lines.append(
        f"Run: {meta.get('run_id', 'local')} - started {meta.get('started', '?')} - FRR {meta.get('frr_image', '?')} - "
        f"{meta.get('keepalived', 'keepalived ?')} - containerlab {meta.get('clab_version', '?')} - kernel "
        f"{meta.get('kernel', '?')} - probe every {meta.get('probe_interval_ms', '?')} ms per direction."
    )
    lines += ["", f"**Verdict: {'PASS' if n_fail == 0 else 'FAIL'}** ({len(checks) - n_fail}/{len(checks)} checks passed)", ""]
    r = results.get("master_kill")
    lines += ["## Failover: core1 (VLAN 10 gateway master) loses power under traffic", ""]
    if not r:
        lines.append("**MISSING** - the scenario did not produce a result.")
    else:
        sm = r["summary"]

        def gap(flow, key="outage"):
            return _fmt((sm.get(flow) or {}).get(key, {}).get("window_ms"))

        def t(key):
            return _fmt((sm.get(key) or {}).get("ms"))

        lines += [
            "| Measurement | Value (ms) |",
            "|---|---|",
            f"| User traffic gap, VLAN 10 h10 -> srv (gateway moved to core2) | {gap('h10_to_srv')} |",
            f"| User traffic gap, VLAN 10 srv -> h10 (return path) | {gap('srv_to_h10')} |",
            f"| Control VLAN 20 h20 -> srv / srv -> h20 | {gap('h20_to_srv')} / {gap('srv_to_h20')} |",
            f"| VRRP takeover (core2 holds 10.10.10.1) | {t('vrrp_takeover')} |",
            f"| OSPF: dc drops the core1 adjacency | {t('ospf_neighbor_down')} |",
            f"| OSPF: dc route to 10.10.0.0/16 reconverged onto core2 | {t('ospf_reconverge')} |",
            f"| Failback: dc ECMP restored after core1 power-on | {t('ecmp_restored')} |",
            f"| Preemption: core1 master again after its backbone links return | {t('preempt_after_wired')} |",
            f"| Loss during failback, h10 -> srv / srv -> h10 | {gap('h10_to_srv', 'failback')} / {gap('srv_to_h10', 'failback')} |",
            "",
            "Control-plane times are upper bounds of the poll bracket (see result.json for both ends).",
        ]
    lines += ["", "## Every check", "", "| Status | Check | Detail |", "|---|---|---|"]
    for c in checks:
        lines.append(f"| {'PASS' if c['status'] == 'PASS' else '**' + c['status'] + '**'} | `{c['id']}` | "
                     f"{str(c['detail']).replace('|', '/')} |")
    lines += ["", "Raw evidence (probe logs, poller samples, vtysh/iproute2 JSON, traceroutes, FRR and keepalived logs) is "
              "next to this file; `python3 measure.py report <dir> out.md` recomputes every number."]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------------
# run.sh wait-loop conditions
# --------------------------------------------------------------------------------------------


def cond(args, text):
    name = args[0] if args else ""
    docs = parse_docs(text)
    if name == "ospf-full":
        full = set()
        for d in docs:
            full |= ospf_full(d)
        return set(args[1:]) <= full
    if name == "nexthop":
        prefix, want = args[1], frozenset(args[2:])
        return any(route_nexthops(d, prefix) == want for d in docs)
    if name == "vip":
        ip, want = args[1], args[2]
        present = any(ip in ipv4_addrs(d) for d in docs)
        return present if want == "present" else not present
    raise SystemExit(f"unknown condition {name!r}")


def _print_checks(checks):
    for c in checks:
        print(f"{c['status']:4} {c['id']}: {c['detail']}")


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd in ("baseline", "scenario"):
        r = (evaluate_baseline if cmd == "baseline" else evaluate_scenario)(argv[2])
        with open(os.path.join(argv[2], "result.json"), "w") as fh:
            json.dump(r, fh, indent=2, default=sorted)
        _print_checks(r["checks"])
        return 0
    if cmd == "report":
        results, checks, n_fail = build_report(argv[2])
        with open(os.path.join(argv[2], "results.json"), "w") as fh:
            json.dump({"verdict": "PASS" if n_fail == 0 else "FAIL", "failed": n_fail, "checks": checks,
                       "results": results}, fh, indent=2, default=sorted)
        with open(argv[3], "w") as fh:
            fh.write(render_markdown(argv[2], results, checks, n_fail))
        _print_checks(checks)
        print(f"verdict: {'PASS' if n_fail == 0 else 'FAIL'} ({n_fail} failed)")
        return 0 if n_fail == 0 else 1
    if cmd == "cond":
        try:
            return 0 if cond(argv[2:], sys.stdin.read()) else 1
        except ValueError:
            return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
