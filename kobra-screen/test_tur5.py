"""Tests for UI round 5: real font metrics and one-line button labels, Helix-style bed mesh with finger drag,
ACE driver answers on the screen, heating confirmation, the goto debug hook, touch debug counters and the fixes of
the combined bug report (docs/32-hata-raporu.md)."""

import json
import os
import signal
import tempfile
import threading
import time
import unittest
from unittest import mock

from PIL import Image, ImageDraw, ImageFont

from fake_moonraker import FakePrinter, FakeWifiManager, serve
from kobra_screen import (bed_mesh, beeper, i18n, limits, network, render_lvgl, render_pil, screens, touch_cal,
                          touch_debug, wizards)
from kobra_screen import font_metrics
from kobra_screen.app import App
from kobra_screen.moonraker import Moonraker, MoonrakerError
from kobra_screen.theme import FONT_SIZES
from kobra_screen.widgets import (FIT_SLACK, Button, Label, Placed, button_label, button_parts, button_sub_label,
                                  ellipsize, text_width)
import test_i18n
from test_screen import StrictFakeLvgl
from test_tur4 import SAMPLE_MUTABLE_CFG


class AppCase(unittest.TestCase):
    """A fake printer, an App talking to it and a few helpers."""

    def setUp(self):
        self.printer = FakePrinter()
        self.httpd = serve(self.printer, simulate=False)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = [100.0]
        self.beeps = []
        self.app = self.make_app()
        self.app.refresh()

    def make_app(self, **kwargs):
        opts = dict(cache_dir=self.tmp.name, wifi=FakeWifiManager(), rinkhals_home=self.tmp.name,
                    clock_fn=lambda: self.clock[0])
        opts.update(kwargs)
        app = App(Moonraker("127.0.0.1", self.httpd.server_address[1]), **opts)
        app.extrusion_lock = False
        app.trigger_beep = lambda event, force=False: self.beeps.append(event) or True
        return app

    def texts(self, app=None):
        out = []
        for item in (app or self.app).layout():
            for attr in ("text", "sub"):
                value = getattr(item.widget, attr, None)
                if value:
                    out.append(value)
        return out

    def button(self, label, icon=None, app=None):
        for item in (app or self.app).layout():
            w = item.widget
            if isinstance(w, Button) and w.text == label and (icon is None or w.icon == icon):
                return w
        self.fail("no button %r in %r" % (label, self.texts(app)))

    def press(self, *labels, icon=None):
        for label in labels:
            self.assertTrue(self.app.press(label, icon), "cannot press %r in %r" % (label, self.texts()))
        self.app.refresh()

    def sync_poll(self, rounds=1):
        for _ in range(rounds):
            self.app.refresh()


# ---------------------------------------------------------------------------------------------------------------
# B-03 / B-11: font metrics, one-line button labels, uniform Tools grid
# ---------------------------------------------------------------------------------------------------------------

class FontMetricsTests(unittest.TestCase):
    FONT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "AlibabaSans-Regular.ttf")

    @unittest.skipUnless(render_pil.ui_font_available(), "needs AlibabaSans-Regular.ttf (kobra-screen/fonts or KOBRA_SCREEN_FONT)")
    def test_table_matches_the_ttf_and_never_underestimates(self):
        samples = ["Maintenance", "Unload filament", "Filament yükle", "Tabla seviyeleme", "Çıkar ğüşıİ", "PETG, 240° · Assist",
                   "0123456789", "Wi-Fi", "Heater extruder", "Orta düzey", "MMMMMWWW", "iiiilll", "…", "%d°C"]
        for px in sorted(set(FONT_SIZES.values())):
            pil = ImageFont.truetype(self.FONT, px, layout_engine=ImageFont.Layout.BASIC)
            for text in samples:
                real = pil.getlength(text)
                ours = text_width(text, px)
                # PIL hints (rounds) every advance at small sizes and can be up to ~1 px wider than the unhinted table
                self.assertGreaterEqual(ours, real - 1.5, "%r at %dpx: %.1f < %.1f" % (text, px, ours, real))
                self.assertLessEqual(ours, real * 1.06 + 2, "%r at %dpx: %.1f far above %.1f" % (text, px, ours, real))

    def test_old_table_was_wrong_for_lowercase(self):
        # the first table had 'm' at 0.86 em; the font says 0.921
        self.assertEqual(font_metrics.ADVANCES["m"], 921)
        self.assertEqual(font_metrics.UNITS_PER_EM, 1000)

    def test_ellipsize_cuts_at_a_word_and_fits(self):
        text = "Boş olarak işaretle ve kaydet"
        cut = ellipsize(text, 12, 80)
        self.assertTrue(cut.endswith("…"))
        self.assertLessEqual(text_width(cut, 12), 80)
        self.assertEqual(ellipsize("kısa", 12, 80), "kısa")
        self.assertFalse(cut[:-1].endswith(" "))


class ButtonLabelTests(unittest.TestCase):
    def item(self, text, w, h=46, **kw):
        return Placed(0, 0, w, h, Button(text, lambda: None, "card", **kw))

    def test_never_breaks_inside_a_word(self):
        # the old layout let LVGL wrap "Maintenance" into "Maintenanc" / "e"
        item = self.item("Maintenance", 105, icon="heal", align="left")
        size, text = button_label(item)
        self.assertNotIn("\n", text)
        _, box = button_parts(item)
        self.assertLessEqual(text_width(text, FONT_SIZES[size]) + FIT_SLACK, box[2] + 1e-6)

    def test_shrinks_before_cutting_and_cuts_only_as_last_resort(self):
        wide = self.item("Filament yükle", 121, icon="load", align="left")
        self.assertEqual(button_label(wide), ("small", "Filament yükle"))
        narrow = self.item("Filament yükle ve temizle", 100, icon="load", align="left")
        size, text = button_label(narrow)
        self.assertEqual(size, "small")
        self.assertTrue(text.endswith("…"))
        _, box = button_parts(narrow)
        self.assertLessEqual(text_width(text, 12), box[2])

    def test_sub_buttons_are_fitted_too(self):
        item = Placed(0, 0, 120, 46, Button("a very long file name.gcode", lambda: None, "card", sub="Slot 1", icon="load",
                                            align="left"))
        _, box = button_parts(item)
        size, main, sub = button_sub_label(item, box[2])
        self.assertLessEqual(text_width(main, FONT_SIZES[size]), box[2])
        self.assertTrue(main.endswith("…"))
        self.assertEqual(sub, "Slot 1")

    def test_lvgl_labels_are_clipped_single_lines(self):
        fake_lv = StrictFakeLvgl()
        old_lv = render_lvgl.lv
        render_lvgl.lv = fake_lv
        try:
            screen = render_lvgl.LvglScreen(manage_system=False)
            item = self.item("Filament yükle ve temizle", 100, icon="load", align="left")
            screen.sync([item])
            button = screen.objects[0]
            label = button.text_label
            self.assertEqual(label._long_mode, int(fake_lv.LABEL_LONG_MODE.CLIP))
            self.assertTrue(button.display_text.endswith("…"))
            self.assertEqual(label._text, button.display_text)
            self.assertLessEqual(label._size_wh[1], 30)  # one line high, so it cannot wrap
        finally:
            render_lvgl.lv = old_lv

    def test_pil_preview_draws_the_same_cut_text(self):
        item = self.item("Filament yükle ve temizle", 100, icon="load", align="left")
        _, text = button_label(item)
        canvas = render_pil.Canvas()
        drawn = []
        orig = canvas.text
        canvas.text = lambda box, t, *a, **k: drawn.append(t) or orig(box, t, *a, **k)
        render_pil.draw_button(canvas, item)
        self.assertIn(text, drawn)


