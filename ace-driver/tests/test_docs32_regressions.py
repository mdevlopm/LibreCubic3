"""Regression tests for docs/32-hata-raporu.md section C (C-01..C-25) and I-03.

The fakes in _fakes.py model the Klipper behaviour the bugs depend on
(timer-running reactor, gcode mutex, save_variables/ConfigParser, ...).
"""

import inspect
import os
import queue
import re
import threading
import unittest
from types import SimpleNamespace

import _fakes
from _fakes import (FakeConfig, FakeGcmd, FakeGcode, FakePrinter, FakeReactor,
                    FakeSerial, KlipperSaveVariables, KlipperShutdown,
                    ReactorPauseInTimer, _tmp_file, feed_reply, frame_for,
                    make_manager, open_fake_port, sent_frames, status_result)

from ace import commands, instance as instance_mod, serial_manager as sm_mod
from ace.config import (ACE_INSTANCES, INSTANCE_MANAGERS, SENSOR_TOOLHEAD)
from ace.persistent_state import PersistentState
from ace.protocol_ace1 import AceJsonProtocolAdapter
from ace.protocol_ace2 import AceProtoProtocolAdapter
from ace.serial_manager import AceSerialManager

sm_mod.termios = _fakes.FakeTermios()


def hb_reply(ace, **kw):
    ace._on_heartbeat_response({"code": 0, "result": status_result(**kw)})
    ace.reactor.run_callbacks()  # callbacks run after the event returns (own greenlet)


class Base(unittest.TestCase):
    def build(self, overrides=None, state="standby", save_file=None):
        self.printer, self.manager = make_manager(overrides, print_state=state, save_file=save_file)
        self.ace = self.manager.instances[0]
        self.mgr = self.ace.serial_mgr
        self.reactor = self.printer.reactor
        self.gcode = self.printer.gcode
        self.ser = open_fake_port(self.mgr)

    def cmd(self, fn, **params):
        fn(FakeGcmd(self.gcode, params))

    def frames(self):
        return sent_frames(self.ser)


# ----------------------------------------------------------------------- I-03
class FakeModelTests(unittest.TestCase):
    def test_pause_inside_timer_is_flagged(self):
        r = FakeReactor()
        handle = r.register_timer(lambda t: r.pause(t + 1) or r.NEVER)
        with self.assertRaises(ReactorPauseInTimer):
            r.fire(handle)

    def test_pause_in_register_callback_is_fine_even_when_started_by_a_timer(self):
        r = FakeReactor()
        seen = []

        def timer(t):
            r.register_callback(lambda e: (r.pause(e + 1), seen.append("ok")))
            return r.NEVER
        r.fire(r.register_timer(timer))
        self.assertEqual(seen, [])        # never inline inside the timer
        r.run_callbacks()
        self.assertEqual(seen, ["ok"])

    def test_timer_is_never_while_running(self):
        r = FakeReactor()
        states = []
        handle = r.register_timer(lambda t: states.append(r.timers[handle][1]) or t + 1)
        r.fire(handle)
        self.assertEqual(states, [r.NEVER])

    def test_run_script_vs_run_script_from_command_are_distinguishable(self):
        g = FakeGcode()
        g.run_script("A")
        g.run_script_from_command("B")
        self.assertEqual(g.script_log, [("run_script", "A"), ("run_script_from_command", "B")])

    def test_klipper_model_lone_percent_shuts_klipper_down(self):
        model = KlipperSaveVariables(_tmp_file())
        with self.assertRaises(KlipperShutdown):
            model.run("SAVE_VARIABLE VARIABLE=x VALUE='\"100% PLA\"'")

    def test_klipper_model_keeps_semicolon_and_hash_inside_quotes(self):
        model = KlipperSaveVariables(_tmp_file())
        model.run("SAVE_VARIABLE VARIABLE=x VALUE='\"a;b#c\"'")
        self.assertEqual(model.allVariables["x"], "a;b#c")


# ----------------------------------------------------------------------- C-01
class C01ReaderTimerTests(Base):
    def reader_handle(self):
        return self.reactor.register_timer(self.mgr._reader, self.reactor.NOW)

    def test_slot_loaded_with_feed_assist_does_not_pause_inside_reader_timer(self):
        self.build()
        self.ace._feed_assist_index = 0
        handle = self.reader_handle()
        self.mgr._send_heartbeat_request()
        self.mgr._writer(self.reactor.now)
        rid = self.frames()[-1]["id"]
        # busy ACE + slot 1 became ready -> feed assist must be re-enabled,
        # which waits for "ready" (a reactor pause)
        reply = status_result(slot_states=["ready", "ready", "empty", "empty"])
        reply["status"] = "busy"
        self.ace.inventory[0]["status"] = "ready"
        self.ace.inventory[0]["material"] = "PLA"
        self.ace.inventory[0]["temp"] = 200
        self.ace.inventory[0]["color"] = [1, 2, 3]
        feed_reply(self.mgr, self.ser, rid, reply)
        self.printer.reactor.on_pause = lambda w: self.ace._info.update(status="ready")
        # must not raise ReactorPauseInTimer (the blocking part runs afterwards,
        # in its own greenlet)
        self.reactor.fire(handle)
        self.assertEqual([f["method"] for f in self.frames()], ["get_status"])  # nothing yet
        self.reactor.run_callbacks()
        methods = [r[0]["method"] for r in list(self.mgr._queue.queue)]
        methods += [f["method"] for f in self.frames()]   # may already be on the wire
        self.assertIn("start_feed_assist", methods)

    def test_old_behaviour_would_have_been_flagged(self):
        # sanity check of the model: calling the blocking function directly
        # from the timer raises
        self.build()
        self.ace._info["status"] = "busy"
        handle = self.reactor.register_timer(
            lambda t: self.ace._enable_feed_assist(0) or self.reactor.NEVER)
        with self.assertRaises(ReactorPauseInTimer):
            self.reactor.fire(handle)

    def test_pending_restore_never_blocks_and_waits_for_ready(self):
        self.build()
        self.ace._pending_feed_assist_restore = 2
        self.ace._feed_assist_index = 2
        self.ace._info["status"] = "busy"
        handle = self.reactor.register_timer(
            lambda t: self.ace._maybe_restore_pending_feed_assist() or self.reactor.NEVER)
        self.reactor.fire(handle)  # would raise ReactorPauseInTimer if it paused
        self.assertEqual(self.ace._pending_feed_assist_restore, 2)  # still pending
        self.assertEqual(self.mgr._queue.qsize(), 0)
        self.ace._info["status"] = "ready"
        self.reactor.fire(handle)
        self.assertEqual(self.ace._pending_feed_assist_restore, -1)
        self.assertEqual(self.mgr._queue.queue[0][0]["method"], "start_feed_assist")

    def test_pending_restore_gives_up_after_30_busy_heartbeats(self):
        self.build()
        self.ace._pending_feed_assist_restore = 1
        self.ace._info["status"] = "busy"
        for _ in range(32):
            self.ace._maybe_restore_pending_feed_assist()
        self.assertEqual(self.ace._pending_feed_assist_restore, -1)


