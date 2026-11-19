"""Regression tests for the independent audit of 0.2.1 (PA-R01 ... PA-R11)."""

import struct
import unittest

import _fakes
from _fakes import (FakeGcode, FakeReactor, FakeSerial, FakeGcmd, feed_reply, frame_for,
                    make_manager, open_fake_port, sent_frames, status_result)

from ace import commands, serial_manager as sm_mod
from ace.protocol_ace1 import AceJsonProtocolAdapter
from ace.serial_manager import AceSerialManager

sm_mod.termios = _fakes.FakeTermios()


class Base(unittest.TestCase):
    def build(self, overrides=None, state="standby"):
        self.printer, self.manager = make_manager(overrides, print_state=state)
        self.ace = self.manager.instances[0]
        self.mgr = self.ace.serial_mgr
        self.reactor = self.printer.reactor
        self.gcode = self.printer.gcode
        self.ser = open_fake_port(self.mgr)

    def frames(self):
        return sent_frames(self.ser)

    def disconnect_port(self):
        self.mgr._connected = False
        self.ser.is_open = False


# ---------------------------------------------------------------- PA-R01
class StartCommandsAreNotResentTests(Base):
    START = [
        {"method": "feed_filament", "params": {"index": 0, "length": 10, "speed": 5}},
        {"method": "unwind_filament", "params": {"index": 0, "length": 10, "speed": 5}},
        {"method": "drying", "params": {"temp": 50, "duration": 10}},
        {"method": "start_feed_assist", "params": {"index": 0}},
        {"method": "update_feeding_speed", "params": {"index": 0, "speed": 5}},
        {"method": "update_unwinding_speed", "params": {"index": 0, "speed": 5}},
    ]

    def test_slow_reply_is_never_answered_with_a_second_frame(self):
        for req in self.START:
            self.build()
            got = []
            self.mgr.send_request(dict(req), lambda response=None: got.append(response))
            self.mgr._writer(self.reactor.now)
            for _ in range(3):                      # 1.5 s, 3.0 s, 3.9 s: still waiting
                self.reactor.advance(1.3)
                self.mgr._writer(self.reactor.now)
            self.assertEqual(len(self.frames()), 1, req["method"])
            self.assertEqual(got, [], req["method"])
            feed_reply(self.mgr, self.ser, self.frames()[0]["id"])   # reply at ~3.9 s
            self.mgr._reader(self.reactor.now)
            self.assertEqual(got[0]["code"], 0)

    def test_no_reply_at_all_fails_once_after_the_longer_wait(self):
        self.build()
        got = []
        self.mgr.send_request(dict(self.START[0]), lambda response=None: got.append(response))
        self.mgr._writer(self.reactor.now)
        self.reactor.advance(3.9)
        self.mgr._writer(self.reactor.now)
        self.assertEqual(got, [])
        self.reactor.advance(0.3)
        self.mgr._writer(self.reactor.now)
        self.assertEqual(got, [None])
        self.assertEqual(len(self.frames()), 1)
        self.assertTrue(any("TIMEOUT after 1 attempt" in m for m in self.gcode.info))

    def test_queries_and_stops_keep_one_second_times_four(self):
        for method in ("get_status", "get_info", "get_filament_info", "drying_stop",
                       "stop_feed_filament", "stop_unwind_filament", "stop_feed_assist",
                       "set_fan_speed"):
            self.build()
            self.mgr.send_request({"method": method, "params": {"index": 0}}, lambda response=None: None)
            self.mgr._writer(self.reactor.now)
            for _ in range(3):
                self.reactor.advance(1.05)
                self.mgr._writer(self.reactor.now)
            self.assertEqual(len(self.frames()), 4, method)
            self.assertEqual(len({f["id"] for f in self.frames()}), 1)

    def test_configured_timeout_larger_than_start_timeout_wins(self):
        self.build({"serial_request_timeout": 6.0})
        self.assertEqual(self.mgr._timeout_for(self.START[0]), 6.0)
        self.assertEqual(self.mgr._timeout_for({"method": "get_status"}), 6.0)


