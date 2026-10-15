"""tools/smoke.py end to end in a sandbox.

A local HTTP server plays API Gateway (403 without the key) and runs the REAL ingest,
validate and enrich handlers against moto SQS/DynamoDB. A small fake plays the parts
moto cannot: SQS's maxReceiveCount move to the DLQ, the CloudWatch alarm reacting to
the DLQ, StartMessageMoveTask, and the read-only config/X-Ray lookups.

It proves the smoke script's control flow and assertions (all PASS on a healthy
pipeline) and - the important half - that it reports FAIL when the fault path is
broken: a DLQ alarm that never fires, and a "fix" that does not fix.
"""

import contextlib
import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import boto3
import pytest
from moto import mock_aws

import common
import redrive
import smoke
from orders import ingest, model, steps

CATALOG = {"SKU-MUG": 1299, "SKU-TSHIRT": 1999}
LOCK = threading.RLock()


class World:
    """The bits of the AWS pipeline moto does not emulate."""

    def __init__(self, sqs, ddb, queue_url, dlq_url, alarm_works=True, fix_works=True):
        self.sqs, self.ddb, self.queue_url, self.dlq_url = sqs, ddb, queue_url, dlq_url
        self.alarm_works, self.fix_works = alarm_works, fix_works
        self.alarm = {"StateValue": "OK", "StateUpdatedTimestamp": datetime.now(UTC)}

    def pump(self):
        """Process everything on the orders queue like worker + state machine would."""
        with LOCK:
            while True:
                msgs = self.sqs.receive_message(QueueUrl=self.queue_url, MaxNumberOfMessages=10).get("Messages", [])
                if not msgs:
                    return
                for msg in msgs:
                    body = json.loads(msg["Body"])
                    try:
                        if not self.fix_works and body["items"][0]["sku"].startswith("SKU-LAUNCH-"):
                            raise model.UnknownSkuError([body["items"][0]["sku"]])
                        order = steps.enrich(steps.validate(body, None), None)
                    except model.UnknownSkuError:
                        # maxReceiveCount exhausted -> SQS moves it to the DLQ; the alarm sees the DLQ
                        self.sqs.send_message(QueueUrl=self.dlq_url, MessageBody=msg["Body"])
                        if self.alarm_works:
                            self.alarm = {"StateValue": "ALARM", "StateUpdatedTimestamp": datetime.now(UTC)}
                    else:
                        item = {
                            "pk": {"S": f"ORDER#{order['order_id']}"},
                            "order_id": {"S": order["order_id"]},
                            "status": {"S": "PERSISTED"},
                            "total_cents": {"N": order["total_cents"]},
                            "accepted_at": {"S": order["accepted_at"]},
                            "persisted_at": {"S": model.utc_now_iso()},
                            "receive_count": {"N": order["receive_count"]},
                        }
                        # the Persist state's conditional write: a duplicate is a no-op, not an error
                        with contextlib.suppress(self.ddb.exceptions.ConditionalCheckFailedException):
                            self.ddb.put_item(
                                TableName="orders", Item=item, ConditionExpression="attribute_not_exists(pk)"
                            )
                    self.sqs.delete_message(QueueUrl=self.queue_url, ReceiptHandle=msg["ReceiptHandle"])


class SqsWithMoveTasks:
    def __init__(self, real, world):
        self.real, self.world, self.done = real, world, False

    def __getattr__(self, name):
        return getattr(self.real, name)

    def start_message_move_task(self, SourceArn, DestinationArn):
        with LOCK:
            for msg in self.real.receive_message(QueueUrl=self.world.dlq_url, MaxNumberOfMessages=10).get(
                "Messages", []
            ):
                self.real.send_message(QueueUrl=self.world.queue_url, MessageBody=msg["Body"])
                self.real.delete_message(QueueUrl=self.world.dlq_url, ReceiptHandle=msg["ReceiptHandle"])
        self.world.pump()
        return {"TaskHandle": "h"}

    def list_message_move_tasks(self, SourceArn, MaxResults):
        return {"Results": [{"TaskHandle": "h", "Status": "COMPLETED", "ApproximateNumberOfMessagesMoved": 1}]}

    def purge_queue(self, QueueUrl):
        return self.real.purge_queue(QueueUrl=QueueUrl)


class Stub:
    def __init__(self, **methods):
        self.__dict__.update(methods)


