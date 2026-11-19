"""Draws the UI to an image with Pillow: PC preview and tests, not used on the printer."""

import os

from PIL import Image, ImageDraw, ImageFont

from . import bed_mesh
from .theme import COLORS, FONT_SIZES, ICON_SIZES, ICONS
from .widgets import (DOT_SIZE, HEIGHT, WIDTH, Bar, Button, Chart, Crosshair, Icon, Label, MeshSurface, Panel, Picture, Ring, Rule,
                      button_border, button_border_width, button_dot_radius, button_fill, button_icon_color,
                      button_icon_size, button_label, button_parts, button_radius, button_sub_label,
                      button_text_color, font_line_height, label_unit_size, swatch_check_color)

RINKHALS_ASSETS = "/useremain/rinkhals/.current/opt/rinkhals/ui/assets/"
LOCAL_FONTS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fonts") + "/"
FONT_PATHS = (
    os.environ.get("KOBRA_SCREEN_FONT", ""),
    LOCAL_FONTS + "AlibabaSans-Regular.ttf",
    RINKHALS_ASSETS + "AlibabaSans-Regular.ttf",
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/adwaita-sans-fonts/AdwaitaSans-Regular.ttf",
    "/usr/share/fonts/google-carlito-fonts/Carlito-Regular.ttf",
)
ICON_FONT_PATHS = (
    os.environ.get("KOBRA_SCREEN_ICON_FONT", ""),
    LOCAL_FONTS + "MaterialIcons-Regular.ttf",
    RINKHALS_ASSETS + "MaterialIcons-Regular.ttf",
)
# The printer's UI font: text measurements in the tests only hold with it (not shipped here, see README)
UI_FONT_PATHS = FONT_PATHS[:3]


def ui_font_available():
    return any(p and os.path.exists(p) for p in UI_FONT_PATHS)


SCALE = 2  # supersampling for smooth circles and arcs
_fonts = {}


def _load(paths, pixels):
    key = (paths, pixels)
    if key not in _fonts:
        _fonts[key] = next((ImageFont.truetype(p, pixels * SCALE) for p in paths if p and os.path.exists(p)), None)
    return _fonts[key]


def font(size):
    return _load(FONT_PATHS, FONT_SIZES[size]) or ImageFont.load_default(size=FONT_SIZES[size] * SCALE)


def icon_font(size):
    return _load(ICON_FONT_PATHS, ICON_SIZES[size])


def text_width(text, size):
    return ImageDraw.Draw(Image.new("L", (1, 1))).textlength(text, font=font(size)) / SCALE


def text_line_height(size):
    ascent, descent = font(size).getmetrics()
    return (ascent + descent) / SCALE


def wrap(draw, text, fnt, width):
    """Split text into lines that fit `width`, breaking long words if needed."""
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split(" "):
            candidate = (line + " " + word).strip()
            if draw.textlength(candidate, font=fnt) <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
            while draw.textlength(word, font=fnt) > width and len(word) > 1:
                cut = len(word)
                while cut > 1 and draw.textlength(word[:cut], font=fnt) > width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            line = word
        lines.append(line)
    return lines