# ---------------------------------------------------------------- PA-R03
class PendingStopTests(Base):
    def test_stop_while_disconnected_is_reported_and_sent_after_reconnect(self):
        self.build()
        self.disconnect_port()
        self.ace._stop_feed(1)
        self.assertEqual(len(self.gcode.raw), 1)
        self.assertTrue(self.gcode.raw[0].startswith("!! ACE[0]: Stop request 'stop_feed_filament' not sent"))
        self.assertIn("will be sent", self.gcode.raw[0])
        self.assertTrue(self.mgr.has_pending_stop({"method": "stop_feed_filament", "params": {"index": 1}}))
        self.assertTrue(self.mgr.connect("/dev/ttyACM0", 115200))
        port = FakeSerial.instances[-1]
        self.mgr._writer(self.reactor.now)
        self.assertEqual([f["method"] for f in sent_frames(port)], ["stop_feed_filament"])  # before get_status
        self.assertFalse(self.mgr._undelivered_stops)
        mgr_out = port.written
        self.assertTrue(mgr_out)

    def test_pending_stop_callback_reports_when_finally_delivered(self):
        self.build()
        self.disconnect_port()
        self.ace._stop_retract(2)
        self.mgr.connect("/dev/ttyACM0", 115200)
        port = FakeSerial.instances[-1]
        self.mgr._writer(self.reactor.now)
        feed_reply(self.mgr, port, sent_frames(port)[0]["id"])
        self.mgr._reader(self.reactor.now)
        self.assertTrue(any("Stop retract successful" in m for m in self.gcode.info))

    def test_stop_swallowed_by_a_disconnect_is_remembered_and_resent(self):
        self.build()
        self.ace._stop_feed(0)                         # queued, not yet written
        self.assertEqual(self.mgr._urgent_queue.qsize(), 1)
        self.mgr.disconnect()                          # clear_queues drops it silently...
        self.assertTrue(self.mgr.has_pending_stop({"method": "stop_feed_filament", "params": {"index": 0}}))
        self.mgr.connect("/dev/ttyACM0", 115200)       # ...but it comes back
        port = FakeSerial.instances[-1]
        self.mgr._writer(self.reactor.now)
        self.assertEqual(sent_frames(port)[0]["method"], "stop_feed_filament")

    def test_stop_in_flight_during_a_disconnect_is_remembered_too(self):
        self.build()
        self.ace._stop_feed(3)
        self.mgr._writer(self.reactor.now)             # on the wire, no reply yet
        self.mgr.disconnect()
        self.assertTrue(self.mgr.has_pending_stop({"method": "stop_feed_filament", "params": {"index": 3}}))

    def test_non_stop_requests_are_not_remembered(self):
        self.build()
        self.mgr.send_request({"method": "feed_filament", "params": {"index": 0}}, None)
        self.mgr.disconnect()
        self.assertEqual(self.mgr._undelivered_stops, [])

    def test_duplicate_stops_are_merged(self):
        self.build()
        self.disconnect_port()
        for _ in range(3):
            self.ace._stop_feed(1)
        self.assertEqual(len(self.mgr._undelivered_stops), 1)

    def test_no_resend_after_shutdown_or_when_ace_disabled_or_stale(self):
        self.build()
        self.ace._stop_feed(0)
        self.printer.send_event("klippy:shutdown")
        self.assertEqual(self.mgr._undelivered_stops, [])
        self.build()
        self.disconnect_port()
        self.ace._stop_feed(0)
        self.reactor.advance(self.mgr.PENDING_STOP_MAX_AGE_S + 1)
        self.mgr.connect("/dev/ttyACM0", 115200)
        port = FakeSerial.instances[-1]
        self.mgr._writer(self.reactor.now)
        self.assertEqual([f["method"] for f in sent_frames(port)], ["get_status"])   # stale: dropped
        self.build()
        self.mgr._ace_pro_enabled = False
        self.ace._stop_feed(0)  # dropped because ACE is disabled
        self.assertEqual(self.mgr._undelivered_stops, [])

    def test_stop_commands_defer_instead_of_refusing(self):
        self.build()
        self.disconnect_port()
        for fn, params in ((commands.cmd_ACE_STOP_DRYING, {}), (commands.cmd_ACE_STOP_DRYING, {"INSTANCE": 0}),
                           (commands.cmd_ACE_STOP_FEED, {"T": 0}), (commands.cmd_ACE_STOP_RETRACT, {"T": 0})):
            self.gcode.raw.clear()
            fn(FakeGcmd(self.gcode, params))
            self.assertEqual(len(self.gcode.raw), 1)
            self.assertIn("will be sent", self.gcode.raw[0])
        self.assertEqual(len(self.mgr._undelivered_stops), 3)   # drying_stop merged


