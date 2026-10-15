"""Lab 08 Synthetics canary: GET <TARGET_URL> once a minute from outside the VPC.

A run PASSES when the ALB answers 200 with {"status": "ok"} within the timeout, and FAILS
otherwise (the handler raises). CloudWatch Synthetics turns that into the SuccessPercent
and Duration metrics the lab's alarms and chaos.sh read: the customer's-eye view of every
experiment, independent of anything the instances or the ALB say about themselves.

urllib only (no browser), so it runs on any syn-python-selenium runtime.
"""
import json
import os
import time
import urllib.request

from aws_synthetics.common import synthetics_logger as logger

TARGET_URL = os.environ["TARGET_URL"]
TIMEOUT_SECONDS = float(os.environ.get("TIMEOUT_SECONDS", "5"))


def check():
    started = time.monotonic()
    req = urllib.request.Request(TARGET_URL, headers={"User-Agent": "lab08-canary"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        status = resp.status
        body = resp.read().decode("utf-8", "replace")
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    logger.info(f"GET {TARGET_URL} -> {status} in {elapsed_ms} ms: {body[:200]}")
    if status != 200:
        raise Exception(f"unexpected status {status}")
    if json.loads(body).get("status") != "ok":
        raise Exception(f"unexpected body {body[:200]}")
    return {"status": status, "elapsed_ms": elapsed_ms}


def handler(event, context):
    return check()
