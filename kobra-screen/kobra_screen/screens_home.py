"""Home, offline, control (heat/move/extra), tune, exclude-object and TemperatureScreen."""

import math
import time

from .screens_ace import _active_ace_row, ace_home_card
from .screens_common import (
    EXTRUDE_STEPS, JOG_STEPS, OBJECTS_PER_PAGE, PRESETS, SPEED_MODES, _go_heat, _z_offset_text,
    duration, fan_card, light_buttons, print_info, section, segmented, status_badge, temps,
)
from .widgets import Bar, Button, Card, Chart, Col, Icon, Label, Picture, Ring, Row, Spacer


def offline(app):
    state = app.state
    confirm_stock = lambda: app.confirm(
        app.tr("Kobra Screen ve Vanilla Klipper devre dışı bırakılıp yazıcı stok moda yeniden başlatılsın mı?"),
        app.return_to_stock,
        "primary",
    )
    if not state.connected:
        return [
            Card([Row(36, [Icon("error", "big", "danger", weight=0.25),
                           Label(app.tr("Bağlantı yok"), size="big", color="danger_text", weight=2.2)]),
                  Row(88, [Label(app.tr_error(state.klippy_message), size="small", color="muted")])],
                 color="danger_bg", border="danger_border", pad=10),
            Row(44, [Button(app.tr("Wi-Fi"), lambda: app.open("wifi"), "card", icon="wifi", align="left", right_icon="next")]),
            Row(44, [Button(app.tr("Stok moda dön"), confirm_stock, "outline_danger", icon="back", align="left")]),
        ]
    rows = [
        Card([Row(36, [Icon("warning", "big", "warn", weight=0.25),
                       Label("Klipper: " + state.klippy, size="h2", weight=2.2)]),
              Row(76, [Label(app.tr_error(state.klippy_message), size="small", color="muted")])],
             pad=8, gap=4),
        Row(44, [Button(app.tr("Yeniden başlat"), lambda: app.do(app.restart_klipper, app.tr("Klipper yeniden başlatıldı")),
                        "primary", icon="refresh", align="left")]),
    ]
    if state.klippy in ("error", "shutdown"):
        rows.append(Row(44, [Button(
            app.tr("Sınırları sıfırla ve başlat"),
            app.when(not app.printing, lambda: app.confirm(
                app.tr("Özel sınırlar (open_kobra_limits.cfg) temizlenip Klipper yeniden başlatılsın mı?"),
                app.recover_limits_and_restart,
                "danger",
            )),
            "outline_danger",
            icon="lock",
            align="left",
        )]))
    rows.append(Row(44, [Button(app.tr("Wi-Fi"), lambda: app.open("wifi"), "card", icon="wifi", align="left", right_icon="next"),
                         Button(app.tr("Stok moda dön"), confirm_stock, "normal", icon="back", align="left")]))
    return rows


def home_temps_card(app, compact=False):
    ext_t = app.get("extruder", "temperature")
    ext_g = app.get("extruder", "target")
    bed_t = app.get("heater_bed", "temperature")
    bed_g = app.get("heater_bed", "target")

    ext_txt = ("%.0f° / %.0f°C" % (ext_t, ext_g)) if ext_g else ("%.0f°C · %s" % (ext_t, app.tr("Kapalı")))
    bed_txt = ("%.0f° / %.0f°C" % (bed_t, bed_g)) if bed_g else ("%.0f°C · %s" % (bed_t, app.tr("Kapalı")))
    row_h = 19 if compact else 24
    sz = "small" if compact else "med"
    return Card([
        Row(row_h, [
            Icon("nozzle", "small", "warn" if ext_g else "tertiary", weight=0.16),
            Label(app.tr("Nozul"), size=sz, color="muted", weight=0.6),
            Label(ext_txt, size=sz, color="text", align="right", weight=1.15),
        ]),
        Row(row_h, [
            Icon("bed", "small", "warn" if bed_g else "tertiary", weight=0.16),
            Label(app.tr("Tabla"), size=sz, color="muted", weight=0.6),
            Label(bed_txt, size=sz, color="text", align="right", weight=1.15),
        ]),
    ], pad=4 if compact else 6, gap=1 if compact else 2, action=lambda: _go_heat(app))


