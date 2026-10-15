"""Test setup: import src/ (the Lambda package) and tools/ without installing anything,
and make sure no test can reach real AWS (fake credentials, moto intercepts calls)."""

import os
import sys
from pathlib import Path

import pytest

sys.dont_write_bytecode = True  # keep src/ free of __pycache__ (it is zipped as the Lambda package)
LAB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB / "src"))
sys.path.insert(0, str(LAB / "tools"))


@pytest.fixture(autouse=True)
def fake_aws(monkeypatch):
    for k in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("_X_AMZN_TRACE_ID", raising=False)
    yield


def pytest_configure(config):
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
