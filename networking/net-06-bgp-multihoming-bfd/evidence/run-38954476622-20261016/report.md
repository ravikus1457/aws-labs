# Lab N6 - BGP multihoming + BFD: measured results

Run: gha-38954476622 - started 2026-10-16T19:23:28Z - FRR quay.io/frrouting/frr:10.6.2 - containerlab 0.79.0 - kernel 6.17.0-1022-azure - probe every 10 ms per direction.

**Verdict: PASS** (73/73 checks passed)

## Failover: ISP-A breaks while traffic flows

Outage = user traffic lost (UDP probe every 10 ms, each direction). Detect = ent declared the ISP-A BGP session down. Reroute = FIB next hop moved to ISP-B (ent: outbound; inet: inbound). Upper bounds of the poll bracket; see result.json for brackets.

| Scenario | BFD | Outage out (ms) | Outage in (ms) | Detect (ms) | ent reroute (ms) | inet reroute (ms) | Failback loss out / in (ms) | Checks |
|---|---|---|---|---|---|---|---|---|
| ISP-A circuit black-holes (ports stay up), BFD on | up | 300.0 | 340.0 | 278.9 | 358.9 | 393.5 | 0.0 / 0.0 | PASS |
| ISP-A circuit black-holes (ports stay up), BFD off - BGP hold timer only | off | 28,840.1 | 28,889.7 | 28,832.1 | 28,891.2 | 28,951.0 | 0.0 / 0.0 | PASS |
| ent's ISP-A port goes down (carrier loss), BFD on | up | 59.7 | 420.0 | 44.0 | 115.9 | 476.4 | 0.0 / 0.0 | PASS |

## Every check

