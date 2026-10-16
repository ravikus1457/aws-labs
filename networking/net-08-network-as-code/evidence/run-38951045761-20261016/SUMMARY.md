# N8 network-as-code: run evidence

**Overall: PASS**: 9 phases, 202 checks passed, 0 failed, 0 skipped. Generated 2026-10-16T18:58:34Z.

| Phase | Result | Pass | Fail | Skip |
|---|---|---|---|---|
| Pre-deploy validation of the source of truth (`validate`) | **PASS** | 1 | 0 | 0 |
| PR-style changes: good one accepted, bad one rejected (`change-demo`) | **PASS** | 2 | 0 | 0 |
| Rendered configs match the committed snapshot (`generate`) | **PASS** | 18 | 0 | 0 |
| Post-deploy setup (forwarding, ACLs, host addressing, listeners) (`post-deploy`) | **PASS** | 13 | 0 | 0 |
| Control plane converged to intent (`converge`) | **PASS** | 29 | 0 | 0 |
| Post-deploy tests (`test-initial`) | **PASS** | 46 | 0 | 0 |
| Drift: inject out-of-band changes, detect, heal (`drift-demo`) | **PASS** | 18 | 0 | 0 |
| Control plane re-converged after the heal (`converge-post-heal`) | **PASS** | 29 | 0 | 0 |
| Post-deploy tests again, after the heal (`test-post-heal`) | **PASS** | 46 | 0 | 0 |

## Measurements

| Measure | Value | Note |
|---|---|---|
| Control-plane convergence after config applied | 14.6 s | polled every 2 s, 7 polls |
| Reachability probes passed (test-initial) | 16/16 | 5 of them must-deny |
| Reachability probes passed (test-post-heal) | 16/16 | 5 of them must-deny |
| Out-of-band changes detected | 3/3 | |
| Heal (re-apply intent) | 0.9 s | {'br1-r1': 'frr-reload.py', 'br2-r1': 'frr-reload.py', 'hq-r1': 'frr-reload.py'} |
| Change `pr-041-add-branch3` | ACCEPT (expected ACCEPT) | - |
| Change `pr-042-add-branch3-overlap` | REJECT (expected REJECT) | IFACE_MULTI_SUBNET, OUTSIDE_BLOCK, SUBNET_OVERLAP |

## Pre-deploy validation of the source of truth (`validate`): PASS

| Result | Check | Subject | Expected | Observed | Detail |
|---|---|---|---|---|---|
| PASS | validate | network.yaml | ACCEPT | ACCEPT | 0 warning(s) |

## PR-style changes: good one accepted, bad one rejected (`change-demo`): PASS

| Result | Check | Subject | Expected | Observed | Detail |
|---|---|---|---|---|---|
| PASS | change-review | pr-041-add-branch3 | ACCEPT | ACCEPT | PR #41 add branch 3 - correct addressing |
| PASS | change-review | pr-042-add-branch3-overlap | REJECT with SUBNET_OVERLAP | REJECT (IFACE_MULTI_SUBNET, OUTSIDE_BLOCK, SUBNET_OVERLAP) | PR #42 add branch 3 - overlapping user LAN; generator refused to render: True |

## Rendered configs match the committed snapshot (`generate`): PASS

| Result | Check | Subject | Expected | Observed | Detail |
|---|---|---|---|---|---|
| PASS | rendered | acl/br1-r1.rules | snapshot == generator output | identical |  |
| PASS | rendered | acl/br2-r1.rules | snapshot == generator output | identical |  |
| PASS | rendered | acl/hq-r1.rules | snapshot == generator output | identical |  |
| PASS | rendered | acl/hq-r2.rules | snapshot == generator output | identical |  |
| PASS | rendered | configs/br1-r1/daemons | snapshot == generator output | identical |  |
| PASS | rendered | configs/br1-r1/frr.conf | snapshot == generator output | identical |  |
| PASS | rendered | configs/br1-r1/vtysh.conf | snapshot == generator output | identical |  |
| PASS | rendered | configs/br2-r1/daemons | snapshot == generator output | identical |  |
| PASS | rendered | configs/br2-r1/frr.conf | snapshot == generator output | identical |  |
| PASS | rendered | configs/br2-r1/vtysh.conf | snapshot == generator output | identical |  |
| PASS | rendered | configs/hq-r1/daemons | snapshot == generator output | identical |  |
| PASS | rendered | configs/hq-r1/frr.conf | snapshot == generator output | identical |  |
| PASS | rendered | configs/hq-r1/vtysh.conf | snapshot == generator output | identical |  |
| PASS | rendered | configs/hq-r2/daemons | snapshot == generator output | identical |  |
| PASS | rendered | configs/hq-r2/frr.conf | snapshot == generator output | identical |  |
| PASS | rendered | configs/hq-r2/vtysh.conf | snapshot == generator output | identical |  |
| PASS | rendered | plan.json | snapshot == generator output | identical |  |
| PASS | rendered | topology.clab.yml | snapshot == generator output | identical |  |

