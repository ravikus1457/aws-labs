"""The PR-style change demo: the control change is accepted, the overlapping one rejected
(for the right reason), and a rejected change can never reach the generator."""

import json

import pytest

from netaac import generate, intent
from netaac.__main__ import main
from netaac.validate import errors, validate


def load_change(lab_dir, name):
    return intent.load(lab_dir / "changes" / name)


def test_apply_change_merges_appends_and_deletes(data):
    change = {
        "merge": {"hosts": {"br1-pc1": None, "x": {"subnet": "br1-users", "ip": "10.20.10.20"}}},
        "append": {"bgp_sessions": [["a", "b"]]},
    }
    out = intent.apply_change(data, change)
    assert "br1-pc1" not in out["hosts"] and "x" in out["hosts"]
    assert out["bgp_sessions"][-1] == ["a", "b"] and len(out["bgp_sessions"]) == len(data["bgp_sessions"]) + 1
    assert "br1-pc1" in data["hosts"], "the base must not be mutated - the change applies to a COPY"


def test_good_change_is_accepted_and_renders(lab_dir, data):
    proposed = intent.apply_change(data, load_change(lab_dir, "pr-041-add-branch3.yaml"))
    assert errors(validate(proposed)) == []
    files = generate.render(proposed)
    assert "configs/br3-r1/frr.conf" in files
    assert "neighbor 172.16.0.10 remote-as 65030" in files["configs/hq-r2/frr.conf"]


def test_overlapping_change_is_rejected_for_overlap(lab_dir, data):
    proposed = intent.apply_change(data, load_change(lab_dir, "pr-042-add-branch3-overlap.yaml"))
    codes = {f.code for f in errors(validate(proposed))}
    assert "SUBNET_OVERLAP" in codes
    with pytest.raises(generate.InvalidIntent):
        generate.render(proposed)


def test_change_demo_cli_passes(lab_dir, tmp_path):
    rc = main(["change-demo", "--evidence", str(tmp_path)])
    doc = json.loads((tmp_path / "change-demo.json").read_text())
    assert rc == 0 and doc["status"] == "PASS"
    assert doc["metrics"]["changes"]["pr-042-add-branch3-overlap"]["generator_refused"] is True
    assert (tmp_path / "changes" / "pr-042-add-branch3-overlap.network.yaml").exists()


def test_change_demo_fails_when_expectation_is_wrong(lab_dir, tmp_path):
    """The demo itself can fail: a good change wrongly expected to be rejected must go red."""
    changes = tmp_path / "changes"
    changes.mkdir()
    text = (lab_dir / "changes" / "pr-041-add-branch3.yaml").read_text().replace("verdict: ACCEPT", "verdict: REJECT")
    (changes / "pr-041-add-branch3.yaml").write_text(text)
    rc = main(["change-demo", "--changes", str(changes), "--evidence", str(tmp_path / "ev")])
    assert rc == 1


def test_change_demo_fails_when_rejected_for_the_wrong_reason(lab_dir, tmp_path):
    changes = tmp_path / "changes"
    changes.mkdir()
    text = (lab_dir / "changes" / "pr-042-add-branch3-overlap.yaml").read_text().replace("codes: [SUBNET_OVERLAP]", "codes: [DUPLICATE_ASN]")
    (changes / "pr-042.yaml").write_text(text)
    assert main(["change-demo", "--changes", str(changes), "--evidence", str(tmp_path / "ev")]) == 1


def test_change_demo_with_no_change_files_fails(tmp_path):
    (tmp_path / "empty").mkdir()
    assert main(["change-demo", "--changes", str(tmp_path / "empty"), "--evidence", str(tmp_path / "ev")]) == 1


def test_change_demo_fails_if_generator_would_render_a_rejected_change(tmp_path, monkeypatch):
    """Sabotage the generator's gate: if a rejected change could still be rendered, the demo is red."""
    import netaac.__main__ as cli

    monkeypatch.setattr(cli.G, "render", lambda data: {})
    assert main(["change-demo", "--evidence", str(tmp_path)]) == 1
