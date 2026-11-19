"""Touch input, calibration, LVGL event registration, and diagnostic tests."""

import io
import json
import os
import struct
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from kobra_screen import render_lvgl, touch_cal
from kobra_screen.widgets import Bar, Button, Panel, Placed


class _FakeFlag(int):
    def __new__(cls, name, val):
        obj = super().__new__(cls, val)
        obj._name = name
        obj.value = val
        return obj

    def __repr__(self):
        return "<%s: %d>" % (self._name, int(self))


class _EnumNS:
    def __init__(self, mapping):
        for k, v in mapping.items():
            setattr(self, k, _FakeFlag(k, v))


class _FakeTimer:
    def __init__(self):
        self.periods = []

    def set_period(self, period_ms):
        self.periods.append(int(period_ms))


class _FakeDisplay:
    def __init__(self):
        self._pointer = 100
        self.rotation = None
        self.dpi = None

    def set_rotation(self, rot):
        self.rotation = rot

    def set_dpi(self, dpi):
        self.dpi = int(dpi)


class _FakeIndev:
    def __init__(self, indev_type, path):
        self._pointer = 200
        self.indev_type = indev_type
        self.path = path
        self.display = None
        self.callbacks = []
        self.waited_release = 0
        self.scroll_limits = []
        self.read_timer = _FakeTimer()
        self.read_calls = 0
        self.state = 0
        self.point_xy = (136, 240)

    def set_display(self, display):
        self.display = display

    def add_event_cb(self, cb, code, user_data):
        self.callbacks.append((cb, code, user_data))

    def wait_release(self):
        self.waited_release += 1

    def set_scroll_limit(self, limit):
        self.scroll_limits.append(int(limit))

    def get_read_timer(self):
        return self.read_timer

    def get_state(self):
        return self.state

    def get_point(self, pt):
        x, y = self.point_xy
        pt.x = x
        pt.y = y

    def read(self):
        self.read_calls += 1


class _FakePoint:
    def __init__(self):
        self.x = 0
        self.y = 0


class _FakeEvent:
    def __init__(self, target=None, code=None):
        self._target = target
        self._code = code

    def get_current_target_obj(self):
        return self._target

    def get_code(self):
        return self._code


class _FakeObj:
    def __init__(self, parent=None, kind="obj", lv_mod=None):
        self._lv = lv_mod
        self._pointer = lv_mod._alloc_ptr()
        self._kind = kind
        self._parent = parent
        self._children = []
        self._callbacks = []
        self._styles = {}
        self._flags = {int(lv_mod.OBJ_FLAG.SCROLLABLE), int(lv_mod.OBJ_FLAG.CLICK_FOCUSABLE)}
        if kind == "button":
            self._flags.discard(int(lv_mod.OBJ_FLAG.SCROLLABLE))
            self._flags.add(int(lv_mod.OBJ_FLAG.SCROLL_ON_FOCUS))
            self._flags.add(int(lv_mod.OBJ_FLAG.CLICKABLE))
        elif kind in ("bar", "arc"):
            self._flags.add(int(lv_mod.OBJ_FLAG.CLICKABLE))
        self._pos = (0, 0)
        self._size = (0, 0)
        self._ext_click_area = 0
        self._text = ""
        self._range = (0, 100)
        self._value = 0
        self.clean_count = 0
        self.invalidate_count = 0
        lv_mod._objects[self._pointer] = self
        if parent is not None:
            parent._children.append(self)

    def __getattr__(self, name):
        if name.startswith("set_style_"):
            def _style_setter(val, selector=0):
                self._styles[(name, int(selector))] = val
            return _style_setter
        raise AttributeError(name)

    def set_pos(self, x, y):
        self._pos = (int(x), int(y))

    def set_size(self, w, h):
        self._size = (int(w), int(h))

    def set_width(self, w):
        self._size = (int(w), self._size[1])

    def set_height(self, h):
        self._size = (self._size[0], int(h))

    def center(self):
        pass

    def clean(self):
        self.clean_count += 1
        self._children.clear()

    def invalidate(self):
        self.invalidate_count += 1

    def add_flag(self, flag):
        self._flags.add(int(flag))

    def remove_flag(self, flag):
        self._flags.discard(int(flag))

    def has_flag(self, flag):
        return int(flag) in self._flags

    def add_event_cb(self, cb, code, user_data):
        self._callbacks.append((cb, code, user_data))

    def set_ext_click_area(self, size):
        self._ext_click_area = int(size)

    def set_text(self, text):
        self._text = str(text)

    def set_long_mode(self, mode):
        self._long_mode = int(mode)

    def set_range(self, lo, hi):
        self._range = (int(lo), int(hi))

    def set_value(self, val, anim=False):
        self._value = int(val)

    def set_bg_angles(self, a, b):
        pass

    def set_rotation(self, rot):
        pass


