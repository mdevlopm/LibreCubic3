"""Shared constants and reusable UI helpers for ``kobra_screen.screens``."""

import math
import time

from . import i18n, limits
from .widgets import Button, Card, Label, Row, Spacer

FILES_PER_PAGE = 6
HISTORY_PER_PAGE = 4
EVENTS_PER_PAGE = 3
OBJECTS_PER_PAGE = 5
WIFI_PER_PAGE = 3
JOG_STEPS = (0.1, 1, 10, 50)
EXTRUDE_STEPS = (1, 5, 10, 50)
ACE_FEED_STEPS = (10, 50, 100, 500)
BRIGHTNESS_STEPS = (25, 50, 75, 100)
SLEEP_TIMEOUTS = (("Kapalı", 0), ("1 dk", 60), ("5 dk", 300), ("15 dk", 900))
# name, nozzle, bed
PRESETS = (("PLA", 210, 60), ("PETG", 240, 80), ("ABS", 250, 100), ("ASA", 260, 110), ("TPU", 220, 60))
SPEED_MODES = (("Sessiz", 50), ("Normal", 100), ("Sport", 125), ("Çılgın", 166))
DRY_PRESETS = (
    ("PLA", 45, 240),
    ("PETG", 55, 360),
    ("ABS/ASA", 60, 360),
    ("PA/PC", 70, 480),
    ("TPU", 50, 360),
)


def clamp_dryer_temperature(temp, limit=None):
    """Clamp dryer temperature to max_dryer_temperature limit."""
    return limits.clamp_dryer_temperature(temp, limit)
# Raw `dryer_status.remain_time` multiplier to convert to seconds (1 = seconds; measured on the device: seconds).
DRYER_REMAIN_TIME_SCALE_SEC = 1
SLOT_MATERIALS = (("PLA", 210), ("PETG", 240), ("ABS", 250), ("TPU", 225))
SLOT_COLORS = ((245, 245, 245), (20, 20, 20), (220, 40, 40), (250, 140, 30), (245, 215, 40), (40, 170, 80),
               (40, 110, 230), (150, 70, 200))
SLOT_COLOR_NAMES = {
    (245, 245, 245): "Beyaz",
    (20, 20, 20): "Siyah",
    (220, 40, 40): "Kırmızı",
    (250, 140, 30): "Turuncu",
    (245, 215, 40): "Sarı",
    (40, 170, 80): "Yeşil",
    (40, 110, 230): "Mavi",
    (150, 70, 200): "Mor",
}
PAGE_TITLES = {"files": "Dosyalar", "file": "Baskı", "tune": "Baskı ayarı", "slot": "Yuva",
               "slot_detail": "Yuva ayrıntısı", "ace": "ACE Pro", "dry_presets": "Kurutma önayarları",
               "heat_chart": "Sıcaklık grafiği", "bed_mesh": "Tabla haritası", "sysinfo": "Sistem durumu",
               "limits": "Sınırlar", "wifi": "Wi-Fi", "wifi_net": "Wi-Fi ağı", "language": "Dil / Language",
               "history": "Baskı geçmişi", "history_detail": "Baskı ayrıntısı",
               "events": "Olay geçmişi", "maintenance": "Bakım", "updates": "Güncelleme", "system": "Sistem",
               "sound": "Sesli uyarı", "spool": "Kalan filament",
               "objects": "Nesneler", "display": "Ekran", "wizard": ""}


def _format_size(size_bytes):
    val = max(0, int(size_bytes or 0))
    if val < 1024:
        return "%d B" % val
    if val < 1024 * 1024:
        kb = val / 1024.0
        return ("%.1f KB" % kb) if kb < 10 else ("%.0f KB" % kb)
    return "%.1f MB" % (val / (1024.0 * 1024.0))


def _format_date(ts, with_year=False):
    if not ts or ts <= 0:
        return "-"
    tm = time.localtime(float(ts))
    if with_year:
        return "%02d.%02d.%04d %02d:%02d" % (tm.tm_mday, tm.tm_mon, tm.tm_year, tm.tm_hour, tm.tm_min)
    return "%02d.%02d %02d:%02d" % (tm.tm_mday, tm.tm_mon, tm.tm_hour, tm.tm_min)


def _format_filament_mm(mm):
    val = max(0.0, float(mm or 0.0))
    if val <= 0.0:
        return "0 m"
    if val < 100.0:
        return "%.0f mm" % val
    return "%.1f m" % (val / 1000.0)


