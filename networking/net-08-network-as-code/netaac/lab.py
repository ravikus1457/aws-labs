"""How the live steps reach the containerlab nodes (docker exec / nsenter).

Kept tiny on purpose: everything that decides PASS/FAIL lives in pure modules and is
exercised in the unit tests through a fake with this same interface (tests/fakenet.py).
"""

from __future__ import annotations

import os
import shlex
import subprocess


class LabError(RuntimeError):
    pass


class DockerLab:
    """Talks to clab-<lab>-<node> containers on this machine."""

    def __init__(self, lab_name: str):
        self.prefix = f"clab-{lab_name}-"
        self.docker = shlex.split(os.environ.get("NET08_DOCKER", "docker"))
        default_sudo = "" if os.geteuid() == 0 else "sudo -n"
        self.sudo = shlex.split(os.environ.get("NET08_SUDO", default_sudo))
        self._pids: dict[str, str] = {}

    def container(self, node: str) -> str:
        return self.prefix + node

    def run(self, node: str, argv: list[str], timeout: int = 30, detach: bool = False) -> tuple[int, str]:
        cmd = [*self.docker, "exec", *(["-d"] if detach else []), self.container(node), *argv]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return 124, f"timeout after {timeout}s: {shlex.join(argv)}"
        return res.returncode, (res.stdout or "") + (res.stderr or "")

    def vtysh(self, router: str, *commands: str, timeout: int = 30) -> str:
        argv = ["vtysh"]
        for c in commands:
            argv += ["-c", c]
        rc, out = self.run(router, argv, timeout=timeout)
        if rc != 0:
            raise LabError(f"{router}: vtysh {' / '.join(commands)} -> rc {rc}: {out.strip()[:400]}")
        return out

    def _pid(self, node: str) -> str:
        if node not in self._pids:
            res = subprocess.run(
                [*self.docker, "inspect", "-f", "{{.State.Pid}}", self.container(node)],
                capture_output=True,
                text=True,
                check=False,
            )
            pid = res.stdout.strip()
            if res.returncode != 0 or not pid.isdigit() or pid == "0":
                raise LabError(f"{node}: cannot find container pid ({res.stderr.strip()})")
            self._pids[node] = pid
        return self._pids[node]

    def netns(self, node: str, argv: list[str], stdin: str | None = None, timeout: int = 30) -> tuple[int, str]:
        """Run a HOST binary (iptables, iptables-save) inside the node's network namespace.
        The FRR image ships no iptables; the runner's does, and netfilter state is per-netns."""
        try:
            cmd = [*self.sudo, "nsenter", "-t", self._pid(node), "-n", *argv]
        except LabError as exc:
            return 125, str(exc)
        try:
            res = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return 124, f"timeout after {timeout}s: {shlex.join(argv)}"
        return res.returncode, (res.stdout or "") + (res.stderr or "")

    def frr_reload(self, router: str) -> tuple[int, str]:
        """FRR's own tool: diff running vs /etc/frr/frr.conf and apply adds AND removals."""
        return self.run(router, ["/usr/lib/frr/frr-reload.py", "--reload", "--stdout", "/etc/frr/frr.conf"], timeout=180)
