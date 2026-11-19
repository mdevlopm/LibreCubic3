#!/usr/bin/env python3
"""
Unit tests for ace_iap.py using a fake ACE device over pty.
"""

import json
import os
import select
import struct
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import ace_iap


class FakeACE:
    def __init__(self, master_fd: int):
        self._master = master_fd
        self._parser = ace_iap.FrameParser()
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._lock = threading.Lock()
        self._state = "idle"
        self._expected_addr = ace_iap.BASE_ADDR
        self._received_data = bytearray()
        self._last_success_addr = None
        self._chunk_count = 0
        self._fail_chunk = -1
        self._fail_count = 0
        self._max_failures = 0
        self._corrupt_crc = False
        self._drop_ack_chunk = -1
        self._drying = False
        self._version = "V1.3.86K"
        self._expected_crc = 0
        self._thread.start()

    def set_drying(self, drying: bool):
        with self._lock:
            self._drying = drying

    def set_fail_chunk(self, chunk_idx: int, max_failures: int = 1):
        with self._lock:
            self._fail_chunk = chunk_idx
            self._max_failures = max_failures
            self._fail_count = 0

    def set_drop_ack_chunk(self, chunk_idx: int):
        self._drop_ack_chunk = chunk_idx

    def set_corrupt_crc(self, corrupt: bool):
        with self._lock:
            self._corrupt_crc = corrupt

    def set_version(self, version: str):
        with self._lock:
            self._version = version

    def get_received_data(self) -> bytes:
        with self._lock:
            return bytes(self._received_data)

    def stop(self):
        self._running = False
        self._thread.join(timeout=2.0)

    def _send_frame(self, frame: bytes):
        os.write(self._master, frame)

    def _send_json(self, request_id: int, code: int, msg: str = "", result: dict | None = None):
        resp = {"id": request_id, "code": code, "msg": msg}
        if result is not None:
            resp["result"] = result
        frame = ace_iap.build_frame(resp)
        if self._corrupt_crc:
            frame = frame[:-3] + bytes([frame[-3] ^ 0xFF]) + frame[-2:]
        self._send_frame(frame)

    def _run(self):
        buf = bytearray()
        while self._running:
            try:
                readable, _, _ = select.select([self._master], [], [], 0.05)
                if not readable:
                    continue
                data = os.read(self._master, 4096)
                if not data:
                    break
                buf += data
                while True:
                    start = buf.find(ace_iap.HEADER)
                    if start == -1:
                        del buf[: max(0, len(buf) - 1)]
                        break
                    del buf[:start]
                    if len(buf) < 7:
                        break
                    length = struct.unpack_from("<H", buf, 2)[0]
                    total = 4 + length + 3
                    if len(buf) < total:
                        if total > 4 * ace_iap.MAX_FRAME:
                            del buf[:2]
                            continue
                        break
                    payload = bytes(buf[4 : 4 + length])
                    crc_rx = struct.unpack_from("<H", buf, 4 + length)[0]
                    if buf[total - 1] != ace_iap.TAIL or crc_rx != ace_iap.crc16(payload):
                        del buf[:2]
                        continue
                    del buf[:total]
                    self._handle_payload(payload)
            except Exception:
                break

    def _handle_payload(self, payload: bytes):
        with self._lock:
            if payload and payload[0] == 0x55:
                self._handle_binary(payload)
            else:
                self._handle_json(payload)

    def _handle_json(self, payload: bytes):
        try:
            req = json.loads(payload.decode("utf-8"))
        except Exception:
            return
        method = req.get("method")
        rid = req.get("id", 0)
        params = req.get("params", {})

        if method == "get_info":
            self._send_json(rid, 0, "success", {"firmware": self._version, "boot_firmware": "V1.0.0"})
        elif method == "get_status":
            status = "drying" if self._drying else "idle"
            self._send_json(rid, 0, "success", {"dryer_status": {"status": status}})
        elif method == "iap_upgrade":
            self._state = "upgrading"
            self._expected_addr = ace_iap.BASE_ADDR
            self._received_data.clear()
            self._last_success_addr = None
            self._chunk_count = 0
            self._fail_count = 0
            self._expected_crc = params.get("crc", 0)
            self._send_json(rid, 0, "success")
        elif method == "iap_upgrade_finish":
            if self._state == "upgrading":
                expected_crc = ace_iap.crc16(bytes(self._received_data))
                if self._corrupt_crc or expected_crc != self._expected_crc:
                    self._send_json(rid, 2, "Upgrade_error")
                else:
                    self._send_json(rid, 0, "Upgrade_success")
                    self._state = "rebooted"
                    self._version = params.get("version", "V1.3.86K")
            else:
                self._send_json(rid, 1, "not_in_upgrade_mode")
        else:
            self._send_json(rid, 1, f"unknown_method:{method}")

    def _handle_binary(self, payload: bytes):
        if len(payload) < 6:
            return
        addr = struct.unpack_from("<I", payload, 1)[0]
        n = payload[5]
        data = payload[6 : 6 + n]

        if self._state != "upgrading":
            self._send_json(0, 1, "not_in_upgrade_mode")
            return

        if self._chunk_count == self._fail_chunk and self._fail_count < self._max_failures:
            self._fail_count += 1
            self._send_json(0, 2, "write_error2")
            return

        expected_addr = ace_iap.BASE_ADDR if self._last_success_addr is None else self._last_success_addr + ace_iap.CHUNK_SIZE
        if addr != expected_addr:
            self._send_json(0, 1, "write_error1")
            return

        self._received_data.extend(data)
        self._last_success_addr = addr
        self._chunk_count += 1
        if self._chunk_count - 1 == self._drop_ack_chunk:
            self._drop_ack_chunk = -1  # written, but the ack is lost once
            return
        self._send_json(0, 0, "write_success")


