"""ACE Pro unit tests: drying duration units, driver details, and layout bounds."""

import tempfile
import unittest

from fake_moonraker import FakePrinter, serve
from kobra_screen import render_pil
from kobra_screen.app import App
from kobra_screen.moonraker import Moonraker
from kobra_screen.render_pil import SCALE, font, render, text_line_height, text_width, wrap
from kobra_screen.widgets import (Bar, Button, Crosshair, HEIGHT, Label, Panel, WIDTH, button_parts,
                                  button_sub_text_size, button_text_size, label_unit_size)


class AceScreenTests(unittest.TestCase):
    def setUp(self):
        self.printer = FakePrinter()
        self.httpd = serve(self.printer, simulate=False)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = App(Moonraker("127.0.0.1", self.httpd.server_address[1]), cache_dir=self.tmp.name)
        self.app.refresh()

    def press(self, *texts, icon=None):
        for text in texts:
            self.assertTrue(self.app.press(text, icon), "basılabilir düğme yok: %r" % text)
        self.app.refresh()

    def texts(self):
        return [item.widget.text for item in self.app.layout() if hasattr(item.widget, "text")]

    def ticks(self, count):
        for _ in range(count):
            self.printer.tick()
        self.app.refresh()

    def test_dryer_units_preset_and_custom_minutes_vs_seconds(self):
        # 1. Preset PLA (45° 4sa) -> DURATION=240 (min), remain_time=14400 (sec) -> "Kalan 4sa 00dk" (NOT 240sa!)
        self.press("Makara")
        self.press("PLA", icon="thermo")
        self.assertEqual(self.printer.gcodes[-1], "ACE_START_DRYING TEMP=45 DURATION=240")
        dry = self.printer.status["ace_instance_0"]["dryer_status"]
        self.assertEqual(dry["duration"], 240)
        self.assertEqual(dry["remain_time"], 14400)
        texts = self.texts()
        self.assertIn("Kalan 4sa 00dk", texts)
        self.assertIn("7000 rpm", texts)
        self.assertFalse(any("240sa" in t for t in texts))

        # 2. Stop and start custom 40 minutes -> DURATION=40, remain_time=2400 -> "Kalan 40dk" (NOT 40sa!)
        self.press("Durdur")
        self.assertEqual(self.printer.status["ace_instance_0"]["dryer_status"]["status"], "stop")
        self.press("Özel", "4", "5", "Tamam", "4", "0", "Tamam")
        self.assertEqual(self.printer.gcodes[-1], "ACE_START_DRYING TEMP=45 DURATION=40")
        dry = self.printer.status["ace_instance_0"]["dryer_status"]
        self.assertEqual(dry["duration"], 40)
        self.assertEqual(dry["remain_time"], 2400)
        texts = self.texts()
        self.assertIn("Kalan 40dk", texts)
        self.assertFalse(any("40sa" in t for t in texts))

        # 3. Progress bar starts at 0.0 and advances cleanly as ticks run
        bar = next(i.widget for i in self.app.layout() if isinstance(i.widget, Bar))
        self.assertAlmostEqual(bar.fraction, 0.0, places=2)
        self.ticks(10)  # 10 simulated minutes elapsed -> 30 min (1800 s) left
        self.assertIn("Kalan 30dk", self.texts())
        bar = next(i.widget for i in self.app.layout() if isinstance(i.widget, Bar))
        self.assertAlmostEqual(bar.fraction, 0.25, places=2)

        # 4. Remaining time reaching 0 stops the dryer
        self.ticks(30)
        self.assertIn("Kurutma kapalı", self.texts())

    def test_ace_detail_pages_hardware_features_and_ops(self):
        self.printer.status["ace_state"]["toolhead_sensor"] = True
        self.app.refresh()
        self.press("Makara", "ACE Pro ayarları")

        # Status segment
        texts = self.texts()
        self.assertIn("Anycubic Color Engine Pro", texts)
        self.assertIn("V1.3.863 (V1.0.1)", texts)
        self.assertIn("bağlı · ace1_json", texts)
        self.assertIn("/dev/ttyACM0", texts)
        self.assertIn("dolu", texts)  # toolhead sensor
        self.assertIn("Yuva 2 (0)", texts)  # feed_assist_slot=1 -> Yuva 2
        self.press("Yenile")
        self.assertEqual(self.printer.gcodes[-1], "ACE_GET_STATUS VERBOSE=1")
        self.press("Yeniden bağlan")
        self.assertEqual(self.printer.gcodes[-1], "ACE_RECONNECT")

        # Features segment
        self.press("Özellik")
        self.assertIn("60 / 50 mm/s", self.texts())
        self.assertIn("2600 / 3000 mm", self.texts())
        # Toggle RFID sync (initially True -> "açık")
        self.press("açık")
        self.assertEqual(self.printer.gcodes[-1], "ACE_DISABLE_RFID_SYNC")
        self.assertFalse(self.printer.status["ace_instance_0"]["rfid_sync_enabled"])
        # Toggle Endless spool (now both toggles are "kapalı"; press second one for Endless spool)
        endless_btn = next(
            it for it in self.app.layout()
            if getattr(it.widget, "text", None) == "kapalı"
            and any(getattr(lb.widget, "text", None) == "Sonsuz makara" and abs(lb.y - it.y) < 15
                    for lb in self.app.layout())
        )
        self.assertTrue(self.app.tap(endless_btn.x + endless_btn.w // 2, endless_btn.y + endless_btn.h // 2))
        self.app.refresh()
        self.assertEqual(self.printer.gcodes[-1], "ACE_ENABLE_ENDLESS_SPOOL")
        self.assertTrue(self.printer.status["ace_state"]["endless_spool_enabled"])
        # Change endless spool mode
        self.press("Malzeme")
        self.assertEqual(self.printer.gcodes[-1], "ACE_SET_ENDLESS_SPOOL_MODE MODE=material")
        # Jump directly to ACE limits page
        self.press("ACE sınırlarını düzenle")
        self.assertIn("ACE Isı ve Besleme", self.texts())
        self.press("", icon="back")

        # Ops segment
        self.press("İşlem")
        self.press("Akıllı yükle")
        self.assertEqual(self.printer.gcodes[-1], "ACE_SMART_LOAD")
        self.press("Akıllı çıkar")
        self.assertEqual(self.printer.gcodes[-1], "ACE_SMART_UNLOAD")
        self.press("Assist kapat")
        self.assertEqual(self.printer.gcodes[-1], "ACE_DISABLE_FEED_ASSIST INSTANCE=0 INDEX=1")
        self.press("Tümünü çıkar", "Evet")
        self.assertEqual(self.printer.gcodes[-1], "ACE_FULL_UNLOAD TOOL=ALL")
        self.press("Kaydet")
        self.assertEqual(self.printer.gcodes[-1], "ACE_SAVE_INVENTORY")
        self.press("Aracı sıfırla", "Evet")
        self.assertEqual(self.printer.gcodes[-1], "ACE_RESET_ACTIVE_TOOLHEAD")
        self.press("Diske yaz")
        self.assertEqual(self.printer.gcodes[-1], "ACE_FLUSH")

    def test_slot_detail_rfid_feed_retract_and_assist(self):
        self.press("Makara", "PLA", "Ayrıntı")
        texts = self.texts()
        self.assertIn("hazır", texts)
        self.assertIn("okundu · Anycubic (AHPLWH-101)", texts)
        self.assertIn("Isı (N/T)", texts)
        self.assertIn("190-230° / 50-60°", texts)
        self.assertIn("1.75 mm · 820/1000", texts)

        # Feed / Retract / Stop
        self.press("Besle")
        self.assertEqual(self.printer.gcodes[-1], "ACE_FEED T=0 LENGTH=50")
        self.press("Özel", "1", "2", "0", "Tamam")
        self.press("Besle")
        self.assertEqual(self.printer.gcodes[-1], "ACE_FEED T=0 LENGTH=120")
        self.press("50 mm", "Geri çek")
        self.assertEqual(self.printer.gcodes[-1], "ACE_RETRACT T=0 LENGTH=50")
        self.press("Besleme dur")
        self.assertEqual(self.printer.gcodes[-1], "ACE_STOP_FEED T=0")
        self.press("Çekme dur")
        self.assertEqual(self.printer.gcodes[-1], "ACE_STOP_RETRACT T=0")

        # Feed assist toggle & speeds
        self.press("kapalı")
        self.assertEqual(self.printer.gcodes[-1], "ACE_ENABLE_FEED_ASSIST T=0")
        self.assertEqual(self.printer.status["ace_instance_0"]["feed_assist_slot"], 0)
        self.press("açık")
        self.assertEqual(self.printer.gcodes[-1], "ACE_DISABLE_FEED_ASSIST T=0")
        self.assertEqual(self.printer.status["ace_instance_0"]["feed_assist_slot"], -1)
        self.press("Sürme hızı", "7", "5", "Tamam")
        self.assertEqual(self.printer.gcodes[-1], "ACE_SET_FEED_SPEED T=0 SPEED=75")
        self.press("Çekme hızı", "4", "5", "Tamam")
        self.assertEqual(self.printer.gcodes[-1], "ACE_SET_RETRACT_SPEED T=0 SPEED=45")

    @unittest.skipUnless(render_pil.ui_font_available(), "needs AlibabaSans-Regular.ttf (kobra-screen/fonts or KOBRA_SCREEN_FONT)")
    def test_new_ace_views_fit_and_not_clipped(self):
        views = []
        self.press("Makara")
        views.append(("filament_idle", self.app.layout()))
        self.press("PLA", "Ayrıntı")
        views.append(("slot_detail_rfid", self.app.layout()))
        self.press("", icon="back")
        self.press("", icon="back")
        self.press("PETG", "Ayrıntı")
        views.append(("slot_detail_plain", self.app.layout()))
        self.press("", icon="back")
        self.press("", icon="back")
        self.press("ACE Pro ayarları")
        views.append(("ace_status", self.app.layout()))
        self.press("Özellik")
        views.append(("ace_features", self.app.layout()))
        self.press("İşlem")
        views.append(("ace_ops", self.app.layout()))

        from PIL import Image, ImageDraw
        draw = ImageDraw.Draw(Image.new("L", (1, 1)))
        for name, placed in views:
            self.assertLessEqual(self.app.content_bottom, self.app.content_limit, "%s taşıyor" % name)
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
                        self.assertLessEqual(text_width(widget.text, widget.size) * 1.15, item.w,
                                             "%s: %r sığmıyor" % (name, widget.text))
            self.assertEqual(render(placed).size, (WIDTH, HEIGHT))


class TestACE(unittest.TestCase):
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

    def test_pending_queue_independent_response(self):
        # ERR-17: İki ardışık ACE komutunda unique_id atanmalı,
        # ilk yanıt geldiğinde (handle_ace_response) SADECE o id'li kaydı çıkarmalı, diğeri beklemeli.
        self.printer.ace_info_lines = False
        self.app.ace_gcode("ACE_FEED SLOT=0")
        self.app.ace_gcode("ACE_FEED SLOT=1")

        self.assertEqual(len(self.app.ace_pending), 2)
        cmd1 = self.app.ace_pending[0]
        cmd2 = self.app.ace_pending[1]
        self.assertIn("id", cmd1)
        self.assertIn("id", cmd2)
        self.assertNotEqual(cmd1["id"], cmd2["id"])

        # Sadece cmd1 için yanıt geldi
        res = self.app.handle_ace_response(cmd1["id"], message="feed 0 ok")
        self.assertIsNotNone(res)
        self.assertEqual(res["id"], cmd1["id"])

        # Kuyrukta sadece cmd2 kalmalı
        self.assertEqual(len(self.app.ace_pending), 1)
        self.assertEqual(self.app.ace_pending[0]["id"], cmd2["id"])
        self.assertEqual(self.app.ace_pending[0]["script"], "ACE_FEED SLOT=1")

        # cmd2 yanıtlandığında kuyruk boşalmalı
        res2 = self.app.handle_ace_response(cmd2["id"], message="feed 1 ok")
        self.assertIsNotNone(res2)
        self.assertEqual(len(self.app.ace_pending), 0)

    def test_reconcile_none_slots(self):
        # ERR-18: slots içinde None öğe varken _reconcile_ace_stopping_locked istisna atmamalı
        self.app.ace_stopping = ("feed", self.app._clock() + 10.0)
        status_with_none = {
            "ace_instance_0": {
                "connection_state": "connected",
                "slots": [None, {"status": "ready"}, None],
            }
        }
        # İstisna atmadan geçmeli
        self.app._reconcile_ace_stopping_locked(status_with_none)

        self.app.ace_stopping = ("retract", self.app._clock() + 10.0)
        self.app._reconcile_ace_stopping_locked(status_with_none)

        self.app.ace_stopping = ("other", self.app._clock() + 10.0)
        self.app._reconcile_ace_stopping_locked(status_with_none)


if __name__ == "__main__":
    unittest.main()
