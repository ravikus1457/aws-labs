"""The cost arithmetic and latency maths the load test reports (pure functions)."""

import math

import pytest

import cost
import stats


def test_percentile_nearest_rank():
    vals = list(range(1, 101))  # 1..100
    assert stats.percentile(vals, 50) == 50
    assert stats.percentile(vals, 99) == 99
    assert stats.percentile(vals, 100) == 100
    assert stats.percentile([7.0], 99) == 7.0
    assert stats.percentile([], 50) is None
    with pytest.raises(ValueError):
        stats.percentile(vals, 0)


def test_summarize():
    s = stats.summarize([10.0, 20.0, 30.0, 40.0])
    assert s["count"] == 4 and s["p50"] == 20.0 and s["max"] == 40.0 and s["mean"] == 25.0
    assert stats.summarize([]) == {"count": 0}


def test_max_in_flight():
    assert stats.max_in_flight([(0, 10), (1, 2), (3, 4), (5, 11)]) == 2
    assert stats.max_in_flight([(0, 1), (1, 2), (2, 3)]) == 1  # back-to-back is not overlap
    assert stats.max_in_flight([]) == 0


def test_sfn_billing_rounds_up_to_100ms_and_64mb():
    # 2,000 executions averaging 140 ms bill as 200 ms x 64 MB each
    assert math.isclose(cost.sfn_billed_gb_seconds(2000, 140.0), 2000 * 0.2 * 0.0625)
    assert math.isclose(cost.sfn_billed_gb_seconds(10, 1.0), 10 * 0.1 * 0.0625)  # 100 ms minimum
    assert cost.sfn_billed_gb_seconds(0, 50.0) == 0.0


def test_estimate_matches_hand_arithmetic():
    usage = {
        "api_requests": 2000,
        "lambda": {"ingest": {"invocations": 2000, "duration_ms_sum": 60000.0, "memory_mb": 128}},
        "sfn_executions": 2000,
        "sfn_avg_duration_ms": 140.0,
        "dynamodb_wcu": 2000,
        "dynamodb_rcu": 4000,
        "sqs_requests": 6000,
        "xray_traces": 100,
        "xray_traces_retrieved": 100,
        "log_bytes": 1024**3,  # exactly 1 GB
        "alarms": 5,
        "dashboards": 1,
        "wall_hours": 1.0,
        "getmetricdata_metrics": 1000,
        "orders_persisted": 2000,
    }
    r = cost.estimate(usage)
    by = {line["item"]: line["cost_usd"] for line in r["lines"]}
    assert math.isclose(by["API Gateway REST requests"], 2000 * 3.50 / 1e6)
    assert math.isclose(by["Lambda requests (all 4 functions)"], 2000 * 0.20 / 1e6)
    assert math.isclose(by["Lambda duration (arm64)"], 60.0 * 0.125 * 0.0000133334)  # 7.5 GB-s
    assert math.isclose(by["Step Functions Express requests"], 2000 * 1.00 / 1e6)
    assert math.isclose(by["Step Functions Express duration (estimate)"], 25.0 * 0.00001667)
    assert math.isclose(by["DynamoDB write request units"], 2000 * 0.625 / 1e6)
    assert math.isclose(by["DynamoDB read request units"], 4000 * 0.125 / 1e6)
    assert math.isclose(by["SQS requests (upper bound)"], 6000 * 0.40 / 1e6)
    assert math.isclose(by["CloudWatch Logs ingestion"], 0.50)
    assert math.isclose(by["CloudWatch alarms (prorated)"], 5 * 0.10 / 730)
    assert math.isclose(by["CloudWatch dashboard (prorated)"], 3.00 / 730)
    assert math.isclose(by["CloudWatch GetMetricData (this report)"], 0.01)
    assert math.isclose(r["total_usd"], sum(by.values()))
    assert math.isclose(r["usd_per_1000_orders"], r["total_usd"] / 2)
    for line in r["lines"]:
        assert line["arithmetic"] and line["price_source"].startswith("https://")


def test_billed_duration_is_preferred_over_the_duration_metric():
    fn = {"invocations": 10, "duration_ms_sum": 1000.0, "billed_ms_sum": 3000.0, "memory_mb": 1024}
    r = cost.estimate({"lambda": {"ingest": fn}})
    line = next(x for x in r["lines"] if x["item"] == "Lambda duration (arm64)")
    assert math.isclose(line["quantity"], 3.0) and "(billed)" in line["arithmetic"]
    del fn["billed_ms_sum"]
    line = next(x for x in cost.estimate({"lambda": {"ingest": fn}})["lines"] if x["item"] == "Lambda duration (arm64)")
    assert math.isclose(line["quantity"], 1.0) and "Duration metric" in line["arithmetic"]


def test_markdown_has_a_row_per_line_and_the_total():
    r = cost.estimate({"api_requests": 10, "orders_persisted": 10})
    md = cost.render_markdown(r)
    assert md.count("\n|") == len(r["lines"]) + 2  # separator + rows + total
    assert "Total (list price, before free tier)" in md
