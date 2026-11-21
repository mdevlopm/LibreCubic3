"""Tests against the software ACE (fake_ace.py). Run: python3 -m unittest -v"""

import json
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from acelink import AceError, AceLink, FrameParser, build_frame, crc16
from fake_ace import FakeAce
from server import Config, is_loopback, make_handler, resolve_access_key


def driver_crc(buffer):
    """CRC as written in the ACEPRO Klipper driver (serial_manager._calc_crc)."""
    crc = 0xFFFF
    for byte in buffer:
        data = byte
        data ^= crc & 0xFF
        data ^= (data & 0x0F) << 4
        data &= 0xFF
        crc = (((data << 8) | (crc >> 8)) ^ (data >> 4) ^ (data << 3)) & 0xFFFF
    return crc


class FrameTests(unittest.TestCase):
    def test_known_vector(self):
        # tests/frame_tests.inc in ACEResearch
        frame = build_frame({"id": 140, "method": "get_status"})
        self.assertEqual(frame[:4], b"\xFF\xAA\x20\x00")
        self.assertEqual(frame[4:-3], b'{"id":140,"method":"get_status"}')
        self.assertEqual(frame[-3:], b"\x27\xFF\xFE")

    def test_crc_matches_driver(self):
        rng = random.Random(1)
        for _ in range(200):
            data = rng.randbytes(rng.randrange(0, 300))
            self.assertEqual(crc16(data), driver_crc(data))

    def test_parser_junk_split_and_bad_crc(self):
        good = build_frame({"id": 1, "result": {"temp": 30}})
        bad = bytearray(build_frame({"id": 2}))
        bad[-2] ^= 0x55
        stream = b"\x00\xFFjunk" + good + bytes(bad) + b"\xFF" + good
        parser = FrameParser()
        out = []
        for i in range(0, len(stream), 3):
            out += parser.feed(stream[i : i + 3])
        self.assertEqual([r["id"] for r in out], [1, 1])

    def test_binary_boot_firmware_is_parsed(self):
        # get_info after an IAP update: raw control bytes and 0x81 (not UTF-8)
        payload = (b'{"id":3,"code":0,"result":{"firmware":"V1.3.86K",'
                   b'"boot_firmware":"\x07\x81\x08\tANYCV1.3.86K"}}')
        frame = (b"\xFF\xAA" + len(payload).to_bytes(2, "little") + payload
                 + crc16(payload).to_bytes(2, "little") + b"\xFE")
        out = FrameParser().feed(frame)
        self.assertEqual(out[0]["result"]["firmware"], "V1.3.86K")

    def test_oversized_frame_refused(self):
        with self.assertRaises(AceError):
            build_frame({"method": "x" * 2000})