# ----------------------------------------------------------------------- C-02 / C-07
class C02C07TangleTests(Base):
    def test_pause_goes_through_pause_resume_and_the_gcode_mutex(self):
        self.build({"tangle_protection": True}, state="printing")
        hb_reply(self.ace, cont_assist_time=20.0)
        self.assertEqual(self.printer.pause_resume.calls, ["send_pause_command"])
        self.assertEqual(self.gcode.script_log, [("run_script", "PAUSE")])  # NOT run_script_from_command

    def test_latched_while_paused_and_no_repause_after_resume(self):
        self.build({"tangle_protection": True}, state="printing")
        hb_reply(self.ace, cont_assist_time=9.0)
        self.assertTrue(self.ace.get_tangle_status()["triggered"])
        self.printer.print_stats.state = "paused"
        hb_reply(self.ace, cont_assist_time=9.0)
        self.assertTrue(self.ace.get_tangle_status()["triggered"])  # UI still sees it
        self.printer.print_stats.state = "printing"  # RESUME, counter not reset yet
        hb_reply(self.ace, cont_assist_time=9.0)
        self.assertEqual(self.gcode.scripts.count("PAUSE"), 1)
        hb_reply(self.ace, cont_assist_time=1.0)  # recovered -> re-armed
        self.assertFalse(self.ace.get_tangle_status()["triggered"])
        hb_reply(self.ace, cont_assist_time=9.0)
        self.assertEqual(self.gcode.scripts.count("PAUSE"), 2)

    def test_rearmed_when_print_ends(self):
        self.build({"tangle_protection": True}, state="printing")
        hb_reply(self.ace, cont_assist_time=9.0)
        self.printer.print_stats.state = "complete"
        hb_reply(self.ace, cont_assist_time=9.0)
        self.assertFalse(self.ace.get_tangle_status()["triggered"])

    def test_pause_helper_without_pause_resume_object(self):
        from ace.klipper_utils import pause_print_from_event
        printer = FakePrinter()
        printer.objects.pop("pause_resume", None)
        printer.pause_resume = None  # lookup returns None -> must not crash
        pause_print_from_event(printer, printer.gcode)
        self.assertEqual(printer.gcode.script_log, [("run_script", "PAUSE")])


# ----------------------------------------------------------------------- C-03
class C03FrameParserTests(unittest.TestCase):
    def setUp(self):
        self.p = AceJsonProtocolAdapter()
        self.mgr = AceSerialManager(FakeGcode(), FakeReactor(), 0)
        self.frame = frame_for(self.p, self.mgr, {"id": 7, "code": 0, "msg": "OK"})

    def feed(self, *chunks):
        buf = bytearray()
        out = []
        for c in chunks:
            buf += c
            res, buf, _ = self.p.extract_responses(buf, self.mgr._calc_crc)
            out += res
        return out

    def test_header_split_between_reads_after_long_junk(self):
        junk = b"\x01\x02\x03\x04\x05\x06\x07\x08"
        first = junk + self.frame[:1]       # ends with the lone 0xFF
        second = self.frame[1:]             # starts with 0xAA
        self.assertEqual(self.frame[0], 0xFF)
        res = self.feed(first, second)
        self.assertEqual([r["id"] for r in res], [7])

    def test_lone_ff_is_kept_junk_dropped(self):
        res, rest, notices = self.p.extract_responses(bytearray(b"abcdefghij\xFF"), self.mgr._calc_crc)
        self.assertEqual(bytes(rest), b"\xFF")
        self.assertTrue(any("dropped junk (10 bytes)" in n for n in notices))

    def test_all_junk_without_ff_is_dropped(self):
        res, rest, _ = self.p.extract_responses(bytearray(b"abcdefghij"), self.mgr._calc_crc)
        self.assertEqual(bytes(rest), b"")

    def test_corrupt_huge_length_does_not_stall_following_frames(self):
        bad = b"\xFF\xAA\xFF\xFF" + b"x" * 20
        res = self.feed(bad + self.frame)
        self.assertEqual([r["id"] for r in res], [7])

    def test_corrupt_huge_length_without_following_frame_resets_buffer(self):
        buf = bytearray(b"\xFF\xAA\xFF\xFF" + b"x" * 20)
        res, rest, notices = self.p.extract_responses(buf, self.mgr._calc_crc)
        self.assertEqual(res, [])
        self.assertEqual(len(rest), 0)
        self.assertTrue(any("Invalid frame length" in n for n in notices))

    def test_normal_frames_unchanged(self):
        res = self.feed(self.frame + self.frame)
        self.assertEqual(len(res), 2)

    def test_ace2_keeps_trailing_ff(self):
        p2 = AceProtoProtocolAdapter()
        res, rest, _ = p2.extract_responses(bytearray(b"abcdefghijkl\xFF"), self.mgr._calc_crc)
        self.assertEqual(bytes(rest), b"\xFF")


