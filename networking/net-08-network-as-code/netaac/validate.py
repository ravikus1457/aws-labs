"""Pre-deploy validation of the source of truth.

Runs in four layers and stops at the first layer with errors, because later layers assume
the earlier ones hold (you cannot check overlaps on a prefix that does not parse):

  1. schema       - structure, types, typos (additionalProperties: false)
  2. addressing   - prefixes parse, references resolve
  3. plan         - overlaps, duplicates, every interface in exactly one subnet, blocks
  4. intent       - topology shape, BGP/OSPF intent, a route to every host LAN, ACL sanity,
                    declared probes agree with the ACL rules

Every finding has a stable code so a test (and a reviewer) can assert WHY a change was
rejected, not just that it was.
"""

from __future__ import annotations

import ipaddress
import itertools
import json
from dataclasses import asdict, dataclass
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path

import jsonschema

from .intent import DuplicateKeyError, loads
from .model import ANY_NET, Net

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "intent" / "schema.json"


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str  # error | warning
    where: str
    message: str


class _Collector:
    def __init__(self) -> None:
        self.items: list[Finding] = []

    def error(self, code: str, where: str, message: str) -> None:
        self.items.append(Finding(code, "error", where, message))

    def warn(self, code: str, where: str, message: str) -> None:
        self.items.append(Finding(code, "warning", where, message))

    @property
    def has_errors(self) -> bool:
        return any(f.severity == "error" for f in self.items)


def errors(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "error"]


def validate_text(text: str) -> list[Finding]:
    try:
        data = loads(text)
    except DuplicateKeyError as exc:
        return [Finding("DUPLICATE_KEY", "error", "yaml", str(exc))]
    except Exception as exc:  # yaml.YAMLError, not-a-mapping
        return [Finding("YAML", "error", "yaml", str(exc).splitlines()[0])]
    return validate(data)


def validate(data: dict, schema_path: Path = SCHEMA_PATH) -> list[Finding]:
    c = _Collector()
    _schema(c, data, schema_path)
    if c.has_errors:
        return c.items
    nets = _parse(c, data)
    if c.has_errors:
        return c.items
    _references(c, data)
    if c.has_errors:
        return c.items
    _plan(c, data, nets)
    _topology(c, data, nets)
    _routing(c, data, nets)
    _acl_static(c, data)
    if c.has_errors:
        return c.items
    net = Net(data)
    _route_gaps(c, net)
    _acl(c, net)
    _probes(c, data, net)
    return c.items


# ---- layer 1: schema --------------------------------------------------------------------
def _schema(c: _Collector, data: dict, schema_path: Path) -> None:
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    for err in sorted(validator.iter_errors(data), key=lambda e: list(map(str, e.absolute_path))):
        where = "/".join(str(p) for p in err.absolute_path) or "(root)"
        c.error("SCHEMA", where, err.message)


# ---- layer 2: parse + references ---------------------------------------------------------
def _parse(c: _Collector, data: dict) -> dict[str, IPv4Network]:
    """Parse every prefix strictly (host bits set = error). Returns label -> network."""
    nets: dict[str, IPv4Network] = {}

    def take(label: str, text: str) -> None:
        try:
            nets[label] = ipaddress.ip_network(text, strict=True)
        except ValueError as exc:
            c.error("PREFIX_NOT_NETWORK", label, f"{text}: {exc}")

    take("lab.mgmt_subnet", data["lab"]["mgmt_subnet"])
    take("ip_plan.wan_block", data["ip_plan"]["wan_block"])
    take("ip_plan.loopback_block", data["ip_plan"]["loopback_block"])
    for name, site in data["sites"].items():
        take(f"sites.{name}.block", site["block"])
    for name, sub in data["subnets"].items():
        take(f"subnets.{name}", sub["prefix"])
    for where, ip in _all_addresses(data):
        try:
            IPv4Address(ip)
        except ValueError as exc:
            c.error("BAD_ADDRESS", where, str(exc))
    return nets


def _all_addresses(data: dict) -> list[tuple[str, str]]:
    out = []
    for rname, r in data["routers"].items():
        out.append((f"routers.{rname}.loopback", r["loopback"]))
        for ifname, i in r["interfaces"].items():
            out.append((f"routers.{rname}.{ifname}", i["ip"]))
    for hname, h in data.get("hosts", {}).items():
        out.append((f"hosts.{hname}", h["ip"]))
    return out