class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ace = FakeAce().start()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.config = Config(os.path.join(cls.tmp.name, "cfg", "config.json"))
        cls.link = AceLink(cls.ace.port)
        cls.link.start()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.link, cls.config))
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not cls.link.snapshot()["status"]:
            time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.link.stop()
        cls.ace.stop()
        cls.tmp.cleanup()

    def request(self, path, body=None, content_type="application/json"):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data)
        if data is not None:
            req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def wait_status(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = self.request("/api/state")[1]["ace"]["status"]
            if status and predicate(status):
                return status
            time.sleep(0.1)
        self.fail("durum beklenen hale gelmedi")

    def test_connects_and_reports(self):
        code, state = self.request("/api/state")
        self.assertEqual(code, 200)
        self.assertTrue(state["ace"]["connected"])
        self.assertEqual(state["ace"]["info"]["model"], "Anycubic Color Engine Pro")
        self.assertEqual(len(state["ace"]["status"]["slots"]), 4)

    def test_page_served(self):
        with urllib.request.urlopen(self.base + "/", timeout=5) as res:
            self.assertIn(b"ACE Pro Paneli", res.read())

    def test_dry_cycle(self):
        code, reply = self.request("/api/dry", {"temp": 45, "duration": 120})
        self.assertEqual((code, reply["msg"]), (200, "drying"))
        sent = [r for r in self.ace.requests if r["method"] == "drying"][-1]
        self.assertEqual(sent["params"], {"temp": 45, "duration": 120})
        status = self.wait_status(lambda s: s["dryer_status"]["status"] == "drying")
        self.assertEqual(status["dryer_status"]["target_temp"], 45)
        self.assertEqual(status["dryer_status"]["duration"], 120)
        # ACE1 hardware reports remain_time in seconds (~7200 for 120 min), not minutes
        self.assertGreater(status["dryer_status"]["remain_time"], 7000)
        self.assertLessEqual(status["dryer_status"]["remain_time"], 7200)
        self.assertEqual(self.request("/api/dry/stop", {})[0], 200)
        self.wait_status(lambda s: s["dryer_status"]["status"] == "stop")

    def test_limit_is_user_configurable(self):
        code, reply = self.request("/api/dry", {"temp": 70, "duration": 60})
        self.assertEqual(code, 400)
        self.assertEqual(self.request("/api/config", {"max_temp": 70})[0], 200)
        self.assertEqual(self.request("/api/dry", {"temp": 70, "duration": 60})[0], 200)
        self.request("/api/dry/stop", {})
        self.assertEqual(self.request("/api/config", {"max_temp": 55})[0], 200)
        with open(os.path.join(self.tmp.name, "cfg", "config.json")) as f:
            self.assertEqual(json.load(f)["max_temp"], 55)

    def test_bad_input_rejected(self):
        self.assertEqual(self.request("/api/dry", {"temp": "45", "duration": 60})[0], 400)
        self.assertEqual(self.request("/api/dry", {"temp": 45})[0], 400)
        self.assertEqual(self.request("/api/config", {"max_temp": 0})[0], 400)
        self.assertEqual(self.request("/api/dry", {"temp": 45, "duration": 60}, "text/plain")[0], 415)
        self.assertEqual(self.request("/api/nope", {})[0], 404)

    def test_slot_and_raw_calls(self):
        self.assertEqual(self.request("/api/feed", {"index": 1, "length": 100, "speed": 25})[0], 200)
        sent = [r for r in self.ace.requests if r["method"] == "feed_filament"][-1]
        self.assertEqual(sent["params"], {"index": 1, "length": 100, "speed": 25})
        self.assertEqual(self.request("/api/raw", {"method": "get_info"})[0], 200)
        code, reply = self.request("/api/raw", {"method": "no_such_method"})
        self.assertEqual(code, 502)
        self.assertIn("unknown method", reply["error"])

    def test_raw_cannot_bypass_limits(self):
        # PK-R07: /api/raw drying / set_fan_speed / temp-like fields obey the same limits as the dedicated routes
        before = len(self.ace.requests)
        self.assertEqual(self.request("/api/raw", {"method": "drying", "params": {"temp": 99, "duration": 60}})[0], 400)
        self.assertEqual(self.request("/api/raw", {"method": "drying", "params": {"temp": 45, "duration": 999999}})[0], 400)
        self.assertEqual(self.request("/api/raw", {"method": "drying", "params": {"temp": 45}})[0], 400)
        self.assertEqual(self.request("/api/raw", {"method": "drying", "params": {"temp": 45, "duration": 60, "fan_speed": 99999}})[0], 400)
        self.assertEqual(self.request("/api/raw", {"method": "drying", "params": {"temp": 45, "duration": 60, "x": 1}})[0], 400)
        self.assertEqual(self.request("/api/raw", {"method": "drying"})[0], 400)
        self.assertEqual(self.request("/api/raw", {"method": "set_fan_speed", "params": {"fan_speed": 99999}})[0], 400)
        self.assertEqual(self.request("/api/raw", {"method": "set_fan_speed", "params": {"fan_speed": "5"}})[0], 400)
        self.assertEqual(self.request("/api/raw", {"method": "something", "params": {"target_temp": 200}})[0], 400)
        self.assertEqual(self.request("/api/raw", {"method": "something", "params": {"temp": True}})[0], 400)
        self.assertEqual([r for r in self.ace.requests[before:] if r["method"] in ("drying", "set_fan_speed", "something")], [])
        # within limits it still goes through unchanged
        self.assertEqual(self.request("/api/raw", {"method": "drying", "params": {"temp": 45, "duration": 30}})[0], 200)
        sent = [r for r in self.ace.requests if r["method"] == "drying"][-1]
        self.assertEqual(sent["params"], {"temp": 45, "duration": 30})
        self.request("/api/dry/stop", {})

    def test_negative_content_length_and_bounds(self):
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        conn.putrequest("POST", "/api/dry")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", "-5")
        conn.endheaders()
        res = conn.getresponse()
        self.assertEqual(res.status, 400)
        conn.close()
        self.assertEqual(make_handler(self.link, self.config).timeout, 10)
        self.assertEqual(self.request("/api/config", {"max_temp": 101})[0], 400)
        self.assertEqual(self.request("/api/config", {"max_temp": 100})[0], 200)
        self.request("/api/config", {"max_temp": 55})

    def test_config_file_is_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c", "config.json")
            cfg = Config(path)
            cfg.update({"max_temp": 50})
            cfg.set_access_key("k" * 16)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_keepalive_cadence(self):
        before = len([r for r in self.ace.requests if r["method"] == "get_status"])
        time.sleep(3.2)
        after = len([r for r in self.ace.requests if r["method"] == "get_status"])
        self.assertGreaterEqual(after - before, 3)  # ACE drops the link after 3 s of silence


class AccessControlTests(unittest.TestCase):
    """A-05: the panel is open only on loopback; any other listen address needs an access key."""

    ROOT = os.path.dirname(os.path.abspath(__file__))

    @classmethod
    def setUpClass(cls):
        cls.ace = FakeAce().start()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.config = Config(os.path.join(cls.tmp.name, "cfg", "config.json"))
        cls.link = AceLink(cls.ace.port)
        cls.link.start()
        cls.key = "s3cret-key"
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.link, cls.config, cls.key))
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.link.stop()
        cls.ace.stop()
        cls.tmp.cleanup()

    def call(self, path, body=None, key=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if key is not None:
            req.add_header("X-Access-Key", key)
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def test_api_requires_key(self):
        for path, body in (
            ("/api/state", None),
            ("/api/dry", {"temp": 45, "duration": 60}),
            ("/api/feed", {"index": 0, "length": 10, "speed": 10}),
            ("/api/raw", {"method": "get_info"}),
            ("/api/config", {"max_temp": 55}),
        ):
            self.assertEqual(self.call(path, body)[0], 401, path)
            self.assertEqual(self.call(path, body, key="wrong")[0], 401, path)
        # nothing reached the ACE
        self.assertEqual([r for r in self.ace.requests if r["method"] in ("drying", "feed_filament")], [])

    def test_valid_key_allows_calls_and_is_never_echoed(self):
        code, state = self.call("/api/state", key=self.key)
        self.assertEqual(code, 200)
        self.assertNotIn(self.key, json.dumps(state))
        self.assertEqual(self.call("/api/raw", {"method": "get_info"}, key=self.key)[0], 200)

    def test_page_loads_without_key(self):
        with urllib.request.urlopen(self.base + "/", timeout=5) as res:
            page = res.read()
        self.assertIn(b"X-Access-Key", page)

    def test_resolve_access_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            cfg = Config(path)
            self.assertEqual(resolve_access_key(cfg, "127.0.0.1", None), "")
            self.assertTrue(is_loopback("::1") and not is_loopback("0.0.0.0"))
            key = resolve_access_key(cfg, "0.0.0.0", None)
            self.assertGreaterEqual(len(key), 16)
            with open(path) as f:
                saved = json.load(f)
            self.assertEqual(saved["access_key"], key)
            # stable across restarts, survives a settings save, never part of the public config
            cfg2 = Config(path)
            self.assertEqual(resolve_access_key(cfg2, "0.0.0.0", None), key)
            cfg2.update({"max_temp": 50})
            with open(path) as f:
                self.assertEqual(json.load(f)["access_key"], key)
            self.assertNotIn("access_key", cfg2.get())
            self.assertEqual(resolve_access_key(cfg2, "0.0.0.0", "cli-key"), "cli-key")
            self.assertEqual(Config(path).access_key, "cli-key")

    def test_defaults_are_loopback(self):
        with open(os.path.join(self.ROOT, "app.json")) as f:
            self.assertEqual(json.load(f)["properties"]["bind_host"]["default"], "127.0.0.1")
        with open(os.path.join(self.ROOT, "app.sh")) as f:
            self.assertIn("BIND_HOST=${BIND_HOST:-127.0.0.1}", f.read())

    def test_non_loopback_server_generates_key_and_enforces_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "config.json")
            proc = subprocess.Popen(
                [sys.executable, os.path.join(self.ROOT, "server.py"), "--fake", "--host", "0.0.0.0",
                 "--http-port", "0", "--config", cfg_path],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            try:
                port = None
                lines = []
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and port is None:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    lines.append(line)
                    if line.startswith("ACE paneli:"):
                        port = int(line.rsplit(":", 1)[1].split("/")[0])
                self.assertIsNotNone(port, "".join(lines))
                with open(cfg_path) as f:
                    key = json.load(f)["access_key"]
                self.assertIn(key, "".join(lines))
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=5)
                    self.fail("anahtarsız istek reddedilmeliydi")
                except urllib.error.HTTPError as err:
                    self.assertEqual(err.code, 401)
                req = urllib.request.Request(f"http://127.0.0.1:{port}/api/state", headers={"X-Access-Key": key})
                with urllib.request.urlopen(req, timeout=5) as res:
                    self.assertEqual(res.status, 200)
            finally:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=5)
                proc.stdout.close()


