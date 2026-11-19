"""ACE Pro home card, filament/dryer tab, drying presets and spool tracker pages."""

import math

from . import i18n, limits, tracker
from .screens_common import (
    DRY_PRESETS, DRYER_REMAIN_TIME_SCALE_SEC, SLOT_COLOR_NAMES, clamp_dryer_temperature, duration, section,
)
from .widgets import Bar, Button, Card, Icon, Label, Row


def _slots(ace):
    """The ACE slot dicts; tolerates a missing or null `slots` and junk entries."""
    return [s for s in ((ace or {}).get("slots") or ()) if isinstance(s, dict) and "index" in s]


def _ace_disconnected_card(app, compact=False, extra_rows=None, tight=False):
    ace = app.ace_data()
    conn_state = app.ace_connection_state()
    state_txt = _conn_state_tr(conn_state, app.lang) if ace else app.tr("bulunamadı")
    state_col = "warn" if conn_state in ("connecting", "reconnecting", "initializing") else "tertiary"
    rows = [
        Row(23, [
            Icon("spool", "normal", "tertiary", weight=0.18),
            Label(app.tr("ACE Pro bağlı değil"), size="med", color="text", weight=1.8),
        ]),
        Row(19, [
            Label(app.tr("Durum"), size="small", color="muted", weight=0.7),
            Label(state_txt, size="small", color=state_col, align="right", weight=1.3),
        ]),
    ]
    if extra_rows:
        rows.extend(extra_rows)
    if ace and conn_state == "shutdown":
        # ACE_RECONNECT does nothing after a Klipper shutdown: the driver comes back with Klipper.
        rows.append(Row(19, [Label(app.tr("Klipper yeniden başlatılmalı"), size="small", color="warn")]))
    elif ace and conn_state != "disabled":
        rows.append(Row(36 if tight else 44, [
            Button(
                app.tr("Yeniden bağlan"),
                app.when(app.ready, lambda: app.ace_gcode(
                    "ACE_RECONNECT", app.tr("ACE yeniden bağlanıyor"), require_connected=False)),
                "normal",
                icon="refresh",
                align="left",
            )
        ]))
    return Card(rows, pad=4 if tight else (5 if compact else 8), gap=2 if tight else (3 if compact else 4))


def _ace_slot_button(app, slot, active, on_home=False):
    idx = slot["index"]
    raw_st = str(slot.get("status") or "").strip().lower()
    empty = raw_st == "empty"
    is_busy = raw_st in app.ACE_ACTIVE_SLOT_STATES
    is_err = raw_st in ("gear_err", "tmc_err")
    is_active = not empty and slot.get("tool", idx) == active
    sp = app.spool_info(str(idx))
    if sp["configured"]:
        sub_txt = app.tr("Y%d · %.0fg!" if sp["low"] else "Y%d · %.0fg") % (idx + 1, sp["remaining_g"])
    else:
        sub_txt = app.tr("Yuva %d") % (idx + 1)
    if is_busy or is_err:
        main_txt = _slot_status_tr(raw_st, app.lang)
    elif empty:
        main_txt = app.tr("Boş")
    else:
        main_txt = slot.get("material") or slot.get("type") or "?"
    if is_err:
        kind = "outline_danger"
    elif is_active or is_busy:
        kind = "active"
    else:
        kind = "card"
    return Button(
        main_txt,
        lambda index=idx: app.open("slot", index),
        kind,
        align="left",
        dot=None if empty else tuple(slot.get("color") or (0, 0, 0)),
        icon="add" if empty else None,
        icon_color="tertiary" if empty else None,
        sub=sub_txt,
    )


def _active_ace_slot(app):
    """Return the active non-empty ACE slot dict if ACE is connected and a slot is active."""
    if not app.ace_connected:
        return None
    active = app.get("ace_state", "current_index", -1)
    if not isinstance(active, int) or active < 0:
        return None
    ace = app.ace_data()
    for slot in _slots(ace):
        idx = slot.get("index")
        if slot.get("tool", idx) == active or idx == active:
            raw_st = str(slot.get("status") or "").strip().lower()
            if raw_st != "empty":
                return slot
    return None


