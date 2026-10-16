"""Validator: the real intent passes clean; each broken variant is rejected for the RIGHT reason."""

import pytest

from netaac.validate import errors, validate, validate_text


def codes(data):
    return {f.code for f in errors(validate(data))}


def test_real_source_of_truth_is_clean(data):
    findings = validate(data)
    assert findings == [], findings


# ---- layer 1: schema ----------------------------------------------------------------------
def test_schema_rejects_typo_key(data):
    data["routers"]["br1-r1"]["interfaces"]["eth2"]["subent"] = "x"
    assert codes(data) == {"SCHEMA"}


def test_schema_rejects_missing_section(data):
    del data["acl"]
    assert codes(data) == {"SCHEMA"}


def test_schema_rejects_floating_image_tag(data):
    data["lab"]["images"]["router"] = "quay.io/frrouting/frr:latest"
    assert "SCHEMA" in codes(data)


def test_schema_rejects_bad_ip_text(data):
    data["hosts"]["br1-pc1"]["ip"] = "10.20.10.300"
    assert codes(data) == {"SCHEMA"}


def test_duplicate_yaml_key_is_not_silently_merged(lab_dir):
    text = (lab_dir / "intent" / "network.yaml").read_text()
    text = text.replace("  br1-r1:\n", "  br1-r1:\n    site: br1\n    loopback: 10.255.0.99\n    interfaces: {}\n  br1-r1:\n", 1)
    assert {f.code for f in validate_text(text)} == {"DUPLICATE_KEY"}


# ---- layer 2/3: addressing ----------------------------------------------------------------
def test_prefix_with_host_bits(data):
    data["subnets"]["br1-users"]["prefix"] = "10.20.10.5/24"
    assert codes(data) == {"PREFIX_NOT_NETWORK"}


def test_overlapping_subnets_rejected(data):
    data["subnets"]["br2-guest"]["prefix"] = "10.30.10.128/25"  # inside br2-users
    data["routers"]["br2-r1"]["interfaces"]["eth3"]["ip"] = "10.30.10.129"
    data["hosts"]["br2-guest1"]["ip"] = "10.30.10.130"
    c = codes(data)
    assert "SUBNET_OVERLAP" in c
    assert "IFACE_MULTI_SUBNET" in c  # the gateway now sits in two subnets


def test_overlap_with_management_network(data):
    data["subnets"]["wan-hq-br2"]["prefix"] = "172.20.80.4/30"
    assert "MGMT_OVERLAP" in codes(data)


def test_duplicate_ip_host_equals_gateway(data):
    data["hosts"]["br1-pc1"]["ip"] = "10.20.10.1"
    assert "DUPLICATE_IP" in codes(data)


def test_duplicate_loopback(data):
    data["routers"]["br2-r1"]["loopback"] = data["routers"]["br1-r1"]["loopback"]
    assert "DUPLICATE_IP" in codes(data)


def test_interface_outside_its_subnet(data):
    data["routers"]["br1-r1"]["interfaces"]["eth2"]["ip"] = "10.30.10.77"  # br2-users space
    c = codes(data)
    assert "IFACE_NOT_IN_SUBNET" in c


@pytest.mark.parametrize("ip", ["10.20.10.0", "10.20.10.255"])
def test_network_and_broadcast_addresses(data, ip):
    data["hosts"]["br1-pc1"]["ip"] = ip
    assert "RESERVED_ADDRESS" in codes(data)


def test_subnet_outside_site_block(data):
    data["subnets"]["br1-users"]["prefix"] = "10.21.10.0/24"
    data["routers"]["br1-r1"]["interfaces"]["eth2"]["ip"] = "10.21.10.1"
    data["hosts"]["br1-pc1"]["ip"] = "10.21.10.10"
    assert codes(data) == {"OUTSIDE_BLOCK"}


def test_site_blocks_overlap(data):
    data["sites"]["br2"]["block"] = "10.16.0.0/12"  # swallows br1's 10.20/16
    assert "SITE_BLOCK_OVERLAP" in codes(data)


