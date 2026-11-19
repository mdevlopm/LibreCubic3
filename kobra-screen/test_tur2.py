"""Tests for UI Round 2: event history, temperature chart, maintenance, updates, sound, and filament tracking."""

import json
import os
import tempfile
import unittest

from fake_moonraker import FakePrinter, FakeWifiManager, serve
from kobra_screen import beeper, tracker
from kobra_screen.app import App
from kobra_screen.moonraker import Moonraker
from kobra_screen.render_pil import render
from kobra_screen.widgets import Chart, HEIGHT, WIDTH
from test_screen import StrictFakeLvgl


class _RecordingBeeper:
    def __init__(self):
        self.calls = []

    @staticmethod
    def method_name(settings=None):
        pin = beeper.find_klipper_beeper_pin(settings)
        return ("Klipper (%s)" % pin[1]) if pin else "PWM"

    def play_async(self, pattern_name, settings=None, gcode_fn=None):
        self.calls.append((pattern_name, bool(settings), gcode_fn is not None))
        return None


class Tur2Tests(unittest.TestCase):
    def setUp(self):
        self.printer = FakePrinter()
        self.httpd = serve(self.printer, simulate=False)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wifi = FakeWifiManager()
        self.now_wall = 1763300000.0
        self.now_mono = 100.0
        self.beeper = _RecordingBeeper()
        self.app = self._make_app()

    def _make_app(self, lang="tr", rinkhals_home=None, beeper_backend=None):
        rh = rinkhals_home or self.tmp.name
        app = App(
            Moonraker("127.0.0.1", self.httpd.server_address[1]),
            cache_dir=self.tmp.name,
            wifi=self.wifi,
            rinkhals_home=rh,
            lang=lang,
            clock_fn=lambda: self.now_mono,
            wall_clock_fn=lambda: self.now_wall,
            beeper_backend=beeper_backend or self.beeper,
        )
        app.extrusion_lock = False
        app.refresh()
        return app

    def press(self, *texts, icon=None):
        for text in texts:
            self.assertTrue(self.app.press(text, icon), "basılabilir düğme yok: %r" % text)
        self.app.refresh()

    def texts(self):
        return [item.widget.text for item in self.app.layout() if hasattr(item.widget, "text")]

    # ---- A: Event & error history ----

    def test_event_log_ring_buffer_sources_and_clear(self):
        # Notifications, Klipper state changes, and G-code errors (!!) are recorded
        self.app.notify("İlk uyarı", 3, "warn")
        self.printer.klippy = "shutdown"
        self.app.refresh()
        self.printer.klippy = "ready"
        self.app.refresh()
        self.printer.fail_next_gcode = "Move out of range"
        self.app.gcode("G1 X999")
        self.app.refresh()

        msgs = [e["message"] for e in self.app.event_log]
        self.assertIn("İlk uyarı", msgs)
        self.assertTrue(any("shutdown" in m for m in msgs))
        self.assertIn("Klipper hazır", msgs)
        self.assertIn("Move out of range", msgs)

        # Ring buffer caps at MAX_EVENT_LOG (100) and persists in persistent config dir
        for i in range(110):
            self.now_wall += 1.0
            self.app.log_event("Olay %d" % i, severity="info", source="notify")
        self.assertEqual(len(self.app.event_log), tracker.MAX_EVENT_LOG)
        self.assertEqual(self.app.event_log[-1]["message"], "Olay 109")
        self.assertTrue(os.path.isfile(self.app.events_path))

        # Reloaded by a fresh App instance
        app2 = self._make_app()
        self.assertEqual(len(app2.event_log), tracker.MAX_EVENT_LOG)
        self.assertEqual(app2.event_log[-1]["message"], "Olay 109")

        # UI navigation & Clear
        self.press("Araçlar", "Olay geçmişi")
        self.assertIn("100 kayıt", self.texts())
        self.press("Temizle")
        self.assertEqual(self.app.event_log, [])
        self.assertIn("Henüz olay kaydı yok", self.texts())
        app3 = self._make_app()
        self.assertEqual(app3.event_log, [])

    # ---- B: Live temperature graph ----

    def test_temperature_chart_store_fallback_pil_and_lvgl(self):
        # Uses Moonraker /server/temperature_store when available
        self.printer.status["extruder"]["target"] = 210.0
        for _ in range(15):
            self.printer.tick()
        self.press("Kontrol", "Ayrıntı")
        charts = [item.widget for item in self.app.layout() if isinstance(item.widget, Chart)]
        self.assertEqual(len(charts), 1)
        chart = charts[0]
        self.assertEqual(len(chart.series), 4)
        self.assertGreater(len(chart.series[1][0]), 10)  # nozzle temps from store

        # PIL renders Chart cleanly
        img = render(self.app.layout())
        self.assertEqual(img.size, (WIDTH, HEIGHT))

        # LVGL renders Chart cleanly using StrictFakeLvgl contract
        import kobra_screen.render_lvgl as render_lvgl
        fake_lv = StrictFakeLvgl()
        orig_lv = render_lvgl.lv
        render_lvgl.lv = fake_lv
        try:
            screen = render_lvgl.LvglScreen(manage_system=False, grab_touch=False)
            screen.sync(self.app.layout())
            lv_charts = [o for o in screen.objects if getattr(o, "_LV_CLASS", None) == "chart"]
            self.assertEqual(len(lv_charts), 1)
            self.assertEqual(lv_charts[0]._point_count, 60)
        finally:
            render_lvgl.lv = orig_lv

        # Fallback to in-app collection when temperature_store returns 404
        self.printer.temperature_store_enabled = False
        app_fb = self._make_app()
        self.assertFalse(app_fb._temp_store_supported)
        for _ in range(5):
            self.printer.tick()
            app_fb.refresh()
        noz_t, _ = app_fb.temperature_series("extruder", 60)
        self.assertGreaterEqual(len(noz_t), 5)

    # ---- C: Maintenance reminders ----

    def test_maintenance_thresholds_home_alert_and_done_reset(self):
        # Fake history totals has ~1.93 h (6960s) and 3 jobs
        self.press("Araçlar", "Bakım")
        self.assertIn("Nozul kontrolü", self.texts())
        self.assertIn("ACE kurutucu filtresi", self.texts())

        # Lower nozzle threshold below 1.93h -> triggers due alert on home screen
        self.app.set_maintenance_threshold("nozzle", 1.0)
        self.app.select_tab("home")
        self.assertTrue(any("Bakım: Nozul kontrolü" in t for t in self.texts()))

        # Also test print-count based threshold (ace_filter, current 3 jobs)
        self.app.set_maintenance_threshold("ace_filter", 2.0)
        due_ids = [d["id"] for d in self.app.due_maintenance_items()]
        self.assertIn("nozzle", due_ids)
        self.assertIn("ace_filter", due_ids)

        # Marking done resets elapsed to 0 relative to current totals and persists
        self.app.mark_maintenance_done("nozzle")
        self.app.mark_maintenance_done("ace_filter")
        self.assertEqual(self.app.due_maintenance_items(), [])
        self.assertFalse(any("Bakım:" in t for t in self.texts()))

        app2 = self._make_app()
        self.assertEqual(app2.due_maintenance_items(), [])

    # ---- D: Update check (info-only) ----

    def test_updates_info_only_unconfigured_and_configured(self):
        ver_file = os.path.join(self.tmp.name, ".open-kobra-version")
        with open(ver_file, "w", encoding="utf-8") as f:
            f.write("0.9.4-test\n")

        self.press("Ayar", "Güncelleme")
        txts = self.texts()
        self.assertIn("0.9.4-test", txts)
        self.assertIn("Güncelleme yöneticisi yapılandırılmamış", txts)
        # No update trigger button exists
        self.assertFalse(any("Güncelle" == t or "Şimdi güncelle" == t for t in txts))

        # Configured update_manager response
        self.printer.update_status = {
            "busy": False,
            "version_info": {
                "klipper": {"version": "v0.13.0-412", "remote_version": "v0.13.0-415", "commits_behind": 3},
                "system": {"package_count": 0},
            },
        }
        self.press("Yenile")
        txts2 = self.texts()
        self.assertIn("klipper", txts2)
        self.assertIn("v0.13.0-412 -> v0.13.0-415", txts2)
        self.assertIn("Güncel", txts2)

    # ---- E: Audible beeper (PWM & Klipper pin + toggles) ----

    def test_beeper_pwm_and_klipper_pin_and_triggers(self):
        # PWM sysfs playback test
        pwm_root = os.path.join(self.tmp.name, "pwmchip0")
        pwm0 = os.path.join(pwm_root, "pwm0")
        os.makedirs(pwm0, exist_ok=True)
        for name, init in (("enable", "0"), ("period", "400000"), ("duty_cycle", "0")):
            with open(os.path.join(pwm0, name), "w", encoding="utf-8") as f:
                f.write(init)
        real_beeper = beeper.Beeper(pwm_chip=pwm_root, pwm_index=0, sleep_fn=lambda _: None)
        self.assertTrue(real_beeper.pwm_available())
        self.assertTrue(real_beeper.play_sync("confirm"))
        with open(os.path.join(pwm0, "enable"), "r", encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "0")  # restored off

        # Prefers Klipper pin when configured in printer settings
        gcodes = []
        settings_with_pin = {"output_pin beeper": {"pwm": False}}
        self.assertTrue(real_beeper.play_sync("confirm", settings=settings_with_pin, gcode_fn=gcodes.append))
        self.assertEqual(len(gcodes), 1)
        self.assertIn("SET_PIN PIN=beeper VALUE=1", gcodes[0])
        self.assertIn("SET_PIN PIN=beeper VALUE=0", gcodes[0])

        # App triggers: print complete, Klipper error, confirm modal
        self.beeper.calls.clear()
        self.press("Yazdır", "kutu_08.gcode", "Yazdır")
        for _ in range(55):
            self.printer.tick()
        self.app.refresh()
        self.assertIn("complete", [c[0] for c in self.beeper.calls])

        self.beeper.calls.clear()
        self.printer.klippy = "error"
        self.app.refresh()
        self.assertIn("error", [c[0] for c in self.beeper.calls])
        self.printer.klippy = "ready"
        self.app.refresh()

        # Toggles in Ayar > Ses persist and suppress disabled events
        self.press("Ayar", "Ses")
        self.press("Onayda bip")
        self.assertFalse(self.app.beep_on_confirm)
        self.beeper.calls.clear()
        self.app.confirm("Test?", lambda: None)
        self.press("Evet")
        self.assertNotIn("confirm", [c[0] for c in self.beeper.calls])

        app2 = self._make_app()
        self.assertFalse(app2.beep_on_confirm)

    # ---- F: Remaining filament tracking ----

    def test_filament_conversions_deduction_complete_cancel_and_preprint_warning(self):
        # 1.75mm PLA (1.24 g/cm3) round-trip
        mm_1kg = tracker.grams_to_mm(1000.0, "PLA")
        self.assertAlmostEqual(tracker.mm_to_grams(mm_1kg, "PLA"), 1000.0, places=3)
        self.assertGreater(tracker.mm_to_meters(mm_1kg), 330.0)
        self.assertLess(tracker.mm_to_meters(mm_1kg), 340.0)
        # PETG is denser than ABS -> fewer meters per 1000g
        self.assertLess(tracker.grams_to_mm(1000.0, "PETG"), tracker.grams_to_mm(1000.0, "ABS"))

        # Configure ACE slot 1 (PETG, active T1) with 20 g
        self.press("Makara", "PETG")
        self.press("Kalan: ayarsız")
        self.press("Gram gir", "2", "0", "Tamam")
        sp = self.app.spool_info("1")
        self.assertTrue(sp["configured"])
        self.assertAlmostEqual(sp["remaining_g"], 20.0, delta=0.2)
        before_mm = sp["remaining_mm"]

        # Pre-print warning when file requires 4210 mm (~12.8g) vs 10g remaining
        self.app.set_spool_amount("1", 10.0, unit="g", material="PETG")
        self.app.select_tab("home")
        self.press("Yazdır", "kutu_08.gcode")
        self.assertTrue(any("Filament yetersiz" in t for t in self.texts()))
        self.press("Yazdır")
        # Confirmation modal blocks immediate start until user confirms
        self.assertEqual(self.printer.status["print_stats"]["state"], "standby")
        self.press("Evet")
        self.assertEqual(self.printer.status["print_stats"]["state"], "printing")

        # Cancel mid-print (~50% -> ~2105 mm used) and verify deduction on cancelled print
        for _ in range(25):
            self.printer.tick()
        self.app.refresh()
        before_cancel_mm = self.app.spool_info("1")["remaining_mm"]
        self.press("Durdur", "Evet")
        after_cancel_mm = self.app.spool_info("1")["remaining_mm"]
        self.assertLess(after_cancel_mm, before_cancel_mm)
        self.assertAlmostEqual(before_cancel_mm - after_cancel_mm, 2105.0, delta=5.0)

        # Low-filament warning appears on home screen and spool persists across restart
        self.assertTrue(any("Düşük filament" in t for t in self.texts()))
        app2 = self._make_app()
        self.assertAlmostEqual(app2.spool_info("1")["remaining_mm"], after_cancel_mm, places=1)

        # External spool mode (no ACE) deducts on complete
        del self.printer.status["ace_instance_0"]
        self.app.refresh()
        self.app.set_spool_amount("external", 100.0, unit="m", material="PLA")
        self.app.select_tab("home")
        self.assertTrue(any("Harici: PLA" in t for t in self.texts()))
        self.press("Yazdır", "kutu_08.gcode", "Yazdır")
        for _ in range(55):
            self.printer.tick()
        self.app.refresh()
        ext = self.app.spool_info("external")
        self.assertAlmostEqual(ext["remaining_mm"], 100000.0 - 4210.0, delta=5.0)


if __name__ == "__main__":
    unittest.main()
