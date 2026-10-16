"""Evidence: one JSON file per phase, then an aggregate JSON + markdown with PASS/FAIL per check.

A phase with zero checks is a FAIL ("ran and found nothing to check" is not a pass), and a
phase the run was supposed to produce but did not is a FAIL too. Absence is never green.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .checks import FAIL, PASS, SKIP, Check

PHASE_TITLES = {
    "validate": "Pre-deploy validation of the source of truth",
    "change-demo": "PR-style changes: good one accepted, bad one rejected",
    "generate": "Rendered configs match the committed snapshot",
    "post-deploy": "Post-deploy setup (forwarding, ACLs, host addressing, listeners)",
    "converge": "Control plane converged to intent",
    "test-initial": "Post-deploy tests",
    "drift-demo": "Drift: inject out-of-band changes, detect, heal",
    "converge-post-heal": "Control plane re-converged after the heal",
    "test-post-heal": "Post-deploy tests again, after the heal",
}


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def phase_doc(phase: str, checks: list[Check], metrics: dict | None = None) -> dict:
    counts = {s: sum(1 for c in checks if c.status == s) for s in (PASS, FAIL, SKIP)}
    status = PASS if checks and counts[FAIL] == 0 else FAIL
    return {
        "phase": phase,
        "title": PHASE_TITLES.get(phase, phase),
        "generated_at": _now(),
        "status": status,
        "counts": counts,
        "metrics": metrics or {},
        "checks": [c.as_dict() for c in checks],
    }


def write_phase(evidence: Path, phase: str, checks: list[Check], metrics: dict | None = None) -> dict:
    doc = phase_doc(phase, checks, metrics)
    evidence = Path(evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / f"{phase}.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return doc


def aggregate(evidence: Path, required: list[str]) -> dict:
    evidence = Path(evidence)
    phases: dict[str, dict] = {}
    for path in sorted(evidence.glob("*.json")):
        if path.name == "report.json":
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(doc, dict) and "phase" in doc and "checks" in doc:
            phases[doc["phase"]] = doc
    missing = [p for p in required if p not in phases]
    order = [p for p in PHASE_TITLES if p in phases] + sorted(p for p in phases if p not in PHASE_TITLES)
    totals = {s: sum(phases[p]["counts"][s] for p in phases) for s in (PASS, FAIL, SKIP)}
    overall = PASS if not missing and phases and all(phases[p]["status"] == PASS for p in phases) else FAIL
    return {
        "generated_at": _now(),
        "overall": overall,
        "required_phases": required,
        "missing_phases": missing,
        "totals": totals,
        "phases": [phases[p] for p in order],
    }


def _cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _metrics_lines(report: dict) -> list[str]:
    m = {p["phase"]: p.get("metrics", {}) for p in report["phases"]}
    lines = []
    if "converge" in m and "seconds" in m["converge"]:
        sec = m["converge"]["seconds"]
        lines.append(
            f"| Control-plane convergence after config applied | {sec if sec is not None else 'did not converge'} s "
            f"| polled every {m['converge'].get('interval', '?')} s, {m['converge'].get('polls', '?')} polls |"
        )
    for ph in ("test-initial", "test-post-heal"):
        if ph in m and "probes_total" in m[ph]:
            x = m[ph]
            lines.append(f"| Reachability probes passed ({ph}) | {x['probes_passed']}/{x['probes_total']} | {x['probes_deny']} of them must-deny |")
    if "drift-demo" in m and "drift_injected" in m["drift-demo"]:
        x = m["drift-demo"]
        lines.append(f"| Out-of-band changes detected | {x['drift_detected']}/{x['drift_injected']} | |")
        lines.append(f"| Heal (re-apply intent) | {x.get('heal_seconds', '?')} s | {_cell(x.get('heal_method', {}))} |")
    if "change-demo" in m and "changes" in m["change-demo"]:
        for name, v in m["change-demo"]["changes"].items():
            lines.append(f"| Change `{name}` | {v['verdict']} (expected {v['expected']}) | {_cell(', '.join(v['codes']) or '-')} |")
    return lines


def markdown(report: dict) -> str:
    t = report["totals"]
    out = [
        "# N8 network-as-code: run evidence",
        "",
        f"**Overall: {report['overall']}**: {len(report['phases'])} phases, "
        f"{t[PASS]} checks passed, {t[FAIL]} failed, {t[SKIP]} skipped. Generated {report['generated_at']}.",
        "",
    ]
    if report["missing_phases"]:
        out += [f"**Missing phases (counted as FAIL):** {', '.join(report['missing_phases'])}", ""]
    out += ["| Phase | Result | Pass | Fail | Skip |", "|---|---|---|---|---|"]
    for p in report["phases"]:
        c = p["counts"]
        out.append(f"| {_cell(p['title'])} (`{p['phase']}`) | **{p['status']}** | {c[PASS]} | {c[FAIL]} | {c[SKIP]} |")
    lines = _metrics_lines(report)
    if lines:
        out += ["", "## Measurements", "", "| Measure | Value | Note |", "|---|---|---|", *lines]
    for p in report["phases"]:
        out += ["", f"## {p['title']} (`{p['phase']}`): {p['status']}", ""]
        out += ["| Result | Check | Subject | Expected | Observed | Detail |", "|---|---|---|---|---|---|"]
        rows = sorted(p["checks"], key=lambda c: {FAIL: 0, SKIP: 1, PASS: 2}[c["status"]])
        for c in rows:
            cells = [c["status"], *(_cell(c[k]) for k in ("category", "subject", "expect", "observed", "detail"))]
            out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def write_report(evidence: Path, required: list[str]) -> dict:
    report = aggregate(evidence, required)
    Path(evidence).mkdir(parents=True, exist_ok=True)
    (Path(evidence) / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (Path(evidence) / "SUMMARY.md").write_text(markdown(report), encoding="utf-8")
    return report
