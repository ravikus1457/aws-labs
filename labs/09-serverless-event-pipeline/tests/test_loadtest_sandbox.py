"""tools/loadtest.py end to end in a sandbox: a local HTTP server stands in for API Gateway
and runs the REAL handlers (ingest -> queue -> validate -> enrich -> persist) against moto,
then the load test's full analysis path runs and must write its evidence.

What this proves: the script's request, read-back, metrics, cost and report code runs
start to finish and counts correctly (every accepted order found, 400s counted as
failures). What it cannot prove: AWS numbers (moto has no real CloudWatch data), so the
CloudWatch-derived fields are only checked for shape.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import boto3
import pytest
from moto import mock_aws

import loadtest
from orders import ingest, model, steps

CATALOG = {"SKU-MUG": 1299, "SKU-TSHIRT": 1999, "SKU-STICKER": 299}
LOCK = threading.Lock()  # moto's in-memory backends are not built for concurrent writers


def make_server(sqs, queue_url, ddb):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["content-length"])).decode()
            if self.headers.get("x-api-key") != "sandbox-key":
                return self._send(403, {"message": "Forbidden"})
            if json.loads(body).get("customer_id") == "lt-customer-13":  # inject some client errors
                body = json.dumps({"customer_id": "x", "items": []})
            with LOCK:
                resp = ingest.handler({"body": body, "headers": dict(self.headers)}, None)
                if resp["statusCode"] == 202:
                    self._pipeline(sqs, queue_url, ddb)
            self._send(resp["statusCode"], json.loads(resp["body"]))

        def _pipeline(self, sqs, queue_url, ddb):
            for msg in sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10).get("Messages", []):
                order = steps.enrich(steps.validate(json.loads(msg["Body"]), None), None)
                # what the Persist state writes (statemachine/order-workflow.asl.json)
                ddb.put_item(
                    TableName="orders",
                    Item={
                        "pk": {"S": f"ORDER#{order['order_id']}"},
                        "order_id": {"S": order["order_id"]},
                        "accepted_at": {"S": order["accepted_at"]},
                        "persisted_at": {"S": model.utc_now_iso()},
                        "receive_count": {"N": order["receive_count"]},
                        "total_cents": {"N": order["total_cents"]},
                    },
                )
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])

        def _send(self, status, obj):
            raw = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    with mock_aws():
        sqs = boto3.client("sqs", region_name="us-east-1")
        queue_url = sqs.create_queue(QueueName="awslabs-lab09-orders")["QueueUrl"]
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="orders",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
        )
        for sku, price in CATALOG.items():
            ddb.put_item(TableName="orders", Item={"pk": {"S": f"SKU#{sku}"}, "price_cents": {"N": str(price)}})
        monkeypatch.setenv("QUEUE_URL", queue_url)
        monkeypatch.setenv("TABLE_NAME", "orders")
        monkeypatch.setattr(ingest, "_sqs", sqs)
        monkeypatch.setattr(steps, "_ddb", ddb)

        server = make_server(sqs, queue_url, ddb)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        fns = {r: f"awslabs-lab09-{r}" for r in ("ingest", "worker", "validate", "enrich")}
        logs = boto3.client("logs", region_name="us-east-1")
        for name in fns.values():
            logs.create_log_group(logGroupName=f"/aws/lambda/{name}")
        outputs = {
            "region": "us-east-1",
            "api_url": f"http://127.0.0.1:{server.server_address[1]}/v1/orders",
            "api_key_id": "unused-in-sandbox",
            "rest_api_name": "awslabs-lab09-orders-api",
            "stage_name": "v1",
            "table_name": "orders",
            "queue_name": "awslabs-lab09-orders",
            "state_machine_arn": "arn:aws:states:us-east-1:123456789012:stateMachine:awslabs-lab09-order-workflow",
            "function_names": fns,
            "function_memory_mb": dict.fromkeys(fns, 128),
            "log_group_names": [f"/aws/lambda/{n}" for n in fns.values()],
            "alarm_names": {"dlq_not_empty": "a", "api_5xx_rate": "b"},
            "catalog": CATALOG,
        }
        (tmp_path / "outputs.json").write_text(json.dumps({k: {"value": v} for k, v in outputs.items()}))
        monkeypatch.setenv("LAB09_API_KEY", "sandbox-key")
        # No real waiting in a unit test: sleeps are no-ops and polls get three tries.
        monkeypatch.setattr(loadtest.time, "sleep", lambda s: None)

        def quick_wait(fn, timeout, interval=5.0):
            for _ in range(3):
                value = fn()
                if value:
                    return value
            return value

        monkeypatch.setattr(loadtest, "wait_until", quick_wait)
        yield tmp_path
        server.shutdown()


def run(tmp_path, monkeypatch, *extra):
    argv = ["loadtest.py", "--outputs", str(tmp_path / "outputs.json"), "--evidence", str(tmp_path / "ev"), *extra]
    monkeypatch.setattr("sys.argv", argv)
    return loadtest.main()


def test_loadtest_runs_end_to_end_and_counts_correctly(sandbox, monkeypatch):
    rc = run(sandbox, monkeypatch, "--requests", "60", "--concurrency", "6", "--min-success", "0.5")
    ev = sandbox / "ev"
    report = json.loads((ev / "loadtest.json").read_text())
    # customer lt-customer-13 is i % 97 == 13 -> exactly one of 60 requests is turned into a 400
    assert report["status_codes"] == {"202": 59, "400": 1}
    assert report["accepted"] == 59 and report["persisted"] == 59 and report["missing"] == 0
    assert 0 < report["max_concurrency"]["client_peak_in_flight"] <= 6
    assert report["max_concurrency"]["distinct_ingest_environments"] == 1  # one process = one "environment"
    assert report["end_to_end_ms"]["count"] == 59
    assert rc == 0
    bill = json.loads((ev / "cost.json").read_text())
    assert bill["lines"] and bill["total_usd"] >= 0
    summary = (ev / "summary.md").read_text()
    assert "Success rate" in summary and "Cost of this run" in summary
    assert len(json.loads((ev / "requests.json").read_text())) == 60


def test_loadtest_fails_when_success_rate_is_below_the_bar(sandbox, monkeypatch):
    assert run(sandbox, monkeypatch, "--requests", "60", "--concurrency", "4", "--min-success", "0.99") == 1
