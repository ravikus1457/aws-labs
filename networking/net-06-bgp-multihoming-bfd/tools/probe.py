#!/usr/bin/env python3
"""One-way UDP probe stream (sender + receiver). Standard library only.

Runs INSIDE the lab containers (bind-mounted at /lab/tools). Every container in a
containerlab topology shares the runner's kernel, so time.time() is the SAME clock in
the sender and the receiver: a packet's one-way fate (and the moment it was sent) is
known exactly, per direction, without NTP.

    probe.py send --dst IP --port N --flow NAME --interval-ms 10 --out FILE --stop FILE
    probe.py recv --port N --out FILE --stop FILE

Log formats (one line per packet, '#' lines are headers):
    sender    "<seq> <t_send> ok"          or "<seq> <t_send> err:<errno>"   (sendto failed)
    receiver  "<seq> <t_send> <t_recv> <src_ip>"

The sender keeps an absolute schedule (t0 + seq * interval). If it falls behind (CPU
stall) it does NOT burst to catch up; it skips the missed slots and the gap is visible
in the sender log, so a stalled sender can never masquerade as network loss.
Both ends stop when the stop file exists (or after --max-seconds, a safety net).
"""

import argparse
import os
import socket
import sys
import time

MAGIC = b"NETPROBE"


def _open_log(path, header):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fh = open(path, "w", buffering=1)  # line-buffered: a killed probe still leaves its log
    fh.write("# " + header + "\n")
    return fh


def send(args):
    interval = args.interval_ms / 1000.0
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    log = _open_log(
        args.out,
        f"probe send flow={args.flow} dst={args.dst}:{args.port} interval_ms={args.interval_ms} started={time.time():.6f}",
    )
    t0 = time.time()
    seq = 0
    deadline = t0 + args.max_seconds
    while True:
        now = time.time()
        if now >= deadline or os.path.exists(args.stop):
            break
        due = t0 + seq * interval
        if now < due:
            time.sleep(due - now)
            continue
        if now - due > interval:  # fell behind: skip missed slots instead of bursting
            seq = int((now - t0) / interval)
        t_send = time.time()
        payload = b"%s %s %d %.6f" % (MAGIC, args.flow.encode(), seq, t_send)
        try:
            sock.sendto(payload, (args.dst, args.port))
            log.write(f"{seq} {t_send:.6f} ok\n")
        except OSError as exc:  # e.g. ENETUNREACH while a route is withdrawn
            log.write(f"{seq} {t_send:.6f} err:{exc.errno}\n")
        seq += 1
    log.write(f"# stopped={time.time():.6f} sent={seq}\n")
    log.close()


def recv(args):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 21)
    sock.bind(("0.0.0.0", args.port))
    sock.settimeout(0.2)
    log = _open_log(args.out, f"probe recv port={args.port} started={time.time():.6f}")
    deadline = time.time() + args.max_seconds
    n = 0
    while time.time() < deadline:
        try:
            data, addr = sock.recvfrom(2048)
        except socket.timeout:
            if os.path.exists(args.stop):
                break
            continue
        t_recv = time.time()
        parts = data.split()
        if len(parts) != 4 or parts[0] != MAGIC:
            continue
        log.write(f"{int(parts[2])} {float(parts[3]):.6f} {t_recv:.6f} {addr[0]}\n")
        n += 1
        if n % 100 == 0 and os.path.exists(args.stop):
            break
    log.write(f"# stopped={time.time():.6f} received={n}\n")
    log.close()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="mode", required=True)
    s = sub.add_parser("send")
    s.add_argument("--dst", required=True)
    s.add_argument("--port", type=int, required=True)
    s.add_argument("--flow", required=True)
    s.add_argument("--interval-ms", type=float, default=10.0)
    s.add_argument("--out", required=True)
    s.add_argument("--stop", required=True)
    s.add_argument("--max-seconds", type=float, default=900.0)
    r = sub.add_parser("recv")
    r.add_argument("--port", type=int, required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--stop", required=True)
    r.add_argument("--max-seconds", type=float, default=960.0)
    args = p.parse_args(argv)
    if args.mode == "send":
        if args.interval_ms <= 0:
            p.error("--interval-ms must be > 0")
        send(args)
    else:
        recv(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
