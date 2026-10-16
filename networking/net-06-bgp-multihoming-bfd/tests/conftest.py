"""Shared helpers. Fixtures under fixtures/ are REAL evidence from a full local run of this lab
(FRR 10.6.2, the exact configs in configs/): baseline vtysh JSON verbatim, and each scenario with its
probe logs gzipped and its poller samples thinned to every state change plus 1 in 25 (thinning keeps
every sample adjacent to a change, so every measured number is identical to the unthinned run)."""

import gzip
import json
import os
import shutil
import sys

import pytest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(LAB, "tests", "fixtures")
sys.path.insert(0, LAB)


@pytest.fixture
def fixture_copy(tmp_path):
    """fixture_copy('real_bfd') -> a writable copy of that fixture directory."""

    def _copy(name):
        dst = tmp_path / name
        shutil.copytree(os.path.join(FIXTURES, name), dst)
        return str(dst)

    return _copy


def read_lines(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt") as fh:
            return fh.read().splitlines()
    with open(path) as fh:
        return fh.read().splitlines()


def write_lines(path, lines):
    data = "\n".join(lines) + "\n"
    if path.endswith(".gz"):
        with gzip.open(path, "wt") as fh:
            fh.write(data)
    else:
        with open(path, "w") as fh:
            fh.write(data)


def drop_received(scenario_dir, flow, t_from, t_to):
    """Delete receiver records for packets SENT in [t_from, t_to) - i.e. make them lost."""
    path = os.path.join(scenario_dir, "probes", f"{flow}.recv.gz")
    keep = []
    for line in read_lines(path):
        if line.startswith("#"):
            keep.append(line)
            continue
        t_send = float(line.split()[1])
        if not (t_from <= t_send < t_to):
            keep.append(line)
    write_lines(path, keep)


def edit_poll(scenario_dir, node, t_from, fn):
    """Apply fn(raw_text) -> raw_text to every poller sample started at/after t_from."""
    path = os.path.join(scenario_dir, "poll", f"{node}.jsonl.gz")
    out = []
    for line in read_lines(path):
        row = json.loads(line)
        if "t0" in row and row["t0"] >= t_from:
            row["out"] = [fn(t) for t in row["out"]]
        out.append(json.dumps(row))
    write_lines(path, out)


def meta(scenario_dir):
    with open(os.path.join(scenario_dir, "meta.json")) as fh:
        return json.load(fh)


def status(result, cid):
    found = [c["status"] for c in result["checks"] if c["id"] == cid]
    assert found, f"no check {cid} in {[c['id'] for c in result['checks']]}"
    return found[0]
