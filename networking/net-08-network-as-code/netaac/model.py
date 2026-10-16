"""The derived network: everything the generator renders and the tests expect, computed
from the source of truth. Build it only from intent that passed netaac.validate (the
validator is what guarantees references resolve and addresses are sane).

Nothing here talks to a router. That is the point: the expectations the live tests hold
the network to are computed from intent, never read back from the network itself.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Interface, IPv4Network

ANY_NET = IPv4Network("0.0.0.0/0")


@dataclass(frozen=True)
class Iface:
    router: str
    name: str
    subnet: str
    ip: IPv4Interface
    ospf: str | None  # None | "active" | "passive"


@dataclass
class Neighbor:
    peer: str
    peer_ip: str
    peer_asn: int
    local_iface: str
    import_prefixes: list[str]
    export_prefixes: list[str]

    @property
    def tag(self) -> str:
        return self.peer.upper()


@dataclass
class Router:
    name: str
    site: str
    asn: int
    role: str
    loopback: IPv4Address
    ifaces: list[Iface]
    redistribute_bgp: bool = False
    neighbors: list[Neighbor] = field(default_factory=list)
    bgp_networks: list[str] = field(default_factory=list)
    ospf_redistribute_prefixes: list[str] = field(default_factory=list)

    @property
    def ospf(self) -> bool:
        return any(i.ospf for i in self.ifaces)

    @property
    def bgp(self) -> bool:
        return bool(self.neighbors)

    def iface(self, name: str) -> Iface:
        return next(i for i in self.ifaces if i.name == name)


@dataclass
class Host:
    name: str
    subnet: str
    ip: IPv4Interface
    gateway: IPv4Address
    router: str
    router_iface: str
    services: list[str]
    routes: list[str]  # prefixes routed via the LAN gateway (eth0 stays the management network)


@dataclass
class AclRule:
    index: int
    name: str
    src: str
    dst: str
    src_net: IPv4Network
    dst_net: IPv4Network
    proto: str
    port: int | None
    action: str
    router: str  # enforcing router = gateway of the src LAN
    iface: str  # ...on that LAN's interface

    @property
    def comment(self) -> str:
        return f"acl:{self.name}"

    def matches(self, src_ip: IPv4Address, dst_ip: IPv4Address, proto: str, port: int | None) -> bool:
        if src_ip not in self.src_net or dst_ip not in self.dst_net:
            return False
        if self.proto == "any":
            return True
        if self.proto != proto:
            return False
        return self.port is None or self.port == port


@dataclass(frozen=True)
class Probe:
    src: str
    dst: str
    dst_ip: str
    proto: str  # icmp | tcp
    port: int | None
    expect: str  # allow | deny
    rule: str | None  # first-matching ACL rule (None = default permit)
    router: str | None  # where that rule is enforced (where its counter must move)
    origin: str  # "matrix" (derived) | "declared" (written in the source of truth)

    @property
    def id(self) -> str:
        what = f"tcp/{self.port}" if self.proto == "tcp" else self.proto
        return f"{self.src} -> {self.dst} {what}"


def net(prefix: str) -> IPv4Network:
    return ipaddress.ip_network(prefix, strict=True)


class Net:
    def __init__(self, data: dict):
        self.data = data
        self.name: str = data["lab"]["name"]
        self.images: dict = data["lab"]["images"]
        self.mgmt_subnet = net(data["lab"]["mgmt_subnet"])
        self.sites: dict = data["sites"]
        self.subnets: dict[str, dict] = {
            n: {"prefix": net(s["prefix"]), "kind": s["kind"], "site": s.get("site")} for n, s in data["subnets"].items()
        }
        self.routers: dict[str, Router] = {}
        for rname, r in data["routers"].items():
            site = self.sites[r["site"]]
            ifaces = []
            for ifname, i in sorted(r["interfaces"].items(), key=lambda kv: _ifnum(kv[0])):
                plen = self.subnets[i["subnet"]]["prefix"].prefixlen
                ifaces.append(Iface(rname, ifname, i["subnet"], IPv4Interface(f"{i['ip']}/{plen}"), i.get("ospf")))
            self.routers[rname] = Router(
                name=rname,
                site=r["site"],
                asn=site["asn"],
                role=site["role"],
                loopback=IPv4Address(r["loopback"]),
                ifaces=ifaces,
                redistribute_bgp=bool((r.get("ospf") or {}).get("redistribute_bgp")),
            )
        self.hosts: dict[str, Host] = {}
        site_blocks = sorted({s["block"] for s in self.sites.values()}, key=lambda p: net(p))
        for hname, h in data.get("hosts", {}).items():
            gw = self.gateway(h["subnet"])
            plen = self.subnets[h["subnet"]]["prefix"].prefixlen
            self.hosts[hname] = Host(
                name=hname,
                subnet=h["subnet"],
                ip=IPv4Interface(f"{h['ip']}/{plen}"),
                gateway=gw.ip.ip,
                router=gw.router,
                router_iface=gw.name,
                services=list(h.get("services", [])),
                routes=site_blocks,
            )
        self._build_bgp()
        self.acl: list[AclRule] = self._build_acl()
        self.probes: list[Probe] = self._build_probes()

    # ---- addressing ---------------------------------------------------------------------
    def ifaces_in(self, subnet: str) -> list[Iface]:
        return [i for r in self.routers.values() for i in r.ifaces if i.subnet == subnet]

    def gateway(self, subnet: str) -> Iface:
        gws = self.ifaces_in(subnet)
        if len(gws) != 1:
            raise ValueError(f"LAN {subnet} must have exactly one router interface, has {len(gws)}")
        return gws[0]

    def lans(self) -> list[str]:
        return sorted((n for n, s in self.subnets.items() if s["kind"] == "lan"), key=lambda n: self.subnets[n]["prefix"])

    def site_lans(self, site: str) -> list[str]:
        return [n for n in self.lans() if self.subnets[n]["site"] == site]

    def prefixes(self, subnet_names: list[str]) -> list[str]:
        return [str(self.subnets[n]["prefix"]) for n in subnet_names]

    def links(self) -> list[tuple[str, str]]:
        """containerlab links as ("node:iface", "node:iface"), deterministic order."""
        out = []
        for name, s in sorted(self.subnets.items(), key=lambda kv: kv[1]["prefix"]):
            ifs = sorted(self.ifaces_in(name), key=lambda i: (i.router, i.name))
            if s["kind"] == "p2p" and len(ifs) == 2:
                out.append((f"{ifs[0].router}:{ifs[0].name}", f"{ifs[1].router}:{ifs[1].name}"))
            elif s["kind"] == "lan" and len(ifs) == 1:
                for h in sorted(self.hosts.values(), key=lambda h: h.name):
                    if h.subnet == name:
                        out.append((f"{ifs[0].router}:{ifs[0].name}", f"{h.name}:eth1"))
        return out

    # ---- routing intent -----------------------------------------------------------------
    def shared_p2p(self, a: str, b: str) -> tuple[Iface, Iface] | None:
        for name, s in self.subnets.items():
            if s["kind"] != "p2p":
                continue
            ifs = {i.router: i for i in self.ifaces_in(name)}
            if a in ifs and b in ifs:
                return ifs[a], ifs[b]
        return None

    def _build_bgp(self) -> None:
        for a, b in self.data.get("bgp_sessions", []):
            pair = self.shared_p2p(a, b)
            if pair is None:
                raise ValueError(f"BGP session {a}-{b}: no shared point-to-point subnet")
            for local, remote in ((pair[0], pair[1]), (pair[1], pair[0])):
                lr, rr = self.routers[local.router], self.routers[remote.router]
                if lr.role == "hub":  # hub <-> spoke
                    imp = self.site_lans(rr.site)
                    exp = [n for n in self.lans() if self.subnets[n]["site"] != rr.site]
                else:
                    imp = [n for n in self.lans() if self.subnets[n]["site"] != lr.site]
                    exp = self.site_lans(lr.site)
                lr.neighbors.append(
                    Neighbor(
                        peer=rr.name,
                        peer_ip=str(remote.ip.ip),
                        peer_asn=rr.asn,
                        local_iface=local.name,
                        import_prefixes=self.prefixes(imp),
                        export_prefixes=self.prefixes(exp),
                    )
                )
        for r in self.routers.values():
            r.neighbors.sort(key=lambda n: IPv4Address(n.peer_ip))
            if r.bgp:
                r.bgp_networks = self.prefixes(self.site_lans(r.site))
                if r.redistribute_bgp:
                    r.ospf_redistribute_prefixes = self.prefixes([n for n in self.lans() if self.subnets[n]["site"] != r.site])

    def expected_routes(self, rname: str) -> dict[str, str]:
        """prefix -> protocol the RIB must show as the selected route on router rname."""
        r = self.routers[rname]
        site_redistributes = any(x.site == r.site and x.bgp and x.ospf and x.redistribute_bgp for x in self.routers.values())
        out: dict[str, str] = {}
        for lan in self.lans():
            prefix = str(self.subnets[lan]["prefix"])
            gw = self.gateway(lan)
            if gw.router == rname:
                out[prefix] = "connected"
            elif self.subnets[lan]["site"] == r.site:
                if r.ospf and gw.ospf and self.routers[gw.router].ospf:
                    out[prefix] = "ospf"
            elif r.bgp:
                out[prefix] = "bgp"
            elif r.ospf and site_redistributes:
                out[prefix] = "ospf"
        if r.ospf:
            for x in self.routers.values():
                if x.name != rname and x.site == r.site and x.ospf:
                    out[f"{x.loopback}/32"] = "ospf"
        return out

    def expected_bgp(self, rname: str) -> dict[str, int]:
        return {n.peer_ip: n.peer_asn for n in self.routers[rname].neighbors}

    def expected_ospf(self, rname: str) -> dict[str, str]:
        """OSPF neighbor router-id -> local interface, for every active p2p adjacency."""
        out = {}
        r = self.routers[rname]
        for i in r.ifaces:
            if i.ospf != "active" or self.subnets[i.subnet]["kind"] != "p2p":
                continue
            for peer in self.ifaces_in(i.subnet):
                if peer.router != rname and peer.ospf == "active":
                    out[str(self.routers[peer.router].loopback)] = i.name
        return out

    # ---- ACL intent ---------------------------------------------------------------------
    def _resolve(self, ref: str) -> IPv4Network:
        return ANY_NET if ref == "any" else self.subnets[ref]["prefix"]

    def _build_acl(self) -> list[AclRule]:
        rules = []
        for idx, rule in enumerate(self.data.get("acl", {}).get("rules", [])):
            gw = self.gateway(rule["src"])
            rules.append(
                AclRule(
                    index=idx,
                    name=rule["name"],
                    src=rule["src"],
                    dst=rule["dst"],
                    src_net=self._resolve(rule["src"]),
                    dst_net=self._resolve(rule["dst"]),
                    proto=rule["proto"],
                    port=rule.get("port"),
                    action=rule["action"],
                    router=gw.router,
                    iface=gw.name,
                )
            )
        return rules

    def acl_rules_for(self, rname: str) -> list[AclRule]:
        return [r for r in self.acl if r.router == rname]

    def acl_verdict(self, src_ip, dst_ip, proto: str, port: int | None = None) -> tuple[str, AclRule | None]:
        """First match wins; default permit. Returns ("allow"|"deny", matching rule or None)."""
        s, d = IPv4Address(str(src_ip)), IPv4Address(str(dst_ip))
        for rule in self.acl:
            if rule.matches(s, d, proto, port):
                return ("allow" if rule.action == "permit" else "deny"), rule
        return "allow", None

    def _probe(self, src: str, dst: str, proto: str, port: int | None, expect: str | None, origin: str) -> Probe:
        verdict, rule = self.acl_verdict(self.hosts[src].ip.ip, self.hosts[dst].ip.ip, proto, port)
        return Probe(
            src=src,
            dst=dst,
            dst_ip=str(self.hosts[dst].ip.ip),
            proto=proto,
            port=port,
            expect=expect or verdict,
            rule=rule.name if rule else None,
            router=rule.router if rule else None,
            origin=origin,
        )

    def _build_probes(self) -> list[Probe]:
        declared = {}
        for p in self.data.get("probes", []) or []:
            key = (p["from"], p["to"], p["proto"], p.get("port"))
            declared[key] = self._probe(p["from"], p["to"], p["proto"], p.get("port"), p["expect"], "declared")
        out = []
        names = sorted(self.hosts)
        for a in names:
            for b in names:
                if a != b and (a, b, "icmp", None) not in declared:
                    out.append(self._probe(a, b, "icmp", None, None, "matrix"))
        return out + list(declared.values())


def _ifnum(name: str) -> int:
    return int(name[3:]) if name.startswith("eth") and name[3:].isdigit() else 0
