"""Tests for multi-color filament tracking across tool changes (ERR-16)."""

import os
import tempfile
import unittest

from fake_moonraker import FakePrinter, FakeWifiManager, serve
from kobra_screen import tracker
from kobra_screen.app import App
from kobra_screen.moonraker import Moonraker


class TestTracker(unittest.TestCase):
    def setUp(self):
        self.printer = FakePrinter()
        self.httpd = serve(self.printer, simulate=False)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wifi = FakeWifiManager()
        self.app = App(
            Moonraker("127.0.0.1", self.httpd.server_address[1]),
            cache_dir=self.tmp.name,
            wifi=self.wifi,
            rinkhals_home=self.tmp.name,
        )
        self.app.extrusion_lock = False
        self.app.refresh()

    def test_parse_toolchange_gcode(self):
        self.assertEqual(tracker.parse_toolchange_gcode("ACE_CHANGE_TOOL TOOL=1"), 1)
        self.assertEqual(tracker.parse_toolchange_gcode("ace_change_tool tool=3"), 3)
        self.assertEqual(tracker.parse_toolchange_gcode("ACE_CHANGE_TOOL TOOL=-1"), -1)
        self.assertEqual(tracker.parse_toolchange_gcode("T0"), 0)
        self.assertEqual(tracker.parse_toolchange_gcode("T2 ; swap to slot 2"), 2)
        self.assertEqual(tracker.parse_toolchange_gcode("_ACE_PRE_TOOLCHANGE T=1"), 1)
        self.assertEqual(tracker.parse_toolchange_gcode("// ACE[0]: Toolchange 0 -> 2"), 2)
        self.assertIsNone(tracker.parse_toolchange_gcode("// ACE[0]: Dryer started: 45C"))
        self.assertIsNone(tracker.parse_toolchange_gcode("M104 S210"))
        self.assertIsNone(tracker.parse_toolchange_gcode("G1 E10 F300"))

    def test_multicolor_split_usage(self):
        # Configure two ACE slots (0 and 1) and an untouched slot (2)
        self.app.set_spool_amount("0", 100.0, unit="g", material="PLA")
        self.app.set_spool_amount("1", 100.0, unit="g", material="PETG")
        self.app.set_spool_amount("2", 100.0, unit="g", material="PLA")
        init_0 = self.app.spool_info("0")["remaining_mm"]
        init_1 = self.app.spool_info("1")["remaining_mm"]
        init_2 = self.app.spool_info("2")["remaining_mm"]

        # Start print with Tool 0 active
        self.printer.status["ace_state"]["current_index"] = 0
        self.printer.status["print_stats"].update(
            state="printing", filename="multicolor.gcode", filament_used=0.0
        )
        self.app.refresh()

        # Extrude 120 mm on Tool 0
        self.printer.status["print_stats"]["filament_used"] = 120.0
        self.app.refresh()

        # Switch to Tool 1 at 120 mm
        self.printer.status["ace_state"]["current_index"] = 1
        self.app.refresh()

        # Slot 0 should already reflect the 120 mm used before tool change
        self.assertAlmostEqual(init_0 - self.app.spool_info("0")["remaining_mm"], 120.0, places=3)
        self.assertAlmostEqual(init_1 - self.app.spool_info("1")["remaining_mm"], 0.0, places=3)

        # Extrude another 280 mm on Tool 1 (total filament_used = 400 mm)
        self.printer.status["print_stats"]["filament_used"] = 400.0
        self.app.refresh()

        # Complete the print
        self.printer.status["print_stats"]["state"] = "complete"
        self.app.refresh()

        self.assertAlmostEqual(init_0 - self.app.spool_info("0")["remaining_mm"], 120.0, places=3)
        self.assertAlmostEqual(init_1 - self.app.spool_info("1")["remaining_mm"], 280.0, places=3)
        self.assertAlmostEqual(self.app.spool_info("2")["remaining_mm"], init_2, places=3)
        self.assertAlmostEqual(self.app.print_tracker.usage_by_spool["0"], 120.0, places=3)
        self.assertAlmostEqual(self.app.print_tracker.usage_by_spool["1"], 280.0, places=3)

    def test_multicolor_via_handle_gcode_response(self):
        self.app.set_spool_amount("0", 50.0, unit="g", material="PLA")
        self.app.set_spool_amount("1", 50.0, unit="g", material="PLA")
        self.app.set_spool_amount("2", 50.0, unit="g", material="PLA")
        init_0 = self.app.spool_info("0")["remaining_mm"]
        init_1 = self.app.spool_info("1")["remaining_mm"]
        init_2 = self.app.spool_info("2")["remaining_mm"]

        self.printer.status["ace_state"]["current_index"] = 0
        self.printer.status["print_stats"].update(
            state="printing", filename="tri.gcode", filament_used=0.0
        )
        self.app.refresh()

        # Tool 0 prints 80 mm, then ACE_CHANGE_TOOL TOOL=1 arrives before status updates
        self.app.handle_gcode_response("ACE_CHANGE_TOOL TOOL=1", filament_used=80.0)
        # Even if a status poll arrives before ace_state.current_index updates, active_tool stays 1
        self.printer.status["print_stats"]["filament_used"] = 140.0
        self.app.refresh()

        # Tool 1 prints 70 mm (up to 150 mm), then T2 arrives
        self.app.handle_gcode_response("T2", filament_used=150.0)

        # Print completes at 250 mm
        self.printer.status["ace_state"]["current_index"] = 2
        self.printer.status["print_stats"].update(state="complete", filament_used=250.0)
        self.app.refresh()

        self.assertAlmostEqual(init_0 - self.app.spool_info("0")["remaining_mm"], 80.0, places=3)
        self.assertAlmostEqual(init_1 - self.app.spool_info("1")["remaining_mm"], 70.0, places=3)
        self.assertAlmostEqual(init_2 - self.app.spool_info("2")["remaining_mm"], 100.0, places=3)

    def test_multicolor_with_intermediate_unload_and_cancel_reset(self):
        self.app.set_spool_amount("0", 50.0, unit="g", material="PLA")
        self.app.set_spool_amount("1", 50.0, unit="g", material="PLA")
        init_0 = self.app.spool_info("0")["remaining_mm"]
        init_1 = self.app.spool_info("1")["remaining_mm"]

        self.printer.status["ace_state"]["current_index"] = 0
        self.printer.status["print_stats"].update(
            state="printing", filename="revisit.gcode", filament_used=0.0
        )
        self.app.refresh()

        # Tool 0 prints 50 mm, then ACE unloads (-1) mid-change
        self.printer.status["print_stats"]["filament_used"] = 50.0
        self.printer.status["ace_state"]["current_index"] = -1
        self.app.refresh()

        # Tool 1 loads and prints 120 mm (total 170 mm)
        self.printer.status["ace_state"]["current_index"] = 1
        self.printer.status["print_stats"]["filament_used"] = 170.0
        self.app.refresh()

        # Back to Tool 0 for 30 mm (total 200 mm)
        self.printer.status["ace_state"]["current_index"] = 0
        self.app.refresh()
        self.printer.status["print_stats"]["filament_used"] = 200.0
        self.app.refresh()

        # Cancelled and Klipper resets filament_used to 0.0 on cancel
        self.printer.status["print_stats"].update(state="cancelled", filament_used=0.0)
        self.app.refresh()

        self.assertAlmostEqual(init_0 - self.app.spool_info("0")["remaining_mm"], 80.0, places=3)
        self.assertAlmostEqual(init_1 - self.app.spool_info("1")["remaining_mm"], 120.0, places=3)

    def test_print_end_unload_attributes_to_last_active_tool(self):
        self.app.set_spool_amount("1", 50.0, unit="g", material="PLA")
        self.app.set_spool_amount("external", 50.0, unit="g", material="PLA")
        init_1 = self.app.spool_info("1")["remaining_mm"]
        init_ext = self.app.spool_info("external")["remaining_mm"]

        self.printer.status["ace_state"]["current_index"] = 1
        self.printer.status["print_stats"].update(
            state="printing", filename="unload_end.gcode", filament_used=0.0
        )
        self.app.refresh()

        # End-print unloads ACE to -1 as state becomes complete
        self.printer.status["ace_state"]["current_index"] = -1
        self.printer.status["print_stats"].update(state="complete", filament_used=95.0)
        self.app.refresh()

        self.assertAlmostEqual(init_1 - self.app.spool_info("1")["remaining_mm"], 95.0, places=3)
        self.assertAlmostEqual(self.app.spool_info("external")["remaining_mm"], init_ext, places=3)

    def test_gcode_store_toolchange_event(self):
        self.app.set_spool_amount("0", 50.0, unit="g", material="PLA")
        self.app.set_spool_amount("1", 50.0, unit="g", material="PLA")
        init_0 = self.app.spool_info("0")["remaining_mm"]
        init_1 = self.app.spool_info("1")["remaining_mm"]

        self.printer.status["ace_state"]["current_index"] = 0
        self.printer.status["print_stats"].update(
            state="printing", filename="store_tc.gcode", filament_used=0.0
        )
        self.app.refresh()

        self.printer.status["print_stats"]["filament_used"] = 60.0
        self.app.refresh()

        # Tool change command logged to gcode_store
        self.printer.gcode_store.append({
            "message": "ACE_CHANGE_TOOL TOOL=1",
            "time": self.app._wall_clock() + 1.0,
            "type": "command",
        })
        self.app._sync_gcode_errors()
        self.assertAlmostEqual(init_0 - self.app.spool_info("0")["remaining_mm"], 60.0, places=3)

        # Finish print on tool 1
        self.printer.status["ace_state"]["current_index"] = 1
        self.printer.status["print_stats"].update(state="complete", filament_used=160.0)
        self.app.refresh()

        self.assertAlmostEqual(init_0 - self.app.spool_info("0")["remaining_mm"], 60.0, places=3)
        self.assertAlmostEqual(init_1 - self.app.spool_info("1")["remaining_mm"], 100.0, places=3)

    def test_mid_print_tracker_startup_does_not_deduct_prior_filament(self):
        """REV-25: PrintStatsTracker started mid-print must only deduct filament extruded after startup."""
        pt = tracker.PrintStatsTracker()
        # First poll happens mid-print when 500 mm were already extruded
        d0 = pt.update("printing", filename="mid.gcode", filament_used=500.0, current_tool=0, ace_present=True)
        self.assertEqual(d0, [])
        self.assertEqual(pt.tool_start_filament, 500.0)

        # Tool 0 extrudes 120 mm more (to 620 mm), then switches to Tool 1
        d1 = pt.update("printing", filename="mid.gcode", filament_used=620.0, current_tool=1, ace_present=True)
        self.assertEqual(len(d1), 1)
        self.assertEqual(d1[0][0], "0")
        self.assertAlmostEqual(d1[0][1], 120.0, places=3)

        # Tool 1 extrudes 80 mm more (to 700 mm) and print completes
        d2 = pt.update("complete", filename="mid.gcode", filament_used=700.0, current_tool=1, ace_present=True)
        self.assertEqual(len(d2), 1)
        self.assertEqual(d2[0][0], "1")
        self.assertAlmostEqual(d2[0][1], 80.0, places=3)
        self.assertAlmostEqual(pt.usage_by_spool["0"], 120.0, places=3)
        self.assertAlmostEqual(pt.usage_by_spool["1"], 80.0, places=3)

        # Regression: tracker that previously saw standby still deducts full new-print filament
        pt2 = tracker.PrintStatsTracker()
        self.assertEqual(pt2.update("standby"), [])
        self.assertEqual(pt2.update("printing", filename="fresh.gcode", filament_used=200.0, current_tool=0, ace_present=True), [])
        d_full = pt2.finish(filament_used=200.0)
        self.assertEqual(len(d_full), 1)
        self.assertAlmostEqual(d_full[0][1], 200.0, places=3)

    def test_mid_print_app_startup_does_not_deduct_prior_filament(self):
        """REV-25: App started mid-print must not deduct pre-existing filament_used from spools."""
        mid_printer = FakePrinter()
        mid_printer.status["ace_state"]["current_index"] = 0
        mid_printer.status["print_stats"].update(
            state="printing", filename="mid_app.gcode", filament_used=500.0
        )
        mid_httpd = serve(mid_printer, simulate=False)
        self.addCleanup(mid_httpd.server_close)
        self.addCleanup(mid_httpd.shutdown)
        with tempfile.TemporaryDirectory() as tmpdir:
            mid_app = App(
                Moonraker("127.0.0.1", mid_httpd.server_address[1]),
                cache_dir=tmpdir,
                wifi=FakeWifiManager(),
                rinkhals_home=tmpdir,
            )
            mid_app.set_spool_amount("0", 100.0, unit="g", material="PLA")
            init_0 = mid_app.spool_info("0")["remaining_mm"]

            # First refresh attaches mid-print at 500 mm
            mid_app.refresh()
            self.assertAlmostEqual(mid_app.spool_info("0")["remaining_mm"], init_0, places=3)

            # Another 150 mm printed (total 650 mm), then print completes
            mid_printer.status["print_stats"].update(state="complete", filament_used=650.0)
            mid_app.refresh()
            self.assertAlmostEqual(init_0 - mid_app.spool_info("0")["remaining_mm"], 150.0, places=3)

    def test_load_event_log_state_preserves_explicit_watermark(self):
        """REV-18: local warn events with source=gcode must not advance an explicit persisted watermark."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "kobra-screen.events.json")
            events = [
                {"ts": 9999.0, "severity": "warn", "source": "gcode", "message": "Ekstrüzyon engellendi: G1 E10"},
            ]
            tracker.save_event_log(path, events, watermark=100.0)
            loaded, wm = tracker.load_event_log_state(path)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(wm, 100.0)


if __name__ == "__main__":
    unittest.main()
