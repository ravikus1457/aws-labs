#!/usr/bin/env python3
"""Load test: send N orders, then MEASURE what happened and what it cost.

  python tools/loadtest.py --outputs outputs.json --requests 2000 --concurrency 25 \
      --evidence evidence/load-local

What is measured, and where each number comes from:
  client latency p50/p99     wall-clock per POST on this machine (includes the internet path)
  success rate               HTTP 202s / requests sent, plus a histogram of every status code
  end-to-end latency         DynamoDB item persisted_at ($$.State.EnteredTime of Persist) minus
                             accepted_at (stamped by the ingest Lambda): both AWS clocks
  persisted                  every accepted order id is read back from DynamoDB (BatchGetItem);
                             "accepted" without "persisted" is a lost order and fails the run
  max concurrency            three readings: the client's peak in-flight requests; the number of
                             distinct Lambda execution environments that answered (each response
                             carries its environment id); CloudWatch ConcurrentExecutions maximum
  API latency p50/p99        CloudWatch AWS/ApiGateway Latency percentiles (server side)
  cost                       CloudWatch usage metrics x unit prices in tools/cost.py

Exit status 1 if the success rate is below --min-success or any accepted order is missing.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(__file__))
import cost  # noqa: E402
import stats  # noqa: E402
from common import Evidence, account_redactor, api_key, load_outputs, post_json, run_id, session, wait_until  # noqa: E402

PERIOD = 60


def send_all(url: str, key: str, catalog: dict, n: int, concurrency: int, prefix: str) -> list[dict]:
    rng = random.Random(42)
    skus = sorted(catalog)
    orders = []
    for i in range(n):
        lines = rng.sample(skus, k=rng.randint(1, min(3, len(skus))))
        orders.append(
            {
                "id": f"{prefix}-{i:06d}",
                "body": {
                    "customer_id": f"lt-customer-{i % 97}",
                    "items": [{"sku": s, "quantity": rng.randint(1, 5)} for s in lines],
                },
            }
        )
    results: list[dict] = [None] * n  # type: ignore[list-item]
    lock = threading.Lock()

    def one(idx: int):
        o = orders[idx]
        t0 = time.perf_counter()
        start_wall = time.time()
        try:
            status, body, _ = post_json(url, o["body"], {"x-api-key": key, "Idempotency-Key": o["id"]}, timeout=30)
        except Exception as exc:  # connection reset, timeout: counted, never retried
            status, body = 0, {"error": repr(exc)}
        t1 = time.perf_counter()
        rec = {
            "id": o["id"],
            "status": status,
            "latency_ms": (t1 - t0) * 1000.0,
            "start": start_wall,
            "end": start_wall + (t1 - t0),
        }
        if isinstance(body, dict):
            rec["env"] = body.get("env")
            rec["cold"] = body.get("cold")
        with lock:
            results[idx] = rec

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(one, range(n)))
    return results


def read_back(ddb, table: str, ids: list[str]) -> dict[str, dict]:
    """BatchGetItem every accepted id (100 per call). Returns {order_id: item} for those found."""
    found: dict[str, dict] = {}
    for i in range(0, len(ids), 100):
        request = {
            table: {
                "Keys": [{"pk": {"S": f"ORDER#{x}"}} for x in ids[i : i + 100]],
                "ProjectionExpression": "order_id, accepted_at, persisted_at, receive_count",
            }
        }
        for _ in range(8):
            resp = ddb.batch_get_item(RequestItems=request)
            for it in resp.get("Responses", {}).get(table, []):
                found[it["order_id"]["S"]] = {k: next(iter(v.values())) for k, v in it.items()}
            request = resp.get("UnprocessedKeys") or {}
            if not request:
                break
            time.sleep(0.5)
    return found


class Metrics:
    """GetMetricData wrapper that counts the metrics it requests (they are part of the bill)."""

    def __init__(self, cw, start: datetime, end: datetime):
        self.cw, self.start, self.end, self.requested = cw, start, end, 0

    def get(self, namespace: str, metric: str, dims: dict, stat: str) -> list[float]:
        self.requested += 1
        resp = self.cw.get_metric_data(
            MetricDataQueries=[
                {
                    "Id": "m",
                    "ReturnData": True,
                    "MetricStat": {
                        "Metric": {
                            "Namespace": namespace,
                            "MetricName": metric,
                            "Dimensions": [{"Name": k, "Value": v} for k, v in dims.items()],
                        },
                        "Period": PERIOD,
                        "Stat": stat,
                    },
                }
            ],
            StartTime=self.start,
            EndTime=self.end,
        )
        return resp["MetricDataResults"][0].get("Values", [])

    def total(self, *a) -> float:
        return float(sum(self.get(*a, "Sum")))

    def maximum(self, *a) -> float | None:
        v = self.get(*a, "Maximum")
        return max(v) if v else None


REPORT_QUERY = (
    'filter type = "platform.report" '
    "| stats sum(record.metrics.billedDurationMs) as billed, sum(record.metrics.initDurationMs) as init, "
    "count(*) as reports"
)


def billed_from_logs(logs, groups: dict[str, str], start: datetime, end: datetime) -> tuple[dict, float]:
    """Exact billed Lambda duration from the platform REPORT records (Logs Insights).

    The CloudWatch Duration metric excludes the INIT phase, which AWS bills for managed
    runtimes since 2025-08-01; the REPORT record's billedDurationMs is what is invoiced.
    Returns ({role: {"billed_ms_sum", "init_ms_sum", "reports"}}, bytes_scanned). Roles whose
    query returns nothing are left out (the caller falls back to the Duration metric).
    """
    found, scanned = {}, 0.0
    for role, group in groups.items():
        try:
            qid = logs.start_query(
                logGroupName=group,
                startTime=int(start.timestamp()),
                endTime=int(end.timestamp()),
                queryString=REPORT_QUERY,
            )["queryId"]
            res: dict = {}
            for _ in range(60):
                res = logs.get_query_results(queryId=qid)
                if res.get("status") in ("Complete", "Failed", "Cancelled", "Timeout"):
                    break
                time.sleep(2)
        except Exception as exc:  # the metric fallback still produces a (labelled) number
            print(f"Logs Insights unavailable for {group}: {exc!r}")
            continue
        scanned += float((res.get("statistics") or {}).get("bytesScanned", 0.0))
        rows = res.get("results") or []
        if res.get("status") != "Complete" or not rows:
            continue
        row = {c["field"]: c["value"] for c in rows[0]}
        if float(row.get("reports", 0) or 0) > 0:
            found[role] = {
                "billed_ms_sum": float(row.get("billed", 0) or 0),
                "init_ms_sum": float(row.get("init", 0) or 0),
                "reports": int(float(row.get("reports", 0))),
            }
    return found, scanned


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outputs", default="outputs.json")
    ap.add_argument("--evidence", default="evidence/load-local")
    ap.add_argument("--requests", type=int, default=2000)
    ap.add_argument("--concurrency", type=int, default=25)
    ap.add_argument("--drain-timeout", type=float, default=900.0)
    ap.add_argument("--min-success", type=float, default=0.99)
    ap.add_argument(
        "--wall-hours",
        type=float,
        default=1.0,
        help="hours the stack lives for this run (prorates alarm + dashboard cost)",
    )
    args = ap.parse_args()

    out = load_outputs(args.outputs)
    sess = session(out)
    ev = Evidence(args.evidence, account_redactor(sess))
    ddb, cw, xray, lam = sess.client("dynamodb"), sess.client("cloudwatch"), sess.client("xray"), sess.client("lambda")
    key = api_key(out, sess)
    prefix = "lt-" + run_id()
    try:  # informational only: the run must not fail because this read is denied or unsupported
        account_limit = lam.get_account_settings().get("AccountLimit", {}).get("ConcurrentExecutions")
    except Exception as exc:
        account_limit = None
        print(f"lambda:GetAccountSettings unavailable: {exc!r}")
    ev.log(
        f"load test: {args.requests} requests, concurrency {args.concurrency}, account Lambda concurrency "
        f"limit {account_limit}, target {out['api_url']}"
    )

    window_start = datetime.now(UTC) - timedelta(seconds=PERIOD)
    t0 = time.time()
    results = send_all(out["api_url"], key, out["catalog"], args.requests, args.concurrency, prefix)
    send_seconds = time.time() - t0
    codes: dict[str, int] = {}
    for r in results:
        codes[str(r["status"])] = codes.get(str(r["status"]), 0) + 1
    accepted = [r for r in results if r["status"] == 202]
    success_rate = len(accepted) / len(results) if results else 0.0
    lat = stats.summarize([r["latency_ms"] for r in results])
    lat_ok = stats.summarize([r["latency_ms"] for r in accepted])
    envs = {r.get("env") for r in accepted if r.get("env")}
    peak_client = stats.max_in_flight([(r["start"], r["end"]) for r in results])
    ev.log(
        f"sent in {send_seconds:.1f} s ({len(results) / send_seconds:.0f} req/s): codes {codes}; "
        f"p50 {lat_ok.get('p50', 0):.0f} ms p99 {lat_ok.get('p99', 0):.0f} ms; {len(envs)} ingest environments"
    )

    # ---- drain: every accepted order must reach DynamoDB -----------------------------------------
    ids = [r["id"] for r in accepted]
    persisted: dict[str, dict] = {}
    t_drain = time.time()

    def all_persisted():
        missing = [x for x in ids if x not in persisted]
        persisted.update(read_back(ddb, out["table_name"], missing))
        ev.log(f"persisted {len(persisted)}/{len(ids)}")
        return len(persisted) >= len(ids)

    wait_until(all_persisted, timeout=args.drain_timeout, interval=15)
    drain_seconds = time.time() - t_drain
    e2e = []
    for it in persisted.values():
        try:
            a = datetime.fromisoformat(it["accepted_at"].replace("Z", "+00:00"))
            p = datetime.fromisoformat(it["persisted_at"].replace("Z", "+00:00"))
            e2e.append((p - a).total_seconds() * 1000.0)
        except (KeyError, ValueError):
            pass
    retried = sum(1 for it in persisted.values() if int(it.get("receive_count", "1")) > 1)
    first_accept = min((it["accepted_at"] for it in persisted.values()), default=None)
    last_persist = max((it["persisted_at"] for it in persisted.values()), default=None)

    # ---- CloudWatch: wait for the metrics to land, then read the window -------------------------
    ev.log("waiting for CloudWatch metrics to catch up (they lag 1-3 min)")
    time.sleep(120)
    end = datetime.now(UTC) + timedelta(seconds=PERIOD)
    m = Metrics(cw, window_start, end)
    fn = out["function_names"]
    wait_until(
        lambda: m.total("AWS/Lambda", "Invocations", {"FunctionName": fn["ingest"]}) >= 0.95 * len(results),
        timeout=360,
        interval=30,
    )
    m.end = datetime.now(UTC) + timedelta(seconds=PERIOD)

    lambda_usage, concurrency_cw = {}, {}
    for role, name in fn.items():
        d = {"FunctionName": name}
        lambda_usage[role] = {
            "invocations": int(m.total("AWS/Lambda", "Invocations", d)),
            "duration_ms_sum": m.total("AWS/Lambda", "Duration", d),
            "memory_mb": int(out["function_memory_mb"][role]),
            "errors": int(m.total("AWS/Lambda", "Errors", d)),
            "throttles": int(m.total("AWS/Lambda", "Throttles", d)),
        }
        concurrency_cw[role] = m.maximum("AWS/Lambda", "ConcurrentExecutions", d)
    concurrency_cw["account_wide"] = m.maximum("AWS/Lambda", "ConcurrentExecutions", {})
    billed, insights_bytes = billed_from_logs(
        sess.client("logs"), {r: f"/aws/lambda/{n}" for r, n in fn.items()}, window_start, m.end
    )
    for role, b in billed.items():
        lambda_usage[role].update(b)

    api_dims = {"ApiName": out["rest_api_name"], "Stage": out["stage_name"]}
    api_count = int(m.total("AWS/ApiGateway", "Count", api_dims))
    api_p50 = m.get("AWS/ApiGateway", "Latency", api_dims, "p50")
    api_p99 = m.get("AWS/ApiGateway", "Latency", api_dims, "p99")
    sm = {"StateMachineArn": out["state_machine_arn"]}
    sfn_started = int(m.total("AWS/States", "ExecutionsStarted", sm))
    sfn_failed = int(m.total("AWS/States", "ExecutionsFailed", sm))
    sfn_time = m.get("AWS/States", "ExecutionTime", sm, "Average")
    sfn_avg = sum(sfn_time) / len(sfn_time) if sfn_time else 0.0
    td = {"TableName": out["table_name"]}
    wcu = m.total("AWS/DynamoDB", "ConsumedWriteCapacityUnits", td)
    rcu = m.total("AWS/DynamoDB", "ConsumedReadCapacityUnits", td)
    q = {"QueueName": out["queue_name"]}
    sqs_parts = {
        k: int(m.total("AWS/SQS", k, q))
        for k in (
            "NumberOfMessagesSent",
            "NumberOfMessagesReceived",
            "NumberOfMessagesDeleted",
            "NumberOfEmptyReceives",
        )
    }
    log_bytes = int(sum(m.total("AWS/Logs", "IncomingBytes", {"LogGroupName": g}) for g in out["log_group_names"]))

    traces, token, pages, xray_error = 0, None, 0, None
    try:
        while pages < 50:
            kw = {"StartTime": window_start, "EndTime": datetime.now(UTC), "Sampling": False}
            if token:
                kw["NextToken"] = token
            page = xray.get_trace_summaries(**kw)
            traces += len(page.get("TraceSummaries", []))
            token, pages = page.get("NextToken"), pages + 1
            if not token:
                break
    except Exception as exc:  # the cost line then reads 0 traces; the error is recorded in loadtest.json
        xray_error = repr(exc)
        print(f"X-Ray trace count unavailable: {xray_error}")

    usage = {
        "api_requests": api_count or len(results),
        "lambda": lambda_usage,
        "sfn_executions": sfn_started,
        "sfn_avg_duration_ms": sfn_avg,
        "dynamodb_wcu": wcu,
        "dynamodb_rcu": rcu,
        "sqs_requests": sum(sqs_parts.values()),
        "xray_traces": traces,
        "xray_traces_retrieved": traces,
        "log_bytes": log_bytes,
        "logs_insights_bytes": insights_bytes,
        "alarms": len(out["alarm_names"]),
        "dashboards": 1,
        "wall_hours": args.wall_hours,
        "getmetricdata_metrics": m.requested,
        "orders_persisted": len(persisted),
    }
    bill = cost.estimate(usage)
    # If CloudWatch had not caught up, the cost lines read low: say so instead of under-reporting quietly.
    cw_complete = sum(v["invocations"] for v in lambda_usage.values()) >= len(ids) and sfn_started >= len(ids)

    report = {
        "requests": len(results),
        "concurrency_setting": args.concurrency,
        "send_seconds": send_seconds,
        "throughput_rps": len(results) / send_seconds if send_seconds else None,
        "status_codes": codes,
        "success_rate": success_rate,
        "client_latency_ms_all": lat,
        "client_latency_ms_202": lat_ok,
        "cold_starts_seen": sum(1 for r in accepted if r.get("cold")),
        "accepted": len(ids),
        "persisted": len(persisted),
        "missing": len(ids) - len(persisted),
        "persisted_after_retry": retried,
        "drain_seconds_after_send": drain_seconds,
        "first_accepted_at": first_accept,
        "last_persisted_at": last_persist,
        "end_to_end_ms": stats.summarize(e2e),
        "max_concurrency": {
            "client_peak_in_flight": peak_client,
            "distinct_ingest_environments": len(envs),
            "cloudwatch_concurrent_executions_max": concurrency_cw,
            "account_concurrency_limit": account_limit,
        },
        "api_gateway": {"count": api_count, "latency_ms_p50_per_minute": api_p50, "latency_ms_p99_per_minute": api_p99},
        "lambda": lambda_usage,
        "step_functions": {"started": sfn_started, "failed": sfn_failed, "avg_execution_ms": sfn_avg},
        "dynamodb": {"wcu": wcu, "rcu": rcu},
        "sqs": sqs_parts,
        "log_bytes": log_bytes,
        "xray_traces": traces,
        "xray_error": xray_error,
        "cloudwatch_usage_complete": cw_complete,
        "window": {"start": window_start.isoformat(), "end": m.end.isoformat()},
    }
    ev.write("loadtest.json", report)
    ev.write("cost.json", bill)
    ev.write("requests.json", [{k: r.get(k) for k in ("id", "status", "latency_ms", "cold")} for r in results])

    def f(v, unit="ms"):
        return "n/a" if v is None else f"{v:,.0f} {unit}"

    e2e_s = report["end_to_end_ms"]
    md = [
        f"# Lab 09 load test ({datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')})",
        "",
        "| Measure | Value | Source |",
        "|---|---|---|",
        f"| Requests sent | {len(results):,} at concurrency {args.concurrency} in {send_seconds:.1f} s "
        f"({report['throughput_rps']:.0f} req/s) | client |",
        f"| Success rate (HTTP 202) | {success_rate:.2%} - codes {codes} | client |",
        f"| Client latency p50 / p99 | {f(lat_ok.get('p50'))} / {f(lat_ok.get('p99'))} | client wall clock |",
        f"| API Gateway latency p50 / p99 (per-minute max) | {f(max(api_p50) if api_p50 else None)} / "
        f"{f(max(api_p99) if api_p99 else None)} | CloudWatch |",
        f"| Accepted -> persisted | {len(persisted):,} / {len(ids):,} ({report['missing']} missing, "
        f"{retried} needed a retry) | DynamoDB read-back |",
        f"| End-to-end p50 / p99 (accepted -> persisted) | {f(e2e_s.get('p50'))} / {f(e2e_s.get('p99'))} | "
        f"item timestamps |",
        f"| Queue drained after sending | {drain_seconds:.0f} s (polled every 15 s) | DynamoDB read-back |",
        f"| Max concurrency: client in-flight / ingest environments / CloudWatch ingest max | {peak_client} / "
        f"{len(envs)} / {concurrency_cw.get('ingest')} | client / responses / CloudWatch |",
        f"| Account Lambda concurrency limit | {account_limit} | lambda:GetAccountSettings |",
        f"| Lambda throttles (all functions) | {sum(v['throttles'] for v in lambda_usage.values())} | CloudWatch |",
        f"| Step Functions executions started / failed | {sfn_started} / {sfn_failed} | CloudWatch |",
        "",
        "## Cost of this run",
        "",
        (
            "Usage below is from CloudWatch for the run window."
            if cw_complete
            else "**WARNING: CloudWatch usage metrics had not fully landed when this was read (Lambda invocations"
            " or Step Functions executions < accepted orders); the cost lines below are a LOWER bound.**"
        ),
        "",
        cost.render_markdown(bill),
        "",
    ]
    ev.write("summary.md", "\n".join(md))
    ev.close("loadtest.log")
    print("\n".join(md))

    ok = success_rate >= args.min_success and len(persisted) == len(ids)
    if not ok:
        print(f"FAIL success {success_rate:.2%} (min {args.min_success:.0%}), missing {len(ids) - len(persisted)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
