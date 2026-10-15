"""Worker Lambda with a stub Step Functions client: the partial-batch-failure contract."""

import json

import pytest

from orders import worker


class StubSfn:
    def __init__(self, outcome):
        self.outcome = outcome  # callable(input_dict) -> response dict, or raises
        self.calls = []

    def start_sync_execution(self, **kw):
        self.calls.append(kw)
        return self.outcome(json.loads(kw["input"]))


def record(mid, body, receive_count="1", trace=None):
    attrs = {"ApproximateReceiveCount": receive_count}
    if trace:
        attrs["AWSTraceHeader"] = trace
    return {"messageId": mid, "body": body if isinstance(body, str) else json.dumps(body), "attributes": attrs}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("STATE_MACHINE_ARN", "arn:aws:states:us-east-1:000000000000:stateMachine:test")


@pytest.mark.parametrize("parallelism", ["1", "4"])
def test_only_failed_messages_are_reported(monkeypatch, parallelism):
    monkeypatch.setenv("BATCH_PARALLELISM", parallelism)

    def outcome(inp):
        if inp["order_id"] == "poison":
            return {"status": "FAILED", "error": "UnknownSkuError", "cause": "unknown SKU(s): SKU-NEW"}
        return {"status": "SUCCEEDED"}

    stub = StubSfn(outcome)
    monkeypatch.setattr(worker, "_sfn", stub)
    batch = [
        record("m1", {"order_id": "ok-1"}),
        record("m2", {"order_id": "poison"}),
        record("m3", {"order_id": "ok-2"}),
    ]
    assert worker.handler({"Records": batch}, None) == {"batchItemFailures": [{"itemIdentifier": "m2"}]}
    assert len(stub.calls) == 3


def test_execution_input_carries_message_metadata_and_trace(monkeypatch):
    stub = StubSfn(lambda inp: {"status": "SUCCEEDED"})
    monkeypatch.setattr(worker, "_sfn", stub)
    worker.handler({"Records": [record("m1", {"order_id": "o-1"}, receive_count="2", trace="Root=1-abc")]}, None)
    call = stub.calls[0]
    assert call["stateMachineArn"].endswith(":stateMachine:test")
    assert call["traceHeader"] == "Root=1-abc"
    assert json.loads(call["input"]) == {"order_id": "o-1", "message_id": "m1", "receive_count": "2"}


@pytest.mark.parametrize("body", ["not json", "[1, 2]"])
def test_unparseable_message_fails_without_calling_the_workflow(monkeypatch, body):
    stub = StubSfn(lambda inp: {"status": "SUCCEEDED"})
    monkeypatch.setattr(worker, "_sfn", stub)
    assert worker.handler({"Records": [record("bad", body)]}, None) == {
        "batchItemFailures": [{"itemIdentifier": "bad"}]
    }
    assert stub.calls == []


@pytest.mark.parametrize("status", ["FAILED", "TIMED_OUT"])
def test_non_success_status_is_a_failure(monkeypatch, status):
    monkeypatch.setattr(worker, "_sfn", StubSfn(lambda inp: {"status": status}))
    assert worker.handler({"Records": [record("m1", {"order_id": "o"})]}, None)["batchItemFailures"] == [
        {"itemIdentifier": "m1"}
    ]


def test_throttled_start_is_retried_via_sqs_not_raised(monkeypatch):
    def boom(inp):
        raise RuntimeError("ThrottlingException")

    monkeypatch.setattr(worker, "_sfn", StubSfn(boom))
    assert worker.handler({"Records": [record("m1", {"order_id": "o"})]}, None)["batchItemFailures"] == [
        {"itemIdentifier": "m1"}
    ]


def test_empty_event():
    assert worker.handler({"Records": []}, None) == {"batchItemFailures": []}