# ---------------------------------------------------------------- PA-R05
class DiscardOnBudgetTests(Base):
    def test_queued_request_is_removed_when_the_caller_gives_up(self):
        self.build()
        cb = lambda response=None: None   # noqa: E731
        self.mgr.send_request({"method": "get_info"}, lambda response=None: None)
        self.mgr._writer(self.reactor.now)             # occupies the window
        self.mgr.send_request({"method": "feed_filament", "params": {"index": 0}}, cb)
        self.assertTrue(self.mgr.discard_request(cb))
        self.assertEqual(self.mgr._queue.qsize(), 0)

    def test_in_flight_request_is_dropped_and_its_late_reply_ignored(self):
        self.build()
        got = []
        cb = lambda response=None: got.append(response)   # noqa: E731
        self.mgr.send_request({"method": "feed_filament", "params": {"index": 0}}, cb)
        self.mgr._writer(self.reactor.now)
        rid = self.frames()[0]["id"]
        self.assertTrue(self.mgr.discard_request(cb))
        self.assertEqual(self.mgr.inflight, {})
        feed_reply(self.mgr, self.ser, rid)
        self.mgr._reader(self.reactor.now)
        self.assertEqual(got, [])
        self.assertEqual(self.mgr._comm_unsolicited_timestamps, [])

    def test_sync_waiter_cancels_its_request_when_the_budget_is_spent(self):
        self.build()
        self.reactor.on_pause = lambda w: None          # the link never answers, no pumping
        self.assertFalse(self.ace._change_feed_speed(0, 5))
        self.assertEqual(self.mgr._queue.qsize(), 0)     # not left behind to run later
        self.assertEqual(self.mgr.inflight, {})

    def test_feed_sync_cancels_too(self):
        self.build()
        self.ace._info["status"] = "ready"
        self.reactor.on_pause = lambda w: None
        resp = self.ace.feed_filament_with_wait_for_response(0, 10, 5)
        self.assertEqual(resp["code"], -1)
        self.assertEqual(self.mgr._queue.qsize(), 0)

    def test_budget_is_two_windows_even_for_start_commands(self):
        self.build({"serial_request_timeout": 0.5})
        # window = max(0.5*4, 4.0) = 4.0 -> 2*4+1
        self.assertEqual(self.ace._rpc_budget(1.0), 9.0)


# ---------------------------------------------------------------- PA-R06 / PA-R07
class QuietFailureTests(Base):
    def test_heartbeat_error_reported_once_per_streak(self):
        self.build()
        for _ in range(3):
            self.ace._on_heartbeat_response({"code": 7, "msg": "BROKEN"})
        self.assertEqual(len([m for m in self.gcode.raw if "Heartbeat response error" in m]), 1)
        self.ace._on_heartbeat_response({"code": 0, "result": status_result()})   # recovered
        self.ace._on_heartbeat_response({"code": 7, "msg": "BROKEN2"})
        self.assertEqual(len([m for m in self.gcode.raw if "Heartbeat response error" in m]), 2)

    def test_rfid_failure_reported_once_per_slot_until_success(self):
        self.build()
        for _ in range(3):
            self.ace._handle_rfid_info_response(1, {"code": 5, "msg": "READ_ERR"})
        self.assertEqual(len([m for m in self.gcode.raw if "get_filament_info failed" in m]), 1)
        self.ace._handle_rfid_info_response(2, {"code": 5, "msg": "READ_ERR"})
        self.assertEqual(len([m for m in self.gcode.raw if "get_filament_info failed" in m]), 2)
        self.ace._handle_rfid_info_response(1, {"code": 0, "result": {"rfid": 0}})   # success clears it
        self.ace._handle_rfid_info_response(1, {"code": 5, "msg": "READ_ERR"})
        self.assertEqual(len([m for m in self.gcode.raw if "get_filament_info failed" in m]), 3)

    def test_rfid_timeouts_do_not_count_towards_link_health(self):
        self.build()
        counted = []
        real = self.mgr._track_comm_timeout
        self.mgr._track_comm_timeout = lambda: (counted.append(1), real())
        for slot in range(2):
            self.mgr.send_request({"method": "get_filament_info", "params": {"index": slot}},
                                  lambda response=None: None)
            self.mgr._writer(self.reactor.now)
            for _ in range(4):
                self.reactor.advance(1.05)
                self.mgr._writer(self.reactor.now)
        self.assertEqual(counted, [])
        self.assertTrue(self.mgr.is_connected())          # no supervision reconnect
        self.assertTrue(self.mgr._check_communication_health()[0])

    def test_other_timeouts_still_count(self):
        self.build()
        self.mgr.send_request({"method": "get_status"}, lambda response=None: None)
        self.mgr._writer(self.reactor.now)
        for _ in range(4):
            self.reactor.advance(1.05)
            self.mgr._writer(self.reactor.now)
        self.assertEqual(len(self.mgr._comm_timeout_timestamps), 4)