class ToolsGridTests(AppCase):
    def test_equal_tiles_one_size_in_both_languages(self):
        for lang in ("tr", "en"):
            app = self.make_app(lang=lang, rinkhals_home=os.path.join(self.tmp.name, lang))
            app.refresh()
            app.select_tab("tools")
            tiles = [i for i in app.layout() if isinstance(i.widget, Button) and i.widget.size == "small"]
            self.assertEqual(len(tiles), 13, lang)
            self.assertLessEqual(max(t.w for t in tiles) - min(t.w for t in tiles), 1, lang)
            self.assertEqual({t.h for t in tiles}, {46})
            self.assertTrue(all(t.widget.right_icon is None for t in tiles))
            for tile in tiles:
                size, text = button_label(tile)
                self.assertEqual(size, "small", "%s: %r" % (lang, tile.widget.text))
                self.assertEqual(text, tile.widget.text, "%s: %r is cut" % (lang, tile.widget.text))

    def test_english_short_names_for_load_and_unload(self):
        app = self.make_app(lang="en", rinkhals_home=os.path.join(self.tmp.name, "en"))
        app.refresh()
        app.select_tab("tools")
        labels = [i.widget.text for i in app.layout() if isinstance(i.widget, Button)]
        self.assertIn("Unload", labels)
        self.assertIn("Load", labels)
        self.assertNotIn("Unload filament", labels)
        # the wizard keeps the long title
        self.assertEqual(i18n.tr("Filament çıkar", "en"), "Unload filament")


class StrictFitTests(unittest.TestCase):
    """Every view of both languages: no button label is cut, nothing is wider than its box, no word is broken."""

    def test_every_view_in_both_languages_fits_with_real_metrics(self):
        helper = test_i18n.I18nTests("test_every_view_and_wizard_step_fits_in_both_languages")
        helper.setUp()
        try:
            checked = 0
            for lang in i18n.language_codes():
                helper.printer = FakePrinter()
                helper.httpd.shutdown()
                helper.httpd.server_close()
                helper.httpd = serve(helper.printer, simulate=False)
                app = helper._make_app(lang=lang, rinkhals_home=os.path.join(helper.tmp.name, "strict_" + lang))
                for name in helper._walk_views(app, lang):
                    for item in app.layout():
                        w = item.widget
                        where = "%s:%s: %r" % (lang, name, getattr(w, "text", None))
                        if isinstance(w, Button) and w.text and not w.sub and w.swatch is None:
                            size, text = button_label(item)
                            _, box = button_parts(item)
                            if "\n" not in w.text:
                                self.assertEqual(text, w.text, where + " is cut")
                                self.assertLessEqual(text_width(text, FONT_SIZES[size]) + FIT_SLACK, box[2] + 1e-6, where)
                            checked += 1
                        elif isinstance(w, Button) and w.sub:
                            _, box = button_parts(item)
                            size, main, sub = button_sub_label(item, box[2])
                            if not w.text.endswith(".gcode"):
                                self.assertEqual((main, sub), (w.text, w.sub), where + " is cut")
                        elif isinstance(w, Label) and w.text and "\n" not in w.text and not w.unit:
                            px = FONT_SIZES[w.size]
                            for word in w.text.split(" "):
                                if w.text.endswith(".gcode") or "Heater" in w.text or "Probe" in w.text:
                                    continue
                                self.assertLessEqual(text_width(word, px), item.w, where + " word %r is broken" % word)
            self.assertGreater(checked, 300)
        finally:
            helper.tearDown()

    def test_home_banner_alert_disconnected_ace_and_external_spool_fit(self):
        # F-04: print banner + alert + ACE card without a connection + external spool used to overflow (413 > 408)
        for lang in ("tr", "en"):
            printer = FakePrinter()
            httpd = serve(printer, simulate=False)
            tmp = tempfile.TemporaryDirectory()
            try:
                app = App(Moonraker("127.0.0.1", httpd.server_address[1]), cache_dir=tmp.name, rinkhals_home=tmp.name,
                          wifi=FakeWifiManager(), lang=lang)
                printer.status["ace_instance_0"]["connection_state"] = "disconnected"
                printer.status["print_stats"]["state"] = "complete"
                app.refresh()
                app.set_spool_amount("external", 120, "g", material="PLA")
                app.set_maintenance_threshold("nozzle", 1.0)
                app.refresh()
                app.layout()
                self.assertLessEqual(app.content_bottom, app.content_limit, lang)
            finally:
                httpd.shutdown()
                httpd.server_close()
                tmp.cleanup()


# ---------------------------------------------------------------------------------------------------------------
# B-07: Helix-style bed mesh
# ---------------------------------------------------------------------------------------------------------------

class BedMeshProjectionTests(unittest.TestCase):
    MATRIX = ((-0.2, -0.1, 0.0), (-0.1, 0.0, 0.1), (0.0, 0.1, 0.2))

    def test_right_angles_equal_the_old_steps_and_other_angles_work(self):
        for rot, expect in ((90, (-0.5, 0.25)), (180, (-0.25, -0.5)), (270, (0.5, -0.25)), (0, (0.25, 0.5))):
            self.assertEqual(bed_mesh._rotate_xy(0.25, 0.5, rot), expect)
        x, y = bed_mesh._rotate_xy(1.0, 0.0, 45)
        self.assertAlmostEqual(x, 0.70710678, places=6)
        self.assertAlmostEqual(y, 0.70710678, places=6)

    def test_continuous_rotation_and_tilt_change_the_picture(self):
        shapes = set()
        for rot, tilt in ((0, 38), (23, 38), (23, 55), (200, 20)):
            quads, outline, _ = bed_mesh.project_mesh_quads(self.MATRIX, 248, 250, rot_deg=rot, tilt=tilt)
            self.assertEqual(len(quads), 4)
            shapes.add(tuple(round(c) for p in outline for c in p))
            for pts, *_ in quads:
                for px, py in pts:
                    self.assertTrue(-1 <= px <= 249 and -1 <= py <= 251, (rot, tilt, px, py))
        self.assertEqual(len(shapes), 4)

    def test_drag_preview_is_small_snaps_and_is_cached_separately(self):
        with tempfile.TemporaryDirectory() as d:
            full = bed_mesh.render_mesh_surface_png(self.MATRIX, 248, 250, rot_deg=31.4, tilt=40, cache_dir=d)
            low = bed_mesh.render_drag_preview_png(self.MATRIX, 248, 250, 31.4, 40, cache_dir=d)
            low2 = bed_mesh.render_drag_preview_png(self.MATRIX, 248, 250, 29.0, 41, cache_dir=d)
            self.assertEqual(low, low2)  # 5 degree steps share one file
            self.assertTrue(os.path.basename(low).startswith("meshlo_"))
            with Image.open(full) as a, Image.open(low) as b:
                self.assertEqual(a.size, (248, 250))
                self.assertEqual(b.size, (124, 125))

    def test_rating_names(self):
        self.assertEqual(bed_mesh._build_mesh_data(self.MATRIX, (0, 1, 0, 1), "klipper", "x").rating_key, "Orta")
        flat = ((0.0, 0.01), (0.02, 0.05))
        self.assertEqual(bed_mesh._build_mesh_data(flat, (0, 1, 0, 1), "klipper", "x").rating_key, "İyi")


