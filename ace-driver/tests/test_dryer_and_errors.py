"""P0 dryer flag, urgent stop, fan speed, and the !!/// message contract."""

import unittest

import _fakes
from _fakes import (FakeGcmd, feed_reply, make_manager, open_fake_port,
                    run_io, sent_frames)

from ace import commands
from ace.reporting import emit_error, format_error_line, is_cancelled_response


class Harness(unittest.TestCase):
    def setUp(self):
        self.printer, self.manager = make_manager()
        self.ace = self.manager.instances[0]
        self.mgr = self.ace.serial_mgr
        self.reactor = self.printer.reactor
        self.gcode = self.printer.gcode
        self.ser = open_fake_port(self.mgr)

    def cmd(self, fn, **params):
        fn(FakeGcmd(self.gcode, params))

    def answer_last(self, code=0, msg="OK"):
        """Run IO so the newest request goes out, then answer it."""
        self.mgr._writer(self.reactor.now)
        rid = sent_frames(self.ser)[-1]["id"]
        feed_reply(self.mgr, self.ser, rid, code=code, msg=msg)
        self.mgr._reader(self.reactor.now)

    def count_info(self, needle):
        return sum(1 for m in self.gcode.info if needle in m)


class DryerFlagTests(Harness):
    def test_start_reported_every_time_across_stop_cycles(self):
        # Regression for docs/30 6.1 / docs/31 B-01: after the first start the
        # "Dryer started" message vanished because a flag was never reset by a
        # parameterless ACE_STOP_DRYING.
        for cycle in range(3):
            self.cmd(commands.cmd_ACE_START_DRYING, TEMP=50, DURATION=60)
            self.answer_last()
            self.assertEqual(self.count_info("Dryer started: 50°C for 60min"), cycle + 1)
            self.cmd(commands.cmd_ACE_STOP_DRYING)  # no INSTANCE= (what the screen sends)
            self.answer_last()
            self.assertEqual(self.count_info("Dryer stopped"), cycle + 1)

    def test_second_start_without_stop_is_also_reported(self):
        self.cmd(commands.cmd_ACE_START_DRYING, TEMP=45, DURATION=30)
        self.answer_last()
        self.cmd(commands.cmd_ACE_START_DRYING, TEMP=55, DURATION=30)
        self.answer_last()
        self.assertEqual(self.count_info("Dryer started"), 2)

    def test_start_with_instance_param(self):
        self.cmd(commands.cmd_ACE_START_DRYING, INSTANCE=0, TEMP=50, DURATION=10)
        self.answer_last()
        self.assertEqual(self.count_info("Dryer started"), 1)

    def test_flag_attribute_is_gone(self):
        self.assertFalse(hasattr(self.ace, "_dryer_start_logged"))


class DryerFrameTests(Harness):
    def test_no_fan_speed_field_by_default(self):
        self.cmd(commands.cmd_ACE_START_DRYING, TEMP=50, DURATION=60)
        self.mgr._writer(self.reactor.now)
        req = sent_frames(self.ser)[0]
        self.assertEqual(req["method"], "drying")
        self.assertEqual(req["params"], {"temp": 50, "duration": 60})

    def test_explicit_fan_speed_goes_out_in_wire_order(self):
        self.cmd(commands.cmd_ACE_START_DRYING, TEMP=50, DURATION=60, FAN_SPEED=7000)
        self.mgr._writer(self.reactor.now)
        req = sent_frames(self.ser)[0]
        self.assertEqual(req["params"], {"temp": 50, "fan_speed": 7000, "duration": 60})
        self.assertEqual(list(req["params"]), ["temp", "fan_speed", "duration"])

    def test_fan_speed_command_parameter(self):
        self.cmd(commands.cmd_ACE_START_DRYING, TEMP=50, DURATION=60, FAN_SPEED=5200)
        self.mgr._writer(self.reactor.now)
        self.assertEqual(sent_frames(self.ser)[0]["params"]["fan_speed"], 5200)

    def test_fan_speed_zero_omits_field(self):
        self.cmd(commands.cmd_ACE_START_DRYING, TEMP=50, DURATION=60, FAN_SPEED=0)
        self.mgr._writer(self.reactor.now)
        self.assertNotIn("fan_speed", sent_frames(self.ser)[0]["params"])

    def test_invalid_fan_speed_is_an_error_line(self):
        self.cmd(commands.cmd_ACE_START_DRYING, TEMP=50, DURATION=60, FAN_SPEED=-5)
        self.assertTrue(any(m.startswith("!! ACE_START_DRYING error") for m in self.gcode.raw))
        self.assertEqual(self.mgr._queue.qsize(), 0)

    def test_set_fan_speed_command(self):
        self.cmd(commands.cmd_ACE_SET_FAN_SPEED, SPEED=4500)
        self.answer_last()
        req = sent_frames(self.ser)[0]
        self.assertEqual(req, {"method": "set_fan_speed", "params": {"fan_speed": 4500}, "id": req["id"]})
        self.assertEqual(self.count_info("Fan speed set to 4500"), 1)

    def test_set_fan_speed_requires_speed(self):
        self.cmd(commands.cmd_ACE_SET_FAN_SPEED)
        self.assertTrue(any(m.startswith("!! ACE_SET_FAN_SPEED error") for m in self.gcode.raw))

    def test_set_fan_speed_device_error_is_error_line(self):
        self.cmd(commands.cmd_ACE_SET_FAN_SPEED, SPEED=4500, INSTANCE=0)
        self.answer_last(code=1, msg="BAD")
        self.assertTrue(any("Fan speed change failed: BAD" in m and m.startswith("!!")
                            for m in self.gcode.raw))

    def test_configured_default_fan_speed(self):
        printer, manager = make_manager({"dryer_fan_speed": 6100})
        ace = manager.instances[0]
        ser = open_fake_port(ace.serial_mgr)
        ace.start_drying(50, 60)
        ace.serial_mgr._writer(printer.reactor.now)
        self.assertEqual(sent_frames(ser)[0]["params"]["fan_speed"], 6100)


