"""
Tool map: the screen routes a print's file tools to the ACE slots that hold
the right filament (T0 in a single-colour file -> the loaded PLA slot), so a
file sliced for T0 does not wait on an empty slot 1.

The map lives for one print: set while idle, active while printing, cleared
when the print ends, is cancelled or never starts.
"""

import unittest

try:
    from tests._fakes import FakeGcmd, make_manager, open_fake_port
    from tests.test_extrusion_guard import FakeGcodeMove, FakeToolhead
except ImportError:  # run from inside tests/
    from _fakes import FakeGcmd, make_manager, open_fake_port
    from test_extrusion_guard import FakeGcodeMove, FakeToolhead

import ace.commands as cmd_mod


def _manager(print_state="standby"):
    printer, mgr = make_manager(print_state=print_state)
    open_fake_port(mgr.instances[0].serial_mgr)
    return printer, mgr


class ToolMapCommandTests(unittest.TestCase):
    def test_set_and_status(self):
        printer, mgr = _manager()
        cmd_mod.cmd_ACE_SET_TOOL_MAP(FakeGcmd(printer.gcode, {"MAP": "0:3, 1:2"}))
        self.assertEqual(mgr.get_tool_map(), {0: 3, 1: 2})
        self.assertEqual(mgr.get_status()["tool_map"], {"0": 3, "1": 2})

    def test_out_of_range_is_rejected(self):
        printer, mgr = _manager()
        cmd_mod.cmd_ACE_SET_TOOL_MAP(FakeGcmd(printer.gcode, {"MAP": "0:9"}))
        self.assertEqual(mgr.get_tool_map(), {})

    def test_bad_entry_is_rejected(self):
        printer, mgr = _manager()
        cmd_mod.cmd_ACE_SET_TOOL_MAP(FakeGcmd(printer.gcode, {"MAP": "0-3"}))
        self.assertEqual(mgr.get_tool_map(), {})

    def test_clear(self):
        printer, mgr = _manager()
        mgr.set_tool_map({0: 3})
        cmd_mod.cmd_ACE_SET_TOOL_MAP(FakeGcmd(printer.gcode, {"CLEAR": 1}))
        self.assertEqual(mgr.get_tool_map(), {})


class ToolMapToolChangeTests(unittest.TestCase):
    def test_file_tool_is_routed_to_mapped_slot(self):
        printer, mgr = _manager(print_state="printing")
        mgr.set_tool_map({0: 3})
        calls = []
        mgr.perform_tool_change = lambda cur, tgt, *a, **k: calls.append(tgt) or "ok"
        cmd_mod.cmd_ACE_CHANGE_TOOL_WRAPPER(FakeGcmd(printer.gcode, {"TOOL": 0}))
        self.assertEqual(calls, [3])
        self.assertTrue(any("T0 -> T3" in m for m in printer.gcode.info))

    def test_unmapped_tool_is_unchanged(self):
        printer, mgr = _manager(print_state="printing")
        mgr.set_tool_map({0: 3})
        calls = []
        mgr.perform_tool_change = lambda cur, tgt, *a, **k: calls.append(tgt) or "ok"
        cmd_mod.cmd_ACE_CHANGE_TOOL_WRAPPER(FakeGcmd(printer.gcode, {"TOOL": 1}))
        self.assertEqual(calls, [1])

    def test_unload_is_not_mapped(self):
        printer, mgr = _manager(print_state="printing")
        mgr.set_tool_map({0: 3})
        mgr.state.set("ace_current_index", 3)
        unloads = []
        mgr.smart_unload = lambda tool, *a, **k: unloads.append(tool) or True
        cmd_mod.cmd_ACE_CHANGE_TOOL_WRAPPER(FakeGcmd(printer.gcode, {"TOOL": -1}))
        self.assertEqual(unloads, [3])