class BedMeshPageTests(AppCase):
    def test_page_has_badge_surface_and_remeasure_only(self):
        self.printer.status["bed_mesh"] = {
            "profile_name": "default", "mesh_min": [15.0, 15.0], "mesh_max": [240.0, 235.0],
            "probed_matrix": [[-0.1 + 0.05 * r + 0.01 * c for c in range(5)] for r in range(5)], "profiles": {}}
        self.app.refresh()
        self.app.select_tab("tools")
        self.press("Tabla haritası")
        layout = self.app.layout()
        surfaces = [i for i in layout if type(i.widget).__name__ == "MeshSurface"]
        self.assertEqual(len(surfaces), 1)
        self.assertGreaterEqual(surfaces[0].h, 240)
        self.assertIn("Orta", self.texts())
        buttons = [i.widget.text for i in layout if isinstance(i.widget, Button) and i.y > 52 and i.y < 410]
        self.assertEqual(buttons, ["Yeniden ölç"])
        for gone in ("Yenile", "2B harita", "3B yüzey", "x50", "x100", "x200"):
            self.assertNotIn(gone, self.texts())

    def test_remeasure_is_locked_by_the_heating_lock_and_asks_first(self):
        self.printer.config_files["printer_mutable.cfg"] = SAMPLE_MUTABLE_CFG
        self.app.select_tab("tools")
        self.press("Tabla haritası")
        self.app.refresh_bed_mesh()
        self.app.dirty = True
        before = len(self.printer.gcodes)

        self.app.heating_lock = True
        locked = self.button("Yeniden ölç")
        self.assertIsNone(locked.action)
        self.assertEqual(locked.icon, "lock")
        self.app.heating_lock = False

        self.press("Yeniden ölç")
        texts = self.texts()
        self.assertTrue(any("ölçüm yaklaşık 8 dk sürer" in t for t in texts), texts)
        self.assertEqual(len(self.printer.gcodes), before)  # nothing runs before the answer
        self.press("Vazgeç")
        self.assertEqual(len(self.printer.gcodes), before)
        self.assertIsNone(self.app.wizard)

        self.press("Yeniden ölç", "Başlat")  # uses the existing "Tabla seviyele" wizard
        self.assertIsNotNone(self.app.wizard)
        self.assertEqual(self.app.wizard.wizard.key, "level")

    def test_remeasure_is_off_while_printing(self):
        self.printer.status["print_stats"]["state"] = "printing"
        self.app.refresh()
        self.app.open("bed_mesh")
        self.assertIsNone(self.button("Yeniden ölç").action)

    def test_stock_mesh_is_labelled_and_klipper_mesh_wins(self):
        self.printer.config_files["printer_mutable.cfg"] = SAMPLE_MUTABLE_CFG
        self.app.open("bed_mesh")
        self.app.refresh_bed_mesh()
        self.app.refresh()
        self.assertIn("Stok kayıt · Klipper'da yok", self.texts())
        self.assertLessEqual(self.app.content_bottom, self.app.content_limit)
        self.printer.status["bed_mesh"] = {"profile_name": "default", "probed_matrix": [[0.0, 0.02], [0.03, 0.05]],
                                           "mesh_min": [0, 0], "mesh_max": [1, 1], "profiles": {}}
        self.app.refresh()
        texts = self.texts()
        self.assertNotIn("Stok kayıt · Klipper'da yok", texts)
        self.assertLessEqual(self.app.content_bottom, self.app.content_limit)
        self.assertIn("İyi", texts)
        self.assertIn("0.05 mm", texts)

    def test_english_rating_names_and_calibration_center_do_not_clash(self):
        self.assertEqual(i18n.tr_short("Orta", "en"), "Fair")
        self.assertEqual(i18n.tr("Orta", "en"), "Center")  # F-01: touch calibration keeps its own word
        self.assertEqual(i18n.tr_short("İyi", "en"), "Good")


class MeshDragTests(AppCase):
    """Finger drag on the picture (LVGL events are faked; the real indev supplies the coordinates)."""

    class Event:
        def __init__(self, target, code):
            self.target, self.code = target, code

        def get_current_target_obj(self):
            return self.target

        def get_code(self):
            return self.code

    def setUp(self):
        super().setUp()
        self.printer.config_files["printer_mutable.cfg"] = SAMPLE_MUTABLE_CFG
        self.app.open("bed_mesh")
        self.app.refresh_bed_mesh()
        self.fake_lv = StrictFakeLvgl()
        self.old_lv = render_lvgl.lv
        render_lvgl.lv = self.fake_lv
        self.addCleanup(lambda: setattr(render_lvgl, "lv", self.old_lv))
        self.screen = render_lvgl.LvglScreen(manage_system=False)
        self.screen.sync(self.app.layout())
        self.image = next(o for o in self.screen.objects if getattr(o, "_mesh_key", None) is not None)
        self.point = [100, 200]
        self.screen._read_point_xy = lambda: tuple(self.point)

    def fire(self, name):
        self.screen._on_mesh_event(self.Event(self.image, getattr(self.fake_lv.EVENT_CODE, name)))

    @unittest.skipUnless(render_pil.ui_font_available(), "needs AlibabaSans-Regular.ttf (kobra-screen/fonts or KOBRA_SCREEN_FONT)")
    def test_horizontal_drag_turns_vertical_drag_tilts_and_release_draws_exact(self):
        full_path = self.image._loaded_path
        self.fire("PRESSED")
        self.point[:] = [150, 200]  # +50 px
        self.fire("PRESSING")
        state = self.screen._mesh_state_of(self.image)
        self.assertAlmostEqual(state.rot, 50 * render_lvgl.LvglScreen.MESH_ROT_PER_PX, places=3)
        self.assertEqual(self.image._loaded_path, full_path)  # the drag preview is set_src only; path stays
        self.assertTrue(os.path.basename(self.image._src).startswith("meshlo_"))
        self.point[:] = [150, 260]  # +60 px down: tilt
        self.fire("PRESSING")
        self.assertAlmostEqual(state.tilt, bed_mesh.clamp_tilt(bed_mesh.DEFAULT_TILT + 60 * render_lvgl.LvglScreen.MESH_TILT_PER_PX))
        # the app already knows the angles but no layout was requested
        self.app.dirty = False
        self.fire("PRESSING")
        self.assertFalse(self.app.dirty)
        self.fire("RELEASED")
        self.assertTrue(self.app.dirty)
        self.assertAlmostEqual(self.app.bed_mesh_rot, state.rot, places=3)
        self.assertTrue(os.path.basename(self.image._src).startswith("mesh_"))
        self.assertFalse(state.dragging)
        with Image.open(self.image._src) as im:
            self.assertGreater(im.size[1], 200)

    def test_layout_during_a_drag_does_not_redraw_the_picture(self):
        self.fire("PRESSED")
        self.point[:] = [130, 200]
        self.fire("PRESSING")
        shown = self.image._src
        self.app.bed_mesh_drag(200.0, 40.0, final=False)
        self.screen.sync(self.app.layout())  # e.g. a poll made the screen dirty
        self.assertEqual(self.image._src, shown)
        self.fire("RELEASED")

    def test_one_callback_object_serves_every_picture(self):
        # Rinkhals' lvgl keeps each distinct callback in a class-level dict forever: no lambda per picture
        callbacks = {cb for cb, _, _ in self.image._callbacks}
        self.assertEqual(callbacks, {self.screen._on_mesh_event})
        for _ in range(3):
            self.app.back()
            self.app.open("bed_mesh")
            self.screen.sync(self.app.layout())
            images = [o for o in self.screen.objects if getattr(o, "_mesh_key", None) is not None]
            for image in images:
                self.assertEqual({cb for cb, _, _ in image._callbacks}, {self.screen._on_mesh_event})
        self.assertEqual(len(self.screen._mesh_map), 2)  # pointer + id of the single live picture

    def test_released_at_the_start_angle_still_restores_the_exact_picture(self):
        self.fire("PRESSED")
        self.point[:] = [101, 200]
        self.fire("PRESSING")
        self.point[:] = [100, 200]
        self.fire("PRESSING")
        self.fire("RELEASED")
        self.assertTrue(os.path.basename(self.image._src).startswith("mesh_"))


# ---------------------------------------------------------------------------------------------------------------
# B-12: heating confirmation
# ---------------------------------------------------------------------------------------------------------------

class HeatingConfirmTests(AppCase):
    def targets(self):
        return self.printer.status["extruder"]["target"], self.printer.status["heater_bed"]["target"]

    def test_preheat_asks_and_runs_only_after_yes(self):
        self.press("Ön ısıtma")
        self.assertIn("Isıtma başlatılsın mı? (nozul 210°C, tabla 60°C)", self.texts())
        self.assertEqual(self.targets(), (0.0, 0.0))
        self.press("Vazgeç")
        self.assertEqual(self.targets(), (0.0, 0.0))
        self.press("Ön ısıtma", "Isıt")
        self.assertEqual(self.targets(), (210.0, 60.0))

    def test_no_question_when_already_heating_or_switching_off_or_locked(self):
        self.press("Kontrol", "PLA", "Isıt")
        self.press("+5°C")  # already heating: adjusting does not ask again
        self.assertEqual(self.targets()[0], 215.0)
        self.press("Hepsini kapat")
        self.assertEqual(self.targets(), (0.0, 0.0))
        self.app.heating_lock = True
        self.assertIsNone(self.button("PLA").action)  # locked buttons stay locked

    def test_single_heater_texts(self):
        self.press("Kontrol", "Hedef", "1", "5", "0", "Tamam")
        self.assertIn("Isıtma başlatılsın mı? (nozul 150°C)", self.texts())
        self.press("Vazgeç")
        self.app.request_heat({"heater_bed": 55})
        self.assertIn("Isıtma başlatılsın mı? (tabla 55°C)", self.texts())

    def test_dont_ask_again_is_persistent(self):
        self.press("Ön ısıtma")
        self.press("Bir daha sorma")  # tick the box
        self.assertTrue(self.app.modal.option_state)
        self.press("Isıt")
        self.assertTrue(self.app.heating_confirm_skip)
        path = i18n.resolve_persistent_config_path(rinkhals_home=self.tmp.name, app_name=self.app.app_name)
        with open(path, encoding="utf-8") as f:
            self.assertTrue(json.load(f)["heating_confirm_skip"])
        self.app.gcode("TURN_OFF_HEATERS")
        app2 = self.make_app()
        app2.refresh()
        self.assertTrue(app2.heating_confirm_skip)
        app2.request_heat({"extruder": 200})
        self.assertIsNone(app2.modal)  # no question now
        self.assertEqual(self.printer.status["extruder"]["target"], 200.0)

    def test_system_page_switch_turns_the_question_back_on(self):
        self.app.set_heating_confirm_skip(True)
        self.app.open("system")
        btn = self.button("Isıtmadan önce sor")
        self.assertFalse(btn.toggle)
        self.press("Isıtmadan önce sor")
        self.assertFalse(self.app.heating_confirm_skip)
        self.assertTrue(self.button("Isıtmadan önce sor").toggle)

    def test_english_texts(self):
        app = self.make_app(lang="en", rinkhals_home=os.path.join(self.tmp.name, "en"))
        app.refresh()
        app.request_heat({"extruder": 210, "heater_bed": 60})
        texts = self.texts(app)
        self.assertIn("Start heating? (nozzle 210°C, bed 60°C)", texts)
        self.assertIn("Don't ask again", texts)
        self.assertIn("Heat", texts)


