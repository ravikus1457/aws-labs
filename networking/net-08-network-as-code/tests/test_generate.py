"""Generator output: deterministic, matches the committed snapshot, and encodes the intent."""

import re

import pytest

from netaac import generate
from netaac.__main__ import main


def test_render_is_deterministic(data):
    assert generate.render(data) == generate.render(data)


def test_committed_snapshot_is_current(tmp_path):
    """rendered/ is what a reviewer reads in the PR diff; it must equal generator output."""
    assert main(["check-rendered", "--evidence", str(tmp_path)]) == 0


def test_snapshot_check_detects_a_hand_edit(lab_dir, tmp_path):
    import shutil

    copy = tmp_path / "rendered"
    shutil.copytree(lab_dir / "rendered", copy)
    conf = copy / "configs" / "br1-r1" / "frr.conf"
    conf.write_text(conf.read_text().replace("timers 3 9", "timers 1 3"))
    assert main(["check-rendered", "--rendered", str(copy), "--evidence", str(tmp_path / "ev")]) == 1


def test_invalid_intent_is_never_rendered(data):
    data["hosts"]["br1-pc1"]["ip"] = "10.20.10.1"  # duplicate of the gateway
    with pytest.raises(generate.InvalidIntent):
        generate.render(data)


def test_generate_cli_refuses_invalid_intent(tmp_path, data):
    import yaml

    data["subnets"]["br2-guest"]["prefix"] = "10.30.10.0/25"
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(data))
    assert main(["generate", "--intent", str(bad), "--out", str(tmp_path / "out")]) == 2
    assert not (tmp_path / "out").exists()


def test_no_undefined_or_none_leaks(files):
    for path, text in files.items():
        assert "None" not in text, path


def test_every_ebgp_neighbor_has_policy_both_ways(net, files):
    for rname, r in net.routers.items():
        conf = files[f"configs/{rname}/frr.conf"]
        for n in r.neighbors:
            for d in ("IN", "OUT"):
                assert f"neighbor {n.peer_ip} route-map RM-{n.tag}-{d} {d.lower()}" in conf
                assert f"route-map RM-{n.tag}-{d} permit 10" in conf
                assert f"ip prefix-list PL-{n.tag}-{d} seq 5 " in conf


def test_spoke_exports_only_its_own_lans(files):
    conf = files["configs/br2-r1/frr.conf"]
    out = re.findall(r"ip prefix-list PL-HQ-R2-OUT seq \d+ permit (\S+)", conf)
    assert out == ["10.30.10.0/24", "10.30.99.0/24"]


def test_hub_redistributes_remote_lans_into_ospf(files):
    conf = files["configs/hq-r2/frr.conf"]
    assert "redistribute bgp route-map RM-BGP-TO-OSPF" in conf
    assert re.findall(r"PL-BGP-TO-OSPF seq \d+ permit (\S+)", conf) == ["10.20.10.0/24", "10.30.10.0/24", "10.30.99.0/24"]
    assert "router ospf" not in files["configs/br1-r1/frr.conf"]


def test_acl_is_enforced_on_the_source_gateway_interface(files):
    br2 = files["acl/br2-r1.rules"]
    assert '-A NET08-ACL -i eth3 -s 10.30.99.0/24 -d 10.10.10.0/24 -m comment --comment "acl:guest-no-hq-servers" -j DROP' in br2
    br1 = files["acl/br1-r1.rules"]
    assert "-i eth2 -s 10.20.10.0/24 -d 10.10.10.0/24 -p tcp -m tcp --dport 22" in br1
    assert "acl:" not in files["acl/hq-r1.rules"]
    for path in (p for p in files if p.startswith("acl/")):
        lines = [ln for ln in files[path].splitlines() if ln.startswith("-A NET08-ACL")]
        assert "--ctstate RELATED,ESTABLISHED" in lines[0], "stateful return must be the first rule"


def test_topology_links_cover_every_interface(net, files):
    topo = files["topology.clab.yml"]
    for r in net.routers.values():
        for i in r.ifaces:
            assert f'"{r.name}:{i.name}"' in topo
    for h in net.hosts:
        assert f'"{h}:eth1"' in topo
    assert topo.count("- endpoints:") == 7


def test_daemons_enable_only_what_intent_uses(files):
    assert "bgpd=yes" in files["configs/br1-r1/daemons"] and "ospfd=no" in files["configs/br1-r1/daemons"]
    assert "bgpd=no" in files["configs/hq-r1/daemons"] and "ospfd=yes" in files["configs/hq-r1/daemons"]
