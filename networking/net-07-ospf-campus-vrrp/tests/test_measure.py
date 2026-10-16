"""measure.py must PASS a good run and FAIL each bad one. Every 'bad' test starts from REAL evidence and
breaks exactly one thing the lab claims to prove; if a detector stops catching it, that test goes red."""

import json
import os
import subprocess
import sys

import pytest
from conftest import FIXTURES, LAB, drop_received, edit_poll, meta, status

import measure

BASELINE = os.path.join(FIXTURES, "baseline")
REAL = "real_master_kill"
VIP_ON = '"local":"10.10.10.1"'


def _j(d, name):
    return json.load(open(os.path.join(d, name)))


def _put(d, name, data):
    json.dump(data, open(os.path.join(d, name), "w"))


# --------------------------------------------------------------------------------------------
# readers on real output
# --------------------------------------------------------------------------------------------


def test_readers_on_real_output():
    assert measure.ospf_full(_j(BASELINE, "dc_ospf_nbr.json")) == {"10.255.0.1", "10.255.0.2"}
    assert measure.ipv4_addrs(_j(BASELINE, "core1_addr10.json")) == {"10.10.10.2", "10.10.10.1"}
    assert measure.ipv4_addrs(_j(BASELINE, "core2_addr10.json")) == {"10.10.10.3"}
    assert measure.neigh_mac(_j(BASELINE, "h10_neigh.json")) == measure.link_mac(_j(BASELINE, "core1_link10.json"))
    choices = measure.ecmp_choices(open(os.path.join(BASELINE, "dc_ecmp_flows.txt")).read())
    assert len(choices) == 16 and set(choices) == {"10.0.11.1", "10.0.12.1"}
    assert measure.route_nexthops(_j(BASELINE, "dc_route_summary.json"), "10.10.0.0/16") == frozenset(
        {"10.0.11.1", "10.0.12.1"})
    assert measure.parse_traceroute(" 1  10.10.10.3  0.1 ms\n 2  *\n 3  10.100.0.10  1 ms") == ["10.10.10.3", None, "10.100.0.10"]


def test_gap_and_failback_windows_are_exact():
    sent = [(i, 1000.0 + i * 0.01, "ok") for i in range(1500)]
    lost = set(range(500, 572)) | set(range(1200, 1203))  # 720 ms gap at the fault, 30 ms at failback
    recv = {i: (t, t) for i, t, _ in sent if i not in lost}
    r = measure.analyze_flow(sent, recv, t_fault=1004.99, t_restore=1010.0)
    assert r["outage"]["window_ms"] == pytest.approx(720.0)
    assert r["failback"]["window_ms"] == pytest.approx(30.0)
    assert r["outage"]["recovered"] and r["delivered_after_restore"] > 0


# --------------------------------------------------------------------------------------------
# baseline
# --------------------------------------------------------------------------------------------


def test_real_baseline_passes():
    r = measure.evaluate_baseline(BASELINE)
    assert [c for c in r["checks"] if c["status"] != "PASS"] == []
    assert len(r["checks"]) == 12


def test_summary_leak_fails(fixture_copy):
    d = fixture_copy("baseline")
    routes = _j(d, "dc_routes.json")
    routes["10.10.10.0/24"] = [{"prefix": "10.10.10.0/24", "protocol": "ospf", "selected": True}]
    _put(d, "dc_routes.json", routes)
    assert status(measure.evaluate_baseline(d), "baseline.area1_summarised") == "FAIL"


def test_single_path_instead_of_ecmp_fails(fixture_copy):
    d = fixture_copy("baseline")
    doc = _j(d, "dc_route_summary.json")
    for e in doc["10.10.0.0/16"]:
        e["nexthops"] = [nh for nh in e["nexthops"] if nh.get("ip") != "10.0.11.1"]
    _put(d, "dc_route_summary.json", doc)
    assert status(measure.evaluate_baseline(d), "baseline.ecmp_fib") == "FAIL"


def test_ecmp_in_fib_but_not_in_dataplane_fails(fixture_copy):
    d = fixture_copy("baseline")
    p = os.path.join(d, "dc_ecmp_flows.txt")
    open(p, "w").write(open(p).read().replace("via 10.0.11.1 dev eth1", "via 10.0.12.1 dev eth2"))
    assert status(measure.evaluate_baseline(d), "baseline.ecmp_dataplane") == "FAIL"


def test_vrrp_split_brain_fails(fixture_copy):
    d = fixture_copy("baseline")
    _put(d, "core2_addr10.json", _j(d, "core1_addr10.json"))  # both cores claim 10.10.10.1
    assert status(measure.evaluate_baseline(d), "baseline.vrrp_roles") == "FAIL"


