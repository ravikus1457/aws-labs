import base64
import json

import pytest

from orders import model

GOOD = {"customer_id": "c-1", "items": [{"sku": "SKU-MUG", "quantity": 2}, {"sku": "SKU-TSHIRT", "quantity": 1}]}


def test_valid_order_is_normalised():
    out = model.validate_order({**GOOD, "extra": "ignored"})
    assert out == {"customer_id": "c-1", "items": GOOD["items"]}


@pytest.mark.parametrize(
    "payload, fragment",
    [
        ([], "JSON object"),
        ({"items": GOOD["items"]}, "customer_id"),
        ({"customer_id": "", "items": GOOD["items"]}, "customer_id"),
        ({"customer_id": "x" * 65, "items": GOOD["items"]}, "customer_id"),
        ({"customer_id": "c", "items": []}, "items must be a list"),
        ({"customer_id": "c", "items": [{"sku": "SKU-A", "quantity": 1}] * 21}, "items must be a list"),
        ({"customer_id": "c", "items": ["SKU-A"]}, "must be an object"),
        ({"customer_id": "c", "items": [{"sku": "sku-lower", "quantity": 1}]}, "sku must match"),
        ({"customer_id": "c", "items": [{"sku": "SKU-A", "quantity": 0}]}, "quantity"),
        ({"customer_id": "c", "items": [{"sku": "SKU-A", "quantity": 101}]}, "quantity"),
        ({"customer_id": "c", "items": [{"sku": "SKU-A", "quantity": 1.5}]}, "quantity"),
        ({"customer_id": "c", "items": [{"sku": "SKU-A", "quantity": True}]}, "quantity"),
        ({"customer_id": "c", "items": [{"sku": "SKU-A", "quantity": "2"}]}, "quantity"),
    ],
)
def test_invalid_orders_are_rejected_with_a_reason(payload, fragment):
    with pytest.raises(model.ValidationError) as exc:
        model.validate_order(payload)
    assert any(fragment in e for e in exc.value.errors), exc.value.errors


def test_all_errors_reported_at_once():
    with pytest.raises(model.ValidationError) as exc:
        model.validate_order({"customer_id": 5, "items": [{"sku": "x", "quantity": 0}]})
    assert len(exc.value.errors) == 3


def test_parse_body_plain_and_base64():
    raw = json.dumps(GOOD)
    assert model.parse_body(raw) == GOOD
    assert model.parse_body(base64.b64encode(raw.encode()).decode(), is_base64=True) == GOOD


@pytest.mark.parametrize("body", [None, "", "{not json", "\xff"])
def test_parse_body_rejects_missing_or_invalid(body):
    with pytest.raises(model.ValidationError):
        model.parse_body(body)


def test_parse_body_enforces_size_cap():
    big = json.dumps({"customer_id": "c", "pad": "x" * model.MAX_BODY_BYTES})
    with pytest.raises(model.ValidationError, match="exceeds"):
        model.parse_body(big)


def test_price_order_totals():
    priced = model.price_order(GOOD["items"], {"SKU-MUG": 1299, "SKU-TSHIRT": 1999})
    assert priced["total_cents"] == 2 * 1299 + 1999
    assert priced["item_count"] == 3
    assert priced["lines"][0] == {"sku": "SKU-MUG", "quantity": 2, "unit_price_cents": 1299, "line_total_cents": 2598}


def test_price_order_unknown_sku_is_the_poison_case():
    with pytest.raises(model.UnknownSkuError) as exc:
        model.price_order(GOOD["items"] + [{"sku": "SKU-NEW", "quantity": 1}], {"SKU-MUG": 1, "SKU-TSHIRT": 1})
    assert exc.value.missing == ["SKU-NEW"]
    assert "SKU-NEW" in str(exc.value)


def test_timestamps_round_trip_and_ttl():
    ts = "2026-10-15T12:00:00.123Z"
    assert model.parse_iso(ts).microsecond == 123000
    assert model.expires_at(ts) - int(model.parse_iso(ts).timestamp()) == 7 * 86400
    now = model.utc_now_iso()
    assert now.endswith("Z") and len(now) == len(ts)


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, None),
        ("", None),
        ("Parent=abc;Sampled=1", None),
        (
            "Root=1-5759e988-bd862e3fe1be46a994272793;Parent=53995c3f42cd8ad8;Sampled=1;Lineage=a87bd80c:0",
            "Root=1-5759e988-bd862e3fe1be46a994272793;Parent=53995c3f42cd8ad8;Sampled=1",
        ),
        ("Root=1-5759e988-bd862e3fe1be46a994272793", "Root=1-5759e988-bd862e3fe1be46a994272793"),
    ],
)
def test_sanitize_trace_header(raw, expected):
    assert model.sanitize_trace_header(raw) == expected
