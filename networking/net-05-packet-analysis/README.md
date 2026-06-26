# Net Lab 05 — Packet Capture & Traffic Analysis

**Goal:** Capture and dissect live traffic to understand the TCP handshake, DNS,
and TLS setup, and to troubleshoot latency.

## What it demonstrates
- `tcpdump` capture filters (BPF) and reading a capture in Wireshark
- TCP 3-way handshake (SYN, SYN-ACK, ACK), teardown (FIN/RST), retransmits
- DNS query/response and TLS ClientHello/ServerHello
- Latency/RTT analysis and spotting packet loss

## Key steps
1. `tcpdump -i any -w cap.pcap 'tcp port 443 or udp port 53'`.
2. Generate traffic (`curl https://example.com`); stop capture.
3. In Wireshark: follow the TCP stream, inspect the handshake, note RTT.
4. Filter `dns`, `tls.handshake`, `tcp.analysis.retransmission`.

## Be ready to explain
- Each step of the TCP handshake and what window size / MSS negotiate.
- How to read RTT and identify retransmissions / duplicate ACKs.
- What's visible (and not) in a TLS 1.3 handshake (SNI, encrypted cert).

> Study note: learn common BPF filters and the Wireshark columns for seq/ack/RTT.
