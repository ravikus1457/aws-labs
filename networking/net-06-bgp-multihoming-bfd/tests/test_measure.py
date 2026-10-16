"""measure.py must PASS a good run and FAIL each bad one. Every 'bad' test below starts from REAL
evidence and breaks exactly one thing the lab claims to prove; if a detector stops catching it,
that test goes red."""

import json
import os
import re
import subprocess
import sys

import pytest
from conftest import FIXTURES, LAB, drop_received, edit_poll, meta, status

import measure

BASELINE = os.path.join(FIXTURES, "baseline")


# --------------------------------------------------------------------------------------------
# parsers
# --------------------------------------------------------------------------------------------


def test_parse_docs_reads_back_to_back_vtysh_documents():
    docs = measure.parse_docs('{\n "a": 1\n}\n{"b": [2]}\n  ')
    assert docs == [{"a": 1}, {"b": [2]}]
    assert measure.parse_docs("") == []
    with pytest.raises(ValueError):
        measure.parse_docs("% Unknown command: show bgp nonsense")


def test_traceroute_parser_handles_timeouts_and_header():
    text = "traceroute to 198.51.100.10 (198.51.100.10), 8 hops max\n 1  203.0.113.1  0.01 ms\n 2  *\n 3  10.0.2.1  0.2 ms\n"
    assert measure.parse_traceroute(text) == ["203.0.113.1", None, "10.0.2.1"]
    assert measure.classify_path(["203.0.113.1", "10.0.1.1"]) == "isp-a"
    assert measure.classify_path(["10.0.2.1"]) == "isp-b"
    assert measure.classify_path(["10.0.1.1", "10.0.2.1"]) == "both"
    assert measure.classify_path([None, None]) == "unknown"


def test_frr_readers_on_real_output():
    j = lambda n: json.load(open(os.path.join(BASELINE, n)))  # noqa: E731
    assert measure.bgp_peer_states(j("ent_summary.json")) == {
        "10.0.1.1": "Established", "10.0.2.1": "Established", "10.255.0.2": "Established"}
    assert measure.route_nexthops(j("ent_route_srv.json"), "198.51.100.0/24") == frozenset({"10.0.1.1"})
    assert measure.route_nexthops(j("ent_route_srv.json"), "10.9.9.0/24") is None
    paths = measure.bgp_prefix_paths(j("inet_bgp_own.json"))
    assert sorted(p["aspath"] for p in paths) == [[64501, 65010], [64502, 65010, 65010, 65010]]
    assert measure.bfd_status(j("ent_bfd.json"), "10.0.1.1") == "up"
    assert measure.bfd_status(j("ent_bfd.json"), "10.9.9.9") is None


# --------------------------------------------------------------------------------------------
# data-plane maths (synthetic, exact)
# --------------------------------------------------------------------------------------------


def _stream(n, interval=0.01, t0=1000.0, lost=(), errors=()):
    sent = [(i, t0 + i * interval, "err:101" if i in errors else "ok") for i in range(n)]
    recv = {i: (t, t + 0.0001) for i, t, _ in sent if i not in lost}
    return sent, recv


def test_outage_window_is_first_lost_to_first_delivered_after():
    sent, recv = _stream(1000, lost=range(500, 537))  # 37 packets lost from t=1005.00
    r = measure.analyze_flow(sent, recv, t_fault=1004.995, t_restore=1008.0)
    assert r["outage"]["lost"] == 37
    assert r["outage"]["window_ms"] == pytest.approx(370.0)
    assert r["outage"]["recovered"] is True
    assert r["pre_fault_lost"] == 0
    assert r["failback"]["window_ms"] == 0.0


def test_intermittent_loss_spans_the_whole_fault_window():
    sent, recv = _stream(1000, lost=[500, 501, 520, 521, 522])
    r = measure.analyze_flow(sent, recv, 1004.995, 1008.0)
    assert r["outage"]["window_ms"] == pytest.approx(230.0)  # 1005.00 -> 1005.23
    assert r["outage"]["longest_run_ms"] == pytest.approx(30.0)


def test_loss_until_restore_is_not_a_failover():
    sent, recv = _stream(1000, lost=range(500, 820))  # nothing delivered until after t_restore=1008.0
    r = measure.analyze_flow(sent, recv, 1004.995, 1008.0)
    assert r["outage"]["recovered"] is False


