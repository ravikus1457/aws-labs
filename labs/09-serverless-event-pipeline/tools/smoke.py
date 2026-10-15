#!/usr/bin/env python3
"""Smoke test: prove every layer of the deployed pipeline, including the failure path.

Reads the DEPLOYED configuration back from AWS (not the Terraform files), then:
  1. config: X-Ray on API stage + 4 Lambdas + state machine; partial-batch failures on the ESM
  2. edge:   no API key -> 403; malformed order -> 400 (never reaches the queue)
  3. happy:  valid order -> 202 -> persisted in DynamoDB with the catalog-computed total
  4. dup:    same Idempotency-Key again -> 202, queue drains, the stored item is unchanged
  5. FAULT:  order for a SKU the catalog does not have -> fails on every attempt ->
             lands in the DLQ -> the DLQ alarm goes to ALARM (the detector is shown to fire)
  6. FIX:    add the SKU to the catalog, redrive the DLQ -> the same order is persisted
  7. trace:  X-Ray recorded traces from API Gateway, Lambda and Step Functions

Every line is PASS / FAIL / WARN; exit 1 if any FAIL. Evidence (account id redacted)
goes to --evidence.

  python tools/smoke.py --outputs outputs.json --evidence evidence/smoke-local
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from datetime import UTC, datetime, timedelta

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(__file__))
from common import Evidence, account_redactor, api_key, load_outputs, post_json, run_id, session, wait_until  # noqa: E402
from redrive import dlq_depth, redrive  # noqa: E402


def xray_trace_id() -> str:
    return f"1-{int(time.time()):08x}-{secrets.token_hex(12)}"


def get_order(ddb, table: str, order_id: str) -> dict | None:
    item = ddb.get_item(TableName=table, Key={"pk": {"S": f"ORDER#{order_id}"}}, ConsistentRead=True).get("Item")
    if not item:
        return None
    return {k: next(iter(v.values())) for k, v in item.items()}


def queue_total(sqs, url: str) -> int:
    attrs = sqs.get_queue_attributes(
        QueueUrl=url,
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
            "ApproximateNumberOfMessagesDelayed",
        ],
    )["Attributes"]
    return sum(int(v) for v in attrs.values())


def alarm(cw, name: str) -> dict:
    found = cw.describe_alarms(AlarmNames=[name]).get("MetricAlarms", [])
    return found[0] if found else {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outputs", default="outputs.json")
    ap.add_argument("--evidence", default="evidence/smoke-local")
    ap.add_argument("--alarm-timeout", type=float, default=900.0, help="seconds to wait for the DLQ alarm")
    args = ap.parse_args()

    out = load_outputs(args.outputs)
    sess = session(out)
    ev = Evidence(args.evidence, account_redactor(sess))
    lam, apigw, sfn = sess.client("lambda"), sess.client("apigateway"), sess.client("stepfunctions")
    sqs, ddb, cw, xray = sess.client("sqs"), sess.client("dynamodb"), sess.client("cloudwatch"), sess.client("xray")
    url, table = out["api_url"], out["table_name"]
    key = api_key(out, sess)
    run = run_id()
    started = datetime.now(UTC)
    ev.log(f"smoke run {run} against {url}")

    # ---- 1. deployed configuration -------------------------------------------------------------
    configs = {}
    for role, name in sorted(out["function_names"].items()):
        cfg = lam.get_function_configuration(FunctionName=name)
        configs[role] = {k: cfg.get(k) for k in ("Runtime", "Architectures", "MemorySize", "Timeout", "TracingConfig")}
        ev.check(
            cfg.get("TracingConfig", {}).get("Mode") == "Active",
            f"lambda {role}: X-Ray tracing Active",
            f"{cfg.get('Runtime')} {cfg.get('Architectures')} {cfg.get('MemorySize')} MB",
        )
    stage = apigw.get_stage(restApiId=out["rest_api_id"], stageName=out["stage_name"])
    ev.check(stage.get("tracingEnabled") is True, "API Gateway stage: X-Ray tracing enabled")
    sm = sfn.describe_state_machine(stateMachineArn=out["state_machine_arn"])
    ev.check(
        sm.get("type") == "EXPRESS" and sm.get("tracingConfiguration", {}).get("enabled") is True,
        "state machine: EXPRESS with X-Ray tracing",
    )
    esm = lam.list_event_source_mappings(FunctionName=out["function_names"]["worker"])["EventSourceMappings"]
    esm0 = esm[0] if esm else {}
    ev.check(
        "ReportBatchItemFailures" in (esm0.get("FunctionResponseTypes") or []) and esm0.get("State") == "Enabled",
        "SQS -> worker mapping: enabled with ReportBatchItemFailures",
        f"batch {esm0.get('BatchSize')}, max concurrency {esm0.get('ScalingConfig', {}).get('MaximumConcurrency')}",
    )
    qattrs = sqs.get_queue_attributes(QueueUrl=out["queue_url"], AttributeNames=["All"])["Attributes"]
    redrive_policy = json.loads(qattrs.get("RedrivePolicy", "{}"))
    ev.check(
        redrive_policy.get("deadLetterTargetArn") == out["dlq_arn"],
        "orders queue: DLQ wired",
        f"maxReceiveCount {redrive_policy.get('maxReceiveCount')}, visibility {qattrs.get('VisibilityTimeout')} s",
    )
    ev.write(
        "deployed-config.json",
        {
            "lambda": configs,
            "stage": {"tracingEnabled": stage.get("tracingEnabled")},
            "state_machine": {"type": sm.get("type"), "tracing": sm.get("tracingConfiguration")},
            "event_source_mapping": esm0,
            "queue_attributes": qattrs,
        },
    )

    # A DLQ left non-empty by an earlier run would make the alarm test meaningless: start clean.
    if dlq_depth(sqs, out["dlq_url"])["ApproximateNumberOfMessages"] > 0:
        ev.log("DLQ not empty from an earlier run: purging it before the fault test")
        sqs.purge_queue(QueueUrl=out["dlq_url"])
        time.sleep(60)  # PurgeQueue takes up to 60 s
    dlq_alarm_name = out["alarm_names"]["dlq_not_empty"]
    before = alarm(cw, dlq_alarm_name)
    if before.get("StateValue") == "ALARM":
        # Left over from an earlier run: an ALARM -> ALARM "transition" proves nothing, so wait for OK.
        ev.log("DLQ alarm already in ALARM before the fault: waiting for it to clear")
        before = wait_until(
            lambda: (lambda a: a if a.get("StateValue") != "ALARM" else None)(alarm(cw, dlq_alarm_name)),
            timeout=420,
            interval=20,
        ) or alarm(cw, dlq_alarm_name)
    alarm_testable = before.get("StateValue") != "ALARM"
    ev.log(f"DLQ alarm before fault: {before.get('StateValue')}")

    # A new stage + usage-plan key can take a little while to propagate: wait until the key is honoured
    # (an empty body is rejected by the ingest Lambda with 400 and never reaches the queue).
    # 10-15 first real run: one 400 then a 403 on the very next keyed call - the key propagates per edge
    # node, so require three accepted calls in a row before testing.
    streak = {"n": 0}

    def key_honoured():
        streak["n"] = streak["n"] + 1 if post_json(url, {}, {"x-api-key": key})[0] == 400 else 0
        return streak["n"] >= 3

    warm = wait_until(key_honoured, timeout=240, interval=5)
    ev.log("API key accepted" if warm else "API key still not accepted after 180 s")

    # ---- 2. edge rejections ---------------------------------------------------------------------
    status, body, _ = post_json(url, {"customer_id": "c-1", "items": [{"sku": "SKU-TSHIRT", "quantity": 1}]})
    ev.check(status == 403, "POST without x-api-key -> 403 (usage plan enforced)", f"got {status}")
    status, body, _ = post_json(
        url, {"customer_id": "c-1", "items": [{"sku": "bad sku", "quantity": 0}]}, {"x-api-key": key}
    )
    ev.check(status == 400, "malformed order -> 400 with details", f"got {status}: {json.dumps(body)[:160]}")

    # ---- 3. happy path ----------------------------------------------------------------------------
    catalog = out["catalog"]
    sku_a, sku_b = sorted(catalog)[:2]
    order = {"customer_id": "smoke-customer", "items": [{"sku": sku_a, "quantity": 2}, {"sku": sku_b, "quantity": 1}]}
    expected_total = 2 * catalog[sku_a] + catalog[sku_b]
    ok_id = f"smoke-{run}-ok"
    trace_id = xray_trace_id()
    t0 = time.monotonic()
    # A 403 here is the edge refusing a not-yet-propagated key (it never reaches the ingest Lambda), so a
    # retry with the SAME Idempotency-Key cannot create a duplicate order. Any other status is final.
    for _attempt in range(12):
        status, body, hdrs = post_json(
            url, order, {"x-api-key": key, "Idempotency-Key": ok_id, "X-Amzn-Trace-Id": f"Root={trace_id};Sampled=1"}
        )
        if status != 403:
            break
        time.sleep(10)
    ev.log(f"valid order: {_attempt + 1} attempt(s)")
    ev.check(
        status == 202 and isinstance(body, dict) and body.get("order_id") == ok_id,
        "valid order -> 202 ACCEPTED",
        f"got {status}: {body}",
    )
    item = wait_until(lambda: get_order(ddb, table, ok_id), timeout=180, interval=3)
    ev.check(
        bool(item) and item.get("status") == "PERSISTED" and int(item.get("total_cents", -1)) == expected_total,
        "order persisted by the workflow with the catalog total",
        f"{time.monotonic() - t0:.1f} s, total_cents {item.get('total_cents') if item else None}"
        f" (expected {expected_total})",
    )
    ev.write("order-happy.json", item or {})

    # ---- 4. duplicate delivery is idempotent ------------------------------------------------------
    status, body, _ = post_json(url, order, {"x-api-key": key, "Idempotency-Key": ok_id})
    drained = wait_until(lambda: queue_total(sqs, out["queue_url"]) == 0, timeout=120, interval=5)
    again = get_order(ddb, table, ok_id)
    ev.check(
        status == 202 and drained and again and item and again.get("persisted_at") == item.get("persisted_at"),
        "same Idempotency-Key again -> queue drains, stored order unchanged (conditional write)",
        f"status {status}, drained {bool(drained)}",
    )

    # ---- 5. FAULT: poison message ----------------------------------------------------------------
    poison_sku = f"SKU-LAUNCH-{secrets.token_hex(3).upper()}"  # never in the catalog until step 6
    ddb.delete_item(TableName=table, Key={"pk": {"S": f"SKU#{poison_sku}"}})
    poison_id = f"smoke-{run}-poison"
    fault_at = datetime.now(UTC)
    status, body, _ = post_json(
        url,
        {"customer_id": "smoke-customer", "items": [{"sku": poison_sku, "quantity": 1}]},
        {"x-api-key": key, "Idempotency-Key": poison_id},
    )
    ev.check(status == 202, f"poison order (unknown {poison_sku}) accepted at the edge -> 202", f"got {status}")
    budget = (int(out["max_receive_count"]) + 1) * int(out["visibility_timeout_seconds"]) + 180
    t0 = time.monotonic()
    in_dlq = wait_until(
        lambda: dlq_depth(sqs, out["dlq_url"])["ApproximateNumberOfMessages"] >= 1, timeout=budget, interval=10
    )
    ev.check(
        bool(in_dlq) and get_order(ddb, table, poison_id) is None,
        "poison message failed every attempt and landed in the DLQ (not persisted)",
        f"{time.monotonic() - t0:.0f} s, maxReceiveCount {out['max_receive_count']}",
    )

    def alarm_fired():
        a = alarm(cw, dlq_alarm_name)
        ts = a.get("StateUpdatedTimestamp")
        return a if a.get("StateValue") == "ALARM" and ts and ts >= fault_at - timedelta(seconds=5) else None

    t0 = time.monotonic()
    fired = wait_until(alarm_fired, timeout=args.alarm_timeout, interval=15) if alarm_testable else None
    ev.check(
        bool(fired) if alarm_testable else None,
        f"CloudWatch alarm {dlq_alarm_name} -> ALARM after the fault",
        f"{time.monotonic() - t0:.0f} s after the message reached the DLQ"
        if fired
        else (
            f"still {alarm(cw, dlq_alarm_name).get('StateValue')} after {args.alarm_timeout:.0f} s"
            if alarm_testable
            else "not testable: the alarm never left ALARM from an earlier run"
        ),
    )

    # ---- 6. FIX + redrive ------------------------------------------------------------------------
    ddb.put_item(
        TableName=table,
        Item={
            "pk": {"S": f"SKU#{poison_sku}"},
            "sku": {"S": poison_sku},
            "name": {"S": "Launch item (added by smoke test)"},
            "price_cents": {"N": "4999"},
        },
    )
    ev.log(f"fix applied: {poison_sku} added to the catalog at 4999 cents")
    task = redrive(sqs, out["dlq_arn"], out["queue_arn"], log=ev.log)
    ev.check(
        task.get("Status") == "COMPLETED",
        "DLQ redrive (StartMessageMoveTask) completed",
        f"moved {task.get('ApproximateNumberOfMessagesMoved')}",
    )
    t0 = time.monotonic()
    replayed = wait_until(lambda: get_order(ddb, table, poison_id), timeout=240, interval=5)
    ev.check(
        bool(replayed) and int(replayed.get("total_cents", -1)) == 4999,
        "redriven order persisted after the fix",
        f"{time.monotonic() - t0:.0f} s, receive_count {replayed.get('receive_count') if replayed else None}",
    )
    ev.check(dlq_depth(sqs, out["dlq_url"])["ApproximateNumberOfMessages"] == 0, "DLQ empty after redrive")
    ev.write("order-redriven.json", replayed or {})
    ddb.delete_item(TableName=table, Key={"pk": {"S": f"SKU#{poison_sku}"}})

    # ---- 7. X-Ray -----------------------------------------------------------------------------------
    def summaries():
        found, token = [], None
        for _ in range(10):
            kw = {"StartTime": started - timedelta(minutes=1), "EndTime": datetime.now(UTC), "Sampling": False}
            if token:
                kw["NextToken"] = token
            page = xray.get_trace_summaries(**kw)
            found += page.get("TraceSummaries", [])
            token = page.get("NextToken")
            if not token:
                break
        return found

    # A synchronous Express workflow emits no StepFunctions node of its own (10-15 first real run: 0 such
    # nodes, while the workflow's task Lambdas sat inside the worker's trace). Continuity THROUGH the
    # workflow is proven by the worker and both task Lambdas sharing one trace id.
    def workflow_traced(traces):
        want = {f"{out['name_prefix']}-{n}" for n in ("worker", "validate", "enrich")}
        for t in traces:
            names = {s.get("Name") for s in t.get("ServiceIds", [])}
            types = {s.get("Type") for s in t.get("ServiceIds", [])}
            if want <= names or "AWS::StepFunctions::StateMachine" in types:
                return t.get("Id")
        return None

    def types_seen(traces):
        return sorted({s.get("Type") for t in traces for s in t.get("ServiceIds", []) if s.get("Type")})

    traces = (
        wait_until(
            lambda: (lambda t: t if "AWS::ApiGateway::Stage" in types_seen(t) and workflow_traced(t) else None)(
                summaries()
            ),
            timeout=180,
            interval=15,
        )
        or summaries()
    )
    seen = types_seen(traces)
    ev.check(
        "AWS::ApiGateway::Stage" in seen and any(s.startswith("AWS::Lambda") for s in seen),
        "X-Ray: traces from API Gateway -> ingest Lambda",
        f"{len(traces)} traces, types {seen}",
    )
    wf = workflow_traced(traces)
    ev.check(
        bool(wf),
        "X-Ray: one trace runs worker -> Step Functions -> validate + enrich",
        f"trace {wf}" if wf else "no trace held the worker and both task Lambdas",
    )
    ours = [t for t in traces if t.get("Id") == trace_id]
    linked = any("AWS::StepFunctions::StateMachine" in {s.get("Type") for s in t.get("ServiceIds", [])} for t in ours)
    ev.check(
        True if linked else None,
        "X-Ray: the client's trace id continued through SQS into Step Functions",
        "observed" if linked else "not observed in this trace (recorded, not asserted)",
    )
    ev.write(
        "xray-trace-summaries.json", {"trace_id_sent": trace_id, "service_types_seen": seen, "traces": traces[:20]}
    )

    # ---- evidence ---------------------------------------------------------------------------------
    alarms = cw.describe_alarms(AlarmNamePrefix=out["name_prefix"]).get("MetricAlarms", [])
    ev.write(
        "alarms.json",
        [{k: a.get(k) for k in ("AlarmName", "StateValue", "StateReason", "StateUpdatedTimestamp")} for a in alarms],
    )
    summary = {
        "run": run,
        "results": [line for _, line in ev.results],
        "pass": sum(1 for lvl, _ in ev.results if lvl == "PASS"),
        "fail": sum(1 for lvl, _ in ev.results if lvl == "FAIL"),
        "warn": sum(1 for lvl, _ in ev.results if lvl == "WARN"),
    }
    ev.write("smoke.json", summary)
    ev.close("smoke.log")
    print(f"\nsmoke: {summary['pass']} PASS, {summary['fail']} FAIL, {summary['warn']} WARN")
    return 1 if ev.failed else 0


if __name__ == "__main__":
    sys.exit(main())
