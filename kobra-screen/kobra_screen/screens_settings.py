"""Settings tab and system, language, calibration, Wi-Fi, display and sound pages."""

import math

from . import i18n
from .screens_common import BRIGHTNESS_STEPS, SLEEP_TIMEOUTS, WIFI_PER_PAGE, blank, light_buttons, section
from .widgets import Button, Card, Icon, Label, Row


def settings(app):
    state = app.state
    auto = int(app.get("leviq3", "auto_zoffset_on_off"))
    has_leviq = bool(state.status.get("leviq3"))
    # Read kobra-screen version from app.json or .open-kobra-version
    screen_version = "?"
    if app.app_root:
        import json
        import os
        candidates = [
            os.path.join(app.app_root, "app.json"),
            "/useremain/home/rinkhals/.open-kobra-version",
        ]
        for path in candidates:
            try:
                if path.endswith(".json"):
                    with open(path) as f:
                        screen_version = json.load(f).get("version", "?")
                else:
                    with open(path) as f:
                        screen_version = f.read().strip() or "?"
                if screen_version != "?":
                    break
            except (OSError, ValueError, KeyError):
                pass
    header = Card([
        Row(19, [
            Label((state.hostname or "Kobra 3") + " · " + screen_version,
                  size="small", color="muted", weight=1.35),
            Label("Z %.3f mm" % app.get("leviq3", "z_offset"),
                  size="small", color="tertiary", align="right", weight=0.82),
        ]),
    ], pad=4, gap=0)
    items = [
        Row(44, [Button(app.tr("Otomatik Z-offset"), app.when(
            app.ready and has_leviq, lambda: app.gcode("LEVIQ_AUTO_ZOFFSET_ON_OFF ON_OFF=%d" % (0 if auto else 1))),
            "card", icon="zoffset", align="left", toggle=bool(auto))]),
        Row(44, [
            Button(app.tr("Ekran"), lambda: app.open("display"), "card", icon="light", align="left"),
            Button(app.tr("Ses"), lambda: app.open("sound"), "card", icon="info", align="left"),
        ]),
        Row(44, [
            Button(app.tr("Wi-Fi"), lambda: app.open("wifi"), "card", icon="wifi", align="left"),
            Button(app.tr("Sınırlar"), lambda: app.open("limits"), "card", icon="lock", align="left"),
        ]),
        light_buttons(app, height=44),
        Row(44, [Button(app.tr("Dil / Language"), lambda: app.open("language"),
            "card", icon="extension", align="left", right_icon="next")]),
        Row(44, [Button(app.tr("Ekranı kalibre et"), lambda: app.open_touch_calibration(),
            "card", icon="move", align="left", right_icon="next")]),
        Row(44, [
            Button(app.tr("Güncelleme"), lambda: app.open("updates"), "card", icon="refresh", align="left"),
            Button(app.tr("Sistem"), lambda: app.open("system"), "card", icon="settings", align="left"),
        ]),
    ]
    return [
        Card([header, Card(items, color="bg", border=None, radius=0, pad=0, gap=2)],
             color="bg", border=None, radius=0, pad=0, gap=3),
    ]


