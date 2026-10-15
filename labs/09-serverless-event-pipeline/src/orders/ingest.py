"""Ingest Lambda: API Gateway (REST, Lambda proxy) -> validate shape -> SQS -> 202.

It does the cheap checks only (shape, sizes, ranges) and answers in milliseconds;
everything that needs other systems (catalog prices, persistence) happens behind
the queue. Its IAM role can do exactly one thing in AWS: sqs:SendMessage on the
orders queue.
"""

from __future__ import annotations

import json
import logging
import os
import uuid

import boto3
from botocore.exceptions import ClientError

from orders import model

log = logging.getLogger()
log.setLevel(logging.INFO)

# One id per execution environment. The load test counts distinct values to
# measure how many environments actually served traffic (a lower bound check on
# the CloudWatch concurrency number, read from the thing itself).
ENV_ID = uuid.uuid4().hex[:12]
_cold = True
_sqs = None


def _client():
    global _sqs
    if _sqs is None:
        _sqs = boto3.client("sqs")
    return _sqs


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event, context):
    global _cold
    cold, _cold = _cold, False

    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
    try:
        payload = model.parse_body(event.get("body"), bool(event.get("isBase64Encoded")))
        order = model.validate_order(payload)
        key = headers.get("idempotency-key")
        if key is not None and not model.ORDER_ID_RE.match(key):
            raise model.ValidationError(["Idempotency-Key must match " + model.ORDER_ID_RE.pattern])
    except model.ValidationError as exc:
        log.info(json.dumps({"event": "rejected", "errors": exc.errors}))
        return _resp(400, {"error": "invalid order", "details": exc.errors})

    # A client-supplied Idempotency-Key becomes the order id, so a retried POST maps
    # to the same DynamoDB key and the workflow's conditional write keeps one copy.
    order_id = key or uuid.uuid4().hex
    message = {"order_id": order_id, "accepted_at": model.utc_now_iso(), **order}
    kwargs = {"QueueUrl": os.environ["QUEUE_URL"], "MessageBody": json.dumps(message)}

    # Explicit X-Ray propagation across the queue: no X-Ray SDK is packaged, so the
    # trace header is handed to SQS as the AWSTraceHeader system attribute.
    trace = model.sanitize_trace_header(os.environ.get("_X_AMZN_TRACE_ID"))
    if trace:
        kwargs["MessageSystemAttributes"] = {"AWSTraceHeader": {"DataType": "String", "StringValue": trace}}

    try:
        try:
            _client().send_message(**kwargs)
        except ClientError as exc:
            # Never let tracing break ingestion: if SQS rejects the trace attribute, send without it.
            if "MessageSystemAttributes" in kwargs and exc.response.get("Error", {}).get("Code") in (
                "InvalidParameterValue",
                "InvalidMessageContents",
            ):
                log.warning(json.dumps({"event": "trace_header_rejected", "trace": trace}))
                kwargs.pop("MessageSystemAttributes")
                _client().send_message(**kwargs)
            else:
                raise
    except Exception:
        log.exception("send_message failed for order %s", order_id)
        return _resp(503, {"error": "order queue unavailable, retry with the same Idempotency-Key"})

    log.info(json.dumps({"event": "accepted", "order_id": order_id, "items": len(order["items"]), "cold": cold}))
    return _resp(202, {"order_id": order_id, "status": "ACCEPTED", "env": ENV_ID, "cold": cold})