class _TouchFakeLvgl:
    STATE = _EnumNS({"DEFAULT": 0, "PRESSED": 32, "DISABLED": 128})
    PART = _EnumNS({"TEXTAREA_PLACEHOLDER": 524288})
    ALIGN = _EnumNS({"CENTER": 9})
    TEXT_ALIGN = _EnumNS({"AUTO": 0, "LEFT": 1, "CENTER": 2, "RIGHT": 3})
    LABEL_LONG_MODE = _EnumNS({"WRAP": 0, "DOTS": 1, "CLIP": 4})
    OBJ_FLAG = _EnumNS({
        "HIDDEN": 1,
        "CLICKABLE": 2,
        "CLICK_FOCUSABLE": 4,
        "SCROLLABLE": 16,
        "SCROLL_ON_FOCUS": 1024,
        "PRESS_LOCK": 8192,
        "IGNORE_LAYOUT": 131072,
    })
    EVENT_CODE = _EnumNS({
        "ALL": 0,
        "PRESSED": 1,
        "PRESSING": 2,
        "PRESS_LOST": 3,
        "SHORT_CLICKED": 4,
        "CLICKED": 10,
        "RELEASED": 11,
        "VALUE_CHANGED": 35,
    })
    INDEV_STATE = _EnumNS({"RELEASED": 0, "PRESSED": 1})
    DISPLAY_ROTATION = _EnumNS({"_0": 0, "_90": 1, "_180": 2, "_270": 3})
    INDEV_TYPE = _EnumNS({"POINTER": 1})
    OPA = _EnumNS({"TRANSP": 0, "COVER": 255})

    def __init__(self):
        self._next_ptr = 1000
        self._objects = {}
        self.calibration = None
        self.swap_axes_calls = []
        self.timer_handler_hook = None
        self.ticks = []
        self.active_screen = None
        self.indevs = []

    def _alloc_ptr(self):
        self._next_ptr += 1
        return self._next_ptr

    def init(self):
        pass

    def linux_fbdev_create(self):
        return _FakeDisplay()

    def linux_fbdev_set_file(self, disp, path):
        self.fbdev = path

    def evdev_create(self, indev_type, path):
        indev = _FakeIndev(indev_type, path)
        self.indevs.append(indev)
        return indev

    def evdev_grab_device(self, indev):
        self.grabbed = True

    def evdev_release_device(self, indev):
        self.grabbed = False

    def evdev_set_calibration(self, indev, x1, y1, x2, y2):
        self.calibration = (x1, y1, x2, y2)

    def evdev_set_swap_axes(self, indev, swap):
        self.swap_axes_calls.append(bool(swap))

    def color_make(self, r, g, b):
        return (r, g, b)

    def font_get_default(self):
        return None

    def tiny_ttf_create_file(self, path, size):
        return None

    def obj(self, parent=None):
        return _FakeObj(parent, kind="obj", lv_mod=self)

    def label(self, parent=None):
        return _FakeObj(parent, kind="label", lv_mod=self)

    def button(self, parent=None):
        return _FakeObj(parent, kind="button", lv_mod=self)

    def bar(self, parent=None):
        return _FakeObj(parent, kind="bar", lv_mod=self)

    def arc(self, parent=None):
        return _FakeObj(parent, kind="arc", lv_mod=self)

    def screen_load(self, root):
        self.active_screen = root

    def point(self):
        return _FakePoint()

    def tick_inc(self, ms):
        self.ticks.append(int(ms))

    def timer_handler(self):
        if self.timer_handler_hook is not None:
            self.timer_handler_hook()


