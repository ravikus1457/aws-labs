"""Validate + Enrich task Lambdas; Enrich reads the catalog from a moto DynamoDB table."""

import json

import boto3
import pytest
from moto import mock_aws

from orders import model, steps

ACCEPTED = "2026-10-15T12:00:00.123Z"
EVENT = {
    "order_id": "order-0001",
    "accepted_at": ACCEPTED,
    "receive_count": "1",
    "message_id": "m",
    "customer_id": "c-1",
    "items": [{"sku": "SKU-MUG", "quantity": 2}, {"sku": "SKU-TSHIRT", "quantity": 1}],
}


@pytest.fixture
def table(monkeypatch):
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="orders",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
        )
        for sku, price in {"SKU-MUG": 1299, "SKU-TSHIRT": 1999}.items():
            ddb.put_item(TableName="orders", Item={"pk": {"S": f"SKU#{sku}"}, "price_cents": {"N": str(price)}})
        monkeypatch.setenv("TABLE_NAME", "orders")
        monkeypatch.setattr(steps, "_ddb", ddb)
        yield ddb


def test_validate_passes_through_a_good_order():
    out = steps.validate(EVENT, None)
    assert out["order_id"] == "order-0001" and out["accepted_at"] == ACCEPTED
    assert out["items"] == EVENT["items"] and "message_id" not in out


@pytest.mark.parametrize(
    "patch",
    [
        {"order_id": None},
        {"order_id": "bad id!"},
        {"accepted_at": "yesterday"},
        {"accepted_at": None},
        {"items": [{"sku": "SKU-MUG", "quantity": -1}]},
    ],
)
def test_validate_rejects(patch):
    with pytest.raises(model.ValidationError):
        steps.validate({**EVENT, **patch}, None)


def test_enrich_prices_from_the_catalog(table):
    out = steps.enrich(steps.validate(EVENT, None), None)
    assert out["total_cents"] == str(2 * 1299 + 1999)
    assert out["item_count"] == "3"
    assert json.loads(out["items_json"])[1]["unit_price_cents"] == 1999
    assert out["expires_at"] == str(model.expires_at(ACCEPTED))
    # DynamoDB "N" values travel as strings through the state machine
    assert all(isinstance(v, str) for v in out.values())


def test_enrich_unknown_sku_raises_the_poison_error(table):
    poison = {**EVENT, "items": [{"sku": "SKU-LAUNCH-ABC123", "quantity": 1}]}
    with pytest.raises(model.UnknownSkuError):
        steps.enrich(steps.validate(poison, None), None)


def test_enrich_succeeds_once_the_sku_is_added(table):
    """The redrive story in miniature: same message, catalog fixed, now it prices."""
    poison = steps.validate({**EVENT, "items": [{"sku": "SKU-LAUNCH-ABC123", "quantity": 2}]}, None)
    with pytest.raises(model.UnknownSkuError):
        steps.enrich(poison, None)
    table.put_item(TableName="orders", Item={"pk": {"S": "SKU#SKU-LAUNCH-ABC123"}, "price_cents": {"N": "4999"}})
    assert steps.enrich(poison, None)["total_cents"] == "9998"
