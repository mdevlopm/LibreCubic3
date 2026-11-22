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


def _set_slots(mgr, specs):
    """Populate instance 0 inventory from [(status, material, color), ...]."""
    inst = mgr.instances[0]
    for idx, (status, material, color) in enumerate(specs):
        inst.inventory[idx] = {
            "status": status,
            "material": material,
            "color": list(color),
            "temp": 200 if status == "ready" else 0,
            "rfid": bool(material),
        }


class AutoToolMapUploadAndPrintTests(unittest.TestCase):
    """Upload-and-print (external print) automatic slot matching before first T<n>."""

    def _setup_print(self, slots, meta, print_state="printing", filename="box.gcode"):
        printer, mgr = _manager(print_state=print_state)
        printer.print_stats.filename = filename
        _set_slots(mgr, slots)
        fetches = []

        def fetch(fname):
            fetches.append(fname)
            return dict(meta)

        mgr._auto_tool_mapper._metadata_fetcher = fetch
        calls = []
        mgr.perform_tool_change = lambda cur, tgt, *a, **k: calls.append(tgt) or "ok"
        return printer, mgr, calls, fetches

    def test_slots_already_match_does_not_set_map(self):
        # Slot 0 already holds PLA; even though slot 3 has a closer colour, do nothing.
        slots = [
            ("ready", "PLA", (255, 255, 255)),
            ("empty", "", (0, 0, 0)),
            ("ready", "PETG", (40, 170, 80)),
            ("ready", "PLA", (0, 193, 174)),
        ]
        meta = {"referenced_tools": [0], "filament_type": "PLA", "filament_colors": ["#00C1AE"]}
        printer, mgr, calls, fetches = self._setup_print(slots, meta)
        cmd_mod.cmd_ACE_CHANGE_TOOL_WRAPPER(FakeGcmd(printer.gcode, {"TOOL": 0}))
        self.assertEqual(mgr.get_tool_map(), {})
        self.assertEqual(calls, [0])
        self.assertEqual(fetches, ["box.gcode"])
        self.assertFalse(any("Otomatik yuva eşleştirme" in m for m in printer.gcode.info))

    def test_empty_slot_triggers_auto_map_before_first_toolchange(self):
        # Classic Upload-and-Print case: T0 sliced as PLA, slot 0 empty, slot 3 holds PLA.
        slots = [
            ("empty", "", (0, 0, 0)),
            ("empty", "", (0, 0, 0)),
            ("ready", "PETG", (40, 170, 80)),
            ("ready", "PLA", (245, 245, 245)),
        ]
        meta = {"referenced_tools": [0], "filament_type": "PLA", "filament_colors": ["#FFFFFF"]}
        printer, mgr, calls, _ = self._setup_print(slots, meta)
        cmd_mod.cmd_ACE_CHANGE_TOOL_WRAPPER(FakeGcmd(printer.gcode, {"TOOL": 0}))
        self.assertEqual(mgr.get_tool_map(), {0: 3})
        self.assertEqual(calls, [3])
        self.assertTrue(any("Otomatik yuva eşleştirme: T0→4" in m for m in printer.gcode.info))

    def test_material_mismatch_triggers_auto_map(self):
        # Slot 0 has PETG while file wants PLA; slot 2 has PLA -> auto-map T0 -> slot 3 (index 2).
        slots = [
            ("ready", "PETG", (40, 170, 80)),
            ("empty", "", (0, 0, 0)),
            ("ready", "PLA", (245, 245, 245)),
            ("empty", "", (0, 0, 0)),
        ]
        meta = {"referenced_tools": [0], "filament_type": "PLA"}
        printer, mgr, calls, _ = self._setup_print(slots, meta)
        cmd_mod.cmd_ACE_CHANGE_TOOL_WRAPPER(FakeGcmd(printer.gcode, {"TOOL": 0}))
        self.assertEqual(mgr.get_tool_map(), {0: 2})
        self.assertEqual(calls, [2])
        self.assertTrue(any("Otomatik yuva eşleştirme: T0→3" in m for m in printer.gcode.info))

    def test_screen_map_is_never_overwritten_or_doubled(self):
        # Screen sent ACE_SET_TOOL_MAP before print_start (even an override to PETG slot 2).
        slots = [
            ("empty", "", (0, 0, 0)),
            ("empty", "", (0, 0, 0)),
            ("ready", "PETG", (40, 170, 80)),
            ("ready", "PLA", (245, 245, 245)),
        ]
        meta = {"referenced_tools": [0], "filament_type": "PLA"}
        printer, mgr, calls, fetches = self._setup_print(slots, meta, print_state="standby")
        cmd_mod.cmd_ACE_SET_TOOL_MAP(FakeGcmd(printer.gcode, {"MAP": "0:2"}))
        printer.print_stats.state = "printing"
        cmd_mod.cmd_ACE_CHANGE_TOOL_WRAPPER(FakeGcmd(printer.gcode, {"TOOL": 0}))
        self.assertEqual(mgr.get_tool_map(), {0: 2})
        self.assertEqual(calls, [2])
        self.assertEqual(fetches, [])
        self.assertFalse(any("Otomatik yuva eşleştirme" in m for m in printer.gcode.info))

    def test_multicolour_maps_all_tools_once_and_reuses_for_later_tools(self):
        # T0 wants PLA, T1 wants PETG; slot 0 holds PETG, slot 1 empty, slot 2 holds PLA -> T0->2 (3), T1->0 (1).
        slots = [
            ("ready", "PETG", (40, 170, 80)),
            ("empty", "", (0, 0, 0)),
            ("ready", "PLA", (245, 245, 245)),
            ("empty", "", (0, 0, 0)),
        ]
        meta = {"referenced_tools": [0, 1], "filament_type": "PLA;PETG"}
        printer, mgr, calls, fetches = self._setup_print(slots, meta)
        cmd_mod.cmd_ACE_CHANGE_TOOL_WRAPPER(FakeGcmd(printer.gcode, {"TOOL": 0}))
        cmd_mod.cmd_ACE_CHANGE_TOOL_WRAPPER(FakeGcmd(printer.gcode, {"TOOL": 1}))
        self.assertEqual(mgr.get_tool_map(), {0: 2, 1: 0})
        self.assertEqual(calls, [2, 0])
        self.assertEqual(fetches, ["box.gcode"])
        self.assertTrue(any("Otomatik yuva eşleştirme: T0→3, T1→1" in m for m in printer.gcode.info))

    def test_insufficient_loaded_slots_preserves_existing_behaviour(self):
        # File needs ABS; no ABS slot exists -> do not set map, fall through to normal T0 path.
        slots = [
            ("empty", "", (0, 0, 0)),
            ("empty", "", (0, 0, 0)),
            ("ready", "PETG", (40, 170, 80)),
            ("ready", "PLA", (245, 245, 245)),
        ]
        meta = {"referenced_tools": [0], "filament_type": "ABS"}
        printer, mgr, calls, _ = self._setup_print(slots, meta)
        cmd_mod.cmd_ACE_CHANGE_TOOL_WRAPPER(FakeGcmd(printer.gcode, {"TOOL": 0}))
        self.assertEqual(mgr.get_tool_map(), {})
        self.assertEqual(calls, [0])
        self.assertFalse(any("Otomatik yuva eşleştirme" in m for m in printer.gcode.info))

    def test_idle_manual_toolchange_never_auto_maps(self):
        slots = [
            ("empty", "", (0, 0, 0)),
            ("empty", "", (0, 0, 0)),
            ("empty", "", (0, 0, 0)),
            ("ready", "PLA", (245, 245, 245)),
        ]
        meta = {"referenced_tools": [0], "filament_type": "PLA"}
        printer, mgr, calls, fetches = self._setup_print(slots, meta, print_state="standby")
        cmd_mod.cmd_ACE_CHANGE_TOOL_WRAPPER(FakeGcmd(printer.gcode, {"TOOL": 0}))
        self.assertEqual(mgr.get_tool_map(), {})
        self.assertEqual(calls, [0])
        self.assertEqual(fetches, [])

    def test_unknown_material_in_direct_slot_is_not_remapped(self):
        # Non-RFID spool in slot 0 ("Unknown") should stay on slot 0 rather than stealing RFID PLA in slot 3.
        slots = [
            ("ready", "Unknown", (0, 0, 0)),
            ("empty", "", (0, 0, 0)),
            ("empty", "", (0, 0, 0)),
            ("ready", "PLA", (245, 245, 245)),
        ]
        meta = {"referenced_tools": [0], "filament_type": "PLA"}
        printer, mgr, calls, _ = self._setup_print(slots, meta)
        cmd_mod.cmd_ACE_CHANGE_TOOL_WRAPPER(FakeGcmd(printer.gcode, {"TOOL": 0}))
        self.assertEqual(mgr.get_tool_map(), {})
        self.assertEqual(calls, [0])

    def test_prefetch_during_g9111_and_re_evaluation_on_next_print(self):
        slots = [
            ("empty", "", (0, 0, 0)),
            ("empty", "", (0, 0, 0)),
            ("empty", "", (0, 0, 0)),
            ("ready", "PLA", (245, 245, 245)),
        ]
        meta = {"referenced_tools": [0], "filament_type": "PLA"}
        printer, mgr, calls, fetches = self._setup_print(slots, meta)
        # Simulate 2 s health tick while G9111 is heating/homing (prefetch runs in background)
        mgr.update_tool_map_lifecycle()
        entry = mgr._auto_tool_mapper._prefetch
        self.assertIsNotNone(entry)
        self.assertTrue(entry.done.wait(timeout=2.0))
        # Now G9111 reaches T0: uses prefetched metadata without fetching twice
        cmd_mod.cmd_ACE_CHANGE_TOOL_WRAPPER(FakeGcmd(printer.gcode, {"TOOL": 0}))
        self.assertEqual(calls, [3])
        self.assertEqual(fetches, ["box.gcode"])
        # Print completes -> map & auto-map state reset -> next print of the same file re-evaluates
        printer.print_stats.state = "complete"
        mgr.update_tool_map_lifecycle()
        self.assertEqual(mgr.get_tool_map(), {})
        # User now puts PLA into slot 0 for the second print of box.gcode
        mgr.instances[0].inventory[0]["status"] = "ready"
        mgr.instances[0].inventory[0]["material"] = "PLA"
        printer.print_stats.state = "printing"
        cmd_mod.cmd_ACE_CHANGE_TOOL_WRAPPER(FakeGcmd(printer.gcode, {"TOOL": 0}))
        self.assertEqual(mgr.get_tool_map(), {})
        self.assertEqual(calls, [3, 0])
        self.assertEqual(len(fetches), 2)

    def test_http_metadata_fetch_end_to_end_on_first_toolchange(self):
        import http.server
        import json
        import threading

        seen_paths = []
        payload = {"result": {"referenced_tools": [0], "filament_type": "PLA",
                              "filament_colors": ["#FFFFFF"]}}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen_paths.append(self.path)
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            printer, mgr = _manager(print_state="printing")
            printer.print_stats.filename = "dir/my model.gcode"
            mgr._auto_tool_mapper.base_url = f"http://127.0.0.1:{port}"
            _set_slots(mgr, [
                ("empty", "", (0, 0, 0)),
                ("empty", "", (0, 0, 0)),
                ("empty", "", (0, 0, 0)),
                ("ready", "PLA", (245, 245, 245)),
            ])
            calls = []
            mgr.perform_tool_change = lambda cur, tgt, *a, **k: calls.append(tgt) or "ok"
            cmd_mod.cmd_ACE_CHANGE_TOOL_WRAPPER(FakeGcmd(printer.gcode, {"TOOL": 0}))
            self.assertEqual(calls, [3])
            self.assertEqual(mgr.get_tool_map(), {0: 3})
            self.assertEqual(seen_paths, ["/server/files/metadata?filename=dir/my%20model.gcode"])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
