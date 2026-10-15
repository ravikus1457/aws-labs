"""Step Functions task Lambdas: Validate and Enrich. (Persist is a direct DynamoDB
service integration in the state machine - no Lambda, no code, no extra hop.)

Validate: re-checks the order (defence in depth: the queue can be written by
          anything holding sqs:SendMessage, not only the ingest Lambda). No AWS access.
Enrich:   prices the order from the catalog items in DynamoDB. Its IAM policy can
          read ONLY keys that start with "SKU#" (dynamodb:LeadingKeys), so it can
          never read or change an order.

The output of Enrich is the exact input the Persist state reads; the contract is
pinned by tests/test_statemachine.py (every "$.x" Persist references must be a key
Enrich returns, as a string, because DynamoDB's "N" type is a string on the wire).
"""

from __future__ import annotations

import json
import logging
import os

import boto3

from orders import model

log = logging.getLogger()
log.setLevel(logging.INFO)

_ddb = None


def _client():
    global _ddb
    if _ddb is None:
        _ddb = boto3.client("dynamodb")
    return _ddb


def validate(event, context):
    order_id = event.get("order_id") if isinstance(event, dict) else None
    if not isinstance(order_id, str) or not model.ORDER_ID_RE.match(order_id):
        raise model.ValidationError(["order_id missing or malformed"])
    accepted_at = event.get("accepted_at")
    try:
        if not isinstance(accepted_at, str):
            raise TypeError("accepted_at must be a string")
        model.parse_iso(accepted_at)
    except (TypeError, ValueError):
        raise model.ValidationError(["accepted_at missing or malformed"]) from None
    order = model.validate_order(event)
    return {
        "order_id": order_id,
        "accepted_at": accepted_at,
        "receive_count": str(event.get("receive_count", "1")),
        **order,
    }


def _load_catalog(skus: list[str]) -> dict[str, int]:
    table = os.environ["TABLE_NAME"]
    keys = [{"pk": {"S": f"SKU#{s}"}} for s in sorted(set(skus))]
    found: dict[str, int] = {}
    request = {table: {"Keys": keys, "ProjectionExpression": "pk, price_cents"}}
    for _ in range(5):  # BatchGetItem may return UnprocessedKeys under throttling
        out = _client().batch_get_item(RequestItems=request)
        for item in out.get("Responses", {}).get(table, []):
            found[item["pk"]["S"][len("SKU#") :]] = int(item["price_cents"]["N"])
        request = out.get("UnprocessedKeys") or {}
        if not request:
            break
    else:
        raise RuntimeError("catalog read still had unprocessed keys after 5 attempts")
    return found


def enrich(event, context):
    catalog = _load_catalog([i["sku"] for i in event["items"]])
    priced = model.price_order(event["items"], catalog)  # raises UnknownSkuError -> execution fails
    out = {
        "order_id": event["order_id"],
        "customer_id": event["customer_id"],
        "accepted_at": event["accepted_at"],
        "receive_count": str(event.get("receive_count", "1")),
        "items_json": json.dumps(priced["lines"], separators=(",", ":")),
        "item_count": str(priced["item_count"]),
        "total_cents": str(priced["total_cents"]),
        "expires_at": str(model.expires_at(event["accepted_at"])),
    }
    log.info(json.dumps({"event": "enriched", "order_id": out["order_id"], "total_cents": out["total_cents"]}))
    return out
