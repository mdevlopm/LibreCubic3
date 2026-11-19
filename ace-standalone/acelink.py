"""Anycubic ACE Pro (Gen1) USB link, no printer required.

Speaks the ACE1 JSON-RPC protocol over the ACE's USB CDC serial port using
only the Python standard library (termios), so it runs on any Linux host.

Frame: FF AA | len (u16 LE) | JSON | CRC-16/MCRF4XX of JSON (u16 LE) | FE
Sources: printers-for-people/ACEResearch (CC0) and the ACEPRO Klipper driver.
"""

from __future__ import annotations

import glob
import json
import os
import select
import struct
import termios
import threading
import time

HEADER = b"\xFF\xAA"
TAIL = 0xFE
MAX_FRAME = 1024  # the ACE reportedly locks up on longer frames
KEEPALIVE_S = 1.0  # the ACE drops USB after 3 s without a complete frame


class AceError(Exception):
    """Request could not be completed (not connected, timeout, device error)."""


def crc16(data: bytes) -> int:
    """CRC-16/MCRF4XX (init 0xFFFF, reflected poly 0x8408, no final XOR)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc


def build_frame(request: dict) -> bytes:
    payload = json.dumps(request, separators=(",", ":")).encode("utf-8")
    frame = HEADER + struct.pack("<H", len(payload)) + payload
    frame += struct.pack("<H", crc16(payload)) + bytes([TAIL])
    if len(frame) > MAX_FRAME:
        raise AceError(f"frame too long ({len(frame)} > {MAX_FRAME} bytes)")
    return frame


class FrameParser:
    """Incremental parser; tolerates junk and frames split across reads."""

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[dict]:
        self._buf += data
        out = []
        while True:
            start = self._buf.find(HEADER)
            if start == -1:
                # keep a trailing FF, it may be the first half of a header
                del self._buf[: max(0, len(self._buf) - 1)]
                break
            del self._buf[:start]
            if len(self._buf) < 7:
                break
            length = struct.unpack_from("<H", self._buf, 2)[0]
            total = 4 + length + 3
            if len(self._buf) < total:
                if total > 4 * MAX_FRAME:  # bogus length, resync
                    del self._buf[:2]
                    continue
                break
            payload = bytes(self._buf[4 : 4 + length])
            crc_rx = struct.unpack_from("<H", self._buf, 4 + length)[0]
            if self._buf[total - 1] != TAIL or crc_rx != crc16(payload):
                del self._buf[:2]
                continue
            del self._buf[:total]
            try:
                # strict=False: boot_firmware holds raw bytes after an IAP update
                out.append(json.loads(payload.decode("utf-8"), strict=False))
            except (UnicodeDecodeError, ValueError):
                pass
        return out


def _read_usb_desc(tty_sysfs: str) -> str:
    """Read USB product/manufacturer strings from sysfs for ttyACM or ttyUSB."""
    parts = []
    for rel in (
        ("device", "..", "product"),
        ("device", "..", "manufacturer"),
        ("device", "..", "..", "product"),
        ("device", "..", "..", "manufacturer"),
    ):
        try:
            with open(os.path.join(tty_sysfs, *rel)) as f:
                val = f.read().strip()
                if val:
                    parts.append(val)
        except OSError:
            pass
    return " ".join(parts)


def find_ace_ports() -> list[str]:
    """Return USB serial devices whose USB description or by-id name mentions ACE."""
    ports = []
    seen = set()

    for pattern in ("/sys/class/tty/ttyACM*", "/sys/class/tty/ttyUSB*"):
        for tty in sorted(glob.glob(pattern)):
            if "ace" in _read_usb_desc(tty).lower():
                dev = "/dev/" + os.path.basename(tty)
                if dev not in seen:
                    seen.add(dev)
                    ports.append(dev)

    for link in sorted(glob.glob("/dev/serial/by-id/*")):
        if "ace" in os.path.basename(link).lower():
            dev = os.path.realpath(link)
            if dev not in seen:
                seen.add(dev)
                ports.append(dev)

    return ports


def candidate_ports() -> list[str]:
    """Return ACE-named ports, or fall back to ttyACM*, serial/by-id/*, and ttyUSB*."""
    ports = find_ace_ports()
    if ports:
        return ports

    seen = set()
    candidates = []
    for pattern in ("/dev/ttyACM*", "/dev/serial/by-id/*", "/dev/ttyUSB*"):
        for path in sorted(glob.glob(pattern)):
            dev = os.path.realpath(path) if "/by-id/" in path else path
            if dev not in seen:
                seen.add(dev)
                candidates.append(dev)
    return candidates


def _open_serial(path: str) -> int:
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        attrs = termios.tcgetattr(fd)
        attrs[0] = 0  # iflag
        attrs[1] = 0  # oflag
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL  # cflag
        attrs[3] = 0  # lflag
        attrs[4] = attrs[5] = termios.B115200
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIOFLUSH)
    except Exception:
        os.close(fd)
        raise
    return fd


class AceLink:
    """Keeps one ACE connected, polls its status and runs requests.

    One request is on the wire at a time (the ACE shares a single buffer for
    input and output), guarded by a lock shared by the poller and callers.
    """

    def __init__(self, port: str | None = None, timeout: float = 2.0):
        self._wanted_port = port
        self._timeout = timeout
        self._io_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._fd = -1
        self._parser = FrameParser()
        self._next_id = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ace-link", daemon=True)
        self._state = {
            "connected": False,
            "port": None,
            "info": None,
            "status": None,
            "status_age": None,
            "error": "bağlantı bekleniyor",
        }
        self._status_time = 0.0

    # ---- public API ----

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=5)
        with self._io_lock:
            self._close()

    def snapshot(self) -> dict:
        with self._state_lock:
            state = dict(self._state)
        if state["connected"] and self._status_time:
            state["status_age"] = round(time.monotonic() - self._status_time, 1)
        return state

    def call(self, method: str, params: dict | None = None) -> dict:
        """Send one request and return the full response dict.

        Raises AceError when not connected, on timeout, or when the ACE
        answers with a non-zero code.
        """
        with self._io_lock:
            if self._fd < 0:
                raise AceError("ACE bağlı değil")
            try:
                response = self._transact(method, params)
            except OSError as exc:
                self._drop(f"seri port hatası: {exc}")
                raise AceError(f"seri port hatası: {exc}") from exc
        code = response.get("code", 0)
        if code != 0:
            raise AceError(f"ACE hata döndürdü: code={code} msg={response.get('msg')!r}")
        return response

    # ---- internals ----

    def _set(self, **changes):
        with self._state_lock:
            self._state.update(changes)

    def _close(self):
        if self._fd >= 0:
            try:
                os.close(self._fd)
            except OSError:
                pass
        self._fd = -1

    def _drop(self, reason: str):
        self._close()
        self._status_time = 0.0
        self._set(connected=False, status=None, status_age=None, error=reason)

    def _transact(self, method: str, params: dict | None) -> dict:
        request = {"method": method}
        if params:
            request["params"] = params
        while True:
            self._next_id = self._next_id + 1 if self._next_id < 0xFFFF else 1
            request_id = self._next_id
            frame = build_frame({"id": request_id, **request})
            # a CRC of FF AA would look like a frame header to the ACE
            if frame[-3:-1] != HEADER:
                break
        view = memoryview(frame)
        deadline = time.monotonic() + self._timeout
        while view:
            _, writable, _ = select.select([], [self._fd], [], max(0.0, deadline - time.monotonic()))
            if not writable:
                raise AceError(f"{method}: yazma zaman aşımı")
            view = view[os.write(self._fd, view) :]
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AceError(f"{method}: yanıt zaman aşımı ({self._timeout:g} s)")
            readable, _, _ = select.select([self._fd], [], [], remaining)
            if not readable:
                continue
            data = os.read(self._fd, 4096)
            if not data:
                raise OSError("port kapandı")
            for response in self._parser.feed(data):
                if response.get("id") == request_id:
                    return response

    def _connect(self) -> bool:
        ports = [self._wanted_port] if self._wanted_port else candidate_ports()
        if not ports:
            self._set(error="ACE bulunamadı (USB kablosu ve ACE gücü?)")
            return False
        last_error = "ACE bulunamadı"
        for port in ports:
            try:
                fd = _open_serial(port)
            except OSError as exc:
                last_error = f"{port} açılamadı: {exc}"
                continue
            with self._io_lock:
                self._fd = fd
                self._parser = FrameParser()
                try:
                    info = self._transact("get_info", None).get("result")
                except (OSError, AceError) as exc:
                    self._drop(f"{port}: ACE yanıt vermedi ({exc})")
                    last_error = f"{port}: ACE yanıt vermedi ({exc})"
                    continue
            self._set(connected=True, port=port, info=info, error=None)
            return True
        self._set(error=last_error)
        return False

    def _run(self):
        misses = 0
        while not self._stop.is_set():
            if self._fd < 0:
                if not self._connect():
                    self._stop.wait(2.0)
                    continue
                misses = 0
            started = time.monotonic()
            try:
                with self._io_lock:
                    if self._fd < 0:
                        continue
                    response = self._transact("get_status", None)
                self._status_time = time.monotonic()
                self._set(status=response.get("result"), error=None)
                misses = 0
            except OSError as exc:
                with self._io_lock:
                    self._drop(f"seri port hatası: {exc}")
            except AceError as exc:
                misses += 1
                self._set(error=str(exc))
                if misses >= 3:
                    with self._io_lock:
                        self._drop("ACE yanıt vermiyor, yeniden bağlanılıyor")
            self._stop.wait(max(0.0, KEEPALIVE_S - (time.monotonic() - started)))
