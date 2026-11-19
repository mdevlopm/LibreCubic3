import json
import unittest

import _fakes
from _fakes import FakeGcode, FakeReactor, FakeSerial, FakeTermios, frame_for

from ace import serial_manager as sm_mod
from ace.serial_manager import AceSerialManager

# Never touch the host's real termios with the fake file descriptor
sm_mod.termios = FakeTermios()


def new_manager():
    reactor = FakeReactor()
    gcode = FakeGcode()
    mgr = AceSerialManager(gcode, reactor, 0)
    return mgr, reactor, gcode


def attach_serial(mgr):
    """Pretend the port is open (no timers, no heartbeat)."""
    mgr._serial = FakeSerial("/dev/ttyACM0")
    mgr._connected = True
    return mgr._serial


def sent_frames(ser):
    """Decode request JSONs from the raw frames written to the fake port."""
    out = []
    for raw in ser.written:
        n = int.from_bytes(raw[2:4], "little")
        out.append(json.loads(raw[4:4 + n].decode()))
    return out


def reply(mgr, ser, rid, result=None, code=0, msg="OK"):
    payload = {"id": rid, "code": code, "msg": msg}
    if result is not None:
        payload["result"] = result
    ser.rx += frame_for(mgr.protocol, mgr, payload)


class WindowAndRetryTests(unittest.TestCase):
    def test_constants_match_stock_gklib(self):
        self.assertEqual(AceSerialManager.WINDOW_SIZE, 1)
        self.assertEqual(AceSerialManager.DEFAULT_TIMEOUT_S, 1.0)
        self.assertEqual(AceSerialManager.MAX_ATTEMPTS, 4)

    def test_only_one_request_on_the_wire(self):
        mgr, reactor, _ = new_manager()
        ser = attach_serial(mgr)
        for i in range(3):
            mgr.send_request({"method": f"m{i}"}, lambda response=None: None)
        mgr._writer(reactor.now)
        self.assertEqual([f["method"] for f in sent_frames(ser)], ["m0"])
        self.assertEqual(len(mgr.inflight), 1)

    def test_reply_releases_next_request_immediately(self):
        mgr, reactor, _ = new_manager()
        ser = attach_serial(mgr)
        got = []
        mgr.send_request({"method": "a"}, lambda response=None: got.append(response))
        mgr.send_request({"method": "b"}, lambda response=None: got.append(response))
        mgr._writer(reactor.now)
        rid = sent_frames(ser)[0]["id"]
        reply(mgr, ser, rid, {"x": 1})
        mgr._reader(reactor.now)  # no writer tick in between
        self.assertEqual(got[0]["result"], {"x": 1})
        self.assertEqual([f["method"] for f in sent_frames(ser)], ["a", "b"])

    def test_timeout_retries_same_id_then_fails_after_four_attempts(self):
        mgr, reactor, gcode = new_manager()
        ser = attach_serial(mgr)
        got = []
        mgr.send_request({"method": "a"}, lambda response=None: got.append(response))
        mgr._writer(reactor.now)
        for _ in range(3):
            reactor.advance(1.05)
            mgr._writer(reactor.now)
            self.assertEqual(got, [])  # still retrying
        reactor.advance(1.05)
        mgr._writer(reactor.now)
        frames = sent_frames(ser)
        self.assertEqual(len(frames), 4)
        self.assertEqual(len({f["id"] for f in frames}), 1)  # same id every attempt
        self.assertEqual(got, [None])
        self.assertEqual(mgr.inflight, {})
        self.assertTrue(any("TIMEOUT after 4 attempt" in m for m in gcode.info))

    def test_reply_to_retry_still_matches_callback(self):
        mgr, reactor, _ = new_manager()
        ser = attach_serial(mgr)
        got = []
        mgr.send_request({"method": "a"}, lambda response=None: got.append(response))
        mgr._writer(reactor.now)
        reactor.advance(1.05)
        mgr._writer(reactor.now)  # retry #2
        rid = sent_frames(ser)[0]["id"]
        reply(mgr, ser, rid, {"ok": True})
        mgr._reader(reactor.now)
        self.assertEqual(got[0]["result"], {"ok": True})

    def test_duplicate_reply_of_retried_request_is_not_unsolicited(self):
        mgr, reactor, gcode = new_manager()
        ser = attach_serial(mgr)
        mgr.send_request({"method": "a"}, lambda response=None: None)
        mgr._writer(reactor.now)
        reactor.advance(1.05)
        mgr._writer(reactor.now)
        rid = sent_frames(ser)[0]["id"]
        reply(mgr, ser, rid)
        reply(mgr, ser, rid)  # ACE answered both attempts
        mgr._reader(reactor.now)
        self.assertEqual(mgr._comm_unsolicited_timestamps, [])
        self.assertFalse(any("UNSOLICITED" in m for m in gcode.info))

    def test_truly_unsolicited_reply_is_counted(self):
        mgr, reactor, gcode = new_manager()
        ser = attach_serial(mgr)
        reply(mgr, ser, 4242)
        mgr._reader(reactor.now)
        self.assertEqual(len(mgr._comm_unsolicited_timestamps), 1)
        self.assertTrue(any("UNSOLICITED" in m for m in gcode.info))

    def test_clear_queues_resets_retry_bookkeeping(self):
        mgr, reactor, _ = new_manager()
        attach_serial(mgr)
        mgr.send_request({"method": "a"}, lambda response=None: None)
        mgr._writer(reactor.now)
        mgr.clear_queues()
        self.assertEqual(mgr.inflight, {})
        self.assertEqual(mgr._inflight_meta, {})


