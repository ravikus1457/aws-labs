# Net Lab 01 — Site-to-Site WireGuard VPN (Raspberry Pi ↔ AWS VPC)

**Goal:** Establish an encrypted Site-to-Site tunnel between a Raspberry Pi home lab
and an AWS VPC so private subnets on both sides can route to each other.

## Topology
```
[Home LAN 192.168.1.0/24] -- Pi (wg0 10.10.0.1) ===WireGuard UDP/51820=== (wg0 10.10.0.2) EC2 -- [AWS VPC 10.0.0.0/16]
```

## What it demonstrates
- WireGuard peer config (keys, `AllowedIPs`, persistent keepalive)
- Linux routing: `ip route`, IP forwarding (`net.ipv4.ip_forward=1`)
- AWS side: route table entries, Security Group for UDP/51820, source/dest check off
- Crypto key management (Curve25519 keypairs) and least-privilege firewalling

## Key steps
1. `wg genkey | tee priv | wg pubkey > pub` on each peer.
2. `[Interface]` Address + PrivateKey + ListenPort; `[Peer]` PublicKey + Endpoint + AllowedIPs.
3. Enable forwarding; add routes to the remote subnet via `wg0`.
4. AWS: SG allow UDP 51820 from home IP; route 192.168.1.0/24 → ENI; disable src/dst check.
5. Verify: `wg show`, `ping` across subnets, `tcpdump -i wg0`.

## Be ready to explain
- Why WireGuard uses UDP and how `AllowedIPs` doubles as a routing + crypto filter.
- Difference between this and IPsec (handshake, code size, roaming).
- How return traffic is routed and why src/dst check must be disabled on the EC2 ENI.

> Study note: review the WireGuard handshake (Noise protocol) and NAT traversal before interviews.
