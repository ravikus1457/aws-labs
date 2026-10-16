#!/usr/bin/env python3
"""Lab N6 evidence: raw probe logs + FRR JSON -> PASS/FAIL checks, measured milliseconds, report.

Standard library only (runs on a bare GitHub runner and on a laptop).

    measure.py baseline <evidence>/baseline           policy checks on the converged lab -> result.json
    measure.py scenario <evidence>/<scenario>         one fault scenario -> result.json
    measure.py report   <evidence> <out.md>           every result -> results.json + markdown; exit 1 on any FAIL
    measure.py cond <name> [args] < vtysh-output      exit 0 when a condition holds (run.sh's wait loops)

Nothing here is estimated. Two independent clocks of evidence, both on the runner's one kernel clock:

  * DATA PLANE  - tools/probe.py sends a UDP packet every 10 ms in each direction (h1 -> srv, srv -> h1)
                  and the receiver logs what arrived. "Outage" = send time of the first packet delivered
                  after the last packet lost in the fault window, minus the send time of the first packet
                  lost in it. It is what a user's traffic experienced, per direction.
  * CONTROL PLANE - tools/poll.py samples vtysh JSON on ent and inet as fast as vtysh answers (~20-50 ms)
                  with a timestamp before and after every sample. A state change is bracketed by
                  (previous sample's start, this sample's end]; the reported number is the upper bound.

t_fault / t_restore are taken by run.sh immediately before it injects / removes the fault.
"""

import gzip
import json
import os
import re
import sys

# --------------------------------------------------------------------------------------------
# lab facts (must match configs/ and run.sh)
# --------------------------------------------------------------------------------------------
ISPA = "10.0.1.1"  # ISP-A's address on the ent link = ent's next hop via ISP-A
ISPB = "10.0.2.1"
ENT_ON_ISPA = "10.0.1.2"
INET_VIA_ISPA = "10.1.1.1"  # ISP-A's address on the inet link = inet's next hop via ISP-A
INET_VIA_ISPB = "10.1.2.1"
CORE_PEER = "10.255.0.2"
ENT_LOOPBACK = "10.255.0.1"
OWN = "203.0.113.0/24"
SRV_NET = "198.51.100.0/24"
REMOTE2 = "192.0.2.0/24"
DEFAULT = "0.0.0.0/0"
OUR_AS = 65010
INJECTED = {  # junk each ISP deliberately sends; ent must receive it and drop it
    ISPA: {"10.66.0.0/16", "100.64.0.0/10", "198.51.100.128/25"},
    ISPB: {"192.168.50.0/24", "203.0.113.128/25"},
}
MUST_ACCEPT = {DEFAULT, SRV_NET, REMOTE2}  # ...while the legitimate routes still get through
ISPA_HOPS = {ISPA, INET_VIA_ISPA}
ISPB_HOPS = {ISPB, INET_VIA_ISPB}
FLOWS = {"h1_to_srv": "outbound h1->srv", "srv_to_h1": "inbound srv->h1"}
SCENARIOS = ("bfd", "holdtimer", "linkdown")
SCENARIO_TEXT = {
    "bfd": "ISP-A circuit black-holes (ports stay up), BFD on",
    "holdtimer": "ISP-A circuit black-holes (ports stay up), BFD off - BGP hold timer only",
    "linkdown": "ent's ISP-A port goes down (carrier loss), BFD on",
}
WARMUP_S = 0.3  # loss in the first 300 ms of a stream (ARP, socket setup) is not counted as baseline loss

