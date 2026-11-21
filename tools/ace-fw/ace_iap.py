#!/usr/bin/env python3
"""
ACE Pro Gen1 firmware upload tool (IAP).

Protocol:
  Serial: 115200 8N1, raw mode.
  Frame: FF AA + len(u16 LE) + payload + crc16(payload) + FE
  JSON request: payload = compressed JSON {"id":N,"method":...,"params":{...}}
  Binary data packet: payload = 0x55 + addr(u32 LE) + n(u8) + n bytes data

Flow:
  1. get_info, get_status (refuse while drying)
  2. iap_upgrade with size, crc, version
  3. Send image in 128-byte chunks at addr 0x08024000 + i*128
  4. iap_upgrade_finish
  5. Wait for reboot, verify version

Exit codes: 0=success, 1=usage/image error, 2=protocol/device error, 3=version mismatch
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import select
import struct
import sys
import termios
import threading
import time

HEADER = b"\xFF\xAA"
TAIL = 0xFE
MAX_FRAME = 1024
CHUNK_SIZE = 128
BASE_ADDR = 0x08024000

IMAGE_MIN_SIZE = 1024
IMAGE_MAX_SIZE = 0x1C000
SP_MIN, SP_MAX = 0x20000000, 0x20020000
RESET_VEC_MIN, RESET_VEC_MAX = 0x08008000, 0x08024000

IAP_UPGRADE_TIMEOUT = 30.0
CHUNK_TIMEOUT = 3.0
FINISH_TIMEOUT = 30.0
REBOOT_WAIT = 90.0
REBOOT_POLL = 5.0
MAX_RETRIES = 5
PROGRESS_STEP = 5
DEFAULT_VERSION = "V1.3.86K"


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc


def build_frame(request: dict) -> bytes:
    payload = json.dumps(request, separators=(",", ":")).encode()
    frame = HEADER + struct.pack("<H", len(payload)) + payload
    frame += struct.pack("<H", crc16(payload)) + bytes([TAIL])
    if len(frame) > MAX_FRAME:
        raise RuntimeError(f"frame too long ({len(frame)} > {MAX_FRAME} bytes)")
    return frame


def build_binary_packet(addr: int, data: bytes) -> bytes:
    n = len(data)
    payload = b"\x55" + struct.pack("<I", addr) + bytes([n]) + data
    frame = HEADER + struct.pack("<H", len(payload)) + payload
    frame += struct.pack("<H", crc16(payload)) + bytes([TAIL])
    return frame


class FrameParser:
    def __init__(self):
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[dict]:
        self._buf += data
        out = []
        while True:
            start = self._buf.find(HEADER)
            if start == -1:
                del self._buf[: max(0, len(self._buf) - 1)]
                break
            del self._buf[:start]
            if len(self._buf) < 7:
                break
            length = struct.unpack_from("<H", self._buf, 2)[0]
            total = 4 + length + 3
            if len(self._buf) < total:
                if total > 4 * MAX_FRAME:
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
                # strict=False: boot_firmware may hold raw control bytes once
                # the bootloader info block has been rewritten
                out.append(json.loads(payload.decode("utf-8", "replace"), strict=False))
            except ValueError:
                pass
        return out


def candidate_ports() -> list[str]:
    ports, seen = [], set()
    for pattern in ("/sys/class/tty/ttyACM*", "/sys/class/tty/ttyUSB*"):
        for tty in sorted(glob.glob(pattern)):
            try:
                desc = " ".join(
                    open(os.path.join(tty, *rel)).read().strip()
                    for rel in (("device", "..", "product"), ("device", "..", "manufacturer"),
                                ("device", "..", "..", "product"), ("device", "..", "..", "manufacturer"))
                    if os.path.exists(os.path.join(tty, *rel))
                )
                if "ace" in desc.lower():
                    dev = "/dev/" + os.path.basename(tty)
                    if dev not in seen:
                        seen.add(dev)
                        ports.append(dev)
            except OSError:
                pass
    for link in sorted(glob.glob("/dev/serial/by-id/*")):
        if "ace" in os.path.basename(link).lower():
            dev = os.path.realpath(link)
            if dev not in seen:
                seen.add(dev)
                ports.append(dev)
    if ports:
        return ports
    for pattern in ("/dev/ttyACM*", "/dev/serial/by-id/*", "/dev/ttyUSB*"):
        for path in sorted(glob.glob(pattern)):
            dev = os.path.realpath(path) if "/by-id/" in path else path
            if dev not in seen:
                seen.add(dev)
                ports.append(dev)
    return ports


def _open_serial(path: str) -> int:
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        attrs = termios.tcgetattr(fd)
        attrs[0] = attrs[1] = attrs[3] = 0
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[4] = attrs[5] = termios.B115200
        attrs[6][termios.VMIN] = attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIOFLUSH)
    except Exception:
        os.close(fd)
        raise
    return fd


def validate_image(data: bytes) -> tuple[int, int, str]:
    size = len(data)
    if not (IMAGE_MIN_SIZE <= size <= IMAGE_MAX_SIZE) or size % 4 != 0:
        raise ValueError(f"image size {size} invalid (range [{IMAGE_MIN_SIZE}, {IMAGE_MAX_SIZE}], multiple of 4)")
    sp = struct.unpack_from("<I", data, 0)[0]
    if not (SP_MIN <= sp <= SP_MAX):
        raise ValueError(f"stack pointer {sp:#x} out of range [{SP_MIN:#x}, {SP_MAX:#x}]")
    reset_vec = struct.unpack_from("<I", data, 4)[0]
    if reset_vec % 2 == 0 or not (RESET_VEC_MIN <= reset_vec <= RESET_VEC_MAX):
        raise ValueError(f"reset vector {reset_vec:#x} invalid (must be odd, in [{RESET_VEC_MIN:#x}, {RESET_VEC_MAX:#x}])")
    img_crc = crc16(data)
    version = DEFAULT_VERSION
    for variant in (b"V1.3.86K", b"V1.3.863"):
        idx = data.find(variant)
        if idx != -1:
            end = data.find(b"\x00", idx)
            version = data[idx : end if end != -1 else idx + len(variant)].decode(errors="ignore")
            break
    return size, img_crc, version


class VersionMismatch(Exception):
    pass


class IAPUploader:
    def __init__(self, port: str | None, timeout: float = 2.0):
        self._port = port
        self._timeout = timeout
        self._fd = -1
        self._parser = FrameParser()
        self._next_id = 0
        self._lock = threading.Lock()

    def _open(self) -> bool:
        ports = [self._port] if self._port else candidate_ports()
        if not ports:
            print("ACE bulunamadı", file=sys.stderr)
            return False
        for port in ports:
            try:
                fd = _open_serial(port)
            except OSError as exc:
                continue
            self._fd = fd
            self._parser = FrameParser()
            try:
                self._transact("get_info", None, 5.0)
            except (OSError, RuntimeError):
                self._close()
                continue
            self._port = port
            return True
        return False

    def _close(self):
        if self._fd >= 0:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = -1

    def _transact(self, method: str, params: dict | None, timeout: float | None = None) -> dict:
        if self._fd < 0:
            raise RuntimeError("port closed")
        request = {"method": method}
        if params:
            request["params"] = params
        while True:
            self._next_id = (self._next_id + 1) % 0xFFFF or 1
            frame = build_frame({"id": self._next_id, **request})
            if frame[-3:-1] != HEADER:
                break
        deadline = time.monotonic() + (timeout or self._timeout)
        view = memoryview(frame)
        while view:
            _, w, _ = select.select([], [self._fd], [], max(0.0, deadline - time.monotonic()))
            if not w:
                raise RuntimeError(f"{method}: write timeout")
            view = view[os.write(self._fd, view) :]
        while True:
            rem = deadline - time.monotonic()
            if rem <= 0:
                raise RuntimeError(f"{method}: response timeout")
            r, _, _ = select.select([self._fd], [], [], rem)
            if not r:
                continue
            data = os.read(self._fd, 4096)
            if not data:
                raise OSError("port closed")
            for resp in self._parser.feed(data):
                if resp.get("id") == self._next_id:
                    return resp

    def _drain(self):
        """Drop late replies so they are not taken as the answer to the next packet."""
        while select.select([self._fd], [], [], 0.2)[0]:
            if not os.read(self._fd, 4096):
                break
        self._parser = FrameParser()

    def send_binary(self, addr: int, data: bytes, timeout: float = CHUNK_TIMEOUT) -> dict:
        if self._fd < 0:
            raise RuntimeError("port closed")
        frame = build_binary_packet(addr, data)
        deadline = time.monotonic() + timeout
        view = memoryview(frame)
        while view:
            _, w, _ = select.select([], [self._fd], [], max(0.0, deadline - time.monotonic()))
            if not w:
                raise RuntimeError("binary write timeout")
            view = view[os.write(self._fd, view) :]
        while True:
            rem = deadline - time.monotonic()
            if rem <= 0:
                raise RuntimeError("binary response timeout")
            r, _, _ = select.select([self._fd], [], [], rem)
            if not r:
                continue
            rx = os.read(self._fd, 4096)
            if not rx:
                raise OSError("port closed")
            for resp in self._parser.feed(rx):
                if resp.get("id") == 0:
                    return resp

    def pre_check(self):
        info = self._transact("get_info", None, 5.0).get("result", {})
        print(f"firmware: {info.get('firmware', '?')}")
        print(f"boot_firmware: {info.get('boot_firmware', '?')}")
        status = self._transact("get_status", None, 5.0).get("result", {})
        if status.get("dryer_status", {}).get("status") == "drying":
            raise RuntimeError("drying active; stop drying first")

    def upgrade_start(self, size: int, img_crc: int, version: str):
        resp = self._transact("iap_upgrade", {"size": size, "crc": img_crc, "version": version}, IAP_UPGRADE_TIMEOUT)
        if resp.get("code") != 0 or resp.get("msg") != "success":
            raise RuntimeError(f"iap_upgrade failed: code={resp.get('code')} msg={resp.get('msg')!r}")

    def send_chunks(self, data: bytes):
        total = (len(data) + CHUNK_SIZE - 1) // CHUNK_SIZE
        next_pct = PROGRESS_STEP
        for i in range(total):
            offset = i * CHUNK_SIZE
            chunk = data[offset : offset + CHUNK_SIZE]
            addr = BASE_ADDR + offset
            last_err = None
            timed_out = False
            for attempt in range(MAX_RETRIES):
                try:
                    resp = self.send_binary(addr, chunk)
                    if resp.get("code") == 0 and resp.get("msg") == "write_success":
                        break
                    # The device only accepts the chunk that follows the last one it wrote.
                    # write_error1 right after a lost ack means this chunk already landed;
                    # the CRC check in iap_upgrade_finish catches any mistake here.
                    if timed_out and resp.get("code") == 1 and addr != BASE_ADDR:
                        break
                    last_err = f"code={resp.get('code')} msg={resp.get('msg')!r}"
                except RuntimeError as e:
                    last_err = str(e)
                    timed_out = True
                if attempt < MAX_RETRIES - 1:
                    time.sleep(0.1)
                    self._drain()
            else:
                raise RuntimeError(f"chunk {i+1}/{total} (addr={addr:#x}) failed: {last_err}")
            pct = (i + 1) * 100 // total
            if pct >= next_pct:
                print(f"%{pct}")
                next_pct += PROGRESS_STEP

    def upgrade_finish(self):
        resp = self._transact("iap_upgrade_finish", None, FINISH_TIMEOUT)
        if resp.get("code") != 0 or resp.get("msg") != "Upgrade_success":
            raise RuntimeError(f"iap_upgrade_finish failed: code={resp.get('code')} msg={resp.get('msg')!r}")

    def wait_reboot_and_verify(self, expect_version: str | None):
        self._close()
        deadline = time.monotonic() + REBOOT_WAIT
        while time.monotonic() < deadline:
            for port in candidate_ports():
                try:
                    fd = _open_serial(port)
                except OSError:
                    continue
                try:
                    parser = FrameParser()
                    req_id = 1
                    frame = build_frame({"id": req_id, "method": "get_info"})
                    view = memoryview(frame)
                    while view:
                        _, w, _ = select.select([], [fd], [], 0.5)
                        if not w:
                            break
                        view = view[os.write(fd, view) :]
                    deadline2 = time.monotonic() + 2.0
                    while time.monotonic() < deadline2:
                        r, _, _ = select.select([fd], [], [], 0.2)
                        if not r:
                            continue
                        rx = os.read(fd, 4096)
                        if not rx:
                            break
                        for resp in parser.feed(rx):
                            if resp.get("id") == req_id:
                                fw = resp.get("result", {}).get("firmware", "")
                                print(f"yeni firmware: {fw}")
                                os.close(fd)
                                if expect_version and expect_version not in fw:
                                    raise VersionMismatch(f"expected={expect_version!r} got={fw!r}")
                                return
                    os.close(fd)
                except VersionMismatch:
                    raise
                except Exception:
                    try:
                        os.close(fd)
                    except Exception:
                        pass
            time.sleep(REBOOT_POLL)
        raise RuntimeError("port not found after reboot")


def main() -> int:
    parser = argparse.ArgumentParser(description="ACE Pro Gen1 firmware uploader (IAP)")
    parser.add_argument("--port", help="Serial port (e.g. /dev/ttyACM0). Auto-detect if omitted.")
    parser.add_argument("--version", help="Firmware version string (default: from image)")
    parser.add_argument("--expect-version", help="Expected version after upgrade")
    parser.add_argument("--dry-run", action="store_true", help="Print size, CRC, chunks, version without writing")
    parser.add_argument("image", nargs="?", help="Firmware image file (IMAGE.bin)")
    args = parser.parse_args()

    if not args.image and not args.dry_run:
        parser.error("image file required (IMAGE.bin)")
    if args.dry_run and not args.image:
        parser.error("--dry-run requires image file")

    try:
        with open(args.image, "rb") as f:
            data = f.read()
    except OSError as e:
        print(f"image read error: {e}", file=sys.stderr)
        return 1

    try:
        size, img_crc, version = validate_image(data)
    except ValueError as e:
        print(f"invalid image: {e}", file=sys.stderr)
        return 1

    if args.version:
        version = args.version

    print(f"boyut: {size} bayt ({(size + CHUNK_SIZE - 1) // CHUNK_SIZE} parça)")
    print(f"crc16: {img_crc:#06x}")
    print(f"sürüm: {version}")

    if args.dry_run:
        return 0

    uploader = IAPUploader(args.port)
    try:
        if not uploader._open():
            return 2
        uploader.pre_check()
        uploader.upgrade_start(size, img_crc, version)
        uploader.send_chunks(data)
        uploader.upgrade_finish()
        uploader.wait_reboot_and_verify(args.expect_version)
        print("Yükleme başarılı.")
        return 0
    except VersionMismatch as e:
        print(f"HATA: sürüm eşleşmedi: {e}", file=sys.stderr)
        return 3
    except RuntimeError as e:
        print(f"HATA: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"Seri port hatası: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())