# ----------------------------------------------------------------------- C-04
class C04PersistentStateTests(Base):
    NASTY = {
        "material": "true_pla", "brand": "Bambu's", "sku": "100% PLA",
        "note": "a;b#c", "null_co": "null false true", "quote": 'say "hi"',
    }

    def setUp(self):
        self.path = _tmp_file()
        self.build(save_file=self.path)

    def reload(self):
        return KlipperSaveVariables(self.path).allVariables

    def test_round_trip_of_every_dangerous_character(self):
        nasty = {k: v for k, v in self.NASTY.items() if k != "sku"}   # sku has the '%'
        inv = [dict(nasty, status="ready", color=[1, 2, 3], temp=200, rfid=True)]
        self.manager.state.set("ace_inventory_0", inv)
        self.manager.state.flush()                     # via real SAVE_VARIABLE path
        self.assertEqual(self.reload()["ace_inventory_0"], inv)
        self.assertFalse(self.manager.state.has_pending)

    def test_old_json_replace_bug_is_gone(self):
        self.manager.state.set("v", [{"material": "true_pla", "x": None, "y": False}])
        self.manager.state.flush()
        self.assertEqual(self.reload()["v"], [{"material": "true_pla", "x": None, "y": False}])

    def test_percent_does_not_shut_klipper_down_now_or_later(self):
        P = PersistentState.PERCENT_REPLACEMENT
        self.manager.state.set("v", "100% PLA")
        self.manager.state.set("other", 1)
        self.manager.state.flush()   # a lone '%' anywhere would raise KlipperShutdown
        self.assertEqual(self.reload()["v"], "100" + P + " PLA")
        # Klipper rewrites its whole dict on EVERY later SAVE_VARIABLE: still fine
        self.manager.state.set("another", 2)
        self.manager.state.flush()
        self.assertEqual(self.reload()["another"], 2)
        self.assertNotIn("%", repr(self.printer.save_variables.allVariables))

    def test_percent_in_aliased_object_is_scrubbed_before_the_next_save(self):
        inv = self.manager.instances[0].inventory
        self.manager.state.set("ace_inventory_0", inv)      # stored by reference
        inv[0]["material"] = "PLA 100%"                       # edited afterwards
        self.manager.state.set("n", 1)
        self.manager.state.flush()
        self.assertNotIn("%", repr(self.printer.save_variables.allVariables))
        self.assertEqual(inv[0]["material"], "PLA 100%")      # the instance keeps its own text

    def test_values_without_percent_keep_object_identity(self):
        inv = [{"a": 1}]
        self.manager.state.set("k", inv)
        self.assertIs(self.printer.save_variables.allVariables["k"], inv)

    def test_flush_direct_with_percent_and_atomic(self):
        P = PersistentState.PERCENT_REPLACEMENT
        self.printer.save_variables.allVariables.update({"v": "100% PLA", "n": 5})
        self.manager.state.flush_direct()
        loaded = self.reload()   # Klipper's own loader (basic interpolation) must accept it
        self.assertEqual(loaded["v"], "100" + P + " PLA")
        self.assertEqual(loaded["n"], 5)
        leftovers = [f for f in os.listdir(os.path.dirname(self.path)) if ".tmp." in f]
        self.assertEqual(leftovers, [])

    def test_flush_direct_failure_keeps_old_file_intact(self):
        self.printer.save_variables.allVariables["keep"] = 1
        self.manager.state.flush_direct()
        with open(self.path) as fh:
            before = fh.read()
        self.printer.save_variables.allVariables["new"] = 2
        orig = os.replace
        os.replace = lambda *a, **k: (_ for _ in ()).throw(OSError("disk"))
        try:
            self.manager.state.flush_direct()   # logs, does not raise
        finally:
            os.replace = orig
        with open(self.path) as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual([f for f in os.listdir(os.path.dirname(self.path)) if ".tmp." in f], [])

    def test_failed_variable_stays_dirty_and_is_retried(self):
        state = self.manager.state
        state.set("a", 1)
        state.set("b", 2)
        real = self.gcode.run_script_from_command

        def flaky(script):
            if "VARIABLE=a " in script + " ":
                raise RuntimeError("boom")
            return real(script)
        self.gcode.run_script_from_command = flaky
        state.flush()
        self.assertEqual(state._dirty, {"a"})
        self.gcode.run_script_from_command = real
        state.flush()
        self.assertEqual(state._dirty, set())
        self.assertEqual(self.reload()["a"], 1)

    def test_format_value_is_a_single_shell_token(self):
        import shlex
        for v in ("x y", "it's", 5, 1.5, None, True, [1, "a b"], {"k": "v%"}):
            tok = PersistentState._format_value(v)
            self.assertEqual(len(shlex.split(tok)), 1)


# ----------------------------------------------------------------------- C-05
class FakeHeaters:
    def __init__(self):
        self.sensor_factories = {}

    def add_sensor_factory(self, name, factory):
        self.sensor_factories[name] = factory


class TempPrinter:
    def __init__(self, heaters=None):
        self.reactor = FakeReactor()
        self.heaters = heaters
        self.shutdowns = []
        self.objects = {}

    def get_reactor(self):
        return self.reactor

    def get_start_args(self):
        return {}

    def add_object(self, name, obj):
        self.objects[name] = obj

    def register_event_handler(self, *a):
        pass

    def invoke_shutdown(self, msg):
        self.shutdowns.append(msg)

    def lookup_object(self, name, default=None):
        if name == "heaters" and self.heaters is not None:
            return self.heaters
        if name == "mcu":
            return SimpleNamespace(estimated_print_time=lambda t: t)
        if default is None and name == "heaters":
            raise KeyError(name)
        return default

    def load_object(self, config, name):
        if config is not self.config:
            raise AttributeError("'Printer' object has no attribute 'getsection'")
        self.heaters = FakeHeaters()
        return self.heaters


class TempConfig:
    def __init__(self, printer):
        self._p = printer
        printer.config = self

    def get_printer(self):
        return self._p

    def get_name(self):
        return "temperature_sensor ace_temp"

    def getint(self, name, default=0):
        return default


class C05TemperatureTests(unittest.TestCase):
    def setUp(self):
        import temperature_ace
        self.mod = temperature_ace
        self.printer = TempPrinter(FakeHeaters())
        self.cfg = TempConfig(self.printer)
        self.sensor = temperature_ace.TemperatureACE(self.cfg)
        self.sensor.setup_minmax(0, 70)
        self.serial = SimpleNamespace(connected=True, is_connected=lambda: self.serial.connected)
        self.inst = SimpleNamespace(
            _info={"temp": 76.0}, serial_mgr=self.serial, instance_num=0, _info_time=None)
        self.sensor._ace_instance = self.inst

    def sample(self, n=1):
        for _ in range(n):
            self.printer.reactor.advance(1.0)
            self.inst._info_time = self.printer.reactor.monotonic()
            self.sensor._sample_ace_temperature(self.printer.reactor.now)

    def test_disconnected_ace_with_stale_temperature_never_shuts_down(self):
        self.serial.connected = False
        self.sample(10)
        self.assertEqual(self.printer.shutdowns, [])
        self.assertEqual(self.sensor.temp, 0.0)

    def test_old_status_is_unknown_even_if_port_is_open(self):
        self.printer.reactor.advance(100)
        self.inst._info_time = self.printer.reactor.monotonic() - 60  # 60 s old
        self.sensor._sample_ace_temperature(self.printer.reactor.now)
        self.assertEqual(self.printer.shutdowns, [])
        self.assertEqual(self.sensor.temp, 0.0)

    def test_single_spike_is_debounced_sustained_overtemp_shuts_down(self):
        self.inst._info["temp"] = 76.0
        self.sample(1)
        self.inst._info["temp"] = 60.0
        self.sample(1)
        self.assertEqual(self.printer.shutdowns, [])
        self.inst._info["temp"] = 76.0
        self.sample(self.mod.ACE_LIMIT_DEBOUNCE_SAMPLES)
        self.assertEqual(len(self.printer.shutdowns), 1)
        self.assertIn("above maximum", self.printer.shutdowns[0])

    def test_normal_temperature_reported(self):
        self.inst._info["temp"] = 45.5
        self.sample(3)
        self.assertEqual(self.sensor.temp, 45.5)
        self.assertEqual(self.printer.shutdowns, [])

    def test_callback_errors_do_not_escape(self):
        def bad(print_time, temp):
            raise RuntimeError("x")
        self.sensor.setup_callback(bad)
        self.printer.reactor  # noqa
        self.sample(1)  # must not raise

    def test_factory_registered_again_for_a_new_heaters_object_after_restart(self):
        h1 = FakeHeaters()
        self.mod.register_sensor_factory(TempConfig(TempPrinter(h1)))
        self.assertIn("temperature_ace", h1.sensor_factories)
        h2 = FakeHeaters()  # FIRMWARE_RESTART: new Printer/heaters, same process
        self.mod.register_sensor_factory(TempConfig(TempPrinter(h2)))
        self.assertIn("temperature_ace", h2.sensor_factories)

    def test_registration_is_idempotent_per_heaters(self):
        h = FakeHeaters()
        cfg = TempConfig(TempPrinter(h))
        self.mod.register_sensor_factory(cfg)
        first = h.sensor_factories["temperature_ace"]
        self.mod.register_sensor_factory(cfg)
        self.assertIs(h.sensor_factories["temperature_ace"], first)

    def test_load_config_passes_the_config_so_heaters_can_be_loaded(self):
        printer = TempPrinter(heaters=None)
        cfg = TempConfig(printer)
        self.mod.load_config(cfg)
        self.assertIn("temperature_ace", printer.heaters.sensor_factories)