DEFAULT_THRESHOLDS = {
    "bfd": {"outage_max_ms": 1500, "detect_min_ms": 0, "detect_max_ms": 1000, "reroute_max_ms": 1500},
    "holdtimer": {"outage_max_ms": 35000, "detect_min_ms": 15000, "detect_max_ms": 32000, "reroute_max_ms": 35000},
    "linkdown": {"outage_max_ms": 1500, "detect_min_ms": 0, "detect_max_ms": 1000, "reroute_max_ms": 1500},
    "failback_loss_max_ms": 1000,
    "failback_max_s": 120,
    "bfd_speedup_min": 5.0,
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
    """vtysh -c A -c B prints the JSON documents back to back; return them as a list."""
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


def check(cid, ok, detail, value=None, status=None):
    return {"id": cid, "status": status or ("PASS" if ok else "FAIL"), "detail": detail, "value": value}


# --------------------------------------------------------------------------------------------
# FRR JSON readers (shapes verified against FRR 10.x source; tolerant of older key spellings)
# --------------------------------------------------------------------------------------------


def bgp_peer_states(summary):
    """show bgp ipv4 unicast summary json -> {peer_ip: state}."""
    if not isinstance(summary, dict):
        return {}
    if "peers" not in summary and isinstance(summary.get("ipv4Unicast"), dict):
        summary = summary["ipv4Unicast"]
    return {ip: p.get("state") for ip, p in (summary.get("peers") or {}).items() if isinstance(p, dict)}


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
        fib = [nh for nh in nhs if nh.get("fib")]
        if not fib and e.get("installed"):
            fib = [nh for nh in nhs if nh.get("active")]
        ips.update(nh["ip"] for nh in fib if nh.get("ip"))
    return frozenset(ips)


def _aspath_list(p):
    a = p.get("aspath")
    s = a.get("string", "") if isinstance(a, dict) else (a if isinstance(a, str) else p.get("path", ""))
    return [int(x) for x in re.findall(r"\d+", s or "")]


def _path_record(p):
    nhs = p.get("nexthops") or []
    best = p.get("bestpath")
    best = bool(best.get("overall")) if isinstance(best, dict) else bool(best)
    peer = p.get("peer")
    peer_id = peer.get("peerId") if isinstance(peer, dict) else p.get("peerId")
    return {
        "nexthop": nhs[0].get("ip") if nhs else None,
        "peer": peer_id,
        "locprf": p.get("locPrf", p.get("localpref", p.get("localPref"))),
        "aspath": _aspath_list(p),
        "best": best,
        "valid": bool(p.get("valid", True)),
    }


def bgp_prefix_paths(doc):
    """show bgp ipv4 unicast <prefix> json -> [path records]."""
    if not isinstance(doc, dict):
        return []
    paths = doc.get("paths")
    if paths is None:  # some versions nest under the prefix key
        for v in doc.values():
            if isinstance(v, dict) and "paths" in v:
                paths = v["paths"]
                break
    return [_path_record(p) for p in (paths or []) if isinstance(p, dict)]


def bgp_table(doc):
    """show bgp ipv4 unicast json -> {prefix: [path records]}."""
    routes = (doc or {}).get("routes") or {}
    return {pfx: [_path_record(p) for p in paths if isinstance(p, dict)] for pfx, paths in routes.items()}


def adj_prefixes(doc, key):
    """neighbors X advertised-routes / received-routes json -> set of prefixes."""
    return set(((doc or {}).get(key) or {}).keys())


def bfd_status(peers, peer_ip):
    """show bfd peers json -> status of the session to peer_ip ('up', 'down', ...) or None if absent."""
    for p in peers if isinstance(peers, list) else []:
        if p.get("peer") == peer_ip:
            return p.get("status")
    return None


def best_path(paths):
    return next((p for p in paths if p["best"]), None)


_HOP = re.compile(r"^\s*(\d+)\s+(.*)$")
_IP = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


def parse_traceroute(text):
    """busybox / classic `traceroute -n` output -> [hop ip or None]; header line ignored."""
    hops = []
    for line in (text or "").splitlines():
        m = _HOP.match(line)
        if not m:
            continue
        ip = _IP.search(m.group(2))
        hops.append(ip.group(1) if ip else None)
    return hops


def classify_path(hops):
    a = any(h in ISPA_HOPS for h in hops)
    b = any(h in ISPB_HOPS for h in hops)
    if a and not b:
        return "isp-a"
    if b and not a:
        return "isp-b"
    return "both" if a and b else "unknown"


# --------------------------------------------------------------------------------------------
# probe logs
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
    """Span of a group of lost packets: first lost send -> first delivered send after the last lost one."""
    if not lost_part:
        return {"lost": 0, "window_ms": 0.0, "longest_run_ms": 0.0, "recovered": True, "start": None, "end": None}
    first_t = lost_part[0][1]
    last_seq = lost_part[-1][0]
    resume = next((s for s in sent if s[0] > last_seq and s[0] in delivered), None)
    end_t = resume[1] if resume else None
    # longest contiguous run of lost sends (by position in the sender log)
    pos = {s[0]: i for i, s in enumerate(sent)}
    lost_seqs = {s[0] for s in lost_part}
    longest, i = 0.0, 0
    idx = sorted(pos[s] for s in lost_seqs)
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and idx[j + 1] == idx[j] + 1:
            j += 1
        start_t = sent[idx[i]][1]
        stop_t = sent[idx[j] + 1][1] if idx[j] + 1 < len(sent) else sent[idx[j]][1]
        longest = max(longest, stop_t - start_t)
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
    """Per-direction data-plane result for one scenario."""
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
        "first_send": t_first,
        "last_send": sent[-1][1],
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
    """[(t0, t1, value)] where value = extract(list of parsed docs); unparseable samples are skipped."""
    out = []
    for s in samples:
        try:
            docs = []
            for text in s["out"]:
                docs.extend(parse_docs(text))
            value = extract(docs)
        except (ValueError, KeyError, TypeError, IndexError):
            continue
        out.append((s["t0"], s["t1"], value))
    return out


def transition(series, t_from, pred, t_until=None, stable=True):
    """First sample started at/after t_from whose value satisfies pred (and, if stable, every later
    sample before t_until does too). Returns ms after t_from (upper bound) + the bracket, or None."""
    window = [s for s in series if s[0] >= t_from and (t_until is None or s[0] < t_until)]
    if not window:
        return None
    start = 0
    if stable:
        last_bad = max((i for i, s in enumerate(window) if not pred(s[2])), default=-1)
        start = last_bad + 1
    for i in range(start, len(window)):
        if pred(window[i][2]):
            prev_t0 = window[i - 1][0] if i > 0 else t_from
            return {
                "ms": ms(window[i][1] - t_from),
                "bracket_ms": [ms(prev_t0 - t_from), ms(window[i][1] - t_from)],
                "at": window[i][1],
            }
    return None


def series_stats(series, t_from, t_until):
    pts = [s for s in series if t_from <= s[0] <= t_until]
    if len(pts) < 2:
        return {"samples": len(pts), "max_gap_ms": None, "median_period_ms": None}
    gaps = sorted(b[0] - a[0] for a, b in zip(pts, pts[1:]))
    return {"samples": len(pts), "max_gap_ms": ms(gaps[-1]), "median_period_ms": ms(gaps[len(gaps) // 2])}


def ent_extract(docs):
    """ent poller: [bgp summary json, show ip route SRV_NET json]."""
    peers = {}
    nh = None
    for d in docs:
        if isinstance(d, dict) and ("peers" in d or "ipv4Unicast" in d):
            peers = bgp_peer_states(d)
        elif isinstance(d, dict) and SRV_NET in d:
            nh = route_nexthops(d, SRV_NET)
    return {"ispa_state": peers.get(ISPA), "nh": nh}


def inet_extract(docs):
    nh = None
    for d in docs:
        if isinstance(d, dict) and OWN in d:
            nh = route_nexthops(d, OWN)
    return {"nh": nh}


# --------------------------------------------------------------------------------------------
# baseline
# --------------------------------------------------------------------------------------------


def evaluate_baseline(d):
    j = lambda name: load_json(os.path.join(d, name))  # noqa: E731
    checks = []

    def sessions(node, fname, want):
        states = bgp_peer_states(j(fname))
        bad = {p: states.get(p) for p in want if states.get(p) != "Established"}
        checks.append(check(f"baseline.sessions.{node}", not bad and bool(states),
                            f"{node}: {len(want) - len(bad)}/{len(want)} BGP sessions Established"
                            + (f"; not up: {bad}" if bad else ""), {p: states.get(p) for p in want}))

    sessions("ent", "ent_summary.json", [ISPA, ISPB, CORE_PEER])
    sessions("inet", "inet_summary.json", [INET_VIA_ISPA, INET_VIA_ISPB])
    sessions("core", "core_summary.json", [ENT_LOOPBACK])

    for isp, fname in ((ISPA, "ent_adv_ispa.json"), (ISPB, "ent_adv_ispb.json")):
        adv = adj_prefixes(j(fname), "advertisedRoutes")
        checks.append(check(f"baseline.outbound_filter.{isp}", adv == {OWN},
                            f"ent advertises exactly {{{OWN}}} to {isp} (got {sorted(adv)}) - no transit, no infra leak",
                            sorted(adv)))

    table = bgp_table(j("ent_rib.json"))
    for isp, fname in ((ISPA, "ent_rcv_ispa.json"), (ISPB, "ent_rcv_ispb.json")):
        received = adj_prefixes(j(fname), "receivedRoutes")
        accepted = {pfx for pfx, paths in table.items() if any(p["peer"] == isp or p["nexthop"] == isp for p in paths)}
        junk = INJECTED[isp]
        sent_junk = junk & received
        leaked = junk & accepted
        missing = MUST_ACCEPT - accepted
        ok = sent_junk == junk and not leaked and not missing
        checks.append(check(f"baseline.inbound_filter.{isp}", ok,
                            f"{isp} sent {len(sent_junk)}/{len(junk)} junk prefixes, ent accepted {len(leaked)} of them"
                            f" {sorted(leaked) if leaked else ''}; legit routes missing: {sorted(missing) or 'none'}",
                            {"received": sorted(received), "accepted": sorted(accepted)}))

    paths = bgp_prefix_paths(j("ent_bgp_srv.json"))
    best = best_path(paths)
    backup = next((p for p in paths if p["nexthop"] == ISPB), None)
    ok = bool(best and best["nexthop"] == ISPA and best["locprf"] == 200 and backup and backup["locprf"] == 100)
    checks.append(check("baseline.local_preference", ok,
                        f"ent best path to {SRV_NET}: via {best and best['nexthop']} LP {best and best['locprf']}; "
                        f"backup via ISP-B LP {backup and backup['locprf']} (want ISP-A/200 best, ISP-B/100 standby)",
                        {"best": best, "backup": backup}))

    dbest = best_path(bgp_prefix_paths(j("ent_bgp_default.json")))
    checks.append(check("baseline.default_route", bool(dbest and dbest["nexthop"] == ISPA),
                        f"ent best default route via {dbest and dbest['nexthop']} (want {ISPA})", dbest))

    ipaths = bgp_prefix_paths(j("inet_bgp_own.json"))
    ibest = best_path(ipaths)
    via_b = next((p for p in ipaths if p["nexthop"] == INET_VIA_ISPB), None)
    ok = bool(ibest and ibest["nexthop"] == INET_VIA_ISPA and via_b and via_b["aspath"].count(OUR_AS) == 3)
    checks.append(check("baseline.as_path_prepend", ok,
                        f"inet best path to {OWN}: via {ibest and ibest['nexthop']} AS-path {ibest and ibest['aspath']}; "
                        f"via ISP-B AS-path {via_b and via_b['aspath']} (want ISP-A best, {OUR_AS} x3 via ISP-B)",
                        {"best": ibest, "via_ispb": via_b}))

    ent_bfd = j("ent_bfd.json")
    ispa_bfd = j("ispa_bfd.json")
    st = {ISPA: bfd_status(ent_bfd, ISPA), ISPB: bfd_status(ent_bfd, ISPB), "ispa->ent": bfd_status(ispa_bfd, ENT_ON_ISPA)}
    checks.append(check("baseline.bfd", all(v == "up" for v in st.values()),
                        f"BFD sessions: {st} (want all up)", st))

    fib = route_nexthops(j("ent_route_srv.json"), SRV_NET)
    checks.append(check("baseline.fib", fib == frozenset({ISPA}),
                        f"ent FIB next hop for {SRV_NET}: {sorted(fib) if fib is not None else 'no route'} (want {ISPA})",
                        sorted(fib) if fib is not None else None))
    cfib = route_nexthops(j("core_route_srv.json"), SRV_NET)
    checks.append(check("baseline.ibgp_inside", bool(cfib),
                        f"core has {SRV_NET} installed via iBGP: next hops {sorted(cfib) if cfib else 'none'}",
                        sorted(cfib) if cfib else None))

    for name, label in (("trace_h1_srv.txt", "h1->srv"), ("trace_srv_h1.txt", "srv->h1")):
        hops = parse_traceroute(read_text(os.path.join(d, name)))
        path = classify_path(hops)
        checks.append(check(f"baseline.path.{label}", path == "isp-a",
                            f"traceroute {label} goes via {path} (hops {hops}; want isp-a)", hops))
    return {"kind": "baseline", "checks": checks}


# --------------------------------------------------------------------------------------------
# one scenario
# --------------------------------------------------------------------------------------------


def evaluate_scenario(d):
    meta = load_json(os.path.join(d, "meta.json"), {}) or {}
    name = meta.get("scenario") or os.path.basename(os.path.normpath(d))
    thr_all = dict(DEFAULT_THRESHOLDS)
    thr_all.update(meta.get("thresholds") or {})
    thr = thr_all.get(name) or {}
    t_fault, t_restore = meta.get("t_fault"), meta.get("t_restore")
    checks, summary = [], {"scenario": name, "description": SCENARIO_TEXT.get(name, name)}
    if t_fault is None or t_restore is None or name not in SCENARIOS:
        checks.append(check(f"{name}.meta", False, f"meta.json incomplete or unknown scenario: {meta}"))
        return {"kind": "scenario", "scenario": name, "checks": checks, "summary": summary}

    # preconditions: the scenario really is the one it claims to be
    pre_peers = bgp_peer_states(load_json(os.path.join(d, "pre", "ent_summary.json")))
    pre_bfd = bfd_status(load_json(os.path.join(d, "pre", "ent_bfd.json")), ISPA)
    up = pre_peers.get(ISPA) == "Established" and pre_peers.get(ISPB) == "Established"
    checks.append(check(f"{name}.pre.sessions", up, f"before the fault: ISP-A {pre_peers.get(ISPA)}, ISP-B {pre_peers.get(ISPB)}"))
    want_bfd = name != "holdtimer"
    bfd_ok = (pre_bfd == "up") if want_bfd else (pre_bfd != "up")
    checks.append(check(f"{name}.pre.bfd", bfd_ok,
                        f"before the fault: BFD to ISP-A is {pre_bfd or 'not configured'} "
                        f"(want {'up' if want_bfd else 'not running'})", pre_bfd))
    summary["bfd"] = pre_bfd or "off"

    # data plane, per direction
    worst_outage = 0.0
    for flow, label in FLOWS.items():
        sent = load_sent(os.path.join(d, "probes", f"{flow}.sent"))
        recv = load_recv(os.path.join(d, "probes", f"{flow}.recv"))
        r = analyze_flow(sent, recv, t_fault, t_restore)
        summary[flow] = r
        if "error" in r:
            checks.append(check(f"{name}.{flow}.probe", False, f"{label}: {r['error']}"))
            continue
        checks.append(check(f"{name}.{flow}.baseline_clean", r["pre_fault_lost"] == 0,
                            f"{label}: {r['pre_fault_lost']}/{r['pre_fault_sent']} packets lost BEFORE the fault"))
        o = r["outage"]
        failed_over = o["recovered"] and r["delivered_during_fault_after_outage"] > 0
        checks.append(check(f"{name}.{flow}.failover", failed_over,
                            f"{label}: traffic {'resumed' if failed_over else 'did NOT resume'} while ISP-A was still broken "
                            f"({r['delivered_during_fault_after_outage']} packets delivered over the backup path)"))
        lim = thr.get("outage_max_ms")
        ok = o["window_ms"] is not None and o["recovered"] and (lim is None or o["window_ms"] <= lim)
        checks.append(check(f"{name}.{flow}.outage_ms", ok,
                            f"{label}: loss window {o['window_ms']} ms ({o['lost']} packets, longest run "
                            f"{o['longest_run_ms']} ms; limit {lim} ms)", o["window_ms"]))
        if o["window_ms"] is not None:
            worst_outage = max(worst_outage, o["window_ms"])
        fb = r["failback"]
        flim = thr_all["failback_loss_max_ms"]
        ok = fb["recovered"] and r["delivered_after_restore"] > 0 and fb["window_ms"] is not None and fb["window_ms"] <= flim
        checks.append(check(f"{name}.{flow}.failback_loss_ms", ok,
                            f"{label}: failback loss window {fb['window_ms']} ms ({fb['lost']} packets; limit {flim} ms)",
                            fb["window_ms"]))
    summary["worst_outage_ms"] = worst_outage

    # control plane
    ent = sample_series(load_poll(os.path.join(d, "poll", "ent.jsonl")), ent_extract)
    inet = sample_series(load_poll(os.path.join(d, "poll", "inet.jsonl")), inet_extract)
    summary["poll"] = {"ent": series_stats(ent, t_fault - 5, t_restore + 5), "inet": series_stats(inet, t_fault - 5, t_restore + 5)}

    det = transition(ent, t_fault, lambda v: v["ispa_state"] != "Established", t_restore, stable=False)
    lo, hi = thr.get("detect_min_ms", 0), thr.get("detect_max_ms")
    ok = det is not None and lo <= det["ms"] and (hi is None or det["ms"] <= hi)
    checks.append(check(f"{name}.detect_ms", ok,
                        f"ent declared the ISP-A session down {det and det['ms']} ms after the fault "
                        f"(bracket {det and det['bracket_ms']}; expected {lo}..{hi} ms)", det and det["ms"]))
    summary["detect"] = det

    rlim = thr.get("reroute_max_ms")
    for node, series, nh_backup, nh_primary in (("ent", ent, ISPB, ISPA), ("inet", inet, INET_VIA_ISPB, INET_VIA_ISPA)):
        fo = transition(series, t_fault, lambda v, n=nh_backup: v["nh"] == frozenset({n}), t_restore)
        ok = fo is not None and (rlim is None or fo["ms"] <= rlim)
        checks.append(check(f"{name}.{node}_reroute_ms", ok,
                            f"{node} FIB moved to the ISP-B path {fo and fo['ms']} ms after the fault (limit {rlim} ms)",
                            fo and fo["ms"]))
        fb = transition(series, t_restore, lambda v, n=nh_primary: v["nh"] == frozenset({n}))
        ok = fb is not None and fb["ms"] <= thr_all["failback_max_s"] * 1000
        checks.append(check(f"{name}.{node}_failback_ms", ok,
                            f"{node} FIB back on the ISP-A path {fb and fb['ms']} ms after the repair "
                            f"(must happen and stay; limit {thr_all['failback_max_s']} s)", fb and fb["ms"]))
        summary[f"{node}_reroute"] = fo
        summary[f"{node}_failback"] = fb

    for when, want in (("during", "isp-b"), ("after", "isp-a")):
        for flow, label in (("h1_srv", "h1->srv"), ("srv_h1", "srv->h1")):
            hops = parse_traceroute(read_text(os.path.join(d, "trace", f"{when}_{flow}.txt")))
            path = classify_path(hops)
            checks.append(check(f"{name}.path_{when}.{label}", path == want,
                                f"traceroute {label} {when} the fault: {path} (want {want}; hops {hops})", hops))
    return {"kind": "scenario", "scenario": name, "checks": checks, "summary": summary, "meta": meta}


# --------------------------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------------------------


def _fmt(v, unit=""):
    return "n/a" if v is None else f"{v:,.1f}{unit}" if isinstance(v, float) else f"{v}{unit}"


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
    bfd = results.get("bfd", {}).get("summary", {}).get("worst_outage_ms")
    hold = results.get("holdtimer", {}).get("summary", {}).get("worst_outage_ms")
    need = DEFAULT_THRESHOLDS["bfd_speedup_min"]
    if bfd and hold:
        ratio = round(hold / bfd, 1)
        checks.append(check("compare.bfd_speedup", ratio >= need,
                            f"hold-timer-only outage {hold:,.1f} ms vs BFD {bfd:,.1f} ms = {ratio}x (want >= {need}x)", ratio))
    else:
        checks.append(check("compare.bfd_speedup", False, f"cannot compare: bfd={bfd} holdtimer={hold} (both must have run and lost traffic)"))
    n_fail = sum(1 for c in checks if c["status"] == "FAIL")
    return results, checks, n_fail


def render_markdown(evidence, results, checks, n_fail):
    meta = load_json(os.path.join(evidence, "meta.json"), {}) or {}
    lines = ["# Lab N6 - BGP multihoming + BFD: measured results", ""]
    lines.append(
        f"Run: {meta.get('run_id', 'local')} - started {meta.get('started', '?')} - FRR {meta.get('frr_image', '?')} - "
        f"containerlab {meta.get('clab_version', '?')} - kernel {meta.get('kernel', '?')} - probe every "
        f"{meta.get('probe_interval_ms', '?')} ms per direction."
    )
    lines += ["", f"**Verdict: {'PASS' if n_fail == 0 else 'FAIL'}** ({len(checks) - n_fail}/{len(checks)} checks passed)", ""]
    lines += [
        "## Failover: ISP-A breaks while traffic flows",
        "",
        "Outage = user traffic lost (UDP probe every 10 ms, each direction). Detect = ent declared the ISP-A BGP "
        "session down. Reroute = FIB next hop moved to ISP-B (ent: outbound; inet: inbound). Upper bounds of the "
        "poll bracket; see result.json for brackets.",
        "",
        "| Scenario | BFD | Outage out (ms) | Outage in (ms) | Detect (ms) | ent reroute (ms) | inet reroute (ms) | "
        "Failback loss out / in (ms) | Checks |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in SCENARIOS:
        r = results.get(s)
        if not r:
            lines.append(f"| {s} | | | | | | | | **MISSING** |")
            continue
        sm = r["summary"]
        out = sm.get("h1_to_srv", {})
        inn = sm.get("srv_to_h1", {})
        sc = r["checks"]
        bad = sum(1 for c in sc if c["status"] == "FAIL")
        lines.append(
            "| {d} | {bfd} | {o} | {i} | {det} | {er} | {ir} | {fo} / {fi} | {v} |".format(
                d=SCENARIO_TEXT[s], bfd=sm.get("bfd"),
                o=_fmt(out.get("outage", {}).get("window_ms")), i=_fmt(inn.get("outage", {}).get("window_ms")),
                det=_fmt((sm.get("detect") or {}).get("ms")),
                er=_fmt((sm.get("ent_reroute") or {}).get("ms")), ir=_fmt((sm.get("inet_reroute") or {}).get("ms")),
                fo=_fmt(out.get("failback", {}).get("window_ms")), fi=_fmt(inn.get("failback", {}).get("window_ms")),
                v="PASS" if bad == 0 else f"**FAIL ({bad})**",
            )
        )
    lines += ["", "## Every check", "", "| Status | Check | Detail |", "|---|---|---|"]
    for c in checks:
        lines.append(f"| {'PASS' if c['status'] == 'PASS' else '**' + c['status'] + '**'} | `{c['id']}` | "
                     f"{str(c['detail']).replace('|', '/')} |")
    lines += ["", "Raw evidence (probe logs, poller samples, vtysh JSON, traceroutes, FRR logs) is next to this file; "
              "`python3 measure.py report <dir> out.md` recomputes every number."]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------------
# run.sh wait-loop conditions
# --------------------------------------------------------------------------------------------


def cond(args, text):
    name = args[0] if args else ""
    if name == "trace-via":
        return classify_path(parse_traceroute(text)) == args[1]
    docs = parse_docs(text)
    if name == "established":
        states = {}
        for d in docs:
            states.update(bgp_peer_states(d))
        return all(states.get(p) == "Established" for p in args[1:])
    if name == "nexthop":
        prefix, want = args[1], frozenset(args[2:])
        return any(route_nexthops(d, prefix) == want for d in docs)
    if name == "bfd":
        peer, want = args[1], args[2]
        st = bfd_status(docs[0] if docs else [], peer)
        return (st is None) if want == "absent" else (st == want)
    if name == "best-nexthop":
        want = args[1]
        return any((best_path(bgp_prefix_paths(d)) or {}).get("nexthop") == want for d in docs)
    raise SystemExit(f"unknown condition {name!r}")


# --------------------------------------------------------------------------------------------


def _print_checks(checks):
    for c in checks:
        print(f"{c['status']:4} {c['id']}: {c['detail']}")


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "baseline":
        r = evaluate_baseline(argv[2])
        with open(os.path.join(argv[2], "result.json"), "w") as fh:
            json.dump(r, fh, indent=2, default=list)
        _print_checks(r["checks"])
        return 0
    if cmd == "scenario":
        r = evaluate_scenario(argv[2])
        with open(os.path.join(argv[2], "result.json"), "w") as fh:
            json.dump(r, fh, indent=2, default=list)
        _print_checks(r["checks"])
        return 0
    if cmd == "report":
        results, checks, n_fail = build_report(argv[2])
        with open(os.path.join(argv[2], "results.json"), "w") as fh:
            json.dump({"verdict": "PASS" if n_fail == 0 else "FAIL", "failed": n_fail, "checks": checks,
                       "results": results}, fh, indent=2, default=list)
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