def home(app):
    state = app.print_state
    chip, chip_color = app._status_chip()
    if state in ("printing", "paused"):
        info = print_info(app)
        thumbnail = info["meta"].get("thumbnail")
        percent = "%d%%" % round(info["progress"] * 100)
        bar_color = "warn" if state == "paused" else "accent"
        visual = Picture(thumbnail, weight=0.9) if thumbnail else Ring(info["progress"], percent, color=bar_color, weight=0.9)
        layers = "%s / %s" % (info["layer"], info["total"]) if info["total"] else "-"
        ace = app.ace_data() if app.ace_connected else {}
        strip = [
            Col([Row(19, [Label(app.tr("Nozul"), size="small", color="tertiary", align="center")]),
                 Row(19, [Label(temps(app, "extruder"), size="small", align="center")])], gap=1),
            Col([Row(19, [Label(app.tr("Tabla"), size="small", color="tertiary", align="center")]),
                 Row(19, [Label(temps(app, "heater_bed"), size="small", align="center")])], gap=1),
        ]
        if ace:
            strip.append(Col([Row(19, [Label("ACE", size="small", color="tertiary", align="center")]),
                              Row(19, [Label("%s°C" % ace.get("temp", "?"), size="small", align="center")])], gap=1))
        card_rows = [
            Row(84, [visual, Col([
                Row(23, [status_badge(chip, chip_color, weight=1.0), Spacer(0.35)]),
                Row(38, [Label(info["filename"], size="small")]),
                Row(19, [Icon("layers", "small", "muted", weight=0.22), Label(layers, size="small", color="muted")]),
            ], weight=1.6, gap=2)]),
            Row(44, [
                Label(percent, size="num", weight=1.0),
                Col([
                    Row(23, [Label(app.tr("Kalan %s") % duration(info["remaining"], app.lang), size="med", color="text", align="right")]),
                    Row(19, [Label(app.tr("Geçen %s") % duration(info["elapsed"], app.lang), size="small", color="muted", align="right")]),
                ], weight=1.3),
            ]),
            Row(8, [Bar(info["progress"], color=bar_color)]),
            Row(19, [Label(_z_offset_text(app), size="small", color="tertiary")]),
        ]
        ace_row = _active_ace_row(app)
        if ace_row is not None:
            card_rows.append(ace_row)
        stop = Button(app.tr("Durdur"), lambda: app.confirm(app.tr("Baskı iptal edilsin mi?"), lambda: app.do(app.client.print_cancel),
                                                            "danger"), "danger", icon="stop", align="left")
        if state == "printing":
            first = Button(app.tr("Duraklat"), lambda: app.do(app.client.print_pause), "normal", icon="pause", align="left")
        else:
            ext_locked = bool(getattr(app, "extrusion_lock", False))
            first = Button(
                app.tr("Devam"),
                app.resume_print,
                "normal" if ext_locked else "primary",
                icon="lock" if ext_locked else "play",
                align="left",
            )
        return [Card([
            Card(card_rows, pad=5, gap=2),
            Card([Row(39, strip)], pad=4, gap=2, action=lambda: _go_heat(app)),
            Row(44, [first, stop]),
            Row(44, [
                Button(app.tr("Baskı ayarı"), lambda: app.open("tune"), icon="tune", align="left", weight=1.15),
                Button(app.tr("Nesneler"), lambda: app.open("objects"), icon="layers", align="left", weight=1.0),
            ]),
        ], color="bg", border=None, radius=0, pad=0, gap=6)]

    titles = {"complete": ("Baskı tamamlandı", "check", "ok"), "cancelled": ("Baskı iptal edildi", "close", "muted"),
              "error": ("Baskı hatası", "error", "danger")}
    banner = state in titles
    rows = []
    if banner:
        title, icon, color = titles[state]
        detail = app.get("print_stats", "message", "") if state == "error" else app.get("print_stats", "filename", "")
        right_badge = (status_badge(app.tr("Isıtma kilitli"), "warn", weight=1.25)
                       if (app.heating_lock and not detail)
                       else Label(detail or _z_offset_text(app),
                                  size="small", color="muted", align="right", weight=1.45))
        rows.append(Card([
            Row(23, [Icon(icon, "normal", color, weight=0.14),
                     Label(app.tr(title), size="med", color="danger_text" if state == "error" else "text", weight=2.1)]),
            Row(23, [status_badge(chip, chip_color, weight=0.95), right_badge]),
        ], color="danger_bg" if state == "error" else "card",
            border="danger_border" if state == "error" else "border_subtle", pad=4, gap=2))
    else:
        right_item = (status_badge(app.tr("Isıtma kilitli"), "warn", weight=1.25)
                      if app.heating_lock
                      else Label(_z_offset_text(app),
                                 size="small", color="tertiary", align="right", weight=1.35))
        rows.append(Card([
            Row(23, [status_badge(chip, chip_color, weight=0.90), right_item]),
        ], pad=6, gap=0))

    due = app.due_maintenance_items()
    low_spools = app.low_filament_spools()
    has_alert = bool(due or low_spools)
    if due:
        first_due = app.tr(due[0]["label"])
        alert_txt = (app.tr("Bakım: %s (+%d)") % (first_due, len(due) - 1)) if len(due) > 1 else (app.tr("Bakım: %s") % first_due)
        alert_card = Card([Row(19, [
            Icon("warning", "small", "warn", weight=0.18),
            Label(alert_txt, size="small", color="warn", weight=1.8),
            Icon("next", "small", "tertiary", weight=0.15),
        ])], color="surface2", border="warn", pad=4, gap=0, action=lambda: app.open("maintenance"))
    elif low_spools:
        sp0 = low_spools[0]
        if sp0["key"] == "external":
            alert_txt = app.tr("Düşük filament: Harici (%.0fg)") % sp0["remaining_g"]
        else:
            alert_txt = app.tr("Düşük filament: Yuva %d (%.0fg)") % (int(sp0["key"]) + 1, sp0["remaining_g"])
        alert_card = Card([Row(19, [
            Icon("spool", "small", "warn", weight=0.18),
            Label(alert_txt, size="small", color="warn", weight=1.8),
            Icon("next", "small", "tertiary", weight=0.15),
        ])], color="surface2", border="warn", pad=4, gap=0, action=lambda k=sp0["key"]: app.open("spool", k))
    else:
        alert_card = None

    if alert_card is not None:
        rows.append(alert_card)

    compact_home = banner and has_alert
    btn_h = 44 if (banner or has_alert) else 46
    rows.append(home_temps_card(app, compact=compact_home))
    rows.append(ace_home_card(app, compact=compact_home))

    def preheat():
        app.request_heat({"extruder": PRESETS[0][1], "heater_bed": PRESETS[0][2]}, app.tr("PLA ön ısıtma başladı"))

    locked = bool(app.heating_lock)
    rows.append(Row(btn_h, [
        Button(app.tr("Tabla seviyeleme"), app.when(not locked, lambda: app.start_wizard("level")),
               icon="lock" if locked else "level", align="left", weight=1.42),
        Button(app.tr("Ön ısıtma"), app.when(not locked, preheat),
               icon="lock" if locked else "nozzle", align="left", weight=1.0),
    ]))
    ext_locked = bool(getattr(app, "extrusion_lock", False))
    rows.append(Row(btn_h, [Button(
        app.tr("Yazdır"),
        (lambda: app.notify(app.tr("Ekstrüzyon kilidi açık"))) if ext_locked else (lambda: app.open("files")),
        "normal" if ext_locked else "primary",
        icon="lock" if ext_locked else "print",
        align="left" if ext_locked else "center",
        sub=app.tr("Ekstrüzyon kilidi") if ext_locked else "",
    )]))
    if has_alert or banner:
        return [Card(rows, color="bg", border=None, radius=0, pad=0, gap=3 if compact_home else 5)]
    return rows