# ---------------------------------------------------------------------------------------------------------------
# B-02: what the ACE driver says on the screen
# ---------------------------------------------------------------------------------------------------------------

class AceAnswerTests(AppCase):
    def setUp(self):
        super().setUp()
        self.app.select_tab("filament")

    def event_texts(self):
        return [(e["severity"], e["source"], e["message"]) for e in self.app.event_log]

    def test_info_lines_reach_the_event_log_and_the_toast(self):
        self.press("PLA", icon="thermo")
        self.assertIn("Kurutma başladı", self.texts())
        infos = [e for e in self.event_texts() if e[2].startswith("ACE[0]: Dryer started")]
        self.assertEqual(len(infos), 1)
        self.assertEqual(infos[0][:2], ("ok", "ace"))
        self.assertFalse(self.app.ace_pending)

    def test_identical_info_lines_are_logged_once(self):
        for _ in range(4):
            self.printer.respond("// ACE[0]: Retry in 5s")
            time.sleep(0.002)
        self.app._sync_gcode_errors()
        self.printer.respond("// ACE[0]: Retry in 5s")
        self.app._sync_gcode_errors()
        retry = [e for e in self.event_texts() if "Retry in 5s" in e[2]]
        self.assertEqual(len(retry), 1)
        self.assertEqual(retry[0][0], "warn")

    def test_driver_error_becomes_a_notice_a_beep_and_an_event(self):
        self.printer.fail_next_ace = "Dryer start failed: FORBIDDEN"
        self.press("PLA", icon="thermo")
        self.assertNotIn("Kurutma başladı", self.texts())  # Moonraker said 200, the driver refused
        self.assertIn("ACE[0]: Dryer start failed: FORBIDDEN", self.texts())
        self.assertIn("error", self.beeps)
        self.assertIn(("error", "ace", "ACE[0]: Dryer start failed: FORBIDDEN"), self.event_texts())

    def test_silent_driver_falls_back_to_the_done_text_after_a_wait(self):
        self.printer.ace_info_lines = False
        self.press("PLA", icon="thermo")
        self.assertNotIn("Kurutma başladı", self.texts())
        self.assertTrue(self.app.ace_pending)
        self.clock[0] += 5.0
        self.app.tick_timers()
        self.assertIn("Kurutma başladı", self.texts())
        self.assertFalse(self.app.ace_pending)

    def test_stopping_window_follows_the_ace_status_not_a_fixed_time(self):
        self.printer.status["ace_instance_0"]["dryer_status"] = {"status": "drying", "target_temp": 55,
                                                                 "remain_time": 1200, "duration": 240}
        self.app.refresh()
        orig = self.printer.run_gcode
        # the driver is slow: it accepts ACE_STOP_DRYING but the status changes only later
        self.printer.run_gcode = lambda script: None if "ACE_STOP_DRYING" in script else orig(script)
        self.press("Durdur")
        self.assertEqual(self.app.ace_stopping_kind(), "drying")
        self.clock[0] += 10.0  # the old fixed 4 s window would have ended long ago
        self.app.refresh()
        self.assertEqual(self.app.ace_stopping_kind(), "drying")
        self.assertIn("Durduruluyor…", self.texts())
        self.printer.status["ace_instance_0"]["dryer_status"] = {"status": "stop", "target_temp": 0, "duration": 0,
                                                                 "remain_time": 0}
        self.app.refresh()
        self.assertIsNone(self.app.ace_stopping_kind())

    def test_stopping_window_has_a_backup_timeout(self):
        self.printer.status["ace_instance_0"]["dryer_status"] = {"status": "drying", "target_temp": 55,
                                                                 "remain_time": 1200, "duration": 240}
        self.app.refresh()
        self.printer.run_gcode = lambda script: None
        self.press("Durdur")
        self.clock[0] += 31.0
        self.assertIsNone(self.app.ace_stopping_kind())

    def test_driver_error_ends_the_stopping_window_at_once(self):
        self.printer.status["ace_instance_0"]["dryer_status"] = {"status": "drying", "target_temp": 55,
                                                                 "remain_time": 1200, "duration": 240}
        self.app.refresh()
        self.printer.fail_next_ace = "Dryer stop failed: timeout"
        self.press("Durdur")
        self.assertIsNone(self.app.ace_stopping_kind())
        self.assertIn("ACE[0]: Dryer stop failed: timeout", self.texts())
        self.assertIsNotNone(self.button("Durdur").action)  # can be tried again

    def test_failed_http_call_ends_the_stopping_window(self):
        self.printer.status["ace_instance_0"]["dryer_status"] = {"status": "drying", "target_temp": 55,
                                                                 "remain_time": 1200, "duration": 240}
        self.app.refresh()
        self.printer.fail_next_gcode = "Moonraker busy"
        self.press("Durdur")
        self.assertIsNone(self.app.ace_stopping_kind())
        self.assertFalse(self.app.ace_pending)

    def test_error_that_arrives_on_its_own_raises_a_notice_and_a_beep(self):
        app = self.make_app()
        app.refresh()
        self.printer.respond("!! ACE[0]: Filament tangle suspected")
        app._sync_gcode_errors()
        self.assertIn("ACE[0]: Filament tangle suspected", self.texts(app))
        self.assertIn("error", self.beeps)

    def test_old_errors_do_not_beep_after_a_restart(self):
        self.printer.gcode_store.append({"message": "!! ACE[0]: very old", "time": 1000.0, "type": "response"})
        before = len(self.beeps)
        self.app._sync_gcode_errors()
        self.assertEqual(len(self.beeps), before)
        self.assertIn(("error", "ace", "ACE[0]: very old"), self.event_texts())

    def test_shutdown_state_is_translated_and_has_no_reconnect_button(self):
        self.printer.status["ace_instance_0"]["connection_state"] = "shutdown"
        self.app.refresh()
        texts = self.texts()
        self.assertIn("kapatıldı", texts)
        self.assertIn("Klipper yeniden başlatılmalı", texts)
        self.assertNotIn("Yeniden bağlan", texts)
        self.assertEqual(screens._conn_state_tr("shutdown", "en"), "shut down")

    def test_tangle_protection_switch_appears_with_the_new_driver(self):
        self.app.open("ace")
        self.press("Özellik")
        self.assertNotIn("Dolaşma koruması", self.texts())
        self.printer.status["ace_instance_0"]["tangle_protection"] = {"enabled": False, "threshold": 4}
        self.app.refresh()
        self.assertIn("Dolaşma koruması", self.texts())
        self.assertEqual(screens._tangle_protection(self.app, self.app.ace_data()), (True, False, 4))
        self.assertLessEqual(self.app.content_bottom, self.app.content_limit)  # the extra row still fits
        # the switch sits in the "Dolaşma koruması" row: its button says "kapalı" and turns the protection on
        tangle_row = next(i for i in self.app.layout() if getattr(i.widget, "text", "").startswith("Dolaşma koruması"))
        switch = next(i.widget for i in self.app.layout()
                      if isinstance(i.widget, Button) and i.widget.text == "kapalı" and abs(i.y - tangle_row.y) < 3)
        switch.action()
        self.assertEqual(self.printer.gcodes[-1], "ACE_TANGLE_PROTECTION ENABLE=1")

    def test_assist_off_uses_the_instance_of_the_ace_data(self):
        self.printer.status["ace_instance_0"]["feed_assist_slot"] = 2
        self.printer.status["ace_instance_0"]["instance"] = 0
        self.app.refresh()
        self.app.open("ace")
        self.press("İşlem", "Assist kapat")
        self.assertIn("ACE_DISABLE_FEED_ASSIST INSTANCE=0 INDEX=2", self.printer.gcodes[-1])