def test_warmup_loss_ignored_but_later_baseline_loss_counted():
    sent, recv = _stream(1000, lost=[0, 1, 100])
    r = measure.analyze_flow(sent, recv, 1005.0, 1008.0)
    assert r["pre_fault_lost"] == 1  # packets 0,1 are inside the 300 ms warm-up; 100 is not


def test_transition_requires_the_state_to_stick():
    series = [(t, t + 0.01, v) for t, v in [(1.0, "a"), (1.1, "b"), (1.2, "a"), (1.3, "b"), (1.4, "b")]]
    r = measure.transition(series, 1.0, lambda v: v == "b")
    assert r["ms"] == pytest.approx(310.0)  # 1.3 + 0.01 - 1.0: the flap at 1.1 does not count
    assert measure.transition(series, 1.0, lambda v: v == "b", stable=False)["ms"] == pytest.approx(110.0)
    assert measure.transition(series, 1.0, lambda v: v == "z") is None


# --------------------------------------------------------------------------------------------
# baseline: real run passes; each broken policy fails its own check
# --------------------------------------------------------------------------------------------


def test_real_baseline_passes():
    r = measure.evaluate_baseline(BASELINE)
    failed = [c for c in r["checks"] if c["status"] != "PASS"]
    assert not failed, failed
    assert len(r["checks"]) == 15


def _edit_json(d, name, fn):
    p = os.path.join(d, name)
    data = json.load(open(p))
    data = fn(data) or data
    json.dump(data, open(p, "w"))


def test_bogon_accepted_fails_inbound_filter(fixture_copy):
    d = fixture_copy("baseline")

    def leak(rib):
        rib["routes"]["10.66.0.0/16"] = [{"valid": True, "bestpath": True, "peerId": "10.0.1.1",
                                          "nexthops": [{"ip": "10.0.1.1"}], "path": "64501"}]

    _edit_json(d, "ent_rib.json", leak)
    assert status(measure.evaluate_baseline(d), "baseline.inbound_filter.10.0.1.1") == "FAIL"


def test_filter_not_exercised_fails(fixture_copy):
    """If the ISP never sent the junk, 'nothing leaked' proves nothing - the check must not pass."""
    d = fixture_copy("baseline")
    _edit_json(d, "ent_rcv_ispb.json", lambda j: j["receivedRoutes"].pop("192.168.50.0/24"))
    assert status(measure.evaluate_baseline(d), "baseline.inbound_filter.10.0.2.1") == "FAIL"


def test_transit_leak_fails_outbound_filter(fixture_copy):
    d = fixture_copy("baseline")
    _edit_json(d, "ent_adv_ispa.json", lambda j: j["advertisedRoutes"].update({"198.51.100.0/24": {}}))
    assert status(measure.evaluate_baseline(d), "baseline.outbound_filter.10.0.1.1") == "FAIL"


def test_wrong_local_preference_fails(fixture_copy):
    d = fixture_copy("baseline")

    def flip(doc):
        for p in doc["paths"]:
            p["locPrf"] = 100

    _edit_json(d, "ent_bgp_srv.json", flip)
    assert status(measure.evaluate_baseline(d), "baseline.local_preference") == "FAIL"


def test_missing_prepend_fails(fixture_copy):
    d = fixture_copy("baseline")

    def no_prepend(doc):
        for p in doc["paths"]:
            if p["nexthops"][0]["ip"] == "10.1.2.1":
                p["aspath"]["string"] = "64502 65010"

    _edit_json(d, "inet_bgp_own.json", no_prepend)
    assert status(measure.evaluate_baseline(d), "baseline.as_path_prepend") == "FAIL"


def test_bfd_down_fails(fixture_copy):
    d = fixture_copy("baseline")
    _edit_json(d, "ispa_bfd.json", lambda peers: [dict(p, status="down") for p in peers])
    assert status(measure.evaluate_baseline(d), "baseline.bfd") == "FAIL"


def test_wrong_data_path_fails(fixture_copy):
    d = fixture_copy("baseline")
    p = os.path.join(d, "trace_h1_srv.txt")
    open(p, "w").write(open(p).read().replace("10.0.1.1", "10.0.2.1").replace("10.1.1.2", "10.1.2.2"))
    assert status(measure.evaluate_baseline(d), "baseline.path.h1->srv") == "FAIL"


def test_missing_capture_fails_not_skips(fixture_copy):
    d = fixture_copy("baseline")
    os.remove(os.path.join(d, "inet_summary.json"))
    assert status(measure.evaluate_baseline(d), "baseline.sessions.inet") == "FAIL"