def _active_ace_row(app):
    """Build a compact print-card row showing the active ACE slot's color dot and name."""
    slot = _active_ace_slot(app)
    if slot is None:
        return None
    idx = slot["index"]
    raw_color = slot.get("color") or ()
    has_color = len(raw_color) >= 3
    color = tuple(int(c) for c in raw_color[:3]) if has_color else (167, 175, 171)
    material = str(slot.get("material") or slot.get("type") or "").strip()
    cname = (app.tr(SLOT_COLOR_NAMES[color]) if color in SLOT_COLOR_NAMES else app.tr("Özel")) if has_color else ""
    parts = [app.tr("Yuva %d") % (idx + 1)]
    if material:
        parts.append(material)
    if cname:
        parts.append(cname)
    return Row(28, [Button(" · ".join(parts), None, "ghost", dot=color, align="left")])


def ace_home_card(app, compact=False):
    if not app.ace_connected:
        ext = app.spool_info("external")
        extra = []
        if ext["configured"]:
            col = "danger_text" if ext["low"] else "muted"
            txt = app.tr("Harici: %s · %.0fg") % (ext["material"], ext["remaining_g"])
            extra.append(Row(19 if compact else 23, [Label(txt, size="small" if compact else "med", color=col)]))
        if not app.ace_driver_present and ext["configured"]:
            return Card([
                Row(19, [Icon("spool", "small", "tertiary", weight=0.16),
                         Label(app.tr("ACE Pro bağlı değil"), size="small", color="muted", weight=1.8)]),
                Row(24, [Label(app.tr("Harici: %s · %.0fg") % (ext["material"], ext["remaining_g"]),
                               size="med", color="danger_text" if ext["low"] else "text")]),
            ], pad=6 if compact else 8, gap=2, action=lambda: app.open("spool", "external"))
        # `compact` is only set when the print banner and an alert already use the space: squeeze the card further
        return _ace_disconnected_card(app, compact=True, extra_rows=extra, tight=compact)

    ace = app.ace_data()
    dry = ace.get("dryer_status") or ace.get("dryer") or {}
    active = app.get("ace_state", "current_index", -1)
    slots = [_ace_slot_button(app, slot, active, on_home=True) for slot in _slots(ace)]
    ops = app.ace_active_slot_ops()
    drying = dry.get("status") == "drying"
    fa = ace.get("feed_assist_slot", -1)

    right_txt = ""
    right_col = "tertiary"
    if ops:
        s0, st0 = ops[0]
        right_txt = app.tr("Y%d: %s") % (s0["index"] + 1, _slot_status_tr(st0, app.lang))
        right_col = "warn"
    elif drying:
        right_txt = app.tr("Kurutuyor · %s°") % dry.get("target_temp", 0)
        right_col = "warn"
    elif active >= 0:
        sp = app.spool_info(str(active))
        if sp["configured"]:
            right_txt = "T%d · %.0fg" % (active, sp["remaining_g"])
            right_col = "danger_text" if sp["low"] else "tertiary"
        else:
            right_txt = app.tr("aktif: T%d") % active
    else:
        low_list = app.low_filament_spools()
        if low_list:
            right_txt = app.tr("Az: Y%d") % (int(low_list[0]["key"]) + 1)
            right_col = "warn"
        elif fa >= 0:
            right_txt = app.tr("Assist: Y%d") % (fa + 1)
            right_col = "active_text"

    return Card([
        Row(19, [Label("ACE Pro", size="small", color="muted", weight=0.7),
                 Label(right_txt, size="small", color=right_col, align="right", weight=1.3)]),
        Row(44, slots[:2]),
        Row(44, slots[2:4]),
    ], pad=4 if compact else 6, gap=2 if compact else 3, action=lambda: app.select_tab("filament"))


def dryer(app):
    ace = app.ace_data()
    dry = ace.get("dryer_status") or ace.get("dryer")
    return ace, (dry if isinstance(dry, dict) else {})