def _references(c: _Collector, data: dict) -> None:
    sites, subnets, routers, hosts = data["sites"], data["subnets"], data["routers"], data.get("hosts", {})
    for name, sub in subnets.items():
        if sub.get("site") is not None and sub["site"] not in sites:
            c.error("REF_UNKNOWN", f"subnets.{name}.site", f"unknown site {sub['site']!r}")
        if sub["kind"] == "lan" and sub.get("site") is None:
            c.error("SITE_REQUIRED", f"subnets.{name}", "a LAN must belong to a site (only WAN p2p links are siteless)")
    for rname, r in routers.items():
        if r["site"] not in sites:
            c.error("REF_UNKNOWN", f"routers.{rname}.site", f"unknown site {r['site']!r}")
        for ifname, i in r["interfaces"].items():
            if i["subnet"] not in subnets:
                c.error("REF_UNKNOWN", f"routers.{rname}.{ifname}", f"unknown subnet {i['subnet']!r}")
    for hname, h in hosts.items():
        if h["subnet"] not in subnets:
            c.error("REF_UNKNOWN", f"hosts.{hname}", f"unknown subnet {h['subnet']!r}")
        if hname in routers:
            c.error("NAME_CLASH", f"hosts.{hname}", "a host and a router share this name")
    for idx, (a, b) in enumerate(data.get("bgp_sessions", [])):
        for end in (a, b):
            if end not in routers:
                c.error("REF_UNKNOWN", f"bgp_sessions[{idx}]", f"unknown router {end!r}")
    for idx, rule in enumerate(data["acl"]["rules"]):
        for key in ("src", "dst"):
            ref = rule[key]
            if ref == "any" and key == "dst":
                continue
            if ref not in subnets:
                c.error("REF_UNKNOWN", f"acl.rules[{idx}].{key}", f"unknown subnet {ref!r}")
    for idx, p in enumerate(data.get("probes", []) or []):
        for key in ("from", "to"):
            if p[key] not in hosts:
                c.error("REF_UNKNOWN", f"probes[{idx}].{key}", f"unknown host {p[key]!r}")


# ---- layer 3: the address plan ------------------------------------------------------------
def _plan(c: _Collector, data: dict, nets: dict[str, IPv4Network]) -> None:
    mgmt = nets["lab.mgmt_subnet"]
    wan = nets["ip_plan.wan_block"]
    lo_block = nets["ip_plan.loopback_block"]
    blocks = {k: v for k, v in nets.items() if k.startswith("sites.")}
    subnets = {k.split(".", 1)[1]: v for k, v in nets.items() if k.startswith("subnets.")}

    # Allocation blocks must be disjoint (loopbacks may sit inside a site block's range only
    # if they do not collide with a subnet; that is checked per address below).
    for (ka, a), (kb, b) in itertools.combinations(blocks.items(), 2):
        if a.overlaps(b):
            c.error("SITE_BLOCK_OVERLAP", f"{ka} / {kb}", f"{a} overlaps {b}")
    for k, b in blocks.items():
        for label, other in (("wan_block", wan), ("mgmt_subnet", mgmt)):
            if b.overlaps(other):
                c.error("BLOCK_OVERLAP", k, f"{b} overlaps {label} {other}")
    for label, other in (("wan_block", wan), ("loopback_block", lo_block)):
        if other.overlaps(mgmt):
            c.error("MGMT_OVERLAP", f"ip_plan.{label}", f"{other} overlaps the management network {mgmt}")

    for (na, a), (nb, b) in itertools.combinations(sorted(subnets.items()), 2):
        if a.overlaps(b):
            c.error("SUBNET_OVERLAP", f"subnets.{na} / subnets.{nb}", f"{a} ({na}) overlaps {b} ({nb})")
    for name, sn in subnets.items():
        sub = data["subnets"][name]
        if sn.overlaps(mgmt):
            c.error("MGMT_OVERLAP", f"subnets.{name}", f"{sn} overlaps the management network {mgmt}")
        if sn.overlaps(lo_block):
            c.error("SUBNET_OVERLAP", f"subnets.{name}", f"{sn} overlaps the loopback block {lo_block}")
        if sub.get("site"):
            block = nets[f"sites.{sub['site']}.block"]
            if not sn.subnet_of(block):
                c.error("OUTSIDE_BLOCK", f"subnets.{name}", f"{sn} is outside site {sub['site']}'s block {block}")
        elif not sn.subnet_of(wan):
            c.error("OUTSIDE_BLOCK", f"subnets.{name}", f"siteless (WAN) subnet {sn} is outside the wan_block {wan}")
        if sub["kind"] == "p2p" and sn.prefixlen not in (30, 31):
            c.error("P2P_PREFIX_LEN", f"subnets.{name}", f"point-to-point links are /30 or /31, got /{sn.prefixlen}")

    # Every address: unique, and every interface in exactly one subnet (its declared one).
    owners: dict[IPv4Address, list[str]] = {}
    for where, ip in _all_addresses(data):
        owners.setdefault(IPv4Address(ip), []).append(where)
    for ip, who in sorted(owners.items()):
        if len(who) > 1:
            c.error("DUPLICATE_IP", ", ".join(who), f"{ip} is assigned {len(who)} times")

    def check_member(where: str, ip_text: str, declared: str) -> None:
        ip = IPv4Address(ip_text)
        containing = [n for n, sn in sorted(subnets.items()) if ip in sn]
        if declared not in containing:
            c.error("IFACE_NOT_IN_SUBNET", where, f"{ip} is not inside its subnet {declared} ({subnets[declared]})")
        if len(containing) > 1:
            c.error("IFACE_MULTI_SUBNET", where, f"{ip} falls inside {len(containing)} subnets: {', '.join(containing)}")
        sn = subnets[declared]
        if ip in sn and sn.prefixlen <= 30 and ip in (sn.network_address, sn.broadcast_address):
            c.error("RESERVED_ADDRESS", where, f"{ip} is the network or broadcast address of {sn}")
        if ip in mgmt:
            c.error("MGMT_OVERLAP", where, f"{ip} is inside the management network {mgmt}")

    for rname, r in data["routers"].items():
        for ifname, i in r["interfaces"].items():
            check_member(f"routers.{rname}.{ifname}", i["ip"], i["subnet"])
        lo = IPv4Address(r["loopback"])
        if lo not in lo_block:
            c.error("OUTSIDE_BLOCK", f"routers.{rname}.loopback", f"{lo} is outside the loopback_block {lo_block}")
        inside = [n for n, sn in subnets.items() if lo in sn]
        if inside:
            c.error("IFACE_MULTI_SUBNET", f"routers.{rname}.loopback", f"loopback {lo} falls inside subnet {inside[0]}")
    for hname, h in data.get("hosts", {}).items():
        check_member(f"hosts.{hname}", h["ip"], h["subnet"])


