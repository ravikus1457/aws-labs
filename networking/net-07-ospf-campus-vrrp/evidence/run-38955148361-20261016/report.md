# Lab N7 - OSPF campus + VRRP gateway failover: measured results

Run: gha-38955148361 - started 2026-10-16T19:28:58Z - FRR quay.io/frrouting/frr:10.6.2 - Keepalived v2.3.1 (05/24,2024) - containerlab 0.79.0 - kernel 6.17.0-1022-azure - probe every 10 ms per direction.

**Verdict: PASS** (42/42 checks passed)

## Failover: core1 (VLAN 10 gateway master) loses power under traffic

| Measurement | Value (ms) |
|---|---|
| User traffic gap, VLAN 10 h10 -> srv (gateway moved to core2) | 640.0 |
| User traffic gap, VLAN 10 srv -> h10 (return path) | 0.0 |
| Control VLAN 20 h20 -> srv / srv -> h20 | 0.0 / 0.0 |
| VRRP takeover (core2 holds 10.10.10.1) | 745.7 |
| OSPF: dc drops the core1 adjacency | 99.7 |
| OSPF: dc route to 10.10.0.0/16 reconverged onto core2 | 99.7 |
| Failback: dc ECMP restored after core1 power-on | 14,347.6 |
| Preemption: core1 master again after its backbone links return | 10,501.2 |
| Loss during failback, h10 -> srv / srv -> h10 | 0.0 / 0.0 |

Control-plane times are upper bounds of the poll bracket (see result.json for both ends).

## Every check