def _dryer_remain_seconds(dry, scale=None):
    """Convert `dryer_status.remain_time` to seconds using `DRYER_REMAIN_TIME_SCALE_SEC`."""
    raw = float((dry or {}).get("remain_time") or 0)
    factor = DRYER_REMAIN_TIME_SCALE_SEC if scale is None else float(scale)
    return max(0, int(round(raw * factor)))


def _dryer_progress(dry, remain_sec):
    """Compute [0, 1] dryer progress; dryer_status.duration is minutes."""
    total_min = (dry or {}).get("duration") or 0
    total_sec = int(round(total_min * 60)) if total_min > 0 else remain_sec
    if total_sec <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - remain_sec / float(total_sec)))


def _slot_status_tr(status, lang=i18n.DEFAULT_LANG):
    raw = str(status or "")
    key = {
        "empty": "boş",
        "ready": "hazır",
        "feeding": "sürülüyor",
        "unwinding": "geri çekiliyor",
        "shifting": "geçişte",
        "preload": "ön yükleme",
        "identifying": "RFID okunuyor",
        "gear_err": "dişli hatası",
        "tmc_err": "sürücü hatası",
    }.get(raw.lower())
    return i18n.tr(key, lang) if key else (raw or "?")


def _ace_speeds(app, ace):
    """Feed / retract speed: what the driver reports now, else screen-set value or start-up setting."""
    def _pick(ace_key, cache_key):
        val = ace.get(ace_key)
        if val is None or val == "":
            val = app.ace_speeds.get(cache_key)
        if val is None or val == "":
            val = app.setting("ace", ace_key, "-")
        if isinstance(val, float) and val.is_integer():
            return int(val)
        return val

    return _pick("feed_speed", "feed"), _pick("retract_speed", "retract")


def _tangle_protection(app, ace):
    """(present, enabled, threshold) of the ACE1 tangle protection (ace-driver 0.2.0); absent on older drivers."""
    raw = ace.get("tangle_protection")
    if raw is None:
        raw = app.get("ace_state", "tangle_protection", None)
    if raw is None:
        return False, False, None
    if isinstance(raw, dict):
        enabled = raw.get("enabled", raw.get("enable", raw.get("active", False)))
        return True, bool(enabled), raw.get("threshold")
    return True, bool(raw), None


def _conn_state_tr(state, lang=i18n.DEFAULT_LANG):
    raw = str(state or "")
    key = {
        "connected": "bağlı",
        "connecting": "bağlanıyor",
        "reconnecting": "yeniden bağlanıyor",
        "initializing": "başlatılıyor",
        "disabled": "devre dışı",
        "shutdown": "kapatıldı",
    }.get(raw.lower())
    return i18n.tr(key, lang) if key else (raw or i18n.tr("bilinmiyor", lang))


def _sensor_tr(val, lang=i18n.DEFAULT_LANG):
    if val is True:
        return i18n.tr("dolu", lang)
    if val is False:
        return i18n.tr("boş", lang)
    return i18n.tr("yok", lang)


def _format_mmss(seconds):
    """Format seconds as m:ss (e.g., 1:00, 0:07), minimum 0:01."""
    seconds = max(1, int(math.ceil(seconds)))
    minutes = seconds // 60
    secs = seconds % 60
    return "%d:%02d" % (minutes, secs)


def _rfid_label(slot_data, lang=i18n.DEFAULT_LANG):
    rfid = slot_data.get("rfid", False)
    if rfid is True or rfid == 2:
        parts = [i18n.tr("okundu", lang)]
        if slot_data.get("brand"):
            parts.append(str(slot_data["brand"]))
        if slot_data.get("sku"):
            parts.append("(%s)" % slot_data["sku"])
        return " · ".join(parts[:2]) + (" " + parts[2] if len(parts) > 2 else "")
    if rfid == 3:
        return i18n.tr("okunuyor", lang)
    if rfid == 1:
        return i18n.tr("okunamadı", lang)
    return i18n.tr("etiket yok", lang)


def _open_ace_limits(app):
    app.limit_page = 4
    app.open("limits")