# ---- layer 4: intent ----------------------------------------------------------------------
def _topology(c: _Collector, data: dict, nets: dict) -> None:
    routers, hosts = data["routers"], data.get("hosts", {})
    members: dict[str, list[str]] = {n: [] for n in data["subnets"]}
    host_members: dict[str, list[str]] = {n: [] for n in data["subnets"]}
    for rname, r in routers.items():
        for ifname, i in r["interfaces"].items():
            members[i["subnet"]].append(f"{rname}:{ifname}")
            sub_site = data["subnets"][i["subnet"]].get("site")
            if sub_site is not None and sub_site != r["site"]:
                c.error("SITE_MISMATCH", f"routers.{rname}.{ifname}", f"router is in site {r['site']}, subnet {i['subnet']} in {sub_site}")
    for hname, h in hosts.items():
        host_members[h["subnet"]].append(hname)
    for name, sub in data["subnets"].items():
        ends, hs = members[name], host_members[name]
        if sub["kind"] == "p2p":
            routers_on = {e.split(":")[0] for e in ends}
            if len(ends) != 2 or len(routers_on) != 2:
                c.error("P2P_ENDPOINTS", f"subnets.{name}", f"a p2p link needs exactly 2 routers, has {ends or 'none'}")
            if hs:
                c.error("HOST_ON_P2P", f"subnets.{name}", f"hosts {hs} are on a point-to-point link")
        else:
            if len(ends) != 1:
                c.error("LAN_GATEWAY", f"subnets.{name}", f"a LAN needs exactly 1 router interface (its gateway), has {ends or 'none'}")
            if len(hs) > 1:
                c.error("LAN_HOSTS", f"subnets.{name}", f"lab constraint: one host per LAN (veth pair, no bridge), has {hs}")
        if not ends and not hs:
            c.warn("UNUSED_SUBNET", f"subnets.{name}", "nothing is attached to this subnet")
    asns: dict[int, list[str]] = {}
    for sname, s in data["sites"].items():
        asns.setdefault(s["asn"], []).append(sname)
    for asn, names in asns.items():
        if len(names) > 1:
            c.error("DUPLICATE_ASN", ", ".join(names), f"AS{asn} is used by {len(names)} sites (eBGP between sites needs one ASN each)")
    hubs = [n for n, s in data["sites"].items() if s["role"] == "hub"]
    if len(hubs) > 1:
        c.error("HUB_COUNT", "sites", f"hub-and-spoke allows one hub, found {hubs}")
    for hname, h in hosts.items():
        for svc in h.get("services", []):
            port = int(svc.split("/")[1])
            if not 1 <= port <= 65535:
                c.error("SERVICE_PORT", f"hosts.{hname}", f"{svc}: port out of range")


