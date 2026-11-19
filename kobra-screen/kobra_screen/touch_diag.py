"""Touch diagnostic CLI helpers for LVGL rendering on Kobra 3."""

import os
import select
import sys
import time

from .lvgl_sys import (_default_state, _enum_val, _evdev_packet_size, _install_signal_stop,  # noqa: F401
                       _is_valid_png, _lv, _wake_backlight, color, MAIN_LOOP_SLEEP_SEC, parse_raw_evdev_packet,
                       parse_screen_config, parse_touch_options, TOUCH_CALIBRATION, WIDTH, HEIGHT, RADIUS_CIRCLE)


def run_raw_touch_diagnostic(duration=30.0, evdev_path=None, stream=None):
    """Print raw /dev/input/event0 coordinates, BTN_TOUCH transitions, and min/max bounds."""
    out = sys.stdout if stream is None else stream
    if evdev_path is None:
        _, evdev_path, _, _, cal = parse_screen_config()
    else:
        cal = TOUCH_CALIBRATION
    opts = parse_touch_options()
    pkt_size = _evdev_packet_size()
    out.write(
        "kobra-screen touch-raw: device=%s pkt=%dB duration=%.1fs cal=%s swap_axes=%s\n"
        % (evdev_path, pkt_size, float(duration), cal, opts["swap_axes"])
    )
    out.write("Tap corners and center of the display to inspect raw ADC min/max bounds.\n")
    out.flush()

    stopped = [False]
    _install_signal_stop(lambda _sig: stopped.__setitem__(0, True))

    try:
        fd = os.open(evdev_path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as exc:
        out.write("ERROR: failed to open %s: %s\n" % (evdev_path, exc))
        out.flush()
        return 1

    min_x = min_y = None
    max_x = max_y = None
    cur_x = cur_y = None
    touch_down = False
    down_at = 0.0
    tap_count = 0
    deadline = time.monotonic() + max(1.0, float(duration))
    try:
        while not stopped[0] and time.monotonic() < deadline:
            rlist, _, _ = select.select([fd], [], [], 0.2)
            if not rlist:
                continue
            try:
                buf = os.read(fd, pkt_size * 32)
            except BlockingIOError:
                continue
            for off in range(0, len(buf) - (len(buf) % pkt_size), pkt_size):
                _, _, ev_type, code, value = parse_raw_evdev_packet(buf[off:off + pkt_size])
                if ev_type == 3:  # EV_ABS
                    if code in (0, 53):  # ABS_X / ABS_MT_POSITION_X
                        cur_x = value
                        min_x = value if min_x is None else min(min_x, value)
                        max_x = value if max_x is None else max(max_x, value)
                    elif code in (1, 54):  # ABS_Y / ABS_MT_POSITION_Y
                        cur_y = value
                        min_y = value if min_y is None else min(min_y, value)
                        max_y = value if max_y is None else max(max_y, value)
                elif ev_type == 1 and code == 330:  # EV_KEY BTN_TOUCH
                    now = time.monotonic()
                    if value:
                        touch_down = True
                        down_at = now
                        out.write("DOWN raw=(%s, %s)\n" % (cur_x, cur_y))
                    else:
                        dt_ms = (now - down_at) * 1000.0 if touch_down else 0.0
                        touch_down = False
                        tap_count += 1
                        out.write(
                            "UP   raw=(%s, %s) dur=%.1fms bounds_x=[%s,%s] bounds_y=[%s,%s]\n"
                            % (cur_x, cur_y, dt_ms, min_x, max_x, min_y, max_y)
                        )
                    out.flush()
    finally:
        os.close(fd)

    out.write(
        "SUMMARY: taps=%d raw_x=[%s..%s] raw_y=[%s..%s]\n"
        % (tap_count, min_x, max_x, min_y, max_y)
    )
    out.flush()
    return 0


def run_touch_diagnostic(duration=60.0, screen=None, stream=None):
    """Interactive visual crosshair diagnostic showing mapped LVGL touch coordinates vs targets."""
    from . import render_lvgl
    lv = _lv()

    out = sys.stdout if stream is None else stream
    owns_screen = screen is None
    if screen is None:
        screen = render_lvgl.LvglScreen(manage_system=True)
    default = _default_state()
    screen.root.clean()

    header = screen._text(screen.root, (10, 8, WIDTH - 20, 22), "med", "left")
    header.set_text("TOUCH DIAG")
    header.set_style_text_color(color("text"), default)

    cal_label = screen._text(screen.root, (10, 32, WIDTH - 20, 32), "small", "left")
    cal_label.set_text(
        "cal=%s swap=%s %dms"
        % (screen._touch_calibration, screen._touch_swap_axes, screen._indev_period_ms)
    )
    cal_label.set_style_text_color(color("muted"), default)

    status_label = screen._text(screen.root, (10, 68, WIDTH - 20, 36), "small", "left")
    status_label.set_text("Tap targets or anywhere. Wait timeout to exit.")
    status_label.set_style_text_color(color("accent"), default)

    targets = [
        ("TL", 20, 20),
        ("TR", WIDTH - 20, 20),
        ("C", WIDTH // 2, HEIGHT // 2),
        ("BL", 20, HEIGHT - 20),
        ("BR", WIDTH - 20, HEIGHT - 20),
    ]
    for _, tx, ty in targets:
        mark = screen._box(screen.root, tx - 7, ty - 7, 14, 14)
        mark.set_style_radius(RADIUS_CIRCLE, default)
        mark.set_style_border_width(2, default)
        mark.set_style_border_color(color("warn"), default)

    marker_r = 9
    marker = screen._box(screen.root, WIDTH // 2 - marker_r, HEIGHT // 2 - marker_r, marker_r * 2, marker_r * 2)
    marker.set_style_radius(RADIUS_CIRCLE, default)
    marker.set_style_bg_opa(lv.OPA.COVER, default)
    marker.set_style_bg_color(color("ok"), default)
    marker.set_style_border_width(2, default)
    marker.set_style_border_color(color("text"), default)

    exit_btn = lv.button(screen.root)
    screen._plain(exit_btn, WIDTH // 2 - 56, HEIGHT - 54, 112, 36)
    exit_btn.set_style_radius(8, default)
    exit_btn.set_style_bg_opa(lv.OPA.COVER, default)
    exit_btn.set_style_bg_color(color("surface3"), default)
    exit_btn.set_style_border_width(1, default)
    exit_btn.set_style_border_color(color("danger"), default)
    exit_lbl = screen._text(exit_btn, (0, 0, 112, 36), "med", "center")
    exit_lbl.set_text("EXIT DIAG")
    exit_lbl.set_style_text_color(color("text"), default)

    diag_state = {"done": False, "count": 0, "last_x": None, "last_y": None}
    exit_btn.add_event_cb(lambda _ev: diag_state.__setitem__("done", True), lv.EVENT_CODE.CLICKED, None)

    pt = lv.point() if hasattr(lv, "point") else None

    def _read_point_xy():
        if pt is None or screen.touch is None or not hasattr(screen.touch, "get_point"):
            return None, None
        screen.touch.get_point(pt)
        if hasattr(pt, "_pointer"):
            try:
                return int(pt._pointer[0].x), int(pt._pointer[0].y)
            except Exception:
                pass
        if hasattr(pt, "x") and hasattr(pt, "y"):
            return int(pt.x), int(pt.y)
        return None, None

    event_names = {
        _enum_val(getattr(lv.EVENT_CODE, name)): name
        for name in ("PRESSED", "PRESSING", "PRESS_LOST", "SHORT_CLICKED", "CLICKED", "RELEASED")
        if hasattr(lv.EVENT_CODE, name)
    }

    def _on_diag_event(code):
        name = event_names.get(code, str(code))
        px, py = _read_point_xy()
        if px is not None and py is not None:
            mx = max(0, min(WIDTH - marker_r * 2, px - marker_r))
            my = max(0, min(HEIGHT - marker_r * 2, py - marker_r))
            marker.set_pos(mx, my)
            diag_state["last_x"] = px
            diag_state["last_y"] = py
        if name == "PRESSED":
            diag_state["count"] += 1
        if name in ("PRESSED", "CLICKED", "RELEASED", "PRESS_LOST"):
            msg = "#%d %s (%s, %s)" % (diag_state["count"], name, px, py)
            status_label.set_text(msg)
            out.write("TOUCH_DIAG: %s\n" % msg)
            out.flush()

    prev_cb = screen._touch_diag_cb
    screen._touch_diag_cb = _on_diag_event
    screen._running = True
    _install_signal_stop(lambda _sig: diag_state.__setitem__("done", True))

    out.write(
        "kobra-screen touch-diag: cal=%s swap=%s period=%dms scroll_limit=%d ext_click=%d\n"
        % (
            screen._touch_calibration,
            screen._touch_swap_axes,
            screen._indev_period_ms,
            screen._scroll_limit,
            screen._ext_click_max,
        )
    )
    out.flush()

    deadline = time.monotonic() + max(1.0, float(duration))
    last_tick = time.monotonic()
    last_hb = 0.0
    hb_interval = min(1.0, max(0.05, screen.hang_timeout / 4.0)) if screen.hang_timeout > 0 else 1.0
    try:
        while screen._running and not diag_state["done"] and time.monotonic() < deadline:
            now = time.monotonic()
            if screen.manage_system and now - last_hb >= hb_interval:
                last_hb = now
                screen._write_heartbeat()
            elapsed = max(1, int((now - last_tick) * 1000))
            last_tick = now
            lv.tick_inc(elapsed)
            if not screen._has_fast_read_timer and screen.touch is not None and hasattr(screen.touch, "read"):
                screen.touch.read()
            lv.timer_handler()
            render_lvgl.time.sleep(MAIN_LOOP_SLEEP_SEC)
    finally:
        screen._touch_diag_cb = prev_cb
        if owns_screen:
            screen.close()
    return 0


def _cli(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="kobra-screen LVGL / touch diagnostic helpers")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_diag = sub.add_parser("touch-diag", help="Visual LVGL touch coordinate diagnostic")
    p_diag.add_argument("--duration", type=float, default=60.0)
    p_raw = sub.add_parser("touch-raw", help="Raw /dev/input/event0 touch packet diagnostic")
    p_raw.add_argument("--duration", type=float, default=30.0)
    p_raw.add_argument("--evdev", default=None)
    args = parser.parse_args(argv)
    if args.cmd == "touch-diag":
        return run_touch_diagnostic(duration=args.duration)
    if args.cmd == "touch-raw":
        return run_raw_touch_diagnostic(duration=args.duration, evdev_path=args.evdev)
    return 0