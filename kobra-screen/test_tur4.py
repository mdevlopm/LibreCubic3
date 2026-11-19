"""Tests for UI Round 4: 3D/2D bed mesh, realistic button language, fast ACE stop dispatch, and heating lock."""

from concurrent.futures import ThreadPoolExecutor
import os
import tempfile
import threading
import time
import unittest

from PIL import Image

from fake_moonraker import FakePrinter, FakeWifiManager, serve
from kobra_screen import bed_mesh, i18n, render_lvgl, render_pil, screens
from kobra_screen.app import App
from kobra_screen.moonraker import Moonraker
from test_screen import StrictFakeLvgl


SAMPLE_MUTABLE_CFG = (
    b'{\n'
    b'\t"bed_mesh default": {\n'
    b'\t\t"algo": "lagrange",\n'
    b'\t\t"max_x": "240",\n'
    b'\t\t"max_y": "235",\n'
    b'\t\t"min_x": "15",\n'
    b'\t\t"min_y": "15",\n'
    b'\t\t"points": "-1.406000, -1.440500, -1.460000, -1.507000, -1.678000\\n'
    b'-0.671500, -0.702500, -0.707500, -0.711000, -0.899500\\n'
    b'-0.026500, -0.012000, -0.046500, -0.112000, -0.367000\\n'
    b'0.539500, 0.467500, 0.429000, 0.339000, 0.160500\\n'
    b'0.970000, 0.882000, 0.817000, 0.699000, 0.638500",\n'
    b'\t\t"x_count": "5",\n'
    b'\t\t"y_count": "5"\n'
    b'\t}\n'
    b'}\n'
)


