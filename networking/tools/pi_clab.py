#!/usr/bin/env python3
"""pi_clab.py - run a containerlab topology with PLAIN Docker (no containerlab, no root).

Why this exists (2026-10-16): Raspberry Pi 6.18 kernels no longer build the legacy `ip_tables` module,
and containerlab 0.81 refuses to start without it (even with nftables iptables and
`mgmt: external-access: false`). Docker itself works on the Pi. This tool reads the SAME .clab.yml the
labs use on GitHub's runners and builds an equivalent topology from Docker primitives, so every lab
also runs on a Pi:

  * node   -> container named clab-<lab>-<node> (same name containerlab uses, so lab scripts that
              `docker exec clab-...` work unchanged), privileged, IP forwarding on, image entrypoint held
              until wiring is done.
  * link   -> one internal Docker bridge network per link ("a cable"). Docker's own IP on that interface
              is flushed, so the lab's router config owns addressing, exactly as with veth pairs.
  * names  -> each link interface is renamed inside the container to the name in the topology (eth1,
              eth2, ...), found by the MAC address Docker assigned.
  * start  -> after wiring, the image's normal entrypoint is started (FRR's docker-start), then each node's
              `exec:` commands run, in order.

Supported subset of the containerlab schema: name, mgmt (ignored: pi_clab makes its own mgmt bridge), topology.nodes
(kind linux, image, binds, exec, env, cmd, entrypoint, sysctls), topology.defaults/kinds (incl. sysctls),
topology.links[].endpoints. Anything else is reported, never silently ignored.

  pi_clab.py deploy  -t lab.clab.yml
  pi_clab.py destroy -t lab.clab.yml
  pi_clab.py inspect -t lab.clab.yml
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shlex
import subprocess
import sys
import time

import yaml

SUPPORTED_NODE_KEYS = {"kind", "image", "binds", "exec", "env", "cmd", "entrypoint", "labels", "sysctls"}


def sh(args, check=True, capture=True):
    r = subprocess.run(args, capture_output=capture, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(shlex.quote(a) for a in args)}\n{r.stderr.strip()}")
    return r.stdout.strip() if capture else ""


def load(path: pathlib.Path) -> dict:
    topo = yaml.safe_load(path.read_text())
    name = topo["name"]
    t = topo.get("topology") or {}
    defaults = t.get("defaults") or {}
    kinds = t.get("kinds") or {}
    nodes = {}
    unsupported = []
    for n, spec in (t.get("nodes") or {}).items():
        spec = dict(spec or {})
        kind = spec.get("kind") or defaults.get("kind") or "linux"
        merged = {**defaults, **(kinds.get(kind) or {}), **spec}
        merged["sysctls"] = {**(defaults.get("sysctls") or {}), **((kinds.get(kind) or {}).get("sysctls") or {}),
                             **(spec.get("sysctls") or {})}
        for k in merged:
            if k not in SUPPORTED_NODE_KEYS:
                unsupported.append(f"node {n}: '{k}'")
        if kind != "linux":
            unsupported.append(f"node {n}: kind '{kind}' (only 'linux' is supported)")
        nodes[n] = merged
    links = []
    for i, l in enumerate(t.get("links") or []):
        eps = l.get("endpoints") or []
        if len(eps) != 2:
            unsupported.append(f"link {i}: needs exactly 2 endpoints")
            continue
        a, b = (e.split(":", 1) for e in eps)
        links.append((a[0], a[1], b[0], b[1]))
    for k in t:
        if k not in {"nodes", "links", "defaults", "kinds"}:
            unsupported.append(f"topology.{k}")
    return {"name": name, "dir": path.resolve().parent, "nodes": nodes, "links": links, "unsupported": unsupported}


def cname(lab, node):
    return f"clab-{lab}-{node}"


def netname(lab, i):
    return f"clab-{lab}-link{i}"


def bind_arg(spec: str, base: pathlib.Path) -> str:
    parts = spec.split(":")
    src = pathlib.Path(parts[0])
    if not src.is_absolute():
        src = (base / src).resolve()
    return ":".join([str(src)] + parts[1:])


def deploy(topo):
    if topo["unsupported"]:
        sys.exit("pi_clab: unsupported topology features (refusing to half-build it):\n  "
                 + "\n  ".join(topo["unsupported"]))
    lab, base = topo["name"], topo["dir"]
    destroy(topo, quiet=True)
    # eth0 = a private management network, like containerlab's mgmt bridge (Docker cannot add networks to a
    # container created with --network none). --internal: no route off the host, same as external-access false.
    sh(["docker", "network", "create", "--internal", "--label", f"pi-clab={lab}", f"clab-{lab}-mgmt"])
    entry = {}
    for n, spec in topo["nodes"].items():
        img = spec["image"]
        if not sh(["docker", "images", "-q", img]):
            print(f"  pulling {img}")
            sh(["docker", "pull", "-q", img])
        info = json.loads(sh(["docker", "image", "inspect", img]))[0]["Config"]
        entry[n] = (spec.get("entrypoint") or info.get("Entrypoint") or [],
                    spec.get("cmd") or info.get("Cmd") or [])
        if isinstance(entry[n][0], str):
            entry[n] = (shlex.split(entry[n][0]), entry[n][1])
        if isinstance(entry[n][1], str):
            entry[n] = (entry[n][0], shlex.split(entry[n][1]))
        args = ["docker", "create", "--name", cname(lab, n), "--hostname", n, "--privileged",
                "--network", f"clab-{lab}-mgmt", "--label", f"pi-clab={lab}",
                "--sysctl", "net.ipv4.ip_forward=1", "--sysctl", "net.ipv6.conf.all.disable_ipv6=0",
                "--entrypoint", "sleep"]
        for k, v in (spec.get("sysctls") or {}).items():
            args += ["--sysctl", f"{k}={v}"]
        for b in spec.get("binds") or []:
            args += ["-v", bind_arg(b, base)]
        for k, v in (spec.get("env") or {}).items():
            args += ["-e", f"{k}={v}"]
        args += [img, "infinity"]
        sh(args)
        sh(["docker", "start", cname(lab, n)])
    for i, (an, ai, bn, bi) in enumerate(topo["links"]):
        net = netname(lab, i)
        # NOT --internal: Docker's internal-network rules drop any packet whose source/destination is outside the
        # network's own (Docker-chosen) subnet, i.e. all of the lab's 10.x traffic (10-16 smoke test: 100% loss).
        # A normal bridge keeps Docker's same-bridge ACCEPT; masquerade off so nothing is NATed off-host.
        sh(["docker", "network", "create", "--label", f"pi-clab={lab}",
            "-o", "com.docker.network.bridge.enable_ip_masquerade=false", net])
        for node, ifname in ((an, ai), (bn, bi)):
            sh(["docker", "network", "connect", net, cname(lab, node)])
            mac = json.loads(sh(["docker", "inspect", cname(lab, node)]))[0]["NetworkSettings"]["Networks"][net]["MacAddress"]
            script = (f"cur=$(ip -o link | awk -F': ' 'tolower($0) ~ /{mac.lower()}/ {{print $2}}' | cut -d@ -f1); "
                      f"[ -n \"$cur\" ] || exit 3; ip link set \"$cur\" down; "
                      f"[ \"$cur\" = '{ifname}' ] || ip link set \"$cur\" name '{ifname}'; "
                      f"ip addr flush dev '{ifname}'; ip link set '{ifname}' up")
            sh(["docker", "exec", cname(lab, node), "sh", "-c", script])
    for n, spec in topo["nodes"].items():
        ep, cmd = entry[n]
        full = list(ep) + list(cmd)
        if full and full != ["sleep", "infinity"]:
            sh(["docker", "exec", "-d", cname(lab, n)] + full)
    time.sleep(2)
    for n, spec in topo["nodes"].items():
        for c in spec.get("exec") or []:
            out = subprocess.run(["docker", "exec", cname(lab, n), "sh", "-c", c], capture_output=True, text=True)
            if out.returncode != 0:
                print(f"  exec on {n} failed (rc {out.returncode}): {c}\n    {out.stderr.strip()[:200]}")
    inspect(topo)


def destroy(topo, quiet=False):
    lab = topo["name"]
    ids = sh(["docker", "ps", "-aq", "--filter", f"label=pi-clab={lab}"], check=False).split()
    if ids:
        sh(["docker", "rm", "-f"] + ids, check=False)
    nets = sh(["docker", "network", "ls", "-q", "--filter", f"label=pi-clab={lab}"], check=False).split()
    if nets:
        sh(["docker", "network", "rm"] + nets, check=False)
    if not quiet:
        print(f"destroyed lab '{lab}': {len(ids)} container(s), {len(nets)} link network(s)")


def inspect(topo):
    lab = topo["name"]
    print(f"lab '{lab}' (pi_clab, plain Docker)")
    for n in topo["nodes"]:
        st = sh(["docker", "inspect", "-f", "{{.State.Status}}", cname(lab, n)], check=False) or "absent"
        ifs = sh(["docker", "exec", cname(lab, n), "sh", "-c",
                  "ip -br addr | awk '$1 !~ /^lo/ {print $1\"=\"$3}' | tr '\\n' ' '"], check=False)
        print(f"  {cname(lab, n):32s} {st:8s} {ifs}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("action", choices=["deploy", "destroy", "inspect", "version"])
    ap.add_argument("-t", "--topo")
    # containerlab-compatible flags the lab scripts pass; deploy always rebuilds and destroy always cleans up.
    ap.add_argument("--reconfigure", action="store_true")
    ap.add_argument("--cleanup", action="store_true")
    a = ap.parse_args()
    if a.action == "version":
        print("    version: pi_clab-1.0 (plain Docker; containerlab-compatible subset)")
        return
    if not a.topo:
        ap.error("-t/--topo is required")
    topo = load(pathlib.Path(a.topo))
    {"deploy": deploy, "destroy": destroy, "inspect": inspect}[a.action](topo)


if __name__ == "__main__":
    main()
