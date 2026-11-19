import copy
import os
import sys
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(__file__)
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

import _fakes  # noqa: E402
from ace import ace2_bus  # noqa: E402
from ace import commands as cmd_mod  # noqa: E402
from ace import config as ace_config  # noqa: E402
from ace import moonraker_lane_sync as lane_sync  # noqa: E402
from ace import protocol_ace1 as p1  # noqa: E402
from ace import protocol_ace2 as p2  # noqa: E402
from ace import reporting  # noqa: E402
from ace import serial_manager as sm_mod  # noqa: E402
import temperature_ace  # noqa: E402


def _make_serial_manager(protocol=None, connected=True):
    printer = _fakes.FakePrinter()
    if protocol is None:
        protocol = p1.AceJsonProtocolAdapter()
    sm = sm_mod.AceSerialManager(printer.gcode, printer.reactor, 0, protocol=protocol)
    ser = _fakes.FakeSerial() if connected else None
    if connected:
        sm._serial = ser
        sm._connected = True
    return printer, sm, ser


def _ace2_response_frame(rid, cmd_code, payload_bytes, crc_calc, flags=0x03):
    """Build a valid ACE2 response frame with Flags.REQUEST=1|RESPONSE=2."""
    header = bytes(
        [
            0xFF,
            0xAA,
            flags,
            rid & 0xFF,
            (rid >> 8) & 0xFF,
            cmd_code & 0xFF,
            len(payload_bytes) & 0xFF,
        ]
    )
    crc = crc_calc(header[2:] + payload_bytes)
    return header + payload_bytes + bytes([crc & 0xFF, (crc >> 8) & 0xFF, 0xFE])


def _encode_varint(value):
    out = bytearray()
    v = int(value)
    while v >= 0x80:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v)
    return bytes(out)


def _pb_varint(field_num, value):
    tag = (field_num << 3) | 0
    return _encode_varint(tag) + _encode_varint(value)


def _pb_bytes(field_num, data):
    tag = (field_num << 3) | 2
    return _encode_varint(tag) + _encode_varint(len(data)) + data


