"""Static checks on the lab definition itself: the things that break a deploy (or silently change what
is measured) before any container starts."""

import os
import re

import yaml
from conftest import LAB

TOPO = yaml.safe_load(open(os.path.join(LAB, "topology.clab.yml")))
NODES = TOPO["topology"]["nodes"]
LINKS = [tuple(ep.split(":") for ep in link["endpoints"]) for link in TOPO["topology"]["links"]]


def _frr_nodes():
    for name, spec in NODES.items():
        for b in spec.get("binds") or []:
            src, dst = b.split(":")[:2]
            if dst == "/etc/frr/frr.conf":
                yield name, os.path.join(LAB, src)


def test_every_image_is_pinned():
    for name, spec in NODES.items():
        image = spec["image"]
        repo, _, tag = image.rpartition(":")
        assert repo and tag and tag != "latest", f"{name}: image {image!r} is not pinned to a tag"


def test_every_bind_source_exists():
    for name, spec in NODES.items():
        for b in spec.get("binds") or []:
            src = b.split(":")[0]
            assert os.path.exists(os.path.join(LAB, src)), f"{name}: bind source {src} missing"


def test_links_reference_real_nodes_and_no_port_is_used_twice():
    seen = set()
    for a, b in LINKS:
        for node, ifname in (a, b):
            assert node in NODES, f"link endpoint {node}:{ifname} is not a node"
            assert (node, ifname) not in seen, f"{node}:{ifname} cabled twice"
            seen.add((node, ifname))


def test_router_configs_only_name_cabled_interfaces():
    cabled = {(n, i) for a, b in LINKS for n, i in (a, b)}
    for name, path in _frr_nodes():
        ifaces = re.findall(r"^interface (\S+)", open(path).read(), re.M)
        assert ifaces, f"{name}: no interfaces in {path}"
        for ifn in ifaces:
            base = ifn.split(".")[0]  # eth1.10 rides on eth1
            if ifn == "lo":
                continue
            assert (name, base) in cabled, f"{name}: frr.conf configures {ifn} but {name}:{base} is not cabled"


def test_daemons_enabled_for_every_protocol_used():
    need = {"router bgp": "bgpd", "router ospf": "ospfd", "\nbfd\n": "bfdd", "ip route ": "staticd"}
    for name, path in _frr_nodes():
        conf = open(path).read()
        daemons_bind = next(b for b in NODES[name]["binds"] if b.split(":")[1] == "/etc/frr/daemons")
        daemons = open(os.path.join(LAB, daemons_bind.split(":")[0])).read()
        for marker, daemon in need.items():
            if marker in conf:
                assert re.search(rf"^{daemon}=yes", daemons, re.M), f"{name} uses {marker.strip()} but {daemon} is off"
