"""Ingest Lambda against a moto SQS queue (no AWS account involved)."""

import json

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from orders import ingest

ORDER = {"customer_id": "c-1", "items": [{"sku": "SKU-MUG", "quantity": 2}]}


@pytest.fixture
def queue(monkeypatch):
    with mock_aws():
        sqs = boto3.client("sqs", region_name="us-east-1")
        url = sqs.create_queue(QueueName="orders")["QueueUrl"]
        monkeypatch.setenv("QUEUE_URL", url)
        monkeypatch.setattr(ingest, "_sqs", sqs)
        yield sqs, url


def event(body, headers=None, b64=False):
    return {"body": body if isinstance(body, str) else json.dumps(body), "headers": headers, "isBase64Encoded": b64}


def drain(sqs, url):
    msgs = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10, MessageSystemAttributeNames=["All"])
    return msgs.get("Messages", [])


def test_valid_order_is_queued_and_acknowledged(queue):
    sqs, url = queue
    resp = ingest.handler(event(ORDER), None)
    assert resp["statusCode"] == 202
    body = json.loads(resp["body"])
    assert body["status"] == "ACCEPTED" and len(body["order_id"]) == 32 and body["env"] == ingest.ENV_ID
    [msg] = drain(sqs, url)
    queued = json.loads(msg["Body"])
    assert queued["order_id"] == body["order_id"]
    assert queued["items"] == ORDER["items"] and queued["customer_id"] == "c-1"
    assert queued["accepted_at"].endswith("Z")


def test_idempotency_key_becomes_the_order_id_case_insensitively(queue):
    sqs, url = queue
    resp = ingest.handler(event(ORDER, {"Idempotency-Key": "client-key-0001"}), None)
    assert json.loads(resp["body"])["order_id"] == "client-key-0001"
    assert json.loads(drain(sqs, url)[0]["Body"])["order_id"] == "client-key-0001"


@pytest.mark.parametrize(
    "body, headers",
    [
        ({"customer_id": "c", "items": []}, None),
        ("not json", None),
        (ORDER, {"idempotency-key": "short"}),
        (ORDER, {"idempotency-key": "has spaces in it"}),
    ],
)
def test_bad_requests_are_400_and_never_queued(queue, body, headers):
    sqs, url = queue
    resp = ingest.handler(event(body, headers), None)
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["details"]
    assert drain(sqs, url) == []


def test_queue_failure_is_503_not_a_crash(queue, monkeypatch):
    class Broken:
        def send_message(self, **kw):
            raise ClientError(
                {"Error": {"Code": "AWS.SimpleQueueService.NonExistentQueue", "Message": "gone"}}, "SendMessage"
            )

    monkeypatch.setattr(ingest, "_sqs", Broken())
    resp = ingest.handler(event(ORDER), None)
    assert resp["statusCode"] == 503


def test_trace_header_is_propagated_to_sqs(queue, monkeypatch):
    sqs, url = queue
    monkeypatch.setenv(
        "_X_AMZN_TRACE_ID", "Root=1-5759e988-bd862e3fe1be46a994272793;Parent=53995c3f42cd8ad8;Sampled=1;Lineage=x:0"
    )
    sent = {}
    real = sqs.send_message

    def spy(**kw):
        sent.update(kw)
        return real(**kw)

    monkeypatch.setattr(sqs, "send_message", spy)
    assert ingest.handler(event(ORDER), None)["statusCode"] == 202
    attr = sent["MessageSystemAttributes"]["AWSTraceHeader"]["StringValue"]
    assert attr == "Root=1-5759e988-bd862e3fe1be46a994272793;Parent=53995c3f42cd8ad8;Sampled=1"


def test_rejected_trace_header_falls_back_to_plain_send(queue, monkeypatch):
    monkeypatch.setenv("_X_AMZN_TRACE_ID", "Root=1-5759e988-bd862e3fe1be46a994272793")
    calls = []

    class Picky:
        def send_message(self, **kw):
            calls.append(kw)
            if "MessageSystemAttributes" in kw:
                raise ClientError({"Error": {"Code": "InvalidParameterValue", "Message": "bad"}}, "SendMessage")
            return {"MessageId": "1"}

    monkeypatch.setattr(ingest, "_sqs", Picky())
    assert ingest.handler(event(ORDER), None)["statusCode"] == 202
    assert len(calls) == 2 and "MessageSystemAttributes" not in calls[1]


def test_cold_flag_only_on_first_invocation(queue, monkeypatch):
    monkeypatch.setattr(ingest, "_cold", True)
    first = json.loads(ingest.handler(event(ORDER), None)["body"])
    second = json.loads(ingest.handler(event(ORDER), None)["body"])
    assert first["cold"] is True and second["cold"] is False
