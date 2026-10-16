# pi_clab.py: run these labs on a Raspberry Pi (plain Docker)

GitHub's runners use **containerlab**. On a Raspberry Pi with a 6.18 kernel, containerlab 0.81 refuses to start
("Kernel module ip_tables is not loaded"): the Pi kernel dropped the legacy module and containerlab checks for it
even with nftables and `external-access: false`. Docker itself works, so `pi_clab.py` builds the same topology
from the same `.clab.yml` with plain Docker:

| containerlab | pi_clab |
|---|---|
| node container `clab-<lab>-<node>` | same name, privileged, IP forwarding on |
| mgmt network (eth0) | per-lab Docker bridge, no default route off the lab |
| link (veth pair) | one Docker bridge per link, NAT off; Docker's IP flushed so the router config owns addressing |
| interface names | renamed in-container to the topology's names (eth1, eth2...) by MAC |
| startup | image entrypoint started after wiring, then each node's `exec:` |

**Run a whole lab on the Pi** (the same `run.sh` CI runs; `pi-containerlab` is a drop-in for `containerlab`):
```
cd networking/net-06-bgp-multihoming-bfd
CLAB=$HOME/aws-devops-labs/networking/tools/pi-containerlab SUDO= sg docker -c ./run.sh
```
Verified 2026-10-16 on the Pi 5: **N6 PASS, 0 failed** (BFD 340 ms vs hold timer 28.1 s = 82.7x; port-down 72/360 ms),
destroy left 0 containers / 0 networks. N7 needs `containerlab tools veth` (re-cabling a restarted node) and N8 needs
passwordless `sudo nsenter` for its ACLs, so those two stay CI-only for now.

Low-level:
```
sg docker -c "python3 networking/tools/pi_clab.py deploy  -t networking/<lab>/<lab>.clab.yml"
sg docker -c "python3 networking/tools/pi_clab.py inspect -t ..."
sg docker -c "python3 networking/tools/pi_clab.py destroy -t ..."
```
(`sg docker` only until your next login adds the docker group.)

Verified 2026-10-16 on a Pi 5 (Docker 26.1.5, FRR 10.1.1): a 2-router OSPF topology reached Full adjacency,
loopback routes were learned and pinged; destroy left 0 containers and 0 networks.

Two traps it already handles: Docker refuses extra networks on a `--network none` container (hence the mgmt
bridge), and Docker `--internal` networks drop every packet outside the network's own subnet (100% loss on the
lab's 10.x addressing), so links are normal bridges with masquerade off.