def set_temp(app, name, value):
    app.request_heat({name: value})


def heater_card(app, title, name, icon, compact=False):
    max_temp = app.setting(name, "max_temp", 0)
    limit = int(max_temp) if max_temp is not None else 300
    temp = app.get(name, "temperature")
    target = app.get(name, "target")
    ok = app.ready
    locked = bool(app.heating_lock)
    # Combine temperature and target into one label to avoid overlap with unit
    temp_text = ("%.0f / %.0f°C" % (temp, target)) if target else ("%.0f°C · %s" % (temp, app.tr("kapalı")))
    label_title = app.tr(title)
    target_txt = ("%.0f°C" % target) if target else app.tr("Hedef")
    return Card([
        Row(28 if compact else 29, [
            Icon(icon, "normal", "warn" if target else "tertiary", weight=0.22),
            Label(label_title, size="med", color="muted", weight=0.8),
            Label(temp_text, size="h2", align="right", weight=1.8),
        ]),
        Row(36 if compact else 44, [
            Button("-5°C", app.when(ok and not locked, lambda: set_temp(app, name, target - 5)), weight=0.88),
            Button(target_txt, app.when(ok and not locked, lambda: app.keypad(
                app.tr("%s (en çok %d°C)") % (label_title, limit), 0, limit, lambda value: set_temp(app, name, value),
                initial=target)),
                "active" if target else "normal", icon="lock" if locked else "edit", weight=1.24),
            Button("+5°C", app.when(ok and not locked, lambda: set_temp(app, name, target + 5)), weight=0.88),
            Button(app.tr("Kapat"), app.when(ok, lambda: set_temp(app, name, 0)), weight=0.95),
        ]),
    ], pad=3 if compact else 8, gap=2 if compact else 6)


