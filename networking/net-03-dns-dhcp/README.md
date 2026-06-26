# Net Lab 03 — DNS & DHCP Services on Linux

**Goal:** Stand up authoritative DNS + DHCP for a lab network and trace a full
name-resolution path.

## What it demonstrates
- DHCP scopes, leases, reservations, option 6/15 (DNS/domain)
- Authoritative + recursive DNS (dnsmasq or BIND9), forward + reverse zones
- The resolution path: stub resolver → recursive → root → TLD → authoritative
- Troubleshooting with `dig`, `nslookup`, `journalctl`

## Key steps
1. dnsmasq: `dhcp-range=10.20.0.50,10.20.0.200,12h`; `dhcp-option=6,<dns>`.
2. Define A/PTR records; enable a forward zone + reverse (in-addr.arpa).
3. Point clients at the server; renew lease; confirm `dig lab.local` + reverse `dig -x`.
4. Break it on purpose (wrong forwarder) and diagnose with `dig +trace`.

## Be ready to explain
- Recursive vs authoritative vs forwarding resolvers.
- TTL, caching, and why a stale record lingers; DHCP DORA exchange.
- Forward vs reverse zones and where PTR records matter (mail, logging).

> Study note: memorize the DORA (Discover/Offer/Request/Ack) flow and `dig +trace` output.
