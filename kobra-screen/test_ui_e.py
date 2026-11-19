"""Unit tests for ui-e: ERR-13 (ACE tangle threshold & live speeds) and ERR-25 (Chart incremental updates)."""

import tempfile
import unittest

from PIL import Image, ImageDraw

from fake_moonraker import FakePrinter, serve
from kobra_screen import i18n, render_lvgl, screens
from kobra_screen.app import App
from kobra_screen.moonraker import Moonraker
from kobra_screen.render_pil import SCALE, font, render, text_line_height, text_width, wrap
from kobra_screen.widgets import (
    HEIGHT, WIDTH, Button, Chart, Label, Placed, button_parts, button_sub_text_size, button_text_size, label_unit_size,
)
from test_screen import StrictFakeLvgl


class AceFeaturesAndSpeedsTests(unittest.TestCase):
    def setUp(self):
        self.printer = FakePrinter()
        self.httpd = serve(self.printer, simulate=False)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = App(Moonraker("127.0.0.1", self.httpd.server_address[1]), cache_dir=self.tmp.name)
        self.app.extrusion_lock = False
        self.app.refresh()

    def press(self, *texts, icon=None):
        for text in texts:
            self.assertTrue(self.app.press(text, icon), "button not found: %r" % text)
        self.app.refresh()

    def texts(self):
        return [item.widget.text for item in self.app.layout() if hasattr(item.widget, "text")]

    def _assert_fits(self, app, label):
        draw = ImageDraw.Draw(Image.new("L", (1, 1)))
        placed = app.layout()
        self.assertLessEqual(app.content_bottom, app.content_limit, "%s overflows bottom" % label)
        for item in placed:
            self.assertGreaterEqual(item.x, 0, label)
            self.assertLessEqual(item.x + item.w, WIDTH, label)
            self.assertLessEqual(item.y + item.h, HEIGHT, label)
            w = item.widget
            if isinstance(w, Button) and w.text:
                _, box = button_parts(item)
                if box:
                    if w.sub:
                        self.assertLessEqual(text_width(w.sub, "small") * 1.15, box[2], "%s sub %r" % (label, w.sub))
                        sz = button_sub_text_size(item, box[2])
                        self.assertLessEqual(text_width(w.text, sz) * 1.15, box[2], "%s btn %r" % (label, w.text))
                    else:
                        sz = button_text_size(item)
                        lh = text_line_height(sz)
                        if "\n" not in w.text and box[3] < 2 * lh:
                            self.assertLessEqual(text_width(w.text, sz) * 1.15, box[2], "%s btn %r" % (label, w.text))
                        else:
                            lines = wrap(draw, w.text, font(sz), (box[2] / 1.15) * SCALE)
                            self.assertLessEqual(len(lines) * lh, box[3] + 1.0, "%s btn %r" % (label, w.text))
            elif isinstance(w, Label) and w.text:
                lh = text_line_height(w.size)
                self.assertLessEqual(lh, item.h + 0.5, "%s lbl %r" % (label, w.text))
                if w.unit:
                    total_w = text_width(w.text, w.size) + 2 + text_width(w.unit, label_unit_size(w.size))
                    self.assertLessEqual(total_w * 1.15, item.w, "%s lbl %r" % (label, w.text))
                elif "\n" not in w.text and item.h < 2 * lh:
                    self.assertLessEqual(text_width(w.text, w.size) * 1.15, item.w, "%s lbl %r" % (label, w.text))
        self.assertEqual(render(placed).size, (WIDTH, HEIGHT))

    def test_tangle_threshold_keypad_command_and_fit_tr_en(self):
        self.printer.status["ace_instance_0"]["tangle_protection"] = {"enabled": False, "threshold": 8.0}
        self.app.refresh()
        self.app.open("ace")
        self.press("Özellik")

        self.assertIn("8sn", self.texts())
        self._assert_fits(self.app, "tr:ace_features_tangle")

        # Open threshold keypad and confirm valid range (2-60 s)
        self.press("8sn")
        self.assertIsNotNone(self.app.modal)
        self.assertEqual(self.app.modal.low, 2)
        self.assertEqual(self.app.modal.high, 60)

        # Out-of-range value (<2 or >60) disables "Tamam" and shows range error
        gcodes_before = len(self.printer.gcodes)
        self.press("1")
        self.assertFalse(self.app.press("Tamam"))
        self.assertIn("Sınır dışı: 2–60", self.texts())
        self.assertIsNotNone(self.app.modal)
        self.assertEqual(len(self.printer.gcodes), gcodes_before)

        # Valid threshold sends ACE_TANGLE_PROTECTION THRESHOLD=15
        self.press("5", "Tamam")
        self.assertIsNone(self.app.modal)
        self.assertEqual(self.printer.gcodes[-1], "ACE_TANGLE_PROTECTION THRESHOLD=15")
        self.assertIn("15sn", self.texts())

        # >60 is also rejected
        self.press("15sn")
        self.press("6", "1")
        self.assertFalse(self.app.press("Tamam"))
        self.assertIn("Sınır dışı: 2–60", self.texts())
        self.press("Vazgeç")

        # English layout fits without clipping and displays "15s"
        self.app.set_language("en", persist=False)
        self.assertIn(i18n.tr("Dolaşma koruması", "en"), self.texts())
        self.assertIn("15s", self.texts())
        self._assert_fits(self.app, "en:ace_features_tangle")

    def test_live_feed_and_retract_speeds_override_cached_values(self):
        # Even if older UI-cached speeds exist, ace_instance_0 live fields take priority
        self.app.ace_speeds["feed"] = 70
        self.app.ace_speeds["retract"] = 40
        self.printer.status["ace_instance_0"]["feed_speed"] = 85.0
        self.printer.status["ace_instance_0"]["retract_speed"] = 42
        self.app.refresh()

        self.assertEqual(screens._ace_speeds(self.app, self.app.ace_data()), (85, 42))
        self.app.open("ace")
        self.press("Özellik")
        self.assertIn("85 / 42 mm/s", self.texts())

        self.app.back()
        self.press("Makara", "PLA", "Ayrıntı")
        self.assertIn("85 / 42 mm/s", self.texts())


