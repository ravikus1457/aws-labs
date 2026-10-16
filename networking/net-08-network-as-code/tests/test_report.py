"""Evidence aggregation: absence is never green."""

from netaac import report as R
from netaac.checks import FAIL, PASS, Check


def ok(subject="x"):
    return Check("cat", subject, "a", "a", PASS)


def test_phase_with_no_checks_fails(tmp_path):
    assert R.write_phase(tmp_path, "test-initial", [])["status"] == FAIL


def test_missing_required_phase_fails_overall(tmp_path):
    R.write_phase(tmp_path, "validate", [ok()])
    rep = R.write_report(tmp_path, ["validate", "drift-demo"])
    assert rep["overall"] == FAIL and rep["missing_phases"] == ["drift-demo"]
    assert "Missing phases" in (tmp_path / "SUMMARY.md").read_text()


def test_one_fail_fails_overall_and_is_listed_first(tmp_path):
    R.write_phase(tmp_path, "validate", [ok()])
    R.write_phase(tmp_path, "test-initial", [ok("a"), Check("route", "r1 | p", "bgp", "static", FAIL, "pipe | inside")])
    rep = R.write_report(tmp_path, ["validate", "test-initial"])
    assert rep["overall"] == FAIL
    md = (tmp_path / "SUMMARY.md").read_text()
    section = md.split("(`test-initial`): FAIL")[1]
    assert section.index("| FAIL |") < section.index("| PASS |")
    assert "r1 \\| p" in md


def test_all_pass(tmp_path):
    for ph in ("validate", "change-demo", "converge"):
        R.write_phase(tmp_path, ph, [ok()], {"seconds": 12.0, "polls": 6, "interval": 2} if ph == "converge" else None)
    rep = R.write_report(tmp_path, ["validate", "change-demo", "converge"])
    assert rep["overall"] == PASS
    assert "| Control-plane convergence after config applied | 12.0 s |" in (tmp_path / "SUMMARY.md").read_text()