def make_server(world):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["content-length"])).decode()
            if self.headers.get("x-api-key") != "sandbox-key":
                resp = {"statusCode": 403, "body": json.dumps({"message": "Forbidden"})}
            else:
                with LOCK:
                    resp = ingest.handler({"body": body, "headers": dict(self.headers)}, None)
                world.pump()
            raw = resp["body"].encode()
            self.send_response(resp["statusCode"])
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def sandbox_run(monkeypatch, tmp_path, **world_kw):
    with mock_aws():
        sqs = boto3.client("sqs", region_name="us-east-1")
        queue_url = sqs.create_queue(QueueName="awslabs-lab09-orders")["QueueUrl"]
        dlq_url = sqs.create_queue(QueueName="awslabs-lab09-orders-dlq")["QueueUrl"]
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="orders",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
        )
        for sku, price in CATALOG.items():
            ddb.put_item(TableName="orders", Item={"pk": {"S": f"SKU#{sku}"}, "price_cents": {"N": str(price)}})
        world = World(sqs, ddb, queue_url, dlq_url, **world_kw)
        monkeypatch.setenv("QUEUE_URL", queue_url)
        monkeypatch.setenv("TABLE_NAME", "orders")
        monkeypatch.setenv("LAB09_API_KEY", "sandbox-key")
        monkeypatch.setattr(ingest, "_sqs", sqs)
        monkeypatch.setattr(steps, "_ddb", ddb)
        server = make_server(world)
        threading.Thread(target=server.serve_forever, daemon=True).start()

        arn = "arn:aws:states:us-east-1:123456789012:stateMachine:awslabs-lab09-order-workflow"
        dlq_arn = "arn:aws:sqs:us-east-1:123456789012:awslabs-lab09-orders-dlq"
        sqs.set_queue_attributes(
            QueueUrl=queue_url,
            Attributes={"RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": "2"})},
        )
        fns = {r: f"awslabs-lab09-{r}" for r in ("ingest", "worker", "validate", "enrich")}
        clients = {
            "sqs": SqsWithMoveTasks(sqs, world),
            "dynamodb": ddb,
            "sts": boto3.client("sts", region_name="us-east-1"),
            "lambda": Stub(
                get_function_configuration=lambda FunctionName: {
                    "Runtime": "python3.12",
                    "Architectures": ["arm64"],
                    "MemorySize": 128,
                    "TracingConfig": {"Mode": "Active"},
                },
                list_event_source_mappings=lambda FunctionName: {
                    "EventSourceMappings": [
                        {
                            "FunctionResponseTypes": ["ReportBatchItemFailures"],
                            "State": "Enabled",
                            "BatchSize": 10,
                            "ScalingConfig": {"MaximumConcurrency": 5},
                        }
                    ]
                },
            ),
            "apigateway": Stub(get_stage=lambda restApiId, stageName: {"tracingEnabled": True}),
            "stepfunctions": Stub(
                describe_state_machine=lambda stateMachineArn: {
                    "type": "EXPRESS",
                    "tracingConfiguration": {"enabled": True},
                }
            ),
            "cloudwatch": Stub(
                describe_alarms=lambda AlarmNames=None, AlarmNamePrefix=None: {
                    "MetricAlarms": [{"AlarmName": "awslabs-lab09-dlq-not-empty", **world.alarm}]
                }
            ),
            "xray": Stub(
                get_trace_summaries=lambda **kw: {
                    "TraceSummaries": [
                        {
                            "Id": "1-x",
                            "ServiceIds": [
                                {"Type": "AWS::ApiGateway::Stage"},
                                {"Type": "AWS::Lambda::Function"},
                                {"Type": "AWS::StepFunctions::StateMachine"},
                            ],
                        }
                    ]
                }
            ),
        }
        sess = Stub(client=lambda name: clients[name])
        monkeypatch.setattr(smoke, "session", lambda out: sess)
        monkeypatch.setattr(smoke.time, "sleep", lambda s: None)
        monkeypatch.setattr(redrive.time, "sleep", lambda s: None)

        def quick_wait(fn, timeout, interval=5.0):
            value = None
            for _ in range(3):
                value = fn()
                if value:
                    return value
            return value

        monkeypatch.setattr(smoke, "wait_until", quick_wait)
        outputs = {
            "region": "us-east-1",
            "name_prefix": "awslabs-lab09",
            "api_url": f"http://127.0.0.1:{server.server_address[1]}/v1/orders",
            "api_key_id": "unused",
            "rest_api_id": "abc",
            "stage_name": "v1",
            "queue_url": queue_url,
            "queue_arn": "arn:aws:sqs:us-east-1:123456789012:awslabs-lab09-orders",
            "dlq_url": dlq_url,
            "dlq_arn": dlq_arn,
            "max_receive_count": 2,
            "visibility_timeout_seconds": 90,
            "table_name": "orders",
            "catalog": CATALOG,
            "state_machine_arn": arn,
            "function_names": fns,
            "alarm_names": {"dlq_not_empty": "awslabs-lab09-dlq-not-empty"},
        }
        (tmp_path / "outputs.json").write_text(json.dumps({k: {"value": v} for k, v in outputs.items()}))
        monkeypatch.setattr(
            "sys.argv", ["smoke.py", "--outputs", str(tmp_path / "outputs.json"), "--evidence", str(tmp_path / "ev")]
        )
        try:
            rc = smoke.main()
        finally:
            server.shutdown()
        return rc, json.loads((tmp_path / "ev" / "smoke.json").read_text())


def test_smoke_passes_on_a_healthy_pipeline(monkeypatch, tmp_path):
    rc, summary = sandbox_run(monkeypatch, tmp_path)
    assert summary["fail"] == 0, summary["results"]
    assert rc == 0
    assert summary["pass"] >= 17
    assert any("poison message failed every attempt" in r and r.startswith("PASS") for r in summary["results"])
    assert any("redriven order persisted" in r and r.startswith("PASS") for r in summary["results"])
    assert "123456789012" not in (tmp_path / "ev" / "deployed-config.json").read_text()  # account id redacted


@pytest.mark.parametrize(
    "world_kw, failing_check",
    [
        ({"alarm_works": False}, "-> ALARM after the fault"),
        ({"fix_works": False}, "redriven order persisted after the fix"),
    ],
)
def test_smoke_fails_when_the_fault_path_is_broken(monkeypatch, tmp_path, world_kw, failing_check):
    rc, summary = sandbox_run(monkeypatch, tmp_path, **world_kw)
    assert rc == 1
    assert any(r.startswith("FAIL") and failing_check in r for r in summary["results"]), summary["results"]


def test_evidence_redaction_falls_back_to_regex_without_sts():
    ev = common.Redactor(None)
    assert ev.account_id is None