# ---- layer 4: topology + routing ----------------------------------------------------------
def test_p2p_link_with_one_end(data):
    del data["routers"]["br1-r1"]["interfaces"]["eth1"]
    c = codes(data)
    assert "P2P_ENDPOINTS" in c and "BGP_NO_SHARED_LINK" in c


def test_lan_with_two_gateways(data):
    data["routers"]["br1-r1"]["interfaces"]["eth3"] = {"subnet": "br2-users", "ip": "10.30.10.2"}
    c = codes(data)
    assert "LAN_GATEWAY" in c and "SITE_MISMATCH" in c


def test_two_hosts_on_one_lan(data):
    data["hosts"]["br1-pc2"] = {"subnet": "br1-users", "ip": "10.20.10.11"}
    assert codes(data) == {"LAN_HOSTS"}


def test_duplicate_asn(data):
    data["sites"]["br2"]["asn"] = data["sites"]["br1"]["asn"]
    assert "DUPLICATE_ASN" in codes(data)


def test_bgp_session_listed_twice(data):
    data["bgp_sessions"].append(["br1-r1", "hq-r2"])
    assert codes(data) == {"BGP_DUP_SESSION"}


def test_ospf_one_sided(data):
    del data["routers"]["hq-r2"]["interfaces"]["eth1"]["ospf"]
    assert "OSPF_ONE_SIDED" in codes(data)


def test_missing_bgp_session_leaves_a_route_gap(data):
    data["bgp_sessions"] = [["hq-r2", "br1-r1"]]
    c = codes(data)
    assert c == {"ROUTE_GAP"}


# ---- ACL intent sanity ----------------------------------------------------------------------
def test_acl_rule_shadowed_by_earlier_rule(data):
    data["acl"]["rules"].insert(1, {"name": "guest-web", "src": "br2-guest", "dst": "hq-servers", "proto": "tcp", "port": 80, "action": "permit"})
    assert codes(data) == {"ACL_SHADOWED"}


def test_acl_src_must_be_a_lan(data):
    data["acl"]["rules"].append({"name": "wan-x", "src": "wan-hq-br1", "dst": "hq-servers", "proto": "any", "action": "deny"})
    assert codes(data) == {"ACL_SRC_NOT_LAN"}


def test_acl_same_subnet_never_matches(data):
    data["acl"]["rules"].append({"name": "self", "src": "br1-users", "dst": "br1-users", "proto": "any", "action": "deny"})
    assert codes(data) == {"ACL_SAME_SUBNET"}


def test_acl_port_without_tcp_udp(data):
    data["acl"]["rules"][0]["port"] = 22
    assert codes(data) == {"ACL_PORT_PROTO"}


def test_acl_duplicate_name(data):
    data["acl"]["rules"][1]["name"] = data["acl"]["rules"][0]["name"]
    assert codes(data) == {"ACL_DUP_NAME"}


def test_acl_deny_without_any_probe_is_flagged(data):
    data["acl"]["rules"].append({"name": "br2-no-telnet", "src": "br2-users", "dst": "hq-servers", "proto": "tcp", "port": 23, "action": "deny"})
    findings = validate(data)
    assert not errors(findings)
    assert ("ACL_UNTESTED", "warning") in {(f.code, f.severity) for f in findings}


# ---- declared probes vs ACL ------------------------------------------------------------------
def test_probe_contradicting_acl_is_rejected(data):
    # "reachable but should be denied" caught at intent level: the probe says allow, the ACL says deny
    data["probes"].append({"from": "br2-guest1", "to": "br1-pc1", "proto": "icmp", "expect": "allow"})
    assert codes(data) == {"PROBE_CONTRADICTS_ACL"}


def test_probe_expecting_deny_that_acl_allows(data):
    data["probes"].append({"from": "br1-pc1", "to": "br2-pc1", "proto": "icmp", "expect": "deny"})
    assert codes(data) == {"PROBE_CONTRADICTS_ACL"}


def test_tcp_allow_probe_needs_a_listener(data):
    data["probes"].append({"from": "br2-pc1", "to": "hq-srv1", "proto": "tcp", "port": 443, "expect": "allow"})
    assert codes(data) == {"PROBE_NO_LISTENER"}
