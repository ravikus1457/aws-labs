"""Cost of one run, computed from MEASURED usage x PUBLISHED unit prices.

Pure arithmetic (no AWS calls) so it is unit-tested (tests/test_cost.py) and every
line in the output carries its own arithmetic string a reviewer can redo by hand.

The prices are us-east-1 list prices as recorded in PRICES below, with the page
each one comes from. They are constants in code, not fetched: re-check them on the
pricing pages before quoting a number. The total is the price BEFORE any free
allowance. The always-free monthly allowances (1M Lambda requests + 400k GB-s, 1M SQS
requests, 100k X-Ray traces, 5 GB logs, 10 alarms, 3 dashboards) absorb most lines on
an otherwise idle account; Step Functions Express and DynamoDB on-demand requests have
no always-free allowance, and API Gateway's 1M calls/month is a 12-month offer (or
free-plan credits on accounts created after July 2025), so those lines still bill.
"""

from __future__ import annotations

import math

PRICES_AS_OF = "2026-10-15 (us-east-1 list prices as recorded here; verify on the linked pages)"

PRICES = {
    "apigw_rest_per_million": (3.50, "https://aws.amazon.com/api-gateway/pricing/ (REST API, first 333M/month)"),
    "lambda_requests_per_million": (0.20, "https://aws.amazon.com/lambda/pricing/"),
    "lambda_arm64_gb_second": (0.0000133334, "https://aws.amazon.com/lambda/pricing/ (Arm, first 7.5B GB-s/month)"),
    "sfn_express_requests_per_million": (1.00, "https://aws.amazon.com/step-functions/pricing/ (Express)"),
    "sfn_express_gb_second": (
        0.00001667,
        "https://aws.amazon.com/step-functions/pricing/ (Express, first 1,000 GB-hours)",
    ),
    "dynamodb_write_request_units_per_million": (0.625, "https://aws.amazon.com/dynamodb/pricing/on-demand/"),
    "dynamodb_read_request_units_per_million": (0.125, "https://aws.amazon.com/dynamodb/pricing/on-demand/"),
    "sqs_standard_requests_per_million": (0.40, "https://aws.amazon.com/sqs/pricing/ (standard queue, after free 1M)"),
    "xray_traces_recorded_per_million": (5.00, "https://aws.amazon.com/xray/pricing/"),
    "xray_traces_retrieved_per_million": (0.50, "https://aws.amazon.com/xray/pricing/"),
    "cloudwatch_logs_ingest_per_gb": (0.50, "https://aws.amazon.com/cloudwatch/pricing/ (Logs, standard ingestion)"),
    "cloudwatch_alarm_per_month": (
        0.10,
        "https://aws.amazon.com/cloudwatch/pricing/ (standard-resolution alarm metric)",
    ),
    "cloudwatch_dashboard_per_month": (3.00, "https://aws.amazon.com/cloudwatch/pricing/ (per dashboard)"),
    "cloudwatch_getmetricdata_per_thousand_metrics": (0.01, "https://aws.amazon.com/cloudwatch/pricing/"),
    "cloudwatch_logs_insights_per_gb_scanned": (0.005, "https://aws.amazon.com/cloudwatch/pricing/ (Logs Insights)"),
}

HOURS_PER_MONTH = 730
SFN_EXPRESS_BILLING_MB = 64  # Express memory is billed in 64 MB chunks; the lab's tiny payloads fit in one
SFN_EXPRESS_BILLING_MS = 100  # ... and duration in 100 ms increments


def price(key: str) -> float:
    return PRICES[key][0]


def _line(item: str, quantity: float, unit: str, unit_key: str, arithmetic: str, cost: float) -> dict:
    return {
        "item": item,
        "quantity": quantity,
        "unit": unit,
        "unit_price_usd": price(unit_key),
        "price_source": PRICES[unit_key][1],
        "arithmetic": arithmetic,
        "cost_usd": cost,
    }


def sfn_billed_gb_seconds(executions: int, avg_duration_ms: float) -> float:
    """Express billed duration, estimated from the average: round the average UP to 100 ms.

    Exact billing rounds each execution individually; with only an average available this
    is an estimate and is labelled as one in the output.
    """
    if executions <= 0:
        return 0.0
    billed_ms = max(
        SFN_EXPRESS_BILLING_MS, math.ceil(avg_duration_ms / SFN_EXPRESS_BILLING_MS) * SFN_EXPRESS_BILLING_MS
    )
    return executions * (billed_ms / 1000.0) * (SFN_EXPRESS_BILLING_MB / 1024.0)


