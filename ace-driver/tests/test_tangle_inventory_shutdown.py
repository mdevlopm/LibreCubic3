"""Tangle protection, inventory spam, shutdown handling, protocol + config."""

import unittest

import _fakes
from _fakes import (FakeGcmd, FakeConfig, FakePrinter, make_manager, open_fake_port,
                    status_result)

from ace import commands, config as ace_config_mod
from ace.protocol_ace1 import AceJsonProtocolAdapter


def heartbeat(ace, **kw):
    ace._on_heartbeat_response({"code": 0, "result": status_result(**kw)})
    ace.reactor.run_callbacks()  # register_callback bodies run after the event returns


class TangleTests(unittest.TestCase):
    def build(self, overrides=None, state="printing"):
        self.printer, self.manager = make_manager(overrides, print_state=state)
        self.ace = self.manager.instances[0]
        self.gcode = self.printer.gcode

    def pauses(self):
        return [s for s in self.gcode.scripts if s == "PAUSE"]

    def test_default_is_off_and_never_pauses(self):
        self.build()
        self.assertEqual(self.manager.get_tangle_protection(),
                         {"enabled": False, "threshold": 8.0, "source": "config"})
        for _ in range(5):
            heartbeat(self.ace, cont_assist_time=50.0)
        self.assertEqual(self.pauses(), [])
        self.assertEqual(self.gcode.raw, [])

    def test_enabled_by_config_pauses_at_threshold(self):
        self.build({"tangle_protection": True})
        heartbeat(self.ace, cont_assist_time=7.9)
        self.assertEqual(self.pauses(), [])
        heartbeat(self.ace, cont_assist_time=8.0)
        self.assertEqual(self.pauses(), ["PAUSE"])
        self.assertEqual(len(self.gcode.raw), 1)
        self.assertTrue(self.gcode.raw[0].startswith("!! ACE[0]: Filament tangle suspected"))
        self.assertIn("8.0s >= 8.0s", self.gcode.raw[0])

    def test_custom_config_threshold(self):
        self.build({"tangle_protection": True, "tangle_protection_threshold": 3.5})
        heartbeat(self.ace, cont_assist_time=3.4)
        self.assertEqual(self.pauses(), [])
        heartbeat(self.ace, cont_assist_time=3.6)
        self.assertEqual(self.pauses(), ["PAUSE"])

    def test_triggers_once_per_excursion_and_rearms(self):
        self.build({"tangle_protection": True})
        for v in (9.0, 10.0, 11.0):
            heartbeat(self.ace, cont_assist_time=v)
        self.assertEqual(len(self.pauses()), 1)
        heartbeat(self.ace, cont_assist_time=0.0)  # recovered
        heartbeat(self.ace, cont_assist_time=9.5)
        self.assertEqual(len(self.pauses()), 2)
        self.assertEqual(self.ace.get_tangle_status()["event_count"], 2)

    def test_not_while_idle_or_paused(self):
        self.build({"tangle_protection": True}, state="standby")
        heartbeat(self.ace, cont_assist_time=30.0)
        self.printer.print_stats.state = "paused"
        heartbeat(self.ace, cont_assist_time=30.0)
        self.assertEqual(self.pauses(), [])

    def test_not_during_toolchange_or_shutdown(self):
        self.build({"tangle_protection": True})
        self.manager.toolchange_in_progress = True
        heartbeat(self.ace, cont_assist_time=30.0)
        self.assertEqual(self.pauses(), [])
        self.manager.toolchange_in_progress = False
        self.manager._shutdown_in_progress = True
        heartbeat(self.ace, cont_assist_time=30.0)
        self.assertEqual(self.pauses(), [])

    def test_missing_value_is_ignored(self):
        self.build({"tangle_protection": True})
        result = status_result()
        result.pop("cont_assist_time")
        self.ace._on_heartbeat_response({"code": 0, "result": result})
        self.assertEqual(self.pauses(), [])

    def test_pause_runs_via_reactor_callback_not_inline(self):
        self.build({"tangle_protection": True})
        self.ace._on_heartbeat_response({"code": 0, "result": status_result(cont_assist_time=20.0)})
        self.assertEqual(self.pauses(), [])          # deferred
        self.assertEqual(len(self.gcode.raw), 1)     # event already reported
        self.printer.reactor.run_callbacks()
        self.assertEqual(self.pauses(), ["PAUSE"])

    def test_pause_failure_is_reported(self):
        self.build({"tangle_protection": True})
        self.gcode.fail_scripts.add("PAUSE")
        heartbeat(self.ace, cont_assist_time=20.0)
        self.assertTrue(any("Could not pause print after tangle" in m for m in self.gcode.raw))

    def test_runtime_command_enable_and_threshold_persist(self):
        self.build()
        commands.cmd_ACE_TANGLE_PROTECTION(FakeGcmd(self.gcode, {"ENABLE": 1, "THRESHOLD": 5}))
        self.assertEqual(self.manager.get_tangle_protection(),
                         {"enabled": True, "threshold": 5.0, "source": "saved"})
        saved = self.printer.save_variables.allVariables
        self.assertIs(saved["ace_tangle_protection_enabled"], True)
        self.assertEqual(saved["ace_tangle_protection_threshold"], 5.0)
        self.assertIn("ace_tangle_protection_enabled", self.manager.state._dirty)
        self.assertIn("ACE: Tangle protection: enabled=1 threshold=5.0s (source=saved)", self.gcode.info)
        heartbeat(self.ace, cont_assist_time=5.5)
        self.assertEqual(len(self.pauses()), 1)

    def test_runtime_disable_overrides_config_enable(self):
        self.build({"tangle_protection": True})
        commands.cmd_ACE_TANGLE_PROTECTION(FakeGcmd(self.gcode, {"ENABLE": 0}))
        heartbeat(self.ace, cont_assist_time=30.0)
        self.assertEqual(self.pauses(), [])

    def test_status_without_parameters_changes_nothing(self):
        self.build()
        commands.cmd_ACE_TANGLE_PROTECTION(FakeGcmd(self.gcode, {}))
        self.assertNotIn("ace_tangle_protection_enabled", self.printer.save_variables.allVariables)
        self.assertNotIn("ace_tangle_protection_threshold", self.printer.save_variables.allVariables)
        self.assertIn("enabled=0 threshold=8.0s (source=config)", self.gcode.info[-1])

    def test_reset_returns_to_config(self):
        self.build({"tangle_protection": False})
        commands.cmd_ACE_TANGLE_PROTECTION(FakeGcmd(self.gcode, {"ENABLE": 1}))
        commands.cmd_ACE_TANGLE_PROTECTION(FakeGcmd(self.gcode, {"RESET": 1}))
        self.assertEqual(self.manager.get_tangle_protection()["source"], "config")
        self.assertFalse(self.manager.get_tangle_protection()["enabled"])
        self.assertTrue(any("SAVE_VARIABLE VARIABLE=ace_tangle_protection_enabled VALUE=None" == s
                            for s in self.gcode.scripts))

    def test_saved_none_value_falls_back_to_config(self):
        self.build({"tangle_protection": True})
        self.printer.save_variables.allVariables["ace_tangle_protection_enabled"] = None
        self.assertTrue(self.manager.get_tangle_protection()["enabled"])

    def test_threshold_out_of_range_is_error_line(self):
        self.build()
        commands.cmd_ACE_TANGLE_PROTECTION(FakeGcmd(self.gcode, {"THRESHOLD": 0.1}))
        self.assertTrue(self.gcode.raw[0].startswith("!! ACE_TANGLE_PROTECTION error: threshold must be between"))
        commands.cmd_ACE_TANGLE_PROTECTION(FakeGcmd(self.gcode, {"ENABLE": 2}))
        self.assertEqual(len(self.gcode.raw), 2)

    def test_status_exposed_for_screen(self):
        self.build({"tangle_protection": True})
        self.assertEqual(self.manager.get_status()["tangle_protection"]["enabled"], True)
        st = self.ace.get_status()["tangle_protection"]
        self.assertEqual(set(st), {"enabled", "threshold", "triggered", "event_count", "last_event"})
        heartbeat(self.ace, cont_assist_time=9.0)
        st = self.ace.get_status()["tangle_protection"]
        self.assertTrue(st["triggered"])
        self.assertEqual(st["last_event"]["cont_assist_time"], 9.0)

    def test_command_registered(self):
        names = [n for n, _, _ in commands.ACE_COMMANDS]
        self.assertIn("ACE_TANGLE_PROTECTION", names)
        self.assertIn("ACE_SET_FAN_SPEED", names)


