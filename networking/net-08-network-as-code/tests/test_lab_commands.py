"""DockerLab builds the right docker/nsenter command lines (stub binaries, no Docker needed)."""

import json
import os
import stat

import pytest

from netaac.lab import DockerLab, LabError

STUB = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["STUB_LOG"], "a") as f:
    f.write(json.dumps([os.path.basename(sys.argv[0])] + sys.argv[1:]) + "\\n")
args = sys.argv[1:]
if args[:1] == ["inspect"]:
    print("4242")
elif "fail" in args:
    print("boom", file=sys.stderr); sys.exit(3)
else:
    print("ok")
"""


@pytest.fixture
def stubs(tmp_path, monkeypatch):
    for name in ("docker", "nsenter"):
        p = tmp_path / name
        p.write_text(STUB)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_LOG", str(log))
    monkeypatch.setenv("NET08_SUDO", "")
    monkeypatch.setenv("NET08_DOCKER", "docker")
    return lambda: [json.loads(line) for line in log.read_text().splitlines()]


def test_vtysh_command_line(stubs):
    DockerLab("net08").vtysh("br1-r1", "configure terminal", "ip route 203.0.113.0/24 blackhole", "end")
    assert stubs()[-1] == [
        "docker",
        "exec",
        "clab-net08-br1-r1",
        "vtysh",
        "-c",
        "configure terminal",
        "-c",
        "ip route 203.0.113.0/24 blackhole",
        "-c",
        "end",
    ]


def test_vtysh_failure_raises(stubs):
    with pytest.raises(LabError):
        DockerLab("net08").vtysh("br1-r1", "fail")


def test_detached_exec(stubs):
    rc, _ = DockerLab("net08").run("hq-srv1", ["socat", "TCP-LISTEN:80,fork,reuseaddr", "SYSTEM:echo net08"], detach=True)
    assert rc == 0 and stubs()[-1][:4] == ["docker", "exec", "-d", "clab-net08-hq-srv1"]


def test_netns_uses_container_pid(stubs):
    rc, out = DockerLab("net08").netns("br2-r1", ["iptables-save", "-c", "-t", "filter"])
    log = stubs()
    assert log[-2] == ["docker", "inspect", "-f", "{{.State.Pid}}", "clab-net08-br2-r1"]
    assert log[-1] == ["nsenter", "-t", "4242", "-n", "iptables-save", "-c", "-t", "filter"]
    assert rc == 0 and out.strip() == "ok"


def test_frr_reload_uses_frr_tool(stubs):
    DockerLab("net08").frr_reload("hq-r1")
    assert stubs()[-1][3:] == ["/usr/lib/frr/frr-reload.py", "--reload", "--stdout", "/etc/frr/frr.conf"]
