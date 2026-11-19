"""ACE Pro slot configuration, manual feed and ACE settings sub-pages."""

from .screens_ace import (
    _ace_disconnected_card, _ace_speeds, _conn_state_tr, _dryer_remain_seconds, _open_ace_limits,
    _rfid_label, _sensor_tr, _slot_status_tr, _slots, _tangle_protection, dryer,
)
from .screens_common import SLOT_COLORS, SLOT_COLOR_NAMES, SLOT_MATERIALS, duration, section, segmented
from .widgets import Button, Card, Col, Label, Row


def slot(app, index):
    if not app.ace_connected:
        return [_ace_disconnected_card(app)]
    ace, _ = dryer(app)
    data = next((s for s in _slots(ace) if s.get("index") == index), None)
    if data is None:
        return [Row(60, [Label(app.tr("Yuva bilgisi yok"), color="muted", align="center")])]
    tool = data.get("tool", index)
    raw_st = str(data.get("status") or "").strip().lower()
    empty = raw_st == "empty"
    slot_busy = raw_st in app.ACE_ACTIVE_SLOT_STATES
    ace_busy = app.ace_busy()
    fa_active = ace.get("feed_assist_slot", -1) == index
    material = str(data.get("material") or "PLA").strip() or "PLA"
    raw_color = data.get("color") or ()
    # an empty slot reports [0, 0, 0]: start from white; RGBA values lose their alpha
    color = (245, 245, 245) if (empty or len(raw_color) < 3) else tuple(int(c) for c in raw_color[:3])
    temp = data.get("temp") or dict(SLOT_MATERIALS).get(material, 210)
    selected_color_name = app.tr(SLOT_COLOR_NAMES.get(color, "Özel")) if not empty else ""

    def save(material=material, color=color, temp=temp):
        mat_arg = str(material).strip().replace(" ", "_") or "PLA"  # a space would end the G-code parameter
        rgb = tuple(int(c) for c in color[:3])
        app.ace_gcode("ACE_SET_SLOT T=%d COLOR=%d,%d,%d MATERIAL=%s TEMP=%d" % ((tool,) + rgb + (mat_arg, int(temp))))

    def swatch_cell(c):
        cname = app.tr(SLOT_COLOR_NAMES.get(c, ""))
        active = not empty and c == color
        return Col([
            Row(44, [Button("", app.when(app.ready and not ace_busy, lambda c=c: save(color=c)), swatch=c, ring=active)]),
            Row(18, [Label(cname, size="tab", color="text" if active else "muted", align="center")]),
        ], gap=0)

    locked = bool(app.heating_lock)  # ACE change/unload macros heat the nozzle (M109)
    sp = app.spool_info(str(index))
    if sp["configured"]:
        spool_lbl = app.tr("Kalan: %.0fg") % sp["remaining_g"]
    else:
        spool_lbl = app.tr("Kalan: ayarsız")

    if slot_busy:
        sub_line = _slot_status_tr(raw_st, app.lang)
    elif empty:
        sub_line = app.tr("boş")
    else:
        sub_line = "%s, %s°" % (material, temp)
        if fa_active:
            sub_line += " · Assist"

    return [Card([
        Card([
            Row(44, [Button("", None, "ghost", dot=None if empty else color, icon="add" if empty else None, weight=0.38),
                     Col([Row(23, [Label(app.tr("Yuva %d") % (index + 1), size="med")]),
                          Row(19, [Label(sub_line, size="small", color="warn" if slot_busy else "muted")])],
                         weight=1.7, gap=1),
                     Button(app.tr("Ayrıntı"), lambda: app.open("slot_detail", index), "normal",
                            icon="info", align="left", weight=1.1)]),
            Row(44, [Button(app.tr("Yükle"), app.when(app.idle and not empty and not ace_busy and not locked, lambda: app.ace_gcode(
                         "ACE_CHANGE_TOOL TOOL=%d" % tool, app.tr("Filament yüklendi"))), "primary",
                            icon="lock" if locked else "load", align="left"),
                     Button(app.tr("Geri çek"), app.when(app.idle and not empty and not ace_busy and not fa_active and not locked, lambda: app.ace_gcode(
                         "ACE_FULL_UNLOAD TOOL=%d" % tool, app.tr("Filament geri çekildi"))),
                            icon="lock" if locked else "unload", align="left")]),
        ], pad=4, gap=2),
        Card([
            Row(40, [Button(name, app.when(app.ready and not ace_busy, lambda n=name, t=t: save(material=n, temp=t)),
                            "active" if not empty and name == material else "normal") for name, t in SLOT_MATERIALS]),
            Row(19, [Label(app.tr("Renk"), size="small", color="muted"),
                     Label(selected_color_name, size="small", color="text", align="right")]),
            Row(62, [swatch_cell(c) for c in SLOT_COLORS[:4]]),
            Row(62, [swatch_cell(c) for c in SLOT_COLORS[4:]]),
        ], pad=4, gap=2),
        Row(44, [
            Button(spool_lbl, lambda: app.open("spool", index), "card", icon="spool", align="left"),
            Button(app.tr("Boş işaretle"), app.when(app.ready and not empty and not ace_busy and not fa_active, lambda: app.ace_gcode(
                "ACE_SET_SLOT T=%d EMPTY=1" % tool)), "outline_danger", icon="close", align="left"),
        ]),
    ], color="bg", border=None, radius=0, pad=0, gap=3)]