def system_page(app, arg):
    confirm_stock = lambda: app.confirm(
        app.tr("Kobra Screen ve Vanilla Klipper devre dışı bırakılıp yazıcı stok moda yeniden başlatılsın mı?"),
        app.return_to_stock,
        "primary",
    )
    locked = bool(app.heating_lock)
    ext_locked = bool(getattr(app, "extrusion_lock", False))

    def on_ext_toggle():
        if getattr(app, "extrusion_lock", False):
            app.confirm(
                app.tr("Hotend soğutma fanı takılı mı?"),
                lambda: app.set_extrusion_lock(False),
                "danger",
            )
        else:
            app.set_extrusion_lock(True)

    return [Card([
        Card([
            section(app.tr("Güvenlik"), app.tr("Açık") if (locked or ext_locked) else app.tr("Kapalı"),
                    color="warn" if (locked or ext_locked) else "muted"),
            Row(18, [Label(app.tr("Nozul ve tabla ısıtmayı engeller"), size="tab", color="tertiary")]),
            Row(42, [Button(app.tr("Isıtma kilidi"), lambda: app.toggle_heating_lock(),
                            "card", icon="lock", align="left", toggle=locked)]),
            Row(42, [Button(app.tr("Ekstrüzyon kilidi"), on_ext_toggle,
                            "card", icon="lock", align="left", toggle=ext_locked)]),
            Row(42, [Button(app.tr("Isıtmadan önce sor"),
                            lambda: app.set_heating_confirm_skip(not app.heating_confirm_skip),
                            "card", icon="warning", align="left", toggle=not app.heating_confirm_skip)]),
        ], pad=5, gap=2),
        Row(42, [Button(app.tr("Klipper'ı yeniden başlat"), lambda: app.confirm(
            app.tr("Klipper yeniden başlatılsın mı?"), lambda: app.do(app.restart_klipper, app.tr("Klipper yeniden başlatıldı"))),
            "card", icon="refresh", align="left", right_icon="next")]),
        Row(42, [Button(app.tr("Yazıcıyı yeniden başlat"), app.when(not app.printing, lambda: app.confirm(
            app.tr("Yazıcı yeniden başlatılsın mı?"), lambda: app.do(app.client.machine_reboot), "danger")),
            "outline_danger", icon="power", align="left")]),
        Row(42, [Button(app.tr("Stok moda dön"), app.when(not app.printing, confirm_stock),
            "card", icon="back", align="left")]),
    ], color="bg", border=None, radius=0, pad=0, gap=3)]


def language_page(app, arg):
    langs = i18n.available_languages()
    rows = [
        Card([section(app.tr("Geçerli dil"), i18n.language_name(app.lang), color="ok", w1=1.25, w2=0.85)], pad=10, gap=4),
    ]
    for code, name in langs:
        selected = (code == app.lang)
        rows.append(Row(44, [Button(
            name,
            lambda c=code: app.set_language(c),
            "active" if selected else "card",
            icon="check" if selected else None,
            align="left",
        )]))
    return rows