## Post-deploy setup (forwarding, ACLs, host addressing, listeners) (`post-deploy`): PASS

| Result | Check | Subject | Expected | Observed | Detail |
|---|---|---|---|---|---|
| PASS | setup | br1-r1 | ip_forward=1 | ok |  |
| PASS | setup | br1-r1 | iptables-restore acl/br1-r1.rules | ok |  |
| PASS | setup | br2-r1 | ip_forward=1 | ok |  |
| PASS | setup | br2-r1 | iptables-restore acl/br2-r1.rules | ok |  |
| PASS | setup | hq-r1 | ip_forward=1 | ok |  |
| PASS | setup | hq-r1 | iptables-restore acl/hq-r1.rules | ok |  |
| PASS | setup | hq-r2 | ip_forward=1 | ok |  |
| PASS | setup | hq-r2 | iptables-restore acl/hq-r2.rules | ok |  |
| PASS | setup | br1-pc1 | 10.20.10.10/24 gw 10.20.10.1 | ok |  |
| PASS | setup | br2-guest1 | 10.30.99.10/24 gw 10.30.99.1 | ok |  |
| PASS | setup | br2-pc1 | 10.30.10.10/24 gw 10.30.10.1 | ok |  |
| PASS | setup | hq-srv1 | 10.10.10.10/24 gw 10.10.10.1 | ok |  |
| PASS | setup | hq-srv1 | listening tcp/22,80 | tcp/22,80 |  |

## Control plane converged to intent (`converge`): PASS

| Result | Check | Subject | Expected | Observed | Detail |
|---|---|---|---|---|---|
| PASS | convergence | all routers | converged within 180 s | 14.6 s |  |
| PASS | bgp-session | br1-r1 -> hq-r2 (172.16.0.1) | Established, AS65000 | Established, AS65000 |  |
| PASS | bgp-policy | br1-r1 -> hq-r2 (172.16.0.1) | received 3, sent 1 | received 3, sent 1 |  |
| PASS | route | br1-r1 10.10.10.0/24 | bgp | bgp |  |
| PASS | route | br1-r1 10.20.10.0/24 | connected | connected |  |
| PASS | route | br1-r1 10.30.10.0/24 | bgp | bgp |  |
| PASS | route | br1-r1 10.30.99.0/24 | bgp | bgp |  |
| PASS | bgp-session | br2-r1 -> hq-r2 (172.16.0.5) | Established, AS65000 | Established, AS65000 |  |
| PASS | bgp-policy | br2-r1 -> hq-r2 (172.16.0.5) | received 2, sent 2 | received 2, sent 2 |  |
| PASS | route | br2-r1 10.10.10.0/24 | bgp | bgp |  |
| PASS | route | br2-r1 10.20.10.0/24 | bgp | bgp |  |
| PASS | route | br2-r1 10.30.10.0/24 | connected | connected |  |
| PASS | route | br2-r1 10.30.99.0/24 | connected | connected |  |
| PASS | ospf-adjacency | hq-r1 -> 10.255.0.2 via eth1 | Full | Full/- |  |
| PASS | route | hq-r1 10.10.10.0/24 | connected | connected |  |
| PASS | route | hq-r1 10.20.10.0/24 | ospf | ospf |  |
| PASS | route | hq-r1 10.255.0.2/32 | ospf | ospf |  |
| PASS | route | hq-r1 10.30.10.0/24 | ospf | ospf |  |
| PASS | route | hq-r1 10.30.99.0/24 | ospf | ospf |  |
| PASS | bgp-session | hq-r2 -> br1-r1 (172.16.0.2) | Established, AS65010 | Established, AS65010 |  |
| PASS | bgp-policy | hq-r2 -> br1-r1 (172.16.0.2) | received 1, sent 3 | received 1, sent 3 |  |
| PASS | bgp-session | hq-r2 -> br2-r1 (172.16.0.6) | Established, AS65020 | Established, AS65020 |  |
| PASS | bgp-policy | hq-r2 -> br2-r1 (172.16.0.6) | received 2, sent 2 | received 2, sent 2 |  |
| PASS | ospf-adjacency | hq-r2 -> 10.255.0.1 via eth1 | Full | Full/- |  |
| PASS | route | hq-r2 10.10.10.0/24 | ospf | ospf |  |
| PASS | route | hq-r2 10.20.10.0/24 | bgp | bgp |  |
| PASS | route | hq-r2 10.255.0.1/32 | ospf | ospf |  |
| PASS | route | hq-r2 10.30.10.0/24 | bgp | bgp |  |
| PASS | route | hq-r2 10.30.99.0/24 | bgp | bgp |  |