def _dryer_temp_limit(app, ace):
    """Return configured max_dryer_temperature for the active ACE instance."""
    try:
        return int(float(limits.ace_instance_value(app.setting("ace", "max_dryer_temperature", 60),
                                                   ace.get("instance", 0), 60)))
    except (TypeError, ValueError):
        return 60


def _start_drying(app, temp, minutes, limit):
    """Clamp drying temperature against limit and send ACE_START_DRYING."""
    app._dryer_stopped_ts = None  # Clear cooling message when new drying starts
    clamped = int(round(clamp_dryer_temperature(temp, limit)))
    return app.ace_gcode("ACE_START_DRYING TEMP=%d DURATION=%d" % (clamped, minutes), app.tr("Kurutma başladı"))


def dry_presets_page(app, arg):
    """Full list of material drying presets reachable from the Spools dryer card."""
    if not app.ace_connected:
        return [_ace_disconnected_card(app)]
    ace, dry = dryer(app)
    drying = dry.get("status") == "drying"
    stopping_dry = app.ace_stopping_kind() == "drying"
    limit = _dryer_temp_limit(app, ace)

    def start_and_back(temp, minutes):
        if _start_drying(app, temp, minutes, limit):
            app.back()

    preset_rows = [
        Row(48, [Button(
            app.tr(name),
            app.when(app.ready and not drying and not stopping_dry and temp <= limit,
                     lambda t=temp, m=minutes: start_and_back(t, m)),
            icon="thermo",
            align="left",
            sub="%d°C · %s" % (temp, duration(minutes * 60, app.lang)),
        )])
        for name, temp, minutes in DRY_PRESETS
    ]
    return [Card(
        [section(app.tr("Kurutma"), app.tr("En çok %d°C") % limit, color="muted")] + preset_rows,
        pad=8, gap=5,
    )]