class InventorySpamTests(unittest.TestCase):
    def build(self, state="standby"):
        self.printer, self.manager = make_manager(print_state=state)
        self.ace = self.manager.instances[0]
        self.calls = []
        self.manager.state._dirty.clear()  # constructor-time writes are not what is tested
        orig = self.manager._sync_inventory_to_persistent

        def spy(instance_num=None, flush=True):
            self.calls.append(instance_num)
            return orig(instance_num, flush=flush)
        self.manager._sync_inventory_to_persistent = spy

    def test_empty_slots_do_not_dirty_inventory_every_heartbeat(self):
        self.build()
        for _ in range(10):
            heartbeat(self.ace)  # all four slots empty
        self.assertEqual(self.calls, [])
        self.assertFalse(self.manager.state.has_pending)

    def test_stale_metadata_is_cleared_and_persisted_exactly_once(self):
        self.build()
        self.ace.inventory[2].update({"status": "ready", "material": "PLA", "color": [1, 2, 3], "temp": 200})
        heartbeat(self.ace, slot_states=["empty", "empty", "empty", "empty"])
        self.assertEqual(self.calls, [0])
        self.assertEqual(self.ace.inventory[2]["material"], "")
        for _ in range(5):
            heartbeat(self.ace)
        self.assertEqual(self.calls, [0])

    def test_leftover_material_on_empty_slot_triggers_once(self):
        self.build()
        self.ace.inventory[1].update({"status": "empty", "material": "PETG", "color": [0, 0, 0], "temp": 0})
        heartbeat(self.ace)
        heartbeat(self.ace)
        self.assertEqual(self.calls, [0])

    def test_leftover_rfid_flag_triggers_once(self):
        self.build()
        self.ace.inventory[3].update({"status": "empty", "rfid": True})
        heartbeat(self.ace)
        heartbeat(self.ace)
        self.assertEqual(self.calls, [0])
        self.assertFalse(self.ace.inventory[3]["rfid"])

    def test_status_transition_still_persists(self):
        self.build()
        heartbeat(self.ace, slot_states=["ready", "empty", "empty", "empty"])
        self.assertEqual(self.calls, [0])

    def test_printing_keeps_metadata_of_empty_slot(self):
        self.build(state="printing")
        self.ace.inventory[0].update({"status": "ready", "material": "PLA", "color": [9, 9, 9], "temp": 200})
        heartbeat(self.ace, slot_states=["empty", "empty", "empty", "empty"])  # runout
        self.assertEqual(self.ace.inventory[0]["material"], "PLA")  # preserved for endless spool


