"""Parsers/evaluators against FRR-shaped output, including outputs that MUST fail."""

import copy
import json

import pytest

from netaac import checks as C
from netaac.checks import FAIL, PASS, SKIP

FIX = __import__("pathlib").Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIX / name).read_text()


def statuses(cs):
    return [c.status for c in cs]


# ---- BGP ----------------------------------------------------------------------------------
def test_bgp_established_with_policy_counts(net):
    cs = C.bgp_checks(net, "br1-r1", fixture("br1-r1.bgp-summary.json"))
    assert statuses(cs) == [PASS, PASS]


@pytest.mark.parametrize(
    "mutate, category",
    [
        (lambda p: p.update(state="Active"), "bgp-session"),
        (lambda p: p.update(remoteAs=65099), "bgp-session"),
        (lambda p: p.update(pfxRcd=4), "bgp-policy"),  # import filter leaks one prefix
        (lambda p: p.update(pfxSnt=2), "bgp-policy"),  # spoke advertising more than its own LAN
    ],
)
def test_bgp_failures(net, mutate, category):
    data = json.loads(fixture("br1-r1.bgp-summary.json"))
    mutate(data["ipv4Unicast"]["peers"]["172.16.0.1"])
    cs = C.bgp_checks(net, "br1-r1", data)
    assert FAIL in [c.status for c in cs if c.category == category]


def test_bgp_peer_missing(net):
    cs = C.bgp_checks(net, "br1-r1", {"peers": {}})
    assert statuses(cs) == [FAIL]


def test_bgp_counts_absent_is_skip_not_pass(net):
    data = json.loads(fixture("br1-r1.bgp-summary.json"))
    for k in ("pfxRcd", "pfxSnt"):
        del data["ipv4Unicast"]["peers"]["172.16.0.1"][k]
    assert statuses(C.bgp_checks(net, "br1-r1", data)) == [PASS, SKIP]


def test_bgp_garbage_output_fails(net):
    assert statuses(C.bgp_checks(net, "br1-r1", "% Unknown command")) == [FAIL]


# ---- OSPF ---------------------------------------------------------------------------------
def test_ospf_full(net):
    assert statuses(C.ospf_checks(net, "hq-r1", fixture("hq-r1.ospf-neighbor.json"))) == [PASS]


@pytest.mark.parametrize("state", ["2-Way/DROther", "ExStart/-", "Init/-"])
def test_ospf_not_full(net, state):
    data = json.loads(fixture("hq-r1.ospf-neighbor.json"))
    entry = data["neighbors"]["10.255.0.2"][0]
    entry["nbrState"] = state
    del entry["converged"]
    assert statuses(C.ospf_checks(net, "hq-r1", data)) == [FAIL]


def test_ospf_legacy_state_key(net):
    data = {"neighbors": {"10.255.0.2": [{"state": "Full/DR"}]}}
    assert statuses(C.ospf_checks(net, "hq-r1", data)) == [PASS]


def test_ospf_neighbor_absent(net):
    assert statuses(C.ospf_checks(net, "hq-r1", {"neighbors": {}})) == [FAIL]


# ---- RIB ----------------------------------------------------------------------------------
def test_routes_match_intent(net):
    cs = C.route_checks(net, "br1-r1", fixture("br1-r1.ip-route.json"))
    assert statuses(cs) == [PASS] * 4


def test_route_missing(net):
    data = json.loads(fixture("br1-r1.ip-route.json"))
    del data["10.30.99.0/24"]
    cs = {c.subject: c.status for c in C.route_checks(net, "br1-r1", data)}
    assert cs["br1-r1 10.30.99.0/24"] == FAIL


def test_route_wrong_protocol(net):
    data = json.loads(fixture("br1-r1.ip-route.json"))
    data["10.10.10.0/24"][0]["protocol"] = "static"  # someone pinned it by hand
    cs = {c.subject: c for c in C.route_checks(net, "br1-r1", data)}
    assert cs["br1-r1 10.10.10.0/24"].status == FAIL and cs["br1-r1 10.10.10.0/24"].observed == "static"


def test_route_present_but_not_selected_or_installed(net):
    data = json.loads(fixture("br1-r1.ip-route.json"))
    unsel, uninst = copy.deepcopy(data), copy.deepcopy(data)
    unsel["10.30.10.0/24"][0]["selected"] = False
    uninst["10.30.10.0/24"][0]["installed"] = False
    for d in (unsel, uninst):
        cs = {c.subject: c.status for c in C.route_checks(net, "br1-r1", d)}
        assert cs["br1-r1 10.30.10.0/24"] == FAIL


# ---- ACL ----------------------------------------------------------------------------------
def test_acl_counters_parse_quoted_and_unquoted():
    counters = C.acl_counters(fixture("br2-r1.iptables-save.txt"))
    assert counters == {
        "net08:stateful-return": 18,
        "acl:guest-no-hq-servers": 3,
        "acl:guest-no-br1-users": 3,
        "acl:guest-no-br2-users": 0,
    }


def test_acl_installed_in_intent_order(net):
    assert statuses(C.acl_installed_checks(net, "br2-r1", fixture("br2-r1.iptables-save.txt"))) == [PASS]


def test_acl_wrong_order_fails(net):
    text = fixture("br2-r1.iptables-save.txt").splitlines()
    i, j = 8, 9
    text[i], text[j] = text[j], text[i]
    assert statuses(C.acl_installed_checks(net, "br2-r1", "\n".join(text))) == [FAIL]


def test_acl_without_forward_jump_fails(net):
    text = fixture("br2-r1.iptables-save.txt").replace("[40:3360] -A FORWARD -j NET08-ACL\n", "")
    assert statuses(C.acl_installed_checks(net, "br2-r1", text)) == [FAIL]


def test_acl_rule_missing_fails(net):
    text = "\n".join(ln for ln in fixture("br2-r1.iptables-save.txt").splitlines() if "guest-no-br1-users" not in ln)
    assert statuses(C.acl_installed_checks(net, "br2-r1", text)) == [FAIL]


# ---- probes: the four ways a reachability result can be wrong ------------------------------
def probe(net, pid):
    return next(p for p in net.probes if p.id == pid)


def test_allow_reached_passes(net):
    assert C.probe_check(probe(net, "br1-pc1 -> hq-srv1 icmp"), True, None).status == PASS


def test_unreachable_but_should_reach_fails(net):
    c = C.probe_check(probe(net, "br1-pc1 -> hq-srv1 icmp"), False, None)
    assert c.status == FAIL and "reachable" in c.detail


def test_reachable_but_should_be_denied_fails(net):
    c = C.probe_check(probe(net, "br2-guest1 -> hq-srv1 icmp"), True, 0)
    assert c.status == FAIL and "intent says deny" in c.detail


def test_denied_but_not_by_the_acl_fails(net):
    """Blocked, but the rule's counter did not move: a broken path is not a working policy."""
    c = C.probe_check(probe(net, "br2-guest1 -> hq-srv1 icmp"), False, 0)
    assert c.status == FAIL and "broken path" in c.detail
    assert C.probe_check(probe(net, "br2-guest1 -> hq-srv1 icmp"), False, None).status == FAIL


def test_denied_by_the_acl_passes(net):
    p = probe(net, "br1-pc1 -> hq-srv1 tcp/22")
    assert p.rule == "br1-no-ssh-servers" and p.router == "br1-r1"
    assert C.probe_check(p, False, 2).status == PASS
