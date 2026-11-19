"""Draws the UI on the printer's touchscreen with the LVGL 9 CFFI binding that
ships with Rinkhals (/useremain/rinkhals/.current/opt/rinkhals/ui/lvgl).
"""

import atexit
import os
import subprocess
import time

from .lvgl_sys import (_WATCHDOG_SH, _default_state, _enum_val, _find_pids, _find_pids_multi, _install_signal_stop,
                       _signal_pids, _wake_backlight, color, DEFAULT_INDEV_READ_PERIOD_MS,
                       DEFAULT_SCROLL_LIMIT, DEFAULT_EXT_CLICK_AREA, FONT_FILE, FONT_SIZES, HEIGHT, ICON_FONT_FILE,
                       ICON_SIZES, MAIN_LOOP_SLEEP_SEC, IDLE_LOOP_SLEEP_SEC,
                       IDLE_SLEEP_MIN_SEC, IDLE_SLEEP_MAX_SEC, ACTIVE_TOUCH_WINDOW_SEC, HOUSEKEEPING_INTERVAL_SEC,
                       COEXIST_CHECK_ACTIVE_SEC, COEXIST_CHECK_YIELDED_SEC, MAX_PRESS_SYNC_DEFER_SEC, BACKLIGHT_PATH,
                       KOBRA_PID_FILE, KOBRA_HEARTBEAT_FILE, DEFAULT_HANG_TIMEOUT, WIDTH, parse_screen_config,
                       parse_touch_options, read_max_backlight, set_backlight_percent)
from .lvgl_widgets import _LvglWidgetsMixin, _MeshState
from .touch_cal import (RawEvdevReader, lv_rotation_degrees, reset_touch_config, save_touch_config)
from .touch_debug import TouchDebug, enabled as touch_debug_enabled

try:
    import lvgl as lv  # noqa: F401
except Exception:
    lv = None


