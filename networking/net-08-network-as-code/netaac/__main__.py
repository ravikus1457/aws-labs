"""CLI: python -m netaac <command>. Run from the lab directory (paths default to it).

Offline (no Docker):  validate | change-demo | generate | check-rendered | report
Live (containerlab):  post-deploy | wait-converged | test | drift-check | drift-demo

Every phase command writes evidence/<phase>.json and exits 1 when any check FAILs.
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

import yaml

from . import generate as G
from . import intent as I
from . import live as L
from . import report as R
from .checks import FAIL, PASS, Check
from .lab import DockerLab
from .model import Net
from .validate import errors, summary, validate, validate_text

LAB = Path(__file__).resolve().parent.parent


def _phase_exit(doc: dict) -> int:
    print(f"[{doc['phase']}] {doc['status']}: {doc['counts']}")
    for c in doc["checks"]:
        if c["status"] == FAIL:
            print(f"  FAIL {c['category']} {c['subject']}: expected {c['expect']!r}, observed {c['observed']!r} {c['detail']}")
    return 0 if doc["status"] == PASS else 1


def _print_findings(findings) -> None:
    for f in findings:
        print(f"  {f.severity.upper():7} {f.code:22} {f.where}: {f.message}")


def cmd_validate(a) -> int:
    findings = validate_text(Path(a.intent).read_text(encoding="utf-8"))
    s = summary(findings, str(a.intent))
    print(f"{a.intent}: {s['verdict']} ({s['errors']} errors, {s['warnings']} warnings)")
    _print_findings(findings)
    checks = [
        Check(
            "validate",
            Path(a.intent).name,
            "ACCEPT",
            s["verdict"],
            PASS if s["verdict"] == "ACCEPT" else FAIL,
            ", ".join(s["codes"]) or f"{s['warnings']} warning(s)",
        )
    ]
    metrics = {"errors": s["errors"], "warnings": s["warnings"], "findings": s["findings"]}
    if s["verdict"] == "ACCEPT":
        net = Net(I.load(a.intent))
        metrics.update(
            routers=len(net.routers),
            hosts=len(net.hosts),
            subnets=len(net.subnets),
            acl_rules=len(net.acl),
            probes=len(net.probes),
            links=len(net.links()),
        )
    return _phase_exit(R.write_phase(a.evidence, "validate", checks, metrics))


def cmd_change_demo(a) -> int:
    base = I.load(a.intent)
    out_dir = Path(a.evidence) / "changes"
    out_dir.mkdir(parents=True, exist_ok=True)
    checks, results = [], {}
    files = sorted(Path(a.changes).glob("*.yaml"))
    for path in files:
        change = I.load(path)
        proposed = I.apply_change(copy.deepcopy(base), change)
        # The PR's version of the source of truth, as a reviewer would see it.
        (out_dir / f"{path.stem}.network.yaml").write_text(yaml.safe_dump(proposed, sort_keys=False), encoding="utf-8")
        findings = validate(proposed)
        s = summary(findings, path.name)
        want = change["expect"]
        want_codes = set(want.get("codes", []))
        ok = s["verdict"] == want["verdict"] and want_codes <= set(s["codes"])
        # A rejected change must never reach the generator: prove it refuses to render.
        refused = None
        if s["verdict"] == "REJECT":
            try:
                G.render(proposed)
                refused = False
            except G.InvalidIntent:
                refused = True
            ok = ok and refused
        print(f"{path.name}: {s['verdict']} (expected {want['verdict']}{' with ' + ', '.join(sorted(want_codes)) if want_codes else ''})")
        _print_findings(errors(findings))
        results[path.stem] = {"verdict": s["verdict"], "expected": want["verdict"], "codes": s["codes"], "generator_refused": refused}
        detail = change.get("title", "")
        if refused is not None:
            detail += f"; generator refused to render: {refused}"
        checks.append(
            Check(
                "change-review",
                path.stem,
                want["verdict"] + (f" with {', '.join(sorted(want_codes))}" if want_codes else ""),
                s["verdict"] + (f" ({', '.join(s['codes'])})" if s["codes"] else ""),
                PASS if ok else FAIL,
                detail,
            )
        )
    if not files:
        checks.append(Check("change-review", str(a.changes), "at least one change file", "none found", FAIL))
    return _phase_exit(R.write_phase(a.evidence, "change-demo", checks, {"changes": results}))


def cmd_generate(a) -> int:
    try:
        files = G.render(I.load(a.intent))
    except G.InvalidIntent as exc:
        print("REFUSING to render invalid intent:")
        _print_findings(exc.findings)
        return 2
    G.write(files, Path(a.out))
    print(f"rendered {len(files)} files into {a.out}")
    return 0


def cmd_check_rendered(a) -> int:
    files = G.render(I.load(a.intent))
    root = Path(a.rendered)
    on_disk = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}
    checks = []
    for rel in sorted(set(files) | on_disk):
        if rel not in files:
            checks.append(Check("rendered", rel, "generated", "stale file in snapshot", FAIL))
        elif rel not in on_disk:
            checks.append(Check("rendered", rel, "committed", "missing from snapshot", FAIL))
        else:
            same = (root / rel).read_text(encoding="utf-8") == files[rel]
            checks.append(Check("rendered", rel, "snapshot == generator output", "identical" if same else "DIFFERS", PASS if same else FAIL))
    doc = R.write_phase(a.evidence, "generate", checks, {"files": len(files)})
    if doc["status"] != PASS:
        print(f"rendered/ is out of date: run `python -m netaac generate --out {a.rendered}` and commit the diff")
    return _phase_exit(doc)


def _live(a) -> tuple[Net, DockerLab]:
    net = Net(I.load(a.intent))
    return net, DockerLab(net.name)


def cmd_post_deploy(a) -> int:
    net, lab = _live(a)
    return _phase_exit(R.write_phase(a.evidence, "post-deploy", L.post_deploy(net, lab, Path(a.build))))


def cmd_wait(a) -> int:
    net, lab = _live(a)
    seconds, snap, polls = L.wait_converged(net, lab, timeout=a.timeout, interval=a.interval)
    head = Check(
        "convergence",
        "all routers",
        f"converged within {a.timeout:.0f} s",
        f"{seconds} s" if seconds is not None else "timed out",
        PASS if seconds is not None else FAIL,
    )
    metrics = {"seconds": seconds, "polls": polls, "interval": a.interval, "timeout": a.timeout}
    return _phase_exit(R.write_phase(a.evidence, a.phase, [head, *snap], metrics))


def cmd_test(a) -> int:
    net, lab = _live(a)
    results = L.run_tests(net, lab)
    probes = [c for c in results if c.category == "reachability"]
    metrics = {
        "probes_total": len(probes),
        "probes_passed": sum(c.status == PASS for c in probes),
        "probes_deny": sum(p.expect == "deny" for p in net.probes),
    }
    return _phase_exit(R.write_phase(a.evidence, a.phase, results, metrics))


def cmd_drift_check(a) -> int:
    net, lab = _live(a)
    drifts = L.drift_check(net, lab, Path(a.build))
    checks = [
        Check("drift", r, "running == intent", "clean" if d.clean else "DRIFT", PASS if d.clean else FAIL, "; ".join(d.missing + d.extra)[:400])
        for r, d in drifts.items()
    ]
    return _phase_exit(R.write_phase(a.evidence, a.phase, checks, {"drift": {r: d.as_dict() for r, d in drifts.items()}}))


def cmd_drift_demo(a) -> int:
    net, lab = _live(a)
    checks, metrics = L.drift_demo(net, lab, Path(a.build))
    return _phase_exit(R.write_phase(a.evidence, "drift-demo", checks, metrics))


def cmd_report(a) -> int:
    required = [p for p in a.require.split(",") if p]
    rep = R.write_report(Path(a.evidence), required)
    print(f"overall {rep['overall']} totals={rep['totals']} missing={rep['missing_phases']}")
    return 0 if rep["overall"] == PASS else 1


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--intent", default=str(LAB / "intent" / "network.yaml"), help="source of truth")
    common.add_argument("--evidence", default=str(LAB / "evidence" / "local"), help="where phase JSON goes")
    common.add_argument("--build", default=str(LAB / "build"), help="rendered output used by the live lab")
    p = argparse.ArgumentParser(prog="netaac", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    _add = sub.add_parser

    def add(name):
        return _add(name, parents=[common])

    add("validate").set_defaults(fn=cmd_validate)
    s = add("change-demo")
    s.add_argument("--changes", default=str(LAB / "changes"))
    s.set_defaults(fn=cmd_change_demo)
    s = add("generate")
    s.add_argument("--out", default=str(LAB / "build"))
    s.set_defaults(fn=cmd_generate)
    s = add("check-rendered")
    s.add_argument("--rendered", default=str(LAB / "rendered"))
    s.set_defaults(fn=cmd_check_rendered)
    add("post-deploy").set_defaults(fn=cmd_post_deploy)
    s = add("wait-converged")
    s.add_argument("--timeout", type=float, default=180)
    s.add_argument("--interval", type=float, default=2)
    s.add_argument("--phase", default="converge")
    s.set_defaults(fn=cmd_wait)
    s = add("test")
    s.add_argument("--phase", default="test-initial")
    s.set_defaults(fn=cmd_test)
    s = add("drift-check")
    s.add_argument("--phase", default="drift-check")
    s.set_defaults(fn=cmd_drift_check)
    add("drift-demo").set_defaults(fn=cmd_drift_demo)
    s = add("report")
    s.add_argument("--require", default="")
    s.set_defaults(fn=cmd_report)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
