"""Pure order logic shared by the Lambdas.

No AWS calls and no I/O in this module: everything here is unit-tested directly
(tests/test_model.py), and the handlers stay thin wrappers around it.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime, timedelta

SKU_RE = re.compile(r"^SKU-[A-Z0-9][A-Z0-9-]{0,31}$")
ORDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAX_ITEMS = 20
MAX_QUANTITY = 100
MAX_BODY_BYTES = 16 * 1024
ORDER_TTL_DAYS = 7  # DynamoDB TTL on persisted orders: the table never grows without bound


class ValidationError(Exception):
    """The request can never succeed as sent (HTTP 400 at the edge; a failed execution in the workflow)."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class UnknownSkuError(Exception):
    """An order references a SKU the catalog does not (yet) have.

    This is the lab's "poison message": the ingest Lambda cannot know the catalog,
    so the order is accepted, fails in the workflow on every attempt, and lands in
    the DLQ until someone adds the SKU and redrives it.
    """

    def __init__(self, missing: list[str]):
        self.missing = sorted(missing)
        super().__init__("unknown SKU(s): " + ", ".join(self.missing))


def utc_now_iso(now: datetime | None = None) -> str:
    """ISO-8601 UTC with milliseconds, the same shape Step Functions uses for $$.State.EnteredTime."""
    now = now or datetime.now(UTC)
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def parse_iso(value: str) -> datetime:
    """Parse the timestamps this pipeline writes (accepted_at, persisted_at)."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_body(body: str | None, is_base64: bool = False) -> object:
    """Decode an API Gateway proxy body into JSON, enforcing the size cap."""
    if body is None or body == "":
        raise ValidationError(["request body is required"])
    raw = base64.b64decode(body) if is_base64 else body.encode("utf-8")
    if len(raw) > MAX_BODY_BYTES:
        raise ValidationError([f"request body exceeds {MAX_BODY_BYTES} bytes"])
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ValidationError(["request body is not valid JSON"]) from None


def _is_int(value: object) -> bool:
    # bool is a subclass of int in Python; {"quantity": true} is not a quantity.
    return isinstance(value, int) and not isinstance(value, bool)


def validate_order(payload: object) -> dict:
    """Return the normalised order {customer_id, items[{sku, quantity}]} or raise ValidationError."""
    if not isinstance(payload, dict):
        raise ValidationError(["body must be a JSON object"])
    errors: list[str] = []

    customer = payload.get("customer_id")
    if not isinstance(customer, str) or not 1 <= len(customer) <= 64:
        errors.append("customer_id must be a string of 1-64 characters")

    items = payload.get("items")
    normalised: list[dict] = []
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        errors.append(f"items must be a list of 1-{MAX_ITEMS} entries")
    else:
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"items[{i}] must be an object")
                continue
            sku = item.get("sku")
            qty = item.get("quantity")
            if not isinstance(sku, str) or not SKU_RE.match(sku):
                errors.append(f"items[{i}].sku must match {SKU_RE.pattern}")
            if not _is_int(qty) or not 1 <= qty <= MAX_QUANTITY:
                errors.append(f"items[{i}].quantity must be an integer 1-{MAX_QUANTITY}")
            if not errors:
                normalised.append({"sku": sku, "quantity": qty})

    if errors:
        raise ValidationError(errors)
    return {"customer_id": customer, "items": normalised}


def price_order(items: list[dict], catalog: dict[str, int]) -> dict:
    """Price validated items against {sku: price_cents}. Raises UnknownSkuError for any missing SKU."""
    missing = {i["sku"] for i in items if i["sku"] not in catalog}
    if missing:
        raise UnknownSkuError(sorted(missing))
    lines = []
    total = 0
    for item in items:
        unit = int(catalog[item["sku"]])
        line_total = unit * item["quantity"]
        total += line_total
        lines.append(
            {
                "sku": item["sku"],
                "quantity": item["quantity"],
                "unit_price_cents": unit,
                "line_total_cents": line_total,
            }
        )
    return {"lines": lines, "total_cents": total, "item_count": sum(i["quantity"] for i in items)}


def expires_at(accepted_at: str, days: int = ORDER_TTL_DAYS) -> int:
    """Epoch seconds for DynamoDB TTL."""
    return int((parse_iso(accepted_at) + timedelta(days=days)).timestamp())


def sanitize_trace_header(value: str | None) -> str | None:
    """Reduce a Lambda _X_AMZN_TRACE_ID value to the Root/Parent/Sampled fields.

    The Lambda environment variable can carry extra fields (e.g. Lineage) that are
    not part of the classic X-Ray header. SQS's AWSTraceHeader and Step Functions'
    traceHeader take the classic form, so anything else is dropped. Returns None
    when there is no usable Root.
    """
    if not value:
        return None
    fields = {}
    for part in value.split(";"):
        key, sep, val = part.strip().partition("=")
        if sep and key in ("Root", "Parent", "Sampled") and val:
            fields[key] = val
    if "Root" not in fields:
        return None
    header = ";".join(f"{k}={fields[k]}" for k in ("Root", "Parent", "Sampled") if k in fields)
    return header if len(header) <= 256 else None