# ---------------------------------------------------------------- PA-R08
class PumpUndeliveredTests(Base):
    def test_request_the_frame_writer_could_not_deliver_does_not_block_the_window(self):
        self.build()
        got = []
        self.mgr.send_request({"method": "get_info"}, lambda response=None: got.append(response))
        # port looked open for _pump, but _send_frame cannot deliver (vanished in between)
        self.mgr._send_frame = lambda req: False
        self.mgr._pump(self.reactor.now)
        self.assertEqual(got, [None])
        self.assertEqual(self.mgr.inflight, {})
        self.assertEqual(self.mgr._inflight_meta, {})
        self.assertEqual(self.mgr._callback_map, {})

    def test_callback_already_called_by_the_write_error_path_is_not_called_twice(self):
        self.build()
        got = []
        self.ser.write_error = sm_mod.SerialException("gone")
        self.mgr.send_request({"method": "get_info"}, lambda response=None: got.append(response))
        self.mgr._pump(self.reactor.now)
        self.assertEqual(got, [None])

    def test_fake_register_callback_is_deferred_by_default(self):
        r = FakeReactor()
        seen = []
        r.register_callback(lambda e: seen.append(1))
        self.assertEqual(seen, [])
        r.run_callbacks()
        self.assertEqual(seen, [1])


# ---------------------------------------------------------------- PA-R04
class FanSpeedDefaultTests(Base):
    def test_default_drying_request_has_no_fan_speed(self):
        self.build()
        self.ace.start_drying(50, 60)
        self.mgr._writer(self.reactor.now)
        self.assertEqual(self.frames()[0]["params"], {"temp": 50, "duration": 60})

    def test_explicit_parameter_and_config_still_work(self):
        self.build()
        self.ace.start_drying(50, 60, fan_speed=6500)
        self.mgr._writer(self.reactor.now)
        self.assertEqual(self.frames()[0]["params"]["fan_speed"], 6500)
        self.build({"dryer_fan_speed": 7000})
        self.ace.start_drying(50, 60)
        self.mgr._writer(self.reactor.now)
        self.assertEqual(self.frames()[0]["params"]["fan_speed"], 7000)

    def test_set_fan_speed_command_unchanged(self):
        self.build()
        commands.cmd_ACE_SET_FAN_SPEED(FakeGcmd(self.gcode, {"SPEED": 4200}))
        self.mgr._writer(self.reactor.now)
        self.assertEqual(self.frames()[0]["params"], {"fan_speed": 4200})


# ---------------------------------------------------------------- PA-R11
class FrameLengthLimitTests(unittest.TestCase):
    def setUp(self):
        self.p = AceJsonProtocolAdapter()
        self.mgr = AceSerialManager(FakeGcode(), FakeReactor(), 0)

    def test_limit_is_4096(self):
        self.assertEqual(AceJsonProtocolAdapter.MAX_PAYLOAD_LEN, 4096)

    def test_length_above_limit_resyncs_to_the_next_frame(self):
        good = frame_for(self.p, self.mgr, {"id": 9, "code": 0})
        bad = b"\xFF\xAA" + struct.pack("<H", 4097) + b"x" * 10
        res, rest, notices = self.p.extract_responses(bytearray(bad + good), self.mgr._calc_crc)
        self.assertEqual([r["id"] for r in res], [9])
        self.assertTrue(any("Invalid frame length (4097)" in n for n in notices))

    def test_length_at_limit_is_still_waited_for(self):
        buf = bytearray(b"\xFF\xAA" + struct.pack("<H", 4096) + b"x" * 10)
        res, rest, notices = self.p.extract_responses(buf, self.mgr._calc_crc)
        self.assertEqual(res, [])
        self.assertEqual(len(rest), len(buf))     # incomplete frame kept, not discarded


if __name__ == "__main__":
    unittest.main()
