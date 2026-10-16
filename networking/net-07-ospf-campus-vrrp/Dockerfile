# Lab N7 core-router image: the pinned FRR image (OSPF) + keepalived (VRRPv3 first-hop redundancy).
# keepalived comes from the SAME Alpine release the FRR image is built on (3.22, community repo);
# the run records `keepalived --version` in the evidence. Build: docker build -t net07-core:local .
FROM quay.io/frrouting/frr:10.6.2
RUN apk add --no-cache 'keepalived~2.3' && keepalived --version 2>&1 | head -n 1