# ----------------------------------------------------------------------- C-06
class C06ChangeToolTests(Base):
    def run_failed_change(self, pos, current=0, target=1):
        self.build(state="printing")
        self.manager.state.set("ace_current_index", current)
        self.manager.state.set("ace_filament_pos", pos)

        def boom(cur, tgt, **kw):
            raise RuntimeError("unload failed")
        self.manager.perform_tool_change = boom
        commands.cmd_ACE_CHANGE_TOOL(self.manager, FakeGcmd(self.gcode, {}), target)
        return self.manager.state.get("ace_current_index")

    def test_unload_incomplete_keeps_previous_tool(self):
        self.assertEqual(self.run_failed_change("nozzle"), 0)
        self.assertIn("SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=active VALUE=0", self.gcode.scripts)
        self.assertIn("PAUSE", self.gcode.scripts)

    def test_splitter_position_also_keeps_previous_tool(self):
        self.assertEqual(self.run_failed_change("splitter"), 0)

    def test_unload_done_keeps_old_recovery_behaviour(self):
        self.assertEqual(self.run_failed_change("bowden"), 1)


# ----------------------------------------------------------------------- C-08
class C08RunoutTests(Base):
    def setUp(self):
        self.build(state="standby")
        self.mon = self.manager.runout_monitor
        self.manager._sensor_override = {SENSOR_TOOLHEAD: True}
        self.manager.state.set("ace_current_index", 0)

    def tick(self, state=None):
        if state is not None:
            self.printer.print_stats.state = state
        self.reactor.advance(0.3)
        return self.mon._monitor_runout(self.reactor.now)

    def start_print(self):
        self.tick("printing")
        self.assertTrue(self.mon.runout_detection_active)

    def test_cleanup_runs_after_print_end_disabled_detection(self):
        self.start_print()
        self.manager.set_runout_detection_active(False)   # what _ACE_HANDLE_PRINT_END does
        self.manager.state.set("ace_current_index", -1)   # tool unloaded
        self.gcode.scripts.clear()
        self.tick("complete")
        self.assertTrue(self.mon.runout_detection_active)  # restored
        self.assertIn("SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=active VALUE=-1", self.gcode.scripts)
        self.assertIsNone(self.mon.prev_toolhead_sensor_state)

    def test_print_cancelled_while_paused_also_cleans_up(self):
        self.start_print()
        self.tick("paused")
        self.manager.set_runout_detection_active(False)
        self.gcode.scripts.clear()
        self.tick("cancelled")
        self.assertIn("SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=active VALUE=-1", self.gcode.scripts)

    def test_cleanup_is_deferred_while_a_toolchange_runs(self):
        self.start_print()
        self.manager.set_runout_detection_active(False)
        self.manager.toolchange_in_progress = True
        self.gcode.scripts.clear()
        self.tick("complete")
        self.assertEqual(self.gcode.scripts, [])
        self.manager.toolchange_in_progress = False
        self.tick()
        self.assertIn("SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=active VALUE=-1", self.gcode.scripts)

    def test_runout_swap_does_not_overwrite_prev_sensor_with_stale_state(self):
        self.start_print()
        self.tick()  # baseline
        self.assertTrue(self.mon.prev_toolhead_sensor_state)
        self.manager._sensor_override = {SENSOR_TOOLHEAD: False}

        def fake_handle(tool):
            self.manager._sensor_override = {SENSOR_TOOLHEAD: True}  # swap loaded new spool
        self.mon._handle_runout_detected = fake_handle
        self.tick()
        self.assertIs(self.mon.prev_toolhead_sensor_state, True)


# ----------------------------------------------------------------------- C-09
class C09EndlessSpoolTests(Base):
    def setUp(self):
        self.build(state="printing")
        self.es = self.manager.endless_spool
        inv = self.ace.inventory
        for i, st in enumerate(["empty", "ready", "ready", "empty"]):
            inv[i].update(status=st, material="PLA", color=[10, 10, 10], temp=200)

    def test_empty_material_never_matches(self):
        for i in (0, 1, 2):
            self.ace.inventory[i]["material"] = ""
        self.assertEqual(self.es.find_exact_match(0), -1)
        self.manager.state.set("ace_endless_spool_match_mode", "material")
        self.assertEqual(self.es.find_exact_match(0), -1)

    def test_next_mode_still_takes_first_ready(self):
        self.manager.state.set("ace_endless_spool_match_mode", "next")
        for i in (0, 1, 2):
            self.ace.inventory[i]["material"] = ""
        self.assertEqual(self.es.find_exact_match(0), 1)

    def test_matching_material_still_found(self):
        self.assertEqual(self.es.find_exact_match(0), 1)

    def test_failed_recovery_unload_stops_the_swap_and_keeps_inventory_honest(self):
        loads = []

        def fail_load(cur, tgt, **kw):
            loads.append(tgt)
            raise RuntimeError("feed failed")
        self.manager.perform_tool_change = fail_load
        self.ace._smart_unload_slot = lambda slot, length=0, **kw: False  # path stays blocked
        self.manager.is_filament_path_free_instant = lambda: False
        self.es.execute_swap(0, 1)
        self.assertEqual(loads, [1])                       # T2 was NOT tried on top of the jam
        self.assertEqual(self.ace.inventory[0]["status"], "empty")   # ran-out spool stays empty
        self.assertEqual(self.ace.inventory[1]["status"], "ready")   # candidate not declared empty
        self.assertTrue(any("ENDLESS SPOOL SWAP FAILED" in m for m in self.gcode.raw))

    def test_successful_recovery_moves_on_to_the_next_candidate(self):
        loads = []

        def load(cur, tgt, **kw):
            loads.append(tgt)
            if tgt == 1:
                raise RuntimeError("feed failed")
            return "ok"
        self.manager.perform_tool_change = load
        self.ace._smart_unload_slot = lambda slot, length=0, **kw: True
        self.manager.is_filament_path_free_instant = lambda: True
        self.es.execute_swap(0, 1)
        self.assertEqual(loads, [1, 2])
        self.assertEqual(self.ace.inventory[1]["status"], "empty")  # bad candidate marked


