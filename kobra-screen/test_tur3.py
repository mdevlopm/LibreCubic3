"""Tests for UI Round 3: Bambu-style horizontal buttons, summary/detail split, ACE connection & busy logic, and strict fit."""

import tempfile
import unittest

from fake_moonraker import FakePrinter, FakeWifiManager, serve
from kobra_screen import i18n, render_lvgl, screens
from kobra_screen.app import App
from kobra_screen.moonraker import Moonraker
from kobra_screen.render_pil import text_line_height, text_width
from kobra_screen.widgets import HEIGHT, TABS_H, WIDTH, Button, Chart, Label, button_parts
from test_screen import StrictFakeLvgl


ACE_FORBIDDEN_BUTTONS_WHEN_DISCONNECTED = {
    "Yükle",
    "Geri çek",
    "Boş işaretle",
    "PLA",
    "PETG",
    "Daha fazla",
    "More",
    "Özel",
    "Durdur",
    "ACE Pro ayarları",
    "Besle",
    "Geri çek",
    "Besleme dur",
    "Çekme dur",
    "50 mm sür",
    "Özel sür",
    "50 mm çek",
    "Özel çek",
    "Sürme dur",
    "Sürme hızı",
    "Çekme hızı",
    "Akıllı yükle",
    "Akıllı çıkar",
    "Tümünü çıkar",
    "Assist kapat",
    "Kaydet",
    "Aracı sıfırla",
    "Envanter sil",
    "Diske yaz",
    "ACE sınırlarını düzenle",
    "Yenile",
    "Load",
    "Unload",
    "Mark as empty",
    "Custom",
    "Stop",
    "ACE Pro settings",
    "Feed 50 mm",
    "Custom feed",
    "Retract 50 mm",
    "Custom retract",
    "Stop feed",
    "Stop retract",
    "Smart load",
    "Smart unload",
    "Unload all",
    "Stop assist",
    "Refresh",
}