class HealthSupervisionTests(unittest.TestCase):
    def test_timeouts_alone_trigger_unhealthy(self):
        mgr, _, _ = new_manager()
        for _ in range(mgr.COMM_TIMEOUT_THRESHOLD - 1):
            mgr._track_comm_timeout()
        self.assertTrue(mgr._check_communication_health()[0])
        mgr._track_comm_timeout()
        healthy, reason = mgr._check_communication_health()
        self.assertFalse(healthy)
        self.assertIn("timeouts", reason)

    def test_unsolicited_alone_trigger_unhealthy(self):
        mgr, _, _ = new_manager()
        for _ in range(mgr.COMM_UNSOLICITED_THRESHOLD):
            mgr._track_comm_unsolicited()
        healthy, reason = mgr._check_communication_health()
        self.assertFalse(healthy)
        self.assertIn("unsolicited", reason)

    def test_supervision_forces_reconnect_on_timeouts(self):
        mgr, reactor, _ = new_manager()
        ser = attach_serial(mgr)
        for _ in range(mgr.COMM_TIMEOUT_THRESHOLD):
            mgr._track_comm_timeout()
        reactor.advance(10)
        mgr._supervision_check_and_recover()
        self.assertTrue(ser.closed)
        self.assertIsNotNone(mgr.connect_timer)  # a reconnect is scheduled
        self.assertEqual(mgr.connection_state, "reconnecting")

    def test_timeouts_from_unanswered_requests_trigger_reconnect_path(self):
        mgr, reactor, _ = new_manager()
        attach_serial(mgr)
        mgr.send_request({"method": "a"}, lambda response=None: None)
        mgr._writer(reactor.now)
        for _ in range(4):
            reactor.advance(1.05)
            mgr._handle_inflight_timeouts(reactor.now)
        self.assertEqual(len(mgr._comm_timeout_timestamps), 4)
        mgr.send_request({"method": "b"}, lambda response=None: None)
        mgr._pump(reactor.now)
        reactor.advance(1.05)
        mgr._handle_inflight_timeouts(reactor.now)
        self.assertFalse(mgr._check_communication_health()[0])