def slot_detail(app, index):
    if not app.ace_connected:
        return [_ace_disconnected_card(app)]
    ace, _ = dryer(app)
    data = next((s for s in _slots(ace) if s.get("index") == index), None)
    if data is None:
        return [Row(60, [Label(app.tr("Yuva bilgisi yok"), color="muted", align="center")])]
    tool = data.get("tool", index)
    raw_st = str(data.get("status") or "").strip().lower()
    slot_busy = raw_st in app.ACE_ACTIVE_SLOT_STATES
    ace_busy = app.ace_busy()
    fa_active = ace.get("feed_assist_slot", -1) == index

    ext_t = data.get("extruder_temp") or {}
    bed_t = data.get("hotbed_temp") or {}
    if ext_t.get("min") or ext_t.get("max"):
        temp_label = app.tr("Isı (N/T)")
        temp_info = "%s-%s° / %s-%s°" % (
            ext_t.get("min", "-"), ext_t.get("max", "-"),
            bed_t.get("min", "-"), bed_t.get("max", "-"))
    else:
        temp_label = app.tr("Önerilen")
        temp_info = (app.tr("Kayıtlı ısı: %s°C") % data.get("temp")) if data.get("temp") else "-"

    diam = data.get("diameter")
    cur_amt = data.get("current")
    tot_amt = data.get("total")
    if diam is not None or tot_amt is not None:
        parts = []
        if diam is not None:
            parts.append("%s mm" % diam)
        if tot_amt is not None:
            parts.append("%s/%s" % (cur_amt if cur_amt is not None else "-", tot_amt))
        extra_info = " · ".join(parts)
    else:
        extra_info = app.tr("T%d · Feed assist: %s") % (tool, app.tr("açık") if fa_active else app.tr("kapalı"))

    feed_spd, retract_spd = _ace_speeds(app, ace)
    can_move = app.idle and not ace_busy and not fa_active
    stopping_kind = app.ace_stopping_kind()
    stopping_feed = stopping_kind == "feed"
    stopping_retract = stopping_kind == "retract"
    can_stop_feed = app.ready and not stopping_feed and (raw_st == "feeding" or not ace_busy)
    can_stop_retract = app.ready and not stopping_retract and (raw_st == "unwinding" or not ace_busy)
    step = int(app.ace_feed_step or 50)

    def set_step(value):
        app.ace_feed_step = int(value)
        app.dirty = True

    def pick_custom_step():
        app.keypad(app.tr("Besleme uzunluğu (mm)"), 5, 2000, lambda length: set_step(length), digits=4)

    def set_feed_speed():
        def apply(speed):
            if app.ace_gcode("ACE_SET_FEED_SPEED T=%d SPEED=%d" % (tool, speed), app.tr("Sürme hızı %d mm/s") % speed):
                app.ace_speeds["feed"] = speed  # the driver does not report it back; remember what was set

        app.keypad(app.tr("Sürme hızı (mm/s)"), 5, 150, apply, digits=3)

    def set_retract_speed():
        def apply(speed):
            if app.ace_gcode("ACE_SET_RETRACT_SPEED T=%d SPEED=%d" % (tool, speed), app.tr("Çekme hızı %d mm/s") % speed):
                app.ace_speeds["retract"] = speed

        app.keypad(app.tr("Çekme hızı (mm/s)"), 5, 150, apply, digits=3)

    def toggle_fa():
        if fa_active:
            app.ace_gcode("ACE_DISABLE_FEED_ASSIST T=%d" % tool, app.tr("Feed assist kapatıldı"))
        else:
            app.ace_gcode("ACE_ENABLE_FEED_ASSIST T=%d" % tool, app.tr("Feed assist açıldı"))

    step_btns = [
        Button("%d mm" % s, lambda v=s: set_step(v),
               "segment" if step == s else "segment_off",
               weight=1.10 if s == 100 else 0.94)
        for s in (10, 50, 100)
    ]
    custom_lbl = ("%d mm" % step) if step not in (10, 50, 100) else app.tr("Özel")
    step_btns.append(Button(custom_lbl, pick_custom_step,
                            "segment" if step not in (10, 50, 100) else "segment_off", weight=1.02))

    rfid_txt = _rfid_label(data, app.lang)
    rfid_sz = "tab" if len(rfid_txt) > 22 else "small"
    return [Card([
        Card([
            section(app.tr("Yuva %d (T%d)") % (index + 1, tool), _slot_status_tr(raw_st, app.lang),
                    color="warn" if slot_busy else "muted"),
            Row(19, [Label("RFID", size="small", color="muted", weight=0.36),
                     Label(rfid_txt, size=rfid_sz, color="text", align="right", weight=1.95)]),
            Row(19, [Label(temp_label, size="small", color="muted", weight=0.75),
                     Label(temp_info, size="small", color="tertiary", align="right", weight=1.45)]),
            Row(19, [Label(app.tr("Ölçü"), size="small", color="muted", weight=0.6),
                     Label(extra_info, size="small", color="tertiary", align="right", weight=1.6)]),
        ], pad=4, gap=1),
        Card([
            section(app.tr("Manuel besleme"), "%s / %s mm/s" % (feed_spd, retract_spd)),
            segmented(step_btns),
            Row(44, [
                Button(app.tr("Besle"), app.when(can_move, lambda: app.ace_gcode(
                    "ACE_FEED T=%d LENGTH=%d" % (tool, step), app.tr("%d mm sürüldü") % step)), icon="load", align="left"),
                Button(app.tr("Geri çek"), app.when(can_move, lambda: app.ace_gcode(
                    "ACE_RETRACT T=%d LENGTH=%d" % (tool, step), app.tr("%d mm çekildi") % step)), icon="unload", align="left"),
            ]),
            Row(44, [
                Button(app.tr("Durduruluyor…") if stopping_feed else app.tr("Besleme dur"),
                       app.when(can_stop_feed, lambda: app.ace_gcode("ACE_STOP_FEED T=%d" % tool)),
                       "outline_danger", icon="stop", align="left"),
                Button(app.tr("Durduruluyor…") if stopping_retract else app.tr("Çekme dur"),
                       app.when(can_stop_retract, lambda: app.ace_gcode("ACE_STOP_RETRACT T=%d" % tool)),
                       "outline_danger", icon="stop", align="left"),
            ]),
        ], pad=4, gap=2),
        Card([
            Row(44, [Label("Feed assist", size="small", color="muted", weight=1.1),
                     Button(app.tr("açık") if fa_active else app.tr("kapalı"), app.when(app.ready and not slot_busy, toggle_fa),
                            "active" if fa_active else "normal", weight=1.0)]),
            Row(44, [Button(app.tr("Sürme hızı"), app.when(app.ready and not slot_busy, set_feed_speed), icon="speed", align="left"),
                     Button(app.tr("Çekme hızı"), app.when(app.ready and not slot_busy, set_retract_speed), icon="speed", align="left")]),
        ], pad=4, gap=2),
    ], color="bg", border=None, radius=0, pad=0, gap=2)]