# ----------------------------------------------------------------------- C-10 / C-11 / C-12 / C-19 / C-22
class CommandFixTests(Base):
    def test_c10_full_unload_all_keeps_tool_when_nozzle_slot_skipped(self):
        self.build()
        self.ace.inventory[0]["status"] = "ready"
        self.ace.inventory[1]["status"] = "ready"
        self.manager.state.set("ace_current_index", 0)
        self.manager.state.set("ace_filament_pos", "nozzle")
        self.manager.full_unload_slot = lambda tool: True
        self.cmd(commands.cmd_ACE_FULL_UNLOAD, TOOL="ALL")
        self.assertEqual(self.manager.state.get("ace_current_index"), 0)

    def test_c10_full_unload_all_clears_tool_when_nothing_skipped(self):
        self.build()
        self.ace.inventory[1]["status"] = "ready"
        self.manager.state.set("ace_current_index", 1)
        self.manager.state.set("ace_filament_pos", "bowden")
        self.manager.full_unload_slot = lambda tool: True
        self.cmd(commands.cmd_ACE_FULL_UNLOAD, TOOL="ALL")
        self.assertEqual(self.manager.state.get("ace_current_index"), -1)

    def test_c10_failures_are_error_lines(self):
        self.build()
        self.ace.inventory[1]["status"] = "ready"
        self.manager.full_unload_slot = lambda tool: False
        self.cmd(commands.cmd_ACE_FULL_UNLOAD, TOOL="ALL")
        self.assertTrue(any(m.startswith("!! ACE: Full unload ALL failed for: T1") for m in self.gcode.raw))

    def test_c11_query_slots_survives_malformed_colour(self):
        self.build()
        self.ace.inventory[0].update(status="ready", material="PLA", color=None)
        self.ace.inventory[1].update(status="ready", material="PLA", color=[1, 2])
        self.ace.inventory[2].update(status="ready", material="PLA", color=["x", 1, 2])
        self.cmd(commands.cmd_ACE_QUERY_SLOTS)
        self.assertEqual(self.gcode.raw, [])
        self.assertTrue(any("RGB(0,0,0)" in m for m in self.gcode.info))

    def test_c12_reconnect_after_shutdown_is_refused_with_error_line(self):
        self.build()
        self.printer.send_event("klippy:shutdown")
        self.cmd(commands.cmd_ACE_RECONNECT, INSTANCE=0)
        self.cmd(commands.cmd_ACE_RECONNECT)
        self.assertEqual(len(self.gcode.raw), 2)
        self.assertIn("FIRMWARE_RESTART", self.gcode.raw[0])
        self.assertFalse(any("reconnecting in" in m for m in self.gcode.info))

    def test_c19_connection_status_reports_unhealthy_as_the_serial_layer_does(self):
        self.build()
        for _ in range(self.mgr.COMM_TIMEOUT_THRESHOLD):
            self.mgr._track_comm_timeout()
        self.cmd(commands.cmd_ACE_GET_CONNECTION_STATUS)
        text = "\n".join(self.gcode.info)
        self.assertIn("Serial Health: UNHEALTHY", text)
        self.assertFalse(self.mgr._check_communication_health()[0])

    def test_c19_healthy_below_thresholds_and_unsolicited_alone_is_unhealthy(self):
        self.build()
        for _ in range(self.mgr.COMM_TIMEOUT_THRESHOLD - 1):
            self.mgr._track_comm_timeout()
        self.cmd(commands.cmd_ACE_GET_CONNECTION_STATUS)
        self.assertIn("Serial Health: healthy", "\n".join(self.gcode.info))
        for _ in range(self.mgr.COMM_UNSOLICITED_THRESHOLD):
            self.mgr._track_comm_unsolicited()
        self.gcode.info.clear()
        self.cmd(commands.cmd_ACE_GET_CONNECTION_STATUS)
        self.assertIn("Serial Health: UNHEALTHY", "\n".join(self.gcode.info))

    def test_c22_get_status_tool_selects_one_instance(self):
        self.build()
        self.cmd(commands.cmd_ACE_GET_STATUS, TOOL=0)
        self.assertFalse(any("All Instances" in m for m in self.gcode.info))
        self.assertEqual(sent_frames(self.ser) or [], [])
        self.mgr._writer(self.reactor.now)
        self.assertEqual(self.frames()[-1]["method"], "get_status")

    def test_c22_set_slot_requires_valid_temp(self):
        self.build()
        for params in ({"T": 0, "COLOR": "RED", "MATERIAL": "PLA"},
                       {"T": 0, "COLOR": "RED", "MATERIAL": "PLA", "TEMP": 500},
                       {"T": 0, "COLOR": "RED", "MATERIAL": "  ", "TEMP": 200}):
            self.cmd(commands.cmd_ACE_SET_SLOT, **params)
        self.assertEqual(len(self.gcode.raw), 3)
        self.assertEqual(self.ace.inventory[0]["status"], "empty")
        self.cmd(commands.cmd_ACE_SET_SLOT, T=0, COLOR="RED", MATERIAL=" PLA ", TEMP=210)
        self.assertEqual(self.ace.inventory[0]["material"], "PLA")
        self.assertEqual(self.ace.inventory[0]["temp"], 210)

    def test_c22_purge_amount_validation(self):
        self.build()
        before = (self.manager.toolchange_purge_length, self.manager.toolchange_purge_speed)
        for params in ({"PURGELENGTH": -10}, {"PURGELENGTH": 10, "PURGESPEED": 0},
                       {"PURGELENGTH": 10, "INSTANCE": 99}):
            self.cmd(commands.cmd_ACE_SET_PURGE_AMOUNT, **params)
        self.assertEqual(len(self.gcode.raw), 3)
        self.assertEqual((self.manager.toolchange_purge_length, self.manager.toolchange_purge_speed), before)
        self.cmd(commands.cmd_ACE_SET_PURGE_AMOUNT, PURGELENGTH=30, PURGESPEED=200)
        self.assertEqual(self.manager.toolchange_purge_length, 30.0)

    def test_c22_reconnect_negative_delay_rejected(self):
        self.build()
        self.cmd(commands.cmd_ACE_RECONNECT, INSTANCE=0, DELAY=-5)
        self.assertEqual(len(self.gcode.raw), 1)
        self.assertIsNone(self.mgr.connect_timer)

    def test_c22_command_errors_are_one_line_without_modal_or_traceback(self):
        self.build()
        wrapped = commands.safe_gcode_command(commands.cmd_ACE_ENABLE_FEED_ASSIST)
        wrapped(FakeGcmd(self.gcode, {}))  # no T= / INSTANCE= INDEX=
        self.assertEqual(len(self.gcode.raw), 1)
        self.assertTrue(self.gcode.raw[0].startswith("!! ACE_ENABLE_FEED_ASSIST error:"))
        self.assertEqual(self.gcode.scripts, [])  # no RESPOND prompt_begin modal

    def test_c22_internal_errors_still_open_the_dialog(self):
        self.build()

        def boom(gcmd):
            raise ValueError("internal")
        commands.safe_gcode_command(boom)(FakeGcmd(self.gcode, {}))
        self.assertTrue(any("prompt_begin" in s for s in self.gcode.scripts))


