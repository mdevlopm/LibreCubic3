"""Tools grid, limits, wizard, events, maintenance and updates pages."""

from . import i18n, limits, tracker, wizards
from .screens_common import EVENTS_PER_PAGE, _format_date, blank, duration, section
from .widgets import Bar, Button, Card, Col, Icon, Label, Ring, Row, Rule, Spacer


def tools(app):
    """Equal tiles in two columns; one text size for all so labels line up and never wrap."""
    order = ("cold_pull", "clean", "load", "unload", "level", "zoffset", "pid", "shaper")
    locked = bool(app.heating_lock)
    tiles = []
    for key in order:
        w = wizards.WIZARDS[key]
        is_locked = locked and app.wizard_heats(key)
        tiles.append(Button(
            app.tr_short(w.name),
            app.when(app.idle and not is_locked, lambda key=key: app.start_wizard(key)),
            "card",
            icon="lock" if is_locked else w.icon,
            align="left",
            size="small",
        ))
    due = app.due_maintenance_items()
    maint_lbl = (app.tr("Bakım (%d)") % len(due)) if due else app.tr("Bakım")
    tiles.append(Button(app.tr("Baskı geçmişi"), lambda: app.open("history"), "card", icon="time",
                        align="left", size="small"))
    tiles.append(Button(app.tr("Olay geçmişi"), lambda: app.open("events"), "card", icon="info",
                        align="left", size="small"))
    tiles.append(Button(maint_lbl, lambda: app.open("maintenance"), "active" if due else "card",
                        icon="warning" if due else "heal", align="left", size="small"))
    tiles.append(Button(app.tr("Tabla haritası"), lambda: app.open("bed_mesh"), "card", icon="level",
                        align="left", size="small"))
    tiles.append(Button(app.tr("Sistem izleme"), lambda: app.open("sysinfo"), "card", icon="chart",
                        align="left", size="small"))
    if len(tiles) % 2:
        tiles.append(Spacer())  # keep an odd last tile the same width as the others
    tool_rows = [Row(46, tiles[i:i + 2]) for i in range(0, len(tiles), 2)]
    return [Card(tool_rows, color="bg", border=None, radius=0, pad=0, gap=5)]