def _routing(c: _Collector, data: dict, nets: dict) -> None:
    routers, sites = data["routers"], data["sites"]
    seen: set[frozenset] = set()
    p2p_members: dict[str, dict[str, str]] = {}
    for rname, r in routers.items():
        for ifname, i in r["interfaces"].items():
            if data["subnets"][i["subnet"]]["kind"] == "p2p":
                p2p_members.setdefault(i["subnet"], {})[rname] = ifname
    bgp_routers: set[str] = set()
    for idx, (a, b) in enumerate(data.get("bgp_sessions", [])):
        where = f"bgp_sessions[{idx}]"
        key = frozenset((a, b))
        if a == b:
            c.error("BGP_SELF", where, f"{a} cannot peer with itself")
            continue
        if key in seen:
            c.error("BGP_DUP_SESSION", where, f"session {a}-{b} is listed twice")
        seen.add(key)
        bgp_routers.update((a, b))
        sa, sb = sites[routers[a]["site"]], sites[routers[b]["site"]]
        if routers[a]["site"] == routers[b]["site"] or sa["asn"] == sb["asn"]:
            c.error("BGP_SAME_AS", where, f"{a} and {b} are in the same AS; this design runs eBGP between sites only")
        elif {sa["role"], sb["role"]} != {"hub", "spoke"}:
            c.error("BGP_TOPOLOGY", where, f"sessions run hub<->spoke; {a} ({sa['role']}) - {b} ({sb['role']})")
        shared = [s for s, m in p2p_members.items() if a in m and b in m]
        if len(shared) != 1:
            c.error("BGP_NO_SHARED_LINK", where, f"{a} and {b} must share exactly one p2p subnet, share {shared or 'none'}")
    for rname, r in routers.items():
        ospf_ifs = {n: i for n, i in r["interfaces"].items() if i.get("ospf")}
        for ifname, i in ospf_ifs.items():
            sub = data["subnets"][i["subnet"]]
            if sub.get("site") is None:
                c.error("OSPF_ON_WAN", f"routers.{rname}.{ifname}", "OSPF runs inside a site; WAN links carry eBGP")
            if i["ospf"] == "active" and sub["kind"] == "lan":
                c.warn("OSPF_ACTIVE_ON_LAN", f"routers.{rname}.{ifname}", "hellos into a host LAN; make it passive")
            if i["ospf"] == "active" and sub["kind"] == "p2p":
                peers = {pr: pi for pr, pi in p2p_members.get(i["subnet"], {}).items() if pr != rname}
                for pr, pi in peers.items():
                    if routers[pr]["interfaces"][pi].get("ospf") != "active":
                        c.error("OSPF_ONE_SIDED", f"routers.{rname}.{ifname}", f"OSPF active here but not on {pr}:{pi} - no adjacency")
        if (r.get("ospf") or {}).get("redistribute_bgp") and (rname not in bgp_routers or not ospf_ifs):
            c.error("REDISTRIBUTE_NO_SOURCE", f"routers.{rname}.ospf", "redistribute_bgp needs both BGP sessions and OSPF interfaces")


def _route_gaps(c: _Collector, net: Net) -> None:
    host_lans = {h.subnet for h in net.hosts.values()}
    for rname in net.routers:
        have = net.expected_routes(rname)
        for lan in sorted(host_lans):
            prefix = str(net.subnets[lan]["prefix"])
            if prefix not in have:
                c.error("ROUTE_GAP", f"routers.{rname}", f"intent gives {rname} no route to {lan} ({prefix}); its hosts would be unreachable")


def _covers(a, b) -> bool:
    """Does rule a match every packet rule b matches?"""
    if not (b.src_net.subnet_of(a.src_net) and b.dst_net.subnet_of(a.dst_net)):
        return False
    if a.proto == "any":
        return True
    return a.proto == b.proto and (a.port is None or a.port == b.port)