class Rev08DryerStopAndErrorTests(unittest.TestCase):
    def test_urgent_stop_drying_wakes_writer_and_cancels_queued_start_without_fan(self):
        printer, sm, _ser = _make_serial_manager()
        writer_timer = printer.reactor.register_timer(lambda et: et + 0.25, printer.reactor.NEVER)
        sm.writer_timer = writer_timer

        sm.send_request({"method": "drying", "params": {"temp": 55, "duration": 60}}, lambda response=None: None)
        self.assertEqual(sm._queue.qsize(), 1)

        seen_replies = []
        sm.send_urgent_request({"method": "drying_stop"}, lambda response=None: seen_replies.append(response))

        self.assertEqual(sm._queue.qsize(), 0)
        self.assertEqual(sm._urgent_queue.qsize(), 1)
        self.assertEqual(printer.reactor.timers[writer_timer][1], printer.reactor.NOW)

        all_queued = list(sm._urgent_queue.queue) + list(sm._queue.queue)
        methods = [sm._method_of(req) for req, _cb in all_queued]
        self.assertNotIn("set_fan_speed", methods)

    def test_ace_stop_drying_command_never_sends_set_fan_speed(self):
        printer, mgr = _fakes.make_manager()
        ace = mgr.instances[0]
        sent = []

        def fake_urgent(req, cb):
            sent.append(dict(req))
            if cb:
                cb({"code": 0, "msg": "OK", "result": {}})

        ace.send_urgent_request = fake_urgent
        ace.serial_mgr.send_urgent_request = fake_urgent
        ace._info["dryer_status"] = {"status": "drying", "target_temp": 55}

        gcmd = _fakes.FakeGcmd(printer.gcode, {"INSTANCE": 0})
        cmd_mod.cmd_ACE_STOP_DRYING(gcmd)

        methods = [req.get("method") or req.get("command") for req in sent]
        self.assertIn("drying_stop", methods)
        self.assertNotIn("set_fan_speed", methods)
        self.assertTrue(any("Dryer stopped" in line for line in printer.gcode.info))

    def test_ace2_drying_stop_cancels_queued_drying_and_maps_status_code_3_and_4(self):
        self.assertEqual(p2.ACE2_DRY_STATUS_BY_CODE[3], "stopping")
        self.assertEqual(p2.ACE2_DRY_STATUS_BY_CODE[4], "error")
        self.assertEqual(p2.ACE2_DRY_STATUS_BY_CODE[5], "error")

        proto = p2.AceProtoProtocolAdapter()
        printer, sm, _ser = _make_serial_manager(protocol=proto)

        sm.send_request(
            {"command": "DRYING", "target_device_id": 1, "params": {"temp": 55, "duration": 60}},
            lambda response=None: None,
        )
        self.assertEqual(sm._queue.qsize(), 1)

        stop_req = {"command": "DRYING", "target_device_id": 1, "params": {"temp": 0, "duration": 0}}
        self.assertTrue(sm._is_stop(stop_req))
        self.assertFalse(sm._is_start(stop_req))
        sm.send_urgent_request(stop_req, lambda response=None: None)
        self.assertEqual(sm._queue.qsize(), 0)
        self.assertEqual(sm._urgent_queue.qsize(), 1)

        cmd_code = p2.ACE2_COMMANDS_BY_NAME["GET_STATUS"].code
        for code_val, expected_status, expected_detail in [
            (3, "stopping", "stopping"),
            (4, "error", "ptc_error"),
        ]:
            dry_msg = _pb_varint(1, code_val) + _pb_varint(2, 55) + _pb_varint(3, 60) + _pb_varint(4, 30)
            status_msg = _pb_varint(1, 0) + _pb_bytes(2, dry_msg)
            frame = _ace2_response_frame(1, cmd_code, status_msg, sm._calc_crc)
            buf = bytearray(frame)
            responses, remaining, _notices = proto.extract_responses(buf, sm._calc_crc)
            self.assertEqual(len(remaining), 0)
            self.assertEqual(len(responses), 1)
            dryer = responses[0]["result"]["dryer_status"]
            self.assertEqual(dryer["status"], expected_status)
            self.assertEqual(dryer["state_detail"], expected_detail)

    def test_dryer_error_reported_once_exposed_in_get_status_and_clears_on_normal(self):
        printer, mgr = _fakes.make_manager()
        ace = mgr.instances[0]
        reported = []
        ace._report_error = lambda msg: reported.append(msg)

        ace._status_update_callback(
            {
                "code": 0,
                "result": {
                    "status": "ready",
                    "dryer_status": {"status": "NTC_err", "target_temp": 0, "remain_time": 0},
                    "slots": [],
                },
            }
        )
        self.assertEqual(ace.get_status()["dryer_error"], "NTC_err")
        self.assertEqual(len(reported), 1)
        self.assertIn("NTC_err", reported[0])

        # Identical subsequent heartbeat should not duplicate warning
        ace._status_update_callback(
            {
                "code": 0,
                "result": {
                    "status": "ready",
                    "dryer_status": {"status": "NTC_err", "target_temp": 0, "remain_time": 0},
                    "slots": [],
                },
            }
        )
        self.assertEqual(len(reported), 1)

        # ACE2 error with state_detail
        ace._status_update_callback(
            {
                "code": 0,
                "result": {
                    "status": "ready",
                    "dryer_status": {
                        "status": "error",
                        "state_detail": "ptc_error",
                        "target_temp": 0,
                        "remain_time": 0,
                    },
                    "slots": [],
                },
            }
        )
        self.assertEqual(ace.get_status()["dryer_error"], "ptc_error")
        self.assertEqual(len(reported), 2)
        self.assertIn("ptc_error", reported[1])

        # Recovery clears dryer_error
        ace._status_update_callback(
            {
                "code": 0,
                "result": {
                    "status": "ready",
                    "dryer_status": {"status": "stopping", "target_temp": 0, "remain_time": 0},
                    "slots": [],
                },
            }
        )
        self.assertIsNone(ace.get_status()["dryer_error"])