class TestACEIAP(unittest.TestCase):
    def setUp(self):
        self.master, self.slave = os.openpty()
        self.port = os.ttyname(self.slave)
        self.fake = FakeACE(self.master)
        time.sleep(0.2)

    def tearDown(self):
        self.fake.stop()
        os.close(self.master)
        os.close(self.slave)

    def _run_uploader(self, image_data: bytes, **kwargs) -> tuple[int, str]:
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(image_data)
            img_path = f.name
        try:
            uploader = ace_iap.IAPUploader(self.port)
            if not uploader._open():
                return 2, ""
            try:
                uploader.pre_check()
                size, img_crc, version = ace_iap.validate_image(image_data)
                if kwargs.get("version"):
                    version = kwargs["version"]
                uploader.upgrade_start(size, img_crc, version)
                uploader.send_chunks(image_data)
                uploader.upgrade_finish()
                return 0, version
            except ValueError as e:
                return 1, str(e)
            except RuntimeError as e:
                return 2, str(e)
            finally:
                uploader._close()
        finally:
            try:
                os.unlink(img_path)
            except OSError:
                pass

    def _make_valid_image(self, size: int = 4096) -> bytes:
        data = bytearray(size)
        struct.pack_into("<I", data, 0, 0x20001000)
        struct.pack_into("<I", data, 4, 0x08010001)
        version_bytes = b"V1.3.86K\x00"
        data[100:100+len(version_bytes)] = version_bytes
        return bytes(data)

    def test_full_successful_upload(self):
        img = self._make_valid_image(4096)
        code, _ = self._run_uploader(img)
        self.assertEqual(code, 0)
        received = self.fake.get_received_data()
        self.assertEqual(received, img)

    def test_chunk_retry_on_write_error2(self):
        img = self._make_valid_image(4096)
        self.fake.set_fail_chunk(5, max_failures=1)
        code, _ = self._run_uploader(img)
        self.assertEqual(code, 0)
        received = self.fake.get_received_data()
        self.assertEqual(received, img)

    def test_chunk_permanent_failure(self):
        img = self._make_valid_image(4096)
        self.fake.set_fail_chunk(3, max_failures=10)
        code, _ = self._run_uploader(img)
        self.assertEqual(code, 2)

    def test_crc_mismatch_on_finish(self):
        img = self._make_valid_image(4096)
        self.fake.set_corrupt_crc(True)
        code, _ = self._run_uploader(img)
        self.assertEqual(code, 2)

    def test_drying_blocks_upload(self):
        img = self._make_valid_image(4096)
        self.fake.set_drying(True)
        code, _ = self._run_uploader(img)
        self.assertEqual(code, 2)

    def test_lost_ack_does_not_resend_forever(self):
        img = self._make_valid_image(4096)
        self.fake.set_drop_ack_chunk(5)
        code, _ = self._run_uploader(img)
        self.assertEqual(code, 0)
        self.assertEqual(self.fake.get_received_data(), img)

    def test_invalid_image_wrong_sp(self):
        img = bytearray(self._make_valid_image(4096))
        struct.pack_into("<I", img, 0, 0x10000000)
        code, _ = self._run_uploader(bytes(img))
        self.assertEqual(code, 1)

    def test_invalid_image_wrong_size(self):
        img = self._make_valid_image(1023)
        code, _ = self._run_uploader(img)
        self.assertEqual(code, 1)

    def test_dry_run_no_write(self):
        img = self._make_valid_image(4096)
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(img)
            img_path = f.name
        try:
            old_argv = sys.argv
            sys.argv = ["ace_iap.py", "--dry-run", "--port", self.port, img_path]
            try:
                exit_code = ace_iap.main()
            except SystemExit as e:
                exit_code = e.code
            self.assertEqual(exit_code, 0)
        finally:
            sys.argv = old_argv
            try:
                os.unlink(img_path)
            except OSError:
                pass
        received = self.fake.get_received_data()
        self.assertEqual(len(received), 0)

    def test_crc16_matches_acelink(self):
        test_data = b"hello world"
        self.assertEqual(ace_iap.crc16(test_data), 0x51F9)

    def test_binary_packet_encoding(self):
        addr = 0x08024000
        data = b"\x00" * 128
        frame = ace_iap.build_binary_packet(addr, data)
        self.assertTrue(frame.startswith(ace_iap.HEADER))
        self.assertEqual(frame[-1], ace_iap.TAIL)
        length = struct.unpack_from("<H", frame, 2)[0]
        payload = frame[4 : 4 + length]
        self.assertEqual(payload[0], 0x55)
        self.assertEqual(struct.unpack_from("<I", payload, 1)[0], addr)
        self.assertEqual(payload[5], 128)
        self.assertEqual(payload[6:], data)
        crc_rx = struct.unpack_from("<H", frame, 4 + length)[0]
        self.assertEqual(crc_rx, ace_iap.crc16(payload))


if __name__ == "__main__":
    unittest.main(verbosity=2)