class ChartIncrementalUpdateTests(unittest.TestCase):
    def _make_recording_lvgl(self):
        fake_lv = StrictFakeLvgl()
        orig_chart_cls = fake_lv.chart
        calls = {"set_point": [], "set_range": [], "refresh": 0}

        class RecordingChart(orig_chart_cls):
            def __getattr__(self, name):
                fn = super().__getattr__(name)
                if name == "set_series_value_by_id":
                    def wrapped(ser, idx, val):
                        calls["set_point"].append((ser, int(idx), int(val)))
                        return fn(ser, idx, val)
                    return wrapped
                if name == "set_axis_range":
                    def wrapped(axis, y_min, y_max):
                        calls["set_range"].append((axis, int(y_min), int(y_max)))
                        return fn(axis, y_min, y_max)
                    return wrapped
                if name == "refresh":
                    def wrapped():
                        calls["refresh"] += 1
                        return fn()
                    return wrapped
                return fn

        object.__setattr__(fake_lv, "chart", RecordingChart)
        return fake_lv, calls

    def test_single_new_point_updates_only_changed_points(self):
        fake_lv, calls = self._make_recording_lvgl()
        orig_lv = render_lvgl.lv
        render_lvgl.lv = fake_lv
        try:
            screen = render_lvgl.LvglScreen(manage_system=False, grab_touch=False)
            noz_g = [210.0] * 60
            noz_t = [200.0] * 60
            bed_g = [60.0] * 60
            bed_t = [59.0] * 60

            def make_placed():
                chart = Chart([
                    (noz_g, "warn", True),
                    (noz_t, "danger", False),
                    (bed_g, "tertiary", True),
                    (bed_t, "info", False),
                ], y_min=0.0, y_max=240.0, point_count=60)
                return [Placed(12, 52, 248, 180, chart)]

            # Initial sync writes all 4 * 60 = 240 points
            screen.sync(make_placed())
            self.assertEqual(len(calls["set_point"]), 240)
            self.assertEqual(len(calls["set_range"]), 1)
            self.assertEqual(calls["refresh"], 1)

            # Single point change: only 1 point in 1 series changed -> only 1 write
            calls["set_point"].clear()
            calls["set_range"].clear()
            calls["refresh"] = 0
            noz_t = noz_t[:-1] + [201.0]
            screen.sync(make_placed())
            self.assertEqual(len(calls["set_point"]), 1)
            self.assertEqual(calls["set_point"][0][1:], (59, 201))
            self.assertEqual(calls["set_range"], [])
            self.assertEqual(calls["refresh"], 1)

            lv_chart = screen.objects[0]
            self.assertEqual(lv_chart._series[0].values, [210] * 60)
            self.assertEqual(lv_chart._series[1].values, [200] * 59 + [201])
            self.assertEqual(lv_chart._series[2].values, [60] * 60)
            self.assertEqual(lv_chart._series[3].values, [59] * 60)

            # Unchanged sync performs zero updates
            calls["set_point"].clear()
            calls["refresh"] = 0
            screen.sync(make_placed())
            self.assertEqual(calls["set_point"], [])
            self.assertEqual(calls["refresh"], 0)
        finally:
            render_lvgl.lv = orig_lv

    def test_short_series_append_and_screen_rebuild_reset(self):
        fake_lv, calls = self._make_recording_lvgl()
        orig_lv = render_lvgl.lv
        render_lvgl.lv = fake_lv
        try:
            screen = render_lvgl.LvglScreen(manage_system=False, grab_touch=False)

            def make_short(n_list, b_list):
                return [Placed(12, 52, 248, 180, Chart([
                    ([210.0], "warn", True),
                    (n_list, "danger", False),
                    ([60.0], "tertiary", True),
                    (b_list, "info", False),
                ], y_min=0.0, y_max=240.0, point_count=60))]

            screen.sync(make_short([24.0], [23.0]))
            self.assertEqual(len(calls["set_point"]), 240)

            # Appending one value to nozzle shifts only the 2 rightmost slots of series 1
            calls["set_point"].clear()
            screen.sync(make_short([24.0, 25.0], [23.0]))
            self.assertEqual(len(calls["set_point"]), 2)
            self.assertEqual([(idx, val) for _, idx, val in calls["set_point"]], [(58, 24), (59, 25)])
            lv_chart = screen.objects[0]
            self.assertEqual(
                lv_chart._series[1].values,
                [render_lvgl.LV_CHART_POINT_NONE] * 58 + [24, 25],
            )

            # Navigating away and back clears _chart_map and re-initializes the new chart
            screen.sync([Placed(12, 52, 248, 44, Label("Kontrol"))])
            self.assertEqual(screen._chart_map, {})
            calls["set_point"].clear()
            screen.sync(make_short([24.0, 25.0], [23.0]))
            self.assertEqual(len(calls["set_point"]), 240)
        finally:
            render_lvgl.lv = orig_lv


if __name__ == "__main__":
    unittest.main()
