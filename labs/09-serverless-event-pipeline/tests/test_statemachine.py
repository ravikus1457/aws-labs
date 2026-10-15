"""Contract tests for statemachine/order-workflow.asl.json.

The state machine cannot run locally, but its wiring can be checked: it must parse
after Terraform-style substitution, every transition must point at a real state,
and every "$.field" the Persist step reads must be a key the Enrich Lambda returns
(as a string, because DynamoDB "N" values are strings on the wire). A rename on
either side fails here instead of failing every order in AWS.
"""

import json
import re
from pathlib import Path

from orders import steps

ASL = Path(__file__).resolve().parent.parent / "statemachine" / "order-workflow.asl.json"
EVENT = {
    "order_id": "order-0001",
    "accepted_at": "2026-10-15T12:00:00.123Z",
    "receive_count": "1",
    "customer_id": "c-1",
    "items": [{"sku": "SKU-MUG", "quantity": 2}],
}


def render() -> dict:
    text = ASL.read_text()
    placeholders = set(re.findall(r"\$\{([a-z_]+)\}", text))
    assert placeholders == {"validate_arn", "enrich_arn", "table_name"}
    for name in placeholders:
        text = text.replace("${" + name + "}", f"PLACEHOLDER-{name}")
    return json.loads(text)


def test_transitions_point_at_real_states():
    sm = render()
    states = sm["States"]
    assert sm["StartAt"] in states
    for name, state in states.items():
        targets = [state.get("Next")] + [c["Next"] for c in state.get("Catch", [])]
        for t in filter(None, targets):
            assert t in states, f"{name} -> {t}"
        assert state.get("End") or state.get("Next") or state["Type"] in ("Succeed", "Fail"), name


def test_flow_is_validate_enrich_persist():
    s = render()["States"]
    assert s["Validate"]["Next"] == "Enrich" and s["Enrich"]["Next"] == "Persist" and s["Persist"]["End"]
    assert s["Persist"]["Resource"] == "arn:aws:states:::dynamodb:putItem"
    assert s["Persist"]["Parameters"]["ConditionExpression"] == "attribute_not_exists(pk)"
    assert s["Persist"]["Catch"][0]["ErrorEquals"] == ["DynamoDB.ConditionalCheckFailedException"]


def test_persist_reads_only_fields_enrich_produces(monkeypatch):
    monkeypatch.setattr(steps, "_load_catalog", lambda skus: {"SKU-MUG": 1299})
    produced = steps.enrich(steps.validate(EVENT, None), None)
    item = render()["States"]["Persist"]["Parameters"]["Item"]
    for attr, typed in item.items():
        for type_key, path in typed.items():
            if not type_key.endswith(".$"):
                continue
            for field in re.findall(r"(?<!\$)\$\.([a-z_]+)", path):
                assert field in produced, f"Persist.{attr} reads $.{field}, Enrich does not return it"
                assert isinstance(produced[field], str), f"$.{field} must be a string for {type_key}"


def test_order_key_prefix_matches_the_iam_condition():
    pk = render()["States"]["Persist"]["Parameters"]["Item"]["pk"]["S.$"]
    assert pk.startswith("States.Format('ORDER#{}'"), "stepfunctions.tf only allows PutItem on ORDER#* keys"
