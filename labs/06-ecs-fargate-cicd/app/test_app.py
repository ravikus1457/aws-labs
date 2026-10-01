"""Unit tests for the Lab 06 service (stdlib unittest, no network beyond loopback).

Run:  python3 -m unittest -v test_app      (from labs/06-ecs-fargate-cicd/app)
The Dockerfile's test stage runs exactly this, so a failing test fails the build.
"""
from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

import app


class ServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = app.build_server("127.0.0.1", 0)  # port 0 = OS picks a free one
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path: str):
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                return resp.status, resp.headers, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, e.headers, json.loads(e.read().decode())

    def test_healthz_ok(self):
        status, headers, body = self._get("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"status": "ok"})
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_version_shape(self):
        status, _, body = self._get("/version")
        self.assertEqual(status, 200)
        for key in ("app", "version", "git_sha", "hostname", "uptime_s"):
            self.assertIn(key, body)
        self.assertEqual(body["app"], app.APP_NAME)

    def test_root_lists_endpoints(self):
        status, _, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("/healthz", body["endpoints"])

    def test_unknown_path_is_404(self):
        status, _, body = self._get("/does-not-exist")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "not found")

    def test_query_string_is_ignored_for_routing(self):
        status, _, _ = self._get("/healthz?probe=1")
        self.assertEqual(status, 200)

    def test_head_has_no_body(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/healthz", method="HEAD")
        with urllib.request.urlopen(req, timeout=3) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), b"")

    def test_server_header_hides_python_version(self):
        _, headers, _ = self._get("/healthz")
        self.assertNotIn("Python", headers.get("Server", ""))


if __name__ == "__main__":
    unittest.main()