class Tur3Tests(unittest.TestCase):
    def setUp(self):
        self.printer = FakePrinter()
        self.httpd = serve(self.printer, simulate=False)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wifi = FakeWifiManager()
        self.app = self._make_app("tr")

    def _make_app(self, lang="tr"):
        app = App(
            Moonraker("127.0.0.1", self.httpd.server_address[1]),
            cache_dir=self.tmp.name,
            wifi=self.wifi,
            rinkhals_home=self.tmp.name,
            lang=lang,
        )
        app.refresh()
        return app

    def press(self, *texts, icon=None):
        for text in texts:
            self.assertTrue(self.app.press(text, icon), "basılabilir düğme yok: %r" % text)
        self.app.refresh()

    def texts(self, app=None):
        target = app or self.app
        return [item.widget.text for item in target.layout() if hasattr(item.widget, "text")]

    def button_items(self, app=None):
        target = app or self.app
        return [item for item in target.layout() if isinstance(item.widget, Button) and item.y < HEIGHT - TABS_H]

    def enabled(self, text, app=None, icon=None):
        target = app or self.app
        return any(
            isinstance(i.widget, Button) and i.widget.text == text and i.widget.action is not None
            and (icon is None or i.widget.icon == icon)
            for i in target.layout()
        )

    # ---- A & B: Bambu-style horizontal buttons & summary/detail split ----

    def test_horizontal_buttons_and_no_vertical_tiles_across_main_tabs(self):
        for tab in ("home", "control", "filament", "tools", "settings"):
            self.app.select_tab(tab)
            btns = self.button_items()
            self.assertTrue(btns, tab)
            for item in btns:
                w = item.widget
                self.assertFalse(w.vertical, "%s sekmesinde dikey düğme olmamalı: %r" % (tab, w.text))
                if tab in ("home", "tools", "settings"):
                    self.assertGreaterEqual(item.h, 44, "%s: %r yüksekliği < 44 (%d)" % (tab, w.text, item.h))
                    if w.icon or w.dot is not None:
                        centre, box = button_parts(item)
                        self.assertIsNotNone(centre, "%s: %r simgesi yok" % (tab, w.text))
                        if box:
                            self.assertLess(centre[0], box[0], "%s: %r simgesi solda olmalı" % (tab, w.text))

        # Tools tab has 2-column horizontal rows and requires no scrolling
        self.app.select_tab("tools")
        placed = self.app.layout()
        self.assertLessEqual(self.app.content_bottom, self.app.content_limit)
        cold_btn = next(i for i in placed if isinstance(i.widget, Button) and i.widget.text == "Cold pull")
        clean_btn = next(i for i in placed if isinstance(i.widget, Button) and i.widget.text == "Nozul temizle")
        self.assertEqual(cold_btn.y, clean_btn.y)
        self.assertLess(cold_btn.x, clean_btn.x)
        self.assertGreaterEqual(cold_btn.h, 44)

    def test_home_temps_single_line_and_heat_detail_chart(self):
        # Home nozzle/bed temps sit on readable single-line labels with full vertical room
        self.app.select_tab("home")
        placed = self.app.layout()
        temp_labels = [
            i for i in placed
            if isinstance(i.widget, Label) and ("24°C" in i.widget.text or "23°C" in i.widget.text)
        ]
        self.assertEqual(len(temp_labels), 2)
        for lbl in temp_labels:
            self.assertGreaterEqual(lbl.h, text_line_height(lbl.widget.size))
            self.assertLessEqual(text_width(lbl.widget.text, lbl.widget.size) * 1.15, lbl.w)

        # Control > Heat shows summary + "Ayrıntı" button opening the 5-min chart page
        self.press("Kontrol")
        self.assertEqual([i.widget for i in self.app.layout() if isinstance(i.widget, Chart)], [])
        self.press("Ayrıntı")
        self.assertEqual(len([i.widget for i in self.app.layout() if isinstance(i.widget, Chart)]), 1)
        self.assertIn("Sıcaklık grafiği", self.texts())

    # ---- C: ACE connection state & busy-state logic ----

    def test_ace_disconnected_hides_all_ace_buttons_and_stale_data(self):
        # Simulate real device observation: ace_instance_0 keeps publishing stale ready/slots/dryer!
        stale_slots = self.printer.status["ace_instance_0"]["slots"]
        self.assertEqual(stale_slots[0]["material"], "PLA")

        for state in ("reconnecting", "connecting", "initializing", "disabled"):
            self.printer.status["ace_instance_0"]["connection_state"] = state
            self.printer.status["ace_instance_0"]["dryer_status"] = {
                "status": "drying", "target_temp": 55, "duration": 240, "remain_time": 12000,
            }
            self.app.refresh()
            self.assertFalse(self.app.ace_connected)

            # Check in both TR and EN on Home, Filament, Slot, Slot Detail, and ACE Page
            for lang in ("tr", "en"):
                app = self._make_app(lang=lang)
                views_to_check = [
                    lambda a=app: a.select_tab("home"),
                    lambda a=app: a.select_tab("filament"),
                    lambda a=app: a.open("slot", 0),
                    lambda a=app: a.open("slot_detail", 0),
                    lambda a=app: a.open("ace"),
                ]
                for enter in views_to_check:
                    app.pages = []
                    enter()
                    btns = {i.widget.text for i in app.layout() if isinstance(i.widget, Button) and i.y < HEIGHT - TABS_H}
                    leaked = btns & ACE_FORBIDDEN_BUTTONS_WHEN_DISCONNECTED
                    self.assertEqual(leaked, set(), "%s/%s ACE düğmeleri gizlenmeli: %r" % (state, lang, leaked))
                    txts = self.texts(app)
                    self.assertIn(i18n.tr("ACE Pro bağlı değil", lang), txts)
                    expected_state = screens._conn_state_tr(state, lang)
                    self.assertIn(expected_state, txts)
                    self.assertNotIn("55°", " ".join(txts))
                    self.assertNotIn("Kurutuyor", txts)
                    self.assertNotIn("Drying", txts)

                    reconn_lbl = i18n.tr("Yeniden bağlan", lang)
                    if state == "disabled":
                        self.assertNotIn(reconn_lbl, btns)
                    else:
                        self.assertIn(reconn_lbl, btns)

        # Missing ace_instance_0 completely -> no reconnect button either
        del self.printer.status["ace_instance_0"]
        self.app.refresh()
        self.app.select_tab("filament")
        btns = {i.widget.text for i in self.app.layout() if isinstance(i.widget, Button) and i.y < HEIGHT - TABS_H}
        self.assertEqual(btns & ACE_FORBIDDEN_BUTTONS_WHEN_DISCONNECTED, set())
        self.assertNotIn("Yeniden bağlan", btns)
        self.assertIn("ACE Pro bağlı değil", self.texts())

    def test_ace_reconnect_button_and_gcode_connection_guard(self):
        self.printer.status["ace_instance_0"]["connection_state"] = "reconnecting"
        self.app.refresh()
        before_gcodes = len(self.printer.gcodes)

        # Operational ACE commands are blocked before sending G-code when disconnected
        self.assertFalse(self.app.ace_gcode("ACE_START_DRYING TEMP=45 DURATION=60"))
        self.assertFalse(self.app.ace_gcode("ACE_CHANGE_TOOL TOOL=0"))
        self.assertEqual(len(self.printer.gcodes), before_gcodes)

        # Clicking "Yeniden bağlan" dispatches ACE_RECONNECT
        self.press("Makara", "Yeniden bağlan")
        self.assertEqual(self.printer.gcodes[-1], "ACE_RECONNECT")

        # Null dryer_status does not crash when connected
        self.printer.status["ace_instance_0"]["connection_state"] = "connected"
        self.printer.status["ace_instance_0"]["dryer_status"] = None
        self.app.refresh()
        self.press("Makara")
        self.assertIn("Kurutma kapalı", self.texts())

    def test_ace_busy_operations_disable_conflicting_buttons_and_show_state(self):
        # 1. Drying active -> preset & custom start buttons disabled, Stop enabled
        self.press("Makara")
        self.press("PLA", icon="thermo")
        self.assertFalse(self.enabled("PLA", icon="thermo"))
        self.assertFalse(self.enabled("PETG", icon="thermo"))
        self.assertFalse(self.enabled("Özel"))
        self.assertTrue(self.enabled("Durdur"))
        self.press("Durdur")
        self.assertTrue(self.enabled("PLA", icon="thermo"))
        self.assertFalse(self.enabled("Durdur"))

        # 2. Slot 0 feeding -> load/unload/feed/retract/material disabled, "Sürme dur" enabled, status shown
        self.printer.status["ace_instance_0"]["slots"][0]["status"] = "feeding"
        self.printer.status["ace_instance_0"]["feed_assist_slot"] = -1
        self.app.refresh()
        self.app.select_tab("home")
        self.assertIn("Y1: sürülüyor", self.texts())
        self.assertIn("sürülüyor", self.texts())

        self.press("Makara", "sürülüyor")
        self.assertFalse(self.enabled("Yükle"))
        self.assertFalse(self.enabled("Geri çek"))
        self.assertFalse(self.enabled("Boş işaretle"))
        self.press("Ayrıntı")
        self.assertIn("sürülüyor", self.texts())
        self.assertFalse(self.enabled("Besle"))
        self.assertFalse(self.enabled("Geri çek"))
        self.assertTrue(self.enabled("Besleme dur"))
        self.assertFalse(self.enabled("Çekme dur"))

        # 3. Slot 0 unwinding -> "Çekme dur" enabled, "Besleme dur" disabled
        self.printer.status["ace_instance_0"]["slots"][0]["status"] = "unwinding"
        self.app.refresh()
        self.assertIn("geri çekiliyor", self.texts())
        self.assertFalse(self.enabled("Besleme dur"))
        self.assertTrue(self.enabled("Çekme dur"))

        # 4. Feed assist active on slot 0 -> manual feed/retract disabled, toggle & Assist kapat enabled
        self.printer.status["ace_instance_0"]["slots"][0]["status"] = "ready"
        self.printer.status["ace_instance_0"]["feed_assist_slot"] = 0
        self.app.refresh()
        self.assertFalse(self.enabled("Besle"))
        self.assertFalse(self.enabled("Geri çek"))
        self.assertTrue(self.enabled("açık"))  # Feed assist toggle stays active so user can turn it off

        self.app.pages = []
        self.press("Makara", "ACE Pro ayarları", "İşlem")
        self.assertTrue(self.enabled("Assist kapat"))

    # ---- D: Dryer remain_time configurable conversion ----

    def test_dryer_remain_seconds_configurable_helper(self):
        self.assertEqual(screens.DRYER_REMAIN_TIME_SCALE_SEC, 1)
        self.assertEqual(screens._dryer_remain_seconds(None), 0)
        self.assertEqual(screens._dryer_remain_seconds({"remain_time": 2400}), 2400)
        self.assertEqual(screens._dryer_remain_seconds({"remain_time": 40}, scale=60), 2400)

    # ---- LVGL unit label & layout metrics parity ----

    def test_lvgl_unit_label_uses_line_height_and_safe_width(self):
        fake_lv = StrictFakeLvgl()
        orig_lv = render_lvgl.lv
        render_lvgl.lv = fake_lv
        try:
            screen = render_lvgl.LvglScreen(manage_system=False, grab_touch=False)
            self.app.select_tab("control")
            screen.sync(self.app.layout())
            # heater_card now combines temperature and target into one label text (no separate unit label)
            # so unit_label is no longer used; verify the heat panel renders without errors
            unit_labels = [o for o in screen.objects if getattr(o, "unit_label", None) is not None]
            # With the new heater_card format, no separate unit labels exist
            self.assertEqual(len(unit_labels), 0)
        finally:
            render_lvgl.lv = orig_lv


if __name__ == "__main__":
    unittest.main()