# ---------------------------------------------------------------------------------------------------------------
# A-01 .. A-03: heating lock
# ---------------------------------------------------------------------------------------------------------------

class HeatingLockTests(AppCase):
    def test_a01_reprint_from_history_is_locked(self):
        self.app.heating_lock = True
        self.app.refresh_history()
        job = self.printer.history_jobs[0]
        self.app.open("history_detail", job["job_id"])
        btn = self.button("Tekrar yazdır")
        self.assertIsNone(btn.action)
        self.assertEqual(btn.icon, "lock")
        self.assertFalse(self.app.start_print("kutu_08.gcode"))
        self.assertEqual(self.printer.status["print_stats"]["state"], "standby")

    def test_a01_reprint_works_when_unlocked(self):
        self.app.refresh_history()
        job = next(j for j in self.printer.history_jobs if j.get("exists", True))
        self.app.open("history_detail", job["job_id"])
        self.press("Tekrar yazdır")
        self.assertEqual(self.printer.status["print_stats"]["state"], "printing")

    def test_a02_ace_load_unload_buttons_are_locked(self):
        self.app.heating_lock = True
        self.app.select_tab("filament")
        self.press("PETG")
        for label in ("Yükle", "Geri çek"):
            btn = self.button(label)
            self.assertIsNone(btn.action, label)
            self.assertEqual(btn.icon, "lock")
        self.app.back()
        self.app.open("ace")
        self.press("İşlem")
        for label in ("Akıllı yükle", "Akıllı çıkar", "Tümünü çıkar"):
            btn = self.button(label)
            self.assertIsNone(btn.action, label)
            self.assertEqual(btn.icon, "lock")
        before = len(self.printer.gcodes)
        self.assertFalse(self.app.ace_gcode("ACE_CHANGE_TOOL TOOL=0"))  # also refused at the command layer
        self.assertEqual(len(self.printer.gcodes), before)

    def test_a03_script_heats_printer_follows_the_real_macros(self):
        heats = ["G9111 BEDTEMP=60 EXTRUDERTEMP=210", "LEVIQ_PREHEATING", "LEVIQ_WIPING", "_ACE_PRE_TOOLCHANGE",
                 "_ACE_POST_TOOLCHANGE", "_ACE_PREPARE_FOR_RETRACTION", "_EXTRUDE", "_RETRACT", "LOAD_FILAMENT",
                 "ACE_CHANGE_TOOL TOOL=1", "ACE_FULL_UNLOAD TOOL=ALL", "ACE_SMART_LOAD", "ACE_SMART_UNLOAD",
                 "G28\nLEVIQ", "PID_CALIBRATE HEATER=extruder TARGET=200", "M104 S200"]
        for script in heats:
            self.assertTrue(App.script_heats_printer(script), script)
        quiet = ["NOZZLE_WIPE_SEQUENCE", "G28", "M104 S0", "ACE_START_DRYING TEMP=45 DURATION=240", "ACE_STOP_DRYING",
                 "ACE_FEED T=0 LENGTH=50", "TURN_OFF_HEATERS"]
        for script in quiet:
            self.assertFalse(App.script_heats_printer(script), script)
        # the "clean" wizard stays locked through its own M104 step
        self.assertTrue(App.wizard_heats("clean"))


# ---------------------------------------------------------------------------------------------------------------
# D / F findings of the combined report
# ---------------------------------------------------------------------------------------------------------------

