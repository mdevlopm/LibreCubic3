"""Modal dialog builders (confirm, QWERTY keyboard, numeric keypad, action:prompt)."""

from .screens_common import blank
from .widgets import Button, Card, Label, Row, Spacer

# ---- modal dialogs ----

_KEYBOARD_LAYERS = {
    "lower": (
        tuple((c, c, 1.0) for c in "qwertyuiop"),
        tuple((c, c, 1.0) for c in "asdfghjkl"),
        tuple((c, c, 1.0) for c in "zxcvbnm"),
        ((".", ".", 1.0), (",", ",", 1.0), ("?", "?", 1.0), ("!", "!", 1.0), ("<-", "bksp", 2.0)),
        (("Shift", "layer:upper", 1.5), ("123", "layer:num", 1.5), ("Boşluk", " ", 3.0)),
    ),
    "upper": (
        tuple((c, c, 1.0) for c in "QWERTYUIOP"),
        tuple((c, c, 1.0) for c in "ASDFGHJKL"),
        tuple((c, c, 1.0) for c in "ZXCVBNM"),
        ((".", ".", 1.0), (",", ",", 1.0), ("?", "?", 1.0), ("!", "!", 1.0), ("<-", "bksp", 2.0)),
        (("abc", "layer:lower", 1.5), ("123", "layer:num", 1.5), ("Boşluk", " ", 3.0)),
    ),
    "num": (
        tuple((c, c, 1.0) for c in "1234567890"),
        tuple((c, c, 1.0) for c in "-/:;()$&@\""),
        tuple((c, c, 1.0) for c in ".?!'\#"),
        tuple((c, c, 1.0) for c in "*+=%^_"),
        (("<-", "bksp", 2.0),),
        (("abc", "layer:lower", 1.5), ("#+=", "layer:sym", 1.5), ("Boşluk", " ", 3.0)),
    ),
    "sym": (
        tuple((c, c, 1.0) for c in "[]{}#%^*+="),
        tuple((c, c, 1.0) for c in "_\\|~<>"),
        tuple((c, c, 1.0) for c in "@#$&()"),
        tuple((c, c, 1.0) for c in "/:;'\""),
        (("<-", "bksp", 2.0),),
        (("abc", "layer:lower", 1.5), ("123", "layer:num", 1.5), ("Boşluk", " ", 3.0)),
    ),
}


def format_keypad_number(value):
    """Short text of a keypad number: 60 -> '60', 2.5 -> '2.5'."""
    value = float(value)
    if value == int(value):
        return "%d" % int(value)
    return ("%.3f" % value).rstrip("0").rstrip(".")


def keypad_range_text(dialog):
    """'min–max' limit hint of a Keypad ('' when it has no limit at all)."""
    low = dialog.low
    if dialog.above is not None and (low is None or dialog.above >= low):
        lo_txt = ">" + format_keypad_number(dialog.above)
    elif low is not None:
        lo_txt = format_keypad_number(low)
    else:
        lo_txt = ""
    hi_txt = format_keypad_number(dialog.high) if dialog.high is not None else ""
    if not lo_txt and not hi_txt:
        return ""
    return "%s–%s" % (lo_txt, hi_txt)