class TouchConfigTests(unittest.TestCase):
    def test_model_calibration_selection_and_override(self):
        fb, ev, rot, dpi, cal = render_lvgl.parse_screen_config({})
        self.assertEqual((fb, ev, rot, dpi, cal), ("/dev/fb0", "/dev/input/event0", 90, 130, (25, 235, 460, 25)))

        _, _, _, dpi_ks1, cal_ks1 = render_lvgl.parse_screen_config({"KOBRA_MODEL_CODE": "KS1"})
        self.assertEqual((dpi_ks1, cal_ks1), (180, (0, 0, 800, 480)))

        _, _, _, dpi_k3, cal_k3 = render_lvgl.parse_screen_config({"KOBRA_MODEL_CODE": "k3v2"})
        self.assertEqual((dpi_k3, cal_k3), (130, (25, 235, 460, 25)))

        _, _, _, _, cal_env = render_lvgl.parse_screen_config({
            "KOBRA_MODEL_CODE": "K3",
            "KOBRA_TOUCH_CALIBRATION": "30, 240, 455, 20",
        })
        self.assertEqual(cal_env, (30, 240, 455, 20))

        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            _, _, _, _, cal_bad = render_lvgl.parse_screen_config({
                "KOBRA_TOUCH_CALIBRATION": "10,20,10,40",
            })
        self.assertEqual(cal_bad, (25, 235, 460, 25))
        self.assertIn("invalid touch_calibration", err.getvalue())

    def test_touch_config_files_and_env_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf_path = os.path.join(tmp, "touch.conf")
            with open(conf_path, "w", encoding="utf-8") as f:
                f.write("touch_calibration = 28,232,458,28\n")
                f.write("touch_swap_axes = true\n")
                f.write("touch_poll_ms = 8\n")
                f.write("touch_scroll_limit = 16\n")
            env = {"KOBRA_TOUCH_CONFIG": conf_path}
            _, _, _, _, cal = render_lvgl.parse_screen_config(env)
            opts = render_lvgl.parse_touch_options(env)
            self.assertEqual(cal, (28, 232, 458, 28))
            self.assertEqual(opts["swap_axes"], True)
            self.assertEqual(opts["indev_period_ms"], 8)
            self.assertEqual(opts["scroll_limit"], 16)

            json_cfg = os.path.join(tmp, "kobra-screen.config")
            with open(json_cfg, "w", encoding="utf-8") as f:
                json.dump({"touch_calibration": "20,238,462,22", "touch_swap_axes": "false"}, f)
            env_json = {"KOBRA_TOUCH_CONFIG": json_cfg, "KOBRA_TOUCH_SWAP_AXES": "true"}
            _, _, _, _, cal2 = render_lvgl.parse_screen_config(env_json)
            opts2 = render_lvgl.parse_touch_options(env_json)
            self.assertEqual(cal2, (20, 238, 462, 22))
            self.assertTrue(opts2["swap_axes"])


