"""The live orchestration, end to end, against a simulated lab - healthy and broken.

A healthy fake must give an all-PASS run. Each fault must turn exactly the check that exists
to catch it red. If a fault ever produces an all-PASS run, the test suite fails: that would be
a detector that cannot detect.
"""

import pytest

from fakenet import FakeLab
from netaac import drift, live
from netaac.checks import FAIL, PASS


def bring_up(net, files, build, **faults):
    lab = FakeLab(net, files, **faults)
    setup = live.post_deploy(net, lab, build, sleep=lambda s: None)
    return lab, setup


def failed(cs):
    return [c for c in cs if c.status == FAIL]


def test_healthy_lab_is_all_green(net, files, build):
    lab, setup = bring_up(net, files, build)
    assert not failed(setup)
    seconds, snap, polls = live.wait_converged(net, lab, timeout=10, interval=0, sleep=lambda s: None)
    assert seconds is not None and not failed(snap)
    results = live.run_tests(net, lab)
    assert not failed(results), failed(results)
    probes = [c for c in results if c.category == "reachability"]
    assert len(probes) == len(net.probes) == 16
    demo, metrics = live.drift_demo(net, lab, build)
    assert not failed(demo), failed(demo)
    assert metrics["drift_injected"] == metrics["drift_detected"] == 3
    assert set(metrics["heal_method"].values()) == {"frr-reload.py"}
    assert not failed(live.run_tests(net, lab)), "post-heal run must be green again"


def test_wait_converged_measures_and_times_out(net, files, build):
    lab, _ = bring_up(net, files, build, converge_after=3)
    t = iter(range(100))
    seconds, snap, polls = live.wait_converged(net, lab, timeout=50, interval=1, clock=lambda: next(t), sleep=lambda s: None)
    assert polls == 4 and seconds is not None
    lab, _ = bring_up(net, files, build, bgp_down={("br1-r1", "172.16.0.1")})
    t = iter(range(100))
    seconds, snap, _ = live.wait_converged(net, lab, timeout=5, interval=1, clock=lambda: next(t), sleep=lambda s: None)
    assert seconds is None and failed(snap)


@pytest.mark.parametrize(
    "faults, category, subject_part",
    [
        ({"acl_restore_fails": {"br2-r1"}}, "reachability", "br2-guest1 -> hq-srv1 icmp"),  # reachable-but-should-be-denied
        ({"acl_leak": {"br1-no-ssh-servers"}}, "reachability", "br1-pc1 -> hq-srv1 tcp/22"),
        ({"path_broken": {("br2-guest1", "br1-pc1")}}, "reachability", "br2-guest1 -> br1-pc1"),  # denied, wrong reason
        ({"path_broken": {("br1-pc1", "br2-pc1")}}, "reachability", "br1-pc1 -> br2-pc1"),  # unreachable-but-should-reach
        ({"no_listener": {("hq-srv1", 80)}}, "reachability", "br1-pc1 -> hq-srv1 tcp/80"),
        ({"bgp_down": {("hq-r2", "172.16.0.6")}}, "bgp-session", "hq-r2 -> br2-r1"),
        ({"route_proto": {("hq-r1", "10.20.10.0/24"): "static"}}, "route", "hq-r1 10.20.10.0/24"),
    ],
)
def test_each_network_fault_is_caught(net, files, build, faults, category, subject_part):
    lab, _ = bring_up(net, files, build, **faults)
    bad = failed(live.run_tests(net, lab))
    assert any(c.category == category and subject_part in c.subject for c in bad), bad


def test_acl_restore_failure_shows_at_setup_and_install(net, files, build):
    lab, setup = bring_up(net, files, build, acl_restore_fails={"br2-r1"})
    assert any(c.subject == "br2-r1" and "iptables-restore" in c.expect for c in failed(setup))
    assert any(c.category == "acl-installed" for c in failed(live.acl_checks(net, lab)))


def test_missing_listener_shows_at_setup(net, files, build):
    _, setup = bring_up(net, files, build, no_listener={("hq-srv1", 22)})
    assert [c.subject for c in failed(setup)] == ["hq-srv1"]


# ---- drift demo: the detector and the healer can each fail ---------------------------------
def test_blind_running_config_fails_detection(net, files, build):
    lab, _ = bring_up(net, files, build, running_stale=True)
    demo, metrics = live.drift_demo(net, lab, build)
    assert metrics["drift_detected"] == 0
    assert {c.category for c in failed(demo)} == {"drift-detect"}


def test_sabotaged_diff_fails_detection(net, files, build, monkeypatch):
    """Sabotage the REPORTER, not the data: a diff that always says clean must go red."""
    lab, _ = bring_up(net, files, build)
    monkeypatch.setattr(drift, "diff", lambda router, a, b: drift.Drift(router))
    demo, _ = live.drift_demo(net, lab, build)
    assert len([c for c in failed(demo) if c.category == "drift-detect"]) == 3


def test_baseline_drift_is_reported_but_not_blamed_on_injection(net, files, build):
    lab, _ = bring_up(net, files, build, baseline_noise=True)
    demo, _ = live.drift_demo(net, lab, build)
    assert {c.category for c in failed(demo)} == {"drift-baseline"}


def test_heal_falls_back_to_vtysh_when_reload_is_unavailable(net, files, build):
    lab, _ = bring_up(net, files, build, reload_broken=True)
    demo, metrics = live.drift_demo(net, lab, build)
    assert not failed(demo), failed(demo)
    assert all("vtysh remediation" in m for m in metrics["heal_method"].values())


def test_failed_heal_is_red(net, files, build):
    lab, _ = bring_up(net, files, build, heal_broken=True)
    demo, _ = live.drift_demo(net, lab, build)
    assert {c.category for c in failed(demo)} == {"drift-heal"}


def test_injections_are_derived_from_intent(net):
    inj = live.plan_injections(net)
    assert [(i.router, i.kind) for i in inj] == [("br2-r1", "extra"), ("hq-r1", "extra"), ("br1-r1", "missing")]
    assert inj[2].line == "router bgp 65010 > neighbor 172.16.0.1 description hq-r2"


def test_sabotaged_probe_runner_turns_everything_red(net, files, build, monkeypatch):
    lab, _ = bring_up(net, files, build)
    monkeypatch.setattr(live, "_attempt", lambda lab, p: False)
    probes = live.run_probes(net, lab)
    assert all(c.status == FAIL for c in probes), "an always-fail prober must not pass a single probe"
    monkeypatch.setattr(live, "_attempt", lambda lab, p: True)
    assert sum(c.status == PASS for c in live.run_probes(net, lab)) == sum(p.expect == "allow" for p in net.probes)
