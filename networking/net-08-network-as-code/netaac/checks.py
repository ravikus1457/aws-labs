"""Turn raw router/host output into PASS/FAIL checks against intent.

Every function here is pure (text or parsed JSON in, Check list out) so each one is
unit-tested against recorded FRR output, including outputs that MUST produce FAIL.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from .model import Net, Probe

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


@dataclass
class Check:
    category: str
    subject: str
    expect: str
    observed: str
    status: str
    detail: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _load(text_or_obj):
    if isinstance(text_or_obj, (dict, list)):
        return text_or_obj
    try:
        return json.loads(text_or_obj or "{}")
    except json.JSONDecodeError:
        return None


# ---- BGP --------------------------------------------------------------------------------
def bgp_checks(net: Net, rname: str, summary) -> list[Check]:
    """`show bgp ipv4 unicast summary json` (or `show bgp summary json`) vs intent."""
    want = net.expected_bgp(rname)
    if not want:
        return []
    data = _load(summary)
    if isinstance(data, dict) and "ipv4Unicast" in data:
        data = data["ipv4Unicast"]
    peers = (data or {}).get("peers", {}) if isinstance(data, dict) else {}
    out = []
    neighbors = {n.peer_ip: n for n in net.routers[rname].neighbors}
    for peer_ip, asn in sorted(want.items()):
        n = neighbors[peer_ip]
        subject = f"{rname} -> {n.peer} ({peer_ip})"
        p = peers.get(peer_ip)
        if p is None:
            out.append(Check("bgp-session", subject, f"Established, AS{asn}", "peer not configured", FAIL))
            continue
        state, remote = p.get("state", "?"), p.get("remoteAs")
        ok = state == "Established" and remote == asn
        out.append(Check("bgp-session", subject, f"Established, AS{asn}", f"{state}, AS{remote}", PASS if ok else FAIL))
        # Prefix counts prove the per-neighbor import/export policy, not just the session.
        want_rcv, want_snt = len(n.import_prefixes), len(n.export_prefixes)
        rcv, snt = p.get("pfxRcd"), p.get("pfxSnt")
        if rcv is None or snt is None:
            out.append(Check("bgp-policy", subject, f"received {want_rcv}, sent {want_snt}", "counts not in output", SKIP))
        else:
            ok = ok and rcv == want_rcv and snt == want_snt
            out.append(Check("bgp-policy", subject, f"received {want_rcv}, sent {want_snt}", f"received {rcv}, sent {snt}", PASS if ok else FAIL))
    return out


# ---- OSPF -------------------------------------------------------------------------------
def _ospf_state(entry: dict) -> str:
    for key in ("nbrState", "state", "converged"):
        if isinstance(entry.get(key), str):
            return entry[key]
    return "?"


def ospf_checks(net: Net, rname: str, neighbors) -> list[Check]:
    """`show ip ospf neighbor json` vs intent (Full adjacency to every expected router-id)."""
    want = net.expected_ospf(rname)
    if not want:
        return []
    data = _load(neighbors)
    if isinstance(data, dict) and isinstance(data.get("neighbors"), dict):
        data = data["neighbors"]
    data = data if isinstance(data, dict) else {}
    out = []
    for rid, ifname in sorted(want.items()):
        entries = data.get(rid) or []
        if isinstance(entries, dict):
            entries = [entries]
        states = [_ospf_state(e) for e in entries if isinstance(e, dict)]
        ok = any(s.startswith("Full") for s in states)
        out.append(Check("ospf-adjacency", f"{rname} -> {rid} via {ifname}", "Full", ", ".join(states) or "no neighbor", PASS if ok else FAIL))
    return out


# ---- RIB --------------------------------------------------------------------------------
def route_checks(net: Net, rname: str, routes) -> list[Check]:
    """`show ip route json` vs intent: each expected prefix selected, installed, right protocol."""
    data = _load(routes)
    data = data if isinstance(data, dict) else {}
    out = []
    for prefix, proto in sorted(net.expected_routes(rname).items()):
        entries = data.get(prefix) or []
        chosen = [e for e in entries if isinstance(e, dict) and e.get("selected")]
        if not chosen:
            seen = ", ".join(sorted({e.get("protocol", "?") for e in entries if isinstance(e, dict)})) or "absent"
            out.append(Check("route", f"{rname} {prefix}", proto, f"not selected ({seen})", FAIL))
            continue
        got = chosen[0].get("protocol", "?")
        installed = chosen[0].get("installed", got == "connected")
        ok = got == proto and bool(installed)
        observed = got + ("" if installed else " (not installed in FIB)")
        out.append(Check("route", f"{rname} {prefix}", proto, observed, PASS if ok else FAIL))
    return out


# ---- ACL --------------------------------------------------------------------------------
_COUNTER = re.compile(r'^\[(\d+):(\d+)\]\s+-A\s+NET08-ACL\b.*?--comment\s+"?([^"\s]+)"?')
_RULE = re.compile(r'^(?:\[\d+:\d+\]\s+)?-A\s+NET08-ACL\b.*?--comment\s+"?([^"\s]+)"?')


def acl_counters(iptables_save: str) -> dict[str, int]:
    """Packet counters per rule comment from `iptables-save -c -t filter`."""
    out: dict[str, int] = {}
    for line in iptables_save.splitlines():
        m = _COUNTER.match(line.strip())
        if m:
            out[m.group(3)] = out.get(m.group(3), 0) + int(m.group(1))
    return out


def acl_order(iptables_save: str) -> list[str]:
    return [m.group(1) for line in iptables_save.splitlines() if (m := _RULE.match(line.strip()))]


def acl_installed_checks(net: Net, rname: str, iptables_save: str) -> list[Check]:
    want = [r.comment for r in net.acl_rules_for(rname)]
    if not want:
        return []
    jump = any(re.match(r"^(\[\d+:\d+\]\s+)?-A FORWARD\b.*-j NET08-ACL\b", ln.strip()) for ln in iptables_save.splitlines())
    have = [c for c in acl_order(iptables_save) if c.startswith("acl:")]
    ok = jump and have == want
    observed = ("FORWARD -> NET08-ACL" if jump else "NO jump from FORWARD") + "; rules: " + (", ".join(have) or "none")
    return [Check("acl-installed", rname, "jump + rules in intent order: " + ", ".join(want), observed, PASS if ok else FAIL)]


# ---- probes -----------------------------------------------------------------------------
def probe_check(p: Probe, reached: bool, counter_delta: int | None) -> Check:
    """A deny only PASSES when the probe failed AND the intended ACL rule's counter moved:
    a probe that fails because routing is broken must not be scored as the ACL working."""
    expect = p.expect if p.expect == "allow" else f"deny by {p.rule} on {p.router}"
    if p.expect == "allow":
        if reached:
            return Check("reachability", p.id, "allow", "reached", PASS, p.origin)
        return Check("reachability", p.id, "allow", "unreachable", FAIL, f"{p.origin}: intent says reachable")
    if reached:
        return Check("reachability", p.id, expect, "reached", FAIL, f"{p.origin}: reachable but intent says deny")
    if not counter_delta or counter_delta <= 0:
        return Check(
            "reachability",
            p.id,
            expect,
            "blocked, ACL counter unchanged",
            FAIL,
            f"{p.origin}: blocked, but not by {p.rule} - broken path, not policy",
        )
    return Check("reachability", p.id, expect, f"blocked (+{counter_delta} pkts on {p.rule})", PASS, p.origin)