class Canvas:
    """Drawing in panel pixels on a supersampled image."""

    def __init__(self):
        self.image = Image.new("RGB", (WIDTH * SCALE, HEIGHT * SCALE), COLORS["bg"])
        self.draw = ImageDraw.Draw(self.image)

    @staticmethod
    def rgb(color):
        return COLORS[color] if isinstance(color, str) else tuple(color)

    def rect(self, x, y, w, h, radius, color=None, border=None, border_width=1):
        s = SCALE
        self.draw.rounded_rectangle(
            (x * s, y * s, (x + w) * s - 1, (y + h) * s - 1),
            radius * s,
            fill=self.rgb(color) if color else None,
            outline=self.rgb(border) if border else None,
            width=border_width * s if border else 0,
        )

    def circle(self, cx, cy, radius, color, outline=None, width=0):
        s = SCALE
        self.draw.ellipse(((cx - radius) * s, (cy - radius) * s, (cx + radius) * s, (cy + radius) * s),
                          fill=self.rgb(color), outline=self.rgb(outline) if outline else None, width=width * s)

    def arc(self, cx, cy, radius, start, end, color, width):
        s = SCALE
        self.draw.arc(((cx - radius) * s, (cy - radius) * s, (cx + radius) * s, (cy + radius) * s),
                      start - 90, end - 90, fill=self.rgb(color), width=width * s)

    def text(self, box, text, size, color, align="left", unit="", clip=False):
        """Draw wrapped text centred vertically in box; `clip` draws one line without wrapping (button labels)."""
        x, y, w, h = box
        fnt = font(size)
        ascent, descent = fnt.getmetrics()
        line_h = (ascent + descent) / SCALE + 1
        if unit:
            ufnt = font(label_unit_size(size))
            uascent, _ = ufnt.getmetrics()
            tw = self.draw.textlength(text, font=fnt) / SCALE
            uw = self.draw.textlength(unit, font=ufnt) / SCALE
            gap = 2
            total_w = tw + gap + uw
            left = x if align == "left" else x + w - total_w if align == "right" else x + (w - total_w) / 2
            top = y + (h - line_h) / 2
            utop = top + (ascent - uascent) / SCALE
            self.draw.text((left * SCALE, top * SCALE), text, font=fnt, fill=self.rgb(color))
            self.draw.text(((left + tw + gap) * SCALE, utop * SCALE), unit, font=ufnt, fill=self.rgb("muted"))
            return
        if clip:
            lines = [line for line in text.split("\n")][:max(1, int(h // line_h))]
        else:
            lines = wrap(self.draw, text, fnt, w * SCALE)
            lines = lines[:max(1, int(h // line_h))]
        top = y + (h - line_h * len(lines)) / 2
        for i, line in enumerate(lines):
            length = self.draw.textlength(line, font=fnt) / SCALE
            left = x if align == "left" else x + w - length if align == "right" else x + (w - length) / 2
            self.draw.text((left * SCALE, (top + i * line_h) * SCALE), line, font=fnt, fill=self.rgb(color))

    def icon(self, cx, cy, name, size, color):
        fnt = icon_font(size)
        if fnt is None:
            return
        pixels = ICON_SIZES[size]
        self.draw.text(((cx - pixels / 2) * SCALE, (cy - pixels / 2) * SCALE), chr(ICONS[name]), font=fnt,
                       fill=self.rgb(color))

    def dot(self, cx, cy, radius, color, ring):
        if ring:
            self.circle(cx, cy, radius + 3, "accent")
            self.circle(cx, cy, radius + 1, "card")
        self.circle(cx, cy, radius, "spool_rim")
        inner = max(3.0, radius - 2.5)
        self.circle(cx, cy, inner, color, outline="border_strong", width=1)
        self.circle(cx, cy, max(3.5, radius * 0.28), "card")


def draw_button(canvas, item):
    widget = item.widget
    if widget.kind == "estop":
        cx, cy = item.x + item.w / 2.0, item.y + item.h / 2.0
        canvas.circle(cx, cy, 13, "danger", outline="danger_border", width=1)
        canvas.icon(cx, cy, widget.icon or "stop", button_icon_size(item), button_icon_color(widget))
        return
    fill = button_fill(widget)
    border = button_border(widget)
    if fill or border:
        canvas.rect(item.x, item.y, item.w, item.h, button_radius(widget), fill, border=border,
                    border_width=button_border_width(widget))
    if widget.swatch is not None:
        if widget.ring:
            canvas.icon(item.x + item.w / 2.0, item.y + item.h / 2.0, "check", "normal",
                        swatch_check_color(widget.swatch))
        return
    if widget.kind == "tab_active":
        bar_w = min(32, max(16, item.w - 16))
        canvas.rect(item.x + (item.w - bar_w) / 2.0, item.y, bar_w, 3, 1.5, "accent")
    t_color = button_text_color(widget)
    i_color = button_icon_color(widget)
    centre, box = button_parts(item)
    if box:
        if widget.sub:
            sub_color = "active_text" if widget.kind == "active" else "tertiary"
            top = box[1] + (box[3] - 40) / 2.0
            main_size, main_text, sub_text = button_sub_label(item, box[2])
            canvas.text((box[0], top, box[2], 19), sub_text, "small", sub_color, widget.align, clip=True)
            canvas.text((box[0], top + 18, box[2], 22), main_text, main_size, t_color, widget.align, clip=True)
        else:
            size, text = button_label(item)
            canvas.text(box, text, size, t_color, widget.align, clip=True)
    if centre and widget.dot is not None:
        radius = button_dot_radius(item)
        canvas.dot(centre[0], centre[1], radius, widget.dot, widget.ring)
    elif centre:
        canvas.icon(centre[0], centre[1], widget.icon, button_icon_size(item), i_color)
    if widget.right_icon:
        rc = "tertiary" if widget.action is not None else "disabled_text"
        canvas.icon(item.x + item.w - 18, item.y + item.h / 2.0, widget.right_icon, "normal", rc)
    if widget.toggle is not None:
        tx, ty, tw, th = item.x + item.w - 52, item.y + (item.h - 22) / 2.0, 40, 22
        t_bg = "disabled" if widget.action is None else ("accent" if widget.toggle else "border_default")
        k_bg = "disabled_text" if widget.action is None else ("text" if widget.toggle else "muted")
        canvas.rect(tx, ty, tw, th, th / 2.0, t_bg)
        kx = tx + tw - 11 if widget.toggle else tx + 11
        canvas.circle(kx, ty + th / 2.0, 8, k_bg)


def draw_ring(canvas, item):
    widget = item.widget
    radius = min(item.w, item.h) / 2 - 2
    cx, cy = item.x + item.w / 2, item.y + item.h / 2
    canvas.arc(cx, cy, radius, 0, 360, "track", 7)
    fraction = max(0.0, min(1.0, widget.fraction))
    if fraction > 0:
        canvas.arc(cx, cy, radius, 0, 360 * fraction, widget.color, 7)
    if widget.sub:
        canvas.text((item.x, cy - 26, item.w, 35), widget.text, "big", "text", "center")
        canvas.text((item.x, cy + 6, item.w, 19), widget.sub, "small", "tertiary", "center")
    else:
        canvas.text((item.x, cy - 17, item.w, 35), widget.text, "big", "text", "center")


def draw_picture(canvas, item):
    try:
        picture = Image.open(item.widget.path).convert("RGB")
    except OSError:
        return
    side = min(item.w, item.h) * SCALE
    picture = picture.resize((side, side))
    canvas.image.paste(picture, (int((item.x + item.w / 2) * SCALE - side / 2),
                                 int((item.y + item.h / 2) * SCALE - side / 2)))


def draw_chart(canvas, item):
    widget = item.widget
    canvas.rect(item.x, item.y, item.w, item.h, 8, "sunken", border="border_subtle", border_width=1)
    pad = 4
    x0 = item.x + pad
    y0 = item.y + pad
    gw = max(4, item.w - 2 * pad)
    gh = max(4, item.h - 2 * pad)
    s = SCALE
    div_col = canvas.rgb("divider")
    for i in range(1, 4):
        gy = (y0 + gh * i / 4.0) * s
        canvas.draw.line((x0 * s, gy, (x0 + gw) * s, gy), fill=div_col, width=1)
    for i in range(1, 5):
        gx = (x0 + gw * i / 5.0) * s
        canvas.draw.line((gx, y0 * s, gx, (y0 + gh) * s), fill=div_col, width=1)

    y_min = float(widget.y_min)
    y_max = float(widget.y_max)
    if y_max <= y_min:
        y_max = y_min + 10.0
    span = y_max - y_min
    cnt = max(2, int(widget.point_count))

    for values, color_name, dashed in widget.series:
        vals = list(values or ())[-cnt:]
        if not vals:
            continue
        pad_left = cnt - len(vals)
        pts = []
        for idx, v in enumerate(vals):
            if v is None:
                if len(pts) >= 2:
                    _draw_polyline(canvas, pts, color_name, dashed)
                pts = []
                continue
            clamped = max(y_min, min(y_max, float(v)))
            px = x0 + (pad_left + idx) * gw / float(cnt - 1)
            py = y0 + gh - ((clamped - y_min) / span) * gh
            pts.append((px * s, py * s))
        if len(pts) >= 2:
            _draw_polyline(canvas, pts, color_name, dashed)
        elif len(pts) == 1:
            px, py = pts[0]
            canvas.draw.ellipse((px - 2, py - 2, px + 2, py + 2), fill=canvas.rgb(color_name))


def _draw_polyline(canvas, pts, color_name, dashed):
    col = canvas.rgb(color_name)
    if not dashed:
        canvas.draw.line(pts, fill=col, width=2 * SCALE)
        return
    for i in range(len(pts) - 1):
        if i % 2 == 0:
            canvas.draw.line((pts[i], pts[i + 1]), fill=col, width=max(2, SCALE))


def draw_mesh_surface(canvas, item):
    widget = item.widget
    s = SCALE
    canvas.rect(item.x, item.y, item.w, item.h, 8, "sunken", border="border_subtle")
    quads, base_outline, front_edge = bed_mesh.project_mesh_quads(
        widget.matrix, item.w * s, item.h * s, rot_deg=widget.rot, z_scale=widget.z_scale, pad=10 * s,
        tilt=widget.tilt
    )
    ox, oy = item.x * s, item.y * s
    if base_outline:
        pts_base = [(ox + px, oy + py) for px, py in base_outline]
        for i in range(4):
            canvas.draw.line((pts_base[i], pts_base[(i + 1) % 4]), fill=canvas.rgb("divider"), width=s)
    if front_edge is not None:
        p0, p1 = front_edge
        canvas.draw.line(((ox + p0[0], oy + p0[1]), (ox + p1[0], oy + p1[1])),
                         fill=canvas.rgb("accent"), width=max(2, s))
    for pts, fill_rgb, edge_rgb, _ in quads:
        poly = [(ox + px, oy + py) for px, py in pts]
        canvas.draw.polygon(poly, fill=fill_rgb, outline=edge_rgb)


def render(placed):
    canvas = Canvas()
    for item in placed:
        widget = item.widget
        if isinstance(widget, Panel):
            canvas.rect(item.x, item.y, item.w, item.h, widget.radius, widget.color,
                        border=widget.border, border_width=widget.border_width)
        elif isinstance(widget, Rule):
            canvas.rect(item.x, item.y + item.h // 2, item.w, 1, 0, widget.color)
        elif isinstance(widget, Label):
            # Auto-clip labels in short rows to prevent multi-line wrap and overlap
            line_h = font_line_height(FONT_SIZES[widget.size])
            auto_clip = (item.h < line_h * 1.8)  # single-line row
            canvas.text((item.x, item.y, item.w, item.h), widget.text, widget.size, widget.color, widget.align,
                        unit=widget.unit, clip=auto_clip)
        elif isinstance(widget, Icon):
            canvas.icon(item.x + item.w / 2, item.y + item.h / 2, widget.name, widget.size, widget.color)
        elif isinstance(widget, Button):
            draw_button(canvas, item)
        elif isinstance(widget, Bar):
            canvas.rect(item.x, item.y, item.w, item.h, item.h // 2, "track")
            filled = round(item.w * max(0.0, min(1.0, widget.fraction)))
            if filled >= item.h:
                canvas.rect(item.x, item.y, filled, item.h, item.h // 2, widget.color)
        elif isinstance(widget, Ring):
            draw_ring(canvas, item)
        elif isinstance(widget, Crosshair):
            cx, cy = item.x + item.w / 2.0, item.y + item.h / 2.0
            r = min(item.w, item.h) / 2.0 - 2.0
            canvas.circle(cx, cy, r, "surface2", outline=widget.color, width=2)
            canvas.circle(cx, cy, 3, widget.color)
        elif isinstance(widget, Picture):
            draw_picture(canvas, item)
        elif isinstance(widget, Chart):
            draw_chart(canvas, item)
        elif isinstance(widget, MeshSurface):
            draw_mesh_surface(canvas, item)
    return canvas.image.resize((WIDTH, HEIGHT), Image.LANCZOS)