class ShutdownTests(unittest.TestCase):
    def build(self, **kw):
        self.printer, self.manager = make_manager(**kw)
        self.ace = self.manager.instances[0]
        self.reactor = self.printer.reactor
        self.mgr = self.ace.serial_mgr
        self.ser = open_fake_port(self.mgr)
        self.manager._start_monitoring()

    def saved(self):
        return self.printer.save_variables.allVariables

    def test_shutdown_event_quiets_serial_and_timers(self):
        self.build()
        self.mgr.start_heartbeat()
        self.mgr.send_request({"method": "a"}, lambda response=None: None)
        self.assertTrue(self.reactor.timers)
        self.printer.send_event("klippy:shutdown")
        self.assertTrue(self.mgr.is_shutdown())
        self.assertTrue(self.ser.closed)
        self.assertEqual(self.reactor.timers, {})
        self.assertFalse(self.mgr.has_pending_requests())
        self.assertTrue(self.manager._shutdown_in_progress)

    def test_shutdown_sends_nothing_to_the_ace(self):
        self.build()
        self.printer.send_event("klippy:shutdown")
        self.assertEqual(self.ser.written, [])

    def test_ace_global_enabled_survives_shutdown_even_if_pin_reads_zero(self):
        self.build()
        self.saved()["ace_global_enabled"] = True
        self.printer.send_event("klippy:shutdown")
        self.printer.pin.value = 0.0           # what a shutdown_value:0 would do
        self.manager._monitor_ace_state(self.reactor.now)
        self.manager.update_ace_support_active_state()
        self.assertTrue(self.manager.get_ace_global_enabled())
        self.assertTrue(self.manager._ace_pro_enabled)
        self.assertTrue(self.saved()["ace_global_enabled"])

    def test_klipper_shutdown_state_alone_also_blocks_flag_write(self):
        self.build()
        self.saved()["ace_global_enabled"] = True
        self.printer.shutdown_state = True
        self.printer.pin.value = 0.0
        self.manager.update_ace_support_active_state()
        self.assertTrue(self.saved()["ace_global_enabled"])
        self.assertTrue(self.manager._ace_pro_enabled)

    def test_user_disable_via_pin_still_works_outside_shutdown(self):
        self.build()
        self.printer.pin.value = 0.0
        self.manager.update_ace_support_active_state()
        self.assertFalse(self.saved()["ace_global_enabled"])
        self.assertFalse(self.manager._ace_pro_enabled)

    def test_no_reconnect_after_shutdown_from_monitor(self):
        self.build()
        self.printer.send_event("klippy:shutdown")
        n = len(_fakes.FakeSerial.instances)
        self.manager._monitor_ace_state(self.reactor.now)
        self.manager._monitor_transport_reconnects()
        self.assertIsNone(self.mgr.connect_timer)
        self.assertEqual(len(_fakes.FakeSerial.instances), n)

    def test_dirty_state_is_flushed_directly_on_shutdown(self):
        self.build()
        self.manager.state.set("ace_current_index", 2)
        calls = []
        self.manager.state.flush_direct = lambda: calls.append("direct")
        self.printer.send_event("klippy:shutdown")
        self.assertEqual(calls, ["direct"])

    def test_disconnect_after_shutdown_skips_gcode_flush_and_is_clean(self):
        self.build()
        self.printer.send_event("klippy:shutdown")
        flushed = []
        self.manager.state.flush = lambda: flushed.append(1)
        self.printer.send_event("klippy:disconnect")  # FIRMWARE_RESTART path
        self.assertEqual(flushed, [])

    def test_disconnect_without_shutdown_still_flushes(self):
        self.build()
        flushed = []
        self.manager.state.flush = lambda: flushed.append(1)
        self.printer.send_event("klippy:disconnect")
        self.assertEqual(flushed, [1])

    def test_fresh_manager_after_restart_can_connect(self):
        self.build()
        self.printer.send_event("klippy:shutdown")
        printer2, manager2 = make_manager()
        mgr2 = manager2.instances[0].serial_mgr
        self.assertFalse(mgr2.is_shutdown())
        mgr2.connect_to_ace(115200, delay=0)
        self.assertIsNotNone(mgr2.connect_timer)