def status_badge(text, color="ok", weight=1.0):
    bg = {"ok": "active", "warn": "surface2", "danger": "danger_bg", "muted": "surface2"}.get(color, "surface2")
    border = {"ok": "accent", "warn": "warn", "danger": "danger_border", "muted": "border_default"}.get(color, "border_subtle")
    txt_color = {"ok": "active_text", "warn": "warn", "danger": "danger_text", "muted": "muted"}.get(color, "text")
    return Card([Row(19, [Label(text, size="small", color=txt_color, align="center")])],
                color=bg, border=border, radius=9, pad=2, gap=0, weight=weight)


def duration(seconds, lang=i18n.DEFAULT_LANG):
    seconds = max(0, int(round(seconds or 0)))
    if seconds >= 3600:
        return i18n.tr("%dsa %02ddk", lang) % (seconds // 3600, seconds // 60 % 60)
    return (i18n.tr("%ddk", lang) % (seconds // 60)) if seconds >= 60 else (i18n.tr("%dsn", lang) % seconds)


def temps(app, name):
    return "%.0f° / %.0f°" % (app.get(name, "temperature"), app.get(name, "target"))


def section(title, value="", color="muted", w1=1.0, w2=1.0):
    if not value:
        return Row(19, [Label(title, size="small", color="muted")])
    return Row(19, [Label(title, size="small", color="muted", weight=w1),
                    Label(value, size="small", color=color, align="right", weight=w2)])


def segmented(buttons, height=36):
    return Card([Row(height, buttons)], color="surface2", border="border_subtle", radius=10, pad=2, gap=2)


# ---- print information ----

def print_info(app):
    """Progress, layers and time left of the current job."""
    filename = app.get("print_stats", "filename", "")
    meta = app.meta.get(filename) or {}
    progress = app.get("virtual_sdcard", "progress")
    elapsed = app.get("print_stats", "print_duration")
    info = app.get("print_stats", "info", {}) or {}
    layer, total = info.get("current_layer"), info.get("total_layer")
    layer_height = meta.get("layer_height")
    if total is None and layer_height and meta.get("object_height"):
        first = meta.get("first_layer_height", layer_height)
        total = int(math.ceil((meta["object_height"] - first) / layer_height + 1))
        z = app.get("gcode_move", "gcode_position", [0, 0, 0, 0])[2]
        layer = max(0, min(total, int(math.ceil((z - first) / layer_height + 1))))
    if progress > 0.02 and elapsed > 30:
        remaining = elapsed / progress - elapsed
    else:
        remaining = meta.get("estimated_time", 0) * (1 - progress)
    return {"filename": filename, "meta": meta, "progress": progress, "elapsed": elapsed, "remaining": remaining,
            "layer": layer, "total": total}


def _go_heat(app):
    app.control_seg = "heat"
    app.select_tab("control")


def _z_offset_text(app):
    """Format LeviQ3 z_offset together with live gcode_move Z baby-step when non-zero (ERR-22)."""
    z_off = float(app.get("leviq3", "z_offset", 0.0) or 0.0)
    ho = app.get("gcode_move", "homing_origin", [0.0, 0.0, 0.0, 0.0])
    z_adj = float(ho[2]) if isinstance(ho, (list, tuple)) and len(ho) > 2 else 0.0
    if abs(z_adj) >= 0.0005:
        return "Z %.3f (%+.3f)" % (z_off, z_adj)
    return "Z-offset %.3f mm" % z_off


def fan_card(app, steps=(0, 25, 50, 75, 100), pad=8, btn_h=44):
    fan = round(app.get("fan", "speed") * 100)
    return Card([
        section(app.tr("Parça fanı"), "%d%%" % fan, color="text"),
        Row(btn_h, [Button("%d%%" % pct, app.when(app.ready, lambda p=pct: app.gcode("M106 S%d" % round(p * 2.55))),
                           "active" if fan == pct else "normal") for pct in steps]),
    ], pad=pad, gap=4)


def light_buttons(app, height=44):
    def light(title, pin, macro):
        on = app.get("output_pin " + pin, "value") > 0
        return Button(app.tr(title), app.when(app.ready, lambda: app.gcode(macro)),
                      "active" if on else "normal", icon="light", icon_color="warn" if on else None, align="left")
    return Row(height, [light("Nozul", "nozzle_light", "nozzle_light_toggle"),
                        light("Logo", "logo_light", "logo_light_toggle")])


def blank(height):
    return Row(height, [Spacer()])