class LvglTouchTests(unittest.TestCase):
    def _make_screen(self, env=None):
        fake_lv = _TouchFakeLvgl()
        env_patch = mock.patch.dict(os.environ, env or {}, clear=False)
        lv_patch = mock.patch.object(render_lvgl, "lv", fake_lv)
        err_patch = mock.patch("sys.stderr", io.StringIO())
        env_patch.start()
        lv_patch.start()
        err_patch.start()
        self.addCleanup(err_patch.stop)
        self.addCleanup(lv_patch.stop)
        self.addCleanup(env_patch.stop)
        screen = render_lvgl.LvglScreen(manage_system=False)
        return screen, fake_lv

    def test_indev_period_scroll_limit_and_swap_axes(self):
        screen, fake_lv = self._make_screen()
        self.assertEqual(fake_lv.calibration, (25, 235, 460, 25))
        self.assertEqual(fake_lv.swap_axes_calls, [])
        self.assertEqual(screen.touch.read_timer.periods, [10])
        self.assertEqual(screen.touch.scroll_limits, [12])
        self.assertTrue(screen._has_fast_read_timer)

        screen2, fake_lv2 = self._make_screen({"KOBRA_TOUCH_SWAP_AXES": "true", "KOBRA_TOUCH_POLL_MS": "7"})
        self.assertEqual(fake_lv2.swap_axes_calls, [True])
        self.assertEqual(screen2.touch.read_timer.periods, [7])

    def test_event_registration_flags_and_click_only_on_clicked(self):
        screen, fake_lv = self._make_screen()
        calls = []
        placed = [
            Placed(12, 10, 120, 40, Button(text="OK", action=lambda: calls.append("btn"), kind="primary")),
            Placed(12, 60, 120, 40, Panel(action=lambda: calls.append("panel"))),
            Placed(12, 110, 120, 40, Panel(action=None)),
            Placed(12, 160, 120, 8, Bar(fraction=0.5)),
        ]
        screen.sync(placed)
        btn_obj, panel_clk_obj, panel_static_obj, bar_obj = screen.objects

        self.assertEqual(btn_obj._callbacks[0][1], fake_lv.EVENT_CODE.CLICKED)
        self.assertEqual(panel_clk_obj._callbacks[0][1], fake_lv.EVENT_CODE.CLICKED)
        self.assertEqual(panel_static_obj._callbacks, [])

        self.assertTrue(btn_obj.has_flag(fake_lv.OBJ_FLAG.PRESS_LOCK))
        self.assertFalse(btn_obj.has_flag(fake_lv.OBJ_FLAG.SCROLL_ON_FOCUS))
        self.assertFalse(btn_obj.has_flag(fake_lv.OBJ_FLAG.SCROLLABLE))
        self.assertTrue(panel_clk_obj.has_flag(fake_lv.OBJ_FLAG.PRESS_LOCK))
        self.assertTrue(panel_clk_obj.has_flag(fake_lv.OBJ_FLAG.CLICKABLE))
        self.assertFalse(bar_obj.has_flag(fake_lv.OBJ_FLAG.CLICKABLE))

        cb_fn = btn_obj._callbacks[0][0]
        cb_fn(_FakeEvent(target=btn_obj, code=fake_lv.EVENT_CODE.CLICKED))
        panel_clk_obj._callbacks[0][0](_FakeEvent(target=panel_clk_obj, code=fake_lv.EVENT_CODE.CLICKED))
        self.assertEqual(calls, ["btn", "panel"])

    def test_pressed_visual_feedback_styles(self):
        screen, fake_lv = self._make_screen()
        pressed = int(fake_lv.STATE.PRESSED)
        default = int(fake_lv.STATE.DEFAULT)
        placed = [
            Placed(12, 10, 100, 38, Button(text="Go", action=lambda: None, kind="primary")),
            Placed(12, 56, 100, 38, Button(text="Off", action=None, kind="secondary")),
            Placed(12, 102, 100, 40, Panel(action=lambda: None)),
        ]
        screen.sync(placed)
        btn_active, btn_disabled, panel_active = screen.objects

        self.assertNotEqual(
            btn_active._styles[("set_style_bg_color", pressed)],
            btn_active._styles[("set_style_bg_color", default)],
        )
        self.assertEqual(btn_active._styles[("set_style_border_width", pressed)], 1)
        self.assertEqual(
            btn_disabled._styles[("set_style_bg_color", pressed)],
            btn_disabled._styles[("set_style_bg_color", default)],
        )
        self.assertNotEqual(
            panel_active._styles[("set_style_bg_color", pressed)],
            panel_active._styles[("set_style_bg_color", default)],
        )

    def test_ext_click_area_respects_gaps_without_overlap(self):
        screen, _ = self._make_screen()
        placed = [
            Placed(12, 10, 80, 32, Button(text="A", action=lambda: None)),
            Placed(94, 10, 80, 32, Button(text="B", action=lambda: None)),   # gap=2 with A
            Placed(174, 10, 80, 32, Button(text="C", action=lambda: None)),  # gap=0 with B
            Placed(12, 60, 120, 40, Button(text="Far", action=lambda: None)),  # gap>=18
            Placed(0, 0, 272, 480, Panel(action=None)),  # background panel ignored
        ]
        areas = render_lvgl.LvglScreen._compute_ext_click_areas(placed, max_ext=4)
        self.assertEqual(areas, [1, 0, 0, 4, 0])

        screen.sync(placed[:2])
        self.assertEqual(screen.objects[0]._ext_click_area, 1)
        self.assertEqual(screen.objects[1]._ext_click_area, 1)

        screen.sync([placed[3]])
        self.assertEqual(screen.objects[0]._ext_click_area, 4)

    def test_run_defers_root_clean_while_touch_pressed(self):
        screen, fake_lv = self._make_screen()
        clicked = []
        layout_stage = [0]

        def make_placed():
            if layout_stage[0] == 0:
                return [Placed(12, 12, 100, 40, Button(text="Step1", action=lambda: clicked.append("step1")))]
            return [Placed(12, 70, 140, 44, Button(text="Step2", action=lambda: clicked.append("step2")))]

        fake_app = SimpleNamespace(dirty=True, layout=make_placed)
        tick = [0]

        def on_timer():
            tick[0] += 1
            if tick[0] == 1:
                # Frame 1 draws Step1; simulate finger going down afterwards and app becoming dirty
                return
            if tick[0] == 2:
                screen._on_touch_activity(_FakeEvent(code=fake_lv.EVENT_CODE.PRESSED))
                layout_stage[0] = 1
                fake_app.dirty = True
                # Finger still down after tick 2: root.clean MUST NOT have been called again yet
                return
            if tick[0] == 3:
                # Finger still down while app.dirty=True
                self.assertEqual(screen.root.clean_count, 1)
                return
            if tick[0] == 4:
                # Click fires against Step1 button and finger releases
                btn = screen.objects[0]
                btn._callbacks[0][0](_FakeEvent(target=btn, code=fake_lv.EVENT_CODE.CLICKED))
                screen._on_touch_activity(_FakeEvent(code=fake_lv.EVENT_CODE.RELEASED))
                return
            if tick[0] >= 5:
                screen.request_stop()

        fake_lv.timer_handler_hook = on_timer
        with mock.patch("kobra_screen.render_lvgl.time.sleep"):
            screen.run(fake_app)

        self.assertEqual(clicked, ["step1"])
        self.assertEqual(screen.root.clean_count, 2)

    def test_raw_evdev_packet_and_touch_diagnostic(self):
        pkt16 = struct.pack("<iiHHi", 10, 250, 3, 0, 123)
        pkt24 = struct.pack("<qqHHi", 10, 250, 1, 330, 1)
        self.assertEqual(render_lvgl.parse_raw_evdev_packet(pkt16), (10, 250, 3, 0, 123))
        self.assertEqual(render_lvgl.parse_raw_evdev_packet(pkt24), (10, 250, 1, 330, 1))
        with self.assertRaises(ValueError):
            render_lvgl.parse_raw_evdev_packet(b"\x00" * 8)

        screen, fake_lv = self._make_screen()
        out = io.StringIO()
        ticks = [0]

        def diag_hook():
            ticks[0] += 1
            if ticks[0] == 1:
                screen.touch.point_xy = (42, 180)
                screen._on_touch_activity(_FakeEvent(code=fake_lv.EVENT_CODE.PRESSED))
                screen._on_touch_activity(_FakeEvent(code=fake_lv.EVENT_CODE.RELEASED))
            elif ticks[0] == 2:
                exit_btn = [o for o in screen.root._children if o._kind == "button"][0]
                exit_btn._callbacks[0][0](_FakeEvent(target=exit_btn, code=fake_lv.EVENT_CODE.CLICKED))

        fake_lv.timer_handler_hook = diag_hook
        with mock.patch("kobra_screen.render_lvgl.time.sleep"):
            rc = render_lvgl.run_touch_diagnostic(duration=5.0, screen=screen, stream=out)

        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("TOUCH_DIAG: #1 PRESSED (42, 180)", text)
        self.assertIn("TOUCH_DIAG: #1 RELEASED (42, 180)", text)

    def test_display_sleep_wake_swallows_click_and_scales_backlight(self):
        with tempfile.TemporaryDirectory() as tmp:
            bl = os.path.join(tmp, "brightness")
            max_bl = os.path.join(tmp, "max_brightness")
            with open(bl, "w") as f:
                f.write("255\n")
            with open(max_bl, "w") as f:
                f.write("200\n")

            screen, fake_lv = self._make_screen()
            screen.backlight_path = bl
            screen._hw_backlight_ok = True
            clicked = []
            screen.sync([Placed(10, 10, 100, 44, Button("Press", lambda: clicked.append("hit")))])

            # 50% of max_brightness=200 -> 100
            screen.set_brightness(50)
            with open(bl) as f:
                self.assertEqual(f.read().strip(), "100")

            # Sleep -> writes 0 and shows clickable black overlay
            screen.set_sleep(True)
            with open(bl) as f:
                self.assertEqual(f.read().strip(), "0")
            self.assertIsNotNone(screen._sleep_overlay)
            self.assertFalse(screen._sleep_overlay.has_flag(fake_lv.OBJ_FLAG.HIDDEN))
            self.assertTrue(screen._sleep_overlay.has_flag(fake_lv.OBJ_FLAG.CLICKABLE))

            # First touch while asleep wakes screen & swallows any CLICKED on underlying button
            btn = screen.objects[0]
            screen._on_touch_activity(_FakeEvent(code=fake_lv.EVENT_CODE.PRESSED))
            btn._callbacks[0][0](_FakeEvent(target=btn, code=fake_lv.EVENT_CODE.CLICKED))
            screen._on_touch_activity(_FakeEvent(code=fake_lv.EVENT_CODE.RELEASED))
            self.assertFalse(screen._asleep)
            self.assertEqual(clicked, [])
            with open(bl) as f:
                self.assertEqual(f.read().strip(), "100")

            # Subsequent touch while awake clicks normally
            screen._on_touch_activity(_FakeEvent(code=fake_lv.EVENT_CODE.PRESSED))
            btn._callbacks[0][0](_FakeEvent(target=btn, code=fake_lv.EVENT_CODE.CLICKED))
            screen._on_touch_activity(_FakeEvent(code=fake_lv.EVENT_CODE.RELEASED))
            self.assertEqual(clicked, ["hit"])

            # Without sysfs backlight, dimming creates a non-clickable LVGL dim overlay
            screen.backlight_path = os.path.join(tmp, "missing_bl")
            screen._hw_backlight_ok = False
            screen.set_brightness(50)
            self.assertIsNotNone(screen._dim_overlay)
            self.assertFalse(screen._dim_overlay.has_flag(fake_lv.OBJ_FLAG.HIDDEN))
            self.assertFalse(screen._dim_overlay.has_flag(fake_lv.OBJ_FLAG.CLICKABLE))