def test_host_arp_pointing_at_the_backup_fails(fixture_copy):
    d = fixture_copy("baseline")
    _put(d, "core1_link10.json", _j(d, "core2_link10.json"))
    assert status(measure.evaluate_baseline(d), "baseline.gateway_arp.h10") == "FAIL"


def test_ospf_adjacency_not_full_fails(fixture_copy):
    d = fixture_copy("baseline")
    p = os.path.join(d, "core2_ospf_nbr.json")
    open(p, "w").write(open(p).read().replace('"Full', '"ExStart'))
    assert status(measure.evaluate_baseline(d), "baseline.ospf_full.core2") == "FAIL"


def test_wrong_first_hop_fails(fixture_copy):
    d = fixture_copy("baseline")
    p = os.path.join(d, "trace_h10_srv.txt")
    open(p, "w").write(open(p).read().replace("10.10.10.2", "10.10.10.3"))
    assert status(measure.evaluate_baseline(d), "baseline.first_hop.h10->srv") == "FAIL"


# --------------------------------------------------------------------------------------------
# the scenario
# --------------------------------------------------------------------------------------------


def test_real_scenario_passes_with_its_numbers():
    r = measure.evaluate_scenario(os.path.join(FIXTURES, REAL))
    assert [c for c in r["checks"] if c["status"] != "PASS"] == []
    sm = r["summary"]
    assert sm["h10_to_srv"]["outage"]["window_ms"] == 400.0
    assert sm["h20_to_srv"]["outage"]["window_ms"] == 0.0
    assert sm["vrrp_takeover"]["ms"] == 612.2


def test_real_scenario_fails_the_ci_limits_where_the_harness_lacks_carrier_loss(fixture_copy):
    """The fixture's relaxed limits are the only reason it passes: with the CI defaults the two checks that
    depend on dc seeing core1's link vanish must fail (OSPF dead interval 4 s decided them locally)."""
    d = fixture_copy(REAL)
    m = meta(d)
    m.pop("thresholds")
    _put(d, "meta.json", m)
    failed = {c["id"] for c in measure.evaluate_scenario(d)["checks"] if c["status"] == "FAIL"}
    assert failed == {"master_kill.srv_to_h10.gap_ms", "master_kill.ospf_reconverge_ms"}


def test_no_failover_fails(fixture_copy):
    d = fixture_copy(REAL)
    m = meta(d)
    drop_received(d, "h10_to_srv", m["t_fault"], m["t_restore"])
    r = measure.evaluate_scenario(d)
    assert status(r, "master_kill.h10_to_srv.failover") == "FAIL"
    assert status(r, "master_kill.h10_to_srv.gap_ms") == "FAIL"


def test_gap_above_threshold_fails(fixture_copy):
    d = fixture_copy(REAL)
    m = meta(d)
    drop_received(d, "h20_to_srv", m["t_fault"] + 0.1, m["t_fault"] + 1.6)  # 1.5 s on the control VLAN
    assert status(measure.evaluate_scenario(d), "master_kill.h20_to_srv.gap_ms") == "FAIL"


def test_vrrp_never_taking_over_fails(fixture_copy):
    d = fixture_copy(REAL)
    m = meta(d)
    edit_poll(d, "core2", m["t_fault"], lambda t: t.replace(VIP_ON, '"local":"10.10.10.99"'))
    r = measure.evaluate_scenario(d)
    assert status(r, "master_kill.vrrp_takeover_ms") == "FAIL"


def test_no_preemption_fails(fixture_copy):
    d = fixture_copy(REAL)
    m = meta(d)
    edit_poll(d, "core1", m["t_keepalived"], lambda t: t.replace(VIP_ON, '"local":"10.10.10.99"'))
    assert status(measure.evaluate_scenario(d), "master_kill.preemption") == "FAIL"


def test_preempting_before_routing_is_up_fails(fixture_copy):
    """keepalived without the track script (or without init_fail) grabs the gateway the moment it starts."""
    d = fixture_copy(REAL)
    m = meta(d)
    edit_poll(d, "core1", m["t_keepalived"] + 0.5, lambda t: t.replace('"local":"10.10.10.2"', VIP_ON))
    assert status(measure.evaluate_scenario(d), "master_kill.preempt_waits_for_routing") == "FAIL"


def test_untested_gate_fails(fixture_copy):
    """If the backbone came back before keepalived started, the gate was never exercised - not a PASS."""
    d = fixture_copy(REAL)
    m = meta(d)
    m["t_wired"] = m["t_keepalived"]
    _put(d, "meta.json", m)
    assert status(measure.evaluate_scenario(d), "master_kill.preempt_waits_for_routing") == "FAIL"