# ----------------------------------------------------------------------- C-13 / C-14 / C-20
class SerialFixTests(unittest.TestCase):
    def new(self):
        reactor, gcode = FakeReactor(), FakeGcode()
        return AceSerialManager(gcode, reactor, 0), reactor, gcode

    def test_c13_full_queue_never_blocks(self):
        orig = AceSerialManager.QUEUE_MAXSIZE
        AceSerialManager.QUEUE_MAXSIZE = 2
        try:
            mgr, _, gcode = self.new()
        finally:
            AceSerialManager.QUEUE_MAXSIZE = orig
        _fakes.open_fake_port(mgr)
        import time
        t0 = time.monotonic()
        results = [mgr.send_request({"method": f"m{i}"}, None) for i in range(4)]
        self.assertLess(time.monotonic() - t0, 0.5)   # put(timeout=1) would take >= 2 s
        self.assertEqual(results, [True, True, False, False])
        self.assertTrue(any("queue full" in m for m in gcode.info))

    def test_c13_pump_without_port_leaves_the_request_queued(self):
        mgr, reactor, _ = self.new()
        mgr._queue.put_nowait([{"method": "a"}, None])
        mgr._pump(reactor.now)
        self.assertEqual(mgr.inflight, {})
        self.assertEqual(mgr._queue.qsize(), 1)

    def test_c13_write_error_fails_the_request_instead_of_hanging_the_window(self):
        mgr, reactor, _ = self.new()
        ser = _fakes.open_fake_port(mgr)
        ser.write_error = sm_mod.SerialException("gone")
        got = []
        mgr.send_request({"method": "a"}, lambda response=None: got.append(response))
        mgr._pump(reactor.now)
        self.assertEqual(got, [None])
        self.assertEqual(mgr.inflight, {})

    def test_c13_send_frame_returns_false_on_failure(self):
        mgr, _, _ = self.new()
        ser = _fakes.open_fake_port(mgr)
        self.assertTrue(mgr._send_frame({"method": "a", "id": 1}))
        ser.write_error = sm_mod.SerialException("x")
        self.assertFalse(mgr._send_frame({"method": "a", "id": 2}))

    def test_c14_disabled_during_connect_clears_the_timer_handle(self):
        mgr, reactor, _ = self.new()
        mgr.connect_to_ace(115200, delay=1)
        handle = mgr.connect_timer
        mgr._ace_pro_enabled = False
        ret = reactor.fire(handle)
        self.assertEqual(ret, reactor.NEVER)
        self.assertIsNone(mgr.connect_timer)

    def test_c14_connect_to_ace_replaces_a_pending_timer(self):
        mgr, reactor, _ = self.new()
        mgr.connect_to_ace(115200, delay=1)
        first = mgr.connect_timer
        mgr.connect_to_ace(115200, delay=1)
        self.assertNotIn(first, reactor.timers)
        self.assertEqual(len(reactor.timers), 1)

    def test_c14_reader_serial_error_closes_port_even_if_connect_timer_exists(self):
        mgr, reactor, _ = self.new()
        ser = _fakes.open_fake_port(mgr)
        mgr.connect_timer = reactor.register_timer(lambda t: reactor.NEVER, reactor.NEVER)
        ser.read_error = sm_mod.SerialException("device disappeared")
        ret = mgr._reader(reactor.now)
        self.assertEqual(ret, reactor.NEVER)
        self.assertFalse(mgr.is_connected())
        self.assertTrue(ser.closed)
        self.assertIsNotNone(mgr.connect_timer)   # a reconnect is scheduled

    def test_c20_requests_while_disconnected_are_refused_not_queued(self):
        mgr, _, _ = self.new()
        self.assertFalse(mgr.send_request({"method": "drying"}, None))
        self.assertFalse(mgr.send_high_prio_request({"method": "get_status"}, None))
        self.assertFalse(mgr.send_urgent_request({"method": "drying_stop"}, None))
        self.assertFalse(mgr.has_pending_requests())

    def test_c20_stale_queue_is_dropped_when_the_port_opens(self):
        mgr, reactor, _ = self.new()
        old = sm_mod.termios
        try:
            mgr._queue.put_nowait([{"method": "drying", "params": {}}, None])   # leftover
            self.assertTrue(mgr.connect("/dev/ttyACM0", 115200))
            methods = []
            while not mgr._hp_queue.empty():
                methods.append(mgr._hp_queue.get_nowait()[0]["method"])
            self.assertEqual(methods, ["get_status"])
            self.assertEqual(mgr._queue.qsize(), 0)
            mgr.disconnect()
        finally:
            sm_mod.termios = old


class C20CommandTests(Base):
    def setUp(self):
        self.build()
        self.mgr._connected = False  # port closed
        self.ser.is_open = False

    def assert_refused(self, fn, **params):
        self.gcode.raw.clear()
        self.cmd(fn, **params)
        self.assertEqual(len(self.gcode.raw), 1, fn.__name__)
        self.assertIn("not connected", self.gcode.raw[0])
        self.assertTrue(self.gcode.raw[0].startswith("!!"))
        self.assertFalse(self.mgr.has_pending_requests())

    def test_all_hardware_commands_refuse_while_disconnected(self):
        self.assert_refused(commands.cmd_ACE_START_DRYING, INSTANCE=0, TEMP=50, DURATION=60)
        self.assert_refused(commands.cmd_ACE_START_DRYING, TEMP=50, DURATION=60)
        self.assert_refused(commands.cmd_ACE_STOP_DRYING, INSTANCE=0)
        self.assert_refused(commands.cmd_ACE_STOP_DRYING)
        self.assert_refused(commands.cmd_ACE_SET_FAN_SPEED, SPEED=4000, INSTANCE=0)
        self.assert_refused(commands.cmd_ACE_SET_FAN_SPEED, SPEED=4000)
        self.assert_refused(commands.cmd_ACE_FEED, T=0, LENGTH=10)
        self.assert_refused(commands.cmd_ACE_STOP_FEED, T=0)
        self.assert_refused(commands.cmd_ACE_RETRACT, T=0, LENGTH=10)
        self.assert_refused(commands.cmd_ACE_STOP_RETRACT, T=0)
        self.assert_refused(commands.cmd_ACE_GET_STATUS, INSTANCE=0)
        self.assert_refused(commands.cmd_ACE_GET_STATUS)

    def test_feed_assist_commands_refuse_while_disconnected(self):
        for fn in (commands.cmd_ACE_ENABLE_FEED_ASSIST, commands.cmd_ACE_DISABLE_FEED_ASSIST):
            self.gcode.raw.clear()
            commands.safe_gcode_command(fn)(FakeGcmd(self.gcode, {"T": 0}))
            self.assertEqual(len(self.gcode.raw), 1)
            self.assertIn("not connected", self.gcode.raw[0])

    def test_nothing_runs_later_when_the_ace_finally_connects(self):
        self.assert_refused(commands.cmd_ACE_START_DRYING, INSTANCE=0, TEMP=50, DURATION=60)
        self.assertTrue(self.mgr.connect("/dev/ttyACM0", 115200))
        port = _fakes.FakeSerial.instances[-1]
        self.mgr._writer(self.reactor.now)
        self.assertEqual([f["method"] for f in sent_frames(port)], ["get_status"])

    def test_sync_waiters_fail_fast_when_disconnected(self):
        start = self.reactor.now
        resp = self.ace.feed_filament_with_wait_for_response(0, 10, 5)
        self.assertEqual(resp["code"], -1)
        self.assertLess(self.reactor.now - start, 3.0)
        self.assertFalse(self.ace._change_feed_speed(0, 5))
        self.assertFalse(self.ace._change_retract_speed(0, 5))