class ConnectTests(unittest.TestCase):
    def setUp(self):
        FakeSerial.instances.clear()
        self.termios = FakeTermios()
        self._orig = sm_mod.termios
        sm_mod.termios = self.termios

    def tearDown(self):
        sm_mod.termios = self._orig

    def test_open_without_handshake_and_hupcl_cleared(self):
        mgr, _, _ = new_manager()
        self.assertTrue(mgr.connect("/dev/ttyACM0", 115200))
        ser = FakeSerial.instances[-1]
        self.assertFalse(ser.kwargs["rtscts"])
        self.assertFalse(ser.kwargs["dsrdtr"])
        self.assertFalse(ser.kwargs["xonxoff"])
        self.assertEqual(ser.baudrate, 115200)
        self.assertEqual(self.termios.cflag & FakeTermios.HUPCL, 0)
        self.assertTrue(self.termios.set_calls)
        mgr.disconnect()

    def test_hupcl_cleared_again_before_close(self):
        mgr, _, _ = new_manager()
        mgr.connect("/dev/ttyACM0", 115200)
        self.termios.cflag |= FakeTermios.HUPCL  # something re-armed it
        before = len(self.termios.set_calls)
        mgr.disconnect()
        self.assertGreater(len(self.termios.set_calls), before)
        self.assertEqual(self.termios.cflag & FakeTermios.HUPCL, 0)
        self.assertTrue(FakeSerial.instances[-1].closed)

    def test_hupcl_failure_does_not_break_connect(self):
        self.termios.fail = True
        mgr, _, _ = new_manager()
        self.assertTrue(mgr.connect("/dev/ttyACM0", 115200))
        mgr.disconnect()

    def test_connect_to_ace_honours_delay(self):
        mgr, reactor, _ = new_manager()
        mgr.connect_to_ace(115200, delay=2)
        _, waketime = reactor.timers[mgr.connect_timer]
        self.assertAlmostEqual(waketime, reactor.now + 2.0)

    def test_connect_to_ace_none_delay_uses_backoff(self):
        mgr, reactor, _ = new_manager()
        mgr.connect_to_ace(115200, delay=None)
        _, waketime = reactor.timers[mgr.connect_timer]
        self.assertAlmostEqual(waketime, reactor.now + mgr._reconnect_backoff)


class PriorityTests(unittest.TestCase):
    def test_urgent_before_hp_before_normal(self):
        mgr, _, _ = new_manager()
        attach_serial(mgr)
        mgr.send_request({"method": "normal"}, None)
        mgr.send_high_prio_request({"method": "hp"}, None)
        mgr.send_urgent_request({"method": "stop_feed_assist", "params": {"index": 1}}, None)
        order = [mgr.get_pending_request()[0]["method"] for _ in range(3)]
        self.assertEqual(order, ["stop_feed_assist", "hp", "normal"])

    def test_urgent_goes_next_on_the_wire_behind_a_busy_window(self):
        mgr, reactor, _ = new_manager()
        ser = attach_serial(mgr)
        mgr.send_request({"method": "slow1"}, lambda response=None: None)
        mgr._writer(reactor.now)
        mgr.send_request({"method": "queued"}, lambda response=None: None)
        mgr.send_urgent_request({"method": "drying_stop"}, lambda response=None: None)
        rid = sent_frames(ser)[0]["id"]
        reply(mgr, ser, rid)
        mgr._reader(reactor.now)
        self.assertEqual([f["method"] for f in sent_frames(ser)], ["slow1", "drying_stop"])

    def test_drying_stop_cancels_queued_drying_start(self):
        mgr, _, _ = new_manager()
        attach_serial(mgr)
        got = []
        mgr.send_request(
            {"method": "drying", "params": {"temp": 50, "duration": 60}},
            lambda response=None: got.append(response),
        )
        mgr.send_request({"method": "get_filament_info", "params": {"index": 0}}, None)
        mgr.send_urgent_request({"method": "drying_stop"}, None)
        self.assertEqual(got[0]["code"], AceSerialManager.CANCELLED_RESPONSE_CODE)
        self.assertEqual(got[0]["msg"], "CANCELLED")
        methods = [mgr.get_pending_request()[0]["method"] for _ in range(2)]
        self.assertEqual(methods, ["drying_stop", "get_filament_info"])
        self.assertEqual(mgr.get_pending_request(), (None, None))

    def test_stop_feed_cancels_only_same_slot(self):
        mgr, _, _ = new_manager()
        attach_serial(mgr)
        cancelled = []
        mgr.send_request({"method": "feed_filament", "params": {"index": 0, "length": 5, "speed": 1}},
                         lambda response=None: cancelled.append(0))
        mgr.send_request({"method": "feed_filament", "params": {"index": 1, "length": 5, "speed": 1}},
                         lambda response=None: cancelled.append(1))
        mgr.send_urgent_request({"method": "stop_feed_filament", "params": {"index": 1}}, None)
        self.assertEqual(cancelled, [1])

    def test_has_pending_includes_urgent(self):
        mgr, _, _ = new_manager()
        attach_serial(mgr)
        self.assertFalse(mgr.has_pending_requests())
        mgr.send_urgent_request({"method": "drying_stop"}, None)
        self.assertTrue(mgr.has_pending_requests())


