#!/usr/bin/env python3
"""Lab 06 service — a deliberately tiny, zero-dependency HTTP app.

Endpoints:
  GET /healthz  -> 200 {"status": "ok"}           (ALB + container health check)
  GET /version  -> 200 {"version", "git_sha", ...} (proves WHICH build is live)
  GET /         -> 200 hello + endpoint list
  GET /metrics  -> 200 Prometheus text format      (lab 07 scrapes this: request
                   count by path/status + latency histogram; no client library)
  GET /boom     -> 500 on purpose                  (fault injection for the lab 07
                   "high 5xx" alert: a detector that has never fired is untested)

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
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_NAME = "lab06-app"
VERSION = os.environ.get("APP_VERSION", "dev")
GIT_SHA = os.environ.get("GIT_SHA", "unknown")
STARTED_AT = time.time()

# ---------------------------------------------------------------------------
# Metrics (Prometheus text exposition format, hand-rolled: ~40 lines beats a
# dependency for two series). Label cardinality is bounded on purpose: only the
# known routes get their own `path` label, anything else is "other", so a
# scanner hitting /wp-admin/... cannot grow the series set.
# ---------------------------------------------------------------------------
KNOWN_PATHS = ("/", "/healthz", "/version", "/metrics", "/boom")
LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)
_metrics_lock = threading.Lock()
_requests_total: dict[tuple[str, str, str], int] = {}  # (method, path, status) -> count
_latency_bucket_counts = [0] * (len(LATENCY_BUCKETS) + 1)  # last slot = +Inf
_latency_sum = 0.0
_latency_count = 0


def record_request(method: str, path: str, status: int, seconds: float) -> None:
    global _latency_sum, _latency_count
    label_path = path if path in KNOWN_PATHS else "other"
    with _metrics_lock:
        key = (method, label_path, str(status))
        _requests_total[key] = _requests_total.get(key, 0) + 1
        for i, le in enumerate(LATENCY_BUCKETS):
            if seconds <= le:
                _latency_bucket_counts[i] += 1
                break
        else:
            _latency_bucket_counts[-1] += 1
        _latency_sum += seconds
        _latency_count += 1


def render_metrics() -> str:
    """Prometheus text format. Histogram buckets are cumulative, as the format requires."""
    lines = [
        "# HELP http_requests_total Total HTTP requests handled, by method, path and status.",
        "# TYPE http_requests_total counter",
    ]
    with _metrics_lock:
        for (method, path, status), n in sorted(_requests_total.items()):
            lines.append(f'http_requests_total{{method="{method}",path="{path}",status="{status}"}} {n}')
        lines += [
            "# HELP http_request_duration_seconds Request handling time.",
            "# TYPE http_request_duration_seconds histogram",
        ]
        cumulative = 0
        for i, le in enumerate(LATENCY_BUCKETS):
            cumulative += _latency_bucket_counts[i]
            lines.append(f'http_request_duration_seconds_bucket{{le="{le}"}} {cumulative}')
        cumulative += _latency_bucket_counts[-1]
        lines.append(f'http_request_duration_seconds_bucket{{le="+Inf"}} {cumulative}')
        lines.append(f"http_request_duration_seconds_sum {_latency_sum:.6f}")
        lines.append(f"http_request_duration_seconds_count {_latency_count}")
    lines += [
        "# HELP app_info Build information (value is always 1).",
        "# TYPE app_info gauge",
        f'app_info{{app="{APP_NAME}",version="{VERSION}",git_sha="{GIT_SHA}"}} 1',
    ]
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    server_version = f"lab06/{VERSION}"
    sys_version = ""  # don't advertise the Python version in Server:

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._send(code, "application/json", body)

    def _send(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self._status = code

    def _route(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._send_json(200, {"status": "ok"})
        elif path == "/metrics":
            self._send(200, "text/plain; version=0.0.4; charset=utf-8", render_metrics().encode("utf-8"))
        elif path == "/boom":
            self._send_json(500, {"error": "intentional", "hint": "fault injection for the 5xx alert"})
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
                    "endpoints": ["/healthz", "/version", "/metrics", "/boom"],
                },
            )
        else:
            self._send_json(404, {"error": "not found", "path": path})

    def _timed(self) -> None:
        self._status = 0
        t0 = time.monotonic()
        try:
            self._route()
        finally:
            record_request(self.command, self.path.split("?", 1)[0], self._status, time.monotonic() - t0)

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        self._timed()

    def do_HEAD(self) -> None:  # noqa: N802
        self._timed()

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
