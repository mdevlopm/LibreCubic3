"""Widget building blocks for LVGL rendering on Kobra 3."""

import sys

from . import bed_mesh
from .lvgl_sys import (_default_state, _enum_val, _is_valid_png, _lv, _part_indicator, _part_knob, _text_align,
                       color, DEFAULT_EXT_CLICK_AREA, FONT_FILE, FONT_SIZES, ICON_FONT_FILE, ICON_SIZES, ICONS,
                       LV_CHART_POINT_NONE, LV_PART_INDICATOR, LV_PART_ITEMS, LV_PART_KNOB, RADIUS_CIRCLE)
from .theme import COLORS
from .widgets import (DOT_SIZE, HEIGHT, WIDTH, Bar, Button, Chart, Crosshair, Icon, Label, MeshSurface, Panel,  # noqa: F401
                      Picture, Ring, Rule, text_width, button_border, button_border_width,
                      button_dot_radius, button_fill, button_icon_color, button_icon_size, button_label,
                      button_parts, button_radius, button_sub_label, button_text_color,
                      label_unit_size, swatch_check_color)


class _MeshState:
    """Drag / drawing state of one MeshSurface picture (kept in LvglScreen._mesh_map, not on the lvgl object)."""

    def __init__(self, image, widget):
        self.image = image
        self.widget = widget
        self.dragging = False
        self.drag = None
        self.drag_shown = None
        self.can_stretch = False
        self.key = None
        self.rot = float(widget.rot) % 360.0
        self.tilt = float(widget.tilt)