class LogicFixTests(AppCase):
    def test_d01_concurrent_config_and_event_writes_lose_nothing(self):
        errors = []

        def save(i):
            try:
                self.app._save_persistent_config({"key_%d" % i: i})
                self.app.log_event("event %d" % i, source="app")
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=save, args=(i,)) for i in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        path = i18n.resolve_persistent_config_path(rinkhals_home=self.tmp.name, app_name=self.app.app_name)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual({k for k in data if k.startswith("key_")}, {"key_%d" % i for i in range(16)})
        with open(self.app.events_path, encoding="utf-8") as f:
            saved = json.load(f)
        events = saved["events"] if isinstance(saved, dict) else saved
        self.assertEqual(len([e for e in events if e["message"].startswith("event ")]), 16)
        leftovers = [n for n in os.listdir(os.path.dirname(path)) if ".tmp." in n]
        self.assertEqual(leftovers, [])

    def test_d03_null_answers_do_not_kill_the_poll(self):
        app = self.app
        with mock.patch.object(app.client, "query", return_value=None):
            app.refresh()  # no exception
        self.assertFalse(app.state.connected is None)
        with mock.patch.object(app.client, "info", return_value=None):
            app.refresh()
        app.refresh()
        self.assertTrue(app.state.connected)
        self.printer.status["ace_instance_0"]["feed_assist_slot"] = None
        app.ace_stopping = ("assist", app._clock() + 10)
        app.refresh()
        self.assertIsNone(app.ace_stopping_kind())

    def test_d03_client_rejects_non_dict_payloads(self):
        client = Moonraker("127.0.0.1", self.httpd.server_address[1])
        with mock.patch.object(client, "_request", return_value=["not", "a", "dict"]):
            with self.assertRaises(MoonrakerError):
                client.info()
            with self.assertRaises(MoonrakerError):
                client.query({"extruder": None})

    def test_d04_blocking_wizard_step_cannot_be_cancelled_and_keeps_the_printer_busy(self):
        self.app.start_wizard("level")
        self.press("İleri")  # "Isıtma" step: non-blocking, cancellable while heating
        run = self.app.wizard
        self.assertFalse(run.step.blocking)
        self.assertIsNotNone(self.button("İptal").action)
        run.goto(self.app, 2)  # "Ölçüm" step: blocking LEVIQ, G-code running
        self.assertTrue(run.step.blocking)
        run.running = True
        self.assertIsNone(self.button("İptal").action)
        self.assertFalse(self.app.idle)
        run.running = False
        self.assertIsNotNone(self.button("İptal").action)
        self.assertTrue(self.app.idle)

    def test_d05_temporary_config_is_merged_not_replacing(self):
        name = "kobra-test-%d" % os.getpid()
        tmp_dir = "/tmp/rinkhals/apps"
        tmp_cfg = os.path.join(tmp_dir, name + ".config")
        home = tempfile.mkdtemp(dir=self.tmp.name)
        os.makedirs(os.path.join(home, "apps"))
        with open(os.path.join(home, "apps", name + ".config"), "w") as f:
            json.dump({"heating_lock": True, "display_brightness": 40}, f)
        os.makedirs(tmp_dir, exist_ok=True)
        self.addCleanup(lambda: os.path.exists(tmp_cfg) and os.remove(tmp_cfg))
        with open(tmp_cfg, "w") as f:
            json.dump({"moonraker_port": 7125}, f)
        # rinkhals_home=None means "also read /tmp/rinkhals/apps"; the persistent file is found via RINKHALS_HOME
        with mock.patch.dict(os.environ, {"RINKHALS_HOME": home}):
            app = App(Moonraker("127.0.0.1", 1), app_name=name, cache_dir=self.tmp.name, wifi=FakeWifiManager())
        self.assertTrue(app.heating_lock)
        self.assertEqual(app.display_brightness, 40)

    def test_d07_null_slots_do_not_crash(self):
        self.printer.status["ace_instance_0"]["slots"] = None
        self.app.refresh()
        self.assertFalse(self.app.spool_info("0")["configured"] and False)
        self.app.set_spool_amount("0", 100, "g", material="PLA")
        for tab in ("home", "filament"):
            self.app.select_tab(tab)
            self.app.layout()

    def test_d08_failed_metadata_is_not_remembered_as_empty(self):
        with mock.patch.object(self.app.client, "metadata", side_effect=MoonrakerError("busy")):
            self.app.load_metadata("kutu_08.gcode")
        self.assertNotIn("kutu_08.gcode", self.app.meta)
        self.app.load_metadata("kutu_08.gcode")  # a retry works
        self.assertIn("kutu_08.gcode", self.app.meta)

    def test_d09_failed_blocking_step_runs_the_cancel_script_and_save_config_closes_the_wizard(self):
        self.app.start_wizard("level")
        self.press("İleri")
        run = self.app.wizard
        deadline = time.time() + 5
        while run.running and time.time() < deadline:
            time.sleep(0.01)
        # now a failing blocking step (index 2: "Ölçüm" / LEVIQ)
        self.printer.fail_next_gcode = "Probe triggered prior to movement"
        run.goto(self.app, 2)
        deadline = time.time() + 5
        while run.running and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(run.step.blocking)
        self.assertTrue(run.failed)
        self.assertEqual(self.printer.gcodes[-1], "M104 S0\nM140 S0\nM106 S0")  # heaters + fan switched off by cancel script
        self.app.end_wizard(cancelled=True)

        self.app.start_wizard("shaper")
        self.press("İleri")
        deadline = time.time() + 5
        while self.app.wizard.running and time.time() < deadline:
            time.sleep(0.01)
        self.app.refresh()
        self.press("İleri")  # measurement done -> "Kaydet" step
        self.assertEqual(self.app.wizard.step.title, "Kaydet")
        self.press("Kaydet")
        self.assertIn("SAVE_CONFIG", self.printer.gcodes[-1])
        self.assertIsNone(self.app.wizard)

    def test_d10_no_gcode_is_sent_while_klipper_is_shut_down(self):
        self.printer.klippy = "shutdown"
        self.app.refresh()
        before = len(self.printer.gcodes)
        self.assertFalse(self.app.gcode("M105"))
        self.assertEqual(len(self.printer.gcodes), before)
        self.assertIn("Klipper hazır değil", self.texts())

    def test_d11_pwm_channel_is_unexported_even_when_period_restore_fails(self):
        with tempfile.TemporaryDirectory() as d:
            chip = os.path.join(d, "pwmchip0")
            base = os.path.join(chip, "pwm0")
            os.makedirs(chip)
            for name in ("export", "unexport"):
                open(os.path.join(chip, name), "w").close()
            writes = []

            def make_base():
                os.makedirs(base, exist_ok=True)
                for name, value in (("enable", "0"), ("period", "0"), ("duty_cycle", "0")):
                    with open(os.path.join(base, name), "w") as f:
                        f.write(value)

            b = beeper.Beeper(pwm_chip=chip, pwm_index=0, sleep_fn=lambda s: None)
            make_base()
            real_write = beeper._write_sysfs

            def fake_write(path, value):
                writes.append((os.path.basename(path), value))
                if path.endswith("period") and value == "0":
                    raise OSError(22, "Invalid argument")
                return real_write(path, value)

            with mock.patch.object(beeper, "_write_sysfs", fake_write), \
                    mock.patch.object(b, "_ensure_pwm_exported", return_value=(True, True)):
                b._play_pwm([(1000, 10, 0)])
            self.assertIn(("unexport", "0"), writes)

    def test_f02_per_instance_dryer_limit_does_not_crash_the_filament_tab(self):
        self.printer.settings.setdefault("ace", {})["max_dryer_temperature"] = "60,1:55"
        self.app.state.settings = {}
        self.app.refresh()
        self.app.select_tab("filament")
        self.assertTrue(self.app.layout())
        self.assertEqual(limits.ace_instance_value("60,1:55", 0, 60), "60")
        self.assertEqual(limits.ace_instance_value("60,1:55", 1, 60), "55")
        self.assertEqual(limits.ace_instance_value("1:55", 0, 60), 60)
        self.assertEqual(limits.ace_instance_value("80", 0, 60), "80")
        self.press("Özel")  # keypad low bound never exceeds the limit
        self.printer.settings["ace"]["max_dryer_temperature"] = "20"
        self.app.state.settings = {}
        self.app.refresh()

    def test_f05_slot_edit_handles_rgba_empty_slot_and_spaces(self):
        slots = self.printer.status["ace_instance_0"]["slots"]
        slots[2].update(color=[220, 40, 40, 255], material="PLA Basic", status="ready")
        self.app.refresh()
        self.app.select_tab("filament")
        self.app.open("slot", 2)
        self.printer.gcodes.clear()
        self.press("PETG")  # RGBA colour: no TypeError, the alpha is dropped
        self.assertEqual(self.printer.gcodes[-1], "ACE_SET_SLOT T=2 COLOR=220,40,40 MATERIAL=PETG TEMP=240")
        slots[3].update(status="empty", color=[0, 0, 0], material="")
        self.app.refresh()
        self.app.back()
        self.app.open("slot", 3)
        self.press("PLA")
        self.assertIn("COLOR=245,245,245", self.printer.gcodes[-1])
        slots[1].update(material="PLA Basic", status="ready", color=[10, 20, 30])
        self.app.refresh()
        self.app.back()
        self.app.open("slot", 1)
        self.printer.gcodes.clear()
        slot_page = self.app.layout()
        swatch = next(i.widget for i in slot_page if isinstance(i.widget, Button) and i.widget.swatch is not None)
        swatch.action()
        self.assertIn("MATERIAL=PLA_Basic", self.printer.gcodes[-1])

    def test_f06_speed_shown_after_setting_it(self):
        self.app.select_tab("filament")
        self.app.open("slot_detail", 0)
        self.press("Sürme hızı", "9", "0", "Tamam")
        self.assertTrue(any("90 / " in t for t in self.texts()), self.texts())
        self.app.back()
        self.app.back()
        self.app.open("ace")
        self.press("Özellik")
        self.assertTrue(any(t.startswith("90 / ") for t in self.texts()), self.texts())

    def test_f08_jog_buttons_stop_at_the_axis_limits(self):
        self.printer.status["toolhead"]["axis_minimum"] = [-19.5, -6.0, -4.0, 0]
        self.printer.status["toolhead"]["axis_maximum"] = [278.5, 277.0, 262.0, 0]
        self.printer.status["toolhead"]["homed_axes"] = "xyz"
        self.printer.status["toolhead"]["position"] = [268.5, 100.0, 10.0, 0]
        self.app.refresh()
        self.press("Kontrol", "Eksen", "50 mm")
        self.assertIsNone(self.button("X+", icon="right").action)
        self.assertIsNotNone(self.button("X-", icon="left").action)
        self.assertIsNotNone(self.button("Y+", icon="up").action)
        self.press("10 mm")
        self.assertIsNotNone(self.button("X+", icon="right").action)  # 278.5 is still inside


# ---------------------------------------------------------------------------------------------------------------
# E findings: touch grab, signals, Wi-Fi, calibration reader
# ---------------------------------------------------------------------------------------------------------------

