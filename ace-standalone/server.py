#!/usr/bin/env python3
"""Web panel for running an Anycubic ACE Pro without a printer.

    python3 server.py                    # http://127.0.0.1:8090, port auto-detected
    python3 server.py --host 0.0.0.0     # reachable from the LAN (an access key is required, see below)
    python3 server.py --port /dev/ttyACM0
    python3 server.py --fake             # try the panel with a software ACE

Access control: on loopback (the default) the panel is open. Any other listen address needs an access key:
`--access-key KEY`, or the `access_key` field of the config file; when none is set one is generated, saved to the
config file and printed once in the log. Every /api/* request must then send it as the `X-Access-Key` header
(the web page asks for it once and remembers it in the browser).
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from acelink import AceError, AceLink

HERE = os.path.dirname(os.path.abspath(__file__))

# Limits are the user's own; edit them from the panel or in the config file.
DEFAULT_CONFIG = {
    "max_temp": 55,  # °C, highest dryer target the panel will send
    "max_duration": 1440,  # minutes
    "default_temp": 45,
    "default_duration": 240,
}


# Absolute ceilings no setting can exceed (the user's own limits live below them).
HARD_LIMITS = {"max_temp": 100, "default_temp": 100}

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def is_loopback(host: str) -> bool:
    return host in LOOPBACK_HOSTS


class Config:
    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        self._data = dict(DEFAULT_CONFIG)
        self._access_key = ""
        try:
            with open(path) as f:
                raw = json.load(f)
            self._data.update(self._validate(raw))
            key = raw.get("access_key") if isinstance(raw, dict) else None
            if isinstance(key, str):
                self._access_key = key
        except FileNotFoundError:
            pass

    @staticmethod
    def _validate(raw: dict) -> dict:
        clean = {}
        for key in DEFAULT_CONFIG:
            if key not in raw:
                continue
            value = raw[key]
            high = HARD_LIMITS.get(key, 100000)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= high:
                raise ValueError(f"{key}: 1 ile {high} arasında tam sayı olmalı")
            clean[key] = value
        return clean

    def get(self) -> dict:
        with self._lock:
            return dict(self._data)

    @property
    def access_key(self) -> str:
        with self._lock:
            return self._access_key

    def set_access_key(self, key: str) -> None:
        with self._lock:
            self._access_key = key
            self._write()

    def _write(self) -> None:
        # dirname("config.json") is "": fall back to the current directory (H-02)
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        tmp = self._path + ".tmp"
        data = dict(self._data)
        if self._access_key:
            data["access_key"] = self._access_key
        # the file may hold the access key: owner-only from the first byte on
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._path)

    def update(self, raw: dict) -> dict:
        clean = self._validate(raw)
        with self._lock:
            self._data.update(clean)
            self._write()
            return dict(self._data)


def resolve_access_key(config: Config, host: str, cli_key: str | None) -> str:
    """Key the server must enforce: "" means open (loopback only)."""
    key = cli_key or config.access_key
    if not key and not is_loopback(host):
        key = secrets.token_hex(8)
        config.set_access_key(key)
        print(f"erişim anahtarı üretildi ve {config._path} içine kaydedildi: {key}", flush=True)
    elif cli_key and cli_key != config.access_key and not is_loopback(host):
        config.set_access_key(cli_key)
    return key


def _int(body: dict, key: str, low: int, high: int) -> int:
    value = body.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key}: tam sayı gerekli")
    if not low <= value <= high:
        raise ValueError(f"{key}: {low} ile {high} arasında olmalı (sınırı Ayarlar'dan değiştirebilirsin)")
    return value


def make_handler(link: AceLink, config: Config, access_key: str = ""):
    def dry(body):
        limits = config.get()
        params = {
            "temp": _int(body, "temp", 1, limits["max_temp"]),
            "duration": _int(body, "duration", 1, limits["max_duration"]),
        }
        if body.get("fan_speed") is not None:
            params["fan_speed"] = _int(body, "fan_speed", 0, 20000)
        return link.call("drying", params)

    def slot_call(method, extra=()):
        def run(body):
            params = {"index": _int(body, "index", 0, 15)}
            for key in extra:
                params[key] = _int(body, key, 1, 100000)
            return link.call(method, params)

        return run

    def raw(body):
        method = body.get("method")
        params = body.get("params")
        if not isinstance(method, str) or not method:
            raise ValueError("method: metin gerekli")
        if params is not None and not isinstance(params, dict):
            raise ValueError("params: nesne olmalı")
        # /api/raw must not be a way around the limits the dedicated routes enforce (PK-R07)
        limits = config.get()
        if method == "drying":
            params = params or {}
            checked = {
                "temp": _int(params, "temp", 1, limits["max_temp"]),
                "duration": _int(params, "duration", 1, limits["max_duration"]),
            }
            if params.get("fan_speed") is not None:
                checked["fan_speed"] = _int(params, "fan_speed", 0, 20000)
            extra = set(params) - set(checked) - {"fan_speed"}
            if extra:
                raise ValueError(f"drying: bilinmeyen parametre: {sorted(extra)}")
            params = checked
        elif method == "set_fan_speed":
            params = params or {}
            params = dict(params)
            params["fan_speed"] = _int(params, "fan_speed", 0, 20000)
        else:
            for key, high in (("temp", limits["max_temp"]), ("target_temp", limits["max_temp"]),
                              ("duration", limits["max_duration"]), ("fan_speed", 20000)):
                if params and key in params:
                    _int(params, key, 0 if key == "fan_speed" else 1, high)
        return link.call(method, params)

    routes = {
        "/api/dry": dry,
        "/api/dry/stop": lambda body: link.call("drying_stop"),
        "/api/feed_assist/start": slot_call("start_feed_assist"),
        "/api/feed_assist/stop": slot_call("stop_feed_assist"),
        "/api/feed": slot_call("feed_filament", ("length", "speed")),
        "/api/feed/stop": slot_call("stop_feed_filament"),
        "/api/unwind": slot_call("unwind_filament", ("length", "speed")),
        "/api/unwind/stop": slot_call("stop_unwind_filament"),
        "/api/filament_info": slot_call("get_filament_info"),
        "/api/raw": raw,
        "/api/config": lambda body: {"config": config.update(body)},
    }

    class Handler(BaseHTTPRequestHandler):
        server_version = "ace-standalone"
        timeout = 10  # a stalled client must not hold a handler thread forever

        def _send(self, code: int, payload, content_type="application/json"):
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            if not access_key:
                return True
            sent = self.headers.get("X-Access-Key", "")
            return hmac.compare_digest(sent.encode("utf-8"), access_key.encode("utf-8"))

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                # the page itself carries no data; it asks for the key and sends it with every API call
                with open(os.path.join(HERE, "web", "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif self.path.startswith("/api/") and not self._authorized():
                self._send(401, {"error": "erişim anahtarı gerekli (X-Access-Key)"})
            elif self.path == "/api/state":
                self._send(200, {"ace": link.snapshot(), "config": config.get()})
            else:
                self._send(404, {"error": "bulunamadı"})

        def do_POST(self):
            route = routes.get(self.path)
            if route is None:
                self._send(404, {"error": "bulunamadı"})
                return
            if not self._authorized():
                self._send(401, {"error": "erişim anahtarı gerekli (X-Access-Key)"})
                return
            # JSON-only bodies keep other sites' plain HTML forms from driving the ACE
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                self._send(415, {"error": "Content-Type application/json olmalı"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0:
                    raise ValueError("Content-Length negatif olamaz")
                body = json.loads(self.rfile.read(length) or b"{}") if length <= 65536 else None
                if not isinstance(body, dict):
                    raise ValueError("gövde bir JSON nesnesi olmalı")
                self._send(200, route(body))
            except (ValueError, UnicodeDecodeError) as exc:
                self._send(400, {"error": str(exc)})
            except AceError as exc:
                self._send(502, {"error": str(exc)})

        def log_message(self, fmt, *args):
            if self.command != "GET":
                super().log_message(fmt, *args)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", help="serial device (default: auto-detect a ttyACM named ACE)")
    parser.add_argument("--host", default="127.0.0.1", help="listen address (default: 127.0.0.1)")
    parser.add_argument("--http-port", type=int, default=8090)
    parser.add_argument("--access-key", help="API access key (required for non-loopback --host; generated if absent)")
    parser.add_argument(
        "--config",
        default=os.path.join(os.path.expanduser("~"), ".config", "ace-standalone", "config.json"),
    )
    parser.add_argument("--max-temp", type=int, help="override max_temp (°C) in config")
    parser.add_argument("--fake", action="store_true", help="use the built-in software ACE (no hardware)")
    args = parser.parse_args()

    if args.port == "auto":
        args.port = None

    if args.fake:
        from fake_ace import FakeAce

        args.port = FakeAce().start().port
        print(f"sahte ACE: {args.port}", flush=True)

    config = Config(args.config)
    if args.max_temp is not None:
        config.update({"max_temp": args.max_temp})

    def _handle_sigterm(_signum, _frame):
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    access_key = resolve_access_key(config, args.host, args.access_key)
    link = AceLink(args.port)
    link.start()
    httpd = ThreadingHTTPServer((args.host, args.http_port), make_handler(link, config, access_key))
    print(f"ACE paneli: http://{args.host}:{httpd.server_address[1]}/", flush=True)
    try:
        httpd.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        httpd.server_close()
        link.stop()


if __name__ == "__main__":
    main()
