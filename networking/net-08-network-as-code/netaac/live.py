"""Live steps against the deployed lab: post-deploy setup, convergence, tests, drift demo.

`lab` is anything with the DockerLab interface (run / vtysh / netns / frr_reload). The unit
tests drive every function here through tests/fakenet.py, including fakes that break the
network or the detector on purpose.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from pathlib import Path

from . import checks as C
from . import drift as D
from .checks import FAIL, PASS, Check
from .lab import LabError
from .model import Net, Probe


def _check_rc(category: str, subject: str, what: str, rc: int, out: str) -> Check:
    return Check(category, subject, what, "ok" if rc == 0 else f"rc {rc}", PASS if rc == 0 else FAIL, out.strip()[-300:] if rc else "")


# ---- post-deploy: forwarding, ACLs, host addressing, listeners ----------------------------
def post_deploy(net: Net, lab, build: Path, sleep=time.sleep) -> list[Check]:
    out: list[Check] = []
    for rname in sorted(net.routers):
        rc, txt = lab.run(rname, ["sysctl", "-w", "net.ipv4.ip_forward=1"])
        out.append(_check_rc("setup", rname, "ip_forward=1", rc, txt))
        rules = (Path(build) / "acl" / f"{rname}.rules").read_text(encoding="utf-8")
        rc, txt = lab.netns(rname, ["iptables-restore"], stdin=rules)
        out.append(_check_rc("setup", rname, "iptables-restore acl/" + rname + ".rules", rc, txt))
    for hname in sorted(net.hosts):
        h = net.hosts[hname]
        steps = [["ip", "link", "set", "eth1", "up"], ["ip", "addr", "replace", str(h.ip), "dev", "eth1"]]
        steps += [["ip", "route", "replace", r, "via", str(h.gateway), "dev", "eth1"] for r in h.routes]
        ok, log = True, []
        for argv in steps:
            rc, txt = lab.run(hname, argv)
            ok = ok and rc == 0
            if rc:
                log.append(f"{' '.join(argv)} -> {txt.strip()}")
        out.append(Check("setup", hname, f"{h.ip} gw {h.gateway}", "ok" if ok else "failed", PASS if ok else FAIL, "; ".join(log)[-300:]))
        ports = [int(s.split("/")[1]) for s in h.services if s.startswith("tcp/")]
        for port in ports:
            lab.run(hname, ["socat", f"TCP-LISTEN:{port},fork,reuseaddr", "SYSTEM:echo net08"], detach=True)
        if ports:
            listening = []
            for _ in range(10):
                rc, txt = lab.run(hname, ["ss", "-Htln"])
                listening = [p for p in ports if f":{p} " in txt or txt.rstrip().endswith(f":{p}")]
                if len(listening) == len(ports):
                    break
                sleep(0.5)
            ok = len(listening) == len(ports)
            out.append(
                Check(
                    "setup",
                    hname,
                    "listening tcp/" + ",".join(map(str, ports)),
                    ("tcp/" + ",".join(map(str, listening))) if listening else "none",
                    PASS if ok else FAIL,
                )
            )
    return out


# ---- control plane ----------------------------------------------------------------------------
def routing_snapshot(net: Net, lab) -> list[Check]:
    out: list[Check] = []
    for rname in sorted(net.routers):
        r = net.routers[rname]
        try:
            if r.bgp:
                out += C.bgp_checks(net, rname, lab.vtysh(rname, "show bgp ipv4 unicast summary json"))
            if r.ospf:
                out += C.ospf_checks(net, rname, lab.vtysh(rname, "show ip ospf neighbor json"))
            out += C.route_checks(net, rname, lab.vtysh(rname, "show ip route json"))
        except LabError as exc:
            out.append(Check("control-plane", rname, "vtysh answers", "error", FAIL, str(exc)[:300]))
    return out


def wait_converged(net: Net, lab, timeout: float = 180, interval: float = 2, clock=time.monotonic, sleep=time.sleep):
    """Poll until every BGP/OSPF/route check passes. Returns (seconds | None, last checks, polls)."""
    start = clock()
    polls = 0
    while True:
        polls += 1
        snap = routing_snapshot(net, lab)
        if snap and all(c.status != FAIL for c in snap):
            return round(clock() - start, 1), snap, polls
        if clock() - start >= timeout:
            return None, snap, polls
        sleep(interval)


# ---- data plane --------------------------------------------------------------------------------
def acl_checks(net: Net, lab) -> list[Check]:
    out: list[Check] = []
    for rname in sorted(net.routers):
        if not net.acl_rules_for(rname):
            continue
        rc, txt = lab.netns(rname, ["iptables-save", "-c", "-t", "filter"])
        if rc:
            out.append(Check("acl-installed", rname, "iptables-save works", f"rc {rc}", FAIL, txt.strip()[-300:]))
            continue
        out += C.acl_installed_checks(net, rname, txt)
    return out


def _counter(lab, router: str, rule: str) -> int | None:
    rc, txt = lab.netns(router, ["iptables-save", "-c", "-t", "filter"])
    if rc:
        return None
    return C.acl_counters(txt).get(f"acl:{rule}", 0)


def _attempt(lab, p: Probe) -> bool:
    if p.proto == "icmp":
        rc, _ = lab.run(p.src, ["ping", "-c", "3", "-W", "1", "-q", p.dst_ip], timeout=20)
    else:
        rc, _ = lab.run(p.src, ["timeout", "3", "bash", "-c", f"exec 3<>/dev/tcp/{p.dst_ip}/{p.port}"], timeout=10)
    return rc == 0


def run_probes(net: Net, lab) -> list[Check]:
    out: list[Check] = []
    for p in net.probes:
        if p.expect == "deny":
            before = _counter(lab, p.router, p.rule)
            reached = _attempt(lab, p)
            after = _counter(lab, p.router, p.rule)
            delta = None if before is None or after is None else after - before
            out.append(C.probe_check(p, reached, delta))
        else:
            reached = _attempt(lab, p) or _attempt(lab, p)  # one retry: first-packet ARP loss
            out.append(C.probe_check(p, reached, None))
    return out


def run_tests(net: Net, lab) -> list[Check]:
    return acl_checks(net, lab) + routing_snapshot(net, lab) + run_probes(net, lab)


# ---- drift -----------------------------------------------------------------------------------
def drift_check(net: Net, lab, build: Path) -> dict[str, D.Drift]:
    out = {}
    for rname in sorted(net.routers):
        intended = (Path(build) / "configs" / rname / "frr.conf").read_text(encoding="utf-8")
        out[rname] = D.diff(rname, intended, lab.vtysh(rname, "show running-config"))
    return out


@dataclass
class Injection:
    router: str
    commands: list[str]
    kind: str  # "extra" | "missing"
    line: str  # what the drift report must contain
    story: str


def plan_injections(net: Net) -> list[Injection]:
    """Three out-of-band changes of different shapes, derived from intent (not hard-coded)."""
    spokes = sorted(n for n, r in net.routers.items() if r.role == "spoke" and r.bgp)
    ospf = sorted(n for n, r in net.routers.items() if any(i.ospf == "active" for i in r.ifaces))
    out = []
    if spokes:
        r = spokes[-1]
        out.append(
            Injection(
                r, ["ip route 203.0.113.0/24 blackhole"], "extra", "ip route 203.0.113.0/24 blackhole", "undocumented static route added by hand"
            )
        )
    if ospf:
        r = net.routers[ospf[0]]
        ifname = next(i.name for i in r.ifaces if i.ospf == "active")
        out.append(
            Injection(
                r.name,
                [f"interface {ifname}", "ip ospf cost 444"],
                "extra",
                f"interface {ifname} > ip ospf cost 444",
                "OSPF cost tuned on the box during an incident",
            )
        )
    if spokes:
        r = net.routers[spokes[0]]
        n = r.neighbors[0]
        out.append(
            Injection(
                r.name,
                [f"router bgp {r.asn}", f"no neighbor {n.peer_ip} description"],
                "missing",
                f"router bgp {r.asn} > neighbor {n.peer_ip} description {n.peer}",
                "a configured line removed on the box",
            )
        )
    return out


def drift_demo(net: Net, lab, build: Path, clock=time.monotonic) -> tuple[list[Check], dict]:
    out: list[Check] = []
    metrics: dict = {}
    baseline = drift_check(net, lab, build)
    for rname, d in baseline.items():
        out.append(
            Check(
                "drift-baseline",
                rname,
                "running == intent",
                "clean" if d.clean else f"{len(d.missing)} missing, {len(d.extra)} extra",
                PASS if d.clean else FAIL,
                "; ".join(d.missing + d.extra)[:400],
            )
        )

    injections = plan_injections(net)
    for inj in injections:
        try:
            lab.vtysh(inj.router, "configure terminal", *inj.commands, "end")
            out.append(Check("drift-inject", inj.router, inj.story, " / ".join(inj.commands), PASS))
        except LabError as exc:
            out.append(Check("drift-inject", inj.router, inj.story, "vtysh failed", FAIL, str(exc)[:300]))

    detected = drift_check(net, lab, build)
    found = 0
    for inj in injections:
        reported = getattr(detected[inj.router], inj.kind)
        hit = inj.line in reported
        found += hit
        out.append(Check("drift-detect", inj.router, f"{inj.kind}: {inj.line}", "reported" if hit else "NOT reported", PASS if hit else FAIL))
    expected = {(i.router, i.kind, i.line) for i in injections}
    for rname, d in detected.items():
        base = baseline[rname]
        unexplained = [
            (k, line) for k in ("missing", "extra") for line in getattr(d, k) if (rname, k, line) not in expected and line not in getattr(base, k)
        ]
        out.append(
            Check(
                "drift-precision",
                rname,
                "no drift beyond what was injected",
                f"{len(unexplained)} unexplained",
                PASS if not unexplained else FAIL,
                "; ".join(f"{k}: {line}" for k, line in unexplained)[:400],
            )
        )
    metrics["drift_injected"] = len(injections)
    metrics["drift_detected"] = found

    t0 = clock()
    methods: dict[str, str] = {}
    for rname in sorted(r for r, d in detected.items() if not d.clean):
        rc, txt = lab.frr_reload(rname)
        methods[rname] = "frr-reload.py" if rc == 0 else f"frr-reload.py rc {rc}"
    healed = drift_check(net, lab, build)
    for rname, d in healed.items():
        if d.clean:
            continue
        for seq in D.remediation(d):
            with contextlib.suppress(LabError):
                lab.vtysh(rname, *seq)
        methods[rname] = methods.get(rname, "") + " + vtysh remediation"
    healed = drift_check(net, lab, build)
    metrics["heal_seconds"] = round(clock() - t0, 1)
    metrics["heal_method"] = methods
    for rname, d in healed.items():
        out.append(
            Check(
                "drift-heal",
                rname,
                "running == intent after re-apply",
                "clean" if d.clean else f"{len(d.missing)} missing, {len(d.extra)} extra",
                PASS if d.clean else FAIL,
                methods.get(rname, "untouched") + ("" if d.clean else ": " + "; ".join(d.missing + d.extra)[:300]),
            )
        )
    metrics["drift_report_after_injection"] = {r: d.as_dict() for r, d in detected.items()}
    return out, metrics