class _LvglWidgetsMixin:
    """Mixin providing LVGL widget creation and update methods."""

    @staticmethod
    def _load_font(path, size):
        lv = _lv()
        font = None
        if hasattr(lv, "tiny_ttf_create_file"):
            try:
                font = lv.tiny_ttf_create_file(path, int(size))
            except Exception:
                font = None
        if font is None:
            sys.stderr.write(
                "kobra-screen: warning: failed to load TTF font %r (size %d); falling back to default font\n"
                % (path, int(size))
            )
            default_fn = getattr(lv, "font_get_default", None)
            if default_fn is not None:
                font = default_fn()
        return font

    @staticmethod
    def _plain(obj, x, y, w, h):
        lv = _lv()
        default = _default_state()
        obj.set_pos(int(x), int(y))
        obj.set_size(int(w), int(h))
        obj.set_style_border_width(0, default)
        obj.set_style_shadow_width(0, default)
        obj.set_style_pad_all(0, default)
        obj.remove_flag(lv.OBJ_FLAG.SCROLLABLE)

    def _font_line_height(self, size):
        fnt = self.fonts.get(size)
        if fnt is not None and hasattr(fnt, "get_line_height"):
            try:
                lh = fnt.get_line_height()
                if isinstance(lh, (int, float)) and lh > 0:
                    return float(lh)
            except Exception:
                pass
        return float(FONT_SIZES[size]) * 1.57

    def _box(self, parent, x, y, w, h):
        """Invisible, untouchable container."""
        lv = _lv()
        box = lv.obj(parent)
        self._plain(box, x, y, w, h)
        box.set_style_bg_opa(lv.OPA.TRANSP, _default_state())
        box.remove_flag(lv.OBJ_FLAG.CLICKABLE)
        return box

    def _text(self, parent, box, size, align, unit="", clip=False):
        """Label centred vertically inside box (x, y, w, h relative to parent).

        `clip` is for button labels: the text is already cut to fit by widgets.button_label, so the label
        must never wrap (LVGL would otherwise break it inside a word); CLIP also keeps a stray pixel
        from growing the label.
        """
        lv = _lv()
        default = _default_state()
        holder = self._box(parent, *box)
        label = lv.label(holder)
        label._box_w = int(box[2])
        label._box_h = int(box[3])
        label._font_size = size
        label._align = align
        label.set_width(int(box[2]))
        if self.fonts.get(size) is not None:
            label.set_style_text_font(self.fonts[size], default)
        label.set_style_text_align(_text_align(align), default)
        if unit:
            usize = label_unit_size(size)
            label.unit_label = lv.label(holder)
            if self.fonts.get(usize) is not None:
                label.unit_label.set_style_text_font(self.fonts[usize], default)
            label.unit_label.set_style_text_color(color("muted"), default)
            label.set_long_mode(lv.LABEL_LONG_MODE.CLIP)
        else:
            label.unit_label = None
            if clip:
                # one line high, so LVGL cannot grow the label into a second line
                label.set_height(max(1, min(int(box[3]), int(round(self._font_line_height(size))))))
                label.set_long_mode(lv.LABEL_LONG_MODE.CLIP)
            elif int(box[3]) < 36:
                label.set_long_mode(lv.LABEL_LONG_MODE.DOTS)
            label.center()
        return label

    def _glyph(self, parent, cx, cy, size):
        lv = _lv()
        pixels = ICON_SIZES[size]
        label = lv.label(parent)
        if self.icon_fonts.get(size) is not None:
            label.set_style_text_font(self.icon_fonts[size], _default_state())
        label.set_pos(int(cx - pixels / 2), int(cy - pixels / 2))
        return label

    # ---- widgets ----

    @staticmethod
    def _shape(item):
        widget = item.widget
        centered_pair = (isinstance(widget, Button) and (widget.icon or widget.dot is not None)
                         and not widget.vertical and widget.align == "center")
        return (type(widget).__name__, item.x, item.y, item.w, item.h, getattr(widget, "size", None),
                getattr(widget, "align", None), getattr(widget, "icon", None), getattr(widget, "vertical", None),
                getattr(widget, "dot", None) is not None, bool(getattr(widget, "sub", "")),
                bool(getattr(widget, "unit", "")), getattr(widget, "swatch", None) is not None,
                getattr(widget, "right_icon", None), getattr(widget, "toggle", None) is not None,
                getattr(widget, "path", None), getattr(widget, "radius", None),
                getattr(widget, "kind", None) == "tab_active",
                getattr(widget, "kind", None) == "estop",
                isinstance(widget, Panel) and widget.action is not None,
                widget.point_count if isinstance(widget, Chart) else None,
                tuple(c for _, c, _ in widget.series) if isinstance(widget, Chart) else None,
                # Exclude button text and sub_label text from shape to prevent root.clean() on text changes
                None if isinstance(widget, Button) else None,
                None)

    def _bind_click(self, obj, index):
        ptr = getattr(obj, "_pointer", None)
        if ptr is not None:
            self._click_map[ptr] = index
        self._click_map[id(obj)] = index
        lv = _lv()
        obj.add_event_cb(self._on_click, lv.EVENT_CODE.CLICKED, None)

    def _on_click(self, event):
        target = event.get_current_target_obj()
        if target is None:
            return
        index = self._click_map.get(id(target))
        if index is None:
            ptr = getattr(target, "_pointer", None)
            if ptr is not None:
                index = self._click_map.get(ptr)
        if index is not None:
            self._clicked(index)

    @staticmethod
    def _mesh_key(widget, rot, tilt, w, h):
        return (widget.matrix, int(round(rot)) % 360, int(round(bed_mesh.clamp_tilt(tilt))), int(widget.z_scale),
                int(w), int(h))

    def _mesh_state_of(self, image):
        ptr = getattr(image, "_pointer", None)
        state = self._mesh_map.get(ptr) if ptr is not None else None
        return state or self._mesh_map.get(id(image))

    def _update_mesh_surface(self, image, widget, w, h):
        state = self._mesh_state_of(image)
        if state is None:
            return
        state.widget = widget
        if state.dragging:
            return  # the finger owns the picture until it lifts
        self._draw_mesh_exact(image, state, widget, float(widget.rot), float(widget.tilt), w, h)

    @staticmethod
    def _draw_mesh_exact(image, state, widget, rot, tilt, w, h):
        lv = _lv()
        key = _LvglWidgetsMixin._mesh_key(widget, rot, tilt, w, h)
        if key == state.key:
            state.rot, state.tilt = rot % 360.0, bed_mesh.clamp_tilt(tilt)
            return
        try:
            png_path = bed_mesh.render_mesh_surface_png(
                widget.matrix, w, h, rot_deg=rot, z_scale=widget.z_scale, tilt=tilt
            )
            if png_path and _is_valid_png(png_path):
                image.set_src(png_path)
                image._loaded_path = png_path
                image._mesh_key = key
                state.key = key
                state.rot, state.tilt = rot % 360.0, bed_mesh.clamp_tilt(tilt)
        except Exception:
            pass

    # Drag sensitivity of the 3D bed mesh: degrees per pixel of finger travel.
    MESH_ROT_PER_PX = 0.8
    MESH_TILT_PER_PX = 0.35

    def _on_mesh_event(self, event):
        """Press / drag / release on the mesh picture: horizontal turns it, vertical tilts it.

        One bound method serves every mesh picture (like _on_click): Rinkhals' lvgl keeps each distinct callback
        object forever in a class-level dict, so a new lambda per picture would leak.  The per-picture drag state
        lives in self._mesh_map, not on the lvgl object.

        While the finger moves, a half-size picture at 5 degree steps is shown (cached files, no layout pass);
        on release the exact angle is drawn at full size.
        """
        lv = _lv()
        # Ignore mesh events during raw capture, sleep, or when a wakeup click should be swallowed
        if self._raw_capture_active or self._asleep or self._swallow_click:
            return
        target = event.get_current_target_obj()
        if target is None:
            return
        state = self._mesh_state_of(target)
        if state is None or state.widget is None:
            return
        image, widget = state.image, state.widget
        try:
            code = _enum_val(event.get_code())
        except Exception:
            return
        pressed = _enum_val(lv.EVENT_CODE.PRESSED)
        pressing = _enum_val(lv.EVENT_CODE.PRESSING)
        ended = {_enum_val(getattr(lv.EVENT_CODE, n)) for n in ("RELEASED", "PRESS_LOST") if hasattr(lv.EVENT_CODE, n)}
        if code == pressed:
            px, py = self._read_point_xy()
            if px is None:
                return
            state.drag = (px, py, state.rot, state.tilt)
            state.dragging = True
            state.drag_shown = None
        elif code == pressing and state.drag is not None:
            px, py = self._read_point_xy()
            if px is None:
                return
            x0, y0, rot0, tilt0 = state.drag
            rot = (rot0 + (px - x0) * self.MESH_ROT_PER_PX) % 360.0
            tilt = bed_mesh.clamp_tilt(tilt0 + (py - y0) * self.MESH_TILT_PER_PX)
            step = bed_mesh.DRAG_STEP
            shown = (int(round(rot / step)) * step % 360, int(round(tilt / step)) * step)
            state.rot, state.tilt = rot, tilt
            if shown != state.drag_shown and state.can_stretch:
                try:
                    path = bed_mesh.render_drag_preview_png(widget.matrix, image._box_w, image._box_h, rot, tilt,
                                                            z_scale=widget.z_scale)
                    if _is_valid_png(path):
                        image.set_src(path)
                        state.drag_shown = shown
                except Exception:
                    pass
            if widget.on_drag is not None:
                widget.on_drag(rot, tilt, False)
        elif code in ended and state.drag is not None:
            state.drag = None
            state.dragging = False
            rot, tilt = state.rot, state.tilt
            state.key = None  # the preview is on screen: always put the exact picture back
            self._draw_mesh_exact(image, state, widget, rot, tilt, image._box_w, image._box_h)
            if widget.on_drag is not None:
                widget.on_drag(rot, tilt, True)

    @staticmethod
    def _update_picture(image, path):
        if path and path != getattr(image, "_loaded_path", None) and _is_valid_png(path):
            try:
                image.set_src(path)
                width = image.get_self_width() or 0
                height = image.get_self_height() or 0
                span = max(width, height)
                if span > 0:
                    image.set_scale(max(1, image._side * 256 // span))
                    image._loaded_path = path
            except Exception:
                pass

    def _create(self, index, item):
        lv = _lv()
        default = _default_state()
        widget = item.widget
        if isinstance(widget, Panel):
            panel = self._box(self.root, item.x, item.y, item.w, item.h)
            panel.set_style_bg_opa(lv.OPA.COVER, default)
            panel.set_style_radius(widget.radius, default)
            if widget.action is not None:
                panel.add_flag(lv.OBJ_FLAG.CLICKABLE)
                press_lock = getattr(lv.OBJ_FLAG, "PRESS_LOCK", None)
                if press_lock is not None:
                    panel.add_flag(press_lock)
                self._bind_click(panel, index)
            return panel
        if isinstance(widget, Rule):
            line = self._box(self.root, item.x, item.y + item.h // 2, item.w, 1)
            line.set_style_bg_opa(lv.OPA.COVER, default)
            return line
        if isinstance(widget, Label):
            # Auto-clip labels in short rows to prevent multi-line wrap and overlap
            line_h = self._font_line_height(widget.size)
            auto_clip = (item.h < line_h * 1.8) and not widget.unit  # single-line row (unit has own clip)
            return self._text(self.root, (item.x, item.y, item.w, item.h), widget.size, widget.align, widget.unit,
                              clip=auto_clip)
        if isinstance(widget, Icon):
            return self._glyph(self.root, item.x + item.w / 2, item.y + item.h / 2, widget.size)
        if isinstance(widget, Button):
            button = lv.button(self.root)
            self._plain(button, item.x, item.y, item.w, item.h)
            button.set_style_radius(button_radius(widget), default)
            press_lock = getattr(lv.OBJ_FLAG, "PRESS_LOCK", None)
            if press_lock is not None:
                button.add_flag(press_lock)
            scroll_on_focus = getattr(lv.OBJ_FLAG, "SCROLL_ON_FOCUS", None)
            if scroll_on_focus is not None:
                button.remove_flag(scroll_on_focus)
            centre, box = button_parts(item)
            button.text_label = button.sub_label = button.glyph = button.r_glyph = None
            button.display_text = button.display_sub = None
            button.dot = button.halo = button.rim = button.hole = None
            button.tab_bar = button.toggle_track = button.toggle_knob = None
            if widget.swatch is not None:
                button.glyph = self._glyph(button, item.w / 2.0, item.h / 2.0, "normal")
                self._bind_click(button, index)
                return button
            if widget.kind == "estop":
                button.rim = self._box(button, (item.w - 26) / 2.0, (item.h - 26) / 2.0, 26, 26)
                button.rim.set_style_radius(RADIUS_CIRCLE, default)
                button.rim.set_style_bg_opa(lv.OPA.COVER, default)
                button.rim.set_style_bg_color(color("danger"), default)
                button.rim.set_style_border_width(1, default)
                button.rim.set_style_border_color(color("danger_border"), default)
                button.glyph = self._glyph(button, item.w / 2.0, item.h / 2.0, button_icon_size(item))
                button.glyph.set_text(chr(ICONS[widget.icon or "stop"]))
                self._bind_click(button, index)
                return button
            if widget.kind == "tab_active":
                bar_w = min(32, max(16, item.w - 16))
                button.tab_bar = self._box(button, (item.w - bar_w) / 2.0, 0, bar_w, 3)
                button.tab_bar.set_style_radius(2, default)
                button.tab_bar.set_style_bg_opa(lv.OPA.COVER, default)
                button.tab_bar.set_style_bg_color(color("accent"), default)
            if box:
                bx, by, bw, bh = box[0] - item.x, box[1] - item.y, box[2], box[3]
                if widget.sub:
                    top = by + (bh - 40) / 2.0
                    main_size, main_text, sub_text = button_sub_label(item, bw)
                    button.sub_label = self._text(button, (bx, top, bw, 19), "small", widget.align, clip=True)
                    button.text_label = self._text(button, (bx, top + 18, bw, 22), main_size, widget.align,
                                                   clip=True)
                    button.display_text, button.display_sub = main_text, sub_text
                    button.fit_size = main_size
                else:
                    size, text = button_label(item)
                    button.text_label = self._text(button, (bx, by, bw, bh), size, widget.align, clip=True)
                    button.display_text, button.display_sub = text, widget.sub
                    button.fit_size = size
            if centre and widget.dot is not None:
                radius = button_dot_radius(item)
                cx, cy = centre[0] - item.x, centre[1] - item.y
                button.halo = self._box(button, cx - radius - 3, cy - radius - 3, 2 * radius + 6, 2 * radius + 6)
                button.halo.set_style_radius(RADIUS_CIRCLE, default)
                button.halo.set_style_border_color(color("accent"), default)
                button.rim = self._box(button, cx - radius, cy - radius, 2 * radius, 2 * radius)
                button.rim.set_style_radius(RADIUS_CIRCLE, default)
                button.rim.set_style_bg_opa(lv.OPA.COVER, default)
                button.rim.set_style_bg_color(color("spool_rim"), default)
                inner = max(3.0, radius - 2.5)
                button.dot = self._box(button, cx - inner, cy - inner, 2 * inner, 2 * inner)
                button.dot.set_style_radius(RADIUS_CIRCLE, default)
                button.dot.set_style_bg_opa(lv.OPA.COVER, default)
                button.dot.set_style_border_width(1, default)
                button.dot.set_style_border_color(color("border_strong"), default)
                hole = max(3.5, radius * 0.28)
                button.hole = self._box(button, cx - hole, cy - hole, 2 * hole, 2 * hole)
                button.hole.set_style_radius(RADIUS_CIRCLE, default)
                button.hole.set_style_bg_opa(lv.OPA.COVER, default)
                button.hole.set_style_bg_color(color("card"), default)
            elif centre:
                button.glyph = self._glyph(button, centre[0] - item.x, centre[1] - item.y, button_icon_size(item))
                button.glyph.set_text(chr(ICONS[widget.icon]))
            if widget.right_icon:
                button.r_glyph = self._glyph(button, item.w - 18, item.h / 2.0, "normal")
                button.r_glyph.set_text(chr(ICONS[widget.right_icon]))
            if widget.toggle is not None:
                button.toggle_track = self._box(button, item.w - 52, (item.h - 22) / 2.0, 40, 22)
                button.toggle_track.set_style_radius(RADIUS_CIRCLE, default)
                button.toggle_track.set_style_bg_opa(lv.OPA.COVER, default)
                button.toggle_knob = self._box(button.toggle_track, 3, 3, 16, 16)
                button.toggle_knob.set_style_radius(RADIUS_CIRCLE, default)
                button.toggle_knob.set_style_bg_opa(lv.OPA.COVER, default)
            self._bind_click(button, index)
            return button
        if isinstance(widget, Bar):
            bar = lv.bar(self.root)
            self._plain(bar, item.x, item.y, item.w, item.h)
            bar.remove_flag(lv.OBJ_FLAG.CLICKABLE)
            bar.set_range(0, 1000)
            bar.set_style_bg_color(color("track"), default)
            bar.set_style_bg_color(color(widget.color), _part_indicator())
            return bar
        if isinstance(widget, Ring):
            side = min(item.w, item.h) - 4
            arc = lv.arc(self.root)
            self._plain(arc, item.x + (item.w - side) / 2, item.y + (item.h - side) / 2, side, side)
            arc.remove_flag(lv.OBJ_FLAG.CLICKABLE)
            arc.set_bg_angles(0, 360)
            arc.set_rotation(270)
            arc.set_range(0, 1000)
            arc.set_style_arc_width(7, default)
            arc.set_style_arc_width(7, _part_indicator())
            arc.set_style_arc_color(color("track"), default)
            arc.set_style_bg_opa(lv.OPA.TRANSP, _part_knob())
            if widget.sub:
                arc.text_label = self._text(arc, (0, side / 2 - 26, side, 35), "big", "center")
                arc.sub_label = self._text(arc, (0, side / 2 + 6, side, 19), "small", "center")
                arc.sub_label.set_style_text_color(color("tertiary"), default)
            else:
                arc.text_label = self._text(arc, (0, side / 2 - 17, side, 35), "big", "center")
                arc.sub_label = None
            return arc
        if isinstance(widget, Crosshair):
            ring = self._box(self.root, item.x, item.y, item.w, item.h)
            ring.set_style_radius(RADIUS_CIRCLE, default)
            ring.set_style_bg_opa(lv.OPA.COVER, default)
            ring.set_style_bg_color(color("surface2"), default)
            ring.set_style_border_width(2, default)
            ring.set_style_border_color(color(widget.color), default)
            dot = self._box(ring, item.w / 2.0 - 3, item.h / 2.0 - 3, 6, 6)
            dot.set_style_radius(RADIUS_CIRCLE, default)
            dot.set_style_bg_opa(lv.OPA.COVER, default)
            dot.set_style_bg_color(color(widget.color), default)
            return ring
        if isinstance(widget, Chart):
            chart = lv.chart(self.root)
            self._plain(chart, item.x, item.y, item.w, item.h)
            chart.remove_flag(lv.OBJ_FLAG.CLICKABLE)
            chart.set_type(lv.CHART_TYPE.LINE)
            chart.set_point_count(int(widget.point_count))
            chart.set_div_line_count(3, 4)
            chart.set_style_bg_opa(lv.OPA.COVER, default)
            chart.set_style_bg_color(color("sunken"), default)
            chart.set_style_border_width(1, default)
            chart.set_style_border_color(color("border_subtle"), default)
            chart.set_style_radius(8, default)
            chart.set_style_pad_all(4, default)
            chart.set_style_line_color(color("divider"), default)
            chart.set_style_size(0, 0, LV_PART_INDICATOR)
            chart.set_style_line_width(2, LV_PART_ITEMS)
            chart._series = [
                chart.add_series(color(c_name), lv.CHART_AXIS.PRIMARY_Y)
                for _, c_name, _ in widget.series
            ]
            return chart
        if isinstance(widget, MeshSurface):
            image = lv.image(self.root)
            self._plain(image, item.x, item.y, item.w, item.h)
            image.set_style_radius(8, default)
            image._box_w = item.w
            image._box_h = item.h
            image._loaded_path = None
            image._mesh_key = None
            state = _MeshState(image, widget)
            align_enum = getattr(lv, "IMAGE_ALIGN", None)
            if align_enum is not None and hasattr(align_enum, "STRETCH") and hasattr(image, "set_inner_align"):
                try:
                    image.set_inner_align(align_enum.STRETCH)  # small drag previews are stretched to the box
                    state.can_stretch = True
                except Exception:
                    pass
            ptr = getattr(image, "_pointer", None)
            if ptr is not None:
                self._mesh_map[ptr] = state
            self._mesh_map[id(image)] = state
            if getattr(widget, "on_drag", None) is not None:
                image.add_flag(lv.OBJ_FLAG.CLICKABLE)
                press_lock = getattr(lv.OBJ_FLAG, "PRESS_LOCK", None)
                if press_lock is not None:
                    image.add_flag(press_lock)
                image.add_event_cb(self._on_mesh_event, lv.EVENT_CODE.ALL, None)
            self._update_mesh_surface(image, widget, item.w, item.h)
            return image
        side = int(min(item.w, item.h))
        image = lv.image(self.root)
        image.set_pos(int(item.x + (item.w - side) / 2), int(item.y + (item.h - side) / 2))
        image.set_size(side, side)
        image._side = side
        image._loaded_path = None
        self._update_picture(image, widget.path)
        return image

    def _clicked(self, index):
        if self._raw_capture_active or self._asleep or self._swallow_click:
            return
        action = self.actions[index] if index < len(self.actions) else None
        if action:
            action()

    @staticmethod
    def _apply_pressed_style(obj, widget):
        lv = _lv()
        pressed = getattr(lv.STATE, "PRESSED", None)
        if pressed is None:
            return
        if isinstance(widget, Panel):
            if widget.action is not None:
                obj.set_style_bg_color(color("surface3"), pressed)
                obj.set_style_border_width(max(1, widget.border_width), pressed)
                obj.set_style_border_color(color("accent"), pressed)
            else:
                obj.set_style_bg_color(color(widget.color), pressed)
                obj.set_style_border_width(widget.border_width if widget.border else 0, pressed)
                if widget.border:
                    obj.set_style_border_color(color(widget.border), pressed)
        elif isinstance(widget, Button):
            fill = button_fill(widget)
            border = button_border(widget)
            if widget.action is not None:
                if widget.kind == "estop":
                    obj.set_style_bg_opa(lv.OPA.TRANSP, pressed)
                    obj.set_style_border_width(0, pressed)
                    return
                if widget.swatch is not None:
                    obj.set_style_bg_opa(lv.OPA.COVER, pressed)
                    obj.set_style_bg_color(color(widget.swatch), pressed)
                    obj.set_style_border_width(2, pressed)
                    obj.set_style_border_color(color("text"), pressed)
                    return
                p_fill = {
                    "primary": "ok",
                    "danger": "danger_text",
                    "outline_danger": "danger_border",
                    "active": "primary",
                }.get(widget.kind, "surface4")
                p_border = (
                    "on_accent" if widget.kind == "primary"
                    else ("danger" if widget.kind in ("danger", "outline_danger") else "accent")
                )
                obj.set_style_bg_opa(lv.OPA.COVER, pressed)
                obj.set_style_bg_color(color(p_fill), pressed)
                p_bw = 2 if widget.kind in ("tab", "tab_active", "ghost") else max(1, button_border_width(widget))
                obj.set_style_border_width(p_bw, pressed)
                obj.set_style_border_color(color(p_border), pressed)
            else:
                obj.set_style_bg_opa(lv.OPA.COVER if fill else lv.OPA.TRANSP, pressed)
                if fill:
                    obj.set_style_bg_color(color(fill), pressed)
                obj.set_style_border_width(button_border_width(widget) if border else 0, pressed)
                if border:
                    obj.set_style_border_color(color(border), pressed)

    @staticmethod
    def _refit_button_text(obj, item):
        """Recompute the cut/fitted text of a reused button for its new text.

        display_text/display_sub are fitted when the button is created; a
        reused button (same shape, new text, e.g. the next page of a list)
        must not keep showing the old fitted text. Returns False when the new
        text needs a different font size than the label was created with.
        """
        if getattr(obj, "text_label", None) is None:
            return True
        widget = item.widget
        if widget.sub:
            _, box = button_parts(item)
            if not box:
                return True
            size, main_text, sub_text = button_sub_label(item, box[2])
        else:
            size, main_text = button_label(item)
            sub_text = widget.sub
        if size != getattr(obj, "fit_size", size):
            return False
        obj.display_text, obj.display_sub = main_text, sub_text
        return True

    def _update(self, obj, widget):
        lv = _lv()
        default = _default_state()
        if isinstance(widget, Panel):
            obj.set_style_bg_color(color(widget.color), default)
            if widget.border:
                obj.set_style_border_width(widget.border_width, default)
                obj.set_style_border_color(color(widget.border), default)
            else:
                obj.set_style_border_width(0, default)
            self._apply_pressed_style(obj, widget)
        elif isinstance(widget, Rule):
            obj.set_style_bg_color(color(widget.color), default)
        elif isinstance(widget, Label):
            obj.set_text(widget.text)
            obj.set_style_text_color(color(widget.color), default)
            if obj.unit_label is not None:
                obj.unit_label.set_text(widget.unit)
                usize = label_unit_size(obj._font_size)
                tw = text_width(widget.text, FONT_SIZES[obj._font_size])
                uw = text_width(widget.unit, FONT_SIZES[usize])
                gap = 2
                line_h = self._font_line_height(obj._font_size)
                uline_h = self._font_line_height(usize)
                main_y = int((obj._box_h - line_h) / 2.0)
                unit_y = int(main_y + (line_h - uline_h) * 0.72)
                if obj._align == "right":
                    lx = int(obj._box_w - tw - gap - uw)
                elif obj._align == "center":
                    lx = int((obj._box_w - tw - gap - uw) / 2.0)
                else:
                    lx = 0
                obj.set_width(int(min(obj._box_w, tw * 1.15 + 6)))
                obj.set_pos(lx, main_y)
                obj.unit_label.set_width(int(uw * 1.15 + 6))
                obj.unit_label.set_pos(int(lx + tw + gap), unit_y)
        elif isinstance(widget, Icon):
            obj.set_text(chr(ICONS[widget.name]))
            obj.set_style_text_color(color(widget.color), default)
        elif isinstance(widget, Button):
            fill = button_fill(widget)
            border = button_border(widget)
            if fill:
                obj.set_style_bg_opa(lv.OPA.COVER, default)
                obj.set_style_bg_color(color(fill), default)
            else:
                obj.set_style_bg_opa(lv.OPA.TRANSP, default)
            if border:
                obj.set_style_border_width(button_border_width(widget), default)
                obj.set_style_border_color(color(border), default)
            else:
                obj.set_style_border_width(0, default)
            self._apply_pressed_style(obj, widget)
            if widget.swatch is not None:
                obj.glyph.set_text(chr(ICONS["check"]) if widget.ring else "")
                if widget.ring:
                    obj.glyph.set_style_text_color(color(swatch_check_color(widget.swatch)), default)
                return
            if widget.kind == "estop":
                if obj.glyph:
                    obj.glyph.set_style_text_color(color(button_icon_color(widget)), default)
                return
            t_color = color(button_text_color(widget))
            i_color = color(button_icon_color(widget))
            if obj.sub_label:
                obj.sub_label.set_text(obj.display_sub if obj.display_sub is not None else widget.sub)
                obj.sub_label.set_style_text_color(
                    color("active_text" if widget.kind == "active" else "tertiary"), default)
            if obj.text_label:
                obj.text_label.set_text(obj.display_text if obj.display_text is not None else widget.text)
                obj.text_label.set_style_text_color(t_color, default)
            if obj.glyph:
                obj.glyph.set_style_text_color(i_color, default)
            if obj.r_glyph:
                obj.r_glyph.set_style_text_color(
                    color("tertiary" if widget.action is not None else "disabled_text"), default)
            if obj.toggle_track:
                t_bg = "disabled" if widget.action is None else ("accent" if widget.toggle else "border_default")
                k_bg = "disabled_text" if widget.action is None else ("text" if widget.toggle else "muted")
                obj.toggle_track.set_style_bg_color(color(t_bg), default)
                obj.toggle_knob.set_pos(21 if widget.toggle else 3, 3)
                obj.toggle_knob.set_style_bg_color(color(k_bg), default)
            if obj.dot:
                obj.dot.set_style_bg_color(color(widget.dot), default)
                obj.halo.set_style_border_width(2 if widget.ring else 0, default)
        elif isinstance(widget, Bar):
            obj.set_style_bg_color(color(widget.color), _part_indicator())
            obj.set_value(int(max(0.0, min(1.0, widget.fraction)) * 1000), False)
        elif isinstance(widget, Ring):
            obj.set_value(int(max(0.0, min(1.0, widget.fraction)) * 1000))
            obj.set_style_arc_color(color(widget.color), _part_indicator())
            obj.text_label.set_text(widget.text)
            if obj.sub_label:
                obj.sub_label.set_text(widget.sub)
        elif isinstance(widget, Picture):
            self._update_picture(obj, widget.path)
        elif isinstance(widget, MeshSurface):
            self._update_mesh_surface(obj, widget, getattr(obj, "_box_w", 248), getattr(obj, "_box_h", 148))
        elif isinstance(widget, Chart):
            self._update_chart(obj, widget)

    def _update_chart(self, obj, widget):
        """Update only changed range/points on an LVGL chart to avoid rewriting all series points."""
        lv = _lv()
        y_min = int(round(widget.y_min))
        y_max = int(round(widget.y_max))
        if y_max <= y_min:
            y_max = y_min + 10
        cnt = int(widget.point_count)
        key = getattr(obj, "_pointer", None)
        if key is None:
            key = id(obj)
        state = self._chart_map.get(key)
        if state is None or state["cnt"] != cnt or len(state["series"]) != len(obj._series):
            state = {"cnt": cnt, "range": None, "series": [None] * len(obj._series)}
            self._chart_map[key] = state
        changed = False
        new_range = (y_min, y_max)
        if state["range"] != new_range:
            obj.set_axis_range(lv.CHART_AXIS.PRIMARY_Y, y_min, y_max)
            state["range"] = new_range
            changed = True
        prev_series = state["series"]
        for s_idx, (ser, (values, _, _)) in enumerate(zip(obj._series, widget.series)):
            vals = list(values or ())[-cnt:]
            pad = cnt - len(vals)
            target = [LV_CHART_POINT_NONE] * pad + [
                LV_CHART_POINT_NONE if v is None else int(round(v)) for v in vals
            ]
            prev = prev_series[s_idx]
            if prev is None:
                for idx, val in enumerate(target):
                    obj.set_series_value_by_id(ser, idx, val)
                prev_series[s_idx] = target
                changed = True
            else:
                for idx in range(cnt):
                    new_val = target[idx]
                    if prev[idx] != new_val:
                        obj.set_series_value_by_id(ser, idx, new_val)
                        prev[idx] = new_val
                        changed = True
        if changed:
            obj.refresh()

    def _prune_lv_objects(self):
        """Prevent Rinkhals lvgl._objects dict from leaking destroyed child wrappers."""
        lv = _lv()
        obj_map = getattr(lv, "_objects", None)
        if not isinstance(obj_map, dict):
            return
        keep = {
            getattr(o, "_pointer", None)
            for o in [self.root, self.display, self.touch, self._dim_overlay, self._sleep_overlay]
            + list(self.fonts.values())
            + list(self.icon_fonts.values())
            if o is not None
        }
        keep.discard(None)
        for ptr in [k for k in list(obj_map.keys()) if k not in keep]:
            obj_map.pop(ptr, None)

    @staticmethod
    def _is_interactive_item(item):
        widget = item.widget
        if isinstance(widget, Button):
            return True
        if isinstance(widget, Panel) and widget.action is not None:
            return True
        return False

    @classmethod
    def _compute_ext_click_areas(cls, placed, max_ext=DEFAULT_EXT_CLICK_AREA):
        """Return safe per-item LVGL ext_click_area padding that never overlaps interactive neighbors."""
        from .lvgl_sys import DEFAULT_EXT_CLICK_AREA
        limit = max(0, int(max_ext))
        areas = [0] * len(placed)
        if limit == 0:
            return areas
        interactive = [i for i, item in enumerate(placed) if cls._is_interactive_item(item)]
        for i in interactive:
            a = placed[i]
            ax1, ay1, ax2, ay2 = a.x, a.y, a.x + a.w, a.y + a.h
            best = limit
            for j in interactive:
                if i == j:
                    continue
                b = placed[j]
                bx1, by1, bx2, by2 = b.x, b.y, b.x + b.w, b.y + b.h
                gx = max(bx1 - ax2, ax1 - bx2)
                gy = max(by1 - ay2, ay1 - by2)
                if gx < 0 and gy < 0:
                    continue
                gap = max(gx, gy)
                allowed = max(0, int(gap) // 2)
                if allowed < best:
                    best = allowed
                    if best == 0:
                        break
            areas[i] = best
        return areas

    @staticmethod
    def _widget_key(widget):
        if isinstance(widget, Label):
            return (widget.text, widget.color, widget.unit)
        if isinstance(widget, Button):
            return (
                widget.kind,
                widget.action is not None,
                widget.text,
                widget.sub,
                widget.icon_color,
                widget.swatch,
                widget.ring,
                widget.dot,
                widget.toggle,
            )
        if isinstance(widget, Panel):
            return (widget.color, widget.border, widget.border_width, widget.action is not None)
        if isinstance(widget, Rule):
            return (widget.color,)
        if isinstance(widget, Icon):
            return (widget.name, widget.color)
        if isinstance(widget, Bar):
            return (widget.color, int(max(0.0, min(1.0, widget.fraction)) * 1000))
        if isinstance(widget, Ring):
            return (
                widget.color,
                int(max(0.0, min(1.0, widget.fraction)) * 1000),
                widget.text,
                widget.sub,
            )
        if isinstance(widget, Picture):
            return (widget.path,)
        if isinstance(widget, MeshSurface):
            return (widget.matrix, int(round(widget.rot)) % 360, int(round(widget.tilt)), int(widget.z_scale))
        if isinstance(widget, Chart):
            cnt = int(widget.point_count)
            return (
                int(round(widget.y_min)),
                int(round(widget.y_max)),
                cnt,
                tuple(
                    tuple(LV_CHART_POINT_NONE if v is None else int(round(v)) for v in (values or ())[-cnt:])
                    for values, _, _ in widget.series
                ),
            )
        return ()

    def sync(self, placed):
        shape = tuple(self._shape(item) for item in placed)
        if shape != self.shape:
            self._click_map.clear()
            self._mesh_map.clear()
            self._chart_map.clear()
            self._dim_overlay = None
            self._sleep_overlay = None
            self._overlay_state = None
            self.root.clean()
            self._prune_lv_objects()
            self.objects = [self._create(i, item) for i, item in enumerate(placed)]
            ext_areas = self._compute_ext_click_areas(placed, max_ext=self._ext_click_max)
            for obj, ext in zip(self.objects, ext_areas):
                if ext > 0 and hasattr(obj, "set_ext_click_area"):
                    obj.set_ext_click_area(ext)
            self.shape = shape
            self._widget_keys = [None] * len(placed)
        self.actions = [getattr(item.widget, "action", None) for item in placed]
        widget_keys = self._widget_keys
        for idx, (obj, item) in enumerate(zip(self.objects, placed)):
            wkey = self._widget_key(item.widget)
            if wkey != widget_keys[idx]:
                if isinstance(item.widget, Button) and not self._refit_button_text(obj, item):
                    # The new text needs another font size: rebuild instead of patching
                    self.shape = None
                    return self.sync(placed)
                self._update(obj, item.widget)
                widget_keys[idx] = wkey
        self._sync_overlays()