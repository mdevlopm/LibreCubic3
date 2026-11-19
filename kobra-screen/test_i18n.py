"""i18n tests: completeness, fallback, persistence, immediate switch, and 272x480 fit in TR & EN."""

import ast
import json
import os
import tempfile
import unittest

from fake_moonraker import FakePrinter, FakeWifiManager, serve
from kobra_screen import i18n, limits, touch_cal, tracker
from kobra_screen import render_pil
from kobra_screen.app import App
from kobra_screen.moonraker import Moonraker
from kobra_screen.render_pil import SCALE, font, render, text_line_height, text_width, wrap
from kobra_screen.widgets import (HEIGHT, WIDTH, Button, Crosshair, Label, Panel, button_parts,
                                  button_sub_text_size, button_text_size, label_unit_size)
from kobra_screen.wizards import WIZARDS


def _collect_static_tr_keys():
    """Collect literal Turkish keys passed to tr(...) / i18n.tr(...) across kobra_screen/*.py."""
    pkg_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kobra_screen")
    keys = set()
    for fname in sorted(os.listdir(pkg_dir)):
        if not fname.endswith(".py") or fname.startswith("lang_"):
            continue
        path = os.path.join(pkg_dir, fname)
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                is_tr = (
                    (isinstance(func, ast.Name) and func.id == "tr")
                    or (isinstance(func, ast.Attribute) and func.attr == "tr")
                )
                if is_tr and node.args:
                    arg0 = node.args[0]
                    if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                        keys.add(arg0.value)

    for _, name in ((t[0], t[0]) for t in App.TABS):
        keys.add(name)
    from kobra_screen import screens
    for title in screens.PAGE_TITLES.values():
        if title:
            keys.add(title)
    for name, _ in screens.SPEED_MODES:
        keys.add(name)
    for name, _, _ in screens.DRY_PRESETS:
        keys.add(name)
    for name, _ in screens.SLEEP_TIMEOUTS:
        keys.add(name)
    for name in screens.SLOT_COLOR_NAMES.values():
        keys.add(name)
    keys.add("Evet, durdur")
    keys.add("Evet, iptal et")
    for wiz in WIZARDS.values():
        keys.add(wiz.name)
        for step in wiz.steps:
            keys.add(step.title)
            keys.add(step.text)
            for lbl, _ in step.actions:
                keys.add(lbl)
            for lbl, _ in step.choices:
                if lbl not in ("PLA", "PETG", "ABS", "PA"):
                    keys.add(lbl)
    for spec in limits.ALL_LIMITS:
        keys.add(spec.label)
        if spec.risk:
            keys.add(spec.risk)
        if spec.unit == " sn":
            keys.add(spec.unit)
    corners, center = touch_cal.calibration_targets()
    for name, _, _ in list(corners) + [center]:
        keys.add(name)
    for item in tracker.DEFAULT_MAINTENANCE_ITEMS:
        keys.add(item["label"])
    return keys