def estimate(usage: dict) -> dict:
    """usage keys (all measured by loadtest.py unless noted):

    api_requests            API Gateway Count
    lambda                  {name: {"invocations": n, "duration_ms_sum": ms (Duration metric),
                                    "billed_ms_sum": ms (REPORT records, preferred), "memory_mb": mb}}
    sfn_executions          ExecutionsStarted
    sfn_avg_duration_ms     ExecutionTime average
    dynamodb_wcu, dynamodb_rcu   Consumed*CapacityUnits sums
    sqs_requests            upper bound: sent + received + deleted + empty receives
    xray_traces             traces recorded in the window (GetTraceSummaries count)
    xray_traces_retrieved   traces the scripts themselves scanned/retrieved
    log_bytes               CloudWatch Logs IncomingBytes for the lab's log groups
    alarms, dashboards      counts (from Terraform outputs)
    wall_hours              hours the stack existed for this run (alarms/dashboard are prorated per hour)
    getmetricdata_metrics   metrics requested by loadtest.py itself
    logs_insights_bytes     bytes scanned by loadtest.py's Logs Insights queries
    """
    lines = []

    n = usage.get("api_requests", 0)
    lines.append(
        _line(
            "API Gateway REST requests",
            n,
            "requests",
            "apigw_rest_per_million",
            f"{n} x ${price('apigw_rest_per_million')}/1M",
            n * price("apigw_rest_per_million") / 1e6,
        )
    )

    inv_total = 0
    gb_s_total = 0.0
    parts = []
    for name, fn in sorted((usage.get("lambda") or {}).items()):
        inv = fn.get("invocations", 0)
        # Prefer the REPORT records' billed duration (includes INIT); else the Duration metric (excludes it).
        ms, src = (
            (fn["billed_ms_sum"], "billed")
            if "billed_ms_sum" in fn
            else (fn.get("duration_ms_sum", 0.0), "Duration metric")
        )
        gb_s = ms / 1000.0 * fn.get("memory_mb", 128) / 1024.0
        inv_total += inv
        gb_s_total += gb_s
        parts.append(f"{name} {ms:.0f} ms ({src}) x {fn.get('memory_mb', 128)} MB")
    lines.append(
        _line(
            "Lambda requests (all 4 functions)",
            inv_total,
            "invocations",
            "lambda_requests_per_million",
            f"{inv_total} x ${price('lambda_requests_per_million')}/1M",
            inv_total * price("lambda_requests_per_million") / 1e6,
        )
    )
    lines.append(
        _line(
            "Lambda duration (arm64)",
            round(gb_s_total, 4),
            "GB-seconds",
            "lambda_arm64_gb_second",
            f"({'; '.join(parts) or 'none'}) = {gb_s_total:.4f} GB-s x ${price('lambda_arm64_gb_second')}",
            gb_s_total * price("lambda_arm64_gb_second"),
        )
    )

    ex = usage.get("sfn_executions", 0)
    avg = usage.get("sfn_avg_duration_ms", 0.0)
    sfn_gb_s = sfn_billed_gb_seconds(ex, avg)
    lines.append(
        _line(
            "Step Functions Express requests",
            ex,
            "executions",
            "sfn_express_requests_per_million",
            f"{ex} x ${price('sfn_express_requests_per_million')}/1M",
            ex * price("sfn_express_requests_per_million") / 1e6,
        )
    )
    lines.append(
        _line(
            "Step Functions Express duration (estimate)",
            round(sfn_gb_s, 4),
            "GB-seconds",
            "sfn_express_gb_second",
            f"{ex} x ceil({avg:.1f} ms -> 100 ms) x {SFN_EXPRESS_BILLING_MB} MB = {sfn_gb_s:.4f} GB-s"
            f" x ${price('sfn_express_gb_second')}",
            sfn_gb_s * price("sfn_express_gb_second"),
        )
    )

    wcu = usage.get("dynamodb_wcu", 0.0)
    rcu = usage.get("dynamodb_rcu", 0.0)
    lines.append(
        _line(
            "DynamoDB write request units",
            wcu,
            "WRU",
            "dynamodb_write_request_units_per_million",
            f"{wcu:g} x ${price('dynamodb_write_request_units_per_million')}/1M",
            wcu * price("dynamodb_write_request_units_per_million") / 1e6,
        )
    )
    lines.append(
        _line(
            "DynamoDB read request units",
            rcu,
            "RRU",
            "dynamodb_read_request_units_per_million",
            f"{rcu:g} x ${price('dynamodb_read_request_units_per_million')}/1M",
            rcu * price("dynamodb_read_request_units_per_million") / 1e6,
        )
    )

    sq = usage.get("sqs_requests", 0)
    lines.append(
        _line(
            "SQS requests (upper bound)",
            sq,
            "requests",
            "sqs_standard_requests_per_million",
            f"{sq} x ${price('sqs_standard_requests_per_million')}/1M",
            sq * price("sqs_standard_requests_per_million") / 1e6,
        )
    )

    xr = usage.get("xray_traces", 0)
    xg = usage.get("xray_traces_retrieved", 0)
    lines.append(
        _line(
            "X-Ray traces recorded",
            xr,
            "traces",
            "xray_traces_recorded_per_million",
            f"{xr} x ${price('xray_traces_recorded_per_million')}/1M",
            xr * price("xray_traces_recorded_per_million") / 1e6,
        )
    )
    lines.append(
        _line(
            "X-Ray traces retrieved/scanned",
            xg,
            "traces",
            "xray_traces_retrieved_per_million",
            f"{xg} x ${price('xray_traces_retrieved_per_million')}/1M",
            xg * price("xray_traces_retrieved_per_million") / 1e6,
        )
    )

    lb = usage.get("log_bytes", 0)
    gb = lb / 1024**3
    lines.append(
        _line(
            "CloudWatch Logs ingestion",
            lb,
            "bytes",
            "cloudwatch_logs_ingest_per_gb",
            f"{lb} B = {gb:.6f} GB x ${price('cloudwatch_logs_ingest_per_gb')}",
            gb * price("cloudwatch_logs_ingest_per_gb"),
        )
    )

    hours = usage.get("wall_hours", 1.0)
    al = usage.get("alarms", 0)
    lines.append(
        _line(
            "CloudWatch alarms (prorated)",
            al,
            "alarms",
            "cloudwatch_alarm_per_month",
            f"{al} x ${price('cloudwatch_alarm_per_month')}/month x {hours:g} h / {HOURS_PER_MONTH} h",
            al * price("cloudwatch_alarm_per_month") * hours / HOURS_PER_MONTH,
        )
    )
    db = usage.get("dashboards", 0)
    lines.append(
        _line(
            "CloudWatch dashboard (prorated)",
            db,
            "dashboards",
            "cloudwatch_dashboard_per_month",
            f"{db} x ${price('cloudwatch_dashboard_per_month')}/month x {hours:g} h / {HOURS_PER_MONTH} h",
            db * price("cloudwatch_dashboard_per_month") * hours / HOURS_PER_MONTH,
        )
    )
    gm = usage.get("getmetricdata_metrics", 0)
    lines.append(
        _line(
            "CloudWatch GetMetricData (this report)",
            gm,
            "metrics",
            "cloudwatch_getmetricdata_per_thousand_metrics",
            f"{gm} x ${price('cloudwatch_getmetricdata_per_thousand_metrics')}/1000",
            gm * price("cloudwatch_getmetricdata_per_thousand_metrics") / 1000,
        )
    )

    ib = usage.get("logs_insights_bytes", 0)
    lines.append(
        _line(
            "CloudWatch Logs Insights (this report)",
            ib,
            "bytes scanned",
            "cloudwatch_logs_insights_per_gb_scanned",
            f"{ib:g} B / 1024^3 x ${price('cloudwatch_logs_insights_per_gb_scanned')}",
            ib / 1024**3 * price("cloudwatch_logs_insights_per_gb_scanned"),
        )
    )

    total = sum(line["cost_usd"] for line in lines)
    orders = usage.get("orders_persisted", 0)
    return {
        "prices_as_of": PRICES_AS_OF,
        "lines": lines,
        "total_usd": total,
        "orders_persisted": orders,
        "usd_per_1000_orders": (total / orders * 1000) if orders else None,
        "note": "List price before the AWS free tier. Linear extrapolation per 1,000 orders includes the fixed "
        "(prorated alarm/dashboard) lines, so it overstates the marginal cost at scale.",
    }


def render_markdown(result: dict) -> str:
    rows = ["| Item | Quantity | Arithmetic | Cost (USD) |", "|---|---|---|---|"]
    for line in result["lines"]:
        rows.append(
            f"| {line['item']} | {line['quantity']:g} {line['unit']} | {line['arithmetic']} | ${line['cost_usd']:.6f} |"
        )
    rows.append(f"| **Total (list price, before free tier)** | | | **${result['total_usd']:.4f}** |")
    per_k = result.get("usd_per_1000_orders")
    tail = f"\n\nPer 1,000 persisted orders: ${per_k:.4f}." if per_k is not None else ""
    return "\n".join(rows) + tail + f"\n\nPrices: {result['prices_as_of']}."
