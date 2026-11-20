#!/usr/bin/env python3
"""Render every screen against the fake Moonraker into preview/sheet.png and preview/sheet_en.png."""

import os
import sys
import tempfile

from PIL import Image

from fake_moonraker import FakePrinter, FakeWifiManager, serve
from kobra_screen import i18n
from kobra_screen.app import App
from kobra_screen.moonraker import Moonraker
from kobra_screen.render_pil import render
from kobra_screen.widgets import HEIGHT, WIDTH

COLUMNS = 5


def render_sheet(lang, out_path, pages_dir=None):
    printer = FakePrinter()
    httpd = serve(printer, simulate=False)
    tmp_home = tempfile.TemporaryDirectory()
    try:
        app = App(
            Moonraker("127.0.0.1", httpd.server_address[1]),
            wifi=FakeWifiManager(),
            lang=lang,
            rinkhals_home=tmp_home.name,
        )
        app.extrusion_lock = False
        shots = []

        def t(text):
            return i18n.tr(text, lang)

        def shot():
            app.notify("", 0)
            app.refresh()
            shots.append(render(app.layout()))

        def press(*texts):
            for text in texts:
                assert app.press(t(text)), "%s: %r" % (lang, text)
            app.refresh()

        def ticks(count):
            for _ in range(count):
                printer.tick()

        app.refresh()
        shot()                                                   # home, idle
        press("Yazdır"); shot()                                  # files
        press("kutu_08.gcode"); shot()                           # file details
        app.open("tool_map", "kutu_08.gcode"); shot()            # ACE slot mapping
        app.back()
        printer.status["extruder"]["target"] = 210.0
        printer.status["heater_bed"]["target"] = 60.0
        ticks(12)
        press("Yazdır"); ticks(17); shot()                       # home, printing
        press("Baskı ayarı", "Sport"); shot()                    # tune
        press("Nesneler"); shot()                                # print objects (exclude_object)
        app.back()
        app.back()
        press("Kontrol"); shot()                                 # control: heat
        press("Ayrıntı"); shot()                                 # heat chart detail
        app.back()
        app.press("210°C"); press("2", "3"); shot()              # keypad
        press("Vazgeç")
        printer.status["extruder"]["target"] = 0.0
        printer.status["heater_bed"]["target"] = 0.0
        app.refresh()
        press("PLA"); shot()                                     # "start heating?" confirmation with "don't ask again"
        press("Vazgeç")
        printer.status["extruder"]["target"] = 210.0
        printer.status["heater_bed"]["target"] = 60.0
        app.refresh()
        printer.status["print_stats"]["state"] = "standby"
        app.refresh()
        press("Eksen"); app.press("", icon="home"); shot()       # control: move (homed)
        press("Ek"); shot()                                      # control: extra
        press("Makara")
        assert app.press(t("PLA"), icon="thermo")
        app.refresh()
        shot()                                                   # filament + dryer
        press("PETG"); shot()                                    # slot page
        app.open("spool", 1); press("1000 g"); shot()            # spool remaining page
        app.back()
        press("Ayrıntı"); shot()                                 # slot detail page
        app.back()
        app.back()
        press("ACE Pro ayarları"); shot()                        # ace: status
        press("Özellik"); shot()                                 # ace: features
        press("İşlem"); shot()                                   # ace: ops
        app.back()
        app.log_event("Klipper hatası örnek kaydı", severity="error", source="klipper")
        app.log_event("Baskı tamamlandı", severity="ok", source="notify")
        press("Araçlar"); shot()                                 # tools
        press("Baskı geçmişi"); shot()                           # history list + totals
        app.open("history_detail", printer.history_jobs[0]["job_id"]); shot()  # history detail
        app.back()
        app.back()
        press("Olay geçmişi"); shot()                            # event log
        app.back()
        press("Bakım"); shot()                                   # maintenance reminders
        app.back()
        printer.config_files["printer_mutable.cfg"] = (
            '{"bed_mesh default": {"min_x": "15", "max_x": "240", "min_y": "15", "max_y": "235", '
            '"x_count": "5", "y_count": "5", "points": '
            '"-1.406000, -1.440500, -1.460000, -1.507000, -1.678000\\n'
            '-0.671500, -0.702500, -0.707500, -0.711000, -0.899500\\n'
            '-0.026500, -0.012000, -0.046500, -0.112000, -0.367000\\n'
            '0.539500, 0.467500, 0.429000, 0.339000, 0.160500\\n'
            '0.970000, 0.882000, 0.817000, 0.699000, 0.638500"}}'
        ).encode("utf-8")
        press("Tabla haritası"); shot()                          # bed mesh: badge + 3D surface + "Re-measure"
        app.bed_mesh_drag(35.0, 50.0, final=True); shot()        # the same surface after a finger drag
        press("Yeniden ölç"); shot()                             # re-measure heats the bed: confirmation first
        press("Vazgeç")
        app.back()
        app.refresh_sysinfo = lambda: None                       # the fake Moonraker has no proc_stats
        app.sysinfo = {"cpu": 41.0, "uptime": 9645, "cpu_temp": None, "mem_total_kb": 217876, "mem_used_kb": 111580,
                       "screen_cpu": 9.6, "screen_rss_kb": 36572, "klipper_cpu": 5.2, "moonraker_cpu": 14.8,
                       "moonraker_mem": "51000 kB",
                       "mcus": [{"label": "Ana kart", "load": 1.0, "rtt_ms": 1.0, "retransmit": 9, "invalid": 0},
                                {"label": "Nozul kartı", "load": 0.9, "rtt_ms": 1.2, "retransmit": 9, "invalid": 0}]}
        press("Sistem izleme"); shot()                           # tools: system status
        app.back()
        printer.status["extruder"]["target"] = 0.0
        ticks(40)
        press("Cold pull", "PLA", "İleri"); ticks(9); shot()     # wizard: heating
        ticks(20); app.refresh()
        press("İleri", "İleri"); ticks(20); shot()               # wizard: cooling
        app.end_wizard(cancelled=True)
        press("Ayar"); shot()                                    # settings
        press("Ekran"); shot()                                   # display sleep & brightness
        app.back()
        press("Ses"); shot()                                     # sound / beeper settings
        app.back()
        press("Güncelleme"); shot()                              # updates info
        app.back()
        press("Sistem"); shot()                                  # restart / stock mode
        app.back()
        press("Dil / Language"); shot()                          # language picker
        app.back()
        press("Ekranı kalibre et"); shot()                       # calibration intro
        press("Kalibrasyonu başlat"); shot()                     # calibration corner 1/4
        for rx, ry in ((401, 210), (401, 49), (83, 210), (83, 49)):
            app.record_calibration_tap(rx, ry)
        shot()                                                   # calibration center verify
        app.record_calibration_verify(136, 240); shot()          # calibration confirm (15s)
        press("Vazgeç")
        press("Wi-Fi"); shot()                                   # wifi list
        press("Ev_5G", "a", "b", "c"); shot()                    # qwerty keyboard
        press("Vazgeç")
        press("AtolyeWiFi"); shot()                              # wifi network detail
        app.back()
        app.back()
        press("Sınırlar"); shot()                                # limits 0
        assert app.press("", icon="next"); shot()                # limits 1
        assert app.press("", icon="next"); shot()                # limits 2
        assert app.press("", icon="next"); shot()                # limits 3
        assert app.press("", icon="next"); shot()                # limits 4 (ACE Isı ve Besleme)
        assert app.press("", icon="next"); shot()                # limits 5 (ACE Algılama)
        assert app.press("", icon="stop"); shot()                # E-stop confirmation
        press("Vazgeç")

        rows = -(-len(shots) // COLUMNS)
        sheet = Image.new("RGB", (COLUMNS * (WIDTH + 10) + 10, rows * (HEIGHT + 10) + 10), (70, 70, 74))
        for i, image in enumerate(shots):
            sheet.paste(image, (10 + (i % COLUMNS) * (WIDTH + 10), 10 + (i // COLUMNS) * (HEIGHT + 10)))
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        sheet.save(out_path)
        if pages_dir:
            os.makedirs(pages_dir, exist_ok=True)
            for i, image in enumerate(shots):
                image.save(os.path.join(pages_dir, "%02d.png" % (i + 1)), optimize=True)
        print("%s: %d ekran -> %s" % (lang, len(shots), out_path))
    finally:
        httpd.shutdown()
        httpd.server_close()
        tmp_home.cleanup()


def main():
    # --pages also writes every screen as its own PNG (preview/pages_<lang>/NN.png)
    pages = "--pages" in sys.argv[1:]
    render_sheet("tr", "preview/sheet.png", "preview/pages_tr" if pages else None)
    render_sheet("en", "preview/sheet_en.png", "preview/pages_en" if pages else None)


if __name__ == "__main__":
    main()
