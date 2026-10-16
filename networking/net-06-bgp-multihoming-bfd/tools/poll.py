#!/usr/bin/env python3
"""Sample a router's state as fast as it will answer, with a timestamp around every sample.

Runs INSIDE a lab container (bind-mounted at /lab/tools). Standard library only.

    poll.py --out FILE --stop FILE --cmd "vtysh -c 'show ip route 198.51.100.0/24 json'" [--cmd ...]

Each --cmd is split with shlex and executed without a shell. Every sample is one JSON line:

    {"t0": <epoch before the first command>, "t1": <epoch after the last>,
     "out": [<stdout of cmd 1>, <stdout of cmd 2>, ...], "rc": [<exit codes>]}

Raw stdout is kept (not parsed here) so the evidence is exactly what the router said;
measure.py parses it. The state in a sample was true at some instant inside [t0, t1],
so a state change is bracketed by (previous sample's t0, this sample's t1].
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import time


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--stop", required=True)
    p.add_argument("--cmd", action="append", required=True)
    p.add_argument("--min-interval-ms", type=float, default=20.0)
    p.add_argument("--max-seconds", type=float, default=900.0)
    p.add_argument("--timeout", type=float, default=5.0)
    args = p.parse_args(argv)

    cmds = [shlex.split(c) for c in args.cmd]
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    deadline = time.time() + args.max_seconds
    period = args.min_interval_ms / 1000.0
    with open(args.out, "w", buffering=1) as fh:
        fh.write(json.dumps({"header": {"cmds": args.cmd, "started": time.time()}}) + "\n")
        while time.time() < deadline and not os.path.exists(args.stop):
            t0 = time.time()
            outs, rcs = [], []
            for c in cmds:
                try:
                    r = subprocess.run(c, capture_output=True, text=True, timeout=args.timeout)
                    outs.append(r.stdout)
                    rcs.append(r.returncode)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    outs.append("")
                    rcs.append(f"error:{exc.__class__.__name__}")
            t1 = time.time()
            fh.write(json.dumps({"t0": round(t0, 6), "t1": round(t1, 6), "out": outs, "rc": rcs}) + "\n")
            spare = period - (time.time() - t0)
            if spare > 0:
                time.sleep(spare)
    return 0


if __name__ == "__main__":
    sys.exit(main())