## Post-deploy tests (`test-initial`): PASS

| Result | Check | Subject | Expected | Observed | Detail |
|---|---|---|---|---|---|
| PASS | acl-installed | br1-r1 | jump + rules in intent order: acl:br1-no-ssh-servers | FORWARD -> NET08-ACL; rules: acl:br1-no-ssh-servers |  |
| PASS | acl-installed | br2-r1 | jump + rules in intent order: acl:guest-no-hq-servers, acl:guest-no-br1-users, acl:guest-no-br2-users | FORWARD -> NET08-ACL; rules: acl:guest-no-hq-servers, acl:guest-no-br1-users, acl:guest-no-br2-users |  |
| PASS | bgp-session | br1-r1 -> hq-r2 (172.16.0.1) | Established, AS65000 | Established, AS65000 |  |
| PASS | bgp-policy | br1-r1 -> hq-r2 (172.16.0.1) | received 3, sent 1 | received 3, sent 1 |  |
| PASS | route | br1-r1 10.10.10.0/24 | bgp | bgp |  |
| PASS | route | br1-r1 10.20.10.0/24 | connected | connected |  |
| PASS | route | br1-r1 10.30.10.0/24 | bgp | bgp |  |
| PASS | route | br1-r1 10.30.99.0/24 | bgp | bgp |  |
| PASS | bgp-session | br2-r1 -> hq-r2 (172.16.0.5) | Established, AS65000 | Established, AS65000 |  |
| PASS | bgp-policy | br2-r1 -> hq-r2 (172.16.0.5) | received 2, sent 2 | received 2, sent 2 |  |
| PASS | route | br2-r1 10.10.10.0/24 | bgp | bgp |  |
| PASS | route | br2-r1 10.20.10.0/24 | bgp | bgp |  |
| PASS | route | br2-r1 10.30.10.0/24 | connected | connected |  |
| PASS | route | br2-r1 10.30.99.0/24 | connected | connected |  |
| PASS | ospf-adjacency | hq-r1 -> 10.255.0.2 via eth1 | Full | Full/- |  |
| PASS | route | hq-r1 10.10.10.0/24 | connected | connected |  |
| PASS | route | hq-r1 10.20.10.0/24 | ospf | ospf |  |
| PASS | route | hq-r1 10.255.0.2/32 | ospf | ospf |  |
| PASS | route | hq-r1 10.30.10.0/24 | ospf | ospf |  |
| PASS | route | hq-r1 10.30.99.0/24 | ospf | ospf |  |
| PASS | bgp-session | hq-r2 -> br1-r1 (172.16.0.2) | Established, AS65010 | Established, AS65010 |  |
| PASS | bgp-policy | hq-r2 -> br1-r1 (172.16.0.2) | received 1, sent 3 | received 1, sent 3 |  |
| PASS | bgp-session | hq-r2 -> br2-r1 (172.16.0.6) | Established, AS65020 | Established, AS65020 |  |
| PASS | bgp-policy | hq-r2 -> br2-r1 (172.16.0.6) | received 2, sent 2 | received 2, sent 2 |  |
| PASS | ospf-adjacency | hq-r2 -> 10.255.0.1 via eth1 | Full | Full/- |  |
| PASS | route | hq-r2 10.10.10.0/24 | ospf | ospf |  |
| PASS | route | hq-r2 10.20.10.0/24 | bgp | bgp |  |
| PASS | route | hq-r2 10.255.0.1/32 | ospf | ospf |  |
| PASS | route | hq-r2 10.30.10.0/24 | bgp | bgp |  |
| PASS | route | hq-r2 10.30.99.0/24 | bgp | bgp |  |
| PASS | reachability | br1-pc1 -> br2-guest1 icmp | allow | reached | matrix |
| PASS | reachability | br1-pc1 -> br2-pc1 icmp | allow | reached | matrix |
| PASS | reachability | br1-pc1 -> hq-srv1 icmp | allow | reached | matrix |
| PASS | reachability | br2-guest1 -> br1-pc1 icmp | deny by guest-no-br1-users on br2-r1 | blocked (+3 pkts on guest-no-br1-users) | matrix |
| PASS | reachability | br2-guest1 -> br2-pc1 icmp | deny by guest-no-br2-users on br2-r1 | blocked (+3 pkts on guest-no-br2-users) | matrix |
| PASS | reachability | br2-guest1 -> hq-srv1 icmp | deny by guest-no-hq-servers on br2-r1 | blocked (+3 pkts on guest-no-hq-servers) | matrix |
| PASS | reachability | br2-pc1 -> br1-pc1 icmp | allow | reached | matrix |
| PASS | reachability | br2-pc1 -> br2-guest1 icmp | allow | reached | matrix |
| PASS | reachability | br2-pc1 -> hq-srv1 icmp | allow | reached | matrix |
| PASS | reachability | hq-srv1 -> br1-pc1 icmp | allow | reached | matrix |
| PASS | reachability | hq-srv1 -> br2-guest1 icmp | allow | reached | matrix |
| PASS | reachability | hq-srv1 -> br2-pc1 icmp | allow | reached | matrix |
| PASS | reachability | br1-pc1 -> hq-srv1 tcp/80 | allow | reached | declared |
| PASS | reachability | br1-pc1 -> hq-srv1 tcp/22 | deny by br1-no-ssh-servers on br1-r1 | blocked (+3 pkts on br1-no-ssh-servers) | declared |
| PASS | reachability | br2-pc1 -> hq-srv1 tcp/22 | allow | reached | declared |
| PASS | reachability | br2-guest1 -> hq-srv1 tcp/80 | deny by guest-no-hq-servers on br2-r1 | blocked (+3 pkts on guest-no-hq-servers) | declared |