class UrgentStopTests(Harness):
    def test_stop_drying_overtakes_queued_requests(self):
        # one request is in flight, others wait; stop must be next on the wire
        self.mgr.send_request({"method": "get_filament_info", "params": {"index": 0}}, lambda response=None: None)
        self.mgr._writer(self.reactor.now)
        self.mgr.send_request({"method": "get_filament_info", "params": {"index": 1}}, lambda response=None: None)
        self.mgr._send_heartbeat_request()
        self.cmd(commands.cmd_ACE_STOP_DRYING)
        rid = sent_frames(self.ser)[0]["id"]
        feed_reply(self.mgr, self.ser, rid)
        self.mgr._reader(self.reactor.now)
        self.assertEqual([f["method"] for f in sent_frames(self.ser)],
                         ["get_filament_info", "drying_stop"])

    def test_stop_cancels_unsent_start_so_it_cannot_restart_drying(self):
        # window is busy, so the start request is still queued when stop arrives
        self.mgr.send_request({"method": "get_filament_info", "params": {"index": 0}}, lambda response=None: None)
        self.mgr._writer(self.reactor.now)
        self.cmd(commands.cmd_ACE_START_DRYING, TEMP=50, DURATION=60)
        self.cmd(commands.cmd_ACE_STOP_DRYING)
        self.assertEqual(self.count_info("Dryer start cancelled"), 1)
        self.assertEqual(self.gcode.raw, [])  # a cancel is not an error
        for _ in range(4):
            rid = sent_frames(self.ser)[-1]["id"]
            feed_reply(self.mgr, self.ser, rid)
            self.mgr._reader(self.reactor.now)
        methods = [f["method"] for f in sent_frames(self.ser)]
        self.assertNotIn("drying", methods)
        self.assertIn("drying_stop", methods)

    def test_stop_feed_and_stop_retract_and_feed_assist_use_urgent_queue(self):
        self.mgr.send_request({"method": "x"}, lambda response=None: None)
        self.mgr._writer(self.reactor.now)  # occupy window
        self.ace._stop_feed(1)
        self.ace._stop_retract(2)
        self.assertEqual(self.mgr._urgent_queue.qsize(), 2)
        self.ace._feed_assist_index = 3
        self.ace.wait_ready = lambda *a, **k: None
        self.ace.dwell = lambda *a, **k: None
        self.ace._disable_feed_assist(3)
        self.assertEqual(self.mgr._urgent_queue.qsize(), 3)
        methods = [q[0]["method"] for q in list(self.mgr._urgent_queue.queue)]
        self.assertEqual(methods, ["stop_feed_filament", "stop_unwind_filament", "stop_feed_assist"])


