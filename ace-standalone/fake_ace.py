#!/usr/bin/env python3
"""Software stand-in for an ACE Pro, for testing the panel without hardware.

Opens a pseudo-terminal, prints its path and answers ACE1 JSON-RPC frames:

    python3 fake_ace.py            # prints e.g. /dev/pts/7
    python3 server.py --port /dev/pts/7

Behaviour is modelled on the ACEResearch notes; it is not a capture of a real
device (the real firmware's limits and error replies are not known here).
"""

from __future__ import annotations

import os
import pty
import threading
import time
import tty

from acelink import FrameParser, build_frame

AMBIENT = 24.0


class FakeAce:
    def __init__(self):
        self.master, slave = pty.openpty()
        tty.setraw(slave)
        self.port = os.ttyname(slave)
        self._slave = slave  # keep open so the pty survives client reconnects
        self._parser = FrameParser()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.temp = AMBIENT
        self.dryer = {"status": "stop", "target_temp": 0, "duration": 0, "remain_time": 0}
        self._dry_end = 0.0
        self.requests = []  # every request seen, for tests
        self._threads = [
            threading.Thread(target=self._serve, daemon=True),
            threading.Thread(target=self._simulate, daemon=True),
        ]

    def start(self):
        for thread in self._threads:
            thread.start()
        return self

    def stop(self):
        self._stop.set()

    def _simulate(self):
        while not self._stop.wait(0.1):
            with self._lock:
                drying = self.dryer["status"] == "drying"
                if drying:
                    left = self._dry_end - time.monotonic()
                    if left <= 0:
                        self._stop_drying()
                        drying = False
                    else:
                        self.dryer["remain_time"] = max(0, int(round(left)))
                goal = self.dryer["target_temp"] if drying else AMBIENT
                self.temp += max(-0.3, min(0.5, goal - self.temp))

    def _stop_drying(self):
        self.dryer = {"status": "stop", "target_temp": 0, "duration": 0, "remain_time": 0}

    def _handle(self, request: dict) -> dict:
        method = request.get("method")
        params = request.get("params") or {}
        reply = {"id": request.get("id"), "code": 0, "msg": "success", "result": {}}
        with self._lock:
            self.requests.append(request)
            if method == "get_info":
                reply["result"] = {
                    "id": 0, "slots": 4, "model": "Anycubic Color Engine Pro",
                    "firmware": "V1.3.863 (sahte)", "boot_firmware": "V1.0.1",
                }
            elif method == "get_status":
                reply["result"] = {
                    "status": "ready",
                    "dryer_status": dict(self.dryer),
                    "temp": round(self.temp),
                    "enable_rfid": 1,
                    "fan_speed": 7000 if self.dryer["status"] == "drying" else 0,
                    "feed_assist_count": 0,
                    "cont_assist_time": 0.0,
                    "slots": [
                        {"index": i, "status": "ready" if i < 2 else "empty", "sku": "",
                         "type": "PLA" if i < 2 else "", "color": [[240, 240, 240], [20, 90, 200], [0, 0, 0], [0, 0, 0]][i],
                         "rfid": 0}
                        for i in range(4)
                    ],
                }
            elif method == "drying":
                duration_min = int(params.get("duration", 0))
                self.dryer = {
                    "status": "drying", "target_temp": params.get("temp", 0),
                    "duration": duration_min, "remain_time": duration_min * 60,
                }
                self._dry_end = time.monotonic() + 60 * duration_min
                reply["msg"] = "drying"
            elif method == "drying_stop":
                self._stop_drying()
            elif method == "stop_feed_assist":
                reply["msg"] = ""
            elif method not in (
                "start_feed_assist", "feed_filament", "stop_feed_filament", "unwind_filament",
                "stop_unwind_filament", "update_feeding_speed", "update_unwinding_speed",
                "get_filament_info", "enable_rfid", "disable_rfid",
            ):
                reply.update(code=-1, msg="unknown method")
        return reply

    def _serve(self):
        while not self._stop.is_set():
            try:
                data = os.read(self.master, 4096)
            except OSError:
                time.sleep(0.05)
                continue
            for request in self._parser.feed(data):
                os.write(self.master, build_frame(self._handle(request)))


if __name__ == "__main__":
    ace = FakeAce().start()
    print(ace.port, flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