def touch_calibration_view(app):
    cal = app.touch_cal
    if cal.stage == "corners":
        name, _, _ = cal.targets[cal.step]
        return [
            blank(90),
            Card([
                section(app.tr("Köşe kalibrasyonu"), "%d / %d" % (cal.step + 1, len(cal.targets)), color="warn", w1=1.3, w2=0.8),
                Row(30, [Label(app.tr("%s hedefine dokunun") % app.tr(name), size="med", align="center")]),
                Row(40, [Label(app.tr("Sarı halkanın ortasına tek kez dokunun ve parmağınızı kaldırın."),
                               size="small", color="muted", align="center")]),
                Row(19, [Label(app.tr("30 sn dokunulmazsa iptal edilir"), size="small", color="tertiary", align="center")]),
            ], pad=12, gap=6),
        ]

    if cal.stage == "verify":
        return [
            Card([
                section(app.tr("Doğrulama"), app.tr("Orta hedef"), color="accent"),
                Row(70, [Label(app.tr("Yeni kalibrasyonu doğrulamak için ortadaki sarı hedefe dokunun."),
                               size="med", align="center")]),
                Row(19, [Label(app.tr("En çok %d px sapma kabul edilir") % round(cal.verify_threshold),
                               size="small", color="tertiary", align="center")]),
            ], pad=12, gap=6),
        ]

    if cal.stage == "confirm":
        rem = max(0, int(math.ceil(cal.deadline - app._clock()))) if cal.deadline > 0 else int(cal.confirm_timeout)
        cal_txt = "%d, %d, %d, %d" % tuple(cal.new_cal)
        err_txt = ("%.1f px" % cal.verify_error_px) if cal.verify_error_px is not None else "-"
        return [
            Card([
                section(app.tr("Yeni kalibrasyon"), app.tr("Test ediliyor"), color="ok"),
                Row(19, [Label(app.tr("Değerler"), size="small", color="muted", weight=0.6),
                         Label(cal_txt, size="small", align="right", weight=1.6)]),
                Row(19, [Label(app.tr("Eksen takası"), size="small", color="muted", weight=0.9),
                         Label(app.tr("Evet") if cal.new_swap else app.tr("Hayır"), size="small", align="right", weight=1.1)]),
                Row(19, [Label(app.tr("Merkez sapma"), size="small", color="muted", weight=1.0),
                         Label(err_txt, size="small", color="ok", align="right", weight=1.0)]),
            ], pad=10, gap=4),
            Card([
                Row(70, [Label(app.tr("Yeni ayar uygulanıyor. Kaydetmek için Onayla düğmesine dokunun."),
                               size="med", align="center")]),
                Row(24, [Label(app.tr("Otomatik geri dönüş: %d sn") % rem, size="med", color="warn", align="center")]),
            ], color="surface2", border="border_default", pad=10, gap=6),
            Row(50, [Button(app.tr("Onayla"), app.confirm_touch_calibration, "primary", icon="check")]),
            Row(44, [Button(app.tr("Vazgeç"), lambda: app.cancel_touch_calibration(False))]),
        ]

    # stage == "intro"
    cur_txt = "%d, %d, %d, %d" % tuple(cal.prev_cal)
    rows = [
        Card([
            section(app.tr("Mevcut ayar"), "Kobra 3" if not cal.prev_swap else app.tr("Takaslı")),
            Row(19, [Label(app.tr("Kalibrasyon"), size="small", color="muted", weight=0.8),
                     Label(cur_txt, size="small", align="right", weight=1.4)]),
            Row(19, [Label(app.tr("Eksen takası"), size="small", color="muted", weight=0.9),
                     Label(app.tr("Evet") if cal.prev_swap else app.tr("Hayır"), size="small", align="right", weight=1.1)]),
            Row(58, [Label(app.tr("4 köşedeki hedeflere ve doğrulama için orta hedefe sırayla dokunun."),
                           size="small", color="muted")]),
        ], pad=10, gap=4),
    ]
    if cal.error_msg:
        rows.append(Card([
            Row(44, [Icon("warning", "normal", "danger", weight=0.2),
                     Label(cal.error_msg, size="small", color="danger_text", weight=1.8)]),
        ], color="danger_bg", border="danger_border", pad=8))
    rows.append(Row(46, [Button(app.tr("Kalibrasyonu başlat"), app.start_touch_calibration, "primary", icon="zoffset")]))
    rows.append(Row(44, [Button(
        app.tr("Varsayılana dön"),
        lambda: app.confirm(app.tr("Varsayılan dokunmatik kalibrasyona dönülsün mü?"),
                            app.reset_touch_calibration_to_default, "danger"),
        "outline_danger",
        icon="refresh",
    )]))
    rows.append(Row(44, [Button(app.tr("Vazgeç"), lambda: app.cancel_touch_calibration(False))]))
    return rows


def _wifi_state_tr(st, scanning=False, lang=i18n.DEFAULT_LANG):
    if scanning:
        return i18n.tr("Taranıyor", lang), "warn"
    state = (st.state or "").upper()
    if state == "COMPLETED" and st.ssid:
        return i18n.tr("Bağlı", lang), "ok"
    if state in ("ASSOCIATING", "ASSOCIATED", "4WAY_HANDSHAKE", "GROUP_HANDSHAKE", "AUTHENTICATING"):
        return i18n.tr("Bağlanıyor", lang), "warn"
    if state == "SCANNING":
        return i18n.tr("Taranıyor", lang), "warn"
    if state == "UNAVAILABLE":
        return i18n.tr("Kapalı", lang), "danger"
    return i18n.tr("Bağlı değil", lang), "muted"


def _open_wifi_connect(app, ssid, flags):
    app.keyboard(
        app.tr("Şifre: %s") % ssid,
        lambda pw, s=ssid, f=flags: app.connect_wifi(s, pw, f),
        secret=True,
        submit_label="Bağlan",
    )