class ErrorContractTests(Harness):
    def test_dryer_start_failure_is_a_raw_bang_line_not_an_info_line(self):
        self.cmd(commands.cmd_ACE_START_DRYING, TEMP=50, DURATION=60)
        self.answer_last(code=1, msg="FORBIDDEN")
        self.assertEqual(self.gcode.raw, ["!! ACE[0]: Dryer start failed: FORBIDDEN"])
        self.assertFalse(any("!!" in m for m in self.gcode.info))
        wire = self.gcode.wire()
        self.assertTrue(any(w.startswith("!!") for w in wire))
        self.assertFalse(any(w.startswith("// !!") for w in wire))

    def test_dryer_start_timeout_is_error_line(self):
        self.cmd(commands.cmd_ACE_START_DRYING, TEMP=50, DURATION=60)
        self.mgr._writer(self.reactor.now)
        for _ in range(4):
            self.reactor.advance(1.05)
            self.mgr._writer(self.reactor.now)
        self.assertEqual(len(self.gcode.raw), 1)
        self.assertTrue(self.gcode.raw[0].startswith("!! ACE[0]: Dryer start failed: no response"))

    def test_dryer_stop_failure_is_error_line(self):
        self.cmd(commands.cmd_ACE_STOP_DRYING)
        self.answer_last(code=2, msg="NOPE")
        self.assertEqual(self.gcode.raw, ["!! ACE[0]: Dryer stop failed: NOPE"])

    def test_disabled_ace_start_is_error_line(self):
        self.manager._ace_pro_enabled = False
        self.cmd(commands.cmd_ACE_START_DRYING, TEMP=50, DURATION=60)
        self.assertEqual(len(self.gcode.raw), 1)
        self.assertIn("ACE Pro is disabled", self.gcode.raw[0])
        self.assertTrue(self.gcode.raw[0].startswith("!!"))

    def test_temp_out_of_range_is_error_line(self):
        self.cmd(commands.cmd_ACE_START_DRYING, INSTANCE=0, TEMP=500, DURATION=60)
        self.assertTrue(self.gcode.raw[0].startswith("!! ACE_START_DRYING error: TEMP must be between"))

    def test_temp_out_of_range_all_instances_is_error_line(self):
        self.cmd(commands.cmd_ACE_START_DRYING, TEMP=500, DURATION=60)
        self.assertEqual(self.gcode.raw, ["!! ACE[0]: Temp 500°C out of range, skipping"])
        self.assertEqual(self.mgr._queue.qsize(), 0)

    def test_command_exception_is_error_line(self):
        self.cmd(commands.cmd_ACE_FEED, T=0, LENGTH=-5)  # LENGTH must be positive
        self.assertTrue(self.gcode.raw[0].startswith("!! ACE_FEED error: LENGTH must be positive"))

    def test_safe_wrapper_emits_error_line(self):
        def boom(gcmd):
            raise RuntimeError("kaput")
        commands.safe_gcode_command(boom)(FakeGcmd(self.gcode, {}))
        self.assertEqual(self.gcode.raw[0], "!! ACE ERROR: RuntimeError: kaput")

    def test_success_messages_stay_info(self):
        self.cmd(commands.cmd_ACE_START_DRYING, TEMP=50, DURATION=60)
        self.answer_last()
        self.assertEqual(self.gcode.raw, [])
        self.assertIn("ACE[0]: Dryer started: 50°C for 60min", self.gcode.info)

    def test_stop_feed_error_paths(self):
        self.ace._stop_feed(0)
        self.answer_last(code=3, msg="E")
        self.assertEqual(self.gcode.raw, ["!! ACE[0]: Stop feed error: E"])

    def test_feed_error_paths(self):
        self.ace._feed(0, 10, 5)
        self.answer_last(code=1, msg="FORBIDDEN")
        self.assertTrue(self.gcode.raw[0].startswith("!! ACE[0]: Feed forbidden"))

    def test_heartbeat_error_response_is_error_line(self):
        self.ace._on_heartbeat_response({"code": 7, "msg": "BROKEN"})
        self.assertEqual(self.gcode.raw, ["!! ACE[0]: Heartbeat response error: BROKEN"])


class ReportingUnitTests(unittest.TestCase):
    def test_emit_error_uses_raw_channel(self):
        g = _fakes.FakeGcode()
        emit_error(g, "ACE[0]: bad")
        self.assertEqual(g.raw, ["!! ACE[0]: bad"])
        self.assertEqual(g.info, [])

    def test_prefix_not_doubled(self):
        self.assertEqual(format_error_line("!! x"), "!! x")
        self.assertEqual(format_error_line("!!x"), "!! x")

    def test_multiline_detail_goes_to_info_first_line_to_bang(self):
        g = _fakes.FakeGcode()
        emit_error(g, "ACE: summary\ndetail 1\ndetail 2")
        self.assertEqual(g.raw, ["!! ACE: summary"])
        self.assertEqual(g.info, ["detail 1\ndetail 2"])

    def test_sink_without_respond_raw_degrades_to_info(self):
        class OnlyInfo:
            def __init__(self):
                self.info = []

            def respond_info(self, m):
                self.info.append(m)
        s = OnlyInfo()
        emit_error(s, "x")
        self.assertEqual(s.info, ["!! x"])

    def test_cancelled_response_detection(self):
        self.assertTrue(is_cancelled_response({"code": -2, "msg": "CANCELLED"}))
        self.assertFalse(is_cancelled_response({"code": 0, "msg": "OK"}))
        self.assertFalse(is_cancelled_response(None))


if __name__ == "__main__":
    unittest.main()