class Rev12Ace2ProtocolAndSharedBusTests(unittest.TestCase):
    def test_serial_manager_command_aliases_target_device_cancel_and_no_health(self):
        proto = p2.AceProtoProtocolAdapter()
        _printer, sm, _ser = _make_serial_manager(protocol=proto)

        self.assertEqual(sm._method_of({"command": "FEED_OR_ROLLBACK"}), "FEED_OR_ROLLBACK")
        self.assertTrue(sm._is_start({"command": "FEED_OR_ROLLBACK"}))
        self.assertTrue(sm._is_start({"command": "UPDATE_SPEED"}))
        self.assertIn("GET_FILAMENT_INFO", sm.NO_HEALTH_METHODS)
        self.assertEqual(
            sm._cancels_for({"command": "STOP_FEED_OR_ROLLBACK"}),
            ("FEED_OR_ROLLBACK", "UPDATE_SPEED"),
        )

        # Canceling on target_device_id=1 must NOT drop queued command for target_device_id=2
        sm.send_request(
            {"command": "FEED_OR_ROLLBACK", "target_device_id": 1, "params": {"slots": [0]}},
            lambda response=None: None,
        )
        sm.send_request(
            {"command": "FEED_OR_ROLLBACK", "target_device_id": 2, "params": {"slots": [0]}},
            lambda response=None: None,
        )
        sm.send_urgent_request(
            {"command": "STOP_FEED_OR_ROLLBACK", "target_device_id": 1, "params": {"slots": [0]}},
            lambda response=None: None,
        )
        queued_targets = [req.get("target_device_id") for req, _ in list(sm._queue.queue)]
        self.assertEqual(queued_targets, [2])

    def test_ace2_frame_payload_limit_and_pb_decode_bounds(self):
        proto = p2.AceProtoProtocolAdapter()
        _printer, sm, _ser = _make_serial_manager(protocol=proto)

        # Oversized payload (> 255 bytes) in serialize_request_frame raises ValueError
        req = {
            "id": 1,
            "command": "SET_RFID_ENABLE",
            "params": {"index": 0, "enable": True},
        }
        with mock.patch.object(proto, "_encode_request_payload", return_value=b"X" * 256):
            with self.assertRaises(ValueError):
                proto.serialize_request_frame(req, sm._calc_crc)

        # Truncated wire_type=2 length must not crash or read past buffer
        truncated = bytes([(1 << 3) | 2, 50, 0x41, 0x42])
        decoded = p2._pb_decode(truncated)
        self.assertEqual(decoded, {})

    def test_shared_bus_on_connected_defers_from_timer_and_keeps_offline_device_unready(self):
        printer, mgr = _fakes.make_manager({"ace_count": 2})
        inst0, inst1 = mgr.instances[0], mgr.instances[1]

        bus = ace2_bus.Ace2BusSession(port="/dev/ttyACM0", baud=115200)
        inst0.bus_session = bus
        inst1.bus_session = bus
        inst0.serial_mgr._serial = _fakes.FakeSerial()
        inst0.serial_mgr._connected = True
        inst0.serial_mgr._port = "/dev/ttyACM0"
        bus.discovered = False

        def fake_init(lead_instance):
            # Trigger reactor.pause inside init to prove it does NOT execute inside timer
            printer.reactor.pause(printer.reactor.now + 0.01)
            lead_instance.bus_session.discovered = True
            return 1

        mgr._initialize_shared_bus_transport = fake_init
        mgr._start_shared_bus_runtime = lambda _b: None
        mgr._queue_shared_bus_instance_setup = lambda _b: None

        timer_handle = printer.reactor.register_timer(
            lambda et: (mgr._on_shared_bus_connected(bus), printer.reactor.NEVER)[1],
            printer.reactor.now,
        )
        printer.reactor.fire(timer_handle)
        self.assertFalse(bus.discovered)
        printer.reactor.run_callbacks()
        self.assertTrue(bus.discovered)

        # Test _initialize_shared_bus_transport keeps persisted-but-offline UID unready
        printer2, mgr2 = _fakes.make_manager({"ace_count": 2})
        d0, d1 = mgr2.instances[0], mgr2.instances[1]
        bus2 = ace2_bus.Ace2BusSession(port="/dev/ttyACM0", baud=115200)
        d0.bus_session = bus2
        d1.bus_session = bus2
        d0.serial_mgr.protocol = p2.AceProtoProtocolAdapter()
        d1.serial_mgr.protocol = p2.AceProtoProtocolAdapter()
        d0.serial_mgr._connected = True

        def fake_load_bindings(bs, _insts):
            bs.bind_logical_instance(0, 101, 102, 103)
            bs.bind_logical_instance(1, 201, 202, 203)

        mgr2._load_shared_bus_bindings = fake_load_bindings
        mgr2._persist_shared_bus_bindings = lambda _bs, _insts: None

        discovered_once = {"sent": False}

        def fake_send_shared(_instance, req, timeout_s=5.0):
            cmd = req.get("command")
            if cmd == "DISCOVER_DEVICE":
                if discovered_once["sent"]:
                    return None
                discovered_once["sent"] = True
                return {"code": 0, "result": {"uid1": 101, "uid2": 102, "uid3": 103}}
            if cmd == "ASSIGN_DEVICE_ID":
                return {"code": 0, "msg": "OK"}
            return {"code": 0}

        mgr2._send_shared_bus_request = fake_send_shared
        ready_count = mgr2._initialize_shared_bus_transport(d0)
        self.assertEqual(ready_count, 1)
        dev0 = bus2.get_device_for_instance(0)
        dev1 = bus2.get_device_for_instance(1)
        self.assertEqual(dev0.device_id, 1)
        self.assertIsNone(dev1.device_id)
        self.assertEqual([i.instance_num for i in mgr2._get_shared_bus_ready_instances(bus2)], [0])

        # Failed ASSIGN_DEVICE_ID (code=3) must clear device_id and return 0 ready instances
        discovered_once["sent"] = False
        mgr2._send_shared_bus_request = lambda _inst, req, timeout_s=5.0: (
            {"code": 0, "result": {"uid1": 101, "uid2": 102, "uid3": 103}}
            if (req.get("command") == "DISCOVER_DEVICE" and not discovered_once.update(sent=True))
            else ({"code": 3, "msg": "FAIL"} if req.get("command") == "ASSIGN_DEVICE_ID" else None)
        )
        # Re-test cleanly with explicit helper
        discovered_flag = {"count": 0}

        def fail_assign(_instance, req, timeout_s=5.0):
            if req.get("command") == "DISCOVER_DEVICE":
                discovered_flag["count"] += 1
                if discovered_flag["count"] == 1:
                    return {"code": 0, "result": {"uid1": 101, "uid2": 102, "uid3": 103}}
                return None
            if req.get("command") == "ASSIGN_DEVICE_ID":
                return {"code": 3, "msg": "ERROR"}
            return None

        mgr2._send_shared_bus_request = fail_assign
        ready_after_fail = mgr2._initialize_shared_bus_transport(d0)
        self.assertEqual(ready_after_fail, 0)
        self.assertIsNone(bus2.get_device_for_instance(0).device_id)