## Drift: inject out-of-band changes, detect, heal (`drift-demo`): PASS

| Result | Check | Subject | Expected | Observed | Detail |
|---|---|---|---|---|---|
| PASS | drift-baseline | br1-r1 | running == intent | clean |  |
| PASS | drift-baseline | br2-r1 | running == intent | clean |  |
| PASS | drift-baseline | hq-r1 | running == intent | clean |  |
| PASS | drift-baseline | hq-r2 | running == intent | clean |  |
| PASS | drift-inject | br2-r1 | undocumented static route added by hand | ip route 203.0.113.0/24 blackhole |  |
| PASS | drift-inject | hq-r1 | OSPF cost tuned on the box during an incident | interface eth1 / ip ospf cost 444 |  |
| PASS | drift-inject | br1-r1 | a configured line removed on the box | router bgp 65010 / no neighbor 172.16.0.1 description |  |
| PASS | drift-detect | br2-r1 | extra: ip route 203.0.113.0/24 blackhole | reported |  |
| PASS | drift-detect | hq-r1 | extra: interface eth1 > ip ospf cost 444 | reported |  |
| PASS | drift-detect | br1-r1 | missing: router bgp 65010 > neighbor 172.16.0.1 description hq-r2 | reported |  |
| PASS | drift-precision | br1-r1 | no drift beyond what was injected | 0 unexplained |  |
| PASS | drift-precision | br2-r1 | no drift beyond what was injected | 0 unexplained |  |
| PASS | drift-precision | hq-r1 | no drift beyond what was injected | 0 unexplained |  |
| PASS | drift-precision | hq-r2 | no drift beyond what was injected | 0 unexplained |  |
| PASS | drift-heal | br1-r1 | running == intent after re-apply | clean | frr-reload.py |
| PASS | drift-heal | br2-r1 | running == intent after re-apply | clean | frr-reload.py |
| PASS | drift-heal | hq-r1 | running == intent after re-apply | clean | frr-reload.py |
| PASS | drift-heal | hq-r2 | running == intent after re-apply | clean | untouched |

## Control plane re-converged after the heal (`converge-post-heal`): PASS

