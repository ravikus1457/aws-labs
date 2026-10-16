"""A simulated lab with the DockerLab interface, for driving netaac.live offline.

It answers the exact commands the live code sends (vtysh show commands, ping, bash
/dev/tcp, iptables-save/-restore, frr-reload.py) from the intent, and it can be told to
break in specific ways. Each fault exists so a test can prove the matching check goes red.

Faults (all optional):
  acl_restore_fails: {router}         iptables-restore errors -> ACL never installed
  acl_leak:          {rule name}      rule installed but lets packets through
  path_broken:       {(src, dst)}     packets silently lost (no ACL involved)
  bgp_down:          {(router, peer_ip)}
  route_proto:       {(router, prefix): proto}   RIB shows a different protocol
  converge_after:    int              control plane empty for the first N polls
  running_stale:     True             `show running-config` never changes (blind detector)
  baseline_noise:    True             box starts with one undocumented line
  reload_broken:     True             frr-reload.py fails (fallback must heal)
  heal_broken:       True             nothing can change the config after injection
  no_listener:       {(host, port)}
"""

from __future__ import annotations

import json
import re
from ipaddress import IPv4Address

from netaac.lab import LabError
from netaac.model import Net

NOISE = "ip route 192.0.2.0/24 blackhole"


# ---- a minimal FRR-like config tree ---------------------------------------------------------
def parse(text: str) -> list[dict]:
    root: list[dict] = []
    stack: list[tuple[int, dict]] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("!") or s in ("end", "exit", "exit-address-family"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        node = {"text": s, "children": []}
        while stack and stack[-1][0] >= indent:
            stack.pop()
        (stack[-1][1]["children"] if stack else root).append(node)
        stack.append((indent, node))
    return root


def render(tree: list[dict]) -> str:
    """FRR `show running-config` style, with banner and FRR's own extra lines."""
    out = ["Building configuration...", "", "Current configuration:", "!", "frr version 10.4.5_git", "no ipv6 forwarding"]

    def emit(nodes, depth):
        for n in nodes:
            out.append(" " * depth + n["text"])
            if n["children"]:
                emit(n["children"], depth + 1)
                if n["text"].startswith("address-family"):
                    out.append(" " * depth + "exit-address-family")
                elif depth == 0:
                    out.append("exit")
            if depth == 0:
                out.append("!")

    # FRR prints blocks in its own order, not the file's: reverse to prove order-insensitivity.
    emit(list(reversed(tree)), 0)
    out.append("end")
    return "\n".join(out) + "\n"


def apply(tree: list[dict], ctx: list[str], leaf: str) -> None:
    nodes = tree
    for c in ctx:
        found = next((n for n in nodes if n["text"] == c), None)
        if found is None:
            found = {"text": c, "children": []}
            nodes.append(found)
        nodes = found["children"]
    if leaf.startswith("no "):
        target = leaf[3:]
        nodes[:] = [n for n in nodes if not (n["text"] == target or n["text"].startswith(target + " "))]
    elif not any(n["text"] == leaf for n in nodes):
        nodes.append({"text": leaf, "children": []})


# ---- the fake lab ---------------------------------------------------------------------------
class FakeLab:
    def __init__(self, net: Net, files: dict[str, str], **faults):
        self.net = net
        self.files = files
        self.f = faults
        self.trees = {r: parse(files[f"configs/{r}/frr.conf"]) for r in net.routers}
        if faults.get("baseline_noise"):
            apply(self.trees[sorted(net.routers)[0]], [], NOISE)
        self.stale = {r: render(t) for r, t in self.trees.items()}
        self.forwarding = {r: False for r in net.routers}
        self.acl = {r: False for r in net.routers}
        self.counters: dict[tuple[str, str], int] = {}
        self.listeners: dict[str, set[int]] = {h: set() for h in net.hosts}
        self.polls = 0
        self.frozen = False
        self.log: list[tuple] = []

    # -- docker exec ---------------------------------------------------------------------
    def run(self, node, argv, timeout=30, detach=False):
        self.log.append(("run", node, tuple(argv)))
        cmd = argv[0]
        if cmd == "sysctl":
            self.forwarding[node] = True
            return 0, "net.ipv4.ip_forward = 1"
        if cmd == "ip":
            return 0, ""
        if cmd == "socat":
            port = int(re.search(r"TCP-LISTEN:(\d+)", argv[1]).group(1))
            if (node, port) not in self.f.get("no_listener", set()):
                self.listeners[node].add(port)
            return 0, ""
        if cmd == "ss":
            return 0, "".join(f"LISTEN 0 5 0.0.0.0:{p} 0.0.0.0:*\n" for p in sorted(self.listeners[node]))
        if cmd == "ping":
            return (0 if self._send(node, argv[-1], "icmp", None, 3) else 1), ""
        if cmd == "timeout" and argv[2] == "bash":
            m = re.search(r"/dev/tcp/([\d.]+)/(\d+)", argv[-1])
            return (0 if self._send(node, m.group(1), "tcp", int(m.group(2)), 2) else 1), ""
        if cmd == "/usr/lib/frr/frr-reload.py":
            return self.frr_reload(node)
        return 127, f"fake: unknown command {argv}"

    def _send(self, src: str, dst_ip: str, proto: str, port, pkts: int) -> bool:
        dst = next(h for h in self.net.hosts.values() if str(h.ip.ip) == dst_ip)
        if not all(self.forwarding.values()):
            return False
        if (src, dst.name) in self.f.get("path_broken", set()):
            return False
        router = self.net.hosts[src].router
        if self.acl[router]:
            s, d = self.net.hosts[src].ip.ip, IPv4Address(dst_ip)
            for rule in self.net.acl_rules_for(router):
                if rule.matches(s, d, proto, port):
                    if rule.action == "deny" and rule.name not in self.f.get("acl_leak", set()):
                        key = (router, rule.comment)
                        self.counters[key] = self.counters.get(key, 0) + pkts
                        return False
                    break
        return not (proto == "tcp" and port not in self.listeners[dst.name])

    # -- nsenter -------------------------------------------------------------------------
    def netns(self, node, argv, stdin=None, timeout=30):
        self.log.append(("netns", node, tuple(argv)))
        if argv[0] == "iptables-restore":
            if node in self.f.get("acl_restore_fails", set()):
                return 1, "iptables-restore: line 9 failed"
            self.acl[node] = True
            return 0, ""
        if argv[0] == "iptables-save":
            if not self.acl[node]:
                return 0, "*filter\n:INPUT ACCEPT [0:0]\n:FORWARD ACCEPT [0:0]\n:OUTPUT ACCEPT [0:0]\nCOMMIT\n"
            lines = []
            for line in self.files[f"acl/{node}.rules"].splitlines():
                if line.startswith("-A "):
                    m = re.search(r'--comment "([^"]+)"', line)
                    pk = self.counters.get((node, m.group(1)), 0) if m else 0
                    # iptables-save prints comments unquoted when they need no quoting
                    lines.append(f"[{pk}:{pk * 84}] " + line.replace(f'"{m.group(1)}"', m.group(1)) if m else line)
                elif not line.startswith("#"):
                    lines.append(line)
            return 0, "\n".join(lines) + "\n"
        return 127, "fake: unknown netns command"

    # -- vtysh ---------------------------------------------------------------------------
    def vtysh(self, router, *commands, timeout=30):
        self.log.append(("vtysh", router, commands))
        if commands and commands[0] == "configure terminal":
            body = [c for c in commands[1:] if c != "end"]
            if not self.frozen:
                apply(self.trees[router], body[:-1], body[-1])
            return ""
        cmd = commands[0]
        if cmd == "show running-config":
            return self.stale[router] if self.f.get("running_stale") else render(self.trees[router])
        if cmd.endswith(" json"):
            first_bgp = min(n for n, r in self.net.routers.items() if r.bgp)
            if cmd == "show bgp ipv4 unicast summary json" and router == first_bgp:
                self.polls += 1  # one poll = one pass over all routers
            return json.dumps(self._json(router, cmd))
        raise LabError(f"fake: unknown vtysh {commands}")

    def _converged(self) -> bool:
        return self.polls > self.f.get("converge_after", 0)

    def _json(self, router: str, cmd: str):
        r = self.net.routers[router]
        up = self._converged()
        if cmd == "show bgp ipv4 unicast summary json":
            peers = {}
            for n in r.neighbors:
                ok = up and (router, n.peer_ip) not in self.f.get("bgp_down", set())
                peers[n.peer_ip] = {
                    "remoteAs": n.peer_asn,
                    "state": "Established" if ok else "Active",
                    "pfxRcd": len(n.import_prefixes) if ok else 0,
                    "pfxSnt": len(n.export_prefixes) if ok else 0,
                }
            return {"routerId": str(r.loopback), "as": r.asn, "peers": peers}
        if cmd == "show ip ospf neighbor json":
            return {
                "neighbors": {
                    rid: [{"nbrState": "Full/-" if up else "Init/-", "ifaceName": f"{ifn}:x"}] for rid, ifn in self.net.expected_ospf(router).items()
                }
            }
        if cmd == "show ip route json":
            out = {}
            for prefix, proto in self.net.expected_routes(router).items():
                if proto != "connected" and not up:
                    continue
                if proto == "bgp" and any(rt == router for rt, _ in self.f.get("bgp_down", set())):
                    continue
                proto = self.f.get("route_proto", {}).get((router, prefix), proto)
                out[prefix] = [{"prefix": prefix, "protocol": proto, "selected": True, "installed": True}]
            return out
        raise LabError(cmd)

    def frr_reload(self, router):
        self.log.append(("reload", router))
        if self.f.get("heal_broken"):
            self.frozen = True
            return 1, "reload failed"
        if self.f.get("reload_broken"):
            return 1, "Please install frr-pythontools package"
        self.trees[router] = parse(self.files[f"configs/{router}/frr.conf"])
        return 0, ""