def control_heat(app):
    ok = app.ready
    locked = bool(app.heating_lock)
    ext_g = round(app.get("extruder", "target"))
    bed_g = round(app.get("heater_bed", "target"))
    preset_btns = [
        Button(name, app.when(ok and not locked, lambda n=nozzle, b=bed: app.request_heat(
            {"extruder": n, "heater_bed": b})),
               "active" if (ext_g == nozzle and bed_g == bed) else "normal",
               icon="lock" if locked else None)
        for name, nozzle, bed in PRESETS
    ]
    row1_btns = preset_btns[:3]
    row2_btns = preset_btns[3:]
    preheat_rows = [
        Row(19, [
            Label(app.tr("Ön ısıtma"), size="small", color="muted", weight=0.68),
            Label(app.tr("Isıtma kilitli") if locked else "PLA · PETG · ABS · ASA · TPU",
                  size="tab", color="warn" if locked else "tertiary", align="right", weight=1.65),
        ]),
        Row(35, row1_btns),
    ]
    if row2_btns:
        preheat_rows.append(Row(35, row2_btns))
    return [Card([
        heater_card(app, "Nozul", "extruder", "nozzle", compact=True),
        heater_card(app, "Tabla", "heater_bed", "bed", compact=True),
        Card(preheat_rows, pad=3, gap=1),
        Row(36, [
            Button(app.tr("Hepsini kapat"), app.when(ok, lambda: app.gcode("TURN_OFF_HEATERS")), "outline_danger",
                   icon="power", align="left", weight=1.25),
            Button(app.tr("Ayrıntı"), lambda: app.open("heat_chart"), "card",
                   icon="chart", align="left", right_icon="next", weight=1.0),
        ]),
    ], color="bg", border=None, radius=0, pad=0, gap=2)]