| Status | Check | Detail |
|---|---|---|
| PASS | `baseline.sessions.ent` | ent: 3/3 BGP sessions Established |
| PASS | `baseline.sessions.inet` | inet: 2/2 BGP sessions Established |
| PASS | `baseline.sessions.core` | core: 1/1 BGP sessions Established |
| PASS | `baseline.outbound_filter.10.0.1.1` | ent advertises exactly {203.0.113.0/24} to 10.0.1.1 (got ['203.0.113.0/24']) - no transit, no infra leak |
| PASS | `baseline.outbound_filter.10.0.2.1` | ent advertises exactly {203.0.113.0/24} to 10.0.2.1 (got ['203.0.113.0/24']) - no transit, no infra leak |
| PASS | `baseline.inbound_filter.10.0.1.1` | 10.0.1.1 sent 3/3 junk prefixes, ent accepted 0 of them ; legit routes missing: none |
| PASS | `baseline.inbound_filter.10.0.2.1` | 10.0.2.1 sent 2/2 junk prefixes, ent accepted 0 of them ; legit routes missing: none |
| PASS | `baseline.local_preference` | ent best path to 198.51.100.0/24: via 10.0.1.1 LP 200; backup via ISP-B LP 100 (want ISP-A/200 best, ISP-B/100 standby) |
| PASS | `baseline.default_route` | ent best default route via 10.0.1.1 (want 10.0.1.1) |
| PASS | `baseline.as_path_prepend` | inet best path to 203.0.113.0/24: via 10.1.1.1 AS-path [64501, 65010]; via ISP-B AS-path [64502, 65010, 65010, 65010] (want ISP-A best, 65010 x3 via ISP-B) |
| PASS | `baseline.bfd` | BFD sessions: {'10.0.1.1': 'up', '10.0.2.1': 'up', 'ispa->ent': 'up'} (want all up) |
| PASS | `baseline.fib` | ent FIB next hop for 198.51.100.0/24: ['10.0.1.1'] (want 10.0.1.1) |
| PASS | `baseline.ibgp_inside` | core has 198.51.100.0/24 installed via iBGP: next hops ['10.0.0.1'] |
| PASS | `baseline.path.h1->srv` | traceroute h1->srv goes via isp-a (hops ['203.0.113.1', '10.0.0.1', '10.0.1.1', '10.1.1.2', '198.51.100.10']; want isp-a) |
| PASS | `baseline.path.srv->h1` | traceroute srv->h1 goes via isp-a (hops ['198.51.100.1', '10.1.1.1', '10.0.1.2', '10.0.0.2', '203.0.113.10']; want isp-a) |
| PASS | `bfd.pre.sessions` | before the fault: ISP-A Established, ISP-B Established |
| PASS | `bfd.pre.bfd` | before the fault: BFD to ISP-A is up (want up) |
| PASS | `bfd.h1_to_srv.baseline_clean` | outbound h1->srv: 0/469 packets lost BEFORE the fault |
| PASS | `bfd.h1_to_srv.failover` | outbound h1->srv: traffic resumed while ISP-A was still broken (554 packets delivered over the backup path) |
| PASS | `bfd.h1_to_srv.outage_ms` | outbound h1->srv: loss window 300.0 ms (30 packets, longest run 300.0 ms; limit 1500 ms) |
| PASS | `bfd.h1_to_srv.failback_loss_ms` | outbound h1->srv: failback loss window 0.0 ms (0 packets; limit 1000 ms) |
| PASS | `bfd.srv_to_h1.baseline_clean` | inbound srv->h1: 0/464 packets lost BEFORE the fault |
| PASS | `bfd.srv_to_h1.failover` | inbound srv->h1: traffic resumed while ISP-A was still broken (551 packets delivered over the backup path) |
| PASS | `bfd.srv_to_h1.outage_ms` | inbound srv->h1: loss window 340.0 ms (34 packets, longest run 340.0 ms; limit 1500 ms) |
| PASS | `bfd.srv_to_h1.failback_loss_ms` | inbound srv->h1: failback loss window 0.0 ms (0 packets; limit 1000 ms) |
| PASS | `bfd.detect_ms` | ent declared the ISP-A session down 278.9 ms after the fault (bracket [244.4, 278.9]; expected 0..1000 ms) |
| PASS | `bfd.ent_reroute_ms` | ent FIB moved to the ISP-B path 358.9 ms after the fault (limit 1500 ms) |
| PASS | `bfd.ent_failback_ms` | ent FIB back on the ISP-A path 1676.3 ms after the repair (must happen and stay; limit 120 s) |
| PASS | `bfd.inet_reroute_ms` | inet FIB moved to the ISP-B path 393.5 ms after the fault (limit 1500 ms) |
| PASS | `bfd.inet_failback_ms` | inet FIB back on the ISP-A path 1789.2 ms after the repair (must happen and stay; limit 120 s) |
| PASS | `bfd.path_during.h1->srv` | traceroute h1->srv during the fault: isp-b (want isp-b; hops ['203.0.113.1', '10.0.0.1', '10.0.2.1', '10.1.2.2', '198.51.100.10']) |
| PASS | `bfd.path_during.srv->h1` | traceroute srv->h1 during the fault: isp-b (want isp-b; hops ['198.51.100.1', '10.1.2.1', '10.0.2.2', '10.0.0.2', '203.0.113.10']) |
| PASS | `bfd.path_after.h1->srv` | traceroute h1->srv after the fault: isp-a (want isp-a; hops ['203.0.113.1', '10.0.0.1', '10.0.1.1', '10.1.1.2', '198.51.100.10']) |
| PASS | `bfd.path_after.srv->h1` | traceroute srv->h1 after the fault: isp-a (want isp-a; hops ['198.51.100.1', '10.1.1.1', '10.0.1.2', '10.0.0.2', '203.0.113.10']) |
| PASS | `holdtimer.pre.sessions` | before the fault: ISP-A Established, ISP-B Established |
| PASS | `holdtimer.pre.bfd` | before the fault: BFD to ISP-A is not configured (want not running) |
| PASS | `holdtimer.h1_to_srv.baseline_clean` | outbound h1->srv: 0/468 packets lost BEFORE the fault |
| PASS | `holdtimer.h1_to_srv.failover` | outbound h1->srv: traffic resumed while ISP-A was still broken (548 packets delivered over the backup path) |
| PASS | `holdtimer.h1_to_srv.outage_ms` | outbound h1->srv: loss window 28840.1 ms (2884 packets, longest run 28840.1 ms; limit 35000 ms) |
| PASS | `holdtimer.h1_to_srv.failback_loss_ms` | outbound h1->srv: failback loss window 0.0 ms (0 packets; limit 1000 ms) |
| PASS | `holdtimer.srv_to_h1.baseline_clean` | inbound srv->h1: 0/464 packets lost BEFORE the fault |
| PASS | `holdtimer.srv_to_h1.failover` | inbound srv->h1: traffic resumed while ISP-A was still broken (543 packets delivered over the backup path) |
| PASS | `holdtimer.srv_to_h1.outage_ms` | inbound srv->h1: loss window 28889.7 ms (2889 packets, longest run 28889.7 ms; limit 35000 ms) |
| PASS | `holdtimer.srv_to_h1.failback_loss_ms` | inbound srv->h1: failback loss window 0.0 ms (0 packets; limit 1000 ms) |
| PASS | `holdtimer.detect_ms` | ent declared the ISP-A session down 28832.1 ms after the fault (bracket [28796.5, 28832.1]; expected 15000..32000 ms) |
| PASS | `holdtimer.ent_reroute_ms` | ent FIB moved to the ISP-B path 28891.2 ms after the fault (limit 35000 ms) |
| PASS | `holdtimer.ent_failback_ms` | ent FIB back on the ISP-A path 1694.6 ms after the repair (must happen and stay; limit 120 s) |
| PASS | `holdtimer.inet_reroute_ms` | inet FIB moved to the ISP-B path 28951.0 ms after the fault (limit 35000 ms) |
| PASS | `holdtimer.inet_failback_ms` | inet FIB back on the ISP-A path 1791.2 ms after the repair (must happen and stay; limit 120 s) |
| PASS | `holdtimer.path_during.h1->srv` | traceroute h1->srv during the fault: isp-b (want isp-b; hops ['203.0.113.1', '10.0.0.1', '10.0.2.1', '10.1.2.2', '198.51.100.10']) |
| PASS | `holdtimer.path_during.srv->h1` | traceroute srv->h1 during the fault: isp-b (want isp-b; hops ['198.51.100.1', '10.1.2.1', '10.0.2.2', '10.0.0.2', '203.0.113.10']) |
| PASS | `holdtimer.path_after.h1->srv` | traceroute h1->srv after the fault: isp-a (want isp-a; hops ['203.0.113.1', '10.0.0.1', '10.0.1.1', '10.1.1.2', '198.51.100.10']) |
| PASS | `holdtimer.path_after.srv->h1` | traceroute srv->h1 after the fault: isp-a (want isp-a; hops ['198.51.100.1', '10.1.1.1', '10.0.1.2', '10.0.0.2', '203.0.113.10']) |
| PASS | `linkdown.pre.sessions` | before the fault: ISP-A Established, ISP-B Established |
| PASS | `linkdown.pre.bfd` | before the fault: BFD to ISP-A is up (want up) |
| PASS | `linkdown.h1_to_srv.baseline_clean` | outbound h1->srv: 0/468 packets lost BEFORE the fault |
| PASS | `linkdown.h1_to_srv.failover` | outbound h1->srv: traffic resumed while ISP-A was still broken (587 packets delivered over the backup path) |
| PASS | `linkdown.h1_to_srv.outage_ms` | outbound h1->srv: loss window 59.7 ms (6 packets, longest run 59.7 ms; limit 1500 ms) |
| PASS | `linkdown.h1_to_srv.failback_loss_ms` | outbound h1->srv: failback loss window 0.0 ms (0 packets; limit 1000 ms) |
| PASS | `linkdown.srv_to_h1.baseline_clean` | inbound srv->h1: 0/463 packets lost BEFORE the fault |
| PASS | `linkdown.srv_to_h1.failover` | inbound srv->h1: traffic resumed while ISP-A was still broken (551 packets delivered over the backup path) |
| PASS | `linkdown.srv_to_h1.outage_ms` | inbound srv->h1: loss window 420.0 ms (42 packets, longest run 420.0 ms; limit 1500 ms) |
| PASS | `linkdown.srv_to_h1.failback_loss_ms` | inbound srv->h1: failback loss window 0.0 ms (0 packets; limit 1000 ms) |
| PASS | `linkdown.detect_ms` | ent declared the ISP-A session down 44.0 ms after the fault (bracket [0.0, 44.0]; expected 0..1000 ms) |
| PASS | `linkdown.ent_reroute_ms` | ent FIB moved to the ISP-B path 115.9 ms after the fault (limit 1500 ms) |
| PASS | `linkdown.ent_failback_ms` | ent FIB back on the ISP-A path 1288.9 ms after the repair (must happen and stay; limit 120 s) |
| PASS | `linkdown.inet_reroute_ms` | inet FIB moved to the ISP-B path 476.4 ms after the fault (limit 1500 ms) |
| PASS | `linkdown.inet_failback_ms` | inet FIB back on the ISP-A path 1382.6 ms after the repair (must happen and stay; limit 120 s) |
| PASS | `linkdown.path_during.h1->srv` | traceroute h1->srv during the fault: isp-b (want isp-b; hops ['203.0.113.1', '10.0.0.1', '10.0.2.1', '10.1.2.2', '198.51.100.10']) |
| PASS | `linkdown.path_during.srv->h1` | traceroute srv->h1 during the fault: isp-b (want isp-b; hops ['198.51.100.1', '10.1.2.1', '10.0.2.2', '10.0.0.2', '203.0.113.10']) |
| PASS | `linkdown.path_after.h1->srv` | traceroute h1->srv after the fault: isp-a (want isp-a; hops ['203.0.113.1', '10.0.0.1', '10.0.1.1', '10.1.1.2', '198.51.100.10']) |
| PASS | `linkdown.path_after.srv->h1` | traceroute srv->h1 after the fault: isp-a (want isp-a; hops ['198.51.100.1', '10.1.1.1', '10.0.1.2', '10.0.0.2', '203.0.113.10']) |
| PASS | `compare.bfd_speedup` | hold-timer-only outage 28,889.7 ms vs BFD 340.0 ms = 85.0x (want >= 5.0x) |

Raw evidence (probe logs, poller samples, vtysh JSON, traceroutes, FRR logs) is next to this file; `python3 measure.py report <dir> out.md` recomputes every number.