class TouchCalibrationTests(unittest.TestCase):
    def _synth_raw_for_cal(self, cal, swap_axes, targets, lv_rot=270):
        min_x, min_y, max_x, max_y = cal
        w_phys, h_phys = touch_cal.physical_resolution(272, 480, lv_rot)
        raw_pts = []
        for _, tx, ty in targets:
            px, py = touch_cal.logical_to_physical(tx, ty, 272, 480, lv_rot)
            sx = min_x + (max_x - min_x) * px / float(w_phys - 1)
            sy = min_y + (max_y - min_y) * py / float(h_phys - 1)
            rx, ry = (sy, sx) if swap_axes else (sx, sy)
            raw_pts.append((int(round(rx)), int(round(ry))))
        return raw_pts

    def test_compute_calibration_normal_inverted_and_swapped_axes(self):
        from kobra_screen import touch_cal

        corners, center = touch_cal.calibration_targets(272, 480)
        targets_xy = [(tx, ty) for _, tx, ty in corners]

        # 1. Exact Kobra 3 calibration (inverted Y: min_y=235 > max_y=25, swap_axes=False)
        true_k3 = (25, 235, 460, 25)
        raw_k3 = self._synth_raw_for_cal(true_k3, False, corners, lv_rot=270)
        cal, swap = touch_cal.compute_calibration(targets_xy, raw_k3, 272, 480, lv_rot=270)
        self.assertFalse(swap)
        for got, exp in zip(cal, true_k3):
            self.assertLessEqual(abs(got - exp), 2)

        # 2. Swapped + inverted X/Y panel (e.g. X: 4000->200, Y: 300->3800, swap_axes=True)
        true_swapped = (4000, 300, 200, 3800)
        raw_swapped = self._synth_raw_for_cal(true_swapped, True, corners, lv_rot=270)
        cal_sw, swap_sw = touch_cal.compute_calibration(targets_xy, raw_swapped, 272, 480, lv_rot=270)
        self.assertTrue(swap_sw)
        for got, exp in zip(cal_sw, true_swapped):
            self.assertLessEqual(abs(got - exp), 8)
        # Simulated center coordinate maps accurately back to (136, 240)
        raw_c = self._synth_raw_for_cal(true_swapped, True, [center], lv_rot=270)[0]
        mapped_c = touch_cal.simulate_lvgl_point(raw_c[0], raw_c[1], cal_sw, swap_sw, 272, 480, lv_rot=270)
        self.assertLessEqual(abs(mapped_c[0] - center[1]), 2)
        self.assertLessEqual(abs(mapped_c[1] - center[2]), 2)

        # 3. Degenerate taps (all near same spot) must be rejected
        with self.assertRaises(ValueError):
            touch_cal.compute_calibration(targets_xy, [(200, 200), (201, 200), (200, 201), (199, 199)])

    def test_wizard_verify_threshold_auto_rollback_save_and_reset(self):
        from fake_moonraker import FakePrinter, serve
        from kobra_screen import touch_cal
        from kobra_screen.app import App
        from kobra_screen.moonraker import Moonraker

        printer = FakePrinter()
        httpd = serve(printer, simulate=False)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "kobra-screen.config")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump({"moonraker_host": "127.0.0.1", "touch_calibration": "30,230,455,30", "touch_swap_axes": "false"}, f)

            now = [100.0]
            state = {"cal": (30, 230, 455, 30), "swap": False, "raw": False, "applied": []}

            class FakeTouchBackend:
                @staticmethod
                def get_state():
                    return state["cal"], state["swap"], 270

                @staticmethod
                def apply(cal, swap):
                    state["cal"] = tuple(cal)
                    state["swap"] = bool(swap)
                    state["applied"].append((tuple(cal), bool(swap)))

                @staticmethod
                def set_raw(enabled):
                    state["raw"] = bool(enabled)

                @staticmethod
                def save(cal, swap):
                    return touch_cal.save_touch_config(cal, swap, config_path=cfg_path)

                @staticmethod
                def reset():
                    return touch_cal.reset_touch_config(config_path=cfg_path)

            app = App(
                Moonraker("127.0.0.1", httpd.server_address[1]),
                cache_dir=tmp,
                touch_backend=FakeTouchBackend(),
                clock_fn=lambda: now[0],
            )
            app.refresh()
            self.assertTrue(app.press("Ayar"))
            self.assertTrue(app.press("Ekranı kalibre et"))
            self.assertIsNotNone(app.touch_cal)

            # 1. Start corners -> verify high error (> 25 px) reverts live calibration back to old
            app.press("Kalibrasyonu başlat")
            self.assertTrue(state["raw"])
            corners, _ = touch_cal.calibration_targets(272, 480)
            raw_pts = self._synth_raw_for_cal((10, 250, 470, 10), False, corners, lv_rot=270)
            for rx, ry in raw_pts:
                app.record_calibration_tap(rx, ry)
            self.assertFalse(state["raw"])
            self.assertEqual(app.touch_cal.stage, "verify")
            self.assertNotEqual(state["cal"], (30, 230, 455, 30))

            # Tap far away from center (136, 240) -> threshold exceeded -> reverts to (30, 230, 455, 30)
            self.assertFalse(app.record_calibration_verify(40, 40))
            self.assertEqual(app.touch_cal.stage, "intro")
            self.assertEqual(state["cal"], (30, 230, 455, 30))
            self.assertIn("Sapma yüksek", app.touch_cal.error_msg)

            # 2. Retry with good center tap -> enters 15s confirm; timeout reverts without saving!
            app.press("Kalibrasyonu başlat")
            for rx, ry in raw_pts:
                app.record_calibration_tap(rx, ry)
            self.assertTrue(app.record_calibration_verify(134, 242))
            self.assertEqual(app.touch_cal.stage, "confirm")
            # 10s elapsed -> still waiting
            now[0] += 10.0
            self.assertFalse(app.tick_calibration())
            self.assertEqual(app.touch_cal.stage, "confirm")
            # 15s elapsed -> auto-reverts to old calibration and leaves config file untouched
            now[0] += 6.0
            self.assertTrue(app.tick_calibration())
            self.assertIsNone(app.touch_cal)
            self.assertEqual(state["cal"], (30, 230, 455, 30))
            with open(cfg_path, "r", encoding="utf-8") as f:
                self.assertEqual(json.load(f)["touch_calibration"], "30,230,455,30")

            # 3. Confirm & Save persists new calibration while keeping moonraker_host
            app.press("Ekranı kalibre et")
            app.press("Kalibrasyonu başlat")
            swapped_raw = self._synth_raw_for_cal((20, 240, 465, 20), True, corners, lv_rot=270)
            for rx, ry in swapped_raw:
                app.record_calibration_tap(rx, ry)
            self.assertTrue(app.record_calibration_verify(136, 240))
            self.assertTrue(app.press("Onayla"))
            self.assertIsNone(app.touch_cal)

            with open(cfg_path, "r", encoding="utf-8") as f:
                saved_data = json.load(f)
            self.assertEqual(saved_data["moonraker_host"], "127.0.0.1")
            self.assertEqual(saved_data["touch_swap_axes"], "true")

            # Verify parse_screen_config & parse_touch_options load the saved values
            env = {"KOBRA_TOUCH_CONFIG": cfg_path}
            _, _, _, _, loaded_cal = render_lvgl.parse_screen_config(env)
            loaded_opts = render_lvgl.parse_touch_options(env)
            self.assertEqual(loaded_cal, state["cal"])
            self.assertTrue(loaded_opts["swap_axes"])

            # 4. "Varsayılana dön" resets back to Kobra 3 default (25, 235, 460, 25), swap_axes=False
            app.press("Ekranı kalibre et")
            app.press("Varsayılana dön")
            app.press("Evet")
            self.assertEqual(state["cal"], (25, 235, 460, 25))
            self.assertFalse(state["swap"])
            with open(cfg_path, "r", encoding="utf-8") as f:
                reset_data = json.load(f)
            self.assertEqual(reset_data["touch_calibration"], "25,235,460,25")
            self.assertEqual(reset_data["touch_swap_axes"], "false")

    def test_raw_evdev_reader_and_lvgl_screen_calibration_flow(self):
        from fake_moonraker import FakePrinter, serve
        from kobra_screen import touch_cal
        from kobra_screen.app import App
        from kobra_screen.moonraker import Moonraker

        # 1. Test RawEvdevReader handles 16B/24B packets and computes median sample on BTN_TOUCH release
        pkt_fmt = "llHHi"
        stream = bytearray()
        for rx, ry in ((400, 208), (402, 210), (404, 212)):
            stream += struct.pack(pkt_fmt, 1, 0, 3, 0, rx)
            stream += struct.pack(pkt_fmt, 1, 0, 3, 1, ry)
            stream += struct.pack(pkt_fmt, 1, 0, 1, 330, 1)
            stream += struct.pack(pkt_fmt, 1, 0, 0, 0, 0)
        stream += struct.pack(pkt_fmt, 1, 10, 1, 330, 0)
        chunks = [bytes(stream), b""]
        reader = touch_cal.RawEvdevReader(
            "/dev/null",
            open_fn=lambda p: 99,
            read_fn=lambda fd, n: chunks.pop(0) if chunks else b"",
            close_fn=lambda fd: None,
        )
        self.assertEqual(reader.poll(), (402, 210))
        reader.close()

        # 2. Test LvglScreen + _TouchFakeLvgl calibration flow (grab release -> raw -> regrab + apply -> verify)
        fake_lv = _TouchFakeLvgl()
        orig_lv = render_lvgl.lv
        render_lvgl.lv = fake_lv
        printer = FakePrinter()
        httpd = serve(printer, simulate=False)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cfg_path = os.path.join(tmp, "kobra-screen.config")
                screen = render_lvgl.LvglScreen(manage_system=False, grab_touch=True)
                backend = screen.touch_backend()
                backend.save = lambda c, s: touch_cal.save_touch_config(c, s, config_path=cfg_path)
                app = App(Moonraker("127.0.0.1", httpd.server_address[1]), cache_dir=tmp, touch_backend=backend)
                app.refresh()
                app.open_touch_calibration()

                corners, _ = touch_cal.calibration_targets(272, 480)
                raw_pts = self._synth_raw_for_cal((20, 240, 464, 20), False, corners, lv_rot=270)
                tick = [0]

                def hook():
                    tick[0] += 1
                    if tick[0] == 1:
                        screen.set_raw_capture = lambda en: (
                            setattr(screen, "_raw_capture_active", bool(en)),
                            fake_lv.evdev_release_device(screen.touch) if en else fake_lv.evdev_grab_device(screen.touch),
                        )
                        app.start_touch_calibration()
                        self.assertFalse(fake_lv.grabbed)
                    elif 2 <= tick[0] <= 5:
                        rx, ry = raw_pts[tick[0] - 2]
                        app.record_calibration_tap(rx, ry)
                    elif tick[0] == 6:
                        self.assertTrue(fake_lv.grabbed)
                        self.assertEqual(app.touch_cal.stage, "verify")
                        screen.touch.point_xy = (136, 240)
                        screen._on_touch_activity(_FakeEvent(code=fake_lv.EVENT_CODE.RELEASED))
                    elif tick[0] == 7:
                        self.assertEqual(app.touch_cal.stage, "confirm")
                        app.confirm_touch_calibration()
                        screen.request_stop()

                fake_lv.timer_handler_hook = hook
                with mock.patch("kobra_screen.render_lvgl.time.sleep"):
                    screen.run(app)

                self.assertIsNone(app.touch_cal)
                self.assertTrue(os.path.isfile(cfg_path))
                self.assertEqual(fake_lv.calibration, screen._touch_calibration)
        finally:
            render_lvgl.lv = orig_lv
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
