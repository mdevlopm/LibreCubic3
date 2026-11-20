"""File browser, print confirmation, print history and bed-mesh screen builders."""

from . import i18n, tool_map
from .screens_common import (
    FILES_PER_PAGE, HISTORY_PER_PAGE, _format_date, _format_filament_mm, _format_size, duration, section,
    status_badge,
)
from .widgets import Button, Card, Icon, Label, MeshSurface, Picture, Row, Rule, Spacer


def files(app, arg):
    raw = list(app.state.files or ())
    if app.file_sort == "oldest":
        items = sorted(raw, key=lambda f: f.get("modified", 0))
    elif app.file_sort == "name":
        items = sorted(raw, key=lambda f: str(f.get("path", "")).lower())
    else:
        items = sorted(raw, key=lambda f: f.get("modified", 0), reverse=True)

    pages = max(1, -(-len(items) // FILES_PER_PAGE))
    app.file_page = max(0, min(app.file_page, pages - 1))
    start = app.file_page * FILES_PER_PAGE

    def set_sort(mode):
        app.file_sort = mode
        app.file_page = 0
        app.dirty = True

    sort_bar = Card([Row(28, [
        Button(app.tr(lbl), lambda m=mode: set_sort(m),
               "segment" if app.file_sort == mode else "segment_off")
        for lbl, mode in (("Yeni", "newest"), ("Eski", "oldest"), ("Ad", "name"))
    ])], color="surface2", border="border_subtle", radius=10, pad=2, gap=2)

    file_rows = []
    for item in items[start:start + FILES_PER_PAGE]:
        sub = "%s · %s" % (_format_size(item.get("size")), _format_date(item.get("modified")))
        file_rows.append(Row(44, [Button(
            item["path"],
            lambda path=item["path"]: app.open("file", path),
            "card",
            icon="file",
            align="left",
            sub=sub,
            right_icon="next",
        )]))
    if not items:
        file_rows.append(Row(60, [Label(app.tr("Dosya yok"), color="muted", align="center")]))

    def turn(delta):
        app.file_page += delta
        app.dirty = True

    paging = Row(44, [
        Button("", app.when(app.file_page > 0, lambda: turn(-1)), icon="prev"),
        Label("%d / %d" % (app.file_page + 1, pages), size="med", color="muted", align="center"),
        Button("", app.when(app.file_page < pages - 1, lambda: turn(1)), icon="next"),
        Button("", lambda: app.run_async(app.refresh_files), icon="refresh"),
    ])
    return [Card([sort_bar] + file_rows + [paging], color="bg", border=None, radius=0, pad=0, gap=2)]


def file(app, path):
    meta = app.meta.get(path) or {}
    thumbnail = meta.get("thumbnail")
    visual = Picture(thumbnail, weight=1) if thumbnail else Icon("file", "xl", "tertiary", weight=1)
    filament_len = meta.get("filament_total")
    dur = duration(meta["estimated_time"], app.lang) if meta.get("estimated_time") else "-"
    fil = "%.1f m" % (filament_len / 1000.0) if filament_len else "-"
    layer_txt = (app.tr("Katman %s mm") % meta["layer_height"]) if meta.get("layer_height") else ""
    warn_info = app.check_print_filament_warning(path, meta=meta)

    def do_start():
        app.start_print(path)

    def start():
        if app.heating_lock:
            app.notify(app.tr("Isıtma kilidi açık"))
            return
        if warn_info is not None:
            sp = warn_info["spool"]
            msg = app.tr(
                "Tahmini filament (%.0f g / %.1f m) kalan miktardan (%.0f g / %.1f m) fazla. Başlatılsın mı?"
            ) % (warn_info["needed_g"], warn_info["needed_m"], sp["remaining_g"], sp["remaining_m"])
            app.confirm(msg, do_start, "danger")
            return
        do_start()

    rows = [
        Card([Row(64 if warn_info else 72, [Spacer(0.25), visual, Spacer(0.25)])],
             color="sunken", border="border_subtle", radius=12, pad=8),
        Card([
            Row(28, [Label(path, size="normal", align="center")]),
            Row(23, [
                Spacer(0.15),
                Icon("time", "small", "muted", weight=0.18),
                Label(dur, size="med", color="muted", weight=0.75),
                Icon("spool", "small", "muted", weight=0.18),
                Label(fil, size="med", color="muted", weight=0.55),
                Spacer(0.15),
            ]),
            Row(19, [Label(layer_txt, size="small", color="tertiary", align="center")]),
        ], pad=8, gap=4),
    ]
    if warn_info is not None:
        sp = warn_info["spool"]
        rows.append(Card([Row(26, [
            Icon("warning", "small", "warn", weight=0.2),
            Label(app.tr("Filament yetersiz: kalan %.0f g") % sp["remaining_g"], size="small", color="warn", weight=1.8),
        ])], color="surface2", border="warn", pad=6, gap=0))
    rows.append(print_options(app))
    plan = app.print_tool_plan(path, meta=meta) if meta else None
    if plan is not None:
        rows.append(tool_map_row(app, path, plan))
    locked = bool(app.heating_lock)
    can_start = app.idle and not locked and not tool_map.unresolved(plan)
    rows.append(Row(48, [Button(app.tr("Yazdır"), app.when(can_start, start), "primary",
                                icon="lock" if locked else "play")]))
    return rows


def _tool_text(entry, sep=" "):
    return "T%d%s%s" % (entry["tool"], sep, entry["material"]) if entry["material"] else "T%d" % entry["tool"]


def _slot_text(app, entry):
    if entry["slot"] is None:
        return app.tr("Yuva yok")
    text = app.tr("Yuva %d") % (entry["slot"] + 1)
    return "%s · %s" % (text, entry["slot_material"]) if entry["slot_material"] else text


def tool_map_row(app, path, plan):
    """ACE slot choice on the print screen: one line, tap for the per-tool page."""
    missing = tool_map.unresolved(plan)
    if missing:
        e = missing[0]
        return Row(44, [Button(app.tr("%s: dolu yuva yok") % _tool_text(e),
                               lambda: app.open("tool_map", path), "outline_danger", icon="warning",
                               align="left", right_icon="next")])
    if len(plan) == 1:
        e = plan[0]
        text = "%s > %s" % (_tool_text(e), _slot_text(app, e))
        dot = e["slot_rgb"] or (0, 0, 0)
    else:
        slots = ", ".join(str(e["slot"] + 1) for e in plan)
        text = app.tr("%d renk: Yuva %s") % (len(plan), slots)
        dot = plan[0]["slot_rgb"] or (0, 0, 0)
    kind = "outline_danger" if any(e["match"] == "other" for e in plan) else "card"
    return Row(44, [Button(text, lambda: app.open("tool_map", path), kind, dot=dot, align="left",
                           right_icon="next")])


def tool_map_page(app, path):
    """Per-tool ACE slot choice: tap a slot to move to the next loaded one; Auto drops the manual picks."""
    meta = app.meta.get(path) or {}
    plan = app.print_tool_plan(path, meta=meta) if meta else None
    if plan is None:
        return [Card([Row(60, [Label(app.tr("ACE Pro bağlı değil"), color="muted", align="center")])], pad=12)]
    slots = app.ace_slots
    rows = []
    for e in plan:
        def change(tool=e["tool"]):
            app.set_print_tool_override(path, tool, tool_map.next_slot(app.print_tool_plan(path) or plan,
                                                                      tool, app.ace_slots))
        kind = "outline_danger" if e["match"] in ("other", "none") else "card"
        rows.append(Row(44, [
            Button("T%d" % e["tool"], None, "ghost", dot=e["rgb"] or (0, 0, 0), sub=e["material"],
                   align="left", weight=0.65),
            Button(_slot_text(app, e), app.when(bool(tool_map.loaded_slots(slots)), change), kind,
                   dot=e["slot_rgb"] if e["slot"] is not None else None, align="left", weight=1.35),
        ]))
    manual = any(e["manual"] for e in plan)
    notes = (("Elle seçim var.",) if manual else ("Malzeme ve renge göre seçildi.", "Değiştirmek için yuvaya dokun."))
    rows += [Row(20, [Label(app.tr(n), size="small", color="tertiary")]) for n in notes]
    rows.append(Row(44, [Button(app.tr("Otomatik"), app.when(manual, app.clear_print_tool_overrides),
                                icon="refresh")]))
    return [Card(rows, pad=8, gap=4)]


def print_options(app):
    """Start options sent to the G9111 start macro: bed mesh and clean-nozzle Z home (both on by default)."""
    def flip(name):
        setattr(app, name, not getattr(app, name))
        app.dirty = True

    return Row(44, [
        Button(app.tr("Bed mesh"), lambda: flip("print_bed_mesh"), "card", align="left",
               toggle=bool(app.print_bed_mesh)),
        Button(app.tr("Z ölçümü"), lambda: flip("print_home_z"), "card", align="left",
               toggle=bool(app.print_home_z)),
    ])


def _history_status(status, lang=i18n.DEFAULT_LANG):
    raw = str(status or "").lower()
    if raw == "completed":
        return i18n.tr("Tamamlandı", lang), "check", "ok"
    if raw == "cancelled":
        return i18n.tr("İptal edildi", lang), "close", "muted"
    if raw == "in_progress":
        return i18n.tr("Yazdırılıyor", lang), "play", "ok"
    if raw in ("error", "klippy_shutdown", "klippy_disconnect", "interrupted"):
        return i18n.tr("Hata", lang), "error", "danger"
    return raw or "?", "info", "muted"


def history_page(app, arg):
    totals = app.history_totals or {}
    total_jobs = int(totals.get("total_jobs") or 0)
    total_print_time = float(totals.get("total_print_time") or 0.0)
    total_filament = float(totals.get("total_filament_used") or 0.0)
    rows = [
        Card([
            section(app.tr("Özet"), app.tr("%d iş") % total_jobs, color="text"),
            Row(19, [Label(app.tr("Baskı süresi"), size="small", color="muted"),
                     Label(duration(total_print_time, app.lang), size="small", color="text", align="right")]),
            Row(19, [Label(app.tr("Toplam filament"), size="small", color="muted"),
                     Label(_format_filament_mm(total_filament), size="small", color="text", align="right")]),
        ], pad=6, gap=2),
    ]
    if app.history_error:
        rows.append(Card([
            Row(44, [Icon("error", "normal", "danger", weight=0.2),
                     Label(app.history_error, size="small", color="danger_text")]),
        ], color="danger_bg", border="danger_border", pad=8))
    jobs = list(app.history_jobs or ())
    pages = max(1, -(-len(jobs) // HISTORY_PER_PAGE))
    app.history_page = max(0, min(app.history_page, pages - 1))
    start = app.history_page * HISTORY_PER_PAGE
    page_items = jobs[start:start + HISTORY_PER_PAGE]
    if not jobs and not app.history_error:
        rows.append(Card([Row(60, [Label(app.tr("Henüz baskı geçmişi yok"), color="muted", align="center")])], pad=10))
    elif page_items:
        cards = []
        for job in page_items:
            jid = job.get("job_id")
            fname = job.get("filename") or "?"
            s_label, s_icon, s_color = _history_status(job.get("status"), app.lang)
            dur_val = job.get("print_duration") or job.get("total_duration") or 0.0
            dur_txt = duration(dur_val, app.lang)
            fil_txt = _format_filament_mm(job.get("filament_used"))
            date_txt = _format_date(job.get("start_time"))
            cards.append(Card([
                Row(19, [
                    Icon(s_icon, "small", s_color, weight=0.18),
                    Label(fname, size="small", color="text", weight=1.5),
                    Label(s_label, size="small", color=s_color, align="right", weight=0.95),
                ]),
                Row(18, [
                    Label("%s · %s" % (dur_txt, fil_txt), size="tab", color="muted", weight=1.2),
                    Label(date_txt, size="tab", color="tertiary", align="right", weight=0.9),
                ]),
            ], pad=5, gap=1, action=lambda jid=jid: app.open("history_detail", jid)))
        rows.append(Card(cards, color="bg", border=None, radius=0, pad=0, gap=4))

    def turn(delta):
        app.history_page += delta
        app.dirty = True

    rows.append(Row(44, [
        Button("", app.when(app.history_page > 0, lambda: turn(-1)), icon="prev"),
        Label("%d / %d" % (app.history_page + 1, pages), size="med", color="muted", align="center"),
        Button("", app.when(app.history_page < pages - 1, lambda: turn(1)), icon="next"),
        Button("", lambda: app.run_async(app.refresh_history), icon="refresh"),
    ]))
    return [Card(rows, color="bg", border=None, radius=0, pad=0, gap=4)]


def history_detail_page(app, job_id):
    job = next((j for j in (app.history_jobs or ()) if j.get("job_id") == job_id), None)
    if job is None:
        return [Card([Row(60, [Label(app.tr("Kayıt bulunamadı"), color="muted", align="center")])], pad=12)]
    fname = job.get("filename") or "?"
    s_label, s_icon, s_color = _history_status(job.get("status"), app.lang)
    print_dur = duration(job.get("print_duration") or 0.0, app.lang)
    total_dur = duration(job.get("total_duration") or 0.0, app.lang)
    fil_txt = _format_filament_mm(job.get("filament_used"))
    date_txt = _format_date(job.get("start_time"), with_year=True)
    exists = bool(job.get("exists"))
    meta_for_check = dict(job.get("metadata") or {})
    if "filament_total" not in meta_for_check and (app.meta.get(fname) or {}).get("filament_total"):
        meta_for_check["filament_total"] = app.meta[fname]["filament_total"]
    warn_info = app.check_print_filament_warning(fname, meta=meta_for_check)

    def do_reprint():
        app.start_print(fname)  # refuses while the heating lock is on

    def reprint():
        if app.heating_lock:
            app.notify(app.tr("Isıtma kilidi açık"))
            return
        if warn_info is not None:
            sp = warn_info["spool"]
            msg = app.tr(
                "Tahmini filament (%.0f g / %.1f m) kalan miktardan (%.0f g / %.1f m) fazla. Başlatılsın mı?"
            ) % (warn_info["needed_g"], warn_info["needed_m"], sp["remaining_g"], sp["remaining_m"])
            app.confirm(msg, do_reprint, "danger")
            return
        do_reprint()

    rows = [
        Card([
            Row(24, [Icon(s_icon, "normal", s_color, weight=0.22),
                     Label(fname, size="med", color="text", weight=1.8)]),
            Row(23, [status_badge(s_label, s_color, weight=0.95),
                     Label(date_txt, size="small", color="tertiary", align="right", weight=1.35)]),
            Rule(),
            Row(19, [Label(app.tr("Baskı süresi"), size="small", color="muted"),
                     Label(print_dur, size="small", color="text", align="right")]),
            Row(19, [Label(app.tr("Toplam süre"), size="small", color="muted"),
                     Label(total_dur, size="small", color="text", align="right")]),
            Row(19, [Label(app.tr("Kullanılan filament"), size="small", color="muted", weight=1.2),
                     Label(fil_txt, size="small", color="text", align="right", weight=0.8)]),
            Row(19, [Label(app.tr("Dosya durumu"), size="small", color="muted"),
                     Label(app.tr("Mevcut") if exists else app.tr("Dosya yok"),
                           size="small", color="ok" if exists else "danger_text", align="right")]),
        ], pad=10, gap=6),
    ]
    if warn_info is not None:
        sp = warn_info["spool"]
        rows.append(Card([Row(23, [
            Icon("warning", "small", "warn", weight=0.2),
            Label(app.tr("Filament yetersiz: kalan %.0f g") % sp["remaining_g"], size="small", color="warn", weight=1.8),
        ])], color="surface2", border="warn", pad=6, gap=0))
    locked = bool(app.heating_lock)
    rows.append(Row(48, [Button(app.tr("Tekrar yazdır"), app.when(app.idle and exists and not locked, reprint),
                                "primary", icon="lock" if locked else "play")]))
    return rows


def bed_mesh_badge(app, mesh):
    """Big rating badge: Good / Fair / Poor and the height range."""
    bg = {"ok": "active", "warn": "surface2", "danger": "danger_bg"}.get(mesh.rating_color, "surface2")
    border = {"ok": "accent", "warn": "warn", "danger": "danger_border"}.get(mesh.rating_color, "border_default")
    txt = {"ok": "active_text", "warn": "warn", "danger": "danger_text"}.get(mesh.rating_color, "text")
    return Card([Row(36, [Label(app.tr_short(mesh.rating_key), size="big", color=txt, weight=1.0),
                          Label("%.2f mm" % mesh.z_range, size="big", color="text", align="right", weight=1.0)])],
                color=bg, border=border, radius=12, pad=8, gap=0)


def bed_mesh_page(app, arg):
    """Helix-style: rating badge on top, one big 3D surface the finger can turn, "Re-measure" at the bottom."""
    mesh = app.bed_mesh_data()
    locked = bool(app.heating_lock)
    remeasure = Button(app.tr("Yeniden ölç"), app.when(app.idle and not locked, app.request_bed_remeasure),
                       "card", icon="lock" if locked else "refresh")
    if mesh is None:
        empty_msg = (app.tr("Tabla haritası yükleniyor...")
                     if not app._bed_mesh_mutable_loaded
                     else app.tr("Kayıtlı tabla haritası yok"))
        return [
            Card([
                Row(36, [Icon("level", "big", "muted")]),
                Row(24, [Label(empty_msg, size="med", color="muted", align="center")]),
            ], pad=12, gap=6),
            Row(46, [remeasure]),
        ]
    stock = mesh.source_kind == "stock"  # Klipper has no mesh: say where this one comes from
    rows = [bed_mesh_badge(app, mesh)]
    if stock:
        rows.append(Row(16, [Label(app.tr("Stok kayıt · Klipper'da yok"), size="tab", color="warn", align="center")]))
    rows.append(Row(233 if stock else 252,
                    [MeshSurface(mesh.matrix, rot=app.bed_mesh_rot, z_scale=app.bed_mesh_z_scale,
                                 tilt=app.bed_mesh_tilt, on_drag=app.bed_mesh_drag)]))
    rows.append(Row(46, [remeasure]))
    return [Card(rows, color="bg", border=None, radius=0, pad=0, gap=3)]