class Rev10TemperatureAceTests(unittest.TestCase):
    def test_explicit_70c_max_temp_does_not_shutdown_when_dryer_max_is_80c(self):
        shutdown_reasons = []
        printer = _fakes.FakePrinter()
        printer.invoke_shutdown = shutdown_reasons.append
        printer.add_object("mcu", mock.MagicMock())
        ace = mock.MagicMock()
        ace.max_dryer_temperature = 80
        ace._info = {"temp": 25.0}
        ace._info_time = printer.reactor.now
        ace.serial_mgr = mock.MagicMock()
        ace.serial_mgr.is_connected.return_value = True
        mgr = mock.MagicMock()
        mgr.instances = [ace]
        printer.add_object("ace", mgr)

        class ConfigStub:
            def get_printer(self):
                return printer

            def get_name(self):
                return "temperature_sensor ace_temp"

            def getint(self, name, default=None, minval=None):
                if name == "ace_instance":
                    return 0
                if name == "report_interval":
                    return 1
                return default

            def get(self, name, default=None):
                if name == "max_temp":
                    return "70"
                return default

        sensor = temperature_ace.TemperatureACE(ConfigStub())
        # Klipper calls setup_minmax(0, 70) BEFORE klippy:ready
        sensor.setup_minmax(0.0, 70.0)
        printer.send_event("klippy:ready")
        # Also safe if setup_minmax is called again after ready
        sensor.setup_minmax(0.0, 70.0)

        self.assertEqual(sensor.max_temp, 90.0)
        # 5 samples at 71C..80C must NOT trigger shutdown
        for temp in [71.0, 75.0, 80.0, 80.0, 79.0]:
            ace._info["temp"] = temp
            ace._info_time = printer.reactor.now
            sensor._sample_ace_temperature(printer.reactor.now)
        self.assertEqual(shutdown_reasons, [])

        # Over-temp (> 90C for 3 consecutive samples) DOES trigger shutdown
        for temp in [91.0, 91.0, 91.0]:
            ace._info["temp"] = temp
            ace._info_time = printer.reactor.now
            sensor._sample_ace_temperature(printer.reactor.now)
        self.assertEqual(len(shutdown_reasons), 1)