class LvglLayerTests(unittest.TestCase):
    def setUp(self):
        self.fake_lv = StrictFakeLvgl()
        self.old_lv = render_lvgl.lv
        render_lvgl.lv = self.fake_lv
        self.addCleanup(lambda: setattr(render_lvgl, "lv", self.old_lv))

    def test_e05_grab_is_taken_once_and_close_does_not_grab_again(self):
        screen = render_lvgl.LvglScreen(manage_system=False)
        self.assertEqual(self.fake_lv.grabbed_count, 1)
        screen._grab_touch("again")  # already held: no second EVIOCGRAB
        self.assertEqual(self.fake_lv.grabbed_count, 1)
        screen.set_raw_capture(True)
        self.assertEqual(self.fake_lv.released_count, 1)
        screen.set_raw_capture(True)  # repeated: nothing
        screen.set_raw_capture(False)
        self.assertEqual(self.fake_lv.grabbed_count, 2)
        screen.set_raw_capture(False)  # not capturing: must not grab
        self.assertEqual(self.fake_lv.grabbed_count, 2)
        screen.close()
        self.assertEqual(self.fake_lv.grabbed_count, 2)
        self.assertEqual(self.fake_lv.released_count, 2)
        self.assertFalse(self.fake_lv.grabbed)

    def test_e05_close_without_raw_capture_never_grabs(self):
        screen = render_lvgl.LvglScreen(manage_system=False)
        screen.close()
        self.assertEqual(self.fake_lv.grabbed_count, 1)
        self.assertEqual(self.fake_lv.released_count, 1)

    def test_e10_waking_up_clears_the_ignore_flag(self):
        screen = render_lvgl.LvglScreen(manage_system=False, grab_touch=False)
        screen._asleep = True
        screen._ignore_wake_until_release = True
        screen.set_sleep(False)
        self.assertFalse(screen._ignore_wake_until_release)

    def test_e01_signal_stop_exits_with_a_failure_code(self):
        screen = render_lvgl.LvglScreen(manage_system=False, grab_touch=False)
        captured = []

        class App:
            dirty = False
            display_brightness = 100
            display_asleep = False
            _lock = None

            def layout(self):
                return []

            def tick_timers(self):
                captured[0](signal.SIGTERM)  # the watchdog's kill arrives

        with mock.patch.object(render_lvgl, "_install_signal_stop", lambda fn: captured.append(fn)), \
                mock.patch.object(render_lvgl.time, "sleep", lambda s: None):
            with self.assertRaises(SystemExit) as ctx:
                screen.run(App())
        self.assertEqual(ctx.exception.code, 128 + signal.SIGTERM)

    def test_e01_request_stop_still_exits_cleanly(self):
        screen = render_lvgl.LvglScreen(manage_system=False, grab_touch=False)

        class App:
            dirty = False
            display_brightness = 100
            display_asleep = False
            _lock = None

            def layout(self):
                return []

            def tick_timers(self):
                screen.request_stop()

        with mock.patch.object(render_lvgl, "_install_signal_stop", lambda fn: None), \
                mock.patch.object(render_lvgl.time, "sleep", lambda s: None):
            screen.run(App())  # returns, no SystemExit

    def test_touch_debug_mode_does_not_grab_and_counts(self):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "debug.log")
            with mock.patch.dict(os.environ, {"KOBRA_TOUCH_DEBUG": "1", "KOBRA_TOUCH_DEBUG_LOG": log,
                                              "KOBRA_SCREEN_EVDEV": os.path.join(d, "missing-event")}):
                screen = render_lvgl.LvglScreen(manage_system=False)
                self.assertEqual(self.fake_lv.grabbed_count, 0)
                self.assertIsNotNone(screen._touch_debug)
                pressed = int(self.fake_lv.EVENT_CODE.PRESSED)
                for _ in range(3):
                    screen._on_touch_activity(type("E", (), {"get_code": lambda s: pressed})())
                self.assertEqual(screen._touch_debug.lv_pressed, 3)
                screen.close()
            with open(log, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("lv_pressed=3", text)
            self.assertIn("not grabbing", text)

    def test_touch_debug_is_off_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KOBRA_TOUCH_DEBUG", None)
            screen = render_lvgl.LvglScreen(manage_system=False)
            self.assertIsNone(screen._touch_debug)
            self.assertEqual(self.fake_lv.grabbed_count, 1)


class TouchDebugTests(unittest.TestCase):
    @staticmethod
    def packet(ev_type, code, value):
        import struct
        return struct.pack("llHHi", 0, 0, ev_type, code, value)

    def test_counts_kernel_presses_and_missed_ones(self):
        import struct
        now = [0.0]
        data = [(self.packet(1, 330, 1) + self.packet(0, 0, 0) + self.packet(1, 330, 0) + self.packet(0, 0, 0)) * 3]

        def read(fd, n):
            if data:
                return data.pop(0)
            raise BlockingIOError

        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "t.log")
            td = touch_debug.TouchDebug("/dev/null", log_path=log, clock=lambda: now[0], open_fn=lambda p: 5,
                                        read_fn=read, close_fn=lambda fd: None)
            pressed, released, clicked = 1, 11, 10
            td.note_lv(pressed, pressed, released, clicked)
            td.note_lv(released, pressed, released, clicked)
            td.note_lv(clicked, pressed, released, clicked)
            td.poll()
            self.assertEqual((td.raw_down, td.raw_up), (3, 3))
            self.assertEqual(td.missed, 0)  # not judged until the panel was quiet
            now[0] = 3.0
            td.poll()
            self.assertEqual(td.missed, 2)  # three kernel presses, LVGL saw one
            td.close()
            with open(log, encoding="utf-8") as f:
                self.assertIn("raw_down=3 raw_up=3 lv_pressed=1 lv_released=1 lv_clicked=1 missed_press=2", f.read())

    def test_enabled_only_by_the_variable(self):
        self.assertFalse(touch_debug.enabled({}))
        self.assertFalse(touch_debug.enabled({"KOBRA_TOUCH_DEBUG": "0"}))
        self.assertTrue(touch_debug.enabled({"KOBRA_TOUCH_DEBUG": "1"}))


class RawReaderTests(unittest.TestCase):
    @staticmethod
    def packet(ev_type, code, value):
        import struct
        return struct.pack("llHHi", 0, 0, ev_type, code, value)

    def test_e07_button_down_before_the_new_coordinates_does_not_reuse_the_old_ones(self):
        chunks = [
            # tap 1 at (400, 400)
            self.packet(3, 0, 400) + self.packet(3, 1, 400) + self.packet(1, 330, 1) + self.packet(0, 0, 0)
            + self.packet(1, 330, 0) + self.packet(0, 0, 0),
        ]
        reader = touch_cal.RawEvdevReader(open_fn=lambda p: 3, read_fn=lambda fd, n: chunks.pop(0) if chunks else b"",
                                          close_fn=lambda fd: None)
        self.assertEqual(reader.poll(), (400, 400))
        # tap 2: BTN_TOUCH comes first, then the new ABS values, one SYN_REPORT
        chunks.append(self.packet(1, 330, 1) + self.packet(3, 0, 100) + self.packet(3, 1, 100) + self.packet(0, 0, 0)
                      + self.packet(1, 330, 0) + self.packet(0, 0, 0))
        self.assertEqual(reader.poll(), (100, 100))