def filament(app):
    if not app.ace_connected:
        ext = app.spool_info("external")
        rem_txt = ("%.1f g · %.1f m" % (ext["remaining_g"], ext["remaining_m"])) if ext["configured"] else app.tr("Ayarlanmadı")
        rem_col = "danger_text" if ext["low"] else ("text" if ext["configured"] else "muted")
        rows = [_ace_disconnected_card(app)]
        ext_rows = [
            section(app.tr("Harici makara"), ext["material"], color="text"),
            Row(23, [Label(app.tr("Kalan"), size="small", color="muted", weight=0.7),
                     Label(rem_txt, size="med", color=rem_col, align="right", weight=1.5)]),
        ]
        if ext["configured"] and ext["initial_mm"] > 0:
            ext_rows.append(Row(8, [Bar(ext["fraction"], "danger" if ext["low"] else "accent")]))
        ext_rows.append(Row(44, [Button(app.tr("Miktar ayarla"), lambda: app.open("spool", "external"), "primary", icon="spool", align="left")]))
        rows.append(Card(ext_rows, pad=8, gap=4))
        rows.append(Row(44, [Button(app.tr("Elle yükle"), app.when(app.idle, lambda: app.start_wizard("load")), "card", icon="load", align="left"),
                             Button(app.tr("Elle çıkar"), app.when(app.idle, lambda: app.start_wizard("unload")), "card", icon="unload", align="left")]))
        return rows

    ace, dry = dryer(app)
    active = app.get("ace_state", "current_index", -1)
    slots = [_ace_slot_button(app, slot, active) for slot in _slots(ace)]
    ops = app.ace_active_slot_ops()
    fa = ace.get("feed_assist_slot", -1)
    if ops:
        s0, st0 = ops[0]
        header_right = app.tr("Y%d: %s") % (s0["index"] + 1, _slot_status_tr(st0, app.lang))
        header_col = "warn"
    elif active >= 0:
        header_right = app.tr("aktif: T%d") % active
        header_col = "muted"
    elif fa >= 0:
        header_right = app.tr("Assist: Y%d") % (fa + 1)
        header_col = "active_text"
    else:
        header_right = app.tr("aktif: %s") % app.tr("yok")
        header_col = "muted"
    rows = [
        Card([section("ACE Pro", header_right, color=header_col),
              Row(44, slots[:2]),
              Row(44, slots[2:4])], pad=6, gap=4),
    ]

    drying = dry.get("status") == "drying"
    stopping_dry = app.ace_stopping_kind() == "drying"
    limit = _dryer_temp_limit(app, ace)
    remain_sec = _dryer_remain_seconds(dry)
    dryer_error = ace.get("dryer_error")
    if hasattr(app, "_update_dryer_cooling_locked"):
        app._update_dryer_cooling_locked()
    # Check if dryer stopped recently to show cooling countdown
    cooling_msg = False
    cooling_remaining = 0
    if app._dryer_stopped_ts is not None:
        elapsed = app._clock() - app._dryer_stopped_ts
        cooldown = app.dryer_cooldown_s()
        if elapsed < cooldown:
            cooling_msg = True
            cooling_remaining = cooldown - elapsed
        else:
            app._dryer_stopped_ts = None  # Clear after cooldown

    if drying or stopping_dry:
        fan_rpm = ace.get("fan_speed")
        fan_txt = "%s rpm" % fan_rpm if fan_rpm else ""
        dry_title = app.tr("Durduruluyor…") if stopping_dry else app.tr("Kurutuyor")
        dryer_rows = [
            Row(23, [Label(dry_title, size="med", color="warn", weight=1.1),
                     Label("%s° / %s°C" % (ace.get("temp", "?"), dry.get("target_temp")),
                           size="med", color="text", align="right")]),
            Row(19, [Label(app.tr("Kalan %s") % duration(remain_sec, app.lang), size="small", color="muted", weight=1.3),
                     Label(fan_txt, size="small", color="tertiary", align="right", weight=0.9)]),
            Row(6, [Bar(_dryer_progress(dry, remain_sec), "warn")]),
        ]
    elif cooling_msg:
        countdown_txt = _format_mmss(cooling_remaining)
        dryer_rows = [
            Row(23, [Label(app.tr("Kurutucu %s sonra duracak") % countdown_txt, size="small", color="warn", weight=3.8),
                     Label("%s°C" % ace.get("temp", "?"), size="med", color="text", align="right", weight=1.0)]),
            Row(19, [Label(app.tr("Kurutma kapalı"), size="small", color="muted", weight=1.4),
                     Label(app.tr("En çok %d°C") % limit, size="small", color="tertiary", align="right", weight=1.0)]),
        ]
        if not dryer_error:
            dryer_rows.append(Row(19, [Label(app.tr("Isıtıcı plakası güvenlik için soğutuluyor"), size="small", color="tertiary", weight=1.0)]))
    else:
        hum = ace.get("humidity")
        right_meta = (app.tr("Nem %s%%") % hum) if hum is not None else _conn_state_tr(ace.get("connection_state"), app.lang)
        dryer_rows = [
            Row(23, [Label(app.tr("Kurutma kapalı"), size="med", color="muted", weight=1.4),
                     Label("%s°C" % ace.get("temp", "?"), size="med", color="text", align="right")]),
            Row(19, [Label(app.tr("En çok %d°C") % limit, size="small", color="tertiary", weight=1.1),
                     Label(right_meta, size="small", color="tertiary", align="right", weight=1.0)]),
        ]

    if dryer_error:
        dryer_rows.insert(1, Row(23, [Label(app.tr("Kurutucu hatası: %s") % dryer_error, size="small", color="danger", weight=1.5)]))

    def custom():
        app.keypad(app.tr("Kurutma sıcaklığı (en çok %d°)") % limit, min(30, limit), limit, lambda temp: app.keypad(
            app.tr("Süre (dakika)"), 10, 1440, lambda minutes: _start_drying(app, temp, minutes, limit), digits=4))

    preset_btns = [
        Button(app.tr(name), app.when(app.ready and not drying and not stopping_dry and temp <= limit,
                                      lambda t=temp, m=minutes: _start_drying(app, t, m, limit)),
               icon="thermo", weight=1.0)
        for idx, (name, temp, minutes) in enumerate(DRY_PRESETS[:2])
    ]
    preset_btns.append(Button(app.tr("Daha fazla"), lambda: app.open("dry_presets"), weight=1.0))
    rows.append(Card(dryer_rows + [
        Row(44, preset_btns),
        Row(44, [Button(app.tr("Özel"), app.when(app.ready and not drying and not stopping_dry, custom), icon="edit", align="left"),
                 Button(app.tr("Durduruluyor…") if stopping_dry else app.tr("Durdur"),
                        app.when(app.ready and drying and not stopping_dry, lambda: app.ace_gcode("ACE_STOP_DRYING")),
                        "outline_danger", icon="stop", align="left")]),
    ], pad=6, gap=3))
    rows.append(Row(44, [Button(app.tr("ACE Pro ayarları"), lambda: app.open("ace"), "card",
                                icon="settings", align="left", right_icon="next")]))
    return [Card(rows, color="bg", border=None, radius=0, pad=0, gap=5)]


