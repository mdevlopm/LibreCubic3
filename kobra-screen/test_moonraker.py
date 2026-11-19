"""Tests for Moonraker HTTP client (ERR-23: keep-alive and connection reuse)."""

import http.client
import http.server
import json
import threading
import time
import unittest
from unittest import mock

from fake_moonraker import FakePrinter, serve
from kobra_screen.moonraker import Moonraker, MoonrakerClient, MoonrakerError


class TestMoonraker(unittest.TestCase):
    def setUp(self):
        self.printer = FakePrinter()
        self.httpd = serve(self.printer, simulate=False)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.client = MoonrakerClient("127.0.0.1", self.httpd.server_address[1])
        self.addCleanup(self.client.close)

    def test_single_connection_reuse(self):
        """ERR-23: Multiple consecutive requests must reuse the same HTTPConnection."""
        with mock.patch("http.client.HTTPConnection", wraps=http.client.HTTPConnection) as mock_conn:
            res1 = self.client.info()
            self.assertIsNotNone(res1)
            res2 = self.client.info()
            self.assertIsNotNone(res2)
            self.assertEqual(mock_conn.call_count, 1)

    def test_connection_retry_on_disconnect(self):
        """Single reconnect retry on connection drop."""
        self.client.info()
        self.assertIsNotNone(self.client._conn)
        self.client._conn.close()
        res = self.client.info()
        self.assertIsNotNone(res)

    def test_request_method_alias(self):
        """Verify request() method works directly."""
        res = self.client.request("GET", "/printer/info")
        self.assertIsInstance(res, dict)

    def test_emergency_stop_not_blocked_by_10s_gcode_or_lock(self):
        """REV-09: emergency_stop must return in <100 ms even while a 10s G-code or _lock is held."""
        gcode_entered = threading.Event()
        gcode_release = threading.Event()
        estop_calls = []

        class SlowGcodeHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"result": {"state": "ready"}}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length:
                    self.rfile.read(length)
                if self.path == "/printer/gcode/script":
                    gcode_entered.set()
                    gcode_release.wait(10.0)
                    body = json.dumps({"result": "ok"}).encode("utf-8")
                elif self.path == "/printer/emergency_stop":
                    estop_calls.append(time.monotonic())
                    body = json.dumps({"result": "ok"}).encode("utf-8")
                else:
                    body = json.dumps({"result": {}}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SlowGcodeHandler)
        srv_thread = threading.Thread(target=srv.serve_forever, daemon=True)
        srv_thread.start()
        client = MoonrakerClient("127.0.0.1", srv.server_address[1], timeout=5.0)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.addCleanup(client.close)
        self.addCleanup(gcode_release.set)

        gcode_result = {}

        def run_long_gcode():
            gcode_result["res"] = client.gcode("G4 P10000", timeout=15.0)

        t = threading.Thread(target=run_long_gcode, daemon=True)
        t.start()
        self.assertTrue(gcode_entered.wait(2.0))

        # 1. emergency_stop must return < 100 ms while 10s G-code is in flight
        t0 = time.monotonic()
        res = client.emergency_stop()
        elapsed_estop = time.monotonic() - t0
        self.assertEqual(res, "ok")
        self.assertLess(elapsed_estop, 0.1)
        self.assertEqual(len(estop_calls), 1)

        # 2. Normal status polling (info) must also not be blocked by long G-code
        t1 = time.monotonic()
        info = client.info()
        elapsed_info = time.monotonic() - t1
        self.assertEqual(info["state"], "ready")
        self.assertLess(elapsed_info, 0.1)

        # 3. Even if self._lock is held by another thread, emergency_stop still finishes < 100 ms
        with client._lock:
            t2 = time.monotonic()
            res2 = client.emergency_stop()
            elapsed_locked = time.monotonic() - t2
        self.assertEqual(res2, "ok")
        self.assertLess(elapsed_locked, 0.1)

        gcode_release.set()
        t.join(timeout=2.0)
        self.assertEqual(gcode_result.get("res"), "ok")


if __name__ == "__main__":
    unittest.main()