class WifiFixTests(unittest.TestCase):
    def setUp(self):
        import test_wifi
        self.tw = test_wifi
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conf = os.path.join(self.tmp.name, "wpa_supplicant.conf")
        with open(self.conf, "w", encoding="utf-8") as f:
            f.write('ctrl_interface=/var/run/wpa_supplicant\n\nnetwork={\n\tssid="OldNet"\n\tpsk="oldsecret1"\n}\n')
        proc = os.path.join(self.tmp.name, "proc")
        for pid, (comm, parts) in {
            "201": ("wpa_supplicant", ["wpa_supplicant", "-i", "wlan0", "-c", self.conf]),
            "202": ("udhcpc", ["udhcpc", "-i", "wlan0", "-x", "hostname:kobra3", "-b"]),
        }.items():
            os.makedirs(os.path.join(proc, pid))
            with open(os.path.join(proc, pid, "comm"), "w") as f:
                f.write(comm + "\n")
            with open(os.path.join(proc, pid, "cmdline"), "wb") as f:
                f.write(b"\x00".join(p.encode() for p in parts) + b"\x00")
        self.proc = proc
        self.sys_net = os.path.join(self.tmp.name, "sys")
        os.makedirs(os.path.join(self.sys_net, "wlan0"))
        with open(os.path.join(self.sys_net, "wlan0", "flags"), "w") as f:
            f.write("0x1003\n")
        self.backend = self.tw.FakeWpaBackend(self.conf)
        self.now = [100.0]

    def manager(self, **kw):
        return network.WifiManager(conf_path=self.conf, proc_root=self.proc, sys_net_root=self.sys_net,
                                   runner=self.backend.runner, signal_fn=self.backend.signal_fn,
                                   spawn_fn=self.backend.spawn_fn, clock_fn=lambda: self.now[0], scan_wait=0.0, **kw)

    def test_e03_switching_to_a_network_in_another_subnet_renews_the_lease_when_associated(self):
        mgr = self.manager()
        mgr.connect("NewNet", password="newsecret123")
        self.backend.signals_sent.clear()
        self.backend.ssid = "NewNet"
        self.backend.state = "COMPLETED"
        self.backend.ip_address = "192.168.1.55"  # still the old network's address
        self.now[0] += 20.0
        self.assertTrue(mgr.ensure_connected())
        self.assertEqual(self.backend.signals_sent, [(202, signal.SIGUSR1)])
        self.now[0] += 20.0
        mgr.ensure_connected()
        self.assertEqual(len(self.backend.signals_sent), 1)  # only once per switch

    def test_e03_failed_reconfigure_stops_the_old_supplicant_first(self):
        mgr = self.manager()
        orig = self.backend.runner

        def runner(cmd, input_text=None, timeout=5.0):
            if cmd[0].endswith("wpa_cli") and "reconfigure" in cmd:
                return 1, "FAIL\n", ""
            return orig(cmd, input_text, timeout)

        mgr._runner = runner
        mgr.connect("NewNet", password="newsecret123")
        self.assertIn((201, signal.SIGTERM), self.backend.signals_sent)
        self.assertTrue(any(os.path.basename(c[0]) == "wpa_supplicant" for c in self.backend.spawned))

    def test_e03_failed_select_gives_the_other_networks_back(self):
        mgr = self.manager()
        mgr.connect("NewNet", password="newsecret123")
        orig = self.backend.runner
        calls = []

        def runner(cmd, input_text=None, timeout=5.0):
            calls.append(cmd[3:] if len(cmd) > 3 else cmd)
            if cmd[0].endswith("wpa_cli") and "select_network" in cmd:
                return 1, "FAIL\n", ""
            return orig(cmd, input_text, timeout)

        mgr._runner = runner
        with self.assertRaises(network.NetworkError):
            mgr.connect("OldNet")
        self.assertIn(["enable_network", "all"], calls)

    def test_e04_braces_in_ssid_and_psk_do_not_break_the_file(self):
        text = ('ctrl_interface=/var/run/wpa_supplicant\n\nnetwork={\n\tssid="Home}5G"\n\tpsk="pass}word{123"\n}\n\n'
                'network={\n\tssid="Other"\n\t# a } in a comment\n\tpsk="secret123"\n}\n')
        header, nets = network.parse_wpa_conf(text)
        self.assertEqual([n[0] for n in nets], ["Home}5G", "Other"])
        self.assertEqual(header[0], "ctrl_interface=/var/run/wpa_supplicant")
        with open(self.conf, "w", encoding="utf-8") as f:
            f.write(text)
        network.upsert_wpa_network(self.conf, "Third", password="thirdsecret1")
        with open(self.conf, encoding="utf-8") as f:
            out = f.read()
        self.assertEqual([n[0] for n in network.parse_wpa_conf(out)[1]], ["Home}5G", "Other", "Third"])
        self.assertEqual(out.count("network={"), 3)
        network.remove_wpa_network(self.conf, "Home}5G")
        with open(self.conf, encoding="utf-8") as f:
            self.assertEqual([n[0] for n in network.parse_wpa_conf(f.read())[1]], ["Other", "Third"])

    def test_e09_new_udhcpc_carries_the_hostname(self):
        os.remove(os.path.join(self.proc, "202", "cmdline"))
        import shutil
        shutil.rmtree(os.path.join(self.proc, "202"))
        mgr = self.manager()
        with mock.patch.object(network.socket, "gethostname", return_value="kobra-k3-0000"):
            mgr._ensure_udhcpc_for_wlan0()
        cmd = self.backend.spawned[-1]
        self.assertEqual(cmd[-2:], ["-x", "hostname:kobra-k3-0000"])
        self.assertIn("wlan0", cmd)
        with mock.patch.object(network.socket, "gethostname", return_value="localhost"):
            mgr._ensure_udhcpc_for_wlan0()
        self.assertNotIn("-x", self.backend.spawned[-1])


class AppShTests(unittest.TestCase):
    PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.sh")

    def read(self):
        with open(self.PATH, encoding="utf-8") as f:
            return f.read()

    def test_e06_supervisor_resolves_touch_settings_before_every_launch(self):
        text = self.read()
        loop = text[text.index('while :; do'):text.index('"$PYTHON_BIN" -m kobra_screen --host')]
        self.assertIn("resolve_touch_properties", loop)
        self.assertIn("export_runtime_env", loop)
        self.assertIn('KOBRA_TOUCH_CALIBRATION="$USER_TOUCH_CAL"', loop)
        with open(os.path.join(os.path.dirname(self.PATH), "app.json"), encoding="utf-8") as f:
            data = f.read()
        self.assertNotIn('"default": "25,235,460,25"', data)

    def test_e09_udhcpc_started_by_app_sh_has_a_hostname(self):
        self.assertIn('-x "hostname:$UDHCPC_HOST"', self.read())

    def test_e11_touch_diag_gives_the_screen_back(self):
        text = self.read()
        body = text[text.index("run_touch_diagnostic_cmd() {"):text.index("case \"$1\" in")]
        self.assertIn("restart_stock_ui", body)
        self.assertIn("WAS_RUNNING", body)

    def test_syntax(self):
        import subprocess
        self.assertEqual(subprocess.run(["sh", "-n", self.PATH]).returncode, 0)


# ---------------------------------------------------------------------------------------------------------------
# Goto debug hook
# ---------------------------------------------------------------------------------------------------------------

class GotoHookTests(AppCase):
    def goto(self, spec):
        path = os.path.join(self.tmp.name, "goto")
        with open(path, "w", encoding="utf-8") as f:
            f.write(spec + "\n")
        self.clock[0] += 1.0
        done = self.app.check_goto_file(path)
        self.assertFalse(os.path.exists(path), "the file is removed")
        return done

    def test_goes_to_tabs_and_pages_and_deletes_the_file(self):
        self.assertTrue(self.goto("tab:tools"))
        self.assertEqual(self.app.tab, "tools")
        self.assertEqual(self.app.pages, [])
        self.assertTrue(self.goto("page:bed_mesh"))
        self.assertEqual(self.app.pages[-1][0], "bed_mesh")
        self.assertTrue(self.goto("page:system"))
        self.assertEqual(self.app.pages, [("system", None)])
        self.assertTrue(self.goto("page:slot:2"))
        self.assertEqual(self.app.pages, [("slot", 2)])
        self.assertTrue(self.goto("home"))
        self.assertEqual((self.app.tab, self.app.pages), ("home", []))

    def test_only_navigates(self):
        before = list(self.printer.gcodes)
        for spec in ("tab:control", "page:limits", "page:wifi", "page:files", "page:wizard", "page:nonsense", "tab:nope",
                     "garbage"):
            self.goto(spec)
        self.assertEqual(self.printer.gcodes, before)
        self.assertIsNone(self.app.wizard)  # wizards (which send G-code) are refused

    def test_wifi_page_is_not_scanned(self):
        scans = []
        self.app.wifi.scan = lambda: scans.append(1) or []
        orig = self.app.refresh_wifi
        self.app.refresh_wifi = lambda scan=False: scans.append(scan) or orig(scan=scan)
        self.goto("page:wifi")
        self.assertEqual([s for s in scans if s is True], [])

    def test_closes_a_dialog_and_ignores_when_no_file(self):
        self.app.confirm("x", lambda: None)
        self.goto("tab:settings")
        self.assertIsNone(self.app.modal)
        self.clock[0] += 1.0
        self.assertFalse(self.app.check_goto_file(os.path.join(self.tmp.name, "absent")))

    def test_checked_from_the_timer_tick_at_most_twice_a_second(self):
        path = os.path.join(self.tmp.name, "goto")
        with mock.patch("kobra_screen.app.GOTO_FILE", path):
            with open(path, "w") as f:
                f.write("tab:tools")
            self.clock[0] += 1.0
            self.app.tick_timers()
            self.assertEqual(self.app.tab, "tools")


class MiscFixTests(AppCase):
    def test_remaining_time_comment_says_seconds(self):
        # B-08: measured on the printer: dryer_status.remain_time is in seconds
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "kobra_screen", "screens.py"),
                  encoding="utf-8") as f:
            src = f.read()
        self.assertIn("measured on the device: seconds", src)
        self.assertNotIn("pending real-device measurement", src)
        self.assertEqual(screens.DRYER_REMAIN_TIME_SCALE_SEC, 1)
        self.assertEqual(screens._dryer_remain_seconds({"remain_time": 14400}), 14400)


if __name__ == "__main__":
    unittest.main()
