"""Shared helpers for smoke.py, loadtest.py and redrive.py (stdlib + boto3 only)."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import boto3


def load_outputs(path: str) -> dict:
    """Read `terraform output -json` and flatten {"name": {"value": v}} to {"name": v}."""
    raw = json.loads(Path(path).read_text())
    return {k: (v["value"] if isinstance(v, dict) and "value" in v else v) for k, v in raw.items()}


def session(outputs: dict):
    return boto3.session.Session(region_name=outputs.get("region") or os.environ.get("AWS_REGION", "us-east-1"))


def api_key(outputs: dict, sess) -> str:
    """The usage-plan API key. Read at run time from API Gateway, never from a file
    or a Terraform output, so it never lands in an uploaded artifact."""
    if os.environ.get("LAB09_API_KEY"):
        return os.environ["LAB09_API_KEY"]
    value = sess.client("apigateway").get_api_key(apiKey=outputs["api_key_id"], includeValue=True)["value"]
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::add-mask::{value}", flush=True)
    return value


def post_json(url: str, body, headers: dict | None = None, timeout: float = 15.0) -> tuple[int, dict | str, dict]:
    data = body if isinstance(body, (bytes, str)) else json.dumps(body)
    if isinstance(data, str):
        data = data.encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("content-type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw, hdrs = resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        status, raw, hdrs = exc.code, exc.read(), dict(exc.headers or {})
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = raw.decode("utf-8", "replace")
    return status, parsed, hdrs


class Redactor:
    """Replace the AWS account id in everything written to evidence (the repo is public)."""

    def __init__(self, account_id: str | None):
        self.account_id = account_id

    def __call__(self, obj):
        text = obj if isinstance(obj, str) else json.dumps(obj, indent=2, sort_keys=True, default=str)
        if self.account_id:
            text = text.replace(self.account_id, "<ACCOUNT_ID>")
        return text


def account_redactor(sess) -> Redactor:
    try:
        return Redactor(sess.client("sts").get_caller_identity()["Account"])
    except Exception:  # redaction must never be the reason a run fails; fall back to a regex
        return Redactor(None)


ACCOUNT_RE = re.compile(r"(?<![0-9])[0-9]{12}(?![0-9])")


class Evidence:
    def __init__(self, directory: str, redact: Redactor):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.redact = redact
        self.results: list[tuple[str, str]] = []
        self.log_lines: list[str] = []

    def write(self, name: str, obj) -> None:
        text = self.redact(obj)
        if self.redact.account_id is None:
            text = ACCOUNT_RE.sub("<ACCOUNT_ID>", text)
        (self.dir / name).write_text(text + ("\n" if not text.endswith("\n") else ""))

    def log(self, msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        self.log_lines.append(line)

    def check(self, ok: bool | None, name: str, detail: str = "") -> bool:
        """ok=True PASS, ok=False FAIL, ok=None WARN (timing-dependent, not asserted)."""
        level = "PASS" if ok else ("WARN" if ok is None else "FAIL")
        line = f"{level} {name}" + (f" - {detail}" if detail else "")
        print(line, flush=True)
        self.results.append((level, line))
        self.log_lines.append(line)
        return bool(ok)

    @property
    def failed(self) -> bool:
        return any(level == "FAIL" for level, _ in self.results)

    def close(self, logname: str) -> None:
        self.write(logname, "\n".join(self.log_lines))


def wait_until(fn, timeout: float, interval: float = 5.0):
    """Call fn() until it returns a truthy value or the timeout passes; return the last value."""
    deadline = time.monotonic() + timeout
    value = fn()
    while not value and time.monotonic() < deadline:
        time.sleep(interval)
        value = fn()
    return value


def run_id() -> str:
    """Unique per workflow run AND attempt (a re-run must not find the previous attempt's orders)."""
    if os.environ.get("GITHUB_RUN_ID"):
        return f"{os.environ['GITHUB_RUN_ID']}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
    return time.strftime("%Y%m%d%H%M%S", time.gmtime())


def die(msg: str) -> None:
    print(f"FAIL {msg}", file=sys.stderr, flush=True)
    sys.exit(1)
