#!/usr/bin/env python3
"""Replay the dead-letter queue into the orders queue - run AFTER the cause is fixed.

Uses SQS's native redrive (StartMessageMoveTask, the API behind the console's
"Start DLQ redrive" button). The DLQ's redrive-allow policy permits exactly one
source queue (byQueue), so this cannot push messages anywhere else.

  python tools/redrive.py --outputs outputs.json            # show what is waiting
  python tools/redrive.py --outputs outputs.json --apply    # move it back and wait

Redriving before the fix just sends the poison message round the loop again
(maxReceiveCount more attempts, then back in the DLQ): harmless, but pointless.
"""

from __future__ import annotations

import argparse
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, __import__("os").path.dirname(__file__))
from common import load_outputs, session  # noqa: E402


def dlq_depth(sqs, dlq_url: str) -> dict:
    attrs = sqs.get_queue_attributes(
        QueueUrl=dlq_url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    return {k: int(v) for k, v in attrs.items()}


def redrive(sqs, dlq_arn: str, dest_arn: str, timeout: float = 300.0, log=print) -> dict:
    """Start a move task DLQ -> source and wait for it to finish. Returns the final task record."""
    handle = sqs.start_message_move_task(SourceArn=dlq_arn, DestinationArn=dest_arn)["TaskHandle"]
    log(f"move task started: {handle[:24]}...")
    deadline = time.monotonic() + timeout
    task: dict = {}
    while time.monotonic() < deadline:
        tasks = sqs.list_message_move_tasks(SourceArn=dlq_arn, MaxResults=10).get("Results", [])
        task = next((t for t in tasks if t.get("TaskHandle") == handle), tasks[0] if tasks else {})
        status = task.get("Status")
        log(
            f"move task {status}: moved {task.get('ApproximateNumberOfMessagesMoved', 0)}"
            f" of {task.get('ApproximateNumberOfMessagesToMove', '?')}"
        )
        if status in ("COMPLETED", "FAILED", "CANCELLED"):
            return task
        time.sleep(5)
    task["Status"] = task.get("Status", "UNKNOWN") + " (timed out waiting)"
    return task


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outputs", default="outputs.json", help="terraform output -json file")
    ap.add_argument("--apply", action="store_true", help="actually start the redrive (default: report only)")
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args()

    out = load_outputs(args.outputs)
    sqs = session(out).client("sqs")
    depth = dlq_depth(sqs, out["dlq_url"])
    print(f"DLQ {out['dlq_url'].rsplit('/', 1)[-1]}: {depth}")
    if not args.apply:
        print("report only; re-run with --apply once the cause is fixed")
        return 0
    if depth["ApproximateNumberOfMessages"] == 0:
        print("nothing to redrive")
        return 0
    task = redrive(sqs, out["dlq_arn"], out["queue_arn"], timeout=args.timeout)
    ok = task.get("Status") == "COMPLETED"
    print("redrive COMPLETED" if ok else f"redrive did not complete: {task}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