def heat_chart_page(app, arg):
    ok = app.ready
    noz_t, noz_g = app.temperature_series("extruder", 60)
    bed_t, bed_g = app.temperature_series("heater_bed", 60)
    all_vals = [v for v in (noz_t + noz_g + bed_t + bed_g) if v is not None] or [0.0]
    y_max = max(120.0, math.ceil((max(all_vals) + 20.0) / 20.0) * 20.0)
    ext_t = app.get("extruder", "temperature")
    ext_g = app.get("extruder", "target")
    bed_temp = app.get("heater_bed", "temperature")
    bed_goal = app.get("heater_bed", "target")
    return [
        Card([
            Row(19, [
                Label(app.tr("Nozul"), size="small", color="danger", weight=0.65),
                Label(app.tr("Tabla"), size="small", color="info", weight=0.65),
                Label(app.tr("5 dk · %.0f°") % y_max, size="small", color="tertiary", align="right", weight=1.1),
            ]),
            Row(180, [Chart([
                (noz_g, "warn", True),
                (noz_t, "danger", False),
                (bed_g, "tertiary", True),
                (bed_t, "info", False),
            ], y_min=0.0, y_max=y_max, point_count=60)]),
        ], pad=8, gap=4),
        Card([
            Row(23, [
                Icon("nozzle", "small", "warn" if ext_g else "tertiary", weight=0.16),
                Label(app.tr("Nozul"), size="med", color="muted", weight=0.6),
                Label(("%.0f° / %.0f°C" % (ext_t, ext_g)) if ext_g else ("%.0f°C · %s" % (ext_t, app.tr("Kapalı"))),
                      size="med", align="right", weight=1.15),
            ]),
            Row(23, [
                Icon("bed", "small", "warn" if bed_goal else "tertiary", weight=0.16),
                Label(app.tr("Tabla"), size="med", color="muted", weight=0.6),
                Label(("%.0f° / %.0f°C" % (bed_temp, bed_goal)) if bed_goal else ("%.0f°C · %s" % (bed_temp, app.tr("Kapalı"))),
                      size="med", align="right", weight=1.15),
            ]),
        ], pad=8, gap=4),
        Row(44, [Button(app.tr("Hepsini kapat"), app.when(ok, lambda: app.gcode("TURN_OFF_HEATERS")), "outline_danger",
                        icon="power", align="left")]),
    ]


def control_move(app):
    ok = app.ready and app.print_state != "printing"
    position = app.get("toolhead", "position", [0, 0, 0, 0])
    homed = app.get("toolhead", "homed_axes", "")

    amin = app.get("toolhead", "axis_minimum", None)
    amax = app.get("toolhead", "axis_maximum", None)

    def in_bounds(axis, sign):
        """False when one more jog step would leave the machine's axis range (Klipper would refuse it)."""
        idx = "XYZ".index(axis)
        try:
            nxt = float(position[idx]) + sign * app.jog_step
            return float(amin[idx]) - 1e-3 <= nxt <= float(amax[idx]) + 1e-3
        except (TypeError, IndexError, ValueError, KeyError):
            return True

    def jog(axis, sign, icon):
        speed = 600 if axis == "Z" else 6000
        label = "%s%s" % (axis, "+" if sign > 0 else "-")
        return Button(label, app.when(ok and axis.lower() in homed and in_bounds(axis, sign), lambda: app.gcode(
            "SAVE_GCODE_STATE NAME=_screen_jog\nG91\nG1 %s%s F%d\nRESTORE_GCODE_STATE NAME=_screen_jog"
            % (axis, sign * app.jog_step, speed))), icon=icon)

    def set_step(value):
        app.jog_step = value
        app.dirty = True

    return [
        Row(20, [Label("%s %.1f" % (axis, position[i]), size="small", align="center",
                       color="text" if axis.lower() in homed else "tertiary") for i, axis in enumerate("XYZ")]),
        Card([
            Row(46, [Spacer(), jog("Y", 1, "up"), Spacer(), jog("Z", 1, "up")]),
            Row(46, [jog("X", -1, "left"),
                     Button("", app.when(ok, lambda: app.gcode("G28")), "active", icon="home"),
                     jog("X", 1, "right"), Spacer()]),
            Row(46, [Spacer(), jog("Y", -1, "down"), Spacer(), jog("Z", -1, "down")]),
        ], pad=6, gap=4),
        segmented([Button("%g mm" % step, lambda s=step: set_step(s),
                          "segment" if step == app.jog_step else "segment_off") for step in JOG_STEPS]),
        Row(44, [Button(app.tr("XY sıfırla"), app.when(ok, lambda: app.gcode("G28 X Y")), icon="home", align="left", weight=0.95),
                 Button(app.tr("Motorları kapat"), app.when(ok, lambda: app.gcode("M84")), icon="power", align="left", weight=1.28)]),
    ]