def _ace_status_view(app, ace, dry):
    fw = ace.get("firmware") or "-"
    boot = ace.get("boot_firmware") or "-"
    fw_txt = ("%s (%s)" % (fw, boot)) if boot != "-" else fw
    fa = ace.get("feed_assist_slot", -1)
    fa_count = ace.get("feed_assist_count", 0)
    fa_txt = (app.tr("Yuva %d (%s)") % (fa + 1, fa_count)) if fa >= 0 else ((app.tr("kapalı (%s)") % fa_count) if fa_count else app.tr("kapalı"))
    drying = dry.get("status") == "drying"
    remain_sec = _dryer_remain_seconds(dry)
    dry_txt = (app.tr("aktif · %s° · %s") % (dry.get("target_temp", 0), duration(remain_sec, app.lang))) if drying else app.tr("kapalı")
    hum = ace.get("humidity")
    cab_txt = "%s°C · %s rpm%s" % (ace.get("temp", "?"), ace.get("fan_speed", 0),
                                   (" · %s%%" % hum) if hum is not None else "")
    return [
        Card([
            section(app.tr("Donanım"), app.tr("birim #%s") % ace.get("instance", 0)),
            Row(19, [Label(app.tr("Model"), size="small", color="muted", weight=0.55),
                     Label(ace.get("model") or "ACE Pro", size="small", align="right", weight=1.75)]),
            Row(19, [Label(app.tr("Yazılım"), size="small", color="muted", weight=0.65),
                     Label(fw_txt, size="small", align="right", weight=1.55)]),
            Row(19, [Label(app.tr("Bağlantı"), size="small", color="muted", weight=0.7),
                     Label("%s · %s" % (_conn_state_tr(ace.get("connection_state"), app.lang), ace.get("protocol") or "-"),
                           size="small", align="right", weight=1.5)]),
            Row(19, [Label(app.tr("USB port"), size="small", color="muted", weight=0.7),
                     Label(ace.get("usb_port") or "-", size="small", align="right", weight=1.5)]),
            Row(19, [Label(app.tr("Kabin / Fan"), size="small", color="muted", weight=0.8),
                     Label(cab_txt, size="small", align="right", weight=1.4)]),
        ], pad=5, gap=1),
        Card([
            section(app.tr("Sensörler")),
            Row(19, [Label(app.tr("Nozul sensörü"), size="small", color="muted", weight=1.1),
                     Label(_sensor_tr(app.get("ace_state", "toolhead_sensor", None), app.lang), size="small", align="right", weight=0.9)]),
            Row(19, [Label(app.tr("RDM (ayırıcı)"), size="small", color="muted", weight=1.1),
                     Label(_sensor_tr(app.get("ace_state", "rdm_sensor", None), app.lang), size="small", align="right", weight=0.9)]),
            Row(19, [Label("Feed assist", size="small", color="muted", weight=0.8),
                     Label(fa_txt, size="small", align="right", weight=1.2)]),
            Row(19, [Label(app.tr("Kurutucu"), size="small", color="muted", weight=0.65),
                     Label(dry_txt, size="small", align="right", weight=1.55)]),
        ], pad=5, gap=1),
        Row(44, [Button(app.tr("Yenile"), app.when(app.ready, lambda: app.ace_gcode("ACE_GET_STATUS VERBOSE=1", app.tr("Durum sorgulandı"))), icon="refresh", align="left", weight=0.7),
                 Button(app.tr("Yeniden bağlan"), app.when(app.ready, lambda: app.ace_gcode("ACE_RECONNECT", app.tr("ACE yeniden bağlanıyor"), require_connected=False)), "card", icon="refresh", align="left", weight=1.3)]),
    ]


