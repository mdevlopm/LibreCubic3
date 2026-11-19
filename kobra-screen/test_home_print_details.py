"""Tests for home screen and print card details (ui-d / ERR-22)."""

import tempfile
import unittest

from fake_moonraker import FakePrinter, FakeWifiManager, serve
from kobra_screen import i18n
from kobra_screen.app import App
from kobra_screen.moonraker import Moonraker
from kobra_screen.widgets import WIDTH, HEIGHT, Button, Label, Panel, button_parts, button_sub_text_size, text_width


class HomePrintDetailsTests(unittest.TestCase):
    def setUp(self):
        self.printer = FakePrinter()
        self.httpd = serve(self.printer, simulate=False)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wifi = FakeWifiManager()

    def _make_app(self, lang="tr", extrusion_lock=True):
        app = App(
            Moonraker("127.0.0.1", self.httpd.server_address[1]),
            cache_dir=self.tmp.name,
            wifi=self.wifi,
            rinkhals_home=self.tmp.name,
            lang=lang,
        )
        app.extrusion_lock = extrusion_lock
        app.refresh()
        return app

    @staticmethod
    def _texts(app):
        return [item.widget.text for item in app.layout() if hasattr(item.widget, "text") and item.widget.text]

    def _assert_fits(self, app):
        placed = app.layout()
        self.assertLessEqual(app.content_bottom, app.content_limit)
        fg = []
        for item in placed:
            self.assertGreaterEqual(item.x, 0)
            self.assertLessEqual(item.x + item.w, WIDTH)
            self.assertLessEqual(item.y + item.h, HEIGHT)
            w = item.widget
            if isinstance(w, Button):
                self.assertGreaterEqual(item.h, 28)
                _, box = button_parts(item)
                if box and w.sub:
                    self.assertGreaterEqual(box[3], 36)
                    self.assertLessEqual(text_width(w.sub, 12) * 1.15, box[2])
                    sz = button_sub_text_size(item, box[2])
                    from kobra_screen.theme import FONT_SIZES
                    self.assertLessEqual(text_width(w.text, FONT_SIZES[sz]) * 1.15, box[2])
            if not isinstance(w, Panel):
                fg.append(item)
        for i in range(len(fg)):
            a = fg[i]
            for j in range(i + 1, len(fg)):
                b = fg[j]
                ox = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
                oy = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
                self.assertFalse(ox > 1 and oy > 1)

    def test_err22_homing_origin_z_shown_on_home_and_print_card(self):
        app = self._make_app(extrusion_lock=False)
        self.assertIn("Z-offset -0.165 mm", self._texts(app))

        # Non-zero live baby-step Z appears alongside z_offset on idle home
        self.printer.status["gcode_move"]["homing_origin"] = [0.0, 0.0, 0.05, 0.0]
        app.refresh()
        idle_texts = self._texts(app)
        self.assertTrue(any("-0.165" in t and "+0.050" in t for t in idle_texts), idle_texts)
        self._assert_fits(app)

        # Also visible on the printing card when printing
        self.printer.status["print_stats"].update(state="printing", filename="kutu_08.gcode", print_duration=120.0)
        self.printer.status["virtual_sdcard"]["progress"] = 0.25
        self.printer.status["gcode_move"]["homing_origin"] = [0.0, 0.0, -0.03, 0.0]
        app.refresh()
        print_texts = self._texts(app)
        self.assertTrue(any("-0.165" in t and "-0.030" in t for t in print_texts), print_texts)
        self._assert_fits(app)

    def test_printing_temp_strip_taps_to_control_heat(self):
        app = self._make_app(extrusion_lock=False)
        app.control_seg = "move"
        self.printer.status["print_stats"].update(state="printing", filename="kutu_08.gcode", print_duration=60.0)
        self.printer.status["virtual_sdcard"]["progress"] = 0.2
        app.refresh()

        placed = app.layout()
        # Find the clickable Panel containing the "Nozul" temperature strip label
        nozzle_lbl = next(i for i in placed if isinstance(i.widget, Label) and i.widget.text == "Nozul")
        strip_panel = next(
            i for i in placed
            if isinstance(i.widget, Panel) and i.widget.action is not None
            and i.x <= nozzle_lbl.x < i.x + i.w and i.y <= nozzle_lbl.y < i.y + i.h
        )
        self.assertTrue(app.tap(strip_panel.x + strip_panel.w // 2, strip_panel.y + strip_panel.h // 2))
        self.assertEqual(app.tab, "control")
        self.assertEqual(app.control_seg, "heat")

    def test_print_card_shows_active_ace_slot_color_and_name(self):
        for lang, expected in (("tr", "Yuva 2 · PETG · Mavi"), ("en", "Slot 2 · PETG · Blue")):
            app = self._make_app(lang=lang, extrusion_lock=False)
            self.printer.status["print_stats"].update(state="printing", filename="kutu_08.gcode", print_duration=60.0)
            self.printer.status["virtual_sdcard"]["progress"] = 0.3
            self.printer.status["gcode_move"]["homing_origin"] = [0.0, 0.0, 0.05, 0.0]
            app.refresh()

            placed = app.layout()
            ace_btn = next((i.widget for i in placed if isinstance(i.widget, Button) and i.widget.dot is not None), None)
            self.assertIsNotNone(ace_btn)
            self.assertEqual(ace_btn.text, expected)
            self.assertEqual(ace_btn.dot, (40, 110, 230))
            self._assert_fits(app)

        # Custom color falls back to "Özel"/"Custom" and preserves RGB dot
        self.printer.status["ace_instance_0"]["slots"][1]["color"] = [12, 34, 56]
        app = self._make_app(lang="tr", extrusion_lock=False)
        app.refresh()
        ace_btn = next((i.widget for i in app.layout() if isinstance(i.widget, Button) and i.widget.dot is not None), None)
        self.assertIsNotNone(ace_btn)
        self.assertEqual(ace_btn.text, "Yuva 2 · PETG · Özel")
        self.assertEqual(ace_btn.dot, (12, 34, 56))

        # No active ACE slot -> no dot button on print card
        self.printer.status["ace_state"]["current_index"] = -1
        app.refresh()
        self.assertIsNone(next((i.widget for i in app.layout() if isinstance(i.widget, Button) and i.widget.dot is not None), None))
        self._assert_fits(app)

    def test_home_print_button_shows_extrusion_lock_indicator_and_warns(self):
        for lang, expected_sub, expected_msg in (
            ("tr", "Ekstrüzyon kilidi", "Ekstrüzyon kilidi açık"),
            ("en", "Extrusion lock", "Extrusion lock enabled"),
        ):
            app = self._make_app(lang=lang, extrusion_lock=True)
            print_lbl = i18n.tr("Yazdır", lang)
            btn = next(
                (i.widget for i in app.layout() if isinstance(i.widget, Button) and i.widget.text == print_lbl),
                None,
            )
            self.assertIsNotNone(btn)
            self.assertEqual(btn.icon, "lock")
            self.assertEqual(btn.sub, expected_sub)
            self._assert_fits(app)

            self.assertTrue(app.press(print_lbl))
            self.assertEqual(app.message, expected_msg)
            self.assertEqual(app.pages, [])
            self.assertEqual(app.tab, "home")

            app.extrusion_lock = False
            app.dirty = True
            btn_unlocked = next(
                (i.widget for i in app.layout() if isinstance(i.widget, Button) and i.widget.text == print_lbl),
                None,
            )
            self.assertIsNotNone(btn_unlocked)
            self.assertEqual(btn_unlocked.icon, "print")
            self.assertEqual(btn_unlocked.sub, "")
            self.assertEqual(btn_unlocked.kind, "primary")


if __name__ == "__main__":
    unittest.main()