def control_extra(app):
    hot = app.get("extruder", "temperature") >= app.setting("extruder", "min_extrude_temp", 170)
    ok = app.idle and hot
    ext_locked = bool(getattr(app, "extrusion_lock", False))
    step = app.extrude_step

    def set_step(value):
        app.extrude_step = value
        app.dirty = True

    def extrude(length):
        return lambda: app.gcode("M83\nG1 E%d F300\nM82" % length)

    return [Card([
        fan_card(app, pad=6),
        Card([section(app.tr("Işık")), light_buttons(app)], pad=6, gap=4),
        Card([
            section(app.tr("Ekstruder"),
                    app.tr("nozul soğuk") if not hot else (app.tr("Adım: %d mm") % step),
                    "warn" if not hot else "tertiary"),
            segmented([Button("%d mm" % s, lambda v=s: set_step(v),
                              "segment" if step == s else "segment_off") for s in EXTRUDE_STEPS]),
            Row(44, [
                Button(app.tr("Geri çek"), app.when(ok, extrude(-step)), icon="unload", align="left"),
                Button(
                    app.tr("Ekstrüde"),
                    app.when(ok and not ext_locked, extrude(step)),
                    icon="lock" if ext_locked else "load",
                    align="left",
                    disabled=ext_locked,
                    tooltip="extrusion_locked_msg" if ext_locked else "",
                ),
            ]),
        ], pad=6, gap=4),
    ], color="bg", border=None, radius=0, pad=0, gap=5)]


def control(app):
    def seg(key):
        def select():
            app.control_seg = key
            app.dirty = True
        return select

    rows = [segmented([Button(app.tr(title), seg(key), "segment" if app.control_seg == key else "segment_off")
                       for title, key in (("Isı", "heat"), ("Eksen", "move"), ("Ek", "extra"))])]
    return rows + {"heat": control_heat, "move": control_move, "extra": control_extra}[app.control_seg](app)


def tune(app, arg):
    ok = app.ready
    speed = round(app.get("gcode_move", "speed_factor", 1) * 100)
    flow = round(app.get("gcode_move", "extrude_factor", 1) * 100)
    offset = app.get("gcode_move", "homing_origin", [0, 0, 0, 0])[2]
    return [Card([
        Card([section(app.tr("Baskı hızı"), "%d%%" % speed, color="text"),
              Row(44, [Button(app.tr(name), app.when(ok, lambda v=value: app.gcode("M220 S%d" % v)),
                              "active" if speed == value else "normal") for name, value in SPEED_MODES])],
             pad=4, gap=2),
        Card([section(app.tr("Akış oranı"), "%d%%" % flow, color="text"),
              Row(44, [Button("-5%", app.when(ok, lambda: app.gcode("M221 S%d" % max(50, flow - 5)))),
                       Button("+5%", app.when(ok, lambda: app.gcode("M221 S%d" % min(150, flow + 5)))),
                       Button("100%", app.when(ok, lambda: app.gcode("M221 S100")))])],
             pad=4, gap=2),
        Card([section(app.tr("Z ince ayar"), "%+.3f mm" % offset, color="text"),
              Row(44, [Button("%+.2f" % step, app.when(ok, lambda s=step: app.gcode(
                  "SET_GCODE_OFFSET Z_ADJUST=%.2f MOVE=1" % s))) for step in (-0.05, -0.01, 0.01, 0.05)])],
             pad=4, gap=2),
        fan_card(app, (0, 50, 100), pad=4, btn_h=44),
        Row(44, [Button(app.tr("Nesneler"), lambda: app.open("objects"), "card",
                        icon="layers", align="left", right_icon="next")]),
    ], color="bg", border=None, radius=0, pad=0, gap=3)]