def test_core1_resurrected_by_docker_fails(fixture_copy):
    d = fixture_copy(REAL)
    m = meta(d)
    m["core1_running_before_restore"] = True
    _put(d, "meta.json", m)
    assert status(measure.evaluate_scenario(d), "master_kill.fault_effective") == "FAIL"


def test_host_still_pointing_at_dead_master_fails(fixture_copy):
    d = fixture_copy(REAL)
    _put(d, os.path.join("during", "h10_neigh.json"), _j(d, os.path.join("after", "h10_neigh.json")))
    assert status(measure.evaluate_scenario(d), "master_kill.gateway_arp_during") == "FAIL"


def test_ecmp_not_restored_fails(fixture_copy):
    d = fixture_copy(REAL)
    m = meta(d)
    edit_poll(d, "dc", m["t_restore"], lambda t: t.replace('"ip":"10.0.11.1"', '"ip":"10.0.12.1"'))
    assert status(measure.evaluate_scenario(d), "master_kill.ecmp_restored_ms") == "FAIL"


def test_failback_loss_fails(fixture_copy):
    d = fixture_copy(REAL)
    m = meta(d)
    drop_received(d, "srv_to_h10", m["t_restore"] + 12.0, m["t_restore"] + 14.0)
    assert status(measure.evaluate_scenario(d), "master_kill.srv_to_h10.failback_loss_ms") == "FAIL"


def test_incomplete_meta_fails(fixture_copy):
    d = fixture_copy(REAL)
    _put(d, "meta.json", {"scenario": "master_kill", "t_fault": 1.0})
    assert status(measure.evaluate_scenario(d), "master_kill.meta") == "FAIL"


# --------------------------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------------------------


def _evidence(tmp_path, fixture_copy, with_scenario=True):
    ev = tmp_path / "ev"
    ev.mkdir()
    os.rename(fixture_copy("baseline"), ev / "baseline")
    _put(str(ev / "baseline"), "result.json", measure.evaluate_baseline(str(ev / "baseline")))
    if with_scenario:
        os.rename(fixture_copy(REAL), ev / "master_kill")
        r = measure.evaluate_scenario(str(ev / "master_kill"))
        json.dump(r, open(ev / "master_kill" / "result.json", "w"), default=sorted)
    return str(ev)


def _report(ev):
    return subprocess.run([sys.executable, os.path.join(LAB, "measure.py"), "report", ev, os.path.join(ev, "report.md")],
                          capture_output=True, text=True)


def test_report_on_real_run_passes(tmp_path, fixture_copy):
    ev = _evidence(tmp_path, fixture_copy)
    r = _report(ev)
    assert r.returncode == 0, r.stdout
    md = open(os.path.join(ev, "report.md")).read()
    assert "**Verdict: PASS**" in md and "| 612.2 |" in md and "| 400.0 |" in md


def test_report_fails_when_the_scenario_is_missing(tmp_path, fixture_copy):
    ev = _evidence(tmp_path, fixture_copy, with_scenario=False)
    assert _report(ev).returncode == 1
    assert "**MISSING**" in open(os.path.join(ev, "report.md")).read()


def test_report_fails_on_any_failed_check(tmp_path, fixture_copy):
    ev = _evidence(tmp_path, fixture_copy)
    p = os.path.join(ev, "baseline", "result.json")
    res = json.load(open(p))
    res["checks"][-1]["status"] = "FAIL"
    json.dump(res, open(p, "w"))
    assert _report(ev).returncode == 1


# --------------------------------------------------------------------------------------------
# run.sh wait-loop conditions
# --------------------------------------------------------------------------------------------


def _cond(args, text):
    return subprocess.run([sys.executable, os.path.join(LAB, "measure.py"), "cond", *args], input=text,
                          capture_output=True, text=True).returncode


def test_cond_on_real_output():
    nbr = open(os.path.join(BASELINE, "dc_ospf_nbr.json")).read()
    assert _cond(["ospf-full", "10.255.0.1", "10.255.0.2"], nbr) == 0
    assert _cond(["ospf-full", "10.255.0.9"], nbr) == 1
    route = open(os.path.join(BASELINE, "dc_route_summary.json")).read()
    assert _cond(["nexthop", "10.10.0.0/16", "10.0.11.1", "10.0.12.1"], route) == 0
    assert _cond(["nexthop", "10.10.0.0/16", "10.0.12.1"], route) == 1
    addr = open(os.path.join(BASELINE, "core1_addr10.json")).read()
    assert _cond(["vip", "10.10.10.1", "present"], addr) == 0
    assert _cond(["vip", "10.10.10.1", "absent"], addr) == 1
    assert _cond(["vip", "10.10.10.1", "present"], "Device \"eth1.10\" does not exist.") == 1
