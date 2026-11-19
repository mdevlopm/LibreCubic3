"""Tests: the UI driven against fake_moonraker over real HTTP. Run: python3 -m unittest -v"""

import json
import os
import tempfile
import unittest
from unittest import mock

from fake_moonraker import FakePrinter, FakeWifiManager, serve
from kobra_screen import render_pil
from kobra_screen.app import App
from kobra_screen.moonraker import Moonraker, MoonrakerError
from kobra_screen.render_pil import SCALE, font, render, text_line_height, text_width, wrap
from kobra_screen.wizards import WIZARDS
from kobra_screen.widgets import (HEIGHT, TABS_H, WIDTH, Button, Crosshair, Label, Panel, Picture,
                                  button_parts, button_sub_text_size, button_text_size, label_unit_size)


class ScreenTests(unittest.TestCase):
    def setUp(self):
        self.printer = FakePrinter()
        self.httpd = serve(self.printer, simulate=False)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wifi = FakeWifiManager()
        self.app = App(Moonraker("127.0.0.1", self.httpd.server_address[1]), cache_dir=self.tmp.name, wifi=self.wifi)
        self.app.extrusion_lock = False
        self.app.refresh()

    def press(self, *texts, icon=None):
        for text in texts:
            self.assertTrue(self.app.press(text, icon), "basılabilir düğme yok: %r" % text)
        self.app.refresh()

    def texts(self):
        return [item.widget.text for item in self.app.layout() if hasattr(item.widget, "text")]

    def enabled(self, text, icon=None):
        return any(isinstance(i.widget, Button) and i.widget.text == text and i.widget.action
                   and (icon is None or i.widget.icon == icon) for i in self.app.layout())

    def ticks(self, count):
        for _ in range(count):
            self.printer.tick()
        self.app.refresh()

    def start_print(self):
        self.press("Yazdır", "kutu_08.gcode", "Yazdır")

    # ---- printing ----

    def test_home_idle(self):
        self.assertIn("Hazır", self.texts())
        self.assertIn("Z-offset -0.165 mm", self.texts())
        self.assertFalse(self.enabled("Duraklat"))

    def test_print_flow(self):
        self.press("Yazdır")
        self.assertIn("kutu_08.gcode", self.texts())  # newest first
        self.press("kutu_08.gcode")
        self.assertEqual(self.printer.status["print_stats"]["state"], "standby")  # details first
        self.assertIn("1sa 30dk", self.texts())
        self.assertIn("4.2 m", self.texts())
        self.press("Yazdır")
        self.assertEqual(self.printer.status["print_stats"]["filename"], "kutu_08.gcode")
        self.assertIn("Yazdırılıyor", self.texts())
        self.press("Duraklat")
        self.assertEqual(self.printer.status["print_stats"]["state"], "paused")
        self.press("Devam", "Durdur")
        self.assertEqual(self.printer.status["print_stats"]["state"], "printing")  # waits for confirmation
        self.press("Vazgeç")
        self.assertEqual(self.printer.status["print_stats"]["state"], "printing")
        self.press("Durdur", "Evet")
        self.assertEqual(self.printer.status["print_stats"]["state"], "cancelled")
        self.assertIn("Baskı iptal edildi", self.texts())

    def test_print_card_shows_thumbnail_layers_and_time(self):
        self.start_print()
        self.ticks(25)  # 50 %, 25 minutes in
        pictures = [i.widget for i in self.app.layout() if isinstance(i.widget, Picture)]
        self.assertEqual(len(pictures), 1)
        self.assertTrue(os.path.getsize(pictures[0].path) > 100)
        self.assertIn("61 / 120", self.texts())
        self.assertIn("Kalan 25dk", self.texts())
        self.assertIn("50%", self.texts())

    def test_files_paging_and_blocked_while_printing(self):
        self.press("Yazdır")
        self.assertNotIn("kutu_02.gcode", self.texts())
        self.press("", icon="next")
        self.assertIn("kutu_02.gcode", self.texts())
        self.assertFalse(self.enabled("", "next"))
        self.press("kutu_02.gcode")
        self.printer.status["print_stats"]["state"] = "printing"
        self.app.refresh()
        self.assertFalse(self.enabled("Yazdır"))

    def test_tune(self):
        self.start_print()
        self.press("Baskı ayarı", "Sport")
        self.assertEqual(self.printer.status["gcode_move"]["speed_factor"], 1.25)
        self.press("+5%", "+5%", "-5%")
        self.assertEqual(self.printer.status["gcode_move"]["extrude_factor"], 1.05)
        self.press("+0.01", "+0.01", "-0.05")
        self.assertAlmostEqual(self.printer.status["gcode_move"]["homing_origin"][2], -0.03)
        self.assertIn("-0.030 mm", self.texts())
        self.press("", icon="back")
        self.assertIn("Baskı ayarı", self.texts())

    # ---- control ----

    def test_keypad_respects_configured_limit(self):
        self.press("Kontrol", "Hedef", "3", "5", "0")
        self.assertFalse(self.enabled("Tamam"))  # 350 > max_temp 320
        self.press("<", "<", "2", "0")
        self.press("Tamam", "Isıt")
        self.assertEqual(self.printer.status["extruder"]["target"], 320.0)
        # the limit comes from printer.cfg: raise it there and the screen follows
        self.printer.settings["extruder"]["max_temp"] = 400.0
        self.app.state.settings = {}
        self.app.refresh()
        self.press("320°C", "3", "5", "0", "Tamam")
        self.assertEqual(self.printer.status["extruder"]["target"], 350.0)

    def test_heat_steps_and_presets(self):
        self.press("Kontrol", "PETG", "Isıt")
        self.assertEqual((self.printer.status["extruder"]["target"], self.printer.status["heater_bed"]["target"]),
                         (240.0, 80.0))
        self.press("+5°C")
        self.assertEqual(self.printer.status["extruder"]["target"], 245.0)
        self.press("Kapat")
        self.assertEqual(self.printer.status["extruder"]["target"], 0.0)
        self.press("-5°C")
        self.assertEqual(self.printer.status["extruder"]["target"], 0.0)
        self.press("Hepsini kapat")
        self.assertEqual(self.printer.status["heater_bed"]["target"], 0.0)

    def test_move_needs_homing_and_idle(self):
        self.press("Kontrol", "Eksen")
        self.assertFalse(self.enabled("X+", "right"))
        self.press("", icon="home")
        self.assertTrue(self.enabled("X+", "right"))
        self.press("1 mm")
        self.press("X+", icon="right")
        self.assertIn("G1 X1 F6000", self.printer.gcodes[-1])
        self.assertIn("G91", self.printer.gcodes[-1])
        self.press("Z-", icon="down")
        self.assertIn("G1 Z-1 F600", self.printer.gcodes[-1])
        self.printer.status["print_stats"]["state"] = "printing"
        self.app.refresh()
        self.assertFalse(self.enabled("X+", "right"))
        self.assertFalse(self.enabled("", "home"))

    def test_extra_fan_lights_extruder(self):
        self.press("Kontrol", "Ek", "50%")
        self.assertAlmostEqual(self.printer.status["fan"]["speed"], 0.5, places=2)
        self.press("Nozul")
        self.assertEqual(self.printer.status["output_pin nozzle_light"]["value"], 1.0)
        self.assertFalse(self.enabled("Ekstrüde"))  # cold nozzle
        self.assertFalse(self.enabled("Geri çek"))
        self.assertIn("nozul soğuk", self.texts())
        self.printer.status["extruder"]["temperature"] = 200.0
        self.app.refresh()
        self.press("Ekstrüde")
        self.assertIn("G1 E10 F300", self.printer.gcodes[-1])

    # ---- filament ----

    def test_slots(self):
        self.press("Makara")
        self.assertIn("aktif: T1", self.texts())
        self.press("PLA")  # first slot
        self.assertIn("Yuva 1", self.texts())
        self.press("Yükle")
        self.assertEqual(self.printer.gcodes[-1], "ACE_CHANGE_TOOL TOOL=0")
        self.press("ABS")
        self.assertEqual(self.printer.gcodes[-1], "ACE_SET_SLOT T=0 COLOR=245,245,245 MATERIAL=ABS TEMP=250")
        self.assertIn("ABS, 250°", self.texts())
        self.press("Boş işaretle")
        self.assertIn("boş", self.texts())
        self.assertFalse(self.enabled("Yükle"))

    def test_dryer_presets_custom_and_limit(self):
        self.press("Makara")
        self.press("PETG", icon="thermo")
        self.assertEqual(self.printer.gcodes[-1], "ACE_START_DRYING TEMP=55 DURATION=360")
        self.assertIn("Kurutuyor", self.texts())
        self.press("Durdur")
        self.assertFalse(self.enabled("Durdur"))
        self.press("Özel", "7", "0")
        self.assertFalse(self.enabled("Tamam"))  # driver limit is 60
        self.press("<", "<", "5", "0", "Tamam")
        self.press("9", "0", "Tamam")
        self.assertEqual(self.printer.gcodes[-1], "ACE_START_DRYING TEMP=50 DURATION=90")
        # limit lowered in the config: the 55 degree preset is no longer offered
        self.printer.settings["ace"]["max_dryer_temperature"] = "50"
        self.app.state.settings = {}
        self.app.refresh()
        self.assertFalse(self.enabled("PETG", icon="thermo"))

    def test_missing_ace_and_leviq(self):
        del self.printer.status["ace_instance_0"], self.printer.status["leviq3"]
        self.app.refresh()
        self.press("Makara")
        self.assertIn("ACE Pro bağlı değil", self.texts())
        self.press("Ayar")
        self.assertFalse(self.enabled("Otomatik Z-offset: kapalı"))

    # ---- wizards ----

    def test_cold_pull(self):
        self.press("Araçlar", "Cold pull")
        self.assertIn("Adım 1 / 7", self.texts())
        self.press("PETG")
        self.assertIn("Elinde 30 cm kadar PETG filament olsun.", " ".join(self.texts()).replace("\n", " "))
        self.press("İleri")
        self.assertEqual(self.printer.gcodes[-1], "M104 S240")
        self.assertFalse(self.enabled("İleri"))  # still cold
        self.ticks(40)
        self.press("İleri", "10 mm sür")
        self.assertIn("G1 E10 F150", self.printer.gcodes[-1])
        self.press("İleri")
        self.assertEqual(self.printer.gcodes[-1], "M106 S255\nM104 S110")
        self.assertFalse(self.enabled("İleri"))  # still hot
        self.ticks(50)
        self.press("İleri")
        self.assertIn("SET_STEPPER_ENABLE STEPPER=extruder ENABLE=0", self.printer.gcodes[-1])
        self.press("İleri")
        self.assertEqual(self.printer.status["extruder"]["target"], 0.0)
        self.press("Tekrarla")  # dirty tip: heat again
        self.assertEqual(self.printer.gcodes[-1], "M104 S240")
        self.assertIn("Adım 3 / 7", self.texts())

    def test_wizard_cancel_turns_heat_off_and_blocks_tabs(self):
        self.press("Araçlar", "Filament yükle", "PLA")
        self.assertEqual(self.printer.status["extruder"]["target"], 215.0)
        self.assertFalse(self.enabled("Ana"))  # no tab bar inside a wizard
        self.press("İptal")
        self.assertEqual(self.printer.gcodes[-1], "M104 S0\nM106 S0")
        self.assertIn("Cold pull", self.texts())  # back on the tools tab

    def test_wizard_blocking_step_and_failure(self):
        self.press("Araçlar", "Tabla seviyele")
        self.printer.fail_next_gcode = "Probe triggered prior to movement"
        self.press("İleri")
        self.assertIn("Probe triggered prior to movement", self.texts())
        self.assertFalse(self.enabled("İleri"))
        self.press("Yeniden dene")
        self.assertEqual(self.printer.gcodes[-1], "G28\nLEVIQ")
        self.assertIn("Tamamlandı", self.texts())
        self.press("İleri", "Bitir")
        self.assertIsNone(self.app.wizard)

    def test_wizards_unavailable_while_printing(self):
        self.start_print()
        self.press("Araçlar")
        self.assertFalse(self.enabled("Cold pull"))

    # ---- settings and failures ----

    def test_settings(self):
        self.press("Ayar", "Otomatik Z-offset")
        self.assertEqual(self.printer.status["leviq3"]["auto_zoffset_on_off"], 1)
        self.press("Sınırlar")
        self.assertIn("320°", self.texts())
        for _ in range(5):
            self.press("", icon="next")
        self.assertIn("kapalı", self.texts())  # tangle detection
        self.press("", icon="back")
        self.press("Sistem")
        self.press("Yazıcıyı yeniden başlat")
        self.assertFalse(self.printer.rebooted)
        self.press("Evet")
        self.assertTrue(self.printer.rebooted)

    def test_limits_pages_and_danger_confirm(self):
        self.press("Ayar", "Sınırlar")
        self.assertFalse(self.enabled("Varsayılana dön"))
        self.assertIn("1 / 6", self.texts())
        self.press("320°", "3", "8", "0", "Tamam")
        self.assertIn("Nozul en çok: 320° -> 380°", "\n".join(self.texts()))
        self.press("Vazgeç")
        self.press("", icon="next")
        self.assertIn("2 / 6", self.texts())
        self.assertIn("Hareket", self.texts())
        self.press("", icon="next")
        self.assertIn("3 / 6", self.texts())
        self.assertIn("Nozul ısı denetimi", self.texts())
        self.press("", icon="next")
        self.assertIn("4 / 6", self.texts())
        self.assertIn("Tabla ısı denetimi", self.texts())
        self.press("", icon="next")
        self.assertIn("5 / 6", self.texts())
        self.assertIn("ACE Isı ve Besleme", self.texts())
        self.press("", icon="next")
        self.assertIn("6 / 6", self.texts())
        self.assertIn("ACE Algılama", self.texts())

    def test_limits_locked_while_printing(self):
        self.start_print()
        self.press("Ayar", "Sınırlar")
        self.assertFalse(self.enabled("320°"))
        self.assertFalse(self.enabled("Yeniden başlat"))
        self.press("", icon="next")
        self.assertIn("Hareket", self.texts())

    def test_limits_save_writes_valid_ini_and_restarts(self):
        import configparser
        self.press("Ayar", "Sınırlar")
        # Non-dangerous change: lowering extruder.max_temp from 320 to 290 saves directly then prompts restart
        self.press("320°", "2", "9", "0", "Tamam")
        raw = self.printer.config_files.get("open_kobra_limits.cfg")
        self.assertIsNotNone(raw)
        parser = configparser.RawConfigParser(strict=False, inline_comment_prefixes=(";", "#"))
        parser.read_string(raw.decode("utf-8"))
        self.assertEqual(parser.getfloat("extruder", "max_temp"), 290.0)
        # Confirming the post-save prompt runs FIRMWARE_RESTART and reloads Klipper settings
        self.assertIn("Sınır kaydedildi. Etkili olması için Klipper yeniden başlatılsın mı?", self.texts())
        self.press("Evet")
        self.app.refresh()
        self.assertEqual(self.printer.settings["extruder"]["max_temp"], 290.0)
        self.assertIn("290°", self.texts())

    def test_limits_reset_clears_cfg_and_restores_defaults(self):
        import configparser
        self.press("Ayar", "Sınırlar")
        self.press("320°", "2", "9", "0", "Tamam", "Evet")
        self.app.refresh()
        self.assertTrue(self.enabled("Varsayılana dön"))
        self.press("Varsayılana dön", "Evet")
        raw = self.printer.config_files.get("open_kobra_limits.cfg")
        self.assertIsNotNone(raw)
        parser = configparser.RawConfigParser(strict=False, inline_comment_prefixes=(";", "#"))
        parser.read_string(raw.decode("utf-8"))
        self.assertEqual(parser.sections(), [])
        self.press("Evet")
        self.app.refresh()
        self.assertEqual(self.printer.settings["extruder"]["max_temp"], 320.0)
        self.assertIn("320°", self.texts())

    def test_limits_missing_cfg_404_still_works(self):
        self.assertNotIn("open_kobra_limits.cfg", self.printer.config_files)
        self.press("Ayar", "Sınırlar")
        self.assertIn("320°", self.texts())
        self.assertEqual(self.app.message, "")
        self.assertFalse(self.enabled("Varsayılana dön"))

    def test_limits_manual_firmware_restart_reloads_settings(self):
        self.printer.config_files["open_kobra_limits.cfg"] = b"[printer]\nmax_velocity = 450\n"
        self.press("Ayar", "Sınırlar")
        self.press("", icon="next")
        self.assertTrue(self.app.limits_pending())
        self.assertEqual(self.printer.settings["printer"]["max_velocity"], 600.0)
        self.assertIn("450 mm/s", self.texts())
        self.press("Yeniden başlat", "Evet")
        self.app.refresh()
        self.assertEqual(self.printer.settings["printer"]["max_velocity"], 450.0)
        self.assertFalse(self.app.limits_pending())

    def test_limits_ace_editable_and_writes_ini_and_bool_confirm(self):
        import configparser
        self.press("Ayar", "Sınırlar")
        for _ in range(4):
            self.press("", icon="next")
        self.assertIn("ACE Isı ve Besleme", self.texts())
        # Raising max_dryer_temperature 60 -> 65 asks dangerous confirm
        self.press("60°", "6", "5", "Tamam")
        self.assertIn("Kurutma en çok: 60° -> 65°", "\n".join(self.texts()))
        self.press("Evet", "Vazgeç")  # save cfg, decline immediate restart
        # Lowering temperature_sensor ace_temp max_temp 70 -> 65 saves directly
        self.press("70°", "6", "5", "Tamam", "Vazgeç")
        # Next page: ACE Algılama booleans
        self.press("", icon="next")
        self.assertIn("ACE Algılama", self.texts())
        # Turning tangle_detection False -> True is safer: saves directly
        self.press("kapalı", "Vazgeç")
        # Both booleans now show "açık"; click the specific button on the "Bağlantı denetimi" row
        btn = next(
            it for it in self.app.layout()
            if getattr(it.widget, "text", None) == "açık"
            and any(getattr(lb.widget, "text", None) == "Bağlantı denetimi" and abs(lb.y - it.y) < 15 for lb in self.app.layout())
        )
        self.assertTrue(self.app.tap(btn.x + btn.w // 2, btn.y + btn.h // 2))
        self.assertIn("Bağlantı denetimi: açık -> kapalı", "\n".join(self.texts()))
        self.press("Evet", "Evet")  # confirm change, then confirm FIRMWARE_RESTART
        self.app.refresh()

        raw = self.printer.config_files.get("open_kobra_limits.cfg")
        self.assertIsNotNone(raw)
        parser = configparser.RawConfigParser(strict=False, inline_comment_prefixes=(";", "#"))
        parser.read_string(raw.decode("utf-8"))
        self.assertEqual(parser.getint("ace", "max_dryer_temperature"), 65)
        self.assertEqual(parser.getint("temperature_sensor ace_temp", "max_temp"), 65)
        self.assertEqual(parser.getboolean("ace", "tangle_detection"), True)
        self.assertEqual(parser.getboolean("ace", "ace_connection_supervision"), False)
        self.assertEqual(self.printer.settings["ace"]["tangle_detection"], True)
        self.assertEqual(self.printer.settings["ace"]["ace_connection_supervision"], False)

    def test_limits_ace_per_instance_override_is_read_only(self):
        self.printer.base_settings["ace"]["max_dryer_temperature"] = "60,1:55"
        self.printer.base_settings["ace"]["total_max_feeding_length"] = "2600,1:3000"
        self.printer.reload_config()
        self.app.state.settings = {}
        self.app.refresh()
        self.press("Ayar", "Sınırlar")
        for _ in range(4):
            self.press("", icon="next")
        texts = self.texts()
        self.assertIn("config dosyasından", texts)
        self.assertEqual(texts.count("config dosyasından"), 2)
        for item in self.app.layout():
            if getattr(item.widget, "text", None) == "config dosyasından":
                self.assertLessEqual(text_width("config dosyasından", item.widget.size), item.w)
        self.assertFalse(self.app.press("config dosyasından"))

    def test_limits_without_ace_has_four_pages(self):
        self.printer.base_settings.pop("ace", None)
        self.printer.base_settings.pop("temperature_sensor ace_temp", None)
        self.printer.reload_config()
        self.app.state.settings = {}
        self.app.refresh()
        self.press("Ayar", "Sınırlar")
        self.assertIn("1 / 4", self.texts())
        for _ in range(3):
            self.press("", icon="next")
        self.assertIn("4 / 4", self.texts())
        self.assertIn("Tabla ısı denetimi", self.texts())
        self.assertFalse(self.enabled("", icon="next"))

    def test_emergency_stop_and_recovery(self):
        placed = self.app.layout()
        self.assertNotIn("DUR", self.texts())
        self.assertNotIn("STOP", self.texts())
        estop_btn = next(
            i for i in placed
            if isinstance(i.widget, Button) and i.widget.kind == "estop" and i.widget.icon == "stop"
        )
        self.assertGreaterEqual(estop_btn.w, 44)
        self.assertGreaterEqual(estop_btn.h, 44)

        # Pressing E-stop icon shows confirmation dialog without immediately stopping
        self.press("", icon="stop")
        self.assertEqual(self.printer.klippy, "ready")
        self.assertIn("Acil durdurma yapılsın mı?", "\n".join(self.texts()))
        self.assertIn("Evet, durdur", self.texts())

        # Cancel leaves printer ready
        self.press("Vazgeç")
        self.assertEqual(self.printer.klippy, "ready")

        # Confirm triggers emergency_stop -> shutdown -> firmware_restart recovers
        self.press("", icon="stop")
        self.press("Evet, durdur")
        self.assertEqual(self.printer.klippy, "shutdown")
        self.assertIn("Klipper: shutdown", self.texts())
        self.press("Yeniden başlat")
        self.assertIn("Hazır", self.texts())

    def test_home_status_badge_in_card_not_header(self):
        from kobra_screen.widgets import TITLE_H
        # Header (y < TITLE_H) must not contain status text; status badge is inside home card (y >= TITLE_H)
        placed = self.app.layout()
        header_texts = [i.widget.text for i in placed if i.y < TITLE_H and hasattr(i.widget, "text")]
        self.assertNotIn("Hazır", header_texts)
        body_hazir = [i for i in placed if i.y >= TITLE_H and getattr(i.widget, "text", None) == "Hazır"]
        self.assertEqual(len(body_hazir), 1)

        # Printing & Paused status badges appear on print card
        self.start_print()
        placed = self.app.layout()
        self.assertTrue(any(i.y >= TITLE_H and getattr(i.widget, "text", None) == "Yazdırılıyor" for i in placed))
        self.press("Duraklat")
        placed = self.app.layout()
        self.assertTrue(any(i.y >= TITLE_H and getattr(i.widget, "text", None) == "Duraklatıldı" for i in placed))
        self.press("Durdur", "Evet")

        # Error status badge
        self.printer.status["print_stats"].update(state="error", message="Thermal fault")
        self.app.refresh()
        placed = self.app.layout()
        self.assertTrue(any(i.y >= TITLE_H and getattr(i.widget, "text", None) == "Hata" for i in placed))

    def test_touch_targets_keyboard_and_color_swatches(self):
        # 1. Wi-Fi keyboard letter keys >= 22 px wide and 44 px tall (QWERTY layout has 10-11 keys per row)
        self.press("Ayar", "Wi-Fi", "Ev_5G")
        for layer_btn in (None, "Shift", "123", "#+="):
            if layer_btn is not None:
                self.press(layer_btn)
            kbd_keys = [
                i for i in self.app.layout()
                if isinstance(i.widget, Button) and i.widget.kind == "kbd"
            ]
            self.assertTrue(kbd_keys)
            for item in kbd_keys:
                self.assertGreaterEqual(item.w, 22, "Key %r too narrow (%d)" % (item.widget.text, item.w))
                self.assertGreaterEqual(item.h, 44, "Key %r too short (%d)" % (item.widget.text, item.h))
        self.press("Vazgeç")

        # 2. Color swatches >= 44x44 and every color has a visible name
        self.press("Makara", "PETG")
        placed = self.app.layout()
        swatches = [i for i in placed if isinstance(i.widget, Button) and i.widget.swatch is not None]
        self.assertEqual(len(swatches), 8)
        for sw in swatches:
            self.assertGreaterEqual(sw.w, 44)
            self.assertGreaterEqual(sw.h, 44)
        for cname in ("Beyaz", "Siyah", "Kırmızı", "Turuncu", "Sarı", "Yeşil", "Mavi", "Mor"):
            self.assertIn(cname, self.texts())

    def test_files_metadata_sorting_and_optimized_rows(self):
        self.press("Yazdır")
        placed = self.app.layout()
        file_btns = [
            i for i in placed
            if isinstance(i.widget, Button) and str(i.widget.text).endswith(".gcode")
        ]
        # 6 rows per page, each >= 44 px tall, with size and modified date in sub line
        self.assertEqual(len(file_btns), 6)
        for btn in file_btns:
            self.assertGreaterEqual(btn.h, 44)
            self.assertIn("KB", btn.widget.sub)
            self.assertIn(":", btn.widget.sub)
        # Default sort is newest first -> kutu_08 first
        self.assertEqual(file_btns[0].widget.text, "kutu_08.gcode")

        # Sort oldest first -> kutu_01 first
        self.press("Eski")
        file_btns = [
            i for i in self.app.layout()
            if isinstance(i.widget, Button) and str(i.widget.text).endswith(".gcode")
        ]
        self.assertEqual(file_btns[0].widget.text, "kutu_01.gcode")

        # Sort by name -> alphabetical (kutu_01 first, case-insensitive)
        self.printer.files.append({"path": "a_ilk.gcode", "modified": 1763200500, "size": 2048})
        self.app.refresh_files()
        self.press("Ad")
        file_btns = [
            i for i in self.app.layout()
            if isinstance(i.widget, Button) and str(i.widget.text).endswith(".gcode")
        ]
        self.assertEqual(file_btns[0].widget.text, "a_ilk.gcode")

    def test_print_history_and_totals(self):
        self.press("Araçlar", "Baskı geçmişi")
        texts = self.texts()
        # Totals from /server/history/totals
        self.assertIn("3 iş", texts)
        self.assertIn("1sa 56dk", texts)
        self.assertIn("5.5 m", texts)
        # List entries from /server/history/list
        self.assertIn("kutu_08.gcode", texts)
        self.assertIn("Tamamlandı", texts)
        self.assertIn("kutu_07.gcode", texts)
        self.assertIn("İptal edildi", texts)
        self.assertIn("silinmis_parca.gcode", texts)
        self.assertIn("Hata", texts)

        # Tap missing-file job -> "Tekrar yazdır" must be disabled
        missing_card = next(
            i for i in self.app.layout()
            if isinstance(i.widget, Panel) and i.widget.action is not None
            and any(getattr(lb.widget, "text", None) == "silinmis_parca.gcode" and abs(lb.y - i.y) < 30 for lb in self.app.layout())
        )
        self.assertTrue(self.app.tap(missing_card.x + 10, missing_card.y + 10))
        self.assertIn("Dosya yok", self.texts())
        self.assertFalse(self.enabled("Tekrar yazdır"))
        self.press("", icon="back")

        # Tap existing job -> "Tekrar yazdır" is enabled and starts print
        existing_card = next(
            i for i in self.app.layout()
            if isinstance(i.widget, Panel) and i.widget.action is not None
            and any(getattr(lb.widget, "text", None) == "kutu_08.gcode" and abs(lb.y - i.y) < 30 for lb in self.app.layout())
        )
        self.assertTrue(self.app.tap(existing_card.x + 10, existing_card.y + 10))
        self.assertIn("Mevcut", self.texts())
        self.assertTrue(self.enabled("Tekrar yazdır"))
        self.press("Tekrar yazdır")
        self.assertEqual(self.printer.status["print_stats"]["state"], "printing")
        self.assertEqual(self.printer.status["print_stats"]["filename"], "kutu_08.gcode")

    def test_exclude_object_list_confirm_and_empty_state(self):
        # 1. Start print with objects -> accessible from both home printing view and Baskı ayarı
        self.start_print()
        self.press("Nesneler")
        self.assertIn("4 nesne · 0 iptal", self.texts())
        self.assertIn("KUTU_GOVDE", self.texts())
        self.assertIn("KUTU_KAPAK", self.texts())

        # Tapping an object opens confirmation before sending EXCLUDE_OBJECT
        self.press("KUTU_KAPAK")
        self.assertIn("KUTU_KAPAK nesnesi iptal edilsin mi?", "\n".join(self.texts()))
        self.press("Vazgeç")
        self.assertEqual(self.printer.status["exclude_object"]["excluded_objects"], [])

        self.press("KUTU_KAPAK")
        self.press("Evet, iptal et")
        self.assertEqual(self.printer.gcodes[-1], "EXCLUDE_OBJECT NAME=KUTU_KAPAK")
        self.assertIn("KUTU_KAPAK", self.printer.status["exclude_object"]["excluded_objects"])
        self.assertIn("4 nesne · 1 iptal", self.texts())
        self.assertFalse(self.enabled("KUTU_KAPAK"))

        # 2. Print without objects (kutu_01.gcode) via Baskı ayarı -> Nesneler shows exact empty message
        self.press("", icon="back")
        self.press("Durdur", "Evet")
        self.press("Yazdır", "Eski", "kutu_01.gcode", "Yazdır")
        self.press("Baskı ayarı", "Nesneler")
        self.assertIn("Bu dosyada nesne bilgisi yok (dilimleyicide etiketlemeyi açın)", self.texts())

    def test_display_brightness_sleep_and_inhibit(self):
        now = [100.0]
        backend_calls = []

        class FakeDisplayBackend:
            @staticmethod
            def set_brightness(pct):
                backend_calls.append(("brightness", pct))

            @staticmethod
            def set_sleep(asleep):
                backend_calls.append(("sleep", asleep))

        cfg_path = os.path.join(self.tmp.name, "kobra-screen.config")
        app = App(
            self.app.client,
            cache_dir=self.tmp.name,
            wifi=self.wifi,
            clock_fn=lambda: now[0],
            config_path=cfg_path,
            display_backend=FakeDisplayBackend(),
        )
        app.refresh()

        self.assertTrue(app.press("Ayar"))
        self.assertTrue(app.press("Ekran"))
        self.assertTrue(app.press("50%"))
        self.assertTrue(app.press("1 dk"))
        self.assertEqual(app.display_brightness, 50)
        self.assertEqual(app.display_sleep_sec, 60)
        self.assertIn(("brightness", 50), backend_calls)

        # Reloading from config restores persisted settings
        app2 = App(self.app.client, cache_dir=self.tmp.name, config_path=cfg_path)
        self.assertEqual(app2.display_brightness, 50)
        self.assertEqual(app2.display_sleep_sec, 60)

        # Sleep inhibited while confirmation modal is open
        app.confirm("Test?", lambda: None)
        now[0] += 120.0
        self.assertIsNone(app.tick_display())
        self.assertFalse(app.display_asleep)
        app.close_modal()

        # After activity timeout with no modal, screen sleeps
        app.note_activity()
        now[0] += 61.0
        self.assertEqual(app.tick_display(), "slept")
        self.assertTrue(app.display_asleep)

        # Waking tap on E-stop coordinates MUST wake without opening E-stop confirm!
        self.assertTrue(app.tap(WIDTH - 24, 22))
        self.assertFalse(app.display_asleep)
        self.assertIsNone(app.modal)

        # Sleep during print: allowed when display_sleep_on_print=True, inhibited when False or on print error
        self.printer.status["print_stats"]["state"] = "printing"
        app.refresh()
        now[0] += 61.0
        self.assertEqual(app.tick_display(), "slept")
        # Error wakes the screen automatically
        self.printer.status["print_stats"]["state"] = "error"
        app.refresh()
        self.assertFalse(app.display_asleep)
        # Disabling sleep during print inhibits sleep while printing
        self.printer.status["print_stats"]["state"] = "printing"
        app.refresh()
        app.set_display_sleep_on_print(False)
        now[0] += 120.0
        self.assertIsNone(app.tick_display())
        self.assertFalse(app.display_asleep)

    def test_gcode_error_is_shown(self):
        self.printer.fail_next_gcode = "Must home axis first"
        self.press("Kontrol", "PLA", "Isıt")
        self.assertIn("Must home axis first", self.texts())

    def test_moonraker_down(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.app.refresh()
        self.assertIn("Bağlantı yok", self.texts())
        with self.assertRaises(MoonrakerError):
            self.app.client.info()

    # ---- layout: every view must fit the 272x480 panel ----

    def views(self):
        yield "home"
        self.press("Yazdır")
        yield "files"
        self.press("kutu_08.gcode")
        yield "file"
        self.press("Yazdır")
        self.ticks(10)
        yield "printing"
        self.press("Baskı ayarı")
        yield "tune"
        self.press("Nesneler")
        yield "objects"
        self.printer.status["exclude_object"] = {"objects": [], "excluded_objects": [], "current_object": None}
        self.app.refresh()
        yield "objects empty"
        self.press("", icon="back")
        self.printer.status["print_stats"].update(state="error", message="Heater extruder not heating at expected rate")
        self.press("", icon="back")
        yield "error"
        self.printer.status["print_stats"]["state"] = "standby"
        self.press("Kontrol")
        yield "heat"
        self.press("Ayrıntı")
        yield "heat_chart"
        self.press("", icon="back")
        self.press("Hedef")
        yield "keypad"
        self.press("Vazgeç", "Eksen")
        yield "move"
        self.press("Ek")
        yield "extra"
        self.press("Makara")
        self.press("PLA", icon="thermo")
        yield "filament"
        self.press("Daha fazla")
        yield "dry_presets"
        self.press("", icon="back")
        self.press("PETG")
        yield "slot"
        self.press("Araçlar")
        yield "tools"
        self.press("Baskı geçmişi")
        yield "history"
        self.app.open("history_detail", self.printer.history_jobs[0]["job_id"])
        yield "history_detail"
        self.press("", icon="back")
        self.press("", icon="back")
        self.press("Tabla haritası")
        yield "bed_mesh empty"
        self.printer.status["bed_mesh"] = {
            "profile_name": "default",
            "mesh_min": [15.0, 15.0],
            "mesh_max": [240.0, 235.0],
            "probed_matrix": [[-0.12 + 0.05 * r + 0.02 * c for c in range(5)] for r in range(5)],
            "mesh_matrix": [[]],
            "profiles": {},
        }
        self.app.refresh()
        yield "bed_mesh 3d"
        self.app.bed_mesh_rot, self.app.bed_mesh_tilt = 40.0, 52.0
        self.app.dirty = True
        yield "bed_mesh turned"
        self.press("Yeniden ölç")
        yield "bed_mesh re-measure confirm"
        self.press("Vazgeç")
        self.press("", icon="back")
        for key, wizard in WIZARDS.items():
            self.app.start_wizard(key)
            for index in range(len(wizard.steps)):
                self.app.wizard.index = index
                self.app.wizard.params = {"material": "PETG", "hot": 240, "pull": 110, "heater": "extruder",
                                          "target": 220}
                self.app.refresh()
                yield "wizard %s %d" % (key, index)
            self.app.wizard.failed = "Probe triggered prior to movement"
            yield "wizard %s failed" % key
            self.app.end_wizard(cancelled=False)
        self.press("Ayar")
        yield "settings"
        self.press("Ekran")
        yield "display"
        self.press("", icon="back")
        self.press("Ekranı kalibre et")
        yield "cal intro"
        self.press("Kalibrasyonu başlat")
        yield "cal corners"
        for rx, ry in ((401, 210), (401, 49), (83, 210), (83, 49)):
            self.app.record_calibration_tap(rx, ry)
        yield "cal verify"
        self.app.record_calibration_verify(136, 240)
        yield "cal confirm"
        self.press("Vazgeç")
        self.press("Wi-Fi")
        yield "wifi"
        self.press("AtolyeWiFi")
        yield "wifi_net"
        self.press("Şifre gir")
        yield "keyboard lower"
        self.press("Shift")
        yield "keyboard upper"
        self.press("123")
        yield "keyboard num"
        self.press("#+=")
        yield "keyboard sym"
        self.press("Vazgeç")
        self.press("", icon="back")
        self.press("Sınırlar")
        yield "limits 0"
        self.press("", icon="next")
        yield "limits 1"
        self.press("", icon="next")
        yield "limits 2"
        self.press("1°")  # En az artış float keypad
        self.press("0", ".", "5")
        yield "limits float keypad"
        self.press("Tamam")
        yield "limits danger confirm"
        self.press("Vazgeç")
        self.press("", icon="next")
        yield "limits 3"
        self.press("", icon="next")
        yield "limits 4"
        self.press("", icon="next")
        yield "limits 5"
        self.press("", icon="back")
        self.press("Sistem")
        yield "system"
        self.press("Klipper'ı yeniden başlat")
        yield "confirm"
        self.press("Vazgeç")
        self.press("", icon="stop")
        yield "estop confirm"
        self.press("Evet, durdur")
        yield "shutdown"

    @unittest.skipUnless(render_pil.ui_font_available(), "needs AlibabaSans-Regular.ttf (kobra-screen/fonts or KOBRA_SCREEN_FONT)")
    def test_every_view_fits_and_texts_are_not_clipped(self):
        from PIL import Image, ImageDraw

        draw = ImageDraw.Draw(Image.new("L", (1, 1)))
        seen = 0
        for name in self.views():
            placed = self.app.layout()
            seen += 1
            self.assertLessEqual(self.app.content_bottom, self.app.content_limit, "%s taşıyor" % name)
            fg = []
            for item in placed:
                self.assertGreaterEqual(item.x, 0, name)
                self.assertLessEqual(item.x + item.w, WIDTH, name)
                self.assertLessEqual(item.y + item.h, HEIGHT, name)
                widget = item.widget
                if isinstance(widget, Button):
                    self.assertGreaterEqual(item.h, 28, "%s: %r çok alçak" % (name, widget.text))
                    _, box = button_parts(item)
                    if box and widget.text:
                        if widget.sub:
                            self.assertGreaterEqual(box[3], 36, "%s: %r alt satır sığmaz" % (name, widget.text))
                            self.assertLessEqual(text_width(widget.sub, "small") * 1.15, box[2],
                                                 "%s: %r alt yazısı sığmıyor" % (name, widget.sub))
                            sz = button_sub_text_size(item, box[2])
                            if not str(widget.text).endswith(".gcode"):
                                self.assertLessEqual(text_width(widget.text, sz) * 1.15, box[2],
                                                     "%s: %r düğmeye sığmıyor" % (name, widget.text))
                        else:
                            sz = button_text_size(item)
                            lh = text_line_height(sz)
                            self.assertLessEqual(lh, box[3] + 0.5, "%s: %r dikey sığmıyor" % (name, widget.text))
                            if "\n" not in widget.text and box[3] < 2 * lh:
                                self.assertLessEqual(text_width(widget.text, sz) * 1.15, box[2],
                                                     "%s: %r düğmeye sığmıyor" % (name, widget.text))
                            else:
                                lines = wrap(draw, widget.text, font(sz), (box[2] / 1.15) * SCALE)
                                self.assertLessEqual(len(lines) * lh, box[3] + 1.0,
                                                     "%s: %r satırları sığmıyor" % (name, widget.text))
                elif isinstance(widget, Label) and widget.text:
                    lh = text_line_height(widget.size)
                    self.assertLessEqual(lh, item.h + 0.5, "%s: %r dikey sığmıyor" % (name, widget.text))
                    if widget.unit:
                        total_w = text_width(widget.text, widget.size) + 2 + text_width(
                            widget.unit, label_unit_size(widget.size))
                        self.assertLessEqual(total_w * 1.15, item.w, "%s: %r%s sığmıyor" % (name, widget.text, widget.unit))
                    elif "\n" not in widget.text and item.h < 2 * lh:
                        if not str(widget.text).endswith(".gcode") and "Heater extruder" not in widget.text:
                            self.assertLessEqual(text_width(widget.text, widget.size) * 1.15, item.w,
                                                 "%s: %r sığmıyor" % (name, widget.text))
                    elif "Heater extruder" not in widget.text and "Probe triggered" not in widget.text:
                        lines = wrap(draw, widget.text, font(widget.size), (item.w / 1.15) * SCALE)
                        self.assertLessEqual(len(lines) * lh, item.h + 1.5,
                                             "%s: %r satırları sığmıyor" % (name, widget.text))
                if not isinstance(widget, (Panel, Crosshair)):
                    is_toast = (
                        bool(self.app.message)
                        and isinstance(widget, Button)
                        and item.x == 12
                        and item.w == WIDTH - 24
                        and item.h == 44
                        and item.y in (HEIGHT - TABS_H - 52, HEIGHT - 52)
                    )
                    if not is_toast:
                        fg.append(item)
            for i in range(len(fg)):
                a = fg[i]
                for j in range(i + 1, len(fg)):
                    b = fg[j]
                    ox = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
                    oy = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
                    self.assertFalse(ox > 1 and oy > 1, "%s: öğe çakışması" % name)
            self.assertEqual(render(placed).size, (WIDTH, HEIGHT))
        self.assertGreater(seen, 40)

    def test_thumbnail_cache_is_bounded_in_tmp(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = App(self.app.client, cache_dir=tmp, max_thumbnails=4)
            app.refresh()
            printing_file = app.get("print_stats", "filename", "")
            for idx in range(10):
                fname = "extra_%02d.gcode" % idx
                self.printer.files.append({"path": fname, "size": 1000, "modified": 1700000000 + idx})
                app.load_metadata(fname)
            pngs = [name for name in os.listdir(tmp) if name.startswith("thumb-") and name.endswith(".png")]
            self.assertLessEqual(len(pngs), 4)
            if printing_file in app.meta and app.meta[printing_file].get("thumbnail"):
                self.assertTrue(os.path.exists(app.meta[printing_file]["thumbnail"]))

    def test_limits_klipper_constraints_and_high_values_allowed(self):
        from kobra_screen import limits

        # 1. Keypad enforces cartesian.py lower bounds for max_velocity (>= max_z_velocity) and max_accel (>= max_z_accel)
        self.press("Ayar", "Sınırlar")
        self.press("", icon="next")  # Hareket
        self.press("600 mm/s", "1", "5")
        self.assertFalse(self.enabled("Tamam"))  # 15 < max_z_velocity (20)
        self.press("<", "<", "2", "0")
        self.assertTrue(self.enabled("Tamam"))   # 20 >= 20
        self.press("Vazgeç")

        self.press("20000 mm/s²", "2", "5", "0", "0")
        self.assertFalse(self.enabled("Tamam"))  # 2500 < max_z_accel (3000)
        self.press("<", "<", "<", "<", "3", "0", "0", "0")
        self.assertTrue(self.enabled("Tamam"))   # 3000 >= 3000
        self.press("Vazgeç")

        # High values are never artificially capped
        self.press("600 mm/s", "1", "2", "0", "0")
        self.assertTrue(self.enabled("Tamam"))
        self.press("Tamam", "Evet", "Evet")
        self.app.refresh()
        self.assertEqual(self.printer.settings["printer"]["max_velocity"], 1200.0)

        # 2. Pre-write validation rejects invalid cross-option combinations without writing cfg
        before_cfg = self.printer.config_files.get("open_kobra_limits.cfg")
        self.assertFalse(self.app.save_limits({("printer", "max_velocity"): 15}))
        self.assertIn("Z hızından", self.app.message)
        self.assertEqual(self.printer.config_files.get("open_kobra_limits.cfg"), before_cfg)

        self.assertFalse(self.app.save_limits({("printer", "max_accel"): 2500}))
        self.assertIn("Z ivmesinden", self.app.message)
        self.assertEqual(self.printer.config_files.get("open_kobra_limits.cfg"), before_cfg)

        # Lowering max_z_velocity first allows setting max_velocity=15
        self.assertTrue(self.app.save_limits({("printer", "max_z_velocity"): 10}))
        self.app.close_modal()
        self.assertTrue(self.app.save_limits({("printer", "max_velocity"): 15}))
        self.app.close_modal()

        # Extruder min_extrude_temp > max_temp and ace_temp max_temp <= min_temp are rejected
        self.assertFalse(self.app.save_limits({("extruder", "max_temp"): 150}))
        self.assertFalse(self.app.save_limits({("temperature_sensor ace_temp", "max_temp"): 0}))
        self.assertEqual(limits.validate_limits({("extruder", "max_temp"): 450}, self.printer.base_settings), [])

    def test_limits_error_recovery_and_auto_rollback(self):
        # 1. Invalid config triggers Klipper error -> offline screen offers reset & restart
        self.printer.config_files["open_kobra_limits.cfg"] = b"[printer]\nmax_velocity = 10\n"
        self.printer.klippy = "error"
        self.app.refresh()
        self.assertIn("Klipper: error", self.texts())
        self.assertTrue(self.enabled("Sınırları sıfırla ve başlat"))
        self.assertTrue(self.enabled("Yeniden başlat"))
        self.assertTrue(self.enabled("Stok moda dön"))
        self.assertLessEqual(self.app.content_bottom, self.app.content_limit)

        self.press("Sınırları sıfırla ve başlat", "Evet")
        self.assertEqual(self.printer.klippy, "ready")
        self.assertIn("Hazır", self.texts())

        # 2. If user is already on limits_page when Klipper is in error, reset & restart stay enabled
        self.press("Ayar", "Sınırlar")
        self.printer.klippy = "error"
        self.app.refresh()
        self.assertTrue(self.enabled("Varsayılana dön"))
        self.assertTrue(self.enabled("Yeniden başlat"))
        self.press("Varsayılana dön", "Evet", "Evet")
        self.assertEqual(self.printer.klippy, "ready")

        # 3. Auto-rollback: if firmware_restart after saving new limits leaves Klipper in error, restore previous cfg
        self.app.save_limits({("extruder", "max_temp"): 290})
        self.press("Evet")
        self.app.refresh()
        self.assertEqual(self.printer.settings["extruder"]["max_temp"], 290.0)

        # Save another change, but simulate Klipper failing to come up ready on restart
        self.app.save_limits({("extruder", "max_temp"): 280})
        orig_fw_restart = self.printer.handle
        inject_once = [True]

        def failing_handle(method, path, query, body):
            if path == "/printer/firmware_restart" and inject_once[0]:
                inject_once[0] = False
                self.printer.klippy = "error"
                return "ok"
            return orig_fw_restart(method, path, query, body)

        self.printer.handle = failing_handle
        try:
            self.press("Evet")  # restarts into injected error -> refresh() rolls back to 290 & restarts cleanly
        finally:
            self.printer.handle = orig_fw_restart
        self.app.refresh()
        self.assertEqual(self.printer.klippy, "ready")
        self.assertEqual(self.printer.settings["extruder"]["max_temp"], 290.0)


class _StrictNamespace:
    def __init__(self, name, allowed):
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_allowed", dict(allowed))

    def __getattr__(self, item):
        allowed = object.__getattribute__(self, "_allowed")
        if item in allowed:
            return allowed[item]
        raise AttributeError("%s has no attribute %r" % (object.__getattribute__(self, "_name"), item))

    def __setattr__(self, key, value):
        raise AttributeError("%s is read-only" % object.__getattribute__(self, "_name"))


def _load_rinkhals_lvgl_contract():
    """Parse Rinkhals opt/rinkhals/ui/lvgl/__init__.py AST to extract exact enums, classes, and method arities."""
    import ast

    candidates = [
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "refs/Rinkhals/files/3-rinkhals/opt/rinkhals/ui/lvgl/__init__.py",
        ),
        "/useremain/rinkhals/.current/opt/rinkhals/ui/lvgl/__init__.py",
    ]
    lvgl_init = next((p for p in candidates if os.path.exists(p)), None)
    if lvgl_init is None:
        return None

    with open(lvgl_init, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=lvgl_init)

    enums = {}
    raw_classes = {}
    class_bases = {}
    functions = {}

    def _arity(fn_node, is_method=False):
        total = len(fn_node.args.args) - (1 if is_method else 0)
        defaults = len(fn_node.args.defaults)
        min_a = max(0, total - defaults)
        max_a = 99 if fn_node.args.vararg is not None else max(0, total)
        return (min_a, max_a)

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
            if any(b in ("Flag", "IntEnum", "Enum") for b in bases):
                members = {}
                for stmt in node.body:
                    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                        try:
                            val = ast.literal_eval(stmt.value)
                        except Exception:
                            continue
                        if isinstance(val, int):
                            members[stmt.targets[0].id] = val
                # Intentionally overwrites earlier class definitions of the same name
                # (reproducing the duplicate `class PART(Flag)` bug at lvgl/__init__.py:325).
                enums[node.name] = members
            else:
                methods = raw_classes.setdefault(node.name, {})
                class_bases[node.name] = bases
                for stmt in node.body:
                    if isinstance(stmt, ast.FunctionDef):
                        methods[stmt.name] = _arity(stmt, is_method=True)
        elif isinstance(node, ast.FunctionDef):
            functions[node.name] = _arity(node, is_method=False)

    classes = {}
    for cls_name, methods in raw_classes.items():
        merged = {}
        for base in class_bases.get(cls_name, []):
            if base in raw_classes:
                merged.update(raw_classes[base])
        merged.update(methods)
        classes[cls_name] = merged

    return {"enums": enums, "classes": classes, "functions": functions}


_LVGL_CONTRACT = _load_rinkhals_lvgl_contract()


class StrictFakeLvgl:
    """Fake `lvgl` module enforcing the exact symbols, enum values, and method arities of Rinkhals lvgl/__init__.py."""

    _FALLBACK_ENUMS = {
        "STATE": {"DEFAULT": 0, "CHECKED": 1, "FOCUSED": 2, "PRESSED": 32, "DISABLED": 128},
        # Reproduce the exact duplicate-class bug in Rinkhals lvgl/__init__.py:314-327:
        # `PART` ONLY has `TEXTAREA_PLACEHOLDER` (MAIN/INDICATOR/KNOB are missing!).
        "PART": {"TEXTAREA_PLACEHOLDER": 524288},
        "ALIGN": {"DEFAULT": 0, "TOP_LEFT": 1, "TOP_MID": 2, "BOTTOM_MID": 5, "LEFT_MID": 7, "RIGHT_MID": 8, "CENTER": 9},
        "TEXT_ALIGN": {"AUTO": 0, "LEFT": 1, "CENTER": 2, "RIGHT": 3},
        "LABEL_LONG_MODE": {"WRAP": 0, "DOTS": 1, "SCROLL": 2, "SCROLL_CIRCULAR": 3, "CLIP": 4},
        "OBJ_FLAG": {"HIDDEN": 1, "CLICKABLE": 2, "SCROLLABLE": 16, "IGNORE_LAYOUT": 131072},
        "EVENT_CODE": {"ALL": 0, "PRESSED": 1, "PRESSING": 2, "PRESS_LOST": 3, "CLICKED": 10, "RELEASED": 11,
                       "VALUE_CHANGED": 35},
        "DISPLAY_ROTATION": {"_0": 0, "_90": 1, "_180": 2, "_270": 3},
        "INDEV_TYPE": {"NONE": 0, "POINTER": 1, "KEYPAD": 2, "BUTTON": 3, "ENCODER": 4},
        "OPA": {"TRANSP": 0, "COVER": 255},
        "CHART_TYPE": {"NONE": 0, "LINE": 1, "BAR": 2, "SCATTER": 3},
        "IMAGE_ALIGN": {"DEFAULT": 0, "STRETCH": 11},
        "CHART_AXIS": {"PRIMARY_Y": 0, "SECONDARY_Y": 1, "PRIMARY_X": 2, "SECONDARY_X": 4, "LAST": 5},
    }

    _FALLBACK_OBJ_ARITY = {
        "__init__": (1, 1),
        "set_pos": (2, 2), "set_x": (1, 1), "set_y": (1, 1),
        "set_size": (2, 2), "set_width": (1, 1), "set_height": (1, 1),
        "set_align": (1, 1), "align": (3, 3), "center": (0, 0),
        "clean": (0, 0), "delete": (0, 0), "invalidate": (0, 0),
        "add_flag": (1, 1), "remove_flag": (1, 1), "has_flag": (1, 1),
        "add_state": (1, 1), "remove_state": (1, 1),
        "add_event_cb": (3, 3), "get_self_width": (0, 0), "get_self_height": (0, 0),
        "set_style_bg_color": (2, 2), "set_style_bg_opa": (2, 2),
        "set_style_border_width": (2, 2), "set_style_border_color": (2, 2), "set_style_border_opa": (2, 2),
        "set_style_shadow_width": (2, 2), "set_style_pad_all": (2, 2), "set_style_radius": (2, 2),
        "set_style_text_font": (2, 2), "set_style_text_color": (2, 2), "set_style_text_align": (2, 2),
        "set_style_arc_width": (2, 2), "set_style_arc_color": (2, 2),
        "set_style_line_width": (2, 2), "set_style_line_color": (2, 2),
        "set_style_width": (2, 2), "set_style_height": (2, 2), "set_style_size": (3, 3),
    }

    _FALLBACK_CLASS_EXTRAS = {
        "obj": {},
        "label": {"set_text": (1, 1), "get_text": (0, 0), "set_long_mode": (1, 1)},
        "button": {},
        "bar": {"set_range": (2, 2), "set_value": (2, 2)},
        "arc": {"set_bg_angles": (2, 2), "set_rotation": (1, 1), "set_range": (2, 2), "set_value": (1, 1)},
        "image": {"set_src": (1, 1), "set_scale": (1, 1)},
        "chart": {
            "set_type": (1, 1), "set_point_count": (1, 1), "set_axis_range": (3, 3),
            "set_div_line_count": (2, 2), "add_series": (2, 2),
            "set_series_value_by_id": (3, 3), "refresh": (0, 0),
        },
        "display": {"set_rotation": (1, 1), "set_dpi": (1, 1)},
        "indev": {"set_display": (1, 1), "add_event_cb": (3, 3), "wait_release": (0, 0)},
        "event": {"get_current_target_obj": (0, 0)},
    }

    _FALLBACK_FUNCTIONS = {
        "init": (0, 0), "deinit": (0, 0), "is_initialized": (0, 0),
        "tick_inc": (1, 1), "timer_handler": (0, 0),
        "color_make": (3, 3), "color_hex": (1, 1), "pct": (1, 1), "dpx": (1, 1),
        "font_get_default": (0, 0), "tiny_ttf_create_file": (2, 2), "tiny_ttf_destroy": (1, 1),
        "screen_load": (1, 1), "screen_active": (0, 0),
        "linux_fbdev_create": (0, 0), "linux_fbdev_set_file": (2, 2),
        "evdev_create": (2, 2), "evdev_grab_device": (1, 1), "evdev_release_device": (1, 1),
        "evdev_set_calibration": (5, 5), "evdev_delete": (1, 1),
    }

    @classmethod
    def _class_methods(cls, cls_name):
        if _LVGL_CONTRACT is not None and cls_name in _LVGL_CONTRACT["classes"]:
            return _LVGL_CONTRACT["classes"][cls_name]
        base = dict(cls._FALLBACK_OBJ_ARITY) if cls_name in ("obj", "label", "button", "bar", "arc", "image", "chart") else {}
        base.update(cls._FALLBACK_CLASS_EXTRAS.get(cls_name, {}))
        return base

    @classmethod
    def _check_arity(cls, owner_name, method_name, arity, args):
        min_a, max_a = arity
        if not (min_a <= len(args) <= max_a):
            raise TypeError(
                "%s.%s expects %d..%d positional args, got %d"
                % (owner_name, method_name, min_a, max_a, len(args))
            )

    def __init__(self):
        object.__setattr__(self, "_next_ptr", 1000)
        object.__setattr__(self, "_objects", {})
        object.__setattr__(self, "grabbed", False)
        object.__setattr__(self, "released_count", 0)
        object.__setattr__(self, "grabbed_count", 0)
        object.__setattr__(self, "active_screen", None)
        object.__setattr__(self, "calibration", None)
        object.__setattr__(self, "fbdev_file", None)

        enum_source = _LVGL_CONTRACT["enums"] if _LVGL_CONTRACT is not None else self._FALLBACK_ENUMS
        for enum_name, fallback_members in self._FALLBACK_ENUMS.items():
            members = enum_source.get(enum_name, fallback_members)
            object.__setattr__(self, enum_name, _StrictNamespace(enum_name, members))

        lv_mod = self

        class FakeObj:
            _LV_CLASS = "obj"
            _USER_ATTRS = {
                "_pointer", "_parent", "_children", "_callbacks", "_styles", "_flags",
                "_pos", "_size_wh", "_text", "_range", "_value", "_src", "_valid_png", "_scale",
                "_box_w", "_box_h", "_size", "_font_size", "_align", "unit_label",
                "text_label", "sub_label", "glyph", "r_glyph", "display_text", "display_sub",
                "dot", "halo", "rim", "hole", "tab_bar", "toggle_track", "toggle_knob",
                "_side", "_loaded_path", "_mesh_key", "_angles", "_rotation", "_long_mode", "_series",
                "_chart_type", "_point_count", "_div_lines", "_axis_ranges",
            }

            def __init__(self, *args):
                methods = StrictFakeLvgl._class_methods(self._LV_CLASS)
                StrictFakeLvgl._check_arity(self._LV_CLASS, "__init__", methods.get("__init__", (1, 1)), args)
                parent = args[0] if args else None
                ptr = lv_mod._alloc_ptr()
                object.__setattr__(self, "_pointer", ptr)
                object.__setattr__(self, "_parent", parent)
                object.__setattr__(self, "_children", [])
                object.__setattr__(self, "_callbacks", [])
                object.__setattr__(self, "_styles", {})
                object.__setattr__(self, "_flags", set())
                object.__setattr__(self, "_pos", (0, 0))
                object.__setattr__(self, "_size_wh", (0, 0))
                lv_mod._objects[ptr] = self
                if parent is not None:
                    parent._children.append(self)

            def __getattr__(self, name):
                methods = StrictFakeLvgl._class_methods(self._LV_CLASS)
                if name in methods:
                    arity = methods[name]

                    def _method(*args):
                        StrictFakeLvgl._check_arity(self._LV_CLASS, name, arity, args)
                        if name.startswith("set_style_"):
                            self._styles[(name, args[-1])] = args[0]
                        elif name == "set_pos":
                            if not isinstance(args[0], int) or not isinstance(args[1], int):
                                raise TypeError("set_pos expects (int, int)")
                            object.__setattr__(self, "_pos", (args[0], args[1]))
                        elif name == "set_size":
                            if not isinstance(args[0], int) or not isinstance(args[1], int):
                                raise TypeError("set_size expects (int, int)")
                            object.__setattr__(self, "_size_wh", (args[0], args[1]))
                        elif name in ("set_width", "set_height", "set_x", "set_y"):
                            if not isinstance(args[0], int):
                                raise TypeError("%s expects int" % name)
                        elif name == "add_flag":
                            self._flags.add(args[0])
                        elif name == "remove_flag":
                            self._flags.discard(args[0])
                        elif name == "has_flag":
                            return args[0] in self._flags
                        elif name == "clean":
                            self._children.clear()
                        elif name == "add_event_cb":
                            if not callable(args[0]):
                                raise TypeError("add_event_cb expects callable first argument")
                            self._callbacks.append(args)
                        elif name == "get_self_width":
                            return 96 if getattr(self, "_valid_png", False) else 0
                        elif name == "get_self_height":
                            return 96 if getattr(self, "_valid_png", False) else 0
                        elif name == "set_text":
                            if not isinstance(args[0], str):
                                raise TypeError("label.set_text expects str")
                            object.__setattr__(self, "_text", args[0])
                        elif name == "get_text":
                            return getattr(self, "_text", "")
                        elif name == "set_long_mode":
                            object.__setattr__(self, "_long_mode", int(args[0]))
                        elif name == "set_range":
                            if not isinstance(args[0], int) or not isinstance(args[1], int):
                                raise TypeError("set_range expects (int, int)")
                            object.__setattr__(self, "_range", (args[0], args[1]))
                        elif name == "set_bg_angles":
                            if not isinstance(args[0], (int, float)) or not isinstance(args[1], (int, float)):
                                raise TypeError("set_bg_angles expects numeric angles")
                            object.__setattr__(self, "_angles", (args[0], args[1]))
                        elif name == "set_rotation":
                            if not isinstance(args[0], int):
                                raise TypeError("set_rotation expects int")
                            object.__setattr__(self, "_rotation", args[0])
                        elif name == "set_value":
                            if self._LV_CLASS == "bar":
                                object.__setattr__(self, "_value", (int(args[0]), bool(args[1])))
                            else:
                                object.__setattr__(self, "_value", int(args[0]))
                        elif name == "set_src":
                            src = args[0]
                            if not isinstance(src, str):
                                raise TypeError("image.set_src expects str path")
                            object.__setattr__(self, "_src", src)
                            valid_png = False
                            if os.path.exists(src):
                                try:
                                    with open(src, "rb") as f:
                                        valid_png = f.read(8) == b"\x89PNG\r\n\x1a\n"
                                except OSError:
                                    valid_png = False
                            object.__setattr__(self, "_valid_png", valid_png)
                        elif name == "set_scale":
                            if not isinstance(args[0], int):
                                raise TypeError("image.set_scale expects int")
                            object.__setattr__(self, "_scale", args[0])
                        elif name == "set_type":
                            object.__setattr__(self, "_chart_type", args[0])
                        elif name == "set_point_count":
                            object.__setattr__(self, "_point_count", int(args[0]))
                        elif name == "set_div_line_count":
                            object.__setattr__(self, "_div_lines", (int(args[0]), int(args[1])))
                        elif name == "set_axis_range":
                            ranges = getattr(self, "_axis_ranges", None)
                            if ranges is None:
                                ranges = {}
                                object.__setattr__(self, "_axis_ranges", ranges)
                            ranges[args[0]] = (int(args[1]), int(args[2]))
                        elif name == "add_series":
                            ser = FakeChartSeries(args[0], args[1], getattr(self, "_point_count", 10))
                            return ser
                        elif name == "set_series_value_by_id":
                            ser, idx, val = args
                            if 0 <= int(idx) < len(ser.values):
                                ser.values[int(idx)] = int(val)
                        return None

                    return _method
                raise AttributeError("%s has no LVGL method/attribute %r" % (self._LV_CLASS, name))

            def __setattr__(self, key, value):
                if key in self._USER_ATTRS:
                    object.__setattr__(self, key, value)
                else:
                    raise AttributeError("unexpected attribute %r on %s" % (key, self._LV_CLASS))

        class FakeLabel(FakeObj):
            _LV_CLASS = "label"

        class FakeButton(FakeObj):
            _LV_CLASS = "button"

        class FakeBar(FakeObj):
            _LV_CLASS = "bar"

        class FakeArc(FakeObj):
            _LV_CLASS = "arc"

        class FakeImage(FakeObj):
            _LV_CLASS = "image"

        class FakeChartSeries:
            def __init__(self, color_val, axis, count):
                self._pointer = lv_mod._alloc_ptr()
                self.color = color_val
                self.axis = axis
                self.values = [0] * int(count)

        class FakeChart(FakeObj):
            _LV_CLASS = "chart"

        class FakeDisplay:
            _LV_CLASS = "display"

            def __init__(self):
                self._pointer = lv_mod._alloc_ptr()
                self.rotation = None
                self.dpi = None

            def __getattr__(self, name):
                methods = StrictFakeLvgl._class_methods(self._LV_CLASS)
                if name in methods:
                    arity = methods[name]

                    def _method(*args):
                        StrictFakeLvgl._check_arity(self._LV_CLASS, name, arity, args)
                        if name == "set_rotation":
                            self.rotation = args[0]
                        elif name == "set_dpi":
                            self.dpi = int(args[0])
                        return None

                    return _method
                raise AttributeError("display has no LVGL method %r" % name)

        class FakeIndev:
            _LV_CLASS = "indev"

            def __init__(self, indev_type, dev_path):
                self._pointer = lv_mod._alloc_ptr()
                self.indev_type = indev_type
                self.dev_path = dev_path
                self.display = None
                self.callbacks = []
                self.waited_release = 0

            def __getattr__(self, name):
                methods = StrictFakeLvgl._class_methods(self._LV_CLASS)
                if name in methods:
                    arity = methods[name]

                    def _method(*args):
                        StrictFakeLvgl._check_arity(self._LV_CLASS, name, arity, args)
                        if name == "set_display":
                            self.display = args[0]
                        elif name == "add_event_cb":
                            self.callbacks.append(args)
                        elif name == "wait_release":
                            self.waited_release += 1
                        return None

                    return _method
                raise AttributeError("indev has no LVGL method %r" % name)

        class FakeEvent:
            _LV_CLASS = "event"

            def __init__(self, target_ptr, recreate_wrapper=False):
                self._pointer = lv_mod._alloc_ptr()
                self._target_ptr = target_ptr
                self._recreate = recreate_wrapper

            def __getattr__(self, name):
                methods = StrictFakeLvgl._class_methods(self._LV_CLASS)
                if name in methods:
                    arity = methods[name]

                    def _method(*args):
                        StrictFakeLvgl._check_arity(self._LV_CLASS, name, arity, args)
                        if name == "get_current_target_obj":
                            if not self._recreate and self._target_ptr in lv_mod._objects:
                                return lv_mod._objects[self._target_ptr]
                            wrapper = FakeObj.__new__(FakeObj)
                            object.__setattr__(wrapper, "_pointer", self._target_ptr)
                            return wrapper
                        return None

                    return _method
                raise AttributeError("event has no LVGL method %r" % name)

        object.__setattr__(self, "obj", FakeObj)
        object.__setattr__(self, "label", FakeLabel)
        object.__setattr__(self, "button", FakeButton)
        object.__setattr__(self, "bar", FakeBar)
        object.__setattr__(self, "arc", FakeArc)
        object.__setattr__(self, "image", FakeImage)
        object.__setattr__(self, "chart", FakeChart)
        object.__setattr__(self, "display", FakeDisplay)
        object.__setattr__(self, "indev", FakeIndev)
        object.__setattr__(self, "event", FakeEvent)

    def _alloc_ptr(self):
        ptr = object.__getattribute__(self, "_next_ptr")
        object.__setattr__(self, "_next_ptr", ptr + 1)
        return ptr

    def __getattr__(self, name):
        fn_map = _LVGL_CONTRACT["functions"] if _LVGL_CONTRACT is not None else self._FALLBACK_FUNCTIONS
        if name in fn_map:
            arity = fn_map[name]

            def _fn(*args):
                StrictFakeLvgl._check_arity("lvgl", name, arity, args)
                return self._dispatch_top_fn(name, args)

            return _fn
        raise AttributeError("lvgl module has no attribute %r" % name)

    def _dispatch_top_fn(self, name, args):
        if name in ("init", "deinit", "tick_inc", "tiny_ttf_destroy"):
            return None
        if name == "is_initialized":
            return True
        if name == "timer_handler":
            return 0
        if name == "color_make":
            return (int(args[0]), int(args[1]), int(args[2]))
        if name in ("color_hex", "pct", "dpx"):
            return int(args[0])
        if name == "font_get_default":
            return ("default_font", 14)
        if name == "tiny_ttf_create_file":
            return ("ttf", args[0], int(args[1]))
        if name == "screen_load":
            object.__setattr__(self, "active_screen", args[0])
            return None
        if name == "screen_active":
            return object.__getattribute__(self, "active_screen")
        if name == "linux_fbdev_create":
            return self.display()
        if name == "linux_fbdev_set_file":
            object.__setattr__(self, "fbdev_file", args[1])
            return None
        if name == "evdev_create":
            indev = self.indev(args[0], args[1])
            object.__setattr__(self, "last_indev", indev)
            return indev
        if name == "evdev_grab_device":
            object.__setattr__(self, "grabbed", True)
            object.__setattr__(self, "grabbed_count", self.grabbed_count + 1)
            return None
        if name == "evdev_release_device":
            object.__setattr__(self, "grabbed", False)
            object.__setattr__(self, "released_count", self.released_count + 1)
            return None
        if name == "evdev_set_calibration":
            object.__setattr__(self, "calibration", (args[1], args[2], args[3], args[4]))
            return None
        if name == "evdev_delete":
            object.__setattr__(self, "grabbed", False)
            return None
        return None


class LvglTests(unittest.TestCase):
    setUp = ScreenTests.setUp
    press = ScreenTests.press
    ticks = ScreenTests.ticks
    start_print = ScreenTests.start_print
    views = ScreenTests.views

    def test_strict_fake_lvgl_enforces_rinkhals_contract(self):
        fake_lv = StrictFakeLvgl()
        # Verify the duplicate PART class bug from Rinkhals lvgl/__init__.py:314-327 is reproduced
        with self.assertRaises(AttributeError):
            _ = fake_lv.PART.INDICATOR
        with self.assertRaises(AttributeError):
            _ = fake_lv.PART.KNOB
        with self.assertRaises(AttributeError):
            _ = fake_lv.font_default
        with self.assertRaises(AttributeError):
            _ = fake_lv.non_existent_symbol

        # Verify exact enum value from lvgl/__init__.py:847
        self.assertEqual(fake_lv.OBJ_FLAG.IGNORE_LAYOUT, 131072)

        obj = fake_lv.obj(None)
        with self.assertRaises(AttributeError):
            obj.non_existent_method()

        # Verify argument count enforcement across widget methods and top-level functions
        with self.assertRaises(TypeError):
            obj.set_pos(10)
        with self.assertRaises(TypeError):
            obj.set_size(10, 20, 30)
        with self.assertRaises(TypeError):
            obj.center(1)
        with self.assertRaises(TypeError):
            obj.set_style_bg_color((0, 0, 0))

        bar = fake_lv.bar(obj)
        with self.assertRaises(TypeError):
            bar.set_range(0)
        with self.assertRaises(TypeError):
            bar.set_value(500)

        arc = fake_lv.arc(obj)
        with self.assertRaises(TypeError):
            arc.set_bg_angles(0)
        with self.assertRaises(TypeError):
            arc.set_value(500, False)

        lbl = fake_lv.label(obj)
        with self.assertRaises(TypeError):
            lbl.set_long_mode()

        disp = fake_lv.linux_fbdev_create()
        with self.assertRaises(TypeError):
            disp.set_dpi()
        with self.assertRaises(TypeError):
            fake_lv.color_make(255, 255)

    def test_lvgl_renders_every_view_and_updates_in_place(self):
        import kobra_screen.render_lvgl as render_lvgl
        fake_lv = StrictFakeLvgl()
        orig_lv = render_lvgl.lv
        render_lvgl.lv = fake_lv
        try:
            screen = render_lvgl.LvglScreen(manage_system=False)
            self.assertEqual(fake_lv.fbdev_file, "/dev/fb0")
            self.assertEqual(fake_lv.calibration, (25, 235, 460, 25))
            self.assertTrue(fake_lv.grabbed)

            seen = 0
            for name in self.views():
                placed = self.app.layout()
                # First sync builds LVGL widget tree (_create); second sync updates in-place (_update)
                screen.sync(placed)
                screen.sync(placed)
                self.assertEqual(len(screen.objects), len(placed), name)
                self.assertLess(len(fake_lv._objects), 500, "%s lv._objects sızıntısı" % name)
                seen += 1
            self.assertGreater(seen, 40)
            screen.close()
            self.assertFalse(fake_lv.grabbed)
        finally:
            render_lvgl.lv = orig_lv

    def test_lvgl_click_events_dispatch_button_and_panel_actions(self):
        import kobra_screen.render_lvgl as render_lvgl
        fake_lv = StrictFakeLvgl()
        orig_lv = render_lvgl.lv
        render_lvgl.lv = fake_lv
        try:
            screen = render_lvgl.LvglScreen(manage_system=False)
            placed = self.app.layout()
            screen.sync(placed)

            # 1. Click a clickable Panel card on the home screen (opens heat control)
            panel_idx = next(
                i for i, item in enumerate(placed)
                if isinstance(item.widget, Panel) and item.widget.action is not None
            )
            panel_obj = screen.objects[panel_idx]
            cb, code, _ = panel_obj._callbacks[0]
            self.assertEqual(code, fake_lv.EVENT_CODE.CLICKED)
            cb(fake_lv.event(panel_obj._pointer, recreate_wrapper=True))
            self.assertEqual(self.app.tab, "control")

            # 2. Sync the control view and click the "Eksen" segment button
            placed = self.app.layout()
            screen.sync(placed)
            btn_idx = next(
                i for i, item in enumerate(placed)
                if isinstance(item.widget, Button) and item.widget.text == "Eksen"
            )
            btn_obj = screen.objects[btn_idx]
            cb, code, _ = btn_obj._callbacks[0]
            cb(fake_lv.event(btn_obj._pointer, recreate_wrapper=False))
            self.assertEqual(self.app.control_seg, "move")
            screen.close()
        finally:
            render_lvgl.lv = orig_lv

    def test_thumbnail_png_validation_rejects_non_png_and_corrupt_files(self):
        import kobra_screen.render_lvgl as render_lvgl
        fake_lv = StrictFakeLvgl()
        orig_lv = render_lvgl.lv
        render_lvgl.lv = fake_lv
        try:
            with tempfile.TemporaryDirectory() as tmp:
                # 1. Non-PNG bytes (e.g. JPEG) must be rejected by App.load_metadata
                self.printer.thumbnail = lambda: b"\xff\xd8\xff\xe0JFIF-not-a-png"
                app = App(self.app.client, cache_dir=tmp)
                app.load_metadata("kutu_08.gcode")
                self.assertNotIn("thumbnail", app.meta["kutu_08.gcode"])

                # 2. Valid 8-byte PNG header is accepted and cached
                self.printer.thumbnail = FakePrinter.thumbnail
                app.load_metadata("kutu_08.gcode")
                thumb_path = app.meta["kutu_08.gcode"].get("thumbnail")
                self.assertTrue(thumb_path and os.path.exists(thumb_path))

                # 3. FakeImage.set_src & _update_picture reject corrupt/non-PNG files
                corrupt_path = os.path.join(tmp, "corrupt.png")
                with open(corrupt_path, "wb") as f:
                    f.write(b"not-a-valid-png")
                img = fake_lv.image(fake_lv.obj(None))
                img._side = 64
                img._loaded_path = None
                img.set_src(corrupt_path)
                self.assertEqual(img.get_self_width(), 0)
                self.assertEqual(img.get_self_height(), 0)

                render_lvgl.LvglScreen._update_picture(img, corrupt_path)
                self.assertIsNone(img._loaded_path)

                render_lvgl.LvglScreen._update_picture(img, thumb_path)
                self.assertEqual(img._loaded_path, thumb_path)
                self.assertGreater(img.get_self_width(), 0)
        finally:
            render_lvgl.lv = orig_lv

    def test_process_matching_ignores_unrelated_processes(self):
        import kobra_screen.render_lvgl as render_lvgl
        with tempfile.TemporaryDirectory() as tmp:
            proc_root = os.path.join(tmp, "proc")
            processes = {
                "1001": ("tail", b"tail\x00-f\x00/userdata/app/gk/K3SysUi.log\x00"),
                "1002": ("grep", b"grep\x00K3SysUi\x00/var/log/messages\x00"),
                "1003": ("tail", b"tail\x00-f\x00/tmp/rinkhals/rinkhals-ui.py.log\x00"),
                "4242": ("K3SysUi", b"/userdata/app/gk/K3SysUi\x00"),
                "5555": ("python", b"python\x00/useremain/rinkhals/.current/opt/rinkhals/ui/rinkhals-ui.py\x00"),
                "6666": ("python3", b"python3\x00-m\x00kobra_screen\x00--host\x00127.0.0.1\x00"),
            }
            for pid, (comm, cmdline) in processes.items():
                pdir = os.path.join(proc_root, pid)
                os.makedirs(pdir)
                with open(os.path.join(pdir, "comm"), "w") as f:
                    f.write(comm + "\n")
                with open(os.path.join(pdir, "cmdline"), "wb") as f:
                    f.write(cmdline)

            self.assertEqual(render_lvgl._find_pids("K3SysUi", proc_root), [4242])
            self.assertEqual(render_lvgl._find_pids("rinkhals-ui.py", proc_root), [5555])
            self.assertEqual(render_lvgl._find_pids("kobra_screen", proc_root), [6666])

    def test_screen_ownership_and_backlight_wake(self):
        import kobra_screen.render_lvgl as render_lvgl
        fake_lv = StrictFakeLvgl()
        orig_lv = render_lvgl.lv
        render_lvgl.lv = fake_lv
        with tempfile.TemporaryDirectory() as tmp:
            proc_root = os.path.join(tmp, "proc")
            os.makedirs(os.path.join(proc_root, "4242"))
            with open(os.path.join(proc_root, "4242", "comm"), "w") as f:
                f.write("K3SysUi\n")
            with open(os.path.join(proc_root, "4242", "cmdline"), "wb") as f:
                f.write(b"/userdata/app/gk/K3SysUi\x00")

            pid_file = os.path.join(tmp, "kobra-screen.pid")
            rinkhals_pid = os.path.join(tmp, "rinkhals-ui.pid")
            with open(rinkhals_pid, "w") as f:
                f.write("9999\n")
            backlight = os.path.join(tmp, "brightness")
            with open(backlight, "w") as f:
                f.write("0\n")

            signals_sent = []
            orig_kill = render_lvgl._signal_pids
            render_lvgl._signal_pids = lambda pids, sig: signals_sent.append((list(pids), sig))
            try:
                screen = render_lvgl.LvglScreen(
                    manage_system=True,
                    pid_file=pid_file,
                    proc_root=proc_root,
                    backlight_path=backlight,
                )
                import signal
                self.assertIn(([4242], signal.SIGTERM), signals_sent)
                self.assertNotIn(([4242], signal.SIGSTOP), signals_sent)
                self.assertTrue(os.path.exists(pid_file))
                # Verify rinkhals-ui.pid is never clobbered or deleted by LvglScreen
                with open(rinkhals_pid) as f:
                    self.assertEqual(f.read().strip(), "9999")
                with open(backlight) as f:
                    self.assertEqual(f.read().strip(), "255")

                # Simulate rinkhals-ui.py starting externally -> LvglScreen yields touch device
                os.makedirs(os.path.join(proc_root, "5555"))
                with open(os.path.join(proc_root, "5555", "cmdline"), "wb") as f:
                    f.write(b"python\x00/useremain/rinkhals/.current/opt/rinkhals/ui/rinkhals-ui.py\x00")
                self.assertTrue(screen._check_rinkhals_ui_coexistence(self.app))
                self.assertFalse(fake_lv.grabbed)

                # Simulate rinkhals-ui.py exiting -> LvglScreen re-acquires touch and screen
                os.remove(os.path.join(proc_root, "5555", "cmdline"))
                os.rmdir(os.path.join(proc_root, "5555"))
                self.assertFalse(screen._check_rinkhals_ui_coexistence(self.app))
                self.assertTrue(fake_lv.grabbed)

                # User-dimmed backlight (e.g. 100) is NOT overwritten on touch, but off (0) IS woken to 255
                with open(backlight, "w") as f:
                    f.write("100\n")
                screen._last_touch -= 5.0
                screen._on_touch_activity(None)
                with open(backlight) as f:
                    self.assertEqual(f.read().strip(), "100")

                with open(backlight, "w") as f:
                    f.write("0\n")
                screen._last_touch -= 5.0
                screen._on_touch_activity(None)
                with open(backlight) as f:
                    self.assertEqual(f.read().strip(), "255")
                self.assertEqual(screen.touch.waited_release, 1)

                screen.close()
                self.assertFalse(any(sig == signal.SIGCONT for _, sig in signals_sent))
                self.assertFalse(os.path.exists(pid_file))
                self.assertTrue(os.path.exists(rinkhals_pid))
            finally:
                render_lvgl._signal_pids = orig_kill
                render_lvgl.lv = orig_lv

    def test_sigkill_on_screen_process_cleans_pid_and_hb_files(self):
        import signal
        import subprocess
        import sys
        import time

        with tempfile.TemporaryDirectory() as tmp:
            proc_root = os.path.join(tmp, "proc")
            os.makedirs(proc_root)
            pid_file = os.path.join(tmp, "kobra-screen.pid")
            hb_file = os.path.join(tmp, "kobra-screen.hb")

            # Spawn a real OS process to represent stock K3SysUi (stopped in open mode, never SIGSTOP)
            dummy_k3 = subprocess.Popen(["sleep", "60"])
            try:
                k3_dir = os.path.join(proc_root, str(dummy_k3.pid))
                os.makedirs(k3_dir)
                with open(os.path.join(k3_dir, "comm"), "w") as f:
                    f.write("K3SysUi\n")
                with open(os.path.join(k3_dir, "cmdline"), "wb") as f:
                    f.write(b"/userdata/app/gk/K3SysUi\x00")

                repo_dir = os.path.dirname(os.path.abspath(__file__))
                child_code = (
                    "import sys, time\n"
                    "sys.path.insert(0, %r)\n"
                    "import test_screen, kobra_screen.render_lvgl as rl\n"
                    "rl.lv = test_screen.StrictFakeLvgl()\n"
                    "scr = rl.LvglScreen(manage_system=True, pid_file=%r, proc_root=%r, heartbeat_file=%r)\n"
                    "sys.stdout.write('READY\\n')\n"
                    "sys.stdout.flush()\n"
                    "time.sleep(60)\n"
                ) % (repo_dir, pid_file, proc_root, hb_file)
                owner = subprocess.Popen(
                    [sys.executable, "-c", child_code],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    line = owner.stdout.readline().strip()
                    self.assertEqual(line, "READY")
                    # K3SysUi must be terminated (not running), and pid_file/hb_file must exist
                    dummy_k3.wait(timeout=2.0)
                    self.assertIsNotNone(dummy_k3.poll())
                    self.assertTrue(os.path.exists(pid_file))
                    self.assertTrue(os.path.exists(hb_file))

                    # Kill the screen owner process with SIGKILL (kill -9)
                    os.kill(owner.pid, signal.SIGKILL)
                    owner.wait(timeout=5)

                    # Pipe-EOF watchdog cleans up PID and heartbeat files automatically
                    deadline = time.monotonic() + 2.0
                    while time.monotonic() < deadline:
                        if not os.path.exists(pid_file) and not os.path.exists(hb_file):
                            break
                        time.sleep(0.02)

                    self.assertFalse(os.path.exists(pid_file), "PID file was not cleaned up after SIGKILL")
                    self.assertFalse(os.path.exists(hb_file), "HB file was not cleaned up after SIGKILL")
                finally:
                    if owner.poll() is None:
                        owner.kill()
                        owner.wait()
                    if owner.stdout is not None:
                        owner.stdout.close()
                    if owner.stderr is not None:
                        owner.stderr.close()
            finally:
                if dummy_k3.poll() is None:
                    dummy_k3.terminate()
                    dummy_k3.wait()

    def test_app_sh_start_stop_status_version_with_fake_tools(self):
        import subprocess
        import time

        app_sh = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.sh")
        with tempfile.TemporaryDirectory() as tmp:
            rinkhals_root = os.path.join(tmp, "rinkhals")
            rinkhals_home = os.path.join(tmp, "rinkhals_home")
            os.makedirs(os.path.join(rinkhals_root, "opt/rinkhals/ui"))
            os.makedirs(os.path.join(rinkhals_home, "apps", "kobra-screen"))
            os.makedirs(os.path.join(rinkhals_home, "apps", "vanilla-klipper"))
            proc_root = os.path.join(tmp, "proc")
            os.makedirs(proc_root)
            log_dir = os.path.join(tmp, "logs")
            os.makedirs(log_dir)
            pid_file = os.path.join(tmp, "kobra-screen.pid")
            env_dump = os.path.join(tmp, "env_dump.txt")
            stock_gk_dir = os.path.join(tmp, "stock_gk")
            os.makedirs(stock_gk_dir)
            k3_started_flag = os.path.join(tmp, "k3_restarted.flag")
            k3_bin = os.path.join(stock_gk_dir, "K3SysUi")
            with open(k3_bin, "w") as f:
                f.write("#!/bin/sh\n: > %r\n" % k3_started_flag)
            os.chmod(k3_bin, 0o755)

            tools_sh = os.path.join(rinkhals_root, "tools.sh")
            with open(tools_sh, "w") as f:
                f.write(
                    "APP_STATUS_STARTED='started'\n"
                    "APP_STATUS_STOPPED='stopped'\n"
                    "log() { echo \"$1\"; }\n"
                    "report_status() { echo \"Status: $1,$2,$3\"; }\n"
                    "get_app_property() { echo \"\"; }\n"
                )

            # Spawn real processes for K3SysUi and an unrelated `tail -f K3SysUi.log`
            dummy_k3 = subprocess.Popen(["sleep", "60"])
            dummy_tail = subprocess.Popen(["sleep", "60"])
            try:
                for pid, comm, cmd in (
                    (dummy_k3.pid, "K3SysUi", b"/userdata/app/gk/K3SysUi\x00"),
                    (dummy_tail.pid, "tail", b"tail\x00-f\x00/tmp/K3SysUi.log\x00"),
                ):
                    pdir = os.path.join(proc_root, str(pid))
                    os.makedirs(pdir)
                    with open(os.path.join(pdir, "comm"), "w") as f:
                        f.write(comm + "\n")
                    with open(os.path.join(pdir, "cmdline"), "wb") as f:
                        f.write(cmd)

                fake_python = os.path.join(tmp, "fake_python.sh")
                with open(fake_python, "w") as f:
                    f.write(
                        "#!/bin/sh\n"
                        "printf 'LD=%%s\\nPY=%%s\\n' \"$LD_LIBRARY_PATH\" \"$PYTHONPATH\" > %r\n"
                        "if [ \"$KOBRA_TEST_FAIL\" = \"1\" ]; then exit 1; fi\n"
                        "trap 'exit 0' TERM INT\n"
                        "while :; do sleep 0.1; done\n" % env_dump
                    )
                os.chmod(fake_python, 0o755)

                env = dict(os.environ)
                env.update({
                    "RINKHALS_ROOT": rinkhals_root,
                    "RINKHALS_HOME": rinkhals_home,
                    "RINKHALS_LOGS": log_dir,
                    "KOBRA_PID_FILE": pid_file,
                    "KOBRA_HEARTBEAT_FILE": os.path.join(tmp, "kobra-screen.hb"),
                    "KOBRA_STOP_MARKER": os.path.join(tmp, "kobra-screen.stopping"),
                    "KOBRA_TRIAL_MARKER": os.path.join(tmp, "kobra-screen.trial_expired"),
                    "KOBRA_SUPERVISOR_PID_FILE": os.path.join(tmp, "kobra-screen-sup.pid"),
                    "KOBRA_PROC_ROOT": proc_root,
                    "KOBRA_SYS_NET": os.path.join(tmp, "sys_net"),
                    "KOBRA_STOCK_GK_DIR": stock_gk_dir,
                    "KOBRA_PYTHON_BIN": fake_python,
                    "KOBRA_START_VERIFY_SEC": "0.15",
                    "KOBRA_STOP_WAIT_ITERS": "10",
                })
                env.pop("PYTHONPATH", None)
                env.pop("LD_LIBRARY_PATH", None)

                # 1. version
                res = subprocess.run(["sh", app_sh, "version"], env=env, capture_output=True, text=True, check=True)
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.json"), "r", encoding="utf-8") as f:
                    app_json_ver = json.load(f)["version"]
                self.assertEqual(res.stdout.strip(), app_json_ver)

                # 2. status (stopped)
                res = subprocess.run(["sh", app_sh, "status"], env=env, capture_output=True, text=True, check=True)
                self.assertIn("Status: stopped,,", res.stdout)

                # 3. start (success) -> K3SysUi is stopped completely (NOT SIGSTOP), tail untouched
                res = subprocess.run(["sh", app_sh, "start"], env=env, capture_output=True, text=True, check=True)
                dummy_k3.wait(timeout=2.0)
                self.assertIsNotNone(dummy_k3.poll())
                self.assertIsNone(dummy_tail.poll())

                with open(env_dump) as f:
                    env_text = f.read()
                self.assertIn("LD=/lib:/usr/lib:", env_text)
                self.assertFalse(env_text.strip().endswith(":"))

                # 4. status (started)
                res = subprocess.run(["sh", app_sh, "status"], env=env, capture_output=True, text=True, check=True)
                self.assertIn("Status: started,", res.stdout)

                # 5. stop (graceful SIGTERM, does not disable apps)
                res = subprocess.run(["sh", app_sh, "stop"], env=env, capture_output=True, text=True, check=True)
                res = subprocess.run(["sh", app_sh, "status"], env=env, capture_output=True, text=True, check=True)
                self.assertIn("Status: stopped,,", res.stdout)
                self.assertFalse(os.path.exists(os.path.join(rinkhals_home, "apps", "kobra-screen", ".disabled")))

                # 6. start when process crashes immediately -> returns 0 (supervisor launched), then supervisor
                # detects crash and disables open mode apps, restarts K3SysUi
                fail_env = dict(env, KOBRA_TEST_FAIL="1")
                res = subprocess.run(["sh", app_sh, "start"], env=fail_env, capture_output=True, text=True)
                # start now returns 0 immediately (supervisor launched)
                self.assertEqual(res.returncode, 0)
                # Give the supervisor time to detect the crash and trigger fallback
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    if (os.path.exists(os.path.join(rinkhals_home, "apps", "kobra-screen", ".disabled")) and
                        os.path.exists(k3_started_flag)):
                        break
                    time.sleep(0.05)
                self.assertTrue(os.path.exists(os.path.join(rinkhals_home, "apps", "kobra-screen", ".disabled")))
                self.assertTrue(os.path.exists(os.path.join(rinkhals_home, "apps", "vanilla-klipper", ".disabled")))
                self.assertTrue(os.path.exists(k3_started_flag))
            finally:
                for p in (dummy_k3, dummy_tail):
                    if p.poll() is None:
                        p.terminate()
                        p.wait()

    def test_watchdog_respawn_and_k3sysui_restart_stop(self):
        import subprocess
        from kobra_screen import render_lvgl

        with tempfile.TemporaryDirectory() as tmp:
            proc_root = os.path.join(tmp, "proc")
            os.makedirs(proc_root)
            pid_file = os.path.join(tmp, "kobra-screen.pid")
            k3_old = subprocess.Popen(["sleep", "60"])
            k3_new = subprocess.Popen(["sleep", "60"])
            fake_lv = StrictFakeLvgl()
            orig_lv = render_lvgl.lv
            try:
                pdir = os.path.join(proc_root, str(k3_old.pid))
                os.makedirs(pdir)
                with open(os.path.join(pdir, "comm"), "w") as f:
                    f.write("K3SysUi\n")
                with open(os.path.join(pdir, "cmdline"), "wb") as f:
                    f.write(b"/userdata/app/gk/K3SysUi\x00")
                with open(os.path.join(pdir, "status"), "w") as f:
                    f.write("Name:\tK3SysUi\nState:\tS (sleeping)\n")

                render_lvgl.lv = fake_lv
                screen = render_lvgl.LvglScreen(manage_system=True, pid_file=pid_file, proc_root=proc_root)
                try:
                    k3_old.wait(timeout=2.0)
                    self.assertIsNotNone(k3_old.poll())

                    # 1. Watchdog dies -> _ensure_watchdog() respawns it
                    old_wd = screen._watchdog_proc
                    self.assertIsNotNone(old_wd)
                    old_wd.kill()
                    old_wd.wait(timeout=3)
                    screen._check_rinkhals_ui_coexistence(self.app)
                    self.assertIsNotNone(screen._watchdog_proc)
                    self.assertIsNone(screen._watchdog_proc.poll())
                    self.assertNotEqual(screen._watchdog_proc.pid, old_wd.pid)

                    # 2. K3SysUi restarts with a new PID -> periodic coexistence check stops it cleanly
                    pdir2 = os.path.join(proc_root, str(k3_new.pid))
                    os.makedirs(pdir2)
                    with open(os.path.join(pdir2, "comm"), "w") as f:
                        f.write("K3SysUi\n")
                    with open(os.path.join(pdir2, "cmdline"), "wb") as f:
                        f.write(b"/userdata/app/gk/K3SysUi\x00")
                    with open(os.path.join(pdir2, "status"), "w") as f:
                        f.write("Name:\tK3SysUi\nState:\tS (sleeping)\n")
                    screen._check_rinkhals_ui_coexistence(self.app)
                    k3_new.wait(timeout=2.0)
                    self.assertIsNotNone(k3_new.poll())
                finally:
                    screen.close()
            finally:
                render_lvgl.lv = orig_lv
                for p in (k3_old, k3_new):
                    if p.poll() is None:
                        p.terminate()
                        p.wait()

    def test_multithreaded_app_race_safety(self):
        import threading

        with tempfile.TemporaryDirectory() as tmp:
            app = App(self.app.client, run_async=lambda fn: threading.Thread(target=fn, daemon=True).start(), cache_dir=tmp)
            app.refresh()
            errors = []
            stop_flag = threading.Event()

            def _poller():
                try:
                    for _ in range(40):
                        if stop_flag.is_set():
                            break
                        app.refresh()
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

            def _meta_loader():
                try:
                    names = [f["path"] for f in self.printer.files]
                    for i in range(40):
                        if stop_flag.is_set():
                            break
                        app.load_metadata(names[i % len(names)])
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

            def _navigator():
                try:
                    tabs = [t for _, t, _ in App.TABS]
                    for i in range(80):
                        if stop_flag.is_set():
                            break
                        app.select_tab(tabs[i % len(tabs)])
                        app.open("files")
                        app.back()
                        app.notify("Test %d" % i, 1)
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

            def _renderer():
                try:
                    for _ in range(100):
                        if stop_flag.is_set():
                            break
                        placed = app.layout()
                        if not placed:
                            raise AssertionError("empty layout")
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

            threads = [
                threading.Thread(target=_poller),
                threading.Thread(target=_poller),
                threading.Thread(target=_meta_loader),
                threading.Thread(target=_navigator),
                threading.Thread(target=_renderer),
                threading.Thread(target=_renderer),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
            stop_flag.set()
            self.assertEqual(errors, [])

    def test_return_to_stock_from_settings_and_offline_disables_rinkhals_apps_and_reboots(self):
        with tempfile.TemporaryDirectory() as tmp:
            rinkhals_home = os.path.join(tmp, "rinkhals_home")
            app_dir = os.path.join(rinkhals_home, "apps", "kobra-screen")
            vk_dir = os.path.join(rinkhals_home, "apps", "vanilla-klipper")
            os.makedirs(app_dir)
            os.makedirs(vk_dir)
            with open(os.path.join(app_dir, ".enabled"), "w") as f:
                f.write("")
            with open(os.path.join(rinkhals_home, "apps", "kobra-screen.enabled"), "w") as f:
                f.write("")
            with open(os.path.join(vk_dir, ".enabled"), "w") as f:
                f.write("")
            with open(os.path.join(rinkhals_home, "apps", "vanilla-klipper.enabled"), "w") as f:
                f.write("")

            stopped = []
            app = App(
                self.app.client,
                cache_dir=self.tmp.name,
                on_return_to_stock=lambda: stopped.append(True),
                rinkhals_home=rinkhals_home,
                wifi=self.wifi,
            )
            app.refresh()
            self.assertTrue(app.press("Ayar"))
            app.refresh()
            self.assertTrue(app.press("Sistem"))
            self.assertTrue(app.press("Stok moda dön"))
            # Must require confirmation first
            self.assertFalse(stopped)
            self.assertFalse(self.printer.rebooted)
            self.assertFalse(os.path.exists(os.path.join(app_dir, ".disabled")))
            self.assertFalse(os.path.exists(os.path.join(vk_dir, ".disabled")))

            self.assertTrue(app.press("Evet"))
            self.assertEqual(stopped, [True])
            self.assertTrue(self.printer.rebooted)
            self.assertTrue(app.stock_requested)
            self.assertTrue(os.path.exists(os.path.join(app_dir, ".disabled")))
            self.assertFalse(os.path.exists(os.path.join(app_dir, ".enabled")))
            self.assertFalse(os.path.exists(os.path.join(rinkhals_home, "apps", "kobra-screen.enabled")))
            self.assertTrue(os.path.exists(os.path.join(vk_dir, ".disabled")))
            self.assertFalse(os.path.exists(os.path.join(vk_dir, ".enabled")))
            self.assertFalse(os.path.exists(os.path.join(rinkhals_home, "apps", "vanilla-klipper.enabled")))

            # Also accessible on offline disconnected screen (with system reboot fallback when Moonraker is down)
            self.httpd.shutdown()
            self.httpd.server_close()
            app.refresh()
            placed = app.layout()
            self.assertLessEqual(app.content_bottom, app.content_limit)
            self.assertTrue(any(isinstance(i.widget, Button) and i.widget.text == "Stok moda dön" and i.widget.action for i in placed))

    def test_moonraker_error_clears_settings_cache(self):
        self.assertTrue(bool(self.app.state.settings))
        self.httpd.shutdown()
        self.httpd.server_close()
        self.app.refresh()
        self.assertFalse(self.app.state.connected)
        self.assertEqual(self.app.state.settings, {})

    def test_hang_watchdog_kills_stalled_loop_and_cleans_files(self):
        import subprocess
        import sys
        import time

        with tempfile.TemporaryDirectory() as tmp:
            proc_root = os.path.join(tmp, "proc")
            os.makedirs(proc_root)
            pid_file = os.path.join(tmp, "kobra-screen.pid")
            hb_file = os.path.join(tmp, "kobra-screen.hb")

            repo_dir = os.path.dirname(os.path.abspath(__file__))
            child_code = (
                "import sys, time\n"
                "sys.path.insert(0, %r)\n"
                "import test_screen, kobra_screen.render_lvgl as rl\n"
                "rl.lv = test_screen.StrictFakeLvgl()\n"
                "scr = rl.LvglScreen(manage_system=True, pid_file=%r, proc_root=%r, "
                "heartbeat_file=%r, hang_timeout=0.35, hb_poll=0.05)\n"
                "class HungApp:\n"
                "    dirty = True\n"
                "    def layout(self):\n"
                "        sys.stdout.write('HUNG\\n')\n"
                "        sys.stdout.flush()\n"
                "        time.sleep(60)\n"
                "        return []\n"
                "scr.run(HungApp())\n"
            ) % (repo_dir, pid_file, proc_root, hb_file)
            owner = subprocess.Popen(
                [sys.executable, "-c", child_code],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                line = owner.stdout.readline().strip()
                self.assertEqual(line, "HUNG")
                self.assertTrue(os.path.exists(pid_file))

                owner.wait(timeout=5.0)
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if not os.path.exists(pid_file) and not os.path.exists(hb_file):
                        break
                    time.sleep(0.02)

                self.assertFalse(os.path.exists(pid_file))
                self.assertFalse(os.path.exists(hb_file))
            finally:
                if owner.poll() is None:
                    owner.kill()
                    owner.wait()
                owner.stdout.close()
                owner.stderr.close()

    def test_app_sh_trial_timer_and_disable(self):
        import subprocess
        import time

        app_sh = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.sh")
        with tempfile.TemporaryDirectory() as tmp:
            rinkhals_root = os.path.join(tmp, "rinkhals")
            rinkhals_home = os.path.join(tmp, "rinkhals_home")
            os.makedirs(os.path.join(rinkhals_root, "opt/rinkhals/ui"))
            os.makedirs(os.path.join(rinkhals_home, "apps"))
            proc_root = os.path.join(tmp, "proc")
            os.makedirs(proc_root)
            log_dir = os.path.join(tmp, "logs")
            os.makedirs(log_dir)
            pid_file = os.path.join(tmp, "kobra-screen.pid")
            hb_file = os.path.join(tmp, "kobra-screen.hb")
            stock_gk_dir = os.path.join(tmp, "stock_gk")
            os.makedirs(stock_gk_dir)
            k3_restored_flag = os.path.join(tmp, "k3_restored.flag")
            k3_bin = os.path.join(stock_gk_dir, "K3SysUi")
            with open(k3_bin, "w") as f:
                f.write("#!/bin/sh\n: > %r\n" % k3_restored_flag)
            os.chmod(k3_bin, 0o755)

            with open(os.path.join(rinkhals_root, "tools.sh"), "w") as f:
                f.write(
                    "APP_STATUS_STARTED='started'\n"
                    "APP_STATUS_STOPPED='stopped'\n"
                    "log() { echo \"$1\"; }\n"
                    "report_status() { echo \"Status: $1,$2,$3\"; }\n"
                    "get_app_property() { echo \"\"; }\n"
                )

            dummy_k3 = subprocess.Popen(["sleep", "60"])
            try:
                pdir = os.path.join(proc_root, str(dummy_k3.pid))
                os.makedirs(pdir)
                with open(os.path.join(pdir, "comm"), "w") as f:
                    f.write("K3SysUi\n")
                with open(os.path.join(pdir, "cmdline"), "wb") as f:
                    f.write(b"/userdata/app/gk/K3SysUi\x00")

                fake_python = os.path.join(tmp, "fake_python.sh")
                with open(fake_python, "w") as f:
                    f.write(
                        "#!/bin/sh\n"
                        "trap 'exit 0' TERM INT\n"
                        "while :; do sleep 0.1; done\n"
                    )
                os.chmod(fake_python, 0o755)

                env = dict(os.environ)
                env.update({
                    "RINKHALS_ROOT": rinkhals_root,
                    "RINKHALS_HOME": rinkhals_home,
                    "RINKHALS_LOGS": log_dir,
                    "KOBRA_PID_FILE": pid_file,
                    "KOBRA_HEARTBEAT_FILE": hb_file,
                    "KOBRA_STOP_MARKER": os.path.join(tmp, "kobra-screen.stopping"),
                    "KOBRA_TRIAL_MARKER": os.path.join(tmp, "kobra-screen.trial_expired"),
                    "KOBRA_SUPERVISOR_PID_FILE": os.path.join(tmp, "kobra-screen-sup.pid"),
                    "KOBRA_PROC_ROOT": proc_root,
                    "KOBRA_SYS_NET": os.path.join(tmp, "sys_net"),
                    "KOBRA_STOCK_GK_DIR": stock_gk_dir,
                    "KOBRA_PYTHON_BIN": fake_python,
                    "KOBRA_START_VERIFY_SEC": "0.1",
                    "KOBRA_STOP_WAIT_ITERS": "10",
                })

                # Invalid trial duration rejected
                bad = subprocess.run(["sh", app_sh, "trial", "abc"], env=env, capture_output=True, text=True)
                self.assertNotEqual(bad.returncode, 0)

                # Trial mode stops K3SysUi, stops kobra-screen on expiry, and restarts K3SysUi
                subprocess.run(["sh", app_sh, "trial", "0.4"], env=env, capture_output=True, text=True, check=True)
                dummy_k3.wait(timeout=2.0)
                self.assertIsNotNone(dummy_k3.poll())

                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    if os.path.exists(k3_restored_flag) and not os.path.exists(pid_file):
                        break
                    time.sleep(0.05)

                self.assertTrue(os.path.exists(k3_restored_flag))
                self.assertFalse(os.path.exists(pid_file))

                # app.sh disable writes Rinkhals disable marker
                subprocess.run(["sh", app_sh, "disable"], env=env, capture_output=True, text=True, check=True)
                self.assertTrue(os.path.exists(os.path.join(rinkhals_home, "apps", "kobra-screen.disabled")))
            finally:
                if dummy_k3.poll() is None:
                    dummy_k3.terminate()
                    dummy_k3.wait()

    def test_idle_refresh_and_sync_skip_redundant_work(self):
        import time
        import kobra_screen.render_lvgl as render_lvgl

        fake_lv = StrictFakeLvgl()
        orig_lv = render_lvgl.lv
        render_lvgl.lv = fake_lv
        try:
            screen = render_lvgl.LvglScreen(manage_system=False)
            self.app.refresh()
            placed = self.app.layout()
            self.app.dirty = False

            # Idle moonraker polls with +-0.12 C ADC noise must NOT mark app dirty
            for i in range(10):
                delta = 0.12 if i % 2 == 0 else -0.08
                self.printer.status["extruder"]["temperature"] = 24.0 + delta
                self.printer.status["heater_bed"]["temperature"] = 23.0 + delta
                self.app.refresh()
                self.assertFalse(self.app.dirty)

            # _sync_temperature_store outside heat view must NOT mark app dirty
            self.app._sync_temperature_store()
            self.assertFalse(self.app.dirty)

            update_calls = []
            orig_update = screen._update
            screen._update = lambda obj, w: (update_calls.append(type(w).__name__), orig_update(obj, w))

            screen.sync(placed)
            initial_updates = len(update_calls)
            self.assertEqual(initial_updates, len(placed))

            # Duplicate sync with identical placed widgets must not touch LVGL objects
            update_calls.clear()
            screen.sync(placed)
            self.assertEqual(update_calls, [])

            # Meaningful 1 C temperature change sets dirty=True and updates only changed widget(s)
            self.printer.status["extruder"]["temperature"] = 28.0
            self.app.refresh()
            self.assertTrue(self.app.dirty)
            placed2 = self.app.layout()
            self.app.dirty = False
            update_calls.clear()
            screen.sync(placed2)
            self.assertGreater(len(update_calls), 0)
            self.assertLessEqual(len(update_calls), 2)

            # Toast notification expires via tick_timers() without needing periodic full layout
            self.app.notify("Kaydedildi", seconds=0.05)
            self.app.dirty = False
            self.app.message_until = time.monotonic() - 0.01
            self.assertTrue(self.app.tick_timers())
            self.assertTrue(self.app.dirty)
            self.assertEqual(self.app.message, "")
            screen.close()
        finally:
            render_lvgl.lv = orig_lv

    def test_adaptive_loop_sleep_and_fast_read_timer(self):
        import time
        import kobra_screen.render_lvgl as render_lvgl

        fake_lv = StrictFakeLvgl()
        orig_lv = render_lvgl.lv
        render_lvgl.lv = fake_lv
        try:
            screen = render_lvgl.LvglScreen(manage_system=False)
            self.assertEqual(screen._indev_period_ms, 10)

            class _AppStub:
                dirty = False

            app_stub = _AppStub()
            now = time.monotonic()
            screen._last_touch = now - 5.0

            # Idle: respects LVGL timer_handler next_ms within [10ms, 50ms]
            # next_timer_ms <= indev_period_ms (10ms) is treated as touch timer -> use IDLE_LOOP_SLEEP_SEC (35ms)
            self.assertAlmostEqual(screen._compute_loop_sleep(now, app_stub, 10, False), render_lvgl.IDLE_LOOP_SLEEP_SEC)
            self.assertAlmostEqual(screen._compute_loop_sleep(now, app_stub, 33, False), 0.033)
            self.assertAlmostEqual(screen._compute_loop_sleep(now, app_stub, 500, False), 0.050)
            # Idle fallback when timer_handler returns 0 or None (fake LVGL)
            self.assertAlmostEqual(
                screen._compute_loop_sleep(now, app_stub, 0, False),
                render_lvgl.IDLE_LOOP_SLEEP_SEC,
            )
            self.assertAlmostEqual(
                screen._compute_loop_sleep(now, app_stub, None, False),
                render_lvgl.IDLE_LOOP_SLEEP_SEC,
            )

            # Fast 5ms loop when touch is active, recently touched, or app is dirty
            self.assertAlmostEqual(
                screen._compute_loop_sleep(now, app_stub, 33, True),
                render_lvgl.MAIN_LOOP_SLEEP_SEC,
            )
            app_stub.dirty = True
            self.assertAlmostEqual(
                screen._compute_loop_sleep(now, app_stub, 33, False),
                render_lvgl.MAIN_LOOP_SLEEP_SEC,
            )
            app_stub.dirty = False
            screen._last_touch = now - 0.1
            self.assertAlmostEqual(
                screen._compute_loop_sleep(now, app_stub, 33, False),
                render_lvgl.MAIN_LOOP_SLEEP_SEC,
            )
            screen.close()
        finally:
            render_lvgl.lv = orig_lv

    def test_idle_10s_simulated_loop_bounds(self):
        import kobra_screen.render_lvgl as render_lvgl

        fake_lv = StrictFakeLvgl()
        orig_lv = render_lvgl.lv
        render_lvgl.lv = fake_lv
        clock = [1000.0]
        loop_iters = [0]
        next_poll = [1001.0]
        poll_i = [0]

        orig_dispatch = fake_lv._dispatch_top_fn

        def wrapped_dispatch(name, args):
            if name == "timer_handler":
                loop_iters[0] += 1
            return orig_dispatch(name, args)

        object.__setattr__(fake_lv, "_dispatch_top_fn", wrapped_dispatch)

        try:
            with mock.patch("kobra_screen.render_lvgl.time.monotonic", side_effect=lambda: clock[0]):
                screen = render_lvgl.LvglScreen(manage_system=False)
                screen._last_touch = clock[0] - 5.0

                layout_calls = [0]
                sync_calls = [0]
                orig_layout = self.app.layout
                orig_sync = screen.sync
                self.app.layout = lambda: (layout_calls.__setitem__(0, layout_calls[0] + 1), orig_layout())[1]
                screen.sync = lambda p: (sync_calls.__setitem__(0, sync_calls[0] + 1), orig_sync(p))[1]

                def fake_sleep(dt):
                    clock[0] += dt
                    while clock[0] >= next_poll[0] and clock[0] < 1010.0:
                        next_poll[0] += 1.0
                        poll_i[0] += 1
                        delta = 0.1 if (poll_i[0] % 2 == 0) else -0.1
                        self.printer.status["extruder"]["temperature"] = 24.0 + delta
                        self.app.refresh()
                    if clock[0] >= 1010.0:
                        if poll_i[0] == 9:
                            # After 10s idle, simulate a real state change and verify immediate update
                            poll_i[0] += 1
                            self.printer.status["extruder"]["temperature"] = 45.0
                            self.app.refresh()
                        else:
                            screen.request_stop()

                with mock.patch("kobra_screen.render_lvgl.time.sleep", side_effect=fake_sleep):
                    screen.run(self.app)

                self.assertEqual(layout_calls[0], 2)  # 1 initial + 1 on real temp change at t=10s
                self.assertEqual(sync_calls[0], 2)
                self.assertLessEqual(loop_iters[0], 350)
                screen.close()
        finally:
            render_lvgl.lv = orig_lv


class ScreenTaskFixesTests(unittest.TestCase):
    def setUp(self):
        self.printer = FakePrinter()
        self.httpd = serve(self.printer, simulate=False)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_event_source_label_ace_err15(self):
        """1) _event_source_label('ace') == 'ACE' (ERR-15)."""
        from kobra_screen.screens import _event_source_label
        self.assertEqual(_event_source_label("ace"), "ACE")
        self.assertEqual(_event_source_label("ace", "tr"), "ACE")
        self.assertEqual(_event_source_label("ace", "en"), "ACE")

    def test_updates_page_ace_driver_version_row_and_format(self):
        """2) Guncellemeler sayfasinda ace_driver_version satirinin varligi ve formatı."""
        version_dir = os.path.join(self.tmp.name, "open-kobra")
        os.makedirs(version_dir, exist_ok=True)
        version_file = os.path.join(version_dir, "ace-driver.version")
        with open(version_file, "w", encoding="utf-8") as f:
            f.write("0.2.2\n")

        app = App(
            Moonraker("127.0.0.1", self.httpd.server_address[1]),
            wifi=FakeWifiManager(),
            rinkhals_home=self.tmp.name,
        )
        app.refresh()
        app.open("updates")
        items = app.layout()
        labels = [i.widget for i in items if isinstance(i.widget, Label)]
        ace_label = next((l for l in labels if l.text == "ace-driver"), None)
        self.assertIsNotNone(ace_label, "ace-driver etiketi bulunamadı")
        self.assertEqual(ace_label.size, "small")
        self.assertEqual(ace_label.color, "muted")

        ver_label = next((l for l in labels if l.text == "0.2.2"), None)
        self.assertIsNotNone(ver_label, "ace-driver sürüm değeri bulunamadı")
        self.assertEqual(ver_label.size, "small")
        self.assertEqual(ver_label.align, "right")

    def test_drying_presets_correct_values_and_not_clipped(self):
        """3) Kurutma onayari listesi dogru degerlerle donuyor ve max_dryer_temperature uzeri kirpilmiyor."""
        from kobra_screen import screens, limits
        self.assertEqual([name for name, _, _ in screens.DRY_PRESETS],
                         ["PLA", "PETG", "ABS/ASA", "PA/PC", "TPU"])
        preset_map = {name: (temp, minutes) for name, temp, minutes in screens.DRY_PRESETS}
        # PLA: 45°C 4h (240 min)
        self.assertEqual(preset_map["PLA"], (45, 240))
        # PETG: 55°C 6h (360 min)
        self.assertEqual(preset_map["PETG"], (55, 360))
        # ABS/ASA: 60°C 6h (360 min)
        self.assertEqual(preset_map["ABS/ASA"], (60, 360))
        # PA/PC: 70°C 8h (480 min)
        self.assertEqual(preset_map["PA/PC"], (70, 480))
        # TPU: 50°C 6h (360 min)
        self.assertEqual(preset_map["TPU"], (50, 360))

        # 70°C exceeds max_dryer_temperature (default 60°C), but DRY_PRESETS list values are not clipped
        self.assertGreater(preset_map["PA/PC"][0], 60)

        # Clamping helper limits properly when requested
        self.assertEqual(screens.clamp_dryer_temperature(70, 60), 60)
        self.assertEqual(limits.clamp_dryer_temperature(70, 60), 60)
        self.assertEqual(screens.clamp_dryer_temperature(55, 60), 55)

    def test_preheat_presets_contain_asa_and_tpu(self):
        """4) On isitma onayarinda ASA ve TPU var."""
        from kobra_screen import screens
        names = [p[0] for p in screens.PRESETS]
        self.assertIn("ASA", names)
        self.assertIn("TPU", names)
        preset_map = {name: (nozzle, bed) for name, nozzle, bed in screens.PRESETS}
        self.assertEqual(preset_map["ASA"], (260, 110))
        self.assertEqual(preset_map["TPU"], (220, 60))


if __name__ == "__main__":
    unittest.main()
