"""Tests for Klipper action:prompt device fixes (C1: startup/Close persistence, C2: <=2s lightweight poll)."""

import tempfile
import unittest
from unittest import mock

from fake_moonraker import FakePrinter, FakeWifiManager, serve
from kobra_screen.app import App
from kobra_screen.moonraker import Moonraker
from kobra_screen.prompts import PromptDialog


class PromptDeviceTests(unittest.TestCase):
    def setUp(self):
        self.printer = FakePrinter()
        self.httpd = serve(self.printer, simulate=False)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = [100.0]

    def make_app(self, lang="en"):
        client = Moonraker("127.0.0.1", self.httpd.server_address[1])
        self.addCleanup(client.close)
        return App(
            client,
            cache_dir=self.tmp.name,
            wifi=FakeWifiManager(),
            rinkhals_home=self.tmp.name,
            clock_fn=lambda: self.now[0],
            lang=lang,
        )

    def test_c1a_startup_ignores_existing_unclosed_prompt_and_shows_new_one(self):
        # Old unclosed prompt already sits in gcode_store before kobra-screen starts
        self.printer.gcode_store.extend([
            {"type": "response", "time": 1000.0, "message": "// action:prompt_begin Old Stale Prompt"},
            {"type": "response", "time": 1000.1, "message": "// action:prompt_text From before restart"},
            {"type": "response", "time": 1000.2, "message": "// action:prompt_button Retry|ACE_RETRY|primary"},
            {"type": "response", "time": 1000.3, "message": "// action:prompt_show"},
        ])

        app = self.make_app()
        app.refresh()
        self.assertIsNone(app.modal)
        self.assertFalse(app.prompt_dialog.visible)

        # Subsequent polls and even a stray prompt_show without new prompt_begin stay closed
        app._sync_gcode_errors()
        self.assertIsNone(app.modal)
        self.printer.gcode_store.append(
            {"type": "response", "time": 1001.0, "message": "// action:prompt_show"}
        )
        app._sync_gcode_errors()
        self.assertIsNone(app.modal)

        # A new prompt_begin arriving after startup opens normally
        self.printer.gcode_store.extend([
            {"type": "response", "time": 1010.0, "message": "// action:prompt_begin Fresh Prompt"},
            {"type": "response", "time": 1010.1, "message": "// action:prompt_text Action needed now"},
            {"type": "response", "time": 1010.2, "message": "// action:prompt_button OK|RESUME|primary"},
            {"type": "response", "time": 1010.3, "message": "// action:prompt_show"},
        ])
        app._sync_gcode_errors()
        self.assertIsInstance(app.modal, PromptDialog)
        self.assertEqual(app.modal.title, "Fresh Prompt")

    def test_c1b_close_permanently_dismisses_dialog_until_new_prompt_begin(self):
        app = self.make_app(lang="en")
        app.refresh()

        self.printer.gcode_store.extend([
            {"type": "response", "time": 2000.0, "message": "// action:prompt_begin Jam Detected"},
            {"type": "response", "time": 2000.1, "message": "// action:prompt_text Check slot 0"},
            {"type": "response", "time": 2000.2, "message": "// action:prompt_button OK|RESUME|primary"},
            {"type": "response", "time": 2000.3, "message": "// action:prompt_show"},
        ])
        app._sync_gcode_errors()
        self.assertIsInstance(app.modal, PromptDialog)
        self.assertEqual(app.modal.begin_ts, 2000.0)

        # Close permanently dismisses this prompt_begin timestamp
        self.assertTrue(app.press("Close"))
        self.assertIsNone(app.modal)

        # Next poll with the same history keeps it closed
        app._sync_gcode_errors()
        self.assertIsNone(app.modal)

        # Even if history overlap is lost or another prompt_show arrives for same dialog, it stays closed
        app._last_prompt_history = []
        app._sync_gcode_errors()
        self.assertIsNone(app.modal)

        self.printer.gcode_store.append(
            {"type": "response", "time": 2001.0, "message": "// action:prompt_show"}
        )
        app._sync_gcode_errors()
        self.assertIsNone(app.modal)

        # A new prompt_begin (even with the same title) at a newer timestamp opens cleanly
        self.printer.gcode_store.extend([
            {"type": "response", "time": 2005.0, "message": "// action:prompt_begin Jam Detected"},
            {"type": "response", "time": 2005.1, "message": "// action:prompt_text Second occurrence"},
            {"type": "response", "time": 2005.2, "message": "// action:prompt_button OK|RESUME|primary"},
            {"type": "response", "time": 2005.3, "message": "// action:prompt_show"},
        ])
        app._sync_gcode_errors()
        self.assertIsInstance(app.modal, PromptDialog)
        self.assertEqual(app.modal.text, "Second occurrence")

        # OK / action button behavior remains unchanged (sends gcode when unlocked; blocked by REV-03 when locked)
        app.extrusion_lock = True
        self.assertFalse(app.press("OK"))
        app.extrusion_lock = False
        self.assertTrue(app.press("OK"))
        self.assertIn("RESUME", self.printer.gcodes)

    def test_c2_idle_gcode_store_poll_interval_at_most_2s_and_small_count(self):
        app = self.make_app()
        calls = []
        orig_gcode_store = app.client.gcode_store

        def spy_gcode_store(count=100):
            calls.append((self.now[0], count))
            return orig_gcode_store(count=count)

        with mock.patch.object(app.client, "gcode_store", side_effect=spy_gcode_store):
            # Simulate 1 Hz idle refresh ticks over 6 seconds
            for sec in range(7):
                self.now[0] = 100.0 + sec
                app.refresh()

        self.assertGreaterEqual(len(calls), 4)
        times = [t for t, _ in calls]
        counts = [c for _, c in calls]
        # Each count must be small (~20) to avoid ARM load
        self.assertTrue(all(c <= 20 for c in counts), counts)
        # Consecutive gcode_store poll intervals must be <= 2.0s (and > 1.0s to save CPU)
        deltas = [times[i + 1] - times[i] for i in range(len(times) - 1)]
        self.assertTrue(all(1.0 < d <= 2.0 for d in deltas), deltas)

    def test_c2_prompt_appears_within_2s_while_idle_without_breaking_ace_matching(self):
        app = self.make_app()
        app.refresh()  # t=100.0 (initial poll)

        # Prompt arrives just after t=100.0 poll while printer is idle
        self.printer.gcode_store.extend([
            {"type": "response", "time": 200.0, "message": "// action:prompt_begin Idle Prompt"},
            {"type": "response", "time": 200.1, "message": "// action:prompt_button OK|RESUME|primary"},
            {"type": "response", "time": 200.2, "message": "// action:prompt_show"},
        ])

        self.now[0] = 101.0
        app.refresh()
        self.now[0] = 102.0
        app.refresh()
        self.assertIsInstance(app.modal, PromptDialog)
        self.assertEqual(app.modal.title, "Idle Prompt")

        # Dismiss and verify ACE pending response matching still works immediately (< 2s)
        self.assertTrue(app.press("Close"))
        self.printer.ace_info_lines = False
        app.ace_gcode("ACE_STOP_DRYING", done="Kurutma durduruldu")
        self.assertTrue(bool(app.ace_pending))

        self.now[0] = 102.2
        self.printer.gcode_store.append({
            "type": "response",
            "time": app._wall_clock(),
            "message": "// ACE[0]: Dryer stopped",
        })
        app.refresh()
        self.assertFalse(bool(app.ace_pending))


if __name__ == "__main__":
    unittest.main()