# ----------------------------------------------------------------------- C-15 / C-23
class C15SyncWaitTests(Base):
    def drive_link(self, first_reply_after):
        """Pump the serial layer on every reactor pause; the ACE answers the
        first request only ``first_reply_after`` seconds after it was sent
        (the serial layer meanwhile retries after 1 s)."""
        state = {"t0": None}

        def hook(waketime):
            self.mgr._writer(self.reactor.now)
            frames = self.frames()
            if not frames:
                return
            if state["t0"] is None:
                state["t0"] = self.reactor.now
            if self.reactor.now - state["t0"] >= first_reply_after:
                feed_reply(self.mgr, self.ser, frames[-1]["id"])
                self.mgr._reader(self.reactor.now)
        self.reactor.on_pause = hook

    def test_feed_speed_change_with_a_slow_reply_succeeds_without_a_resend(self):
        self.build()
        self.drive_link(first_reply_after=1.15)
        self.assertTrue(self.ace._change_feed_speed(0, 5))
        self.assertEqual(self.gcode.raw, [])
        self.assertEqual(len(self.frames()), 1)          # "start" commands are never re-sent

    def test_retract_speed_change_with_a_slower_reply_also_succeeds(self):
        self.build()
        self.drive_link(first_reply_after=2.2)
        self.assertTrue(self.ace._change_retract_speed(0, 5))
        self.assertEqual(len(self.frames()), 1)

    def test_query_with_a_slow_reply_is_retried_with_the_same_id(self):
        self.build()
        got = []
        self.mgr.send_request({"method": "get_info"}, lambda response=None: got.append(response))
        self.mgr._writer(self.reactor.now)
        self.reactor.advance(1.05)
        self.mgr._writer(self.reactor.now)
        self.assertEqual(len({f["id"] for f in self.frames()}), 1)
        self.assertEqual(len(self.frames()), 2)

    def test_budget_covers_the_worst_case_of_two_full_windows(self):
        # waits behind ONE request that uses its whole retry window, then needs its own
        self.build({"serial_request_timeout": 2.0})
        self.assertEqual(self.ace._rpc_budget(5.0), 17.0)    # 2*(2.0*4)+1
        self.assertEqual(self.ace._rpc_budget(20.0), 20.0)
        self.build()
        self.assertEqual(self.ace._rpc_budget(1.0), 9.0)     # 2*(1.0*4)+1
        self.assertEqual(self.ace._rpc_budget(10.0), 10.0)

    def test_real_timeout_ends_the_wait_early_with_a_clear_error(self):
        self.build()
        self.reactor.on_pause = lambda w: self.mgr._writer(self.reactor.now)  # never answered
        self.assertFalse(self.ace._change_feed_speed(0, 5))
        self.assertTrue(any("returned no response" in m for m in self.gcode.raw))

    def test_no_wall_clock_in_wait_loops(self):
        src = inspect.getsource(instance_mod)
        self.assertEqual(len(re.findall(r"time\.time\(\)", src)), 1)  # only the event timestamp


class C23AbortTests(Base):
    def test_wait_ready_and_dwell_abort_on_shutdown(self):
        self.build()
        self.ace._info["status"] = "busy"
        self.printer.send_event("klippy:shutdown")
        with self.assertRaises(RuntimeError):
            self.ace.wait_ready()
        with self.assertRaises(RuntimeError):
            self.ace.dwell(1.0)

    def test_shutdown_during_wait_ends_it_immediately(self):
        self.build()
        self.ace._info["status"] = "busy"
        calls = []

        def hook(w):
            calls.append(w)
            if len(calls) == 2:
                self.printer.send_event("klippy:shutdown")
        self.reactor.on_pause = hook
        with self.assertRaises(RuntimeError):
            self.ace.wait_ready(timeout_s=60)
        self.assertLess(len(calls), 5)

    def test_spool_wait_stops_on_shutdown(self):
        self.build(state="printing")
        self.ace.inventory[0]["status"] = "empty"
        self.ace._info["slots"][0]["status"] = "empty"
        calls = []

        def hook(w):
            calls.append(w)
            if len(calls) == 2:
                self.printer.send_event("klippy:shutdown")
        self.reactor.on_pause = hook
        ok = self.manager.check_and_wait_for_spool_ready(0, timeout_s=300)
        self.assertFalse(ok)
        self.assertLess(len(calls), 6)
        self.assertTrue(any("aborted - printer shutdown" in m for m in self.gcode.raw))

    def test_spool_wait_stops_when_print_is_cancelled(self):
        self.build(state="printing")
        self.ace.inventory[0]["status"] = "empty"
        self.ace._info["slots"][0]["status"] = "empty"
        calls = []

        def hook(w):
            calls.append(w)
            if len(calls) == 2:
                self.printer.print_stats.state = "cancelled"
        self.reactor.on_pause = hook
        self.assertFalse(self.manager.check_and_wait_for_spool_ready(0, timeout_s=300))
        self.assertLess(len(calls), 6)


# ----------------------------------------------------------------------- C-16 / C-17
class C16RfidFeedAssistTests(Base):
    def test_pending_rfid_markers_cleared_on_reconnect(self):
        self.build()
        self.ace._pending_rfid_queries.update({0, 1})
        self.ace._rfid_retry_after[2] = 1e9
        self.ace._on_ace_connect()
        self.assertEqual(self.ace._pending_rfid_queries, set())
        self.assertEqual(self.ace._rfid_retry_after, {})

    def test_rfid_query_while_disconnected_leaves_no_pending_marker(self):
        self.build()
        self.mgr._connected = False
        self.ser.is_open = False
        self.assertFalse(self.ace._query_rfid_full_data(0))
        self.assertEqual(self.ace._pending_rfid_queries, set())

    def test_failed_rfid_read_clears_flag_and_retries_after_a_pause(self):
        self.build()
        reply = status_result(slot_states=["ready", "empty", "empty", "empty"])
        reply["slots"][0]["rfid"] = 2   # RFID_STATE_IDENTIFIED
        self.ace.inventory[0].update(status="empty")
        self.ace._on_heartbeat_response({"code": 0, "result": reply})
        self.assertTrue(self.ace.inventory[0]["rfid"])
        self.assertEqual(self.mgr._queue.queue[-1][0]["method"], "get_filament_info")
        # the read fails
        self.mgr._writer(self.reactor.now)
        rid = self.frames()[-1]["id"]
        feed_reply(self.mgr, self.ser, rid, code=5, msg="READ_ERR")
        self.mgr._reader(self.reactor.now)
        self.assertFalse(self.ace.inventory[0]["rfid"])
        self.assertTrue(any("get_filament_info failed" in m for m in self.gcode.raw))
        # heartbeat before the retry delay: no new query
        queued = self.mgr._queue.qsize() + len(self.ace._pending_rfid_queries)
        self.ace._on_heartbeat_response({"code": 0, "result": reply})
        self.assertEqual(self.mgr._queue.qsize() + len(self.ace._pending_rfid_queries), queued)
        self.reactor.advance(self.ace.RFID_RETRY_DELAY_S + 1)
        self.ace._on_heartbeat_response({"code": 0, "result": reply})
        self.assertIn(0, self.ace._pending_rfid_queries)

    def test_feed_assist_index_rolled_back_when_ace_refuses(self):
        self.build()
        self.ace._enable_feed_assist(2)
        self.assertEqual(self.ace._feed_assist_index, 2)   # optimistic until the reply
        self.mgr._writer(self.reactor.now)
        feed_reply(self.mgr, self.ser, self.frames()[-1]["id"], code=1, msg="FORBIDDEN")
        self.mgr._reader(self.reactor.now)
        self.assertEqual(self.ace._feed_assist_index, -1)
        self.assertTrue(any("Feed assist enable failed" in m for m in self.gcode.raw))

    def test_feed_assist_index_kept_when_ace_accepts(self):
        self.build()
        self.ace._enable_feed_assist(2)
        self.mgr._writer(self.reactor.now)
        feed_reply(self.mgr, self.ser, self.frames()[-1]["id"])
        self.mgr._reader(self.reactor.now)
        self.assertEqual(self.ace._feed_assist_index, 2)

    def test_feed_assist_enable_when_disconnected_rolls_back_at_once(self):
        self.build()
        self.mgr._connected = False
        self.ser.is_open = False
        self.ace._enable_feed_assist(1)
        self.assertEqual(self.ace._feed_assist_index, -1)