def limits_page(app, arg):
    editable = app.ready and not app.printing
    recoverable = app.state.connected and not app.printing

    def open_spec(spec):
        cur = app.limit_value(spec.section, spec.option)
        spec_label = app.tr(spec.label)
        spec_risk = app.tr(spec.risk)
        if spec.is_bool:
            new_val = not bool(cur) if cur is not None else True
            apply = lambda: app.save_limits({(spec.section, spec.option): new_val})
            if limits.is_dangerous_change(spec, cur, new_val):
                msg = "%s: %s -> %s\n\n%s" % (
                    spec_label, limits.format_limit_value(spec, cur, app.lang),
                    limits.format_limit_value(spec, new_val, app.lang), spec_risk)
                app.confirm(msg, apply, "danger")
            else:
                apply()
            return
        low = 0.0 if spec.minval is None else spec.minval
        above = spec.above
        if spec.section == "extruder" and spec.option == "max_temp":
            min_e = app.limit_value("extruder", "min_extrude_temp") or 0.0
            min_t = app.setting("extruder", "min_temp", None)
            low = max(1.0, float(min_e))
            if min_t is not None:
                above = float(min_t) if above is None else max(above, float(min_t))
        elif spec.section == "heater_bed" and spec.option == "max_temp":
            min_t = app.setting("heater_bed", "min_temp", None)
            low = 1.0
            if min_t is not None:
                above = float(min_t) if above is None else max(above, float(min_t))
        elif spec.section == "temperature_sensor ace_temp" and spec.option == "max_temp":
            min_t = app.setting("temperature_sensor ace_temp", "min_temp", None)
            low = 1.0
            if min_t is not None:
                above = float(min_t) if above is None else max(above, float(min_t))
        elif spec.section == "printer" and spec.option == "max_velocity":
            z_vel = app.limit_value("printer", "max_z_velocity")
            if z_vel is not None:
                low = max(low, float(z_vel))
        elif spec.section == "printer" and spec.option == "max_accel":
            z_acc = app.limit_value("printer", "max_z_accel")
            if z_acc is not None:
                low = max(low, float(z_acc))
        high = app.limit_value(*spec.max_ref) if spec.max_ref else None
        title = "%s (%s)" % (spec_label, limits.format_limit_value(spec, cur, app.lang))

        def on_value(new_val):
            apply = lambda: app.save_limits({(spec.section, spec.option): new_val})
            if limits.is_dangerous_change(spec, cur, new_val):
                msg = "%s: %s -> %s\n\n%s" % (
                    spec_label, limits.format_limit_value(spec, cur, app.lang),
                    limits.format_limit_value(spec, new_val, app.lang), spec_risk)
                app.confirm(msg, apply, "danger")
            else:
                apply()

        app.keypad(title, low, high, on_value, digits=spec.digits,
                   allow_float=spec.allow_float, above=above, initial=cur)

    def spec_row(spec):
        spec_label = app.tr(spec.label)
        if app.limit_is_from_config(spec.section, spec.option):
            return Row(37, [Col([
                Row(19, [Label(spec_label, size="small", color="muted")]),
                Row(18, [Label(app.tr("config dosyasından"), size="tab", color="muted", align="right")]),
            ], gap=0)])
        val = app.limit_value(spec.section, spec.option)
        overridden = (spec.section.lower(), spec.option.lower()) in app.limits_overrides
        lbl_sz = "tab" if len(spec_label) > 17 else "small"
        return Row(32, [
            Label(spec_label, size=lbl_sz, color="muted", weight=1.35),
            Button(limits.format_limit_value(spec, val, app.lang), app.when(editable, lambda s=spec: open_spec(s)),
                   "active" if overridden else "normal", weight=1.05),
        ])

    def verify_card(title, section_name, specs, risk_target):
        err_val = app.limit_value(section_name, "max_error") or 0.0
        time_val = app.limit_value(section_name, "check_gain_time") or 0.0
        bypassed = err_val >= 99999.0 and time_val >= 99999.0

        def toggle():
            if bypassed:
                app.save_limits({(section_name, k): None for k in limits.VERIFY_BYPASS})
            else:
                msg = (
                    app.tr("%s ısıtıcı denetimi kapatılsın mı?\n\nKoruma eşikleri en gevşek değerlere çekilir; termal kaçak algılanmayabilir.")
                    % app.tr(risk_target)
                )
                app.confirm(msg, lambda: app.save_limits(
                    {(section_name, k): v for k, v in limits.VERIFY_BYPASS.items()}), "danger")

        toggle_row = Row(34, [
            Label(app.tr("Denetim durumu"), size="small", color="muted", weight=1.3),
            Button(app.tr("Devre dışı") if bypassed else app.tr("Açık"), app.when(editable, toggle),
                   "outline_danger" if bypassed else "normal", weight=1.0),
        ])
        return Card([section(app.tr(title)), Rule(), toggle_row] + [spec_row(s) for s in specs], pad=6, gap=3)

    # Page 0: temperature
    page0 = [Card([section(app.tr("Sıcaklık")), Rule()] + [spec_row(s) for s in limits.TEMP_LIMITS], pad=6, gap=3)]

    # Page 1: motion + LeviQ3 (if loaded)
    page1 = [Card([section(app.tr("Hareket")), Rule()] + [spec_row(s) for s in limits.MOTION_LIMITS], pad=6, gap=3)]
    if "leviq3" in app.state.settings:
        page1.append(Card([section("LeviQ3"), Rule()] + [spec_row(s) for s in limits.LEVIQ3_LIMITS], pad=6, gap=3))

    pages = [
        page0,
        page1,
        [verify_card("Nozul ısı denetimi", "verify_heater extruder", limits.VERIFY_EXTRUDER_LIMITS, "Nozul")],
        [verify_card("Tabla ısı denetimi", "verify_heater heater_bed", limits.VERIFY_BED_LIMITS, "Tabla")],
    ]
    for title, group in (("ACE Isı ve Besleme", limits.ACE_FEED_LIMITS), ("ACE Algılama", limits.ACE_SENSE_LIMITS)):
        loaded_specs = [spec for spec in group if spec.section in app.state.settings]
        if loaded_specs:
            pages.append([Card([section(app.tr(title)), Rule()] + [spec_row(s) for s in loaded_specs], pad=6, gap=3)])
    app.limit_page = max(0, min(app.limit_page, len(pages) - 1))
    rows = list(pages[app.limit_page])

    # Keep bottom controls visually anchored across shorter pages too.
    has_overrides = bool(app.limits_overrides) or app.limits_file_exists
    pending = app.limits_pending()

    def turn(delta):
        app.limit_page += delta
        app.dirty = True

    def confirm_reset():
        app.confirm(app.tr("Tüm özel sınırlar silinip varsayılanlara dönülsün mü?"), app.reset_limits, "danger")

    def confirm_restart():
        app.confirm(app.tr("Değişiklikler için Klipper yeniden başlatılsın mı?"),
                    lambda: app.do(app.restart_klipper, app.tr("Klipper yeniden başlatıldı")), "primary")

    if len(rows) == 1:
        rows.append(blank(20))
    can_reset = recoverable and (has_overrides or not app.ready)
    rows.append(Row(44, [
        Button(app.tr("Varsayılana dön"), app.when(can_reset, confirm_reset), "outline_danger"),
        Button(app.tr("Yeniden başlat"), app.when(recoverable, confirm_restart), "primary" if pending else "normal"),
    ]))
    rows.append(Row(44, [
        Button("", app.when(app.limit_page > 0, lambda: turn(-1)), icon="prev"),
        Label("%d / %d" % (app.limit_page + 1, len(pages)), size="med", color="muted", align="center"),
        Button("", app.when(app.limit_page < len(pages) - 1, lambda: turn(1)), icon="next"),
    ]))
    return [Card(rows, color="bg", border=None, radius=0, pad=0, gap=4)]


