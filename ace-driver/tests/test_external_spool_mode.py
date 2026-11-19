"""
External spool mode: ACE support enabled but no ACE plugged in.

A printer without an ACE must behave like stock: T<n> and the print-end
unload are skipped (no tip cut, no slot cycling, no print cancel), the
standard runout sensor is handed back to Klipper, and the "no device"
retry messages do not flood the console.
"""

import unittest

try:
    from tests import _fakes
    from tests._fakes import FakeGcmd, make_manager, open_fake_port
except ImportError:  # run from inside tests/
    import _fakes
    from _fakes import FakeGcmd, make_manager, open_fake_port

import ace.commands as cmd_mod


def _absent_manager(print_state="standby", ready_ago=60.0):
    printer, mgr = make_manager(print_state=print_state, ace_present=False)
    mgr._ready_time = printer.reactor.now - ready_ago
    return printer, mgr


class ExternalSpoolDetectionTests(unittest.TestCase):
    def test_never_connected_after_grace_is_absent(self):
        _, mgr = _absent_manager()
        self.assertTrue(mgr.is_ace_absent())

    def test_startup_grace_is_not_absent(self):
        _, mgr = _absent_manager(ready_ago=2.0)
        self.assertFalse(mgr.is_ace_absent())

    def test_connected_is_not_absent(self):
        _, mgr = _absent_manager()
        open_fake_port(mgr.instances[0].serial_mgr)
        self.assertFalse(mgr.is_ace_absent())

    def test_disabled_ace_support_is_not_external_mode(self):
        _, mgr = _absent_manager()
        mgr._ace_pro_enabled = False
        self.assertFalse(mgr.is_ace_absent())

    def test_recent_drop_while_idle_is_not_absent_yet(self):
        printer, mgr = _absent_manager()
        mgr.instances[0].serial_mgr._last_connected_time = printer.reactor.now - 30.0
        self.assertFalse(mgr.is_ace_absent())

    def test_old_drop_while_idle_is_absent(self):
        printer, mgr = _absent_manager()
        mgr.instances[0].serial_mgr._last_connected_time = printer.reactor.now - 600.0
        self.assertTrue(mgr.is_ace_absent())

    def test_drop_during_print_keeps_ace_mode(self):
        # An ACE that was in use and drops mid-print must keep the pause path
        printer, mgr = _absent_manager(print_state="printing")
        mgr.instances[0].serial_mgr._last_connected_time = printer.reactor.now - 600.0
        mgr._external_spool_mode = False
        self.assertFalse(mgr.is_ace_absent())

    def test_mode_latched_while_idle_survives_print_start(self):
        printer, mgr = _absent_manager(print_state="printing")
        mgr.instances[0].serial_mgr._last_connected_time = printer.reactor.now - 600.0
        mgr._external_spool_mode = True
        self.assertTrue(mgr.is_ace_absent())

    def test_status_reports_mode(self):
        _, mgr = _absent_manager()
        status = mgr.get_status()
        self.assertFalse(status["ace_connected"])
        self.assertTrue(status["external_spool_mode"])


class ExternalSpoolCommandTests(unittest.TestCase):
    def test_tool_change_is_skipped_without_cut_or_cancel(self):
        printer, mgr = _absent_manager(print_state="printing")
        calls = []
        mgr.perform_tool_change = lambda *a, **k: calls.append(a)
        mgr.smart_unload = lambda *a, **k: calls.append(("unload", a))

        cmd_mod.cmd_ACE_CHANGE_TOOL_WRAPPER(FakeGcmd(printer.gcode, {"TOOL": 0}))

        self.assertEqual(calls, [])
        self.assertNotIn("PAUSE", printer.gcode.scripts)
        self.assertFalse(any("CUT_TIP" in s or "M104 S0" in s for s in printer.gcode.scripts))
        self.assertTrue(any("external spool mode" in m for m in printer.gcode.info))

    def test_unload_request_is_skipped(self):
        printer, mgr = _absent_manager()
        calls = []
        mgr.smart_unload = lambda *a, **k: calls.append(a)
        cmd_mod.cmd_ACE_CHANGE_TOOL_WRAPPER(FakeGcmd(printer.gcode, {"TOOL": -1}))
        self.assertEqual(calls, [])

    def test_print_end_does_not_unload(self):
        printer, mgr = _absent_manager()
        mgr.state.set("ace_current_index", 0)
        calls = []
        mgr.smart_unload = lambda *a, **k: calls.append(a)
        cmd_mod.cmd_ACE_HANDLE_PRINT_END(FakeGcmd(printer.gcode, {}))
        self.assertEqual(calls, [])

    def test_tool_change_still_runs_with_ace_connected(self):
        printer, mgr = _absent_manager()
        open_fake_port(mgr.instances[0].serial_mgr)
        calls = []
        mgr.perform_tool_change = lambda cur, tgt, *a, **k: calls.append(tgt) or "ok"
        cmd_mod.cmd_ACE_CHANGE_TOOL_WRAPPER(FakeGcmd(printer.gcode, {"TOOL": 1}))
        self.assertEqual(calls, [1])


class ExternalSpoolSensorTests(unittest.TestCase):
    def test_standard_sensor_restored_and_reclaimed(self):
        printer, mgr = _absent_manager()
        restored, disabled = [], []
        mgr._restore_sensors = lambda: restored.append(1)
        mgr._disable_all_sensor_detection = lambda: disabled.append(1)

        mgr._health_monitor.update_external_spool_mode()
        self.assertEqual(restored, [1])
        self.assertTrue(mgr._external_spool_mode)

        # Same state again: no repeated toggling
        mgr._health_monitor.update_external_spool_mode()
        self.assertEqual(restored, [1])

        open_fake_port(mgr.instances[0].serial_mgr)
        mgr._health_monitor.update_external_spool_mode()
        self.assertEqual(disabled, [1])
        self.assertFalse(mgr._external_spool_mode)

    def test_runout_monitor_idles_without_ace(self):
        printer, mgr = _absent_manager(print_state="printing")
        mgr.state.set("ace_current_index", 0)
        nxt = mgr.runout_monitor._monitor_runout(printer.reactor.now)
        self.assertEqual(nxt, printer.reactor.now + 1.0)
        self.assertFalse(any("Print state changed" in m for m in printer.gcode.info))


class QuietRetryTests(unittest.TestCase):
    def test_missing_device_reported_once(self):
        printer, mgr = _absent_manager()
        serial_mgr = mgr.instances[0].serial_mgr
        serial_mgr.find_connection_port = lambda instance: None
        for _ in range(5):
            self.assertFalse(serial_mgr.auto_connect(0, 115200))
        hits = [m for m in printer.gcode.info if "No ACE device found" in m]
        self.assertEqual(len(hits), 1)


if __name__ == "__main__":
    unittest.main()
