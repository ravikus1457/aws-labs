# Net Lab 02 — Subnetting & VLAN Segmentation

**Goal:** Design a VLSM addressing plan for a small office and segment broadcast
domains with VLANs + inter-VLAN routing.

## What it demonstrates
- VLSM subnet design (efficient allocation, no overlap)
- 802.1Q VLAN tagging, access vs trunk ports
- Inter-VLAN routing (router-on-a-stick / L3 switch SVIs)
- Broadcast domain isolation and its security benefit

## Sample plan (10.20.0.0/22)
| VLAN | Purpose | Subnet | Usable hosts |
|------|---------|--------|--------------|
| 10 | Staff | 10.20.0.0/24 | 254 |
| 20 | VoIP | 10.20.1.0/25 | 126 |
| 30 | Servers | 10.20.1.128/26 | 62 |
| 99 | Mgmt | 10.20.1.192/28 | 14 |

## Key steps
1. Allocate subnets largest-first to avoid fragmentation.
2. Configure access ports per VLAN; trunk (802.1Q) to the router/L3 switch.
3. Create SVIs / sub-interfaces as default gateways; enable routing.
4. Verify isolation (`ping` across VLANs blocked until routed) and DHCP per VLAN.

## Be ready to explain
- Why segment with VLANs (broadcast control, blast radius, policy boundaries).
- Trunk vs access ports and the native VLAN gotcha.
- How a /26 vs /24 changes host count and why VoIP gets its own VLAN (QoS).

> Study note: practice subnet math by hand (CIDR, host counts, wildcard masks).