class Rev13PreToolchangeStateTests(unittest.TestCase):
    def test_early_toolchange_failure_does_not_restore_purge_state_or_fan(self):
        printer, mgr = _fakes.make_manager(print_state="printing")
        ace = mgr.instances[0]
        ace._info["slots"][0]["status"] = "ready"
        ace._info["slots"][1]["status"] = "ready"
        mgr.state.set("ace_current_index", 0)
        mgr.state.set("ace_filament_pos", "nozzle")
        printer.save_variables.allVariables = {
            "ace_current_index": 0,
            "ace_filament_pos": "nozzle",
            "ace_toolchange_fan_speed": 200,
        }
        printer.objects["fan"] = type("FakeFan", (), {"get_status": lambda self, et: {"speed": 0.25}})()

        mgr.smart_unload = lambda *args, **kwargs: False

        gcmd = _fakes.FakeGcmd(printer.gcode, {"TOOL": 1})
        cmd_mod.cmd_ACE_CHANGE_TOOL_WRAPPER(gcmd)

        self.assertFalse(mgr._pre_toolchange_done)
        self.assertIn("PAUSE", printer.gcode.scripts)
        self.assertFalse(
            any("RESTORE_GCODE_STATE" in s and "purge_state" in s for s in printer.gcode.scripts)
        )
        self.assertFalse(any(s.startswith("M106") for s in printer.gcode.scripts))
        self.assertFalse(mgr.restore_pre_toolchange_state())


class Rev14SpeedSyncTests(unittest.TestCase):
    def test_multi_ace_speed_config_parsing_and_runtime_sync(self):
        printer, mgr = _fakes.make_manager(
            {
                "ace_count": 2,
                "feed_speed": "60,1:40",
                "retract_speed": "50,1:35",
            }
        )
        ace0, ace1 = mgr.instances[0], mgr.instances[1]

        mgr_status = mgr.get_status()
        self.assertEqual(mgr_status["feed_speed"], 60.0)
        self.assertEqual(mgr_status["retract_speed"], 50.0)
        self.assertEqual(ace0.get_status()["feed_speed"], 60.0)
        self.assertEqual(ace1.get_status()["feed_speed"], 40.0)
        self.assertEqual(ace0.get_status()["retract_speed"], 50.0)
        self.assertEqual(ace1.get_status()["retract_speed"], 35.0)

        ace1._change_feed_speed = lambda slot, speed: True
        ace0._change_retract_speed = lambda slot, speed: True

        cmd_mod.cmd_ACE_SET_FEED_SPEED(
            _fakes.FakeGcmd(printer.gcode, {"INSTANCE": 1, "INDEX": 0, "SPEED": 72})
        )
        cmd_mod.cmd_ACE_SET_RETRACT_SPEED(
            _fakes.FakeGcmd(printer.gcode, {"INSTANCE": 0, "INDEX": 0, "SPEED": 58})
        )
        self.assertEqual(ace1.feed_speed, 72.0)
        self.assertEqual(ace1.get_status()["feed_speed"], 72.0)
        self.assertEqual(ace0.retract_speed, 58.0)
        self.assertEqual(ace0.get_status()["retract_speed"], 58.0)

        mgr.feed_speed = 85.0
        self.assertEqual(ace0.feed_speed, 85.0)
        self.assertEqual(ace1.feed_speed, 85.0)
        mgr.feed_speed = None
        self.assertEqual(ace0.feed_speed, 60.0)
        self.assertEqual(ace1.feed_speed, 40.0)