def _ace_features_view(app, ace):
    rfid_sync = bool(ace.get("rfid_sync_enabled", True))
    endless = bool(app.get("ace_state", "endless_spool_enabled", False))
    mode = str(app.get("ace_state", "endless_spool_match_mode", "exact")).lower()
    ace_busy = app.ace_busy()

    def toggle_rfid():
        cmd = "ACE_DISABLE_RFID_SYNC" if rfid_sync else "ACE_ENABLE_RFID_SYNC"
        msg = "RFID eşitleme kapatıldı" if rfid_sync else "RFID eşitleme açıldı"
        app.ace_gcode(cmd, app.tr(msg))

    def toggle_endless():
        cmd = "ACE_DISABLE_ENDLESS_SPOOL" if endless else "ACE_ENABLE_ENDLESS_SPOOL"
        msg = "Sonsuz makara kapatıldı" if endless else "Sonsuz makara açıldı"
        app.ace_gcode(cmd, app.tr(msg))

    tp_present, tp_on, tp_threshold = _tangle_protection(app, ace)

    def toggle_tangle():
        app.ace_gcode("ACE_TANGLE_PROTECTION ENABLE=%d" % (0 if tp_on else 1),
                      app.tr("Dolaşma koruması kapatıldı") if tp_on else app.tr("Dolaşma koruması açıldı"))

    thr_val = None
    if tp_threshold is not None:
        try:
            thr_val = int(round(float(tp_threshold)))
        except (TypeError, ValueError):
            thr_val = None

    def set_tangle_threshold():
        def apply(seconds):
            val = int(round(seconds))
            app.ace_gcode(
                "ACE_TANGLE_PROTECTION THRESHOLD=%d" % val,
                app.tr("Dolaşma eşiği %d sn") % val,
            )

        init_val = thr_val if (thr_val is not None and 2 <= thr_val <= 60) else None
        app.keypad(app.tr("Dolaşma eşiği (sn)"), 2, 60, apply, digits=2, initial=init_val)

    # the extra switch row would push the page over the tab bar: every row of this page gets a bit lower then
    switch_h, mode_h, limits_h = (34, 32, 40) if tp_present else (44, 36, 44)
    tangle_rows = []
    if tp_present:
        thr_lbl = (app.tr("%dsn") % thr_val) if thr_val is not None else "--"
        tangle_rows.append(Row(switch_h, [
            Label(app.tr("Dolaşma koruması"), size="small", color="muted", weight=1.75),
            Button(thr_lbl, app.when(app.ready and not ace_busy, set_tangle_threshold),
                   "normal", size="small", weight=0.55),
            Button(app.tr("açık") if tp_on else app.tr("kapalı"), app.when(app.ready and not ace_busy, toggle_tangle),
                   "active" if tp_on else "normal", weight=0.75)]))
    return [Card([
        Card([
            section(app.tr("Özellikler")),
            Row(switch_h, [Label(app.tr("RFID eşitleme"), size="small", color="muted", weight=1.2),
                     Button(app.tr("açık") if rfid_sync else app.tr("kapalı"), app.when(app.ready and not ace_busy, toggle_rfid),
                            "active" if rfid_sync else "normal", weight=0.9)]),
            Row(switch_h, [Label(app.tr("Sonsuz makara"), size="small", color="muted", weight=1.2),
                     Button(app.tr("açık") if endless else app.tr("kapalı"), app.when(app.ready and not ace_busy, toggle_endless),
                            "active" if endless else "normal", weight=0.9)]),
            *tangle_rows,
            Row(19, [Label(app.tr("Sonsuz makara eşleşme modu"), size="small", color="muted")]),
            Row(mode_h, [Button(app.tr(lbl), app.when(app.ready and not ace_busy, lambda m=m, l=lbl: app.ace_gcode(
                "ACE_SET_ENDLESS_SPOOL_MODE MODE=%s" % m, app.tr("Mod: %s") % app.tr(l))),
                "active" if mode == m else "normal")
                for lbl, m in (("Tam", "exact"), ("Malzeme", "material"), ("Sıradaki", "next"))]),
        ], pad=5, gap=2),
        Card([
            section(app.tr("Sürücü Ayarları")),
            Row(19, [Label(app.tr("Sürme / çekme hızı"), size="small", color="muted", weight=1.25),
                     Label("%s / %s mm/s" % _ace_speeds(app, ace), size="small", align="right", weight=0.95)]),
            Row(19, [Label(app.tr("Sürme / değişim"), size="small", color="muted", weight=1.15),
                     Label("%s / %s mm" % (app.setting("ace", "total_max_feeding_length", "-"),
                                          app.setting("ace", "toolchange_load_length", "-")),
                           size="small", align="right", weight=1.05)]),
            Row(limits_h, [Button(app.tr("ACE sınırlarını düzenle"), lambda: _open_ace_limits(app), "card",
                            icon="lock", align="left", right_icon="next")]),
        ], pad=5, gap=2),
    ], color="bg", border=None, radius=0, pad=0, gap=3)]