class ToolMapLifecycleTests(unittest.TestCase):
    def test_map_survives_until_print_ends(self):
        printer, mgr = _manager()
        mgr.set_tool_map({0: 3})
        mgr.update_tool_map_lifecycle()  # idle, pending
        self.assertEqual(mgr.get_tool_map(), {0: 3})
        printer.print_stats.state = "printing"
        mgr.update_tool_map_lifecycle()
        printer.print_stats.state = "paused"
        mgr.update_tool_map_lifecycle()
        self.assertEqual(mgr.get_tool_map(), {0: 3})
        printer.print_stats.state = "cancelled"
        mgr.update_tool_map_lifecycle()
        self.assertEqual(mgr.get_tool_map(), {})

    def test_pending_map_expires_when_no_print_starts(self):
        printer, mgr = _manager()
        mgr.set_tool_map({0: 3})
        printer.reactor.now += mgr.TOOL_MAP_PENDING_S + 1
        mgr.update_tool_map_lifecycle()
        self.assertEqual(mgr.get_tool_map(), {})

    def test_map_set_after_previous_print_is_kept_until_next_starts(self):
        # print_stats still says "complete" from the last print when the screen sends the map
        printer, mgr = _manager(print_state="complete")
        mgr.set_tool_map({0: 2})
        mgr.update_tool_map_lifecycle()
        self.assertEqual(mgr.get_tool_map(), {0: 2})

    def test_print_end_clears_map(self):
        printer, mgr = _manager(print_state="printing")
        mgr.set_tool_map({0: 3})
        mgr.smart_unload = lambda *a, **k: True
        cmd_mod.cmd_ACE_HANDLE_PRINT_END(FakeGcmd(printer.gcode, {"CUT_TIP": 0}))
        self.assertEqual(mgr.get_tool_map(), {})

    def test_health_tick_runs_lifecycle(self):
        printer, mgr = _manager(print_state="printing")
        mgr.set_tool_map({0: 3})
        mgr._health_monitor._monitor_ace_state(printer.reactor.now)
        printer.print_stats.state = "complete"
        mgr._health_monitor._monitor_ace_state(printer.reactor.now)
        self.assertEqual(mgr.get_tool_map(), {})


class SpoolWaitTests(unittest.TestCase):
    """An empty slot must not hold Klipper's G-code queue for minutes (Stop/Pause queue behind it)."""

    def test_tool_change_waits_only_briefly(self):
        import ace.toolchange as tc
        printer, mgr = _manager(print_state="printing")
        printer.add_object("toolhead", FakeToolhead())
        printer.add_object("gcode_move", FakeGcodeMove())
        seen = []
        mgr.check_and_wait_for_spool_ready = lambda tool, **kw: seen.append(kw.get("timeout_s")) or False
        with self.assertRaises(Exception) as ctx:
            mgr.perform_tool_change(-1, 3)
        self.assertEqual(seen, [tc.SPOOL_READY_WAIT_S])
        self.assertLessEqual(tc.SPOOL_READY_WAIT_S, 20)
        self.assertIn("slot 4", str(ctx.exception))

    def test_empty_slot_wait_returns_after_timeout(self):
        printer, mgr = _manager(print_state="printing")
        inst = mgr.instances[0]
        inst._info["status"] = "ready"
        inst._info["slots"] = [{"status": "empty"} for _ in range(4)]
        start = printer.reactor.now
        self.assertFalse(mgr.check_and_wait_for_spool_ready(0, timeout_s=15.0))
        self.assertLess(printer.reactor.now - start, 20.0)


class RetryUsesFileToolTests(unittest.TestCase):
    def test_retry_button_names_the_file_tool(self):
        printer, mgr = _manager(print_state="printing")
        mgr.set_tool_map({0: 3, 3: 0})

        def fail(cur, tgt, *a, **k):
            raise Exception("no filament")
        mgr.perform_tool_change = fail
        cmd_mod.cmd_ACE_CHANGE_TOOL_WRAPPER(FakeGcmd(printer.gcode, {"TOOL": 0}))
        retry = [s for s in printer.gcode.scripts if "Retry" in s]
        self.assertTrue(retry)
        self.assertIn("Retry T0|T0", retry[0])
        self.assertIn("PAUSE", printer.gcode.scripts)


if __name__ == "__main__":
    unittest.main()