def wifi_page(app, arg):
    st = app.wifi_status
    state_label, state_color = _wifi_state_tr(st, app.wifi_scanning, app.lang)
    sig_txt = "%d dBm" % st.signal_dbm if st.signal_dbm is not None else "-"
    ip_sig = "%s · %s" % (st.ip_address or "-", sig_txt)
    can_forget = bool(st.ssid and (st.ssid in (st.saved_ssids or ()) or st.state == "COMPLETED"))

    rows = [
        Card([
            section("Wi-Fi", state_label, color=state_color),
            Row(19, [Label(app.tr("Ağ"), size="small", color="muted", weight=0.55),
                     Label(st.ssid or app.tr("Bağlı değil"), size="small", color="text", align="right", weight=1.65)]),
            Row(19, [Label(app.tr("IP / Sinyal"), size="small", color="muted", weight=0.75),
                     Label(ip_sig, size="small", color="tertiary", align="right", weight=1.45)]),
            Row(44, [
                Button(app.tr("Yenile"), lambda: app.run_async(lambda: app.refresh_wifi(scan=True)), icon="refresh", align="left"),
                Button(app.tr("Unut"), app.when(can_forget, lambda: app.confirm(
                    app.tr("%s ağı unutulsun mu?") % st.ssid, lambda s=st.ssid: app.forget_wifi(s), "danger")),
                    "outline_danger", icon="close", align="left"),
            ]),
        ], pad=5, gap=2),
    ]

    networks = list(app.wifi_networks or ())
    pages = max(1, -(-len(networks) // WIFI_PER_PAGE))
    app.wifi_page = max(0, min(app.wifi_page, pages - 1))
    start = app.wifi_page * WIFI_PER_PAGE
    page_items = networks[start:start + WIFI_PER_PAGE]

    rows.append(section(app.tr("Ağlar"), app.tr("Taranıyor...") if app.wifi_scanning else (app.tr("%d ağ") % len(networks))))
    if not page_items:
        msg = app.tr_error(st.error) if st.error else (app.tr("Ağlar taranıyor...") if app.wifi_scanning else app.tr("Ağ bulunamadı"))
        rows.append(Card([Row(48, [Label(msg, size="small", color="muted", align="center")])], pad=8))
    else:
        for net in page_items:
            is_cur = (st.state == "COMPLETED" and st.ssid == net.ssid) or net.connected
            sec_txt = app.tr("Açık ağ") if not net.secured else app.tr("Şifreli")
            sig = "%d dBm" % net.signal_dbm if net.signal_dbm is not None else "-"
            tag = app.tr("Bağlı") if is_cur else (app.tr("Kayıtlı") if net.saved else sec_txt)
            sub = "%s · %s" % (tag, sig)
            icon = "check" if is_cur else ("lock" if net.secured else "wifi")
            kind = "active" if is_cur else "card"
            if net.saved or is_cur:
                action = lambda s=net.ssid: app.open("wifi_net", s)
            elif net.secured:
                action = lambda s=net.ssid, f=net.flags: _open_wifi_connect(app, s, f)
            else:
                action = lambda s=net.ssid, f=net.flags: app.confirm(
                    app.tr("%s (açık ağ) ağına bağlanılsın mı?") % s,
                    lambda s=s, f=f: app.connect_wifi(s, None, f),
                    "primary",
                )
            rows.append(Row(44, [Button(net.ssid, action, kind, icon=icon, align="left", sub=sub, right_icon="next")]))

    def turn(delta):
        app.wifi_page += delta
        app.dirty = True

    rows.append(Row(44, [
        Button("", app.when(app.wifi_page > 0, lambda: turn(-1)), icon="prev"),
        Label("%d / %d" % (app.wifi_page + 1, pages), size="med", color="muted", align="center"),
        Button("", app.when(app.wifi_page < pages - 1, lambda: turn(1)), icon="next"),
    ]))
    return [Card(rows, color="bg", border=None, radius=0, pad=0, gap=4)]


def wifi_net_page(app, ssid):
    st = app.wifi_status
    net = next((n for n in (app.wifi_networks or ()) if n.ssid == ssid), None)
    is_cur = st.state == "COMPLETED" and st.ssid == ssid
    saved = bool((net and net.saved) or ssid in (st.saved_ssids or ()) or is_cur)
    secured = net.secured if net is not None else True
    flags = net.flags if net is not None else ""
    sig_txt = ("%d dBm" % net.signal_dbm) if (net and net.signal_dbm is not None) else "-"
    state_txt = app.tr("Bağlı") if is_cur else (app.tr("Kayıtlı") if saved else app.tr("Kayıtlı değil"))

    rows = [
        Card([
            section(ssid, state_txt, color="ok" if is_cur else "muted"),
            Row(19, [Label(app.tr("Güvenlik"), size="small", color="muted", weight=0.8),
                     Label(app.tr("Şifreli") if secured else app.tr("Açık ağ"), size="small", align="right", weight=1.2)]),
            Row(19, [Label(app.tr("Sinyal"), size="small", color="muted", weight=0.8),
                     Label(sig_txt, size="small", color="tertiary", align="right", weight=1.2)]),
        ], pad=10, gap=4),
    ]
    if saved or not secured:
        rows.append(Row(44, [Button(
            app.tr("Bağlan"),
            lambda: (app.back(), app.connect_wifi(ssid, None, flags)),
            "primary",
            icon="wifi",
        )]))
    if secured:
        rows.append(Row(44, [Button(
            app.tr("Şifre gir"),
            lambda: (app.back(), _open_wifi_connect(app, ssid, flags)),
            "card" if saved else "primary",
            icon="lock",
        )]))
    if saved:
        rows.append(Row(44, [Button(
            app.tr("Unut"),
            lambda: app.confirm(app.tr("%s ağı unutulsun mu?") % ssid, lambda: (app.back(), app.forget_wifi(ssid)), "danger"),
            "outline_danger",
            icon="close",
        )]))
    return rows


def display_page(app, arg):
    return [
        Card([
            section(app.tr("Parlaklık"), "%d%%" % app.display_brightness, color="text"),
            Row(44, [Button("%d%%" % p, lambda p=p: app.set_display_brightness(p),
                            "active" if app.display_brightness == p else "normal")
                     for p in BRIGHTNESS_STEPS]),
        ], pad=8, gap=6),
        Card([
            section(app.tr("Ekran uykusu")),
            Row(44, [Button(app.tr(lbl), lambda s=sec: app.set_display_sleep(s),
                            "active" if app.display_sleep_sec == sec else "normal")
                     for lbl, sec in SLEEP_TIMEOUTS]),
        ], pad=8, gap=6),
        Card([
            Row(44, [Button(
                app.tr("Baskıda uyku"),
                lambda: app.set_display_sleep_on_print(not app.display_sleep_on_print),
                "card",
                icon="print",
                align="left",
                toggle=bool(app.display_sleep_on_print),
            )]),
            Row(38, [Label(app.tr("Onay ve hata pencerelerinde ekran açık kalır."), size="small", color="tertiary")]),
        ], pad=8, gap=4),
        Row(44, [Button(app.tr("Ekranı şimdi uyut"), app.sleep_display, "normal", icon="power", align="left")]),
    ]


def sound_page(app, arg):
    method = app.beeper.method_name(app.state.settings)
    return [
        Card([section(app.tr("Ses yöntemi"), method, color="text")], pad=8, gap=2),
        Row(44, [Button(
            app.tr("Sesli uyarı"),
            lambda: app.set_beep_option("beep_enabled", not app.beep_enabled),
            "card",
            icon="info",
            align="left",
            toggle=bool(app.beep_enabled),
        )]),
        Row(44, [Button(
            app.tr("Baskı bitince"),
            app.when(app.beep_enabled, lambda: app.set_beep_option("beep_on_complete", not app.beep_on_complete)),
            "card",
            icon="check",
            align="left",
            toggle=bool(app.beep_on_complete),
        )]),
        Row(44, [Button(
            app.tr("Hata olunca"),
            app.when(app.beep_enabled, lambda: app.set_beep_option("beep_on_error", not app.beep_on_error)),
            "card",
            icon="error",
            align="left",
            toggle=bool(app.beep_on_error),
        )]),
        Row(44, [Button(
            app.tr("Onayda bip"),
            app.when(app.beep_enabled, lambda: app.set_beep_option("beep_on_confirm", not app.beep_on_confirm)),
            "card",
            icon="play",
            align="left",
            toggle=bool(app.beep_on_confirm),
        )]),
        Row(44, [Button(app.tr("Test bip"), lambda: app.trigger_beep("test", force=True), "primary", icon="play", align="left")]),
    ]