def wizard(app, arg):
    run = app.wizard
    step = run.step
    done, fraction, label = run.progress(app)
    count = len(run.wizard.steps)
    body_text = run.text(app.tr(step.text))
    if "\n" not in body_text and len(body_text) <= 55:
        card_h = 68
    elif len(body_text) <= 95:
        card_h = 92
    else:
        card_h = 114
    rows = [
        Row(24, [Label(app.tr("Adım %d / %d") % (run.index + 1, count), size="small", color="muted"),
                 Label(app.tr(step.title), size="med", color="active_icon", align="right")]),
        Row(6, [Bar((run.index + 1) / float(count))]),
        Card([Row(card_h, [Label(body_text, size="med")])], pad=12),
    ]
    if run.failed:
        rows.append(Card([Row(48, [Icon("error", "normal", "danger", weight=0.2),
                                   Label(app.tr_error(run.failed), size="small", color="danger_text")])],
                         color="danger_bg", border="danger_border", pad=10))
        rows.append(Row(48, [Button(app.tr("Yeniden dene"), lambda: run.goto(app, run.index), icon="refresh")]))
    elif fraction is not None:
        rows.append(blank(8))
        rows.append(Row(152, [Spacer(0.32), Ring(fraction, label.split(" / ")[0], app.tr("hedef %s") % label.split(" / ")[-1],
                                                 color="ok" if done else "warn"), Spacer(0.32)]))
        rows.append(blank(8))
    elif label:
        rows.append(Row(52, [Icon("check" if done else "time", "normal", "ok" if done else "warn", weight=0.2),
                             Label(label, size="med", color="ok" if done else "warn")]))
    for i in range(0, len(step.choices), 2):
        rows.append(Row(48, [Button(app.tr(text), lambda params=params: run.choose(app, params), "card")
                             for text, params in step.choices[i:i + 2]]))
    if step.actions:
        def action_btn(text, gcode):
            heats = app.heating_lock and app.script_heats_printer(run.text(gcode))
            extrudes = app.extrusion_lock and app.script_extrudes(run.text(gcode))
            locked = heats or extrudes

            def press(g=gcode):
                script = run.text(g)
                sent = app.gcode(script, app.tr("Kaydedildi, Klipper yeniden başlıyor") if script == "SAVE_CONFIG" else None)
                if sent and script == "SAVE_CONFIG":
                    app.end_wizard(cancelled=False)
            return Button(app.tr(text), app.when(app.ready and not locked, press), icon="lock" if locked else None)
        rows.append(Row(48, [action_btn(text, gcode) for text, gcode in step.actions]))
    last = run.index + 1 == count
    # While Klipper runs the step's G-code a cancel could not stop it (the cancel script would only queue up
    # behind it), so the button waits; the E-stop in the title bar stays available.
    can_cancel = not (step.blocking and run.running)
    buttons = [Button(app.tr("Bitir") if last else app.tr("İptal"),
                      app.when(last or can_cancel, lambda: app.end_wizard(cancelled=not last)),
                      "primary" if last else "normal")]
    if step.repeat_from is not None:
        buttons.insert(0, Button(app.tr("Tekrarla"), lambda: run.goto(app, step.repeat_from), icon="refresh"))
    if not last and not step.choices:
        buttons.append(Button(app.tr("İleri"), app.when(done, lambda: run.next(app)), "primary", icon="next"))
    rows.append(Row(48, buttons))
    return rows


