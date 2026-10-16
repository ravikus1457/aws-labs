#!/bin/sh
# keepalived track script (both cores). Succeeds only when OSPF has installed a route to the server LAN,
# i.e. this core can actually forward what its users send it. While it fails, keepalived subtracts 150
# from the VRRP priority, so a core that is still booting - or has lost its uplinks - never claims (or
# keeps) the gateway address. This is what `preempt delay` / `track object` do on Cisco/Arista gear.
# 188 = RTPROT_OSPF, the kernel route protocol id zebra stamps on OSPF routes (numeric: no rt_protos lookup).
ip -4 route show 10.100.0.0/24 proto 188 | grep -q via