# --------------------------------------------------------------------------------------------
# scenarios: the three real runs pass with their real numbers; each injected defect fails
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scenario,out_ms,in_ms",
    [("bfd", 310.0, 400.0), ("holdtimer", 27170.0, 27230.0), ("linkdown", 71.8, 338.9)],
)
def test_real_scenarios_pass_with_their_measured_numbers(scenario, out_ms, in_ms):
    r = measure.evaluate_scenario(os.path.join(FIXTURES, f"real_{scenario}"))
    failed = [c for c in r["checks"] if c["status"] != "PASS"]
    assert not failed, failed
    assert r["summary"]["h1_to_srv"]["outage"]["window_ms"] == out_ms
    assert r["summary"]["srv_to_h1"]["outage"]["window_ms"] == in_ms


def test_no_failover_fails(fixture_copy):
    d = fixture_copy("real_bfd")
    m = meta(d)
    drop_received(d, "h1_to_srv", m["t_fault"], m["t_restore"])  # nothing got through while ISP-A was down
    r = measure.evaluate_scenario(d)
    assert status(r, "bfd.h1_to_srv.failover") == "FAIL"
    assert status(r, "bfd.h1_to_srv.outage_ms") == "FAIL"


def test_loss_window_above_threshold_fails(fixture_copy):
    d = fixture_copy("real_bfd")
    m = meta(d)
    drop_received(d, "srv_to_h1", m["t_fault"], m["t_fault"] + 2.0)  # a 2 s outage with BFD "on"
    r = measure.evaluate_scenario(d)
    assert status(r, "bfd.srv_to_h1.outage_ms") == "FAIL"
    assert status(r, "bfd.srv_to_h1.failover") == "PASS"  # it did fail over - just too slowly


def test_failback_not_happening_fails(fixture_copy):
    d = fixture_copy("real_bfd")
    m = meta(d)
    # after the repair ent's FIB keeps pointing at ISP-B (e.g. local-pref lost on the restored session)
    edit_poll(d, "ent", m["t_restore"], lambda t: t.replace('"ip":"10.0.1.1"', '"ip":"10.0.2.1"'))
    r = measure.evaluate_scenario(d)
    assert status(r, "bfd.ent_failback_ms") == "FAIL"


def test_traffic_not_restored_after_repair_fails(fixture_copy):
    d = fixture_copy("real_bfd")
    m = meta(d)
    drop_received(d, "h1_to_srv", m["t_restore"], m["t_restore"] + 3600)
    assert status(measure.evaluate_scenario(d), "bfd.h1_to_srv.failback_loss_ms") == "FAIL"


def test_loss_before_the_fault_fails(fixture_copy):
    d = fixture_copy("real_bfd")
    m = meta(d)
    drop_received(d, "h1_to_srv", m["t_fault"] - 2.0, m["t_fault"] - 1.9)
    assert status(measure.evaluate_scenario(d), "bfd.h1_to_srv.baseline_clean") == "FAIL"


def test_holdtimer_run_with_bfd_still_on_fails(fixture_copy):
    """A 'hold timer' scenario that actually ran with BFD must not be reported as a hold-timer result."""
    d = fixture_copy("real_bfd")
    m = meta(d)
    m["scenario"] = "holdtimer"
    json.dump(m, open(os.path.join(d, "meta.json"), "w"))
    r = measure.evaluate_scenario(d)
    assert status(r, "holdtimer.pre.bfd") == "FAIL"
    assert status(r, "holdtimer.detect_ms") == "FAIL"  # 349 ms is not a 30 s hold timer


def test_fault_that_did_nothing_fails(fixture_copy):
    """If the fault never broke ISP-A, no reroute happens; 'zero loss' must not read as success."""
    d = fixture_copy("real_bfd")
    m = meta(d)
    edit_poll(d, "ent", m["t_fault"], lambda t: re.sub(r'"state":"[A-Za-z]+"', '"state":"Established"',
                                                       t.replace('"ip":"10.0.2.1"', '"ip":"10.0.1.1"')))
    r = measure.evaluate_scenario(d)
    assert status(r, "bfd.ent_reroute_ms") == "FAIL"
    assert status(r, "bfd.detect_ms") == "FAIL"