def _event_style(severity):
    sev = str(severity or "info").lower()
    if sev == "error":
        return "error", "danger", "danger_bg", "danger_border", "danger_text"
    if sev == "warn":
        return "warning", "warn", "card", "warn", "warn"
    if sev == "ok":
        return "check", "ok", "card", "border_subtle", "text"
    return "info", "info", "card", "border_subtle", "text"


def _event_source_label(source, lang="tr"):
    mapping = {
        "notify": "Bildirim",
        "gcode": "G-code",
        "klipper": "Klipper",
        "print": "Baskı",
        "ace": "ACE",
    }
    raw = mapping.get(str(source or "").lower(), "Sistem")
    return i18n.tr(raw, lang)


def events_page(app, arg):
    items = list(reversed(app.event_log or ()))
    pages = max(1, -(-len(items) // EVENTS_PER_PAGE))
    app.events_page = max(0, min(app.events_page, pages - 1))
    start = app.events_page * EVENTS_PER_PAGE
    page_items = items[start:start + EVENTS_PER_PAGE]

    rows = [
        Card([section(app.tr("Kayıtlar"), app.tr("%d kayıt") % len(items), color="text")], pad=6, gap=0),
    ]
    if not page_items:
        rows.append(Card([Row(60, [Label(app.tr("Henüz olay kaydı yok"), color="muted", align="center")])], pad=10))
    else:
        cards = []
        for ev in page_items:
            icon, icon_col, bg_col, brd_col, txt_col = _event_style(ev.get("severity"))
            dt = _format_date(ev.get("ts"))
            src = _event_source_label(ev.get("source"), app.lang)
            msg = app.tr_error(str(ev.get("message") or ""))
            cards.append(Card([
                Row(18, [
                    Icon(icon, "small", icon_col, weight=0.18),
                    Label(src, size="tab", color=icon_col, weight=0.8),
                    Label(dt, size="tab", color="tertiary", align="right", weight=1.1),
                ]),
                Row(40, [Label(msg, size="small", color=txt_col)]),
            ], color=bg_col, border=brd_col, pad=6, gap=2))
        rows.append(Card(cards, color="bg", border=None, radius=0, pad=0, gap=4))

    def turn(delta):
        app.events_page += delta
        app.dirty = True

    rows.append(Row(44, [
        Button(app.tr("Temizle"), app.when(bool(items), app.clear_events), "outline_danger", weight=1.4),
        Button("", app.when(app.events_page > 0, lambda: turn(-1)), icon="prev", weight=0.8),
        Label("%d / %d" % (app.events_page + 1, pages), size="small", color="muted", align="center", weight=0.8),
        Button("", app.when(app.events_page < pages - 1, lambda: turn(1)), icon="next", weight=0.8),
    ]))
    return [Card(rows, color="bg", border=None, radius=0, pad=0, gap=4)]


def maintenance_page(app, arg):
    items, total_print_time, total_jobs = app.maintenance_summary()
    total_hours = total_print_time / 3600.0
    rows = [
        Card([
            Row(19, [
                Label(app.tr("Toplam"), size="small", color="muted", weight=0.7),
                Label(app.tr("%.1f sa · %d iş") % (total_hours, total_jobs), size="small", color="text",
                      align="right", weight=1.5),
            ]),
        ], pad=6, gap=0),
    ]
    item_rows = []
    for item in items:
        item_id = item["id"]
        metric = item["metric"]
        elapsed = item["elapsed"]
        threshold = item["threshold"]
        due = item["due"]
        unit_txt = app.tr("sa") if metric == "hours" else app.tr("baskı")
        if threshold <= 0:
            prog_txt = app.tr("Kapalı")
        elif metric == "hours":
            prog_txt = "%.1f / %.0f %s" % (elapsed, threshold, unit_txt)
        else:
            prog_txt = "%.0f / %.0f %s" % (elapsed, threshold, unit_txt)

        def edit_thresh(iid=item_id, lbl=item["label"], met=metric):
            title = "%s (%s)" % (app.tr(lbl), app.tr("saat, 0=kapalı") if met == "hours" else app.tr("baskı, 0=kapalı"))
            app.keypad(title, 0, 5000, lambda val, iid=iid: app.set_maintenance_threshold(iid, val), digits=4,
                       initial=threshold)

        item_rows.append(Row(48, [
            Button(app.tr(item["label"]), edit_thresh, "outline_danger" if due else "card",
                   sub=prog_txt, align="left", weight=1.85),
            Button(app.tr("Yapıldı"), lambda iid=item_id: app.mark_maintenance_done(iid),
                   "primary" if due else "active", icon="check", align="left", weight=1.0),
        ]))
    rows.append(Card(item_rows, color="bg", border=None, radius=0, pad=0, gap=6))
    rows.append(Row(44, [Button(app.tr("Yenile"), lambda: app.run_async(app.refresh_maintenance_totals), icon="refresh", align="left")]))
    return [Card(rows, color="bg", border=None, radius=0, pad=0, gap=6)]


def updates_page(app, arg):
    ok_ver = app.open_kobra_version or app.app_version or "-"
    app_ver = app.app_version or "-"
    klip_ver = app.state.version or "-"
    ace_ver = getattr(app, "ace_driver_version", None)
    if not ace_ver or ace_ver == "-":
        if hasattr(app, "get_ace_driver_version"):
            ace_ver = app.get_ace_driver_version(getattr(app, "rinkhals_home", None))
        else:
            ace_ver, _ = tracker.read_ace_driver_version(getattr(app, "rinkhals_home", None))
    ace_ver = ace_ver or "-"
    rows = [
        Card([
            section(app.tr("Sürümler")),
            Row(19, [Label("open-kobra", size="small", color="muted"),
                     Label(ok_ver, size="small", color="text", align="right")]),
            Row(19, [Label(app.tr("ace_driver_version"), size="small", color="muted"),
                     Label(ace_ver, size="small", color="text", align="right")]),
            Row(19, [Label("kobra-screen", size="small", color="muted"),
                     Label(app_ver, size="small", color="text", align="right")]),
            Row(19, [Label("Klipper", size="small", color="muted"),
                     Label(klip_ver, size="small", color="text", align="right")]),
        ], pad=8, gap=4),
    ]
    if app.update_error:
        rows.append(Card([
            Row(36, [Icon("error", "normal", "danger", weight=0.2),
                     Label(app.update_error, size="small", color="danger_text", weight=1.8)]),
        ], color="danger_bg", border="danger_border", pad=8))
    elif not app.update_checked:
        rows.append(Card([Row(44, [Label(app.tr("Denetleniyor..."), size="med", color="muted", align="center")])], pad=8))
    elif app.update_status_data is None:
        rows.append(Card([
            Row(25, [Icon("info", "normal", "info", weight=0.18),
                     Label("Moonraker update_manager", size="small", color="text", weight=1.8)]),
            Row(38, [Label(app.tr("Güncelleme yöneticisi yapılandırılmamış"), size="small", color="muted")]),
        ], pad=8, gap=4))
    else:
        vinfo = (app.update_status_data or {}).get("version_info") or {}
        comp_rows = [section("Moonraker update_manager")]
        if not vinfo:
            comp_rows.append(Row(24, [Label(app.tr("Bileşen bilgisi yok"), size="small", color="muted")]))
        else:
            for name, info in list(vinfo.items())[:5]:
                cur_v = str((info or {}).get("version") or "?")
                rem_v = str((info or {}).get("remote_version") or "")
                commits = int((info or {}).get("commits_behind") or 0)
                pkg_cnt = int((info or {}).get("package_count") or 0)
                has_upd = commits > 0 or pkg_cnt > 0 or (rem_v and rem_v != "?" and rem_v != cur_v)
                if name == "system":
                    status_txt = (app.tr("%d paket") % pkg_cnt) if pkg_cnt > 0 else app.tr("Güncel")
                elif has_upd:
                    status_txt = "%s -> %s" % (cur_v, rem_v or ("+%d" % commits))
                else:
                    status_txt = cur_v
                comp_rows.append(Row(19, [
                    Label(str(name), size="small", color="muted", weight=0.9),
                    Label(status_txt, size="small", color="warn" if has_upd else "ok", align="right", weight=1.3),
                ]))
        rows.append(Card(comp_rows, pad=8, gap=4))

    rows.append(Row(44, [Button(app.tr("Yenile"), lambda: app.run_async(app.refresh_updates), icon="refresh", align="left")]))
    return rows


def sysinfo_page(app, arg):
    """Host CPU/memory, process shares and MCU load (kobra_screen.sysinfo); refreshed every 2 s while open."""
    info = app.sysinfo
    if not info:
        return [Card([Row(60, [Label(app.tr("Okunuyor..."), color="muted", align="center")])], pad=12)]

    def pct(value):
        return "-" if value is None else "%.0f%%" % value

    def line(title, value, color="text"):
        return Row(19, [Label(title, size="small", color="muted", weight=1.0),
                        Label(value, size="small", color=color, align="right", weight=1.2)])

    mem = "-"
    if info.get("mem_total_kb"):
        used, total = info["mem_used_kb"] or 0, info["mem_total_kb"]
        mem = "%.0f / %.0f MB · %.0f%%" % (used / 1024.0, total / 1024.0, used * 100.0 / total)
    cpu = info.get("cpu")
    host = Card([
        section(app.tr("Yazıcı bilgisayarı")),
        line(app.tr("İşlemci"), pct(cpu), "warn" if cpu is not None and cpu >= 85 else "text"),
        line(app.tr("Bellek"), mem),
        line(app.tr("Çalışma süresi"), duration(info["uptime"], app.lang) if info.get("uptime") else "-"),
    ], pad=8, gap=3)

    screen_rss = info.get("screen_rss_kb")
    procs = Card([
        section(app.tr("İşlemci payı")),
        line(app.tr("Ekran"), pct(info.get("screen_cpu"))
             + ("" if screen_rss is None else " · %.0f MB" % (screen_rss / 1024.0))),
        line("Klipper", pct(info.get("klipper_cpu"))),
        line("Moonraker", pct(info.get("moonraker_cpu"))),
    ], pad=8, gap=3)

    mcu_rows = [section(app.tr("MCU yükü"))]
    for mcu in info.get("mcus") or ():
        errors = mcu["retransmit"] + mcu["invalid"]
        mcu_rows.append(line(app.tr(mcu["label"]), "%s · %.1f ms · %s %d" % (
            pct(mcu["load"]), mcu["rtt_ms"], app.tr("hata"), errors)))
    mcu_rows.append(Row(18, [Label(app.tr("Sıcaklık: kartların yazılımı desteklemiyor"), size="tab", color="tertiary")]))
    return [Card([host, procs, Card(mcu_rows, pad=8, gap=3)], color="bg", border=None, radius=0, pad=0, gap=5)]
