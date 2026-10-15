#!/usr/bin/env python3
"""Lab 08 web app: the smallest thing worth keeping alive.

  GET /         -> {"service": "lab08", "instance": "<id>", "az": "<az>"}
  GET /healthz  -> {"status": "ok", "instance": "<id>"}   (ALB target-group health check + canary)
  anything else -> 404

Standard library only (AL2023 ships python3), threaded so a slow client cannot block the
health check. systemd runs it with Restart=always: a crashed process is the first layer of
self-healing, before the ALB, the ASG or any runbook gets involved.
"""
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("APP_PORT", "8080"))


def imds(path):
    """Read instance metadata with IMDSv2 (the launch template makes tokens mandatory)."""
    try:
        tok_req = urllib.request.Request(
            "http://169.254.169.254/latest/api/token",
            method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
        )
        token = urllib.request.urlopen(tok_req, timeout=2).read().decode()
        req = urllib.request.Request(
            "http://169.254.169.254/latest/meta-data/" + path,
            headers={"X-aws-ec2-metadata-token": token},
        )
        return urllib.request.urlopen(req, timeout=2).read().decode()
    except Exception:  # metadata is decoration, never a reason to fail the health check
        return "unknown"


INSTANCE_ID = imds("instance-id")
AZ = imds("placement/availability-zone")


class Handler(BaseHTTPRequestHandler):
    server_version = "lab08/1"

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802 (http.server naming)
        if self.path == "/healthz":
            self._send(200, {"status": "ok", "instance": INSTANCE_ID})
        elif self.path == "/":
            self._send(200, {"service": "lab08", "instance": INSTANCE_ID, "az": AZ})
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, *args):  # the ALB health check every 5 s would flood the journal
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