def test_incomplete_meta_fails(fixture_copy):
    d = fixture_copy("real_bfd")
    json.dump({"scenario": "bfd"}, open(os.path.join(d, "meta.json"), "w"))
    assert status(measure.evaluate_scenario(d), "bfd.meta") == "FAIL"


# --------------------------------------------------------------------------------------------
# report: the verdict can only be PASS if every check passed and nothing is missing
# --------------------------------------------------------------------------------------------


def _evidence(tmp_path, fixture_copy, scenarios=("bfd", "holdtimer", "linkdown")):
    ev = tmp_path / "ev"
    ev.mkdir()
    os.rename(fixture_copy("baseline"), ev / "baseline")
    json.dump(measure.evaluate_baseline(str(ev / "baseline")), open(ev / "baseline" / "result.json", "w"), default=list)
    for s in scenarios:
        os.rename(fixture_copy(f"real_{s}"), ev / s)
        json.dump(measure.evaluate_scenario(str(ev / s)), open(ev / s / "result.json", "w"), default=list)
    return str(ev)


def _report(ev):
    return subprocess.run([sys.executable, os.path.join(LAB, "measure.py"), "report", ev, os.path.join(ev, "report.md")],
                          capture_output=True, text=True)


def test_report_on_real_run_passes(tmp_path, fixture_copy):
    ev = _evidence(tmp_path, fixture_copy)
    r = _report(ev)
    assert r.returncode == 0, r.stdout
    md = open(os.path.join(ev, "report.md")).read()
    assert "**Verdict: PASS**" in md
    assert "| 310.0 | 400.0 |" in md and "27,170.0" in md
    assert "compare.bfd_speedup" in md


def test_report_fails_when_a_scenario_is_missing(tmp_path, fixture_copy):
    ev = _evidence(tmp_path, fixture_copy, scenarios=("bfd", "linkdown"))
    r = _report(ev)
    assert r.returncode == 1
    md = open(os.path.join(ev, "report.md")).read()
    assert "**MISSING**" in md and "holdtimer.result" in md


def test_report_fails_on_any_failed_check(tmp_path, fixture_copy):
    """Sabotage a result file (as a buggy evaluator would) - the reporter must not average it away."""
    ev = _evidence(tmp_path, fixture_copy)
    p = os.path.join(ev, "linkdown", "result.json")
    res = json.load(open(p))
    res["checks"][3]["status"] = "FAIL"
    json.dump(res, open(p, "w"))
    assert _report(ev).returncode == 1
    assert "**Verdict: FAIL**" in open(os.path.join(ev, "report.md")).read()


def test_report_speedup_check_fails_when_bfd_is_slow(tmp_path, fixture_copy):
    ev = _evidence(tmp_path, fixture_copy)
    p = os.path.join(ev, "bfd", "result.json")
    res = json.load(open(p))
    res["summary"]["worst_outage_ms"] = 20000.0
    json.dump(res, open(p, "w"))
    r = _report(ev)
    assert r.returncode == 1
    assert "FAIL compare.bfd_speedup" in r.stdout


# --------------------------------------------------------------------------------------------
# run.sh wait-loop conditions
# --------------------------------------------------------------------------------------------


def _cond(args, text):
    return subprocess.run([sys.executable, os.path.join(LAB, "measure.py"), "cond", *args], input=text,
                          capture_output=True, text=True).returncode


def test_cond_on_real_output():
    summary = open(os.path.join(BASELINE, "ent_summary.json")).read()
    assert _cond(["established", "10.0.1.1", "10.0.2.1"], summary) == 0
    assert _cond(["established", "10.0.1.1", "10.9.9.9"], summary) == 1
    route = open(os.path.join(BASELINE, "ent_route_srv.json")).read()
    assert _cond(["nexthop", "198.51.100.0/24", "10.0.1.1"], route) == 0
    assert _cond(["nexthop", "198.51.100.0/24", "10.0.2.1"], route) == 1
    bfd = open(os.path.join(BASELINE, "ent_bfd.json")).read()
    assert _cond(["bfd", "10.0.1.1", "up"], bfd) == 0
    assert _cond(["bfd", "10.0.1.1", "absent"], bfd) == 1
    assert _cond(["bfd", "10.0.1.1", "absent"], "[]") == 0
    assert _cond(["established", "10.0.1.1"], "% Can't open configuration file") == 1
    trace = open(os.path.join(BASELINE, "trace_h1_srv.txt")).read()
    assert _cond(["trace-via", "isp-a"], trace) == 0
