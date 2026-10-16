"""Render intent -> FRR configs, ACL rule files, the containerlab topology and a plan file.

The generator REFUSES to render intent that fails validation: there is no path from an
invalid source of truth to a config file, so there is no path to a router either.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .model import Net
from .validate import errors, validate

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


class InvalidIntent(Exception):
    def __init__(self, findings):
        self.findings = findings
        super().__init__(f"{len(findings)} validation error(s): " + "; ".join(f"{f.code} {f.where}" for f in findings))


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=False,  # noqa: S701 - router configs, not HTML
    )


def _descriptions(net: Net, rname: str) -> dict[str, str]:
    out = {}
    for i in net.routers[rname].ifaces:
        sub = net.subnets[i.subnet]
        if sub["kind"] == "p2p":
            peer = next(p for p in net.ifaces_in(i.subnet) if p.router != rname)
            out[i.name] = f"to {peer.router} {peer.name} ({i.subnet})"
        else:
            out[i.name] = f"LAN {i.subnet}"
    return out


def _policy(net: Net, rname: str) -> tuple[list[dict], list[dict]]:
    r = net.routers[rname]
    prefix_lists, route_maps = [], []
    for n in r.neighbors:
        for direction, prefixes in (("IN", n.import_prefixes), ("OUT", n.export_prefixes)):
            pl = f"PL-{n.tag}-{direction}"
            prefix_lists.append({"name": pl, "prefixes": prefixes})
            route_maps.append({"name": f"RM-{n.tag}-{direction}", "prefix_list": pl})
    if r.ospf and r.ospf_redistribute_prefixes:
        prefix_lists.append({"name": "PL-BGP-TO-OSPF", "prefixes": r.ospf_redistribute_prefixes})
        route_maps.append({"name": "RM-BGP-TO-OSPF", "prefix_list": "PL-BGP-TO-OSPF"})
    return prefix_lists, route_maps


def render(data: dict) -> dict[str, str]:
    """Return {relative path: file content}. Pure: no filesystem writes."""
    errs = errors(validate(data))
    if errs:
        raise InvalidIntent(errs)
    net = Net(data)
    env = _env()
    files: dict[str, str] = {}
    for rname in sorted(net.routers):
        r = net.routers[rname]
        prefix_lists, route_maps = _policy(net, rname)
        files[f"configs/{rname}/frr.conf"] = env.get_template("frr.conf.j2").render(
            r=r, prefix_lists=prefix_lists, route_maps=route_maps, descriptions=_descriptions(net, rname)
        )
        files[f"configs/{rname}/daemons"] = env.get_template("daemons.j2").render(r=r)
        files[f"configs/{rname}/vtysh.conf"] = env.get_template("vtysh.conf.j2").render(r=r)
        files[f"acl/{rname}.rules"] = env.get_template("acl.rules.j2").render(r=r, rules=net.acl_rules_for(rname))
    files["topology.clab.yml"] = env.get_template("topology.clab.yml.j2").render(
        net=net,
        routers=[net.routers[n] for n in sorted(net.routers)],
        hosts=[net.hosts[n] for n in sorted(net.hosts)],
        links=net.links(),
    )
    files["plan.json"] = json.dumps(plan(net), indent=2, sort_keys=True) + "\n"
    return files


def plan(net: Net) -> dict:
    """What the tests will hold the live network to - human-readable, committed in rendered/."""
    return {
        "lab": net.name,
        "images": net.images,
        "links": [list(link) for link in net.links()],
        "routers": {
            rname: {
                "site": r.site,
                "asn": r.asn,
                "router_id": str(r.loopback),
                "expected_routes": net.expected_routes(rname),
                "expected_bgp_peers": net.expected_bgp(rname),
                "expected_ospf_neighbors": net.expected_ospf(rname),
                "acl_rules": [a.name for a in net.acl_rules_for(rname)],
            }
            for rname, r in sorted(net.routers.items())
        },
        "hosts": {
            h.name: {"ip": str(h.ip), "gateway": str(h.gateway), "routes": h.routes, "services": h.services}
            for h in sorted(net.hosts.values(), key=lambda h: h.name)
        },
        "probes": [
            {
                "id": p.id,
                "expect": p.expect,
                "rule": p.rule,
                "enforced_on": p.router,
                "origin": p.origin,
            }
            for p in net.probes
        ],
    }


def write(files: dict[str, str], out: Path) -> None:
    out = Path(out)
    for sub in ("configs", "acl"):
        if (out / sub).exists():
            shutil.rmtree(out / sub)  # stale routers from an older intent must not survive
    for rel, content in sorted(files.items()):
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