class C17SharedBusTests(Base):
    def setUp(self):
        self.build()
        self.ace.transport_spec = SimpleNamespace(shared_bus=True)

    def test_heartbeat_does_not_pile_up_on_a_silent_bus(self):
        for _ in range(5):
            self.ace._shared_bus_heartbeat_tick(self.reactor.now)
        self.assertEqual(self.mgr._hp_queue.qsize(), 1)

    def test_heartbeat_resumes_after_reply_reconnect_or_age(self):
        self.ace._shared_bus_heartbeat_tick(self.reactor.now)
        self.mgr._writer(self.reactor.now)
        feed_reply(self.mgr, self.ser, self.frames()[-1]["id"], status_result())
        self.mgr._reader(self.reactor.now)
        self.assertFalse(self.ace._shared_bus_hb_outstanding)
        self.ace._shared_bus_heartbeat_tick(self.reactor.now)
        self.assertTrue(self.ace._shared_bus_hb_outstanding)
        self.ace._on_ace_connect()
        self.assertFalse(self.ace._shared_bus_hb_outstanding)
        self.ace._shared_bus_heartbeat_tick(self.reactor.now)
        self.ace._shared_bus_hb_outstanding = True
        self.ace._shared_bus_hb_sent_at = self.reactor.now - 11   # too old -> allowed again
        self.mgr.clear_queues()
        self.ace._shared_bus_heartbeat_tick(self.reactor.now)
        self.assertEqual(self.mgr._hp_queue.qsize(), 1)

    def test_no_heartbeat_after_shutdown(self):
        self.mgr.shutdown()
        self.ace._shared_bus_heartbeat_tick(self.reactor.now)
        self.assertEqual(self.mgr._hp_queue.qsize(), 0)

    def test_enable_after_disabled_start_uses_protocol_baud(self):
        self.assertEqual(self.mgr.default_baud, int(self.ace.baud))
        self.mgr.default_baud = 230400
        self.mgr._ace_pro_enabled = False
        self.mgr._baud = None
        seen = []
        self.mgr.connect_to_ace = lambda baud, delay=2: seen.append(baud)
        self.mgr.enable_ace_pro()
        self.assertEqual(seen, [230400])


# ----------------------------------------------------------------------- C-24
class C24LaneSyncThreadTests(unittest.TestCase):
    def setUp(self):
        from ace.moonraker_lane_sync import MoonrakerLaneSyncAdapter
        self.printer, self.manager = make_manager()
        self.reactor = self.printer.reactor
        self.gcode = self.printer.gcode
        self.main = threading.current_thread()
        self.adapter = MoonrakerLaneSyncAdapter(
            self.gcode, self.manager, {"moonraker_lane_sync_enabled": True,
                                       "moonraker_lane_sync_post_print_retry_delay": 0.5})
        self.threads = {}
        self.done = threading.Event()

        orig_printing = self.adapter._is_printing_or_paused
        orig_build = self.adapter._build_lane_payload

        def printing():
            self.threads["printing"] = threading.current_thread()
            return orig_printing()

        def build():
            self.threads["build"] = threading.current_thread()
            return orig_build()

        def do_sync(force=False, reason="manual", lanes=None, printing=None):
            self.threads["http"] = threading.current_thread()
            self.threads["lanes"] = lanes
            self.threads["reason"] = reason
            self.done.set()
            return True
        self.adapter._is_printing_or_paused = printing
        self.adapter._build_lane_payload = build
        self.adapter._do_sync = do_sync

    def tearDown(self):
        self.adapter.shutdown()

    def test_klipper_objects_are_read_on_the_calling_thread_only(self):
        self.assertTrue(self.adapter.sync_now(reason="t"))
        self.assertTrue(self.done.wait(3))
        self.assertIs(self.threads["printing"], self.main)
        self.assertIs(self.threads["build"], self.main)
        self.assertIsNot(self.threads["http"], self.main)
        self.assertIn("lane1", self.threads["lanes"])   # snapshot handed to the worker

    def test_skip_during_print_then_retry_runs_as_a_reactor_timer(self):
        self.printer.print_stats.state = "printing"
        self.adapter.sync_now(reason="inv")
        self.assertFalse(self.done.wait(0.3))            # nothing sent during the print
        self.assertEqual(len(self.reactor.timers), 1)    # retry is a reactor timer, no thread
        (handle,) = list(self.reactor.timers)
        self.reactor.advance(1.0)
        self.reactor.fire(handle)                        # still printing -> keeps polling
        self.assertFalse(self.done.is_set())
        self.printer.print_stats.state = "complete"
        self.reactor.advance(1.0)
        self.assertEqual(self.reactor.fire(handle), self.reactor.NEVER)
        self.assertTrue(self.done.wait(3))
        self.assertEqual(self.threads["reason"], "inv_post_print")

    def test_force_syncs_even_during_a_print(self):
        self.printer.print_stats.state = "printing"
        self.adapter.sync_now(force=True, reason="end")
        self.assertTrue(self.done.wait(3))

    def test_console_messages_from_the_worker_go_through_the_reactor(self):
        self.adapter._notify("ACE: hello")
        self.assertNotIn("ACE: hello", self.gcode.info)   # not touched from this call
        self.reactor.run_async_callbacks()
        self.assertIn("ACE: hello", self.gcode.info)


if __name__ == "__main__":
    unittest.main()
