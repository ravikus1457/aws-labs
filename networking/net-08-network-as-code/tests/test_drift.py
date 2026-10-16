"""Drift diff: FRR's formatting noise is NOT drift; any real difference IS."""

from fakenet import apply, parse, render
from netaac import drift


def running(files, router, mutate=None):
    tree = parse(files[f"configs/{router}/frr.conf"])
    if mutate:
        mutate(tree)
    return render(tree)


def test_identical_config_in_frr_format_is_clean(files):
    for path, text in files.items():
        if path.endswith("frr.conf"):
            router = path.split("/")[1]
            d = drift.diff(router, text, running(files, router))
            assert d.clean, (router, d)


def test_frr_noise_lines_are_ignored(files):
    text = files["configs/br1-r1/frr.conf"]
    noisy = running(files, "br1-r1") + "\nip forwarding\ninterface eth0\nexit\n"
    assert drift.diff("br1-r1", text, noisy).clean


def test_static_route_added_out_of_band_is_extra(files):
    run = running(files, "br2-r1", lambda t: apply(t, [], "ip route 203.0.113.0/24 blackhole"))
    d = drift.diff("br2-r1", files["configs/br2-r1/frr.conf"], run)
    assert d.extra == ["ip route 203.0.113.0/24 blackhole"] and d.missing == []


def test_interface_setting_is_reported_with_its_context(files):
    run = running(files, "hq-r1", lambda t: apply(t, ["interface eth1"], "ip ospf cost 444"))
    d = drift.diff("hq-r1", files["configs/hq-r1/frr.conf"], run)
    assert d.extra == ["interface eth1 > ip ospf cost 444"]


def test_removed_line_is_missing(files):
    run = running(files, "br1-r1", lambda t: apply(t, ["router bgp 65010"], "no neighbor 172.16.0.1 description"))
    d = drift.diff("br1-r1", files["configs/br1-r1/frr.conf"], run)
    assert d.missing == ["router bgp 65010 > neighbor 172.16.0.1 description hq-r2"] and d.extra == []


def test_same_line_in_a_different_block_is_drift(files):
    intended = files["configs/br1-r1/frr.conf"]
    moved = intended.replace("  network 10.20.10.0/24\n", "").replace(" bgp router-id", " network 10.20.10.0/24\n bgp router-id")
    d = drift.diff("br1-r1", intended, moved)
    assert d.missing == ["router bgp 65010 > address-family ipv4 unicast > network 10.20.10.0/24"]
    assert d.extra == ["router bgp 65010 > network 10.20.10.0/24"]


def test_changed_address_shows_both_sides(files):
    intended = files["configs/br1-r1/frr.conf"]
    d = drift.diff("br1-r1", intended, intended.replace("ip address 10.20.10.1/24", "ip address 10.20.10.2/24"))
    assert d.missing == ["interface eth2 > ip address 10.20.10.1/24"]
    assert d.extra == ["interface eth2 > ip address 10.20.10.2/24"]


def test_remediation_commands(files):
    d = drift.Drift(
        "x",
        missing=["router bgp 65010 > neighbor 1.1.1.1 description y"],
        extra=["ip route 203.0.113.0/24 blackhole", "interface eth1 > no ip ospf passive"],
    )
    cmds = drift.remediation(d)
    assert ["configure terminal", "no ip route 203.0.113.0/24 blackhole", "end"] in cmds
    assert ["configure terminal", "interface eth1", "ip ospf passive", "end"] in cmds
    assert ["configure terminal", "router bgp 65010", "neighbor 1.1.1.1 description y", "end"] in cmds


def test_hand_written_frr_show_running_is_clean(files):
    """Independent of the fake's renderer: FRR-format text written by hand from FRR's output style."""
    text = (__import__("pathlib").Path(__file__).parent / "fixtures" / "br1-r1.show-running.txt").read_text()
    d = drift.diff("br1-r1", files["configs/br1-r1/frr.conf"], text)
    assert d.clean, d
    d = drift.diff("br1-r1", files["configs/br1-r1/frr.conf"], text.replace("timers connect 5", "timers connect 120"))
    assert d.extra == ["router bgp 65010 > neighbor 172.16.0.1 timers connect 120"]
