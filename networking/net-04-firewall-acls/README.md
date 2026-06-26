# Net Lab 04 — Stateful Firewall & Least-Privilege ACLs

**Goal:** Build a default-deny stateful firewall on Linux and mirror the policy
in AWS Security Groups + Network ACLs.

## What it demonstrates
- nftables/iptables stateful rules (`ct state established,related accept`)
- Default-deny posture with explicit allow-lists
- AWS Security Groups (stateful) vs Network ACLs (stateless) — and when each matters
- Least-privilege: open only required ports per tier

## Key steps
1. nftables base chains: drop policy, allow loopback, allow established/related.
2. Allow inbound 22 from mgmt CIDR only, 443 from anywhere, log+drop the rest.
3. AWS: SG allows 443 from 0.0.0.0/0, 22 from your IP; NACL adds subnet-wide deny.
4. Verify with `nmap` from inside/outside; confirm stateful return traffic works.

## Be ready to explain
- Stateful vs stateless filtering and why NACLs need explicit return-traffic rules.
- SG (instance, stateful, allow-only) vs NACL (subnet, stateless, allow+deny).
- Why default-deny + allow-list beats default-allow + block-list.

> Study note: practice writing an nftables ruleset from scratch and reading AWS SG/NACL evaluation order.
