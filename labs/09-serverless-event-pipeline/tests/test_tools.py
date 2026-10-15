"""The AWS-facing helpers of the smoke/load/redrive scripts, against moto (no account)."""

import json

import boto3
import pytest
from moto import mock_aws

import common
import loadtest
import redrive


@pytest.fixture
def aws():
    with mock_aws():
        yield


class FakeMoveTasks:
    """moto does not implement StartMessageMoveTask, so the polling logic is tested on a stub
    that walks the task through RUNNING -> final status like SQS does."""

    def __init__(self, final="COMPLETED", polls_before_done=2):
        self.final, self.left, self.started = final, polls_before_done, []

    def start_message_move_task(self, SourceArn, DestinationArn):
        self.started.append((SourceArn, DestinationArn))
        return {"TaskHandle": "handle-new"}

    def list_message_move_tasks(self, SourceArn, MaxResults):
        self.left -= 1
        status = "RUNNING" if self.left > 0 else self.final
        older = {"TaskHandle": "handle-old", "Status": "COMPLETED"}  # a previous run's task must be ignored
        return {
            "Results": [{"TaskHandle": "handle-new", "Status": status, "ApproximateNumberOfMessagesMoved": 1}, older]
        }


@pytest.mark.parametrize("final", ["COMPLETED", "FAILED"])
def test_redrive_waits_for_its_own_task(monkeypatch, final):
    monkeypatch.setattr(redrive.time, "sleep", lambda s: None)
    fake = FakeMoveTasks(final=final)
    task = redrive.redrive(fake, "arn:dlq", "arn:orders", timeout=30, log=lambda *_: None)
    assert fake.started == [("arn:dlq", "arn:orders")]
    assert task["TaskHandle"] == "handle-new" and task["Status"] == final


def test_redrive_times_out_instead_of_hanging(monkeypatch):
    monkeypatch.setattr(redrive.time, "sleep", lambda s: None)
    task = redrive.redrive(FakeMoveTasks(polls_before_done=10**9), "a", "b", timeout=0.05, log=lambda *_: None)
    assert "timed out" in task["Status"]


def test_dlq_depth_reads_queue_attributes(aws):
    sqs = boto3.client("sqs", region_name="us-east-1")
    dlq = sqs.create_queue(QueueName="lab-dlq")["QueueUrl"]
    sqs.send_message(QueueUrl=dlq, MessageBody="{}")
    assert redrive.dlq_depth(sqs, dlq)["ApproximateNumberOfMessages"] == 1


def test_read_back_finds_persisted_orders_and_reports_missing_ones(aws):
    ddb = boto3.client("dynamodb", region_name="us-east-1")
    ddb.create_table(
        TableName="t",
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
    )
    ids = [f"lt-test-{i:06d}" for i in range(250)]  # > 100: exercises the BatchGetItem chunking
    for oid in ids[:240]:
        ddb.put_item(
            TableName="t",
            Item={
                "pk": {"S": f"ORDER#{oid}"},
                "order_id": {"S": oid},
                "accepted_at": {"S": "2026-10-15T12:00:00.000Z"},
                "persisted_at": {"S": "2026-10-15T12:00:00.250Z"},
                "receive_count": {"N": "1"},
            },
        )
    found = loadtest.read_back(ddb, "t", ids)
    assert len(found) == 240 and set(ids) - set(found) == set(ids[240:])
    assert found[ids[0]]["persisted_at"].endswith(".250Z")


def test_redactor_removes_the_account_id():
    r = common.Redactor("123456789012")
    assert "123456789012" not in r({"arn": "arn:aws:sqs:us-east-1:123456789012:q"})
    assert "<ACCOUNT_ID>" in r("arn:aws:iam::123456789012:role/x")


def test_load_outputs_flattens_terraform_json(tmp_path):
    p = tmp_path / "outputs.json"
    p.write_text(json.dumps({"api_url": {"value": "https://x/v1/orders", "type": "string", "sensitive": False}}))
    assert common.load_outputs(str(p)) == {"api_url": "https://x/v1/orders"}