class LvglScreen(_LvglWidgetsMixin):
    def __init__(self, manage_system=True, pid_file=None, proc_root=None,
                 backlight_path=BACKLIGHT_PATH,
                 heartbeat_file=None, hang_timeout=None, hb_poll=None,
                 log_file=None, grab_touch=True, on_k3sysui_stopped=None):
        global lv
        if lv is None:
            import lvgl as _lv
            lv = _lv

        fbdev, evdev, rotation, dpi, calibration = parse_screen_config()
        touch_opts = parse_touch_options()
        self.fbdev = fbdev
        self.evdev = evdev
        self.rotation = rotation
        self.dpi = dpi
        self._touch_calibration = calibration
        self._touch_swap_axes = touch_opts["swap_axes"]
        self._indev_period_ms = touch_opts["indev_period_ms"]
        self._indev_period_sec = self._indev_period_ms / 1000.0
        self._scroll_limit = touch_opts["scroll_limit"]
        self._ext_click_max = touch_opts["ext_click_max"]
        self.manage_system = manage_system
        self.pid_file = KOBRA_PID_FILE if pid_file is None else pid_file
        self.proc_root = os.environ.get("KOBRA_PROC_ROOT", "/proc") if proc_root is None else proc_root
        self.backlight_path = backlight_path
        self.heartbeat_file = (
            os.environ.get("KOBRA_HEARTBEAT_FILE", KOBRA_HEARTBEAT_FILE)
            if heartbeat_file is None else heartbeat_file
        )
        self.hang_timeout = (
            float(os.environ.get("KOBRA_HANG_TIMEOUT", str(DEFAULT_HANG_TIMEOUT)))
            if hang_timeout is None else float(hang_timeout)
        )
        self.hb_poll = (
            float(os.environ.get("KOBRA_HB_POLL_SEC", "1"))
            if hb_poll is None else float(hb_poll)
        )
        self.log_file = os.environ.get("KOBRA_LOG_FILE", "") if log_file is None else log_file
        self._on_k3sysui_stopped = on_k3sysui_stopped
        self._closed = False
        self._yielded = False
        self._running = False
        self._last_touch = time.monotonic()
        self._touch_pressed = False
        self._touch_pressed_since = 0.0
        self._touch_diag_cb = None
        self._verify_cb = None
        self._raw_reader = None
        self._raw_capture_active = False
        self._pt = lv.point() if hasattr(lv, "point") else None
        self._has_fast_read_timer = False
        self._watchdog_pipe_w = None
        self._watchdog_proc = None
        self._brightness_pct = 100
        self._asleep = False
        self._swallow_click = False
        self._ignore_wake_until_release = False
        self._hw_backlight_ok = bool(self.backlight_path and os.path.exists(self.backlight_path))
        self._app = None
        self._dim_overlay = None
        self._sleep_overlay = None
        self._overlay_state = None
        self._pressed_state_val = _enum_val(lv.INDEV_STATE.PRESSED) if hasattr(lv, "INDEV_STATE") else None
        self._touch_get_state = None
        self._touch_grabbed = False  # we hold EVIOCGRAB on the touch device (set only by _grab_touch/_release_touch)
        self._exit_signal = None
        self._touch_debug = TouchDebug(evdev) if touch_debug_enabled() else None

        if self.manage_system:
            self._acquire_system_screen()

        lv.init()
        self.display = lv.linux_fbdev_create()
        lv.linux_fbdev_set_file(self.display, fbdev)
        rot_map = {
            0: lv.DISPLAY_ROTATION._0,
            90: lv.DISPLAY_ROTATION._270,
            180: lv.DISPLAY_ROTATION._180,
            270: lv.DISPLAY_ROTATION._90,
        }
        self.display.set_rotation(rot_map.get(rotation, lv.DISPLAY_ROTATION._270))
        self.display.set_dpi(dpi)

        self.touch = None
        if grab_touch:
            self.touch = lv.evdev_create(lv.INDEV_TYPE.POINTER, evdev)
            self.touch.set_display(self.display)
            if not self._yielded:
                if self._touch_debug is not None:
                    self._touch_debug.log("debug mode: not grabbing %s so a second reader sees the same events" % evdev)
                else:
                    self._grab_touch("start")
            if self._touch_swap_axes and hasattr(lv, "evdev_set_swap_axes"):
                lv.evdev_set_swap_axes(self.touch, True)
            lv.evdev_set_calibration(self.touch, *calibration)
            if hasattr(self.touch, "set_scroll_limit"):
                self.touch.set_scroll_limit(self._scroll_limit)
            get_timer = getattr(self.touch, "get_read_timer", None)
            if callable(get_timer):
                read_timer = get_timer()
                if read_timer is not None and hasattr(read_timer, "set_period"):
                    read_timer.set_period(self._indev_period_ms)
                    self._has_fast_read_timer = True
            self.touch.add_event_cb(self._on_touch_activity, lv.EVENT_CODE.ALL, None)
            self._touch_get_state = getattr(self.touch, "get_state", None)

        self.fonts = {name: self._load_font(FONT_FILE, size) for name, size in FONT_SIZES.items()}
        self.icon_fonts = {name: self._load_font(ICON_FONT_FILE, size) for name, size in ICON_SIZES.items()}
        self.root = lv.obj(None)
        self._plain(self.root, 0, 0, WIDTH, HEIGHT)
        self.root.set_style_bg_color(color("bg"), _default_state())
        lv.screen_load(self.root)
        self.shape = None
        self.objects = []
        self._widget_keys = []
        self.actions = []
        self._click_map = {}
        self._mesh_map = {}
        self._chart_map = {}

    def _start_watchdog(self):
        """Spawn a detached pipe-EOF & heartbeat hang watchdog for kobra-screen."""
        if self._watchdog_pipe_w is not None and self._watchdog_proc is not None:
            if self._watchdog_proc.poll() is None:
                return
        self._stop_watchdog()
        r_fd = w_fd = None
        try:
            r_fd, w_fd = os.pipe()
            os.set_inheritable(w_fd, False)
            hb_poll_str = "%g" % self.hb_poll
            hang_str = "%g" % self.hang_timeout
            proc = subprocess.Popen(
                [
                    "/bin/sh", "-c", _WATCHDOG_SH, "kobra-screen-watchdog",
                    self.pid_file or "",
                    str(os.getpid()),
                    self.heartbeat_file or "",
                    hang_str,
                    hb_poll_str,
                    self.log_file or "",
                ],
                stdin=r_fd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
            os.close(r_fd)
            r_fd = None
            self._watchdog_pipe_w = w_fd
            self._watchdog_proc = proc
        except OSError:
            for fd in (r_fd, w_fd):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

    def _ensure_watchdog(self):
        """Respawn the pipe-EOF watchdog if it died unexpectedly while we still run."""
        if not self.manage_system or self._closed:
            return
        if self._watchdog_proc is None or self._watchdog_proc.poll() is not None:
            self._start_watchdog()

    def _stop_k3sysui(self, pids=None):
        """Stop stock K3SysUi completely in open mode (never SIGSTOP)."""
        import signal
        if pids is None:
            pids = _find_pids("K3SysUi", self.proc_root)
        if not pids:
            return False
        _signal_pids(pids, signal.SIGTERM)
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            rem = _find_pids("K3SysUi", self.proc_root)
            if not rem:
                break
            time.sleep(0.05)
        rem = _find_pids("K3SysUi", self.proc_root)
        if rem:
            _signal_pids(rem, signal.SIGKILL)
        if self._on_k3sysui_stopped is not None:
            try:
                self._on_k3sysui_stopped()
            except Exception:
                pass
        return True

    def _stop_watchdog(self):
        w_fd, self._watchdog_pipe_w = self._watchdog_pipe_w, None
        proc, self._watchdog_proc = self._watchdog_proc, None
        if w_fd is not None:
            try:
                os.write(w_fd, b"graceful\n")
            except OSError:
                pass
            try:
                os.close(w_fd)
            except OSError:
                pass
        if proc is not None:
            try:
                proc.wait(timeout=1.0)
            except Exception:
                pass

    def _write_heartbeat(self):
        if not self.heartbeat_file:
            return
        try:
            parent = os.path.dirname(self.heartbeat_file)
            if parent:
                os.makedirs(parent, exist_ok=True)
            tmp = self.heartbeat_file + ".tmp.%d" % os.getpid()
            with open(tmp, "w") as f:
                f.write("%d\n" % int(time.monotonic() * 1000))
            os.replace(tmp, self.heartbeat_file)
        except OSError:
            pass

    def _remove_heartbeat(self):
        if not self.heartbeat_file or not os.path.exists(self.heartbeat_file):
            return
        try:
            os.remove(self.heartbeat_file)
        except OSError:
            pass

    def request_stop(self):
        self._running = False

    def _acquire_system_screen(self):
        """Stop stock K3SysUi (unless rinkhals-ui.py is currently active), start watchdog, and write PID file."""
        self._write_pid_file()
        self._write_heartbeat()
        if _find_pids("rinkhals-ui.py", self.proc_root):
            # Started while rinkhals-ui.py is active (e.g. from the Rinkhals app menu):
            # stay yielded until the user closes rinkhals-ui.py.
            self._yielded = True
            self._start_watchdog()
            return
        self._stop_k3sysui()
        self._start_watchdog()
        _wake_backlight(self.backlight_path, self._target_hw_brightness())

    def _write_pid_file(self):
        if not self.pid_file:
            return
        parent = os.path.dirname(self.pid_file)
        try:
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.pid_file, "w") as f:
                f.write("%d\n" % os.getpid())
        except OSError:
            pass

    def _remove_pid_file(self):
        if not self.pid_file or not os.path.exists(self.pid_file):
            return
        my_pid = str(os.getpid())
        try:
            with open(self.pid_file, "r") as f:
                owner = f.read().strip()
            if not owner or owner == my_pid:
                os.remove(self.pid_file)
        except OSError:
            pass

    def apply_touch_calibration(self, calibration, swap_axes=False):
        self._touch_calibration = tuple(int(v) for v in calibration)
        self._touch_swap_axes = bool(swap_axes)
        if getattr(self, "touch", None) is not None:
            lv.evdev_set_calibration(self.touch, *self._touch_calibration)
            if hasattr(lv, "evdev_set_swap_axes"):
                lv.evdev_set_swap_axes(self.touch, self._touch_swap_axes)

    # ---- touch device grab: every EVIOCGRAB / release goes through these two methods ----

    def _grab_touch(self, reason=""):
        """Take the exclusive grab of the touch device once.

        lv.evdev_grab_device returns nothing and only prints a warning when the kernel says EBUSY, so the call
        itself cannot tell us whether it worked.  A second grab on a device we already hold would always be
        refused (the "ioctl EVIOCGRAB(1) failed: Device or resource busy" lines in the log), so a held grab is
        never asked for again.  Returns True when we hold the grab.
        """
        if self._touch_grabbed:
            return True
        if getattr(self, "touch", None) is None or self._yielded or self._touch_debug is not None:
            return False
        if not hasattr(lv, "evdev_grab_device"):
            return False
        try:
            lv.evdev_grab_device(self.touch)
        except Exception:
            self._yielded = True
            return False
        self._touch_grabbed = True
        return True

    def _release_touch(self):
        if not self._touch_grabbed:
            return
        self._touch_grabbed = False
        if getattr(self, "touch", None) is not None:
            if hasattr(lv, "evdev_release_device"):
                try:
                    lv.evdev_release_device(self.touch)
                except Exception:
                    pass

    def set_raw_capture(self, enabled):
        if enabled:
            if self._raw_capture_active:
                return
            if not self._yielded:
                self._release_touch()
            self._touch_pressed = False
            try:
                self._raw_reader = RawEvdevReader(self.evdev)
            except OSError:
                self._raw_reader = None
            self._raw_capture_active = True
        else:
            if not self._raw_capture_active:
                return  # nothing was captured: do not touch the grab (close() used to grab a second time here)
            reader, self._raw_reader = self._raw_reader, None
            if reader is not None:
                reader.close()
            self._raw_capture_active = False
            self._touch_pressed = False
            if getattr(self, "touch", None) is not None and not self._yielded:
                if hasattr(self.touch, "wait_release"):
                    self.touch.wait_release()
                self._grab_touch("raw capture ended")

    def touch_backend(self):
        screen = self

        class _Backend:
            @staticmethod
            def get_state():
                return (
                    screen._touch_calibration,
                    screen._touch_swap_axes,
                    lv_rotation_degrees(screen.rotation),
                )

            @staticmethod
            def apply(cal, swap_axes):
                screen.apply_touch_calibration(cal, swap_axes)

            @staticmethod
            def set_raw(enabled):
                screen.set_raw_capture(enabled)

            @staticmethod
            def save(cal, swap_axes):
                return save_touch_config(cal, swap_axes)

            @staticmethod
            def reset():
                return reset_touch_config()

        return _Backend()

    def _target_hw_brightness(self):
        max_b = read_max_backlight(self.backlight_path)
        return max(1, min(max_b, int(round(self._brightness_pct * max_b / 100.0))))

    def _sync_overlays(self):
        if getattr(self, "root", None) is None:
            return
        need_dim = not self._hw_backlight_ok and self._brightness_pct < 100 and not self._asleep
        overlay_state = (need_dim, self._brightness_pct if need_dim else None, self._asleep)
        if overlay_state == self._overlay_state:
            return
        default = _default_state()
        if need_dim:
            if self._dim_overlay is None:
                self._dim_overlay = self._box(self.root, 0, 0, WIDTH, HEIGHT)
                self._dim_overlay.set_style_bg_color(lv.color_make(0, 0, 0), default)
            opa = max(0, min(210, int(round((100 - self._brightness_pct) * 2.1))))
            self._dim_overlay.set_style_bg_opa(opa, default)
            self._dim_overlay.remove_flag(lv.OBJ_FLAG.HIDDEN)
        elif self._dim_overlay is not None:
            self._dim_overlay.add_flag(lv.OBJ_FLAG.HIDDEN)

        if self._asleep:
            if self._sleep_overlay is None:
                self._sleep_overlay = self._box(self.root, 0, 0, WIDTH, HEIGHT)
                self._sleep_overlay.set_style_bg_color(lv.color_make(0, 0, 0), default)
                self._sleep_overlay.set_style_bg_opa(lv.OPA.COVER, default)
            self._sleep_overlay.add_flag(lv.OBJ_FLAG.CLICKABLE)
            self._sleep_overlay.remove_flag(lv.OBJ_FLAG.HIDDEN)
        elif self._sleep_overlay is not None:
            self._sleep_overlay.remove_flag(lv.OBJ_FLAG.CLICKABLE)
            self._sleep_overlay.add_flag(lv.OBJ_FLAG.HIDDEN)
        self._overlay_state = overlay_state

    def set_brightness(self, percent):
        self._brightness_pct = max(10, min(100, int(round(percent))))
        if not self._asleep:
            self._hw_backlight_ok = set_backlight_percent(self._brightness_pct, self.backlight_path)
            self._sync_overlays()

    def set_sleep(self, asleep):
        asleep = bool(asleep)
        if asleep == self._asleep:
            return
        self._asleep = asleep
        if asleep:
            if self._is_touch_active():
                self._ignore_wake_until_release = True
            set_backlight_percent(0, self.backlight_path)
        else:
            self._ignore_wake_until_release = False  # a finger that was down when the screen went to sleep
            self._hw_backlight_ok = set_backlight_percent(self._brightness_pct, self.backlight_path)
            if getattr(self, "root", None) is not None:
                self.root.invalidate()
        self._sync_overlays()

    def display_backend(self):
        screen = self

        class _DisplayBackend:
            @staticmethod
            def set_brightness(percent):
                screen.set_brightness(percent)

            @staticmethod
            def set_sleep(asleep):
                screen.set_sleep(asleep)

        return _DisplayBackend()

    def close(self):
        """Release evdev grab, stop watchdog, and remove PID/heartbeat files."""
        if self._closed:
            return
        self._closed = True
        self._running = False
        self.set_raw_capture(False)
        self._stop_watchdog()
        if not self._yielded:
            self._release_touch()
        if self._touch_debug is not None:
            self._touch_debug.close()
        if self.manage_system:
            self._remove_pid_file()
            self._remove_heartbeat()

    def _read_point_xy(self):
        if self._pt is None or getattr(self, "touch", None) is None or not hasattr(self.touch, "get_point"):
            return None, None
        self.touch.get_point(self._pt)
        if hasattr(self._pt, "_pointer"):
            try:
                return int(self._pt._pointer[0].x), int(self._pt._pointer[0].y)
            except Exception:
                pass
        if hasattr(self._pt, "x") and hasattr(self._pt, "y"):
            return int(self._pt.x), int(self._pt.y)
        return None, None

    def _on_touch_activity(self, event):
        if self._raw_capture_active:
            return
        now = time.monotonic()
        code = None
        if event is not None and hasattr(event, "get_code"):
            try:
                code = _enum_val(event.get_code())
            except Exception:
                code = None
        pressed_code = _enum_val(lv.EVENT_CODE.PRESSED)
        released_code = _enum_val(lv.EVENT_CODE.RELEASED)
        release_codes = {
            _enum_val(getattr(lv.EVENT_CODE, name))
            for name in ("RELEASED", "PRESS_LOST", "INDEV_RESET")
            if hasattr(lv.EVENT_CODE, name)
        }
        if self._touch_debug is not None and code is not None:
            self._touch_debug.note_lv(code, pressed_code, released_code, _enum_val(lv.EVENT_CODE.CLICKED))
        if code == pressed_code:
            self._touch_pressed = True
            self._touch_pressed_since = now
        elif code in release_codes:
            self._touch_pressed = False
            self._touch_pressed_since = 0.0

        if self._asleep:
            if self._ignore_wake_until_release:
                if code in release_codes:
                    self._ignore_wake_until_release = False
                self._last_touch = now
                if self._touch_diag_cb is not None and code is not None:
                    self._touch_diag_cb(code)
                return
            self.set_sleep(False)
            self._swallow_click = True
            if self._app is not None and hasattr(self._app, "wake_display"):
                self._app.wake_display()
            if getattr(self, "touch", None) is not None and hasattr(self.touch, "wait_release"):
                self.touch.wait_release()
            self._touch_pressed = False
            self._last_touch = now
            if self._touch_diag_cb is not None and code is not None:
                self._touch_diag_cb(code)
            return

        if code == pressed_code:
            self._swallow_click = False

        if now - self._last_touch > 2.0:
            if _wake_backlight(self.backlight_path, self._target_hw_brightness()):
                self._swallow_click = True
                if getattr(self, "touch", None) is not None and hasattr(self.touch, "wait_release"):
                    self.touch.wait_release()
                self._touch_pressed = False
                if getattr(self, "root", None) is not None:
                    self.root.invalidate()
                self._last_touch = now
                if self._touch_diag_cb is not None and code is not None:
                    self._touch_diag_cb(code)
                if self._app is not None and hasattr(self._app, "note_activity"):
                    self._app.note_activity()
                return
        self._last_touch = now
        if self._app is not None and hasattr(self._app, "note_activity"):
            self._app.note_activity()
        if self._verify_cb is not None and code == released_code:
            px, py = self._read_point_xy()
            if px is not None and py is not None:
                self._verify_cb(px, py)
        if self._touch_diag_cb is not None and code is not None:
            self._touch_diag_cb(code)

    def _is_touch_active(self):
        if self._touch_pressed:
            return True
        get_state = self._touch_get_state
        if get_state is not None and self._pressed_state_val is not None:
            try:
                return _enum_val(get_state()) == self._pressed_state_val
            except Exception:
                return False
        touch = getattr(self, "touch", None)
        if touch is not None and hasattr(touch, "get_state") and hasattr(lv, "INDEV_STATE"):
            try:
                return _enum_val(touch.get_state()) == _enum_val(lv.INDEV_STATE.PRESSED)
            except Exception:
                return False
        return False

    def _check_rinkhals_ui_coexistence(self, app):
        """Yield /dev/fb0 and /dev/input/event0 if rinkhals-ui.py is active."""
        if not self.manage_system:
            return False
        self._ensure_watchdog()
        found = _find_pids_multi(("rinkhals-ui.py", "K3SysUi"), self.proc_root)
        other_ui = bool(found["rinkhals-ui.py"])
        k3_pids = found["K3SysUi"]
        if other_ui and not self._yielded:
            self._release_touch()
            self._touch_pressed = False
            self._yielded = True
        elif not other_ui and self._yielded:
            self._stop_k3sysui(k3_pids)
            self._write_pid_file()
            self._ensure_watchdog()
            self._yielded = False
            if self.touch is not None and not self._grab_touch("other UI closed"):
                self._yielded = True
                return True
            self._touch_pressed = False
            if app is not None:
                lock = getattr(app, "_lock", None)
                if lock is not None:
                    with lock:
                        app.dirty = True
                else:
                    app.dirty = True
            self.root.invalidate()
        elif not other_ui and not self._yielded and k3_pids:
            self._stop_k3sysui(k3_pids)
        return self._yielded

    def _compute_loop_sleep(self, now, app, next_timer_ms, touch_active):
        """Return adaptive sleep duration: fast during touch/updates, timer-paced when idle."""
        if (
            self._raw_capture_active
            or touch_active
            or (now - self._last_touch) < ACTIVE_TOUCH_WINDOW_SEC
            or getattr(app, "dirty", False)
        ):
            return MAIN_LOOP_SLEEP_SEC
        if self._asleep:
            return IDLE_LOOP_SLEEP_SEC
        # Ignore the touch read timer (10ms) for idle sleep; only use real LVGL timers (>10ms).
        # This restores the 35ms idle sleep instead of 10ms.
        if isinstance(next_timer_ms, (int, float)) and not isinstance(next_timer_ms, bool) and next_timer_ms > self._indev_period_ms:
            return max(IDLE_SLEEP_MIN_SEC, min(IDLE_SLEEP_MAX_SEC, next_timer_ms / 1000.0))
        return IDLE_LOOP_SLEEP_SEC

    def run(self, app):
        self._app = app
        self._running = True
        if hasattr(app, "display_brightness"):
            self.set_brightness(app.display_brightness)
        if hasattr(app, "display_asleep"):
            self.set_sleep(app.display_asleep)
        atexit.register(self.close)

        def _on_signal(signum):
            self._exit_signal = signum
            self._running = False

        _install_signal_stop(_on_signal)

        last_coexist_check = 0.0
        last_housekeeping = 0.0
        last_hb = 0.0
        hb_interval = min(1.0, max(0.05, self.hang_timeout / 4.0)) if self.hang_timeout > 0 else 1.0
        last_tick = time.monotonic()
        if self.manage_system:
            self._write_heartbeat()
            last_hb = last_tick
        has_tick_cal = hasattr(app, "tick_calibration")
        has_tick_disp = hasattr(app, "tick_display")
        has_tick_timers = hasattr(app, "tick_timers")
        has_raw_tap = hasattr(app, "record_calibration_tap")
        try:
            while self._running:
                now = time.monotonic()
                if self.manage_system and now - last_hb >= hb_interval:
                    last_hb = now
                    self._write_heartbeat()
                coexist_interval = COEXIST_CHECK_YIELDED_SEC if self._yielded else COEXIST_CHECK_ACTIVE_SEC
                if self.manage_system and now - last_coexist_check >= coexist_interval:
                    last_coexist_check = now
                    self._check_rinkhals_ui_coexistence(app)
                if self._yielded:
                    last_tick = time.monotonic()
                    time.sleep(0.2)
                    continue
                raw_ms = (now - last_tick) * 1000.0
                if raw_ms >= 1.0:
                    elapsed = int(raw_ms)
                    last_tick += elapsed / 1000.0
                else:
                    elapsed = 1
                    last_tick = now
                lv.tick_inc(elapsed)
                if (
                    not self._has_fast_read_timer
                    and self.touch is not None
                    and hasattr(self.touch, "read")
                ):
                    self.touch.read()
                next_timer_ms = lv.timer_handler()
                if self._touch_debug is not None:
                    self._touch_debug.poll()
                if self._raw_capture_active and self._raw_reader is not None:
                    raw_tap = self._raw_reader.poll()
                    if raw_tap is not None and has_raw_tap:
                        app.record_calibration_tap(*raw_tap)
                cal_obj = getattr(app, "touch_cal", None)
                if cal_obj is not None:
                    if getattr(cal_obj, "stage", None) == "verify":
                        self._verify_cb = lambda px, py: app.record_calibration_verify(px, py)
                    else:
                        self._verify_cb = None
                    if has_tick_cal:
                        app.tick_calibration()
                else:
                    self._verify_cb = None
                if now - last_housekeeping >= HOUSEKEEPING_INTERVAL_SEC:
                    last_housekeeping = now
                    if has_tick_disp:
                        app.tick_display()
                    if has_tick_timers:
                        app.tick_timers()
                need_layout = (self.shape is None) or bool(getattr(app, "dirty", False))
                if self._touch_pressed or need_layout or (now - self._last_touch) < ACTIVE_TOUCH_WINDOW_SEC:
                    touch_active = self._is_touch_active()
                else:
                    touch_active = False
                if touch_active and self._touch_pressed_since <= 0.0:
                    self._touch_pressed_since = now
                touch_defer = (
                    touch_active
                    and (now - self._touch_pressed_since) < MAX_PRESS_SYNC_DEFER_SEC
                )
                placed = None
                if need_layout and not touch_defer:
                    lock = getattr(app, "_lock", None)
                    if lock is not None:
                        with lock:
                            if self.shape is None or app.dirty:
                                app.dirty = False
                                placed = app.layout()
                    elif self.shape is None or app.dirty:
                        app.dirty = False
                        placed = app.layout()
                if placed is not None:
                    self.sync(placed)
                time.sleep(self._compute_loop_sleep(now, app, next_timer_ms, touch_active))
        finally:
            self.close()
        if self._exit_signal is not None:
            # Stopped by SIGTERM / SIGHUP / SIGINT (the hang watchdog, a kill): app.sh must see a failure exit and
            # restart us; `app.sh stop` and "return to stock" leave a marker / disable flag, so they stay clean.
            raise SystemExit(128 + int(self._exit_signal))