def spool_page(app, target):
    key = "external" if target in (None, "external") else str(target)
    sp = app.spool_info(key)
    mat = sp["material"]
    density = tracker.material_density(mat)
    title_txt = app.tr("Harici makara") if key == "external" else (app.tr("Yuva %d") % (int(key) + 1))

    if sp["configured"]:
        rem_txt = "%.1f g · %.1f m" % (sp["remaining_g"], sp["remaining_m"])
        init_txt = "%.0f g · %.1f m" % (sp["initial_g"], sp["initial_m"])
    else:
        rem_txt = app.tr("Ayarlanmadı")
        init_txt = "-"

    def enter_grams():
        app.keypad(
            app.tr("Başlangıç miktarı (g)"),
            1, 10000,
            lambda val: app.set_spool_amount(key, val, unit="g", material=mat),
            digits=5,
        )

    def enter_meters():
        app.keypad(
            app.tr("Başlangıç uzunluğu (m)"),
            1, 5000,
            lambda val: app.set_spool_amount(key, val, unit="m", material=mat),
            digits=4,
        )

    def enter_thresh():
        app.keypad(
            app.tr("Az filament eşiği (g)"),
            0, 2000,
            lambda val: app.set_low_filament_threshold(val),
            digits=4,
        )

    status_rows = [
        section(title_txt, "%s (%.2f g/cm3)" % (mat, density), color="text", w1=0.9, w2=1.3),
        Row(23, [Label(app.tr("Kalan"), size="small", color="muted", weight=0.8),
                 Label(rem_txt, size="med", color="danger_text" if sp["low"] else "text", align="right", weight=1.4)]),
        Row(19, [Label(app.tr("Başlangıç"), size="small", color="muted", weight=0.8),
                 Label(init_txt, size="small", color="tertiary", align="right", weight=1.4)]),
    ]
    if sp["configured"] and sp["initial_mm"] > 0:
        status_rows.append(Row(8, [Bar(sp["fraction"], "danger" if sp["low"] else "accent")]))

    rows = [Card(status_rows, pad=6, gap=3)]
    if key == "external":
        rows.append(Card([
            Row(19, [Label(app.tr("Malzeme"), size="small", color="muted")]),
            Row(40, [Button(m, lambda m=m: app.set_spool_material(key, m),
                            "active" if m == mat else "normal") for m in tracker.SPOOL_MATERIALS]),
        ], pad=6, gap=3))

    rows.append(Card([
        section(app.tr("Miktar ayarla")),
        Row(44, [
            Button(app.tr("Gram gir"), enter_grams, "primary"),
            Button(app.tr("Metre gir"), enter_meters),
            Button("1000 g", lambda: app.set_spool_amount(key, 1000, unit="g", material=mat), weight=0.85),
        ]),
        Row(44, [
            Button(app.tr("Eşik: %.0f g") % sp["threshold_g"], enter_thresh, weight=1.3),
            Button(app.tr("Sıfırla"), app.when(sp["configured"], lambda: app.clear_spool(key)), "outline_danger", weight=0.8),
        ]),
    ], pad=6, gap=4))
    return [Card(rows, color="bg", border=None, radius=0, pad=0, gap=4)]