| Result | Check | Subject | Expected | Observed | Detail |
|---|---|---|---|---|---|
| PASS | convergence | all routers | converged within 120 s | 0.4 s |  |
| PASS | bgp-session | br1-r1 -> hq-r2 (172.16.0.1) | Established, AS65000 | Established, AS65000 |  |
| PASS | bgp-policy | br1-r1 -> hq-r2 (172.16.0.1) | received 3, sent 1 | received 3, sent 1 |  |
| PASS | route | br1-r1 10.10.10.0/24 | bgp | bgp |  |
| PASS | route | br1-r1 10.20.10.0/24 | connected | connected |  |
| PASS | route | br1-r1 10.30.10.0/24 | bgp | bgp |  |
| PASS | route | br1-r1 10.30.99.0/24 | bgp | bgp |  |
| PASS | bgp-session | br2-r1 -> hq-r2 (172.16.0.5) | Established, AS65000 | Established, AS65000 |  |
| PASS | bgp-policy | br2-r1 -> hq-r2 (172.16.0.5) | received 2, sent 2 | received 2, sent 2 |  |
| PASS | route | br2-r1 10.10.10.0/24 | bgp | bgp |  |
| PASS | route | br2-r1 10.20.10.0/24 | bgp | bgp |  |
| PASS | route | br2-r1 10.30.10.0/24 | connected | connected |  |
| PASS | route | br2-r1 10.30.99.0/24 | connected | connected |  |
| PASS | ospf-adjacency | hq-r1 -> 10.255.0.2 via eth1 | Full | Full/- |  |
| PASS | route | hq-r1 10.10.10.0/24 | connected | connected |  |
| PASS | route | hq-r1 10.20.10.0/24 | ospf | ospf |  |
| PASS | route | hq-r1 10.255.0.2/32 | ospf | ospf |  |
| PASS | route | hq-r1 10.30.10.0/24 | ospf | ospf |  |
| PASS | route | hq-r1 10.30.99.0/24 | ospf | ospf |  |
| PASS | bgp-session | hq-r2 -> br1-r1 (172.16.0.2) | Established, AS65010 | Established, AS65010 |  |
| PASS | bgp-policy | hq-r2 -> br1-r1 (172.16.0.2) | received 1, sent 3 | received 1, sent 3 |  |
| PASS | bgp-session | hq-r2 -> br2-r1 (172.16.0.6) | Established, AS65020 | Established, AS65020 |  |
| PASS | bgp-policy | hq-r2 -> br2-r1 (172.16.0.6) | received 2, sent 2 | received 2, sent 2 |  |
| PASS | ospf-adjacency | hq-r2 -> 10.255.0.1 via eth1 | Full | Full/- |  |
| PASS | route | hq-r2 10.10.10.0/24 | ospf | ospf |  |
| PASS | route | hq-r2 10.20.10.0/24 | bgp | bgp |  |
| PASS | route | hq-r2 10.255.0.1/32 | ospf | ospf |  |
| PASS | route | hq-r2 10.30.10.0/24 | bgp | bgp |  |
| PASS | route | hq-r2 10.30.99.0/24 | bgp | bgp |  |

## Post-deploy tests again, after the heal (`test-post-heal`): PASS