def objects_page(app, arg):
    eo = app.state.status.get("exclude_object") or {}
    objs = list(eo.get("objects") or ())
    excluded = set(eo.get("excluded_objects") or ())
    current = eo.get("current_object")
    if not objs:
        return [
            Card([
                Row(36, [Spacer(), Icon("info", "big", "muted", weight=0.3), Spacer()]),
                Row(72, [Label(app.tr("Bu dosyada nesne bilgisi yok (dilimleyicide etiketlemeyi açın)"),
                               size="med", color="muted", align="center")]),
            ], pad=14, gap=6),
        ]

    pages = max(1, -(-len(objs) // OBJECTS_PER_PAGE))
    app.objects_page = max(0, min(app.objects_page, pages - 1))
    start = app.objects_page * OBJECTS_PER_PAGE
    page_items = objs[start:start + OBJECTS_PER_PAGE]

    rows = [
        Row(19, [
            Label(app.tr("Baskı nesneleri"), size="small", color="muted", weight=1.02),
            Label(app.tr("%d nesne · %d iptal") % (len(objs), len(excluded)),
                  size="tab", color="muted", align="right", weight=1.38),
        ]),
    ]
    btns = []
    for obj in page_items:
        name = str(obj.get("name") or "")
        is_excluded = name in excluded
        is_current = (current == name) and not is_excluded
        if is_excluded:
            sub = app.tr("İptal edildi")
            kind = "outline_danger"
            icon = "close"
            action = None
        else:
            sub = app.tr("Şu an basılıyor") if is_current else app.tr("Basılıyor")
            kind = "active" if is_current else "card"
            icon = "play" if is_current else "layers"
            action = app.when(
                app.ready and app.printing,
                lambda n=name: app.confirm(
                    app.tr("%s nesnesi iptal edilsin mi?\n\nBu baskıda bu nesne bir daha basılmayacak.") % n,
                    lambda n=n: app.gcode("EXCLUDE_OBJECT NAME=%s" % n, app.tr("%s iptal edildi") % n),
                    "danger",
                    yes_label="Evet, iptal et",
                ),
            )
        btns.append(Row(46, [Button(name, action, kind, icon=icon, align="left", sub=sub,
                                    right_icon=None if is_excluded else "close")]))
    rows.append(Card(btns, color="bg", border=None, radius=0, pad=0, gap=4))

    def turn(delta):
        app.objects_page += delta
        app.dirty = True

    if pages > 1:
        rows.append(Row(44, [
            Button("", app.when(app.objects_page > 0, lambda: turn(-1)), icon="prev"),
            Label("%d / %d" % (app.objects_page + 1, pages), size="med", color="muted", align="center"),
            Button("", app.when(app.objects_page < pages - 1, lambda: turn(1)), icon="next"),
        ]))
    return rows


class TemperatureScreen:
    """ERR-25: Temperature screen controller with throttled updates and dirty flag optimization."""

    def __init__(self, app=None):
        self.app = app
        self._temp_update_interval = 5.0
        self._last_update_time = 0.0
        self._last_data = None
        self.dirty = False

    def update(self, data=None):
        now = time.monotonic()
        if self._last_update_time > 0 and (now - self._last_update_time < self._temp_update_interval):
            return False
        self._last_update_time = now
        if data is not None and data == self._last_data:
            return False
        self._last_data = data
        self.dirty = True
        if self.app is not None:
            self.app.dirty = True
        return True

    def should_update(self, now=None):
        t = now if now is not None else time.monotonic()
        return (t - self._last_update_time) >= self._temp_update_interval