class I18nTests(unittest.TestCase):
    def setUp(self):
        from unittest import mock
        env_patch = mock.patch.dict(os.environ, os.environ.copy(), clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.printer = FakePrinter()
        self.httpd = serve(self.printer, simulate=False)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wifi = FakeWifiManager()

    def _make_app(self, lang=None, rinkhals_home=None, config_path=None):
        app = App(
            Moonraker("127.0.0.1", self.httpd.server_address[1]),
            cache_dir=self.tmp.name,
            wifi=self.wifi,
            rinkhals_home=rinkhals_home or self.tmp.name,
            config_path=config_path,
            lang=lang,
        )
        app.refresh()
        return app

    def test_no_missing_english_keys_and_safe_fallback(self):
        # Verify no duplicate keys exist inside kobra_screen/lang_en.py
        lang_en_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kobra_screen", "lang_en.py")
        with open(lang_en_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=lang_en_path)
        for node in tree.body:
            if isinstance(node, ast.Assign):
                if any(isinstance(t, ast.Name) and t.id == "STRINGS" for t in node.targets):
                    raw_keys = [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
                    dups = sorted({k for k in raw_keys if raw_keys.count(k) > 1})
                    self.assertEqual(dups, [], "Duplicate keys in lang_en.py: %r" % (dups,))

        keys = _collect_static_tr_keys()
        missing = i18n.missing_keys("en", keys)
        self.assertEqual(missing, [], "Missing English translations: %r" % (missing,))

        # Missing key listing helper detects missing entries accurately
        self.assertEqual(
            i18n.missing_keys("en", ["Yazdır", "Olmayan anahtar B", "Olmayan anahtar A"]),
            ["Olmayan anahtar A", "Olmayan anahtar B"],
        )
        self.assertEqual(i18n.missing_keys("tr", ["Olmayan anahtar"]), [])

        # Safe fallback returns Turkish source string for missing keys or unknown languages
        self.assertEqual(i18n.tr("Bilinmeyen metin", "en"), "Bilinmeyen metin")
        self.assertEqual(i18n.tr("Yazdır", "xx"), "Yazdır")
        self.assertEqual(i18n.tr("", "en"), "")

    def test_settings_language_switch_persists_and_applies_immediately(self):
        rinkhals_home = os.path.join(self.tmp.name, "rh")
        cfg_path = os.path.join(rinkhals_home, "apps", "kobra-screen.config")
        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump({"moonraker_host": "127.0.0.1", "touch_calibration": "25,235,460,25"}, f)

        app = self._make_app(rinkhals_home=rinkhals_home)
        self.assertEqual(app.lang, "tr")

        # Navigate Settings -> Dil / Language -> English
        self.assertTrue(app.press("Ayar"))
        self.assertTrue(app.press("Dil / Language"))
        self.assertTrue(app.press("English"))

        # Immediately applied without restart (header E-stop is icon-only, no STOP text)
        self.assertEqual(app.lang, "en")
        placed = app.layout()
        texts = [i.widget.text for i in placed if hasattr(i.widget, "text")]
        self.assertIn("Current language", texts)
        self.assertNotIn("STOP", texts)
        self.assertTrue(any(
            isinstance(i.widget, Button) and i.widget.kind == "estop" and i.widget.icon == "stop"
            for i in placed
        ))
        app.back()
        texts_settings = [i.widget.text for i in app.layout() if hasattr(i.widget, "text")]
        self.assertIn("Touch calibration", texts_settings)
        self.assertIn("Home", texts_settings)
        self.assertIn("Settings", texts_settings)

        # Persisted into shared .config JSON while preserving existing keys
        with open(cfg_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["language"], "en")
        self.assertEqual(saved["moonraker_host"], "127.0.0.1")
        self.assertEqual(saved["touch_calibration"], "25,235,460,25")

        # Touch calibration save preserves language setting
        touch_cal.save_touch_config((20, 240, 465, 20), True, env={}, config_path=cfg_path)
        with open(cfg_path, "r", encoding="utf-8") as f:
            after_cal = json.load(f)
        self.assertEqual(after_cal["language"], "en")
        self.assertEqual(after_cal["touch_swap_axes"], "true")

        # New App instance loads persisted English language
        app2 = self._make_app(rinkhals_home=rinkhals_home)
        self.assertEqual(app2.lang, "en")
        self.assertTrue(app2.press("Settings"))
        self.assertTrue(app2.press("Dil / Language"))
        self.assertTrue(app2.press("Türkçe"))
        self.assertEqual(app2.lang, "tr")
        with open(cfg_path, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f)["language"], "tr")

    def test_error_messages_and_limits_validation_in_english(self):
        # Limits validation in English
        errs = limits.validate_limits({("printer", "max_velocity"): 10}, self.printer.settings, lang="en")
        self.assertTrue(errs)
        self.assertIn("Max velocity", errs[0])

        # Network & Moonraker error translations
        self.assertEqual(i18n.tr_error("Wi-Fi yöneticisi etkin değil", "en"), "Wi-Fi manager is disabled")
        self.assertEqual(
            i18n.tr_error("Moonraker geçersiz yanıt verdi (HTTP 502)", "en"),
            "Invalid Moonraker response (HTTP 502)",
        )
        self.assertEqual(
            i18n.tr_error("Moonraker'a ulaşılamıyor: timed out", "en"),
            "Cannot reach Moonraker: timed out",
        )

        # Calibration error in English
        app = self._make_app(lang="en")
        app.open_touch_calibration()
        app.start_touch_calibration()
        for rx, ry in ((200, 200), (201, 200), (200, 201), (199, 199)):
            app.record_calibration_tap(rx, ry)
        self.assertIn("too close", app.touch_cal.error_msg)

        # Offline Moonraker error renders in English and updates on live language switch
        self.httpd.shutdown()
        self.httpd.server_close()
        app_tr = self._make_app(lang="tr")
        # Trigger connection error
        app_tr.refresh()
        self.assertFalse(app_tr.state.connected)
        texts_tr = [i.widget.text for i in app_tr.layout() if hasattr(i.widget, "text")]
        self.assertIn("Bağlantı yok", texts_tr)
        app_tr.set_language("en", persist=False)
        texts_en = [i.widget.text for i in app_tr.layout() if hasattr(i.widget, "text")]
        self.assertIn("Offline", texts_en)
        self.assertTrue(any("Cannot reach Moonraker" in t for t in texts_en))

    def test_extra_states_and_edge_views_fit_in_both_languages(self):
        for lang in i18n.language_codes():
            self.printer = FakePrinter()
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = serve(self.printer, simulate=False)
            app = self._make_app(lang=lang, rinkhals_home=os.path.join(self.tmp.name, "walk_" + lang))

            # Home complete/cancelled/error banners (also with maintenance alert active)
            app.set_maintenance_threshold("nozzle", 1.0)
            for st in ("complete", "cancelled", "error"):
                self.printer.status["print_stats"]["state"] = st
                app.refresh()
                self._assert_layout_fits(app, "%s:home_%s_alert" % (lang, st))
            app.set_maintenance_threshold("nozzle", 100.0)
            for st in ("complete", "cancelled", "error"):
                self.printer.status["print_stats"]["state"] = st
                app.refresh()
                self._assert_layout_fits(app, "%s:home_%s" % (lang, st))
            self.printer.status["print_stats"]["state"] = "standby"

            # Klipper error state (shows reset limits & restart button)
            self.printer.klippy = "error"
            app.refresh()
            self._assert_layout_fits(app, "%s:klippy_error" % lang)
            self.printer.klippy = "ready"
            app.refresh()

            # Limits: per-instance read-only ("from config file") and bypassed verify
            self.printer.base_settings["ace"]["max_dryer_temperature"] = "60,1:55"
            self.printer.reload_config()
            app.state.settings = {}
            app.refresh()
            app.open("limits")
            app.limit_page = 4
            self._assert_layout_fits(app, "%s:limits_per_instance" % lang)
            app.back()

            # Missing ACE view
            del self.printer.status["ace_instance_0"]
            app.refresh()
            app.select_tab("filament")
            self._assert_layout_fits(app, "%s:filament_no_ace" % lang)

            # Offline disconnected view
            self.httpd.shutdown()
            self.httpd.server_close()
            app.refresh()
            self._assert_layout_fits(app, "%s:offline_disconnected" % lang)

    def test_language_env_fallback_and_config_precedence(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            rh = os.path.join(tmp, "rh")
            # No .config yet -> KOBRA_LANGUAGE env is used
            with mock.patch.dict(os.environ, {"KOBRA_LANGUAGE": "en"}, clear=False):
                self.assertEqual(i18n.load_language(rinkhals_home=rh), "en")
            # Once saved to .config, .config wins even if KOBRA_LANGUAGE is stale
            i18n.save_language("tr", env={}, rinkhals_home=rh)
            with mock.patch.dict(os.environ, {"KOBRA_LANGUAGE": "en"}, clear=False):
                self.assertEqual(i18n.load_language(rinkhals_home=rh), "tr")

    def test_persistent_language_not_overridden_by_tmp_config(self):
        """REV-21: /tmp/rinkhals/apps/<app>.config language=en must not override persistent language=tr."""
        from unittest import mock

        name = "kobra-i18n-test-%d" % os.getpid()
        tmp_dir = "/tmp/rinkhals/apps"
        tmp_cfg = os.path.join(tmp_dir, name + ".config")
        os.makedirs(tmp_dir, exist_ok=True)
        self.addCleanup(lambda: os.path.exists(tmp_cfg) and os.remove(tmp_cfg))
        with open(tmp_cfg, "w", encoding="utf-8") as f:
            json.dump({"language": "en"}, f)

        with tempfile.TemporaryDirectory() as rh:
            # 1. Persistent config has no language yet -> /tmp fallback provides "en"
            perm_cfg = os.path.join(rh, "apps", name + ".config")
            os.makedirs(os.path.dirname(perm_cfg), exist_ok=True)
            with open(perm_cfg, "w", encoding="utf-8") as f:
                json.dump({"heating_lock": True}, f)
            with mock.patch.dict(os.environ, {"RINKHALS_HOME": rh}, clear=False):
                self.assertEqual(i18n.load_language(app_name=name), "en")

            # 2. Once persistent config has language="tr", /tmp language="en" must NOT override it
            with open(perm_cfg, "w", encoding="utf-8") as f:
                json.dump({"language": "tr"}, f)
            with mock.patch.dict(os.environ, {"RINKHALS_HOME": rh}, clear=False):
                self.assertEqual(i18n.load_language(app_name=name), "tr")

    def _assert_layout_fits(self, app, name):
        from PIL import Image, ImageDraw

        draw = ImageDraw.Draw(Image.new("L", (1, 1)))
        placed = app.layout()
        self.assertLessEqual(app.content_bottom, app.content_limit, "%s overflows vertically (%d > %d)" % (
            name, app.content_bottom, app.content_limit))
        fg = []
        for item in placed:
            self.assertGreaterEqual(item.x, 0, name)
            self.assertLessEqual(item.x + item.w, WIDTH, name)
            self.assertLessEqual(item.y + item.h, HEIGHT, name)
            widget = item.widget
            if isinstance(widget, Button):
                self.assertGreaterEqual(item.h, 28, "%s: %r too short" % (name, widget.text))
                _, box = button_parts(item)
                if box and widget.text:
                    if widget.sub:
                        self.assertGreaterEqual(box[3], 36, "%s: %r sub box too short" % (name, widget.text))
                        self.assertLessEqual(
                            text_width(widget.sub, "small") * 1.15, box[2],
                            "%s: sub %r overflows button (%.1f > %d)" % (
                                name, widget.sub, text_width(widget.sub, "small") * 1.15, box[2]),
                        )
                        sz = button_sub_text_size(item, box[2])
                        if not str(widget.text).endswith(".gcode"):
                            self.assertLessEqual(
                                text_width(widget.text, sz) * 1.15, box[2],
                                "%s: %r overflows button (%.1f > %d)" % (
                                    name, widget.text, text_width(widget.text, sz) * 1.15, box[2]),
                            )
                    else:
                        sz = button_text_size(item)
                        lh = text_line_height(sz)
                        self.assertLessEqual(lh, box[3] + 0.5, "%s: %r line height overflow" % (name, widget.text))
                        if "\n" not in widget.text and box[3] < 2 * lh:
                            self.assertLessEqual(
                                text_width(widget.text, sz) * 1.15, box[2],
                                "%s: %r overflows button (%.1f > %d)" % (
                                    name, widget.text, text_width(widget.text, sz) * 1.15, box[2]),
                            )
                        else:
                            lines = wrap(draw, widget.text, font(sz), (box[2] / 1.15) * SCALE)
                            self.assertLessEqual(
                                len(lines) * lh, box[3] + 1.0,
                                "%s: %r wrapped lines overflow button" % (name, widget.text),
                            )
            elif isinstance(widget, Label) and widget.text:
                lh = text_line_height(widget.size)
                self.assertLessEqual(lh, item.h + 0.5, "%s: %r line height overflow (%.1f > %d)" % (
                    name, widget.text, lh, item.h))
                if widget.unit:
                    total_w = text_width(widget.text, widget.size) + 2 + text_width(widget.unit, label_unit_size(widget.size))
                    self.assertLessEqual(
                        total_w * 1.15, item.w,
                        "%s: %r%s overflows label (%.1f > %d)" % (name, widget.text, widget.unit, total_w * 1.15, item.w),
                    )
                elif "\n" not in widget.text and item.h < 2 * lh:
                    if not str(widget.text).endswith(".gcode") and "Heater extruder" not in widget.text:
                        self.assertLessEqual(
                            text_width(widget.text, widget.size) * 1.15, item.w,
                            "%s: %r overflows label (%.1f > %d)" % (
                                name, widget.text, text_width(widget.text, widget.size) * 1.15, item.w),
                        )
                elif "Heater extruder" not in widget.text and "Probe triggered" not in widget.text:
                    lines = wrap(draw, widget.text, font(widget.size), (item.w / 1.15) * SCALE)
                    self.assertLessEqual(
                        len(lines) * lh, item.h + 1.5,
                        "%s: %r wrapped lines overflow label" % (name, widget.text),
                    )
            if not isinstance(widget, (Panel, Crosshair)):
                is_toast = (
                    bool(app.message)
                    and isinstance(widget, Button)
                    and item.x == 12
                    and item.w == WIDTH - 24
                    and item.h == 44
                    and item.y in (HEIGHT - 64 - 52, HEIGHT - 52)
                )
                if not is_toast:
                    fg.append(item)
        for i in range(len(fg)):
            a = fg[i]
            for j in range(i + 1, len(fg)):
                b = fg[j]
                ox = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
                oy = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
                self.assertFalse(ox > 1 and oy > 1, "%s: sibling overlap" % name)
        self.assertEqual(render(placed).size, (WIDTH, HEIGHT))

    def _walk_views(self, app, lang):
        def t(text):
            return i18n.tr(text, lang)

        def press(*texts, icon=None):
            for text in texts:
                self.assertTrue(app.press(t(text), icon), "%s: cannot press %r" % (lang, text))
            app.refresh()

        def ticks(count):
            for _ in range(count):
                self.printer.tick()
            app.refresh()

        yield "home"
        app.extrusion_lock = False
        press("Yazdır")
        yield "files"
        press("kutu_08.gcode")
        yield "file"
        press("Yazdır")
        ticks(10)
        yield "printing"
        press("Baskı ayarı")
        yield "tune"
        press("Nesneler")
        yield "objects"
        self.printer.status["exclude_object"] = {"objects": [], "excluded_objects": [], "current_object": None}
        app.refresh()
        yield "objects empty"
        press("", icon="back")
        self.printer.status["print_stats"].update(state="error", message="Heater extruder not heating at expected rate")
        press("", icon="back")
        yield "error"
        self.printer.status["print_stats"]["state"] = "standby"
        press("Kontrol")
        yield "heat"
        press("Ayrıntı")
        yield "heat_chart"
        press("", icon="back")
        press("Hedef")
        yield "keypad"
        press("Vazgeç", "Eksen")
        yield "move"
        press("Ek")
        yield "extra"
        press("Makara")
        press("PLA", icon="thermo")
        yield "filament"
        press("Daha fazla")
        yield "dry_presets"
        press("", icon="back")
        press("PETG")
        yield "slot"
        press("Kalan: ayarsız")
        yield "spool"
        press("1000 g")
        yield "spool configured"
        press("", icon="back")
        press("Ayrıntı")
        yield "slot_detail_plain"
        press("", icon="back")
        press("", icon="back")
        press("PLA", "Ayrıntı")
        yield "slot_detail_rfid"
        press("", icon="back")
        press("", icon="back")
        press("ACE Pro ayarları")
        yield "ace_status"
        press("Özellik")
        yield "ace_features"
        press("İşlem")
        yield "ace_ops"
        press("", icon="back")
        press("Araçlar")
        yield "tools"
        press("Baskı geçmişi")
        yield "history"
        app.open("history_detail", self.printer.history_jobs[0]["job_id"])
        yield "history_detail"
        press("", icon="back")
        press("", icon="back")
        app.log_event("Örnek hata mesajı", severity="error", source="gcode")
        press("Olay geçmişi")
        yield "events"
        press("", icon="back")
        press("Bakım")
        yield "maintenance"
        press("", icon="back")
        press("Tabla haritası")
        yield "bed_mesh empty"
        self.printer.status["bed_mesh"] = {
            "profile_name": "default",
            "mesh_min": [15.0, 15.0],
            "mesh_max": [240.0, 235.0],
            "probed_matrix": [[-0.25 + 0.10 * r + 0.04 * c for c in range(5)] for r in range(5)],
            "mesh_matrix": [[]],
            "profiles": {},
        }
        app.refresh()
        yield "bed_mesh 3d"
        app.bed_mesh_rot, app.bed_mesh_tilt = 40.0, 52.0
        app.dirty = True
        yield "bed_mesh turned"
        press("Yeniden ölç")
        yield "bed_mesh re-measure confirm"
        press("Vazgeç")
        press("", icon="back")
        for key, wizard in WIZARDS.items():
            app.start_wizard(key)
            for index in range(len(wizard.steps)):
                app.wizard.index = index
                app.wizard.params = {"material": "PETG", "hot": 240, "pull": 110, "heater": "extruder", "target": 220}
                app.refresh()
                yield "wizard %s %d" % (key, index)
            app.wizard.failed = "Probe triggered prior to movement"
            yield "wizard %s failed" % key
            app.end_wizard(cancelled=False)
        press("Ayar")
        yield "settings"
        press("Ekran")
        yield "display"
        press("", icon="back")
        press("Ses")
        yield "sound"
        press("", icon="back")
        press("Güncelleme")
        yield "updates"
        press("", icon="back")
        press("Dil / Language")
        yield "language"
        press("", icon="back")
        press("Ekranı kalibre et")
        yield "cal intro"
        press("Kalibrasyonu başlat")
        yield "cal corners"
        for rx, ry in ((401, 210), (401, 49), (83, 210), (83, 49)):
            app.record_calibration_tap(rx, ry)
        yield "cal verify"
        app.record_calibration_verify(136, 240)
        yield "cal confirm"
        press("Vazgeç")
        press("Wi-Fi")
        yield "wifi"
        press("AtolyeWiFi")
        yield "wifi_net"
        press("Şifre gir")
        yield "keyboard lower"
        press("Shift")
        yield "keyboard upper"
        press("123")
        yield "keyboard num"
        press("#+=")
        yield "keyboard sym"
        press("Vazgeç")
        press("", icon="back")
        press("Sınırlar")
        yield "limits 0"
        press("", icon="next")
        yield "limits 1"
        press("", icon="next")
        yield "limits 2"
        press("1°")
        press("0", ".", "5")
        yield "limits float keypad"
        press("Tamam")
        yield "limits danger confirm"
        press("Vazgeç")
        press("", icon="next")
        yield "limits 3"
        press("", icon="next")
        yield "limits 4"
        press("", icon="next")
        yield "limits 5"
        press("", icon="back")
        press("Sistem")
        yield "system"
        press("Klipper'ı yeniden başlat")
        yield "confirm"
        press("Vazgeç")
        press("", icon="stop")
        yield "estop confirm"
        press("Evet, durdur")
        yield "shutdown"

    @unittest.skipUnless(render_pil.ui_font_available(), "needs AlibabaSans-Regular.ttf (kobra-screen/fonts or KOBRA_SCREEN_FONT)")
    def test_every_view_and_wizard_step_fits_in_both_languages(self):
        for lang in i18n.language_codes():
            # Reset printer state between language passes
            self.printer = FakePrinter()
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = serve(self.printer, simulate=False)
            app = self._make_app(lang=lang, rinkhals_home=os.path.join(self.tmp.name, "walk_" + lang))
            seen = 0
            for name in self._walk_views(app, lang):
                self._assert_layout_fits(app, "%s:%s" % (lang, name))
                seen += 1
            self.assertGreater(seen, 50)

    def test_extrusion_lock_keys(self):
        from kobra_screen import lang_en
        for key in (
            "extrusion_lock",
            "extrusion_lock_desc",
            "extrusion_locked_msg",
            "confirm_disable_extrusion_lock",
        ):
            self.assertIn(key, lang_en.STRINGS)
            self.assertTrue(bool(lang_en.STRINGS[key]))

        # config yokken extrusion_lock False donuyor
        app = self._make_app()
        self.assertFalse(app.extrusion_lock)
        self.assertFalse(limits.load_config().get("extrusion_lock", False))
        self.assertFalse(limits.ConfigSchema.get("extrusion_lock", False))


TestI18n = I18nTests


if __name__ == "__main__":
    unittest.main()
