#!/usr/bin/env python3
"""Lab 06 service — a deliberately tiny, zero-dependency HTTP app.

Endpoints:
  GET /healthz  -> 200 {"status": "ok"}           (ALB + container health check)
  GET /version  -> 200 {"version", "git_sha", ...} (proves WHICH build is live)
  GET /         -> 200 hello + endpoint list

Why stdlib and not FastAPI: no dependency tree to pin, scan, or rebuild for
CVEs; the image stays ~50 MB; the interesting parts of this lab are the
pipeline and the AWS plumbing, not the framework. Swapping in FastAPI is a
Dockerfile change, not an architecture change.

Logs are one JSON object per line on stdout so CloudWatch Logs Insights can
parse them without a custom pattern.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_NAME = "lab06-app"
VERSION = os.environ.get("APP_VERSION", "dev")
GIT_SHA = os.environ.get("GIT_SHA", "unknown")
STARTED_AT = time.time()


class Handler(BaseHTTPRequestHandler):
    server_version = f"lab06/{VERSION}"
    sys_version = ""  # don't advertise the Python version in Server:

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _route(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._send_json(200, {"status": "ok"})
        elif path == "/version":
            self._send_json(
                200,
                {
                    "app": APP_NAME,
                    "version": VERSION,
                    "git_sha": GIT_SHA,
                    "hostname": socket.gethostname(),
                    "uptime_s": round(time.time() - STARTED_AT, 1),
                },
            )
        elif path == "/":
            self._send_json(
                200,
                {
                    "app": APP_NAME,
                    "message": "hello from ECS Fargate",
                    "endpoints": ["/healthz", "/version"],
                },
            )
        else:
            self._send_json(404, {"error": "not found", "path": path})

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        self._route()

    def do_HEAD(self) -> None:  # noqa: N802
        self._route()

    def log_request(self, code="-", size="-") -> None:
        line = {
            "ts": round(time.time(), 3),
            "method": self.command,
            "path": self.path,
            "status": int(code) if str(code).isdigit() else code,
            "client": self.client_address[0],
        }
        sys.stdout.write(json.dumps(line) + "\n")
        sys.stdout.flush()

    def log_error(self, fmt, *args) -> None:  # keep stderr quiet; status is in log_request
        return


def build_server(host: str = "0.0.0.0", port: int = 8080) -> ThreadingHTTPServer:
    """Factory so tests can bind to port 0 and read the real port back."""
    return ThreadingHTTPServer((host, port), Handler)


def main() -> int:
    port = int(os.environ.get("PORT", "8080"))
    server = build_server("0.0.0.0", port)

    # ECS sends SIGTERM on stop, then SIGKILL after stopTimeout (30 s default).
    # Exit promptly so deployments and scale-in are clean.
    def _stop(signum, _frame):
        sys.stdout.write(json.dumps({"event": "shutdown", "signal": signum}) + "\n")
        sys.stdout.flush()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    sys.stdout.write(
        json.dumps({"event": "startup", "app": APP_NAME, "version": VERSION, "git_sha": GIT_SHA, "port": port})
        + "\n"
    )
    sys.stdout.flush()
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
