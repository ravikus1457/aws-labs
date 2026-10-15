"""Worker Lambda: SQS batch -> one synchronous Step Functions Express execution per message.

Partial batch failure: only the messages whose execution did not SUCCEED are
returned in batchItemFailures. SQS makes those visible again after the visibility
timeout; after maxReceiveCount attempts SQS moves them to the DLQ. A poison
message therefore costs a few retries, never the whole batch and never a stuck queue.

IAM: receive/delete on the orders queue (via the event source mapping) and
states:StartSyncExecution on the one state machine. Nothing else.
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

_sfn = None


def _client():
    global _sfn
    if _sfn is None:
        _sfn = boto3.client("stepfunctions")
    return _sfn


def _process(record: dict) -> bool:
    message_id = record.get("messageId", "?")
    attrs = record.get("attributes") or {}
    try:
        body = json.loads(record["body"])
        if not isinstance(body, dict):
            raise ValueError("message body is not a JSON object")
    except (KeyError, ValueError) as exc:
        log.warning(json.dumps({"event": "unparseable", "message_id": message_id, "error": str(exc)}))
        return False

    body["message_id"] = message_id
    body["receive_count"] = str(attrs.get("ApproximateReceiveCount", "1"))
    kwargs = {"stateMachineArn": os.environ["STATE_MACHINE_ARN"], "input": json.dumps(body)}
    trace = attrs.get("AWSTraceHeader")
    if trace and len(trace) <= 256:
        kwargs["traceHeader"] = trace

    try:
        out = _client().start_sync_execution(**kwargs)
    except Exception as exc:  # throttling, network: let SQS retry this message
        log.warning(json.dumps({"event": "start_failed", "order_id": body.get("order_id"), "error": repr(exc)}))
        return False

    status = out.get("status")
    if status == "SUCCEEDED":
        log.info(json.dumps({"event": "processed", "order_id": body.get("order_id")}))
        return True
    log.warning(
        json.dumps(
            {
                "event": "workflow_failed",
                "order_id": body.get("order_id"),
                "status": status,
                "error": out.get("error"),
                "cause": (out.get("cause") or "")[:500],
                "receive_count": body["receive_count"],
            }
        )
    )
    return False


def handler(event, context):
    records = event.get("Records") or []
    if not records:
        return {"batchItemFailures": []}
    parallelism = max(1, min(int(os.environ.get("BATCH_PARALLELISM", "1")), len(records)))
    if parallelism == 1:
        results = [_process(r) for r in records]
    else:
        with ThreadPoolExecutor(max_workers=parallelism) as pool:
            results = list(pool.map(_process, records))
    failures = [{"itemIdentifier": r["messageId"]} for r, ok in zip(records, results, strict=True) if not ok]
    log.info(json.dumps({"event": "batch", "size": len(records), "failed": len(failures)}))
    return {"batchItemFailures": failures}
