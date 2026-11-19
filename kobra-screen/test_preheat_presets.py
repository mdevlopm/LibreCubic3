"""Test preheat presets contain ASA and TPU and fit the 272x480 layout."""

import unittest
from fake_moonraker import FakePrinter, FakeWifiManager, serve
from kobra_screen.app import App
from kobra_screen.moonraker import Moonraker
from kobra_screen import screens
from kobra_screen.widgets import Button


class TestPreheatPresets(unittest.TestCase):
    def setUp(self):
        self.printer = FakePrinter()
        self.httpd = serve(self.printer, simulate=False)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.app = App(Moonraker("127.0.0.1", self.httpd.server_address[1]), wifi=FakeWifiManager())
        self.app.refresh()

    def test_presets_contain_asa_and_tpu(self):
        names = [p[0] for p in screens.PRESETS]
        self.assertIn("ASA", names)
        self.assertIn("TPU", names)
        preset_map = {name: (nozzle, bed) for name, nozzle, bed in screens.PRESETS}
        # ASA ~110°C bed
        self.assertEqual(preset_map["ASA"][1], 110)
        # TPU ~60°C bed
        self.assertEqual(preset_map["TPU"][1], 60)

    def test_asa_preheat_request(self):
        self.app.select_tab("control")
        self.app.refresh()
        self.assertTrue(self.app.press("ASA"))
        self.assertEqual(self.app.modal.text, self.app.tr("Isıtma başlatılsın mı? (nozul 260°C, tabla 110°C)"))
        self.assertTrue(self.app.press("Isıt"))
        self.assertEqual(self.printer.status["extruder"]["target"], 260.0)
        self.assertEqual(self.printer.status["heater_bed"]["target"], 110.0)

    def test_tpu_preheat_request(self):
        self.app.select_tab("control")
        self.app.refresh()
        self.assertTrue(self.app.press("TPU"))
        self.assertEqual(self.app.modal.text, self.app.tr("Isıtma başlatılsın mı? (nozul 220°C, tabla 60°C)"))
        self.assertTrue(self.app.press("Isıt"))
        self.assertEqual(self.printer.status["extruder"]["target"], 220.0)
        self.assertEqual(self.printer.status["heater_bed"]["target"], 60.0)

    def test_preheat_presets_locked_when_heating_lock_enabled(self):
        self.app.heating_lock = True
        self.app.select_tab("control")
        self.app.refresh()
        layout_btns = {item.widget.text: item.widget for item in self.app.layout() if isinstance(item.widget, Button)}
        for name in ("PLA", "PETG", "ABS", "ASA", "TPU"):
            self.assertIn(name, layout_btns)
            self.assertIsNone(layout_btns[name].action)
            self.assertEqual(layout_btns[name].icon, "lock")

    def test_heat_page_fits_within_limits(self):
        self.app.select_tab("control")
        self.app.refresh()
        self.app.layout()
        self.assertLessEqual(self.app.content_bottom, self.app.content_limit)

    def test_no_extrude_button_on_heat_page(self):
        self.app.select_tab("control")
        self.app.refresh()
        btn_texts = [item.widget.text for item in self.app.layout() if isinstance(item.widget, Button)]
        self.assertNotIn("Ekstrüde", btn_texts)


if __name__ == "__main__":
    unittest.main()