class ConfigPathTests(unittest.TestCase):
    def test_config_path_without_directory(self):
        # H-02: dirname("config.json") is "" and os.makedirs("") raises
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                cfg = Config("config.json")
                self.assertEqual(cfg.update({"max_temp": 60})["max_temp"], 60)
                with open(os.path.join(tmp, "config.json")) as f:
                    self.assertEqual(json.load(f)["max_temp"], 60)
            finally:
                os.chdir(cwd)


class RinkhalsAppTests(unittest.TestCase):
    ROOT = os.path.dirname(os.path.abspath(__file__))

    def test_app_manifest_and_disabled_by_default(self):
        with open(os.path.join(self.ROOT, "app.json")) as f:
            manifest = json.load(f)
        self.assertEqual(manifest["$version"], "1")
        for key in ("name", "description", "version", "properties"):
            self.assertIn(key, manifest)
        for prop in ("serial_port", "http_port", "bind_host", "max_temp"):
            self.assertIn(prop, manifest["properties"])
            self.assertIn("display", manifest["properties"][prop])
            self.assertIn("type", manifest["properties"][prop])
            self.assertIn("default", manifest["properties"][prop])
        self.assertEqual(manifest["properties"]["serial_port"]["type"], "enum")
        self.assertIn("auto", manifest["properties"]["serial_port"]["options"])
        self.assertEqual(manifest["properties"]["bind_host"]["type"], "enum")
        self.assertEqual(manifest["properties"]["http_port"]["default"], 8890)
        self.assertFalse(os.path.exists(os.path.join(self.ROOT, ".enabled")))

    def test_candidate_ports_fallback(self):
        import unittest.mock as mock
        import acelink

        def fake_glob(pattern):
            mapping = {
                "/dev/ttyACM*": [],
                "/dev/serial/by-id/*": ["/dev/serial/by-id/usb-Anycubic_123"],
                "/dev/ttyUSB*": ["/dev/ttyUSB0"],
            }
            return mapping.get(pattern, [])

        with mock.patch.object(acelink, "find_ace_ports", return_value=[]):
            with mock.patch.object(acelink.glob, "glob", side_effect=fake_glob):
                with mock.patch.object(acelink.os.path, "realpath", return_value="/dev/ttyUSB0"):
                    self.assertEqual(acelink.candidate_ports(), ["/dev/ttyUSB0"])

    def test_server_max_temp_cli_and_sigterm(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "config.json")
            proc = subprocess.Popen(
                [
                    sys.executable,
                    os.path.join(self.ROOT, "server.py"),
                    "--fake",
                    "--host",
                    "127.0.0.1",
                    "--http-port",
                    "0",
                    "--config",
                    cfg_path,
                    "--max-temp",
                    "88",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not os.path.exists(cfg_path):
                    time.sleep(0.05)
                self.assertTrue(os.path.exists(cfg_path))
                with open(cfg_path) as f:
                    self.assertEqual(json.load(f)["max_temp"], 88)
            finally:
                proc.send_signal(signal.SIGTERM)
                rc = proc.wait(timeout=5)
            self.assertEqual(rc, 0)

    def test_app_sh_lifecycle_and_conflict_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = os.path.join(tmp, "ace-standalone")
            os.makedirs(app_dir)
            for name in ("app.sh", "app.json", "server.py", "acelink.py", "fake_ace.py"):
                shutil.copy2(os.path.join(self.ROOT, name), os.path.join(app_dir, name))
            shutil.copytree(os.path.join(self.ROOT, "web"), os.path.join(app_dir, "web"))
            os.chmod(os.path.join(app_dir, "app.sh"), 0o755)

            logs_dir = os.path.join(tmp, "logs")
            user_apps = os.path.join(tmp, "user_apps")
            tmp_apps = os.path.join(tmp, "tmp_apps")
            os.makedirs(logs_dir)
            os.makedirs(user_apps)
            os.makedirs(tmp_apps)

            # Pick an unused port for the test HTTP server
            import socket

            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                http_port = s.getsockname()[1]

            dummy_dev = os.path.join(tmp, "dummy_ttyACM0")
            with open(dummy_dev, "w") as f:
                f.write("")

            with open(os.path.join(user_apps, "ace-standalone.config"), "w") as f:
                json.dump(
                    {
                        "serial_port": dummy_dev,
                        "http_port": http_port,
                        "bind_host": "127.0.0.1",
                        "max_temp": 78,
                    },
                    f,
                )

            tools_sh = os.path.join(tmp, "tools.sh")
            with open(tools_sh, "w") as f:
                f.write(
                    f"""export RINKHALS_LOGS="{logs_dir}"
export USER_APP_PATH="{user_apps}"
export TEMPORARY_APP_PATH="{tmp_apps}"
export APP_STATUS_STARTED=started
export APP_STATUS_STOPPED=stopped
log() {{ echo "${{*}}"; }}
report_status() {{
    echo "Status: $1"
    echo "PIDs: $2"
    echo "Log: $3"
}}
get_by_name() {{
    ps -eo pid=,args= | grep -F -- "$1" | grep -v grep | awk '{{print $1}}'
}}
get_app_property() {{
    {sys.executable} -c '
import json, os, sys
app, prop = sys.argv[1], sys.argv[2]
for p in (os.environ["TEMPORARY_APP_PATH"] + f"/{{app}}.config",
          os.environ["USER_APP_PATH"] + f"/{{app}}.config"):
    if os.path.exists(p):
        v = json.load(open(p)).get(prop)
        if v is not None:
            print(v); sys.exit(0)
m = json.load(open("{app_dir}/app.json"))
v = m.get("properties", {{}}).get(prop, {{}}).get("default")
if v is not None:
    print(v)
' "$1" "$2"
}}
"""
                )

            env = os.environ.copy()
            env["RINKHALS_TOOLS"] = tools_sh
            env["RINKHALS_LOGS"] = logs_dir
            env["ACE_STANDALONE_PID_FILE"] = os.path.join(tmp, "ace.pid")
            # B-09: steps 1-4 must not depend on unrelated "klippy" processes on the host. Force the conflict
            # check to read a config WITHOUT any [ace] section (only step 5 swaps in the real conflicting cfg).
            benign_cfg = os.path.join(tmp, "benign.klipper.cfg")
            with open(benign_cfg, "w") as f:
                f.write("[printer]\nkinematics: cartesian\n")
            env["ACE_KLIPPER_CONFIG"] = benign_cfg

            def run_app(*args):
                return subprocess.run(
                    [os.path.join(app_dir, "app.sh"), *args],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )

            # 1. Version and initial status
            ver = run_app("version")
            self.assertEqual(ver.returncode, 0)
            self.assertEqual(ver.stdout.strip(), json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.json")))["version"])

            res = run_app("status")
            self.assertEqual(res.returncode, 0)
            self.assertIn("Status: stopped", res.stdout)

            # 2. Start, verify status & /api/state & max_temp, then stop
            try:
                res = run_app("start")
                self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
                res = run_app("status")
                self.assertIn("Status: started", res.stdout)
                with urllib.request.urlopen(f"http://127.0.0.1:{http_port}/api/state", timeout=5) as resp:
                    state = json.loads(resp.read())
                self.assertEqual(state["config"]["max_temp"], 78)
            finally:
                stop_res = run_app("stop")
                self.assertEqual(stop_res.returncode, 0)

            res = run_app("status")
            self.assertIn("Status: stopped", res.stdout)

            # 3. Resource conflict: another process holds dummy_dev
            held_fd = open(dummy_dev, "rb")
            holder = subprocess.Popen(["sleep", "30"], stdin=held_fd)
            held_fd.close()
            try:
                res = run_app("start")
                self.assertNotEqual(res.returncode, 0)
                self.assertIn(str(holder.pid), res.stdout + res.stderr)
                self.assertIn("Status: stopped", run_app("status").stdout)
            finally:
                holder.terminate()
                holder.wait(timeout=5)

            # 4. HTTP port conflict: another socket already binds http_port
            with socket.socket() as busy_sock:
                busy_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                busy_sock.bind(("127.0.0.1", http_port))
                busy_sock.listen(1)
                res = run_app("start")
                self.assertNotEqual(res.returncode, 0)
                self.assertIn(str(http_port), res.stdout + res.stderr)
                self.assertIn("Status: stopped", run_app("status").stdout)

            # 5. vanilla-klipper (klippy) with [ace] config blocks startup.
            # Isolation (B-09): app.sh scans $ACE_PROC_ROOT/*/cmdline for any "klippy" process, so scanning the
            # real /proc would also match stray processes left by earlier or concurrent runs (and report their
            # PID instead of ours). Point it at a private proc root that holds ONLY the fake klippy started
            # here, whose command line carries a path unique to this temp directory.
            klipper_cfg = os.path.join(tmp, "printer.klipper.cfg")
            with open(klipper_cfg, "w") as f:
                f.write("[printer]\nkinematics: cartesian\n[include ace_K3.cfg]\n")
            fake_klippy = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)", "klippy", klipper_cfg]
            )
            try:
                fake_proc = os.path.join(tmp, "fake_proc")
                fake_entry = os.path.join(fake_proc, str(fake_klippy.pid))
                os.makedirs(fake_entry)
                with open(os.path.join(fake_entry, "cmdline"), "wb") as f:
                    f.write(b"\0".join(os.fsencode(a) for a in (sys.executable, "-c", "klippy", klipper_cfg)) + b"\0")

                klippy_env = env.copy()
                klippy_env["ACE_KLIPPER_CONFIG"] = klipper_cfg
                klippy_env["ACE_PROC_ROOT"] = fake_proc
                res = subprocess.run(
                    [os.path.join(app_dir, "app.sh"), "start"],
                    env=klippy_env,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
                self.assertIn(str(fake_klippy.pid), res.stdout + res.stderr)
                self.assertIn("Status: stopped", run_app("status").stdout)
            finally:
                # Never leave processes behind, even if an assertion above failed.
                run_app("stop")
                fake_klippy.terminate()
                try:
                    fake_klippy.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    fake_klippy.kill()
                    fake_klippy.wait(timeout=5)

            # 6. H-01: vanilla-klipper ENABLED (it only launches klippy ~15 s after us at boot, so no klippy process
            # exists yet) and its config drives the ACE -> refuse to start. Private empty proc root: no klippy visible.
            empty_proc = os.path.join(tmp, "empty_proc")
            os.makedirs(empty_proc)
            vk_dir = os.path.join(user_apps, "vanilla-klipper")
            os.makedirs(vk_dir)
            open(os.path.join(vk_dir, ".enabled"), "w").close()
            boot_env = env.copy()
            boot_env["ACE_PROC_ROOT"] = empty_proc
            boot_env["ACE_KLIPPER_CONFIG"] = klipper_cfg  # includes ace_K3.cfg
            try:
                res = subprocess.run(
                    [os.path.join(app_dir, "app.sh"), "start"], env=boot_env, capture_output=True, text=True, timeout=15
                )
                self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
                self.assertIn("vanilla-klipper etkin", res.stdout + res.stderr)
                self.assertIn("Status: stopped", run_app("status").stdout)

                # enabled but its config does not use the ACE: no conflict (the check passes, so it gets past the
                # klippy check and fails later only on what the empty proc root cannot show; assert on the message)
                boot_env["ACE_KLIPPER_CONFIG"] = benign_cfg
                res = subprocess.run(
                    [os.path.join(app_dir, "app.sh"), "start"], env=boot_env, capture_output=True, text=True, timeout=15
                )
                self.assertNotIn("vanilla-klipper etkin", res.stdout + res.stderr)

                # disabled marker wins over .enabled
                open(os.path.join(vk_dir, ".disabled"), "w").close()
                boot_env["ACE_KLIPPER_CONFIG"] = klipper_cfg
                res = subprocess.run(
                    [os.path.join(app_dir, "app.sh"), "start"], env=boot_env, capture_output=True, text=True, timeout=15
                )
                self.assertNotIn("vanilla-klipper etkin", res.stdout + res.stderr)
            finally:
                run_app("stop")

if __name__ == "__main__":
    unittest.main()