# Re-export key symbols for backward compatibility
from .lvgl_sys import (RINKHALS_ROOT, RINKHALS_UI, LOCAL_FONTS, FONT_CANDIDATES, ICON_FONT_CANDIDATES,
                       DEFAULT_FBDEV, DEFAULT_EVDEV, DEFAULT_DPI, TOUCH_CALIBRATION, MODEL_TOUCH_CALIBRATIONS,
                       MODEL_DPIS, DEFAULT_INDEV_READ_PERIOD_MS, DEFAULT_SCROLL_LIMIT, DEFAULT_EXT_CLICK_AREA,
                       MAIN_LOOP_SLEEP_SEC, IDLE_LOOP_SLEEP_SEC, IDLE_SLEEP_MIN_SEC, IDLE_SLEEP_MAX_SEC,
                       ACTIVE_TOUCH_WINDOW_SEC, HOUSEKEEPING_INTERVAL_SEC, COEXIST_CHECK_ACTIVE_SEC,
                       COEXIST_CHECK_YIELDED_SEC, MAX_PRESS_SYNC_DEFER_SEC, BACKLIGHT_PATH, KOBRA_PID_FILE,
                       KOBRA_HEARTBEAT_FILE, DEFAULT_HANG_TIMEOUT, PNG_SIGNATURE, RADIUS_CIRCLE,
                       LV_PART_MAIN, LV_PART_INDICATOR, LV_PART_KNOB, LV_PART_ITEMS, LV_CHART_POINT_NONE,
                       _WATCHDOG_SH, _part_indicator, _part_knob, _default_state, _text_align, color,
                       _enum_val, parse_calibration_str, _parse_bool, _load_touch_config, parse_screen_config,
                       parse_touch_options, _matches_process_name, _find_pids_multi, _find_pids, _pid_state,
                       _signal_pids, read_max_backlight, set_backlight_percent, _wake_backlight, _is_valid_png,
                       parse_raw_evdev_packet, _evdev_packet_size, _install_signal_stop)
from .lvgl_widgets import _MeshState

from .touch_diag import run_raw_touch_diagnostic, run_touch_diagnostic, _cli


if __name__ == "__main__":
    raise SystemExit(_cli())