def modal(app):
    from .app import Confirm, Keyboard, PromptDialog

    dialog = app.modal
    if isinstance(dialog, PromptDialog):
        return prompt_dialog_modal(app, dialog)
    if isinstance(dialog, Confirm):
        def accept():
            app.trigger_beep("confirm")
            app.close_modal()
            dialog.callback()

        msg_size = "med" if ("\n" in dialog.text or len(dialog.text) > 70) else "normal"
        rows = [
            blank(48),
            Card([Row(140 if not dialog.option_label else 112, [Label(dialog.text, size=msg_size, align="center")])],
                 color="surface2", border="border_default", radius=16, pad=16),
        ]
        if dialog.option_label:
            rows.append(Row(44, [Button(app.tr(dialog.option_label), app.toggle_confirm_option, "card",
                                        align="left", toggle=bool(dialog.option_state))]))
        rows.append(Row(48, [Button(app.tr(dialog.no_label), app.close_modal),
                             Button(app.tr(dialog.yes_label), accept, dialog.kind)]))
        return rows

    if isinstance(dialog, Keyboard):
        def on_key(action):
            if action == "bksp":
                dialog.text = dialog.text[:-1]
            elif action.startswith("layer:"):
                dialog.layer = action.split(":", 1)[1]
            elif len(dialog.text) < 64:
                dialog.text += action
            app.dirty = True

        def accept_text():
            value = dialog.text
            if not value:
                return
            app.close_modal()
            dialog.callback(value)

        if dialog.secret:
            if not dialog.text:
                shown = app.tr("Şifre girin")
                shown_color = "tertiary"
            elif len(dialog.text) <= 24:
                shown = "*" * len(dialog.text)
                shown_color = "text"
            else:
                shown = "(%d) %s" % (len(dialog.text), "*" * 20)
                shown_color = "text"
        else:
            shown = dialog.text or app.tr("Metin girin")
            shown_color = "text" if dialog.text else "tertiary"

        layer_rows = _KEYBOARD_LAYERS.get(dialog.layer, _KEYBOARD_LAYERS["lower"])
        key_cards = [
            Row(44, [Button(app.tr(lbl) if lbl == "Boşluk" else lbl, lambda a=act: on_key(a), "kbd", weight=w)
                     for lbl, act, w in row])
            for row in layer_rows
        ]
        return [
            Row(23, [Label(dialog.title, size="med", color="muted", align="center")]),
            Card([Row(30, [Label(shown, size="med", align="left", color=shown_color)])],
                 color="sunken", border="border_subtle", radius=10, pad=6),
            Card(key_cards, color="surface2", border="border_subtle", radius=10, pad=2, gap=2),
            Row(44, [
                Button(app.tr("Vazgeç"), app.close_modal),
                Button(app.tr(dialog.submit_label), app.when(bool(dialog.text), accept_text), "primary"),
            ]),
        ]

    def key(char):
        if dialog.fresh and char != "<":
            dialog.text = ""  # the first digit replaces the value the keypad opened with
        dialog.fresh = False
        if char == "<":
            dialog.text = dialog.text[:-1]
        elif char == ".":
            if "." not in dialog.text and len(dialog.text) < dialog.digits:
                dialog.text = (dialog.text or "0") + "."
        elif len(dialog.text) < dialog.digits:
            dialog.text += char
        app.dirty = True

    def parsed_value():
        if not dialog.text or dialog.text.endswith("."):
            return None
        try:
            return float(dialog.text) if dialog.allow_float else int(dialog.text)
        except ValueError:
            return None

    def accept():
        value = parsed_value()
        if value is None:
            return
        app.close_modal()
        dialog.callback(value)

    val = parsed_value()
    valid = (
        val is not None
        and (dialog.above is None or val > dialog.above)
        and (dialog.low is None or val >= dialog.low)
        and (dialog.high is None or val <= dialog.high)
    )
    range_txt = keypad_range_text(dialog)
    out_of_range = val is not None and not valid
    rows = [
        Row(24, [Label(dialog.title, size="med", color="muted", align="center")]),
    ]
    if range_txt:
        # min-max hint; while the typed value is outside the limits it turns into the reason Tamam is disabled
        rows.append(Row(19, [Label(app.tr("Sınır dışı: %s") % range_txt if out_of_range
                                   else app.tr("Aralık: %s") % range_txt,
                                   size="small", color="danger" if out_of_range else "tertiary", align="center")]))
    rows.append(Card([Row(46, [Label(dialog.text or "-", size="num", align="right",
                                     color="text" if valid or not dialog.text else "danger")])],
                     color="sunken", border="border_subtle", radius=10, pad=8))
    for chars in ("123", "456", "789", ".0<" if dialog.allow_float else " 0<"):
        rows.append(Row(52, [Button(c, lambda c=c: key(c), "key") if c != " " else Spacer() for c in chars]))
    rows.append(Row(48, [Button(app.tr("Vazgeç"), app.close_modal), Button(app.tr("Tamam"), app.when(valid, accept), "primary")]))
    return rows


_PROMPT_KIND_MAP = {
    "normal": "normal",
    "primary": "primary",
    "accent": "primary",
    "success": "primary",
    "danger": "danger",
    "error": "danger",
    "outline_danger": "outline_danger",
    "warning": "outline_danger",
    "warn": "outline_danger",
    "active": "active",
    "info": "active",
    "secondary": "normal",
    "card": "card",
    "ghost": "ghost",
}


def _make_prompt_button(app, btn):
    is_extrude = bool(app.extrusion_lock and app.script_extrudes(btn.gcode))
    kind = _PROMPT_KIND_MAP.get((btn.color or "normal").strip().lower(), "normal")
    if is_extrude and kind in ("primary", "danger", "active"):
        kind = "normal"
    # Empty gcode: just close the dialog
    if not btn.gcode:
        return Button(
            app.tr(btn.label),
            app.close_modal,
            kind,
            align="center",
        )
    return Button(
        app.tr(btn.label),
        app.when(not is_extrude, lambda g=btn.gcode: app.handle_prompt_button(g)),
        kind,
        icon="lock" if is_extrude else None,
        disabled=is_extrude,
        tooltip="extrusion_locked_msg" if is_extrude else "",
        align="center",
    )


def prompt_dialog_modal(app, dialog):
    """Build modal rows for a Klipper/ace-driver action:prompt dialog."""
    from .theme import FONT_SIZES
    from .widgets import text_width

    rows = []
    used_h = 0
    if dialog.title:
        title_txt = app.tr(dialog.title)
        title_h = 24 if text_width(title_txt, FONT_SIZES["med"]) * 1.15 <= 248 else 46
        rows.append(Row(title_h, [Label(title_txt, size="med", color="muted", align="center")]))
        used_h += title_h + 8

    main_btns = [_make_prompt_button(app, b) for b in dialog.buttons]
    footer_btns = [_make_prompt_button(app, b) for b in dialog.footer_buttons]
    close_btn = Button(app.tr_short("Kapat"), app.close_modal)

    # Calculate button layout height
    btn_rows = []
    # Main buttons: use 2 per row when >3, otherwise 1 per row
    if len(main_btns) <= 3:
        for b in main_btns:
            btn_rows.append(Row(42, [b]))
            used_h += 50
    else:
        for i in range(0, len(main_btns), 2):
            btn_rows.append(Row(40, main_btns[i:i + 2]))
            used_h += 48

    footer_rows = []
    if not footer_btns:
        footer_rows.append(Row(44, [close_btn]))
        used_h += 52
    elif len(footer_btns) == 1 and text_width(footer_btns[0].text, FONT_SIZES["small"]) * 1.15 <= 96:
        footer_rows.append(Row(44, [close_btn, footer_btns[0]]))
        used_h += 52
    else:
        for i in range(0, len(footer_btns), 2):
            footer_rows.append(Row(40, footer_btns[i:i + 2]))
            used_h += 48
        footer_rows.append(Row(42, [close_btn]))
        used_h += 50

    # Body text: fit in remaining space (screen is 480px, tab bar ~64px, margins ~24px = ~392px available)
    if dialog.text:
        body_txt = app.tr(dialog.text)
        # Reserve space for buttons + margins
        avail_text_h = max(48, 392 - used_h)
        text_h = min(130, avail_text_h)
        msg_size = "small" if ("\n" in body_txt or len(body_txt) > 90 or text_h < 80) else "med"
        rows.append(Card([Row(text_h, [Label(body_txt, size=msg_size, align="center")])],
                         color="surface2", border="border_default", radius=12, pad=12))

    rows.extend(btn_rows)
    rows.extend(footer_rows)
    return rows