def _intersects(a, b) -> bool:
    if not (a.src_net.overlaps(b.src_net) and a.dst_net.overlaps(b.dst_net)):
        return False
    if "any" in (a.proto, b.proto):
        return True
    return a.proto == b.proto and (a.port is None or b.port is None or a.port == b.port)


def _acl_static(c: _Collector, data: dict) -> None:
    names: dict[str, int] = {}
    for idx, rule in enumerate(data["acl"]["rules"]):
        where = f"acl.rules[{idx}] {rule['name']}"
        if rule["name"] in names:
            c.error("ACL_DUP_NAME", where, f"name already used by rule {names[rule['name']]}")
        names.setdefault(rule["name"], idx)
        if data["subnets"][rule["src"]]["kind"] != "lan":
            c.error("ACL_SRC_NOT_LAN", where, "src must be a LAN: the rule is enforced on that LAN's gateway interface")
        if rule["src"] == rule["dst"]:
            c.error("ACL_SAME_SUBNET", where, "traffic inside one LAN never crosses the router; this rule could never match")
        if "port" in rule and rule["proto"] not in ("tcp", "udp"):
            c.error("ACL_PORT_PROTO", where, f"port given with proto {rule['proto']}")


def _acl(c: _Collector, net: Net) -> None:
    rules = net.acl
    for j, rj in enumerate(rules):
        for ri in rules[:j]:
            if _covers(ri, rj):
                if ri.action != rj.action:
                    c.error("ACL_SHADOWED", f"acl.rules[{j}] {rj.name}", f"never matches: {ri.name} (earlier, {ri.action}) already covers it")
                else:
                    c.warn("ACL_REDUNDANT", f"acl.rules[{j}] {rj.name}", f"{ri.name} (earlier, same action) already covers it")
                break
        if rj.action == "permit" and not any(r.action == "deny" and _intersects(rj, r) for r in rules[j + 1 :]):
            c.warn("ACL_PERMIT_NOOP", f"acl.rules[{j}] {rj.name}", "default is permit and no later deny overlaps this rule")
    # Every deny rule should be exercised by at least one probe, or the live test cannot prove it.
    exercised = {p.rule for p in net.probes if p.expect == "deny"}
    for r in rules:
        if r.action == "deny" and r.name not in exercised:
            c.warn("ACL_UNTESTED", f"acl {r.name}", "no probe (derived or declared) hits this rule first; add a probe")
    if rules and rules[0].dst_net == ANY_NET and rules[0].proto == "any" and rules[0].action == "deny":
        c.warn("ACL_BROAD_DENY", f"acl {rules[0].name}", "first rule denies a whole LAN to anything; intended?")


def _probes(c: _Collector, data: dict, net: Net) -> None:
    seen = set()
    for idx, p in enumerate(data.get("probes", []) or []):
        where = f"probes[{idx}] {p['from']}->{p['to']}"
        key = (p["from"], p["to"], p["proto"], p.get("port"))
        if key in seen:
            c.error("PROBE_DUP", where, "listed twice")
        seen.add(key)
        if p["from"] == p["to"]:
            c.error("PROBE_SAME_HOST", where, "a host probing itself never crosses the network")
            continue
        if (p["proto"] == "tcp") != ("port" in p):
            c.error("PROBE_PORT", where, "tcp probes need a port; icmp probes take none")
            continue
        src, dst = net.hosts[p["from"]], net.hosts[p["to"]]
        verdict, rule = net.acl_verdict(src.ip.ip, dst.ip.ip, p["proto"], p.get("port"))
        if verdict != p["expect"]:
            why = f"rule {rule.name} ({rule.action})" if rule else "the default permit"
            c.error("PROBE_CONTRADICTS_ACL", where, f"intent says {p['expect']}, but {why} makes it {verdict}")
        if p["proto"] == "tcp" and p["expect"] == "allow" and f"tcp/{p['port']}" not in dst.services:
            c.error("PROBE_NO_LISTENER", where, f"{p['to']} has no tcp/{p['port']} service; the probe would fail for the wrong reason")


def summary(findings: list[Finding], source: str) -> dict:
    errs = errors(findings)
    return {
        "source": source,
        "verdict": "REJECT" if errs else "ACCEPT",
        "errors": len(errs),
        "warnings": len(findings) - len(errs),
        "codes": sorted({f.code for f in errs}),
        "findings": [asdict(f) for f in findings],
    }