| Result | Check | Subject | Expected | Observed | Detail |
|---|---|---|---|---|---|
| PASS | acl-installed | br1-r1 | jump + rules in intent order: acl:br1-no-ssh-servers | FORWARD -> NET08-ACL; rules: acl:br1-no-ssh-servers |  |
| PASS | acl-installed | br2-r1 | jump + rules in intent order: acl:guest-no-hq-servers, acl:guest-no-br1-users, acl:guest-no-br2-users | FORWARD -> NET08-ACL; rules: acl:guest-no-hq-servers, acl:guest-no-br1-users, acl:guest-no-br2-users |  |
| PASS | bgp-session | br1-r1 -> hq-r2 (172.16.0.1) | Established, AS65000 | Established, AS65000 |  |
| PASS | bgp-policy | br1-r1 -> hq-r2 (172.16.0.1) | received 3, sent 1 | received 3, sent 1 |  |
| PASS | route | br1-r1 10.10.10.0/24 | bgp | bgp |  |
| PASS | route | br1-r1 10.20.10.0/24 | connected | connected |  |
| PASS | route | br1-r1 10.30.10.0/24 | bgp | bgp |  |
| PASS | route | br1-r1 10.30.99.0/24 | bgp | bgp |  |
| PASS | bgp-session | br2-r1 -> hq-r2 (172.16.0.5) | Established, AS65000 | Established, AS65000 |  |
| PASS | bgp-policy | br2-r1 -> hq-r2 (172.16.0.5) | received 2, sent 2 | received 2, sent 2 |  |
| PASS | route | br2-r1 10.10.10.0/24 | bgp | bgp |  |
| PASS | route | br2-r1 10.20.10.0/24 | bgp | bgp |  |
| PASS | route | br2-r1 10.30.10.0/24 | connected | connected |  |
| PASS | route | br2-r1 10.30.99.0/24 | connected | connected |  |
| PASS | ospf-adjacency | hq-r1 -> 10.255.0.2 via eth1 | Full | Full/- |  |
| PASS | route | hq-r1 10.10.10.0/24 | connected | connected |  |
| PASS | route | hq-r1 10.20.10.0/24 | ospf | ospf |  |
| PASS | route | hq-r1 10.255.0.2/32 | ospf | ospf |  |
| PASS | route | hq-r1 10.30.10.0/24 | ospf | ospf |  |
| PASS | route | hq-r1 10.30.99.0/24 | ospf | ospf |  |
| PASS | bgp-session | hq-r2 -> br1-r1 (172.16.0.2) | Established, AS65010 | Established, AS65010 |  |
| PASS | bgp-policy | hq-r2 -> br1-r1 (172.16.0.2) | received 1, sent 3 | received 1, sent 3 |  |
| PASS | bgp-session | hq-r2 -> br2-r1 (172.16.0.6) | Established, AS65020 | Established, AS65020 |  |
| PASS | bgp-policy | hq-r2 -> br2-r1 (172.16.0.6) | received 2, sent 2 | received 2, sent 2 |  |
| PASS | ospf-adjacency | hq-r2 -> 10.255.0.1 via eth1 | Full | Full/- |  |
| PASS | route | hq-r2 10.10.10.0/24 | ospf | ospf |  |
| PASS | route | hq-r2 10.20.10.0/24 | bgp | bgp |  |
| PASS | route | hq-r2 10.255.0.1/32 | ospf | ospf |  |
| PASS | route | hq-r2 10.30.10.0/24 | bgp | bgp |  |
| PASS | route | hq-r2 10.30.99.0/24 | bgp | bgp |  |
| PASS | reachability | br1-pc1 -> br2-guest1 icmp | allow | reached | matrix |
| PASS | reachability | br1-pc1 -> br2-pc1 icmp | allow | reached | matrix |
| PASS | reachability | br1-pc1 -> hq-srv1 icmp | allow | reached | matrix |
| PASS | reachability | br2-guest1 -> br1-pc1 icmp | deny by guest-no-br1-users on br2-r1 | blocked (+3 pkts on guest-no-br1-users) | matrix |
| PASS | reachability | br2-guest1 -> br2-pc1 icmp | deny by guest-no-br2-users on br2-r1 | blocked (+3 pkts on guest-no-br2-users) | matrix |
| PASS | reachability | br2-guest1 -> hq-srv1 icmp | deny by guest-no-hq-servers on br2-r1 | blocked (+3 pkts on guest-no-hq-servers) | matrix |
| PASS | reachability | br2-pc1 -> br1-pc1 icmp | allow | reached | matrix |
| PASS | reachability | br2-pc1 -> br2-guest1 icmp | allow | reached | matrix |
| PASS | reachability | br2-pc1 -> hq-srv1 icmp | allow | reached | matrix |
| PASS | reachability | hq-srv1 -> br1-pc1 icmp | allow | reached | matrix |
| PASS | reachability | hq-srv1 -> br2-guest1 icmp | allow | reached | matrix |
| PASS | reachability | hq-srv1 -> br2-pc1 icmp | allow | reached | matrix |
| PASS | reachability | br1-pc1 -> hq-srv1 tcp/80 | allow | reached | declared |
| PASS | reachability | br1-pc1 -> hq-srv1 tcp/22 | deny by br1-no-ssh-servers on br1-r1 | blocked (+3 pkts on br1-no-ssh-servers) | declared |
| PASS | reachability | br2-pc1 -> hq-srv1 tcp/22 | allow | reached | declared |
| PASS | reachability | br2-guest1 -> hq-srv1 tcp/80 | deny by guest-no-hq-servers on br2-r1 | blocked (+3 pkts on guest-no-hq-servers) | declared |