class ProtocolAndConfigTests(unittest.TestCase):
    def test_ace1_drying_request(self):
        p = AceJsonProtocolAdapter()
        # default: no fan_speed field (ACE's own default, the proven behaviour)
        self.assertEqual(p.build_start_drying_request(50, 120),
                         {"method": "drying", "params": {"temp": 50, "duration": 120}})
        self.assertEqual(p.build_start_drying_request(50, 120, fan_speed=7000),
                         {"method": "drying", "params": {"temp": 50, "fan_speed": 7000, "duration": 120}})
        self.assertEqual(p.build_start_drying_request(50, 120, fan_speed=3000)["params"]["fan_speed"], 3000)
        self.assertNotIn("fan_speed", p.build_start_drying_request(50, 120, fan_speed=0)["params"])
        self.assertNotIn("fan_speed", p.build_start_drying_request(50, 120, fan_speed=None)["params"])

    def test_ace1_set_fan_speed_request(self):
        p = AceJsonProtocolAdapter()
        self.assertEqual(p.build_set_fan_speed_request(6000),
                         {"method": "set_fan_speed", "params": {"fan_speed": 6000}})

    def test_ace1_stop_drying_unchanged(self):
        self.assertEqual(AceJsonProtocolAdapter().build_stop_drying_request(), {"method": "drying_stop"})

    def test_ace2_ignores_fan_speed_and_rejects_set_fan_speed(self):
        from ace.protocol_ace2 import AceProtoProtocolAdapter as Adapter2
        p = Adapter2()
        with_fan = p.build_start_drying_request(50, 60, fan_speed=7000)
        without = p.build_start_drying_request(50, 60)
        self.assertEqual(with_fan, without)
        with self.assertRaises(NotImplementedError):
            p.build_set_fan_speed_request(5000)

    def test_config_defaults(self):
        cfg = ace_config_mod.read_ace_config(FakeConfig(FakePrinter()))
        self.assertFalse(cfg["tangle_protection"])
        self.assertEqual(cfg["tangle_protection_threshold"], 8.0)
        self.assertEqual(cfg["dryer_fan_speed"], 0)

    def test_serial_tuning_defaults_and_overrides(self):
        cfg = ace_config_mod.read_ace_config(FakeConfig(FakePrinter()))
        self.assertEqual(cfg["serial_request_timeout"], 1.0)
        self.assertEqual(cfg["comm_timeout_threshold"], 5)
        _, manager = make_manager()
        mgr = manager.instances[0].serial_mgr
        self.assertEqual(mgr.timeout_s, 1.0)
        self.assertEqual(mgr.COMM_TIMEOUT_THRESHOLD, 5)
        _, manager = make_manager({"serial_request_timeout": 2.5, "comm_timeout_threshold": 9})
        mgr = manager.instances[0].serial_mgr
        self.assertEqual(mgr.timeout_s, 2.5)
        self.assertEqual(mgr.COMM_TIMEOUT_THRESHOLD, 9)

    def test_config_overrides(self):
        cfg = ace_config_mod.read_ace_config(FakeConfig(FakePrinter(), {
            "tangle_protection": True, "tangle_protection_threshold": 6.5, "dryer_fan_speed": 0}))
        self.assertTrue(cfg["tangle_protection"])
        self.assertEqual(cfg["tangle_protection_threshold"], 6.5)
        self.assertEqual(cfg["dryer_fan_speed"], 0)


if __name__ == "__main__":
    unittest.main()