class Tur4Tests(unittest.TestCase):
    def setUp(self):
        self.printer = FakePrinter()
        self.httpd = serve(self.printer, simulate=False)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wifi = FakeWifiManager()
        self.app = App(
            Moonraker("127.0.0.1", self.httpd.server_address[1]),
            cache_dir=self.tmp.name,
            wifi=self.wifi,
            rinkhals_home=self.tmp.name,
        )
        self.app.extrusion_lock = False
        self.app.refresh()

    def texts(self):
        out = []
        for item in self.app.layout():
            text = getattr(item.widget, "text", None)
            if text:
                out.append(text)
            sub = getattr(item.widget, "sub", None)
            if sub:
                out.append(sub)
        return out

    def buttons(self, label, icon=None):
        return [
            item.widget for item in self.app.layout()
            if getattr(item.widget, "text", None) == label
            and (icon is None or getattr(item.widget, "icon", None) == icon)
        ]

    def enabled(self, label, icon=None):
        matches = self.buttons(label, icon)
        self.assertTrue(matches, "no button %r in %r" % (label, self.texts()))
        return any(btn.action is not None for btn in matches)

    def press(self, *labels):
        for label in labels:
            self.assertTrue(self.app.press(label), "could not press %r in %r" % (label, self.texts()))

    # ---- A: Bed mesh source priority, rendering, stats & controls ----

    def test_bed_mesh_source_priority_and_empty_state(self):
        # Empty when neither Klipper nor printer_mutable.cfg has a mesh
        self.press("Araçlar", "Tabla haritası")
        texts = self.texts()
        self.assertIn("Kayıtlı tabla haritası yok", texts)
        # The only action is "Yeniden ölç", which needs a confirmation before anything runs
        self.assertIn("Yeniden ölç", texts)
        self.assertFalse(any("LEVIQ" in g for g in self.printer.gcodes))
        self.assertNotIn("Tabla seviyeleme", texts)

        # 3rd fallback: stock printer_mutable.cfg, labelled as such
        self.printer.config_files["printer_mutable.cfg"] = SAMPLE_MUTABLE_CFG
        self.app.refresh_bed_mesh()
        self.app.refresh()
        texts = self.texts()
        self.assertIn("Stok kayıt · Klipper'da yok", texts)
        self.assertIn("Kötü", texts)
        self.assertIn("2.65 mm", texts)
        # no statistics, no 2D map, no mode tabs
        for gone in ("2B harita", "3B yüzey", "Yenile", "En düşük: -1.678", "Ortalama: -0.581"):
            self.assertNotIn(gone, texts)

        # 2nd priority: Klipper saved profile overrides stock printer_mutable.cfg
        self.printer.status["bed_mesh"] = {
            "profile_name": "",
            "mesh_min": [0.0, 0.0],
            "mesh_max": [0.0, 0.0],
            "probed_matrix": [[]],
            "mesh_matrix": [[]],
            "profiles": {
                "warm": {
                    "points": [[0.0, 0.08], [0.12, 0.25]],
                    "mesh_params": {"min_x": 15, "max_x": 240, "min_y": 15, "max_y": 235},
                }
            },
        }
        self.app.refresh()
        texts = self.texts()
        self.assertIn("Orta", texts)
        self.assertNotIn("Stok kayıt · Klipper'da yok", texts)

        # 1st priority: active Klipper probed_matrix overrides profiles
        self.printer.status["bed_mesh"]["profile_name"] = "active_pla"
        self.printer.status["bed_mesh"]["probed_matrix"] = [
            [0.01, 0.04, 0.02],
            [-0.02, 0.00, 0.03],
            [0.05, 0.02, -0.01],
        ]
        self.app.refresh()
        texts = self.texts()
        self.assertIn("İyi", texts)
        self.assertIn("0.07 mm", texts)

    def test_bed_mesh_3d_drag_rotation_and_lvgl(self):
        self.printer.config_files["printer_mutable.cfg"] = SAMPLE_MUTABLE_CFG
        self.press("Araçlar", "Tabla haritası")

        # 3D view renders in both PIL and StrictFakeLvgl without idle rebuild
        fake_lv = StrictFakeLvgl()
        old_lv = render_lvgl.lv
        render_lvgl.lv = fake_lv
        try:
            lv_screen = render_lvgl.LvglScreen(manage_system=False)
            lv_screen.sync(self.app.layout())
            prev_imgs = [o for o in lv_screen.objects if getattr(o, "_mesh_key", None) is not None]
            self.assertEqual(len(prev_imgs), 1)
            image = prev_imgs[0]
            p0 = image._loaded_path
            self.assertTrue(p0 and os.path.exists(p0))
            with Image.open(p0) as im:
                self.assertEqual(im.format, "PNG")
                self.assertGreater(im.size[0], 200)
                self.assertGreater(im.size[1], 200)  # one big surface, not the old 132 px strip

            # A finished drag stores any angle (continuous, not 90 degree steps) and redraws once
            self.app.bed_mesh_drag(37.0, 50.0, final=True)
            self.assertEqual(self.app.bed_mesh_rot, 37.0)
            lv_screen.sync(self.app.layout())
            self.assertNotEqual(image._loaded_path, p0)

            # PIL render matches cleanly
            pil_img = render_pil.render(self.app.layout())
            self.assertEqual(pil_img.size, (272, 480))
        finally:
            render_lvgl.lv = old_lv

    # ---- B: Realistic 3D printer button language & unit step selectors ----

    def test_realistic_controls_and_step_selectors(self):
        # Extruder uses step selector chips + Retract / Extrude verbs
        self.printer.status["extruder"]["temperature"] = 205.0
        self.app.refresh()
        self.press("Kontrol", "Ek")
        self.assertIn("Geri çek", self.texts())
        self.assertIn("Ekstrüde", self.texts())
        self.assertNotIn("Geri 10", self.texts())
        self.assertNotIn("İleri 10", self.texts())
        self.press("5 mm", "Ekstrüde")
        self.assertIn("G1 E5 F300", self.printer.gcodes[-1])
        self.press("Geri çek")
        self.assertIn("G1 E-5 F300", self.printer.gcodes[-1])

        # Move uses unit step chips + realistic labels
        self.press("Eksen")
        self.assertIn("10 mm", self.texts())
        self.assertIn("XY sıfırla", self.texts())
        self.assertIn("Motorları kapat", self.texts())

        # Heat uses target value box + unit +/- buttons
        self.press("Isı")
        self.assertIn("Hedef", self.texts())
        self.assertIn("-5°C", self.texts())
        self.assertIn("+5°C", self.texts())
        self.press("Hedef", "2", "1", "5", "Tamam")
        self.assertIn("Isıtma başlatılsın mı? (nozul 215°C)", self.texts())
        self.press("Isıt")
        self.assertIn("215°C", self.texts())

    # ---- C: Urgent ACE stop latency & fast polling ----

    def test_ace_stop_bypasses_blocked_executor_and_updates_fast(self):
        fake_now = [100.0]
        blocker = threading.Event()
        self.addCleanup(blocker.set)
        executor = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(lambda: executor.shutdown(wait=False))

        # Saturate normal executor worker with a blocked task
        executor.submit(blocker.wait)
        send_timestamps = []
        orig_gcode = self.printer.run_gcode

        def spy_gcode(script):
            if "ACE_STOP_DRYING" in script:
                send_timestamps.append(time.monotonic())
            return orig_gcode(script)

        self.printer.run_gcode = spy_gcode
        self.printer.status["ace_instance_0"]["dryer_status"] = {
            "status": "drying", "target_temp": 55, "remain_time": 1200, "duration": 240,
        }

        async_app = App(
            Moonraker("127.0.0.1", self.httpd.server_address[1]),
            run_async=lambda fn: executor.submit(fn),
            cache_dir=self.tmp.name,
            wifi=self.wifi,
            rinkhals_home=self.tmp.name,
            clock_fn=lambda: fake_now[0],
        )
        async_app.refresh()
        async_app.select_tab("filament")

        t0 = time.monotonic()
        self.assertTrue(async_app.press("Durdur"))

        # Immediate UI state (<50 ms) shows "Durduruluyor…" even before HTTP finishes
        immediate_texts = [getattr(i.widget, "text", None) for i in async_app.layout() if getattr(i.widget, "text", None)]
        self.assertIn("Durduruluyor…", immediate_texts)
        self.assertTrue(async_app.fast_poll_active)
        self.assertAlmostEqual(async_app.poll_interval, 0.2)

        # Wait up to 1.0s for urgent thread dispatch & refresh while normal executor stays blocked!
        deadline = t0 + 1.0
        while not send_timestamps and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(send_timestamps, "ACE_STOP_DRYING was blocked by normal executor!")
        dispatch_delay = send_timestamps[0] - t0
        self.assertLessEqual(dispatch_delay, 0.15)

        # Wait briefly for urgent worker post-refresh to reconcile ACE dryer_status -> "stop"
        while async_app.ace_stopping_kind() is not None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNone(async_app.ace_stopping_kind())
        self.assertLessEqual(time.monotonic() - t0, 1.0)
        settled_texts = [getattr(i.widget, "text", None) for i in async_app.layout() if getattr(i.widget, "text", None)]
        self.assertIn("Kurutma kapalı", settled_texts)

    # ---- D: Heating lock enforcement & persistence ----

    def test_heating_lock_blocks_heating_and_persists(self):
        cfg_file = os.path.join(self.tmp.name, "kobra.config")
        app = App(
            Moonraker("127.0.0.1", self.httpd.server_address[1]),
            cache_dir=self.tmp.name,
            wifi=self.wifi,
            rinkhals_home=self.tmp.name,
            config_path=cfg_file,
        )
        app.refresh()
        self.assertFalse(app.heating_lock)

        # Toggle in Ayar -> Sistem -> Isıtma kilidi
        self.assertTrue(app.press("Ayar"))
        self.assertTrue(app.press("Sistem"))
        self.assertTrue(app.press("Isıtma kilidi"))
        self.assertTrue(app.heating_lock)

        # Reload verifies persistence across restarts
        app2 = App(
            Moonraker("127.0.0.1", self.httpd.server_address[1]),
            cache_dir=self.tmp.name,
            wifi=self.wifi,
            rinkhals_home=self.tmp.name,
            config_path=cfg_file,
        )
        app2.refresh()
        self.assertTrue(app2.heating_lock)

        # Home shows badge and locks leveling/preheat
        home_texts = [getattr(i.widget, "text", None) for i in app2.layout() if getattr(i.widget, "text", None)]
        self.assertIn("Isıtma kilitli", home_texts)
        for lbl in ("Tabla seviyeleme", "Ön ısıtma"):
            btns = [i.widget for i in app2.layout() if getattr(i.widget, "text", None) == lbl]
            self.assertTrue(btns and all(b.action is None and b.icon == "lock" for b in btns))

        # Control > Heat locks heating buttons while keeping Turn off / Turn all off active
        self.assertTrue(app2.press("Kontrol"))
        heat_btns = {getattr(i.widget, "text", None): i.widget for i in app2.layout() if getattr(i.widget, "text", None)}
        for blocked_lbl in ("+5°C", "-5°C", "Hedef", "PLA", "PETG", "ABS"):
            self.assertIsNone(heat_btns[blocked_lbl].action)
        self.assertIsNotNone(heat_btns["Hepsini kapat"].action)
        self.assertIsNotNone(heat_btns["Kapat"].action)

        # Tools locks heating wizards, leaves shaper & bed_mesh open
        self.assertTrue(app2.press("Araçlar"))
        tool_btns = {getattr(i.widget, "text", None): i.widget for i in app2.layout() if getattr(i.widget, "text", None)}
        for w_lbl in ("Cold pull", "Nozul temizle", "Filament yükle", "Filament çıkar", "Tabla seviyele", "Z-offset ölç", "PID ayarı"):
            self.assertIsNone(tool_btns[w_lbl].action, "%r should be locked" % w_lbl)
            self.assertEqual(tool_btns[w_lbl].icon, "lock")
        self.assertIsNotNone(tool_btns["Titreşim ayarı"].action)
        self.assertIsNotNone(tool_btns["Tabla haritası"].action)

        # Files disables Print button
        app2.open("files")
        self.assertTrue(app2.press("kutu_08.gcode"))
        print_btns = [i.widget for i in app2.layout() if getattr(i.widget, "text", None) == "Yazdır"]
        self.assertTrue(print_btns and all(b.action is None and b.icon == "lock" for b in print_btns))

        # Command-layer guards: none of the dangerous heating scripts or print_start get dispatched
        before_gcodes = len(self.printer.gcodes)
        for cmd in (
            "M104 S210",
            "M109 S210",
            "M140 S60",
            "M190 S60",
            "SET_HEATER_TEMPERATURE HEATER=extruder TARGET=200",
            "G28\nLEVIQ",
            "LEVIQ_AUTO_ZOFFSET",
            "PID_CALIBRATE HEATER=extruder TARGET=220",
        ):
            self.assertFalse(app2.gcode(cmd), "should block %r" % cmd)
        self.assertFalse(app2.start_print("kutu_08.gcode"))
        self.assertEqual(len(self.printer.gcodes), before_gcodes)
        self.assertEqual(self.printer.status["print_stats"]["state"], "standby")

        # Shutoff commands & ACE dryer STILL work when heating lock is ON!
        self.assertTrue(app2.gcode("M104 S0\nM140 S0"))
        self.assertTrue(app2.gcode("TURN_OFF_HEATERS"))
        self.assertTrue(app2.ace_gcode("ACE_START_DRYING TEMP=55 DURATION=240"))
        self.assertIn("ACE_START_DRYING TEMP=55 DURATION=240", self.printer.gcodes)

        # English translation smoke check
        self.assertEqual(i18n.tr("Isıtma kilidi", "en"), "Heating lock")
        self.assertEqual(i18n.tr("Tabla haritası", "en"), "Bed mesh")


if __name__ == "__main__":
    unittest.main()