class HeartbeatTests(unittest.TestCase):
    def test_heartbeat_does_not_pile_up(self):
        mgr, _, _ = new_manager()
        attach_serial(mgr)
        for _ in range(5):
            mgr._send_heartbeat_request()
        self.assertEqual(mgr._hp_queue.qsize(), 1)

    def test_heartbeat_allowed_again_after_reply(self):
        mgr, reactor, _ = new_manager()
        ser = attach_serial(mgr)
        mgr._send_heartbeat_request()
        mgr._writer(reactor.now)
        rid = sent_frames(ser)[0]["id"]
        reply(mgr, ser, rid, {"status": "ready"})
        mgr._reader(reactor.now)
        self.assertFalse(mgr._heartbeat_outstanding)
        mgr._send_heartbeat_request()
        self.assertEqual(mgr._hp_queue.qsize(), 1)

    def test_heartbeat_allowed_again_after_timeouts(self):
        mgr, reactor, _ = new_manager()
        attach_serial(mgr)
        mgr._send_heartbeat_request()
        mgr._writer(reactor.now)
        for _ in range(4):
            reactor.advance(1.05)
            mgr._handle_inflight_timeouts(reactor.now)
        self.assertFalse(mgr._heartbeat_outstanding)

    def test_heartbeat_flag_not_stuck_when_ace_disabled(self):
        mgr, _, _ = new_manager()
        mgr._ace_pro_enabled = False
        mgr._send_heartbeat_request()
        self.assertFalse(mgr._heartbeat_outstanding)


class ShutdownTests(unittest.TestCase):
    def setUp(self):
        FakeSerial.instances.clear()
        self._orig = sm_mod.termios
        sm_mod.termios = FakeTermios()

    def tearDown(self):
        sm_mod.termios = self._orig

    def test_shutdown_closes_port_and_drops_everything(self):
        mgr, reactor, _ = new_manager()
        mgr.connect("/dev/ttyACM0", 115200)
        ser = FakeSerial.instances[-1]
        self.assertTrue(mgr.heartbeat_timer is not None)
        mgr.send_request({"method": "a"}, lambda response=None: None)
        mgr.shutdown()
        self.assertTrue(ser.closed)
        self.assertIsNone(mgr.heartbeat_timer)
        self.assertIsNone(mgr.writer_timer)
        self.assertIsNone(mgr.reader_timer)
        self.assertFalse(mgr.has_pending_requests())
        self.assertEqual(reactor.timers, {})
        self.assertEqual(mgr.connection_state, "shutdown")
        self.assertTrue(mgr.is_shutdown())

    def test_no_reconnect_or_requests_after_shutdown(self):
        mgr, reactor, _ = new_manager()
        mgr.connect("/dev/ttyACM0", 115200)
        mgr.shutdown()
        n_serials = len(FakeSerial.instances)
        mgr.ensure_connect_timer()
        mgr.reconnect(delay=0)
        mgr.connect_to_ace(115200, delay=0)
        self.assertFalse(mgr.auto_connect(0, 115200))
        self.assertFalse(mgr.send_request({"method": "a"}, None))
        self.assertFalse(mgr.send_high_prio_request({"method": "a"}, None))
        self.assertFalse(mgr.send_urgent_request({"method": "drying_stop"}, None))
        self.assertIsNone(mgr.connect_timer)
        self.assertEqual(reactor.timers, {})
        self.assertEqual(len(FakeSerial.instances), n_serials)

    def test_shutdown_closes_with_hupcl_cleared(self):
        mgr, _, _ = new_manager()
        mgr.connect("/dev/ttyACM0", 115200)
        sm_mod.termios.cflag |= FakeTermios.HUPCL
        mgr.shutdown()
        self.assertEqual(sm_mod.termios.cflag & FakeTermios.HUPCL, 0)


if __name__ == "__main__":
    unittest.main()