def _ace_ops_view(app, ace):
    fa = ace.get("feed_assist_slot", -1)
    ace_busy = app.ace_busy()

    def confirm_unload_all():
        app.confirm(app.tr("Tüm dolu yuvalardaki filamentler geri çekilsin mi?"),
                    lambda: app.ace_gcode("ACE_FULL_UNLOAD TOOL=ALL", app.tr("Tüm yuvalar boşaltıldı")), "danger")

    def confirm_reset_toolhead():
        app.confirm(app.tr("Aktif araç ve feed assist durumu sıfırlansın mı?"),
                    lambda: app.ace_gcode("ACE_RESET_ACTIVE_TOOLHEAD", app.tr("Aktif araç sıfırlandı")), "danger")

    def confirm_reset_inv():
        app.confirm(app.tr("Kayıtlı yuva envanteri sıfırlansın mı?"),
                    lambda: app.ace_gcode("ACE_RESET_PERSISTENT_INVENTORY", app.tr("Envanter sıfırlandı")), "danger")

    stopping_assist = app.ace_stopping_kind() == "assist"
    locked = bool(app.heating_lock)  # these macros heat the nozzle (M109)
    return [
        Card([
            section(app.tr("Birim İşlemleri")),
            Row(44, [Button(app.tr("Akıllı yükle"), app.when(app.idle and not ace_busy and not locked, lambda: app.ace_gcode("ACE_SMART_LOAD", app.tr("Akıllı yükleme bitti"))), icon="lock" if locked else "load", align="left"),
                     Button(app.tr("Akıllı çıkar"), app.when(app.idle and not ace_busy and not locked, lambda: app.ace_gcode("ACE_SMART_UNLOAD", app.tr("Akıllı çıkarma bitti"))), icon="lock" if locked else "unload", align="left")]),
            Row(44, [Button(app.tr("Tümünü çıkar"), app.when(app.idle and not ace_busy and not locked, confirm_unload_all), "outline_danger", icon="lock" if locked else "unload", align="left"),
                     Button(app.tr("Durduruluyor…") if stopping_assist else app.tr("Assist kapat"),
                            app.when(app.ready and fa >= 0 and not ace_busy and not stopping_assist,
                                     lambda: app.ace_gcode("ACE_DISABLE_FEED_ASSIST INSTANCE=%d INDEX=%d" % (ace.get("instance", 0), fa), app.tr("Feed assist kapatıldı"))), icon="stop", align="left")]),
        ], pad=6, gap=4),
        Card([
            section(app.tr("Envanter ve Durum")),
            Row(44, [Button(app.tr("Kaydet"), app.when(app.ready and not ace_busy, lambda: app.ace_gcode("ACE_SAVE_INVENTORY", app.tr("Envanter kaydedildi"))), icon="check", align="left"),
                     Button(app.tr("Aracı sıfırla"), app.when(app.idle and not ace_busy, confirm_reset_toolhead), "outline_danger", icon="refresh", align="left")]),
            Row(44, [Button(app.tr("Envanter sil"), app.when(app.idle and not ace_busy, confirm_reset_inv), "outline_danger", icon="close", align="left"),
                     Button(app.tr("Diske yaz"), app.when(app.ready and not ace_busy, lambda: app.ace_gcode("ACE_FLUSH", app.tr("Durum diske yazıldı"))), icon="check", align="left")]),
        ], pad=6, gap=4),
    ]


def ace_page(app, arg):
    if not app.ace_connected:
        return [_ace_disconnected_card(app)]
    ace, dry = dryer(app)

    def seg(key):
        def select():
            app.ace_seg = key
            app.dirty = True
        return select

    rows = [segmented([Button(app.tr(title), seg(key), "segment" if app.ace_seg == key else "segment_off")
                       for title, key in (("Durum", "status"), ("Özellik", "features"), ("İşlem", "ops"))])]
    if app.ace_seg == "features":
        return rows + _ace_features_view(app, ace)
    if app.ace_seg == "ops":
        return rows + _ace_ops_view(app, ace)
    return rows + _ace_status_view(app, ace, dry)