class Rev11ReactorCallbackSafetyAndPollingTests(unittest.TestCase):
    def test_runout_swap_inside_timer_defers_pause_and_execute_swap_to_callback(self):
        printer, mgr = _fakes.make_manager(print_state="printing")
        mgr.state.set("ace_current_index", 0)
        mgr.state.set("ace_endless_spool_enabled", True)
        mon = mgr.runout_monitor
        mon.runout_debounce_count = 1
        mon.last_printing_active = True
        mon.last_print_state = "printing"
        mon.prev_toolhead_sensor_state = True
        mon.set_detection_active(True)
        mgr.get_switch_state = lambda _s: False

        swap_calls = []
        mon.endless_spool.find_exact_match = lambda _tool: 1

        def fake_swap(old_tool, new_tool):
            printer.reactor.pause(printer.reactor.now + 0.05)
            swap_calls.append((old_tool, new_tool))
            return True

        mon.endless_spool.execute_swap = fake_swap
        mgr.endless_spool = mon.endless_spool

        mon._monitoring_timer = printer.reactor.register_timer(mon._monitor_runout, printer.reactor.now)
        # Must NOT raise ReactorPauseInTimer when timer runs
        printer.reactor.fire(mon._monitoring_timer)
        self.assertEqual(swap_calls, [])

        printer.reactor.run_callbacks()
        self.assertEqual(swap_calls, [(0, 1)])
        self.assertIn("send_pause_command", printer.pause_resume.calls)
        self.assertIn("PAUSE", printer.gcode.scripts)

    def test_tangle_and_manager_connection_pause_defer_from_timer(self):
        printer, mgr = _fakes.make_manager(print_state="printing")
        mon = mgr.runout_monitor
        mon.tangle_pause_enabled = True

        tangle_timer = printer.reactor.register_timer(
            lambda et: (mon._handle_tangle_detected(0), printer.reactor.NEVER)[1],
            printer.reactor.now,
        )
        printer.reactor.fire(tangle_timer)
        self.assertEqual(printer.pause_resume.calls, [])
        printer.reactor.run_callbacks()
        self.assertIn("send_pause_command", printer.pause_resume.calls)
        self.assertIn("PAUSE", printer.gcode.scripts)

        printer2, mgr2 = _fakes.make_manager(print_state="printing")
        unstable = [{"instance": 0, "connected": False, "recent_reconnects": 0, "time_connected": 0}]
        conn_timer = printer2.reactor.register_timer(
            lambda et: (mgr2._pause_for_connection_issue(unstable), printer2.reactor.NEVER)[1],
            printer2.reactor.now,
        )
        printer2.reactor.fire(conn_timer)
        self.assertEqual(printer2.pause_resume.calls, [])
        printer2.reactor.run_callbacks()
        self.assertIn("send_pause_command", printer2.pause_resume.calls)
        self.assertIn("PAUSE", printer2.gcode.scripts)

    def test_adaptive_polling_intervals_for_runout_and_writer(self):
        printer, mgr = _fakes.make_manager(print_state="printing")
        mgr.state.set("ace_current_index", 0)
        mon = mgr.runout_monitor
        mon.last_printing_active = True
        mon.last_print_state = "printing"
        mon.runout_debounce_count = 3
        mon.set_detection_active(True)
        mon.prev_toolhead_sensor_state = True
        mgr.get_switch_state = lambda _s: True

        next_steady = mon._monitor_runout(1000.0)
        self.assertAlmostEqual(next_steady, 1000.25)

        mgr.get_switch_state = lambda _s: False
        next_debounce = mon._monitor_runout(1000.25)
        self.assertAlmostEqual(next_debounce, 1000.30)

        printer.print_stats.state = "standby"
        next_idle = mon._monitor_runout(1001.0)
        self.assertAlmostEqual(next_idle, 1001.50)

        _p, sm, _s = _make_serial_manager()
        sm._connected = False
        self.assertAlmostEqual(sm._writer(2000.0), 2000.0 + sm.IDLE_POLL_INTERVAL)


