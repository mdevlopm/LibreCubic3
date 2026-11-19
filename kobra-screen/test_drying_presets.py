"""Test drying presets contain PLA/PETG/ABS-ASA/PA-PC/TPU and limit clamping works."""

import tempfile
import unittest

from fake_moonraker import FakePrinter, serve
from kobra_screen import limits, screens
from kobra_screen.app import App
from kobra_screen.moonraker import Moonraker
from kobra_screen.widgets import Button


class TestDryingPresets(unittest.TestCase):
    def test_dry_presets_exact_five_items_in_order(self):
        self.assertEqual(
            list(screens.DRY_PRESETS),
            [
                ("PLA", 45, 240),
                ("PETG", 55, 360),
                ("ABS/ASA", 60, 360),
                ("PA/PC", 70, 480),
                ("TPU", 50, 360),
            ],
        )

    def test_dry_presets_temperatures_and_durations(self):
        preset_map = {name: (temp, minutes) for name, temp, minutes in screens.DRY_PRESETS}
        self.assertEqual(preset_map["PLA"], (45, 240))
        self.assertEqual(preset_map["PETG"], (55, 360))
        self.assertEqual(preset_map["ABS/ASA"], (60, 360))
        self.assertEqual(preset_map["PA/PC"], (70, 480))
        self.assertEqual(preset_map["TPU"], (50, 360))

    def test_dryer_temperature_clamping(self):
        limit = 55
        for name, temp, minutes in screens.DRY_PRESETS:
            clamped = screens.clamp_dryer_temperature(temp, limit)
            self.assertLessEqual(clamped, limit)
            if temp > limit:
                self.assertEqual(clamped, limit)
            else:
                self.assertEqual(clamped, temp)

    def test_limits_clamp_dryer_temperature_helper(self):
        self.assertEqual(limits.clamp_dryer_temperature(70, 60), 60)
        self.assertEqual(limits.clamp_dryer_temperature(55, 60), 55)
        self.assertEqual(limits.clamp_dryer_temperature(45, 50), 45)
        self.assertEqual(limits.clamp_dryer_temperature(50, None), 50)

    def test_ui_preset_buttons_first_two_are_pla_petg(self):
        first_two = [p[0] for p in screens.DRY_PRESETS[:2]]
        self.assertEqual(first_two, ["PLA", "PETG"])

    def test_dry_presets_not_clipped_above_max_dryer_temperature(self):
        """Kurutma onayari listesi dogru degerlerle donuyor ve max_dryer_temperature uzeri kirpilmiyor."""
        preset_map = {name: (temp, minutes) for name, temp, minutes in screens.DRY_PRESETS}
        # PA/PC has temperature 70°C, which exceeds default max_dryer_temperature 60°C
        self.assertGreater(preset_map["PA/PC"][0], 60)
        self.assertEqual(preset_map["PA/PC"], (70, 480))
        self.assertEqual(preset_map["PLA"], (45, 240))
        self.assertEqual(preset_map["PETG"], (55, 360))
        self.assertEqual(preset_map["ABS/ASA"], (60, 360))
        self.assertEqual(preset_map["TPU"], (50, 360))

    def test_spools_dryer_card_has_pla_petg_and_more_presets_page(self):
        printer = FakePrinter()
        httpd = serve(printer, simulate=False)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = App(Moonraker("127.0.0.1", httpd.server_address[1]), cache_dir=tmp.name)
        app.refresh()
        app.select_tab("filament")

        placed = app.layout()
        texts = [i.widget.text for i in placed if hasattr(i.widget, "text")]
        self.assertNotIn("45° 4sa", texts)
        self.assertNotIn("55° 6sa", texts)
        self.assertTrue(any(isinstance(i.widget, Button) and i.widget.text == "PLA" and i.widget.icon == "thermo" for i in placed))
        self.assertTrue(any(isinstance(i.widget, Button) and i.widget.text == "PETG" and i.widget.icon == "thermo" for i in placed))
        self.assertIn("Daha fazla", texts)
        self.assertIn("Özel", texts)

        # Opening "Daha fazla" displays all 5 presets; PA/PC (70°) respects max_dryer_temperature=60
        self.assertTrue(app.press("Daha fazla"))
        app.refresh()
        page_items = app.layout()
        page_texts = [i.widget.text for i in page_items if hasattr(i.widget, "text")]
        for name, _, _ in screens.DRY_PRESETS:
            self.assertIn(name, page_texts)
        pa_btn = next(i.widget for i in page_items if isinstance(i.widget, Button) and i.widget.text == "PA/PC")
        self.assertIsNone(pa_btn.action)

        self.assertTrue(app.press("ABS/ASA", icon="thermo"))
        self.assertEqual(printer.gcodes[-1], "ACE_START_DRYING TEMP=60 DURATION=360")

        # When limit is raised to 75 and drying stops, PA/PC becomes enabled and sends 70°C 480m
        printer.status["ace_instance_0"]["dryer_status"] = {"status": "stop", "target_temp": 0, "duration": 0, "remain_time": 0}
        printer.settings["ace"]["max_dryer_temperature"] = "75"
        app.state.settings = {}
        app.refresh()
        self.assertTrue(app.press("Daha fazla"))
        self.assertTrue(app.press("PA/PC", icon="thermo"))
        self.assertEqual(printer.gcodes[-1], "ACE_START_DRYING TEMP=70 DURATION=480")


if __name__ == "__main__":
    unittest.main()