| Status | Check | Detail |
|---|---|---|
| PASS | `baseline.ospf_full.dc` | dc: OSPF adjacencies Full with ['10.255.0.1', '10.255.0.2'] (want ['10.255.0.1', '10.255.0.2']) |
| PASS | `baseline.ospf_full.core1` | core1: OSPF adjacencies Full with ['10.255.0.2', '10.255.0.3'] (want ['10.255.0.2', '10.255.0.3']) |
| PASS | `baseline.ospf_full.core2` | core2: OSPF adjacencies Full with ['10.255.0.1', '10.255.0.3'] (want ['10.255.0.1', '10.255.0.3']) |
| PASS | `baseline.area1_summarised` | dc sees area 1 as 10.10.0.0/16: yes; leaked /24 specifics: none |
| PASS | `baseline.ecmp_fib` | dc FIB next hops for 10.10.0.0/16: ['10.0.11.1', '10.0.12.1'] (want both cores ['10.0.11.1', '10.0.12.1']) |
| PASS | `baseline.ecmp_dataplane` | kernel next hop per flow over 16 flows (L4 hash): {'10.0.11.1': 5, '10.0.12.1': 11} (want both cores used) |
| PASS | `baseline.vrrp_roles` | gateway VIPs: 10.10.10.1 on core1=True core2=False; 10.10.20.1 on core1=False core2=True (want VLAN 10 on core1 only, VLAN 20 on core2 only) |
| PASS | `baseline.gateway_arp.h10` | h10 resolves gateway 10.10.10.1 to aa:c1:ab:67:25:35 (the master's interface is aa:c1:ab:67:25:35) |
| PASS | `baseline.gateway_arp.h20` | h20 resolves gateway 10.10.20.1 to aa:c1:ab:e9:c5:b8 (the master's interface is aa:c1:ab:e9:c5:b8) |
| PASS | `baseline.first_hop.h10->srv` | traceroute h10->srv: first hop 10.10.10.2 (want 10.10.10.2), reaches 10.100.0.10 (hops ['10.10.10.2', '10.0.12.2', '10.100.0.10']) |
| PASS | `baseline.first_hop.h20->srv` | traceroute h20->srv: first hop 10.10.20.3 (want 10.10.20.3), reaches 10.100.0.10 (hops ['10.10.20.3', '10.0.11.2', '10.100.0.10']) |
| PASS | `baseline.reach.srv->h10` | traceroute srv->h10 reaches 10.10.10.10 via ['10.100.0.1', '10.0.12.1', '10.10.10.10'] |
| PASS | `master_kill.pre.core1_is_master` | before the fault 10.10.10.1 is on core1=True, core2=False (want core1 only) |
| PASS | `master_kill.pre.ecmp` | before the fault dc reaches 10.10.0.0/16 via ['10.0.11.1', '10.0.12.1'] (want both cores) |
| PASS | `master_kill.fault_effective` | core1 running right after the kill / just before the repair: [False, False] (want false/false: nothing restarted it behind the lab's back) |
| PASS | `master_kill.h10_to_srv.baseline_clean` | VLAN 10 h10->srv (its gateway master dies): 0/477 packets lost BEFORE the fault |
| PASS | `master_kill.h10_to_srv.failover` | VLAN 10 h10->srv (its gateway master dies): traffic resumed while core1 was still dead (542 packets delivered) |
| PASS | `master_kill.h10_to_srv.gap_ms` | VLAN 10 h10->srv (its gateway master dies): gap 640.0 ms (64 packets lost; limit 2000 ms) |
| PASS | `master_kill.h10_to_srv.failback_loss_ms` | VLAN 10 h10->srv (its gateway master dies): loss while core1 came back and took over again: 0.0 ms (0 packets; limit 1000 ms) |
| PASS | `master_kill.srv_to_h10.baseline_clean` | VLAN 10 srv->h10 (return path): 0/473 packets lost BEFORE the fault |
| PASS | `master_kill.srv_to_h10.failover` | VLAN 10 srv->h10 (return path): traffic resumed while core1 was still dead (614 packets delivered) |
| PASS | `master_kill.srv_to_h10.gap_ms` | VLAN 10 srv->h10 (return path): gap 0.0 ms (0 packets lost; limit 2000 ms) |
| PASS | `master_kill.srv_to_h10.failback_loss_ms` | VLAN 10 srv->h10 (return path): loss while core1 came back and took over again: 0.0 ms (0 packets; limit 1000 ms) |
| PASS | `master_kill.h20_to_srv.baseline_clean` | VLAN 20 h20->srv (control: gateway master survives): 0/469 packets lost BEFORE the fault |
| PASS | `master_kill.h20_to_srv.failover` | VLAN 20 h20->srv (control: gateway master survives): traffic resumed while core1 was still dead (614 packets delivered) |
| PASS | `master_kill.h20_to_srv.gap_ms` | VLAN 20 h20->srv (control: gateway master survives): gap 0.0 ms (0 packets lost; limit 1000 ms) |
| PASS | `master_kill.h20_to_srv.failback_loss_ms` | VLAN 20 h20->srv (control: gateway master survives): loss while core1 came back and took over again: 0.0 ms (0 packets; limit 1000 ms) |
| PASS | `master_kill.srv_to_h20.baseline_clean` | VLAN 20 srv->h20 (control, return path): 0/466 packets lost BEFORE the fault |
| PASS | `master_kill.srv_to_h20.failover` | VLAN 20 srv->h20 (control, return path): traffic resumed while core1 was still dead (614 packets delivered) |
| PASS | `master_kill.srv_to_h20.gap_ms` | VLAN 20 srv->h20 (control, return path): gap 0.0 ms (0 packets lost; limit 1000 ms) |
| PASS | `master_kill.srv_to_h20.failback_loss_ms` | VLAN 20 srv->h20 (control, return path): loss while core1 came back and took over again: 0.0 ms (0 packets; limit 1000 ms) |
| PASS | `master_kill.vrrp_takeover_ms` | core2 took over gateway 10.10.10.1 745.7 ms after the kill (bracket [724.2, 745.7]; limit 2000 ms) |
| PASS | `master_kill.ospf_neighbor_down_ms` | dc dropped its OSPF adjacency to core1 99.7 ms after the kill |
| PASS | `master_kill.ospf_reconverge_ms` | dc's route to 10.10.0.0/16 collapsed from ECMP to core2 only 99.7 ms after the kill (limit 2000 ms) |
| PASS | `master_kill.ecmp_restored_ms` | dc back to ECMP over both cores 14347.6 ms after core1 was powered on |
| PASS | `master_kill.preemption` | core1 (priority 200) took 10.10.10.1 back 10501.2 ms after its backbone links were re-cabled; core2 released it 18641.9 ms after core1 was powered on |
| PASS | `master_kill.preempt_waits_for_routing` | while core1 ran keepalived with no route to the servers (201 samples) it held the gateway in 0; samples with gateway but no route: 0 (want a tested gate: >= 3 samples, 0 and 0) |
| PASS | `master_kill.path_during.h10_srv` | traceroute h10->srv during the fault: ['10.10.10.3', '10.0.12.2', '10.100.0.10'] (want first hop 10.10.10.3 (core2)) |
| PASS | `master_kill.path_during.srv_h10` | traceroute srv->h10 during the fault: ['10.100.0.1', '10.0.12.1', '10.10.10.10'] (want via core2 only) |
| PASS | `master_kill.path_after.h10_srv` | traceroute h10->srv after the fault: ['10.10.10.2', '10.0.12.2', '10.100.0.10'] (want first hop 10.10.10.2 (core1 again)) |
| PASS | `master_kill.gateway_arp_during` | during the fault h10 resolves 10.10.10.1 to aa:c1:ab:e9:c5:b8; the core2 gateway interface is aa:c1:ab:e9:c5:b8 (gratuitous ARP moved the host) |
| PASS | `master_kill.gateway_arp_after` | after the fault h10 resolves 10.10.10.1 to aa:c1:ab:e2:aa:d0; the core1 gateway interface is aa:c1:ab:e2:aa:d0 (gratuitous ARP moved the host) |

Raw evidence (probe logs, poller samples, vtysh/iproute2 JSON, traceroutes, FRR and keepalived logs) is next to this file; `python3 measure.py report <dir> out.md` recomputes every number.