class PerformanceDeadCodeAndRev28Tests(unittest.TestCase):
    def test_status_schema_dead_fields_raw_fields_and_deepcopy_caching(self):
        default_status = ace_config.create_status_dict()
        self.assertIn("dryer_status", default_status)
        self.assertNotIn("dryer", default_status)

        printer, mgr = _fakes.make_manager()
        ace = mgr.instances[0]
        self.assertFalse(hasattr(ace, "_dryer_active"))
        self.assertFalse(hasattr(ace, "_dryer_temperature"))
        self.assertFalse(hasattr(ace, "_dryer_duration"))

        payload = {
            "status": "ready",
            "dryer_status": {"status": "stop", "target_temp": 0, "remain_time": 0},
            "slots": [],
            "raw_fields": {"opaque": b"bytes"},
        }
        ace._status_update_callback({"code": 0, "result": copy.deepcopy(payload)})
        self.assertNotIn("raw_fields", ace._info)
        self.assertNotIn("raw_fields", ace.get_status())

        # Equal heartbeat does not invoke copy.deepcopy inside instance.get_status()
        with mock.patch("ace.instance.copy.deepcopy", wraps=copy.deepcopy) as mocked_dc:
            _ = ace.get_status()
            ace._status_update_callback({"code": 0, "result": copy.deepcopy(payload)})
            calls_before = mocked_dc.call_count
            _ = ace.get_status()
            self.assertEqual(mocked_dc.call_count, calls_before)

        # Alternating reporting.get_status across sinks hits per-sink cache
        reporting._status_cache.clear()
        with mock.patch("ace.reporting.copy.deepcopy", wraps=copy.deepcopy) as rep_dc:
            reporting.get_status(mgr)
            reporting.get_status(ace)
            misses = rep_dc.call_count
            reporting.get_status(mgr)
            reporting.get_status(ace)
            self.assertEqual(rep_dc.call_count, misses)

    def test_rev28_serial_errors_lane_temps_and_reset_toolhead_message(self):
        printer, sm, ser = _make_serial_manager()
        sm.protocol.serialize_request_frame = mock.Mock(side_effect=ValueError("too big"))
        sm.reconnect = mock.Mock()
        self.assertFalse(sm._send_frame({"method": "get_info", "id": 1}))
        self.assertTrue(any("Serial write error" in line for line in printer.gcode.info))

        ser.read_error = OSError("disconnected")
        self.assertEqual(sm._reader(1000.0), printer.reactor.NEVER)
        sm.reconnect.assert_called_once()

        # Empty lane omits nozzle_temp / bed_temp
        printer2, mgr2 = _fakes.make_manager()
        mgr2.instances[0].tool_offset = 0
        mgr2.instances[0].inventory[0] = {
            "status": "empty",
            "material": "PLA",
            "color": [255, 0, 0],
            "temp": 210,
        }
        adapter = lane_sync.MoonrakerLaneSyncAdapter(printer2.gcode, mgr2, mgr2.ace_config)
        try:
            lanes = adapter._build_lane_payload()
            self.assertIn("lane1", lanes)
            self.assertNotIn("nozzle_temp", lanes["lane1"])
            self.assertNotIn("bed_temp", lanes["lane1"])
        finally:
            adapter.shutdown()

        # cmd_ACE_RESET_ACTIVE_TOOLHEAD responds cleanly
        gcmd = _fakes.FakeGcmd(printer2.gcode)
        cmd_mod.cmd_ACE_RESET_ACTIVE_TOOLHEAD(gcmd)
        self.assertTrue(any("Active toolhead state reset" in line for line in printer2.gcode.info))


if __name__ == "__main__":
    unittest.main()
