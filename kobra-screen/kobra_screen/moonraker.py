"""Minimal Moonraker HTTP client (standard library only)."""

import http.client
import json
import threading
import urllib.parse
import uuid


class MoonrakerError(Exception):
    pass


class Moonraker:
    _URGENT_PATHS = frozenset({
        "/printer/emergency_stop",
        "/machine/reboot",
        "/printer/firmware_restart",
        "/printer/print/pause",
        "/printer/print/cancel",
    })
    # Never retried on a timeout (a retry would run the command twice) and kept off the shared connection
    _LONG_PATHS = frozenset({
        "/printer/gcode/script",
        "/printer/print/resume",
    })
    # Moonraker answers these only when the macro has finished: PAUSE parks (and may cut),
    # RESUME reheats, purges and returns, CANCEL_PRINT cuts and unloads the ACE
    PAUSE_TIMEOUT = 60.0
    RESUME_TIMEOUT = 300.0
    CANCEL_TIMEOUT = 90.0
    START_TIMEOUT = 30.0

    def __init__(self, host="127.0.0.1", port=7125, timeout=5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._conn = None
        self._lock = threading.Lock()

    def _get_connection(self, timeout=None):
        tout = timeout or self.timeout
        if self._conn is None:
            self._conn = http.client.HTTPConnection(self.host, self.port, timeout=tout)
        # Always update timeout on existing connection
        self._conn.timeout = tout
        return self._conn

    def _close_connection(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def close(self):
        with self._lock:
            self._close_connection()

    def _isolated_raw_request(self, method, path, body=None, headers=None, timeout=None):
        """Send a single request on an independent connection without acquiring self._lock."""
        tout = self.timeout if timeout is None else timeout
        conn = http.client.HTTPConnection(self.host, self.port, timeout=tout)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            raw = response.read()
            return response.status, raw
        except (http.client.CannotSendRequest, http.client.BadStatusLine,
                http.client.RemoteDisconnected, ConnectionError, OSError,
                TimeoutError, http.client.HTTPException) as exc:
            raise MoonrakerError("Moonraker'a ulaşılamıyor: %s" % (exc,)) from exc
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _raw_request(self, method, path, body=None, headers=None, timeout=None, isolated=False):
        if isolated or path in self._URGENT_PATHS or path in self._LONG_PATHS:
            return self._isolated_raw_request(method, path, body=body, headers=headers, timeout=timeout)
        with self._lock:
            for attempt in range(2):
                conn = self._get_connection(timeout)
                try:
                    conn.request(method, path, body=body, headers=headers or {})
                    response = conn.getresponse()
                    raw = response.read()
                    return response.status, raw
                except (http.client.CannotSendRequest, http.client.BadStatusLine,
                        http.client.RemoteDisconnected, ConnectionError, OSError,
                        TimeoutError, http.client.HTTPException) as exc:
                    self._close_connection()
                    if attempt == 1:
                        raise MoonrakerError("Moonraker'a ulaşılamıyor: %s" % (exc,)) from exc

    def request(self, method, path, body=None, headers=None, timeout=None, isolated=False):
        """Send HTTP request reusing self._conn, retry once on connection failure."""
        hdrs = dict(headers or {})
        data = None
        if body is not None:
            if isinstance(body, (dict, list)):
                data = json.dumps(body).encode("utf-8")
                if "Content-Type" not in hdrs:
                    hdrs["Content-Type"] = "application/json"
            elif isinstance(body, str):
                data = body.encode("utf-8")
            else:
                data = body
        status, raw = self._raw_request(method, path, body=data, headers=hdrs, timeout=timeout, isolated=isolated)
        try:
            payload = json.loads(raw)
        except ValueError:
            raise MoonrakerError("Moonraker geçersiz yanıt verdi (HTTP %d)" % status)
        if not isinstance(payload, dict):
            raise MoonrakerError("Moonraker geçersiz yanıt verdi (HTTP %d)" % status)
        if status >= 400 or "error" in payload:
            error = payload.get("error")
            if not isinstance(error, dict):
                error = {"message": error} if error else {}
            raise MoonrakerError(str(error.get("message", "HTTP %d" % status)))
        return payload.get("result")

    def _request(self, method, path, body=None, timeout=None, isolated=False):
        return self.request(method, path, body=body, timeout=timeout, isolated=isolated)

    def info(self):
        result = self._request("GET", "/printer/info")
        if not isinstance(result, dict):
            raise MoonrakerError("Moonraker geçersiz yanıt verdi (printer/info)")
        return result

    def query(self, objects):
        """objects: {"extruder": None, ...} -> {"extruder": {...}, ...}"""
        result = self._request("POST", "/printer/objects/query", {"objects": objects})
        status = result.get("status", {}) if isinstance(result, dict) else None
        if not isinstance(status, dict):
            raise MoonrakerError("Moonraker geçersiz yanıt verdi (objects/query)")
        return status

    def gcode(self, script, timeout=300):
        # G-code can run for up to a few minutes (heating, leveling, shaper calibration)
        return self._request("POST", "/printer/gcode/script", {"script": script}, timeout=timeout)

    def files(self):
        return self._request("GET", "/server/files/list?root=gcodes")

    def metadata(self, filename):
        return self._request("GET", "/server/files/metadata?filename=" + urllib.parse.quote(filename))

    def download(self, path):
        """Raw bytes of a file under the gcodes root (used for thumbnails)."""
        status, data = self._raw_request("GET", "/server/files/gcodes/" + urllib.parse.quote(path))
        if status != 200:
            raise MoonrakerError("HTTP %d" % status)
        return data

    def download_config(self, path):
        """UTF-8 text of a file under Moonraker's config root, or None if missing (404)."""
        status, data = self._raw_request("GET", "/server/files/config/" + urllib.parse.quote(path))
        if status == 404:
            return None
        if status != 200:
            raise MoonrakerError("HTTP %d" % status)
        return data.decode("utf-8", errors="replace")

    def upload_config(self, path, content):
        """Upload UTF-8 text to Moonraker's config root via multipart/form-data."""
        boundary = "kobra-%s" % uuid.uuid4().hex
        raw = content.encode("utf-8") if isinstance(content, str) else content
        parts = [
            ("--" + boundary + "\r\nContent-Disposition: form-data; name=\"root\"\r\n\r\nconfig\r\n").encode("utf-8"),
            ("--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"" + path
             + "\"\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n").encode("utf-8") + raw + b"\r\n",
            ("--" + boundary + "--\r\n").encode("utf-8"),
        ]
        body = b"".join(parts)
        headers = {
            "Content-Type": "multipart/form-data; boundary=" + boundary,
            "Content-Length": str(len(body)),
        }
        status, resp_raw = self._raw_request("POST", "/server/files/upload", body=body, headers=headers)
        try:
            payload = json.loads(resp_raw)
        except ValueError:
            payload = {}
        if status >= 400 or "error" in payload:
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            raise MoonrakerError(str(error.get("message", "HTTP %d" % status)))
        return payload.get("result", payload) if isinstance(payload, dict) else payload

    def delete_config(self, path):
        """Delete a file under Moonraker's config root (ignores 404)."""
        status, resp_raw = self._raw_request("DELETE", "/server/files/config/" + urllib.parse.quote(path))
        if status == 404:
            return None
        try:
            payload = json.loads(resp_raw)
        except ValueError:
            payload = {}
        if status >= 400 or "error" in payload:
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            raise MoonrakerError(str(error.get("message", "HTTP %d" % status)))
        return payload.get("result", payload) if isinstance(payload, dict) else payload

    def print_start(self, filename):
        return self._request("POST", "/printer/print/start?filename=" + urllib.parse.quote(filename),
                             timeout=self.START_TIMEOUT, isolated=True)

    def print_pause(self):
        return self._request("POST", "/printer/print/pause", timeout=self.PAUSE_TIMEOUT)

    def print_resume(self):
        return self._request("POST", "/printer/print/resume", timeout=self.RESUME_TIMEOUT)

    def print_cancel(self):
        return self._request("POST", "/printer/print/cancel", timeout=self.CANCEL_TIMEOUT)

    def emergency_stop(self, timeout=None):
        tout = min(self.timeout, 3.0) if timeout is None else timeout
        return self._request("POST", "/printer/emergency_stop", timeout=tout)

    def machine_reboot(self):
        return self._request("POST", "/machine/reboot")

    def firmware_restart(self):
        return self._request("POST", "/printer/firmware_restart")

    def history_list(self, limit=50, start=0, order="desc"):
        return self._request(
            "GET",
            "/server/history/list?limit=%d&start=%d&order=%s"
            % (int(limit), int(start), urllib.parse.quote(str(order))),
        )

    def history_totals(self):
        return self._request("GET", "/server/history/totals")

    def temperature_store(self, include_monitors=False):
        flag = "true" if include_monitors else "false"
        return self._request("GET", "/server/temperature_store?include_monitors=" + flag)

    def proc_stats(self):
        return self._request("GET", "/machine/proc_stats")

    def gcode_store(self, count=100):
        return self._request("GET", "/server/gcode_store?count=%d" % int(count))

    def update_status(self):
        """Return /machine/update/status result, or None if update_manager is not configured (HTTP 404)."""
        status, raw = self._raw_request("GET", "/machine/update/status")
        if status == 404:
            return None
        try:
            payload = json.loads(raw)
        except ValueError:
            raise MoonrakerError("Moonraker geçersiz yanıt verdi (HTTP %d)" % status)
        if status >= 400 or "error" in payload:
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            raise MoonrakerError(str(error.get("message", "HTTP %d" % status)))
        return payload.get("result")


MoonrakerClient = Moonraker


