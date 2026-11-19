"""Unit tests for ACE post-drying cooling countdown (configurable via ace_dryer_cooldown_s)."""

import tempfile
import unittest

from fake_moonraker import FakePrinter, serve
from kobra_screen.app import App
from kobra_screen.moonraker import Moonraker


class DryerCoolingTests(unittest.TestCase):
    def setUp(self):
        self.printer = FakePrinter()
        self.httpd = serve(self.printer, simulate=False)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake_now = [1000.0]
        self.app = App(
            Moonraker("127.0.0.1", self.httpd.server_address[1]),
            cache_dir=self.tmp.name,
            rinkhals_home=self.tmp.name,
            clock_fn=lambda: self.fake_now[0],
            lang="tr",  # Force Turkish for consistent test strings
        )
        self.app.refresh()
        self.app.select_tab("filament")

    def texts(self):
        return [item.widget.text for item in self.app.layout() if hasattr(item.widget, "text")]

    def _set_dryer_status(self, status, target_temp=0, duration=0, remain_time=0):
        self.printer.status["ace_instance_0"]["dryer_status"] = {
            "status": status,
            "target_temp": target_temp,
            "duration": duration,
            "remain_time": remain_time,
        }
        self.app.refresh()

    def _find_cooling_text(self, texts):
        """Return the cooling countdown text if present, else None."""
        for t in texts:
            if "sonra duracak" in t or "stopping in" in t:
                return t
        return None

    def _find_explanation_text(self, texts):
        """Return the explanation text if present, else None."""
        for t in texts:
            if "güvenlik için soğutuluyor" in t or "Cooling heater plate" in t:
                return t
        return None

    def test_hidden_on_startup_when_already_stopped(self):
        self.assertEqual(self.printer.status["ace_instance_0"]["dryer_status"]["status"], "stop")
        self.assertIsNone(self.app._dryer_stopped_ts)
        texts = self.texts()
        self.assertNotIn("ACE fanı ısıtıcıyı soğutuyor", texts)
        self.assertNotIn("Kurutma sonrası soğutma", texts)
        self.assertIsNone(self._find_cooling_text(texts))
        self.assertIsNone(self._find_explanation_text(texts))
        self.assertIn("Kurutma kapalı", texts)

    def test_default_180s_cooldown_countdown(self):
        # Start drying
        self._set_dryer_status("drying", target_temp=50, duration=120, remain_time=7200)
        self.assertIsNone(self.app._dryer_stopped_ts)
        texts = self.texts()
        self.assertIsNone(self._find_cooling_text(texts))
        self.assertIn("Kurutuyor", texts)

        # Transition drying -> stop
        self.fake_now[0] = 1050.0
        self._set_dryer_status("stop", target_temp=0, duration=0, remain_time=0)
        self.assertEqual(self.app._dryer_stopped_ts, 1050.0)
        texts = self.texts()

        # At t=0 (transition), should show 3:00 (180s = 3 minutes)
        cooling = self._find_cooling_text(texts)
        self.assertIsNotNone(cooling)
        self.assertIn("3:00", cooling)
        explanation = self._find_explanation_text(texts)
        self.assertIsNotNone(explanation)
        self.assertIn("güvenlik için soğutuluyor", explanation)

        # At t=121s, should show 0:59 (180-121=59s)
        self.fake_now[0] = 1050.0 + 121.0
        self.app.refresh()
        texts = self.texts()
        cooling = self._find_cooling_text(texts)
        self.assertIsNotNone(cooling)
        self.assertIn("0:59", cooling)

        # At t=180s, cooling message should be gone
        self.fake_now[0] = 1050.0 + 180.0
        self.app.refresh()
        texts = self.texts()
        self.assertIsNone(self._find_cooling_text(texts))
        self.assertIsNone(self._find_explanation_text(texts))
        self.assertIsNone(self.app._dryer_stopped_ts)
        self.assertIn("Kurutma kapalı", texts)

    def test_custom_60s_cooldown_countdown(self):
        # Set custom cooldown to 60 seconds
        self.app.set_ace_dryer_cooldown_s(60)
        self.assertEqual(self.app.ace_dryer_cooldown_s, 60)

        # Start drying
        self._set_dryer_status("drying", target_temp=50, duration=120, remain_time=7200)
        self.assertIsNone(self.app._dryer_stopped_ts)

        # Transition drying -> stop
        self.fake_now[0] = 1050.0
        self._set_dryer_status("stop", target_temp=0, duration=0, remain_time=0)
        self.assertEqual(self.app._dryer_stopped_ts, 1050.0)
        texts = self.texts()

        # At t=0, should show 1:00 (60s = 1 minute)
        cooling = self._find_cooling_text(texts)
        self.assertIsNotNone(cooling)
        self.assertIn("1:00", cooling)
        explanation = self._find_explanation_text(texts)
        self.assertIsNotNone(explanation)

        # At t=59.5s, should show 0:01 (ceil(60-59.5)=1s -> 0:01)
        self.fake_now[0] = 1050.0 + 59.5
        self.app.refresh()
        texts = self.texts()
        cooling = self._find_cooling_text(texts)
        self.assertIsNotNone(cooling)
        self.assertIn("0:01", cooling)

        # At t=60s, cooling message should be gone
        self.fake_now[0] = 1050.0 + 60.0
        self.app.refresh()
        texts = self.texts()
        self.assertIsNone(self._find_cooling_text(texts))
        self.assertIsNone(self._find_explanation_text(texts))
        self.assertIsNone(self.app._dryer_stopped_ts)
        self.assertIn("Kurutma kapalı", texts)

    def test_invalid_cooldown_values_fall_back_to_auto(self):
        # Invalid or out-of-range values mean "auto": stock firmware -> 180 s
        for invalid in ["abc", None, "xyz", 5, 9999, -10, 0]:
            self.app.set_ace_dryer_cooldown_s(invalid)
            self.assertIsNone(self.app.ace_dryer_cooldown_s, f"input {invalid!r}")
            self.assertEqual(self.app.dryer_cooldown_s(), 180, f"input {invalid!r}")

        # Valid boundary values are used as-is
        self.app.set_ace_dryer_cooldown_s(10)
        self.assertEqual(self.app.dryer_cooldown_s(), 10)
        self.app.set_ace_dryer_cooldown_s(600)
        self.assertEqual(self.app.dryer_cooldown_s(), 600)

    def test_patched_firmware_auto_selects_60s(self):
        self.printer.status["ace_instance_0"]["firmware"] = "V1.3.86K"
        self.app.refresh()
        self.assertEqual(self.app.dryer_cooldown_s(), 60)
        self.printer.status["ace_instance_0"]["firmware"] = "V1.3.863"
        self.app.refresh()
        self.assertEqual(self.app.dryer_cooldown_s(), 180)
        # an explicit setting wins over detection
        self.printer.status["ace_instance_0"]["firmware"] = "V1.3.86K"
        self.app.refresh()
        self.app.set_ace_dryer_cooldown_s(90)
        self.assertEqual(self.app.dryer_cooldown_s(), 90)

    def test_tick_redraws_once_per_second_while_counting(self):
        self.app.set_ace_dryer_cooldown_s(60)
        self._set_dryer_status("drying", target_temp=50, duration=120, remain_time=7200)
        self.app.refresh()
        self.fake_now[0] = 1050.0
        self._set_dryer_status("stop", target_temp=0, duration=0, remain_time=0)
        self.app.refresh()
        self.app.tick_timers()
        self.app.dirty = False
        self.fake_now[0] = 1050.4
        self.app.tick_timers()
        self.assertFalse(self.app.dirty)  # same second shown
        self.fake_now[0] = 1051.2
        self.app.tick_timers()
        self.assertTrue(self.app.dirty)  # 0:59 now
        self.fake_now[0] = 1110.0
        self.app.dirty = False
        self.app.tick_timers()
        self.assertTrue(self.app.dirty)
        self.assertIsNone(self.app._dryer_stopped_ts)

    def test_english_language_countdown(self):
        # Switch to English
        self.app.set_language("en", persist=False)
        self.app.refresh()
        self.app.select_tab("filament")

        # Start drying
        self._set_dryer_status("drying", target_temp=50, duration=120, remain_time=7200)

        # Transition drying -> stop
        self.fake_now[0] = 1050.0
        self._set_dryer_status("stop", target_temp=0, duration=0, remain_time=0)
        texts = self.texts()

        # Should show English countdown
        cooling = None
        for t in texts:
            if "stopping in" in t.lower():
                cooling = t
                break
        self.assertIsNotNone(cooling)
        self.assertIn("3:00", cooling)

        # Explanation in English
        explanation = None
        for t in texts:
            if "cooling heater plate" in t.lower():
                explanation = t
                break
        self.assertIsNotNone(explanation)
        self.assertIn("safety", explanation)

    def test_cleared_when_new_drying_starts(self):
        # Start and stop drying
        self.assertTrue(self.app.press("PLA", icon="thermo"))
        self.app.refresh()
        self.fake_now[0] = 2000.0
        self.assertTrue(self.app.press("Durdur"))
        self.app.refresh()
        self.assertEqual(self.app._dryer_stopped_ts, 2000.0)
        texts = self.texts()
        self.assertIsNotNone(self._find_cooling_text(texts))

        # Starting new drying clears cooling state immediately
        self.fake_now[0] = 2100.0
        self.assertTrue(self.app.press("PLA", icon="thermo"))
        self.app.refresh()
        self.assertIsNone(self.app._dryer_stopped_ts)
        texts = self.texts()
        self.assertIsNone(self._find_cooling_text(texts))
        self.assertIsNone(self._find_explanation_text(texts))
        self.assertIn("Kurutuyor", texts)

        # Also test non-'stop' non-'drying' transition (e.g. 'idle') and Moonraker restart
        self.fake_now[0] = 2200.0
        self.printer.status["ace_instance_0"]["dryer_status"]["status"] = "idle"
        self.app.refresh()
        self.assertEqual(self.app._dryer_stopped_ts, 2200.0)
        self.assertIsNotNone(self._find_cooling_text(self.texts()))

        self.printer.status["ace_instance_0"]["dryer_status"]["status"] = "drying"
        self.app.refresh()
        self.assertIsNone(self.app._dryer_stopped_ts)
        self.assertIsNone(self._find_cooling_text(self.texts()))

    def test_cooldown_persists_across_refreshes(self):
        """Verify the countdown updates on each refresh without needing new drying cycle."""
        # First set to drying, then to stop to trigger the transition
        self._set_dryer_status("drying", target_temp=50, duration=120, remain_time=7200)
        self.fake_now[0] = 1050.0
        self._set_dryer_status("stop", target_temp=0, duration=0, remain_time=0)
        self.assertEqual(self.app._dryer_stopped_ts, 1050.0)

        # First refresh - should show ~3:00
        texts = self.texts()
        cooling = self._find_cooling_text(texts)
        self.assertIsNotNone(cooling)
        self.assertIn("3:00", cooling)

        # Advance time and refresh - countdown should update
        self.fake_now[0] = 1050.0 + 30.0
        self.app.refresh()
        texts = self.texts()
        cooling = self._find_cooling_text(texts)
        self.assertIsNotNone(cooling)
        self.assertIn("2:30", cooling)

        # Advance more
        self.fake_now[0] = 1050.0 + 90.0
        self.app.refresh()
        texts = self.texts()
        cooling = self._find_cooling_text(texts)
        self.assertIsNotNone(cooling)
        self.assertIn("1:30", cooling)


if __name__ == "__main__":
    unittest.main()