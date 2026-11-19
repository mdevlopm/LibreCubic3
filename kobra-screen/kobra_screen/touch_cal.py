"""Touchscreen calibration math, raw evdev reader, and persistent Rinkhals config helpers."""

import json
import math
import os
import select
import struct
import tempfile
import threading
import time

DEFAULT_ROTATION = 90
DEFAULT_VERIFY_THRESHOLD_PX = 25.0
CONFIRM_TIMEOUT_SEC = 15.0
CAPTURE_TIMEOUT_SEC = 30.0

TOUCH_CALIBRATION = (25, 235, 460, 25)
MODEL_TOUCH_CALIBRATIONS = {
    "K3": (25, 235, 460, 25),
    "K3V2": (25, 235, 460, 25),
    "K3M": (25, 235, 460, 25),
    "K2P": (25, 235, 460, 25),
    "KS1": (0, 0, 800, 480),
    "KS1M": (0, 0, 800, 480),
}
MODEL_DPIS = {
    "KS1": 180,
    "KS1M": 180,
}


def lv_rotation_degrees(qpa_rotation):
    """Map QT_QPA_PLATFORM rotation to LVGL DISPLAY_ROTATION degrees."""
    return {0: 0, 90: 270, 180: 180, 270: 90}.get(int(qpa_rotation), 270)


def physical_resolution(width=272, height=480, lv_rot=270):
    if int(lv_rot) in (90, 270):
        return int(height), int(width)
    return int(width), int(height)


def calibration_targets(width=272, height=480):
    """Return 4 inset corner targets and the center verification target in logical coordinates."""
    w, h = int(width), int(height)
    corners = (
        ("Sol üst", 32, 64),
        ("Sağ üst", w - 32, 64),
        ("Sol alt", 32, h - 36),
        ("Sağ alt", w - 32, h - 36),
    )
    center = ("Orta", w // 2, h // 2)
    return corners, center


def logical_to_physical(tx, ty, width=272, height=480, lv_rot=270):
    """Invert LVGL pointer rotation to physical (disp->hor_res, disp->ver_res) coordinates."""
    w_phys, h_phys = physical_resolution(width, height, lv_rot)
    rot = int(lv_rot) % 360
    if rot == 0:
        return int(tx), int(ty)
    if rot == 90:
        return int(ty), (h_phys - 1) - int(tx)
    if rot == 180:
        return (w_phys - 1) - int(tx), (h_phys - 1) - int(ty)
    return (w_phys - 1) - int(ty), int(tx)


def _evdev_calibrate_val(v, in_min, in_max, out_min, out_max):
    if in_min == in_max:
        return out_min
    res = (v - in_min) * (out_max - out_min) // (in_max - in_min) + out_min
    lo, hi = (out_min, out_max) if out_min <= out_max else (out_max, out_min)
    return max(lo, min(hi, res))


def simulate_lvgl_point(rx, ry, cal, swap_axes=False, width=272, height=480, lv_rot=270):
    """Simulate LVGL 9 _evdev_process_pointer + display pointer rotation (matches 0003-evdev-pointer-fix.patch)."""
    min_x, min_y, max_x, max_y = [int(v) for v in cal]
    w_phys, h_phys = physical_resolution(width, height, lv_rot)
    sx = int(ry) if swap_axes else int(rx)
    sy = int(rx) if swap_axes else int(ry)
    px = _evdev_calibrate_val(sx, min_x, max_x, 0, w_phys - 1)
    py = _evdev_calibrate_val(sy, min_y, max_y, 0, h_phys - 1)
    rot = int(lv_rot) % 360
    if rot == 0:
        return px, py
    if rot == 90:
        return (h_phys - 1) - py, px
    if rot == 180:
        return (w_phys - 1) - px, (h_phys - 1) - py
    return py, (w_phys - 1) - px


def _split_axis_means(coords, values):
    lo_val = min(coords)
    hi_val = max(coords)
    if lo_val == hi_val:
        raise ValueError("Geçersiz hedef koordinatları")
    mid = (lo_val + hi_val) / 2.0
    lo_items = [(c, v) for c, v in zip(coords, values) if c < mid]
    hi_items = [(c, v) for c, v in zip(coords, values) if c >= mid]
    if not lo_items or not hi_items:
        raise ValueError("Köşe noktaları yeterince ayrı değil; tekrar deneyin")
    c_lo = sum(c for c, _ in lo_items) / float(len(lo_items))
    v_lo = sum(v for _, v in lo_items) / float(len(lo_items))
    c_hi = sum(c for c, _ in hi_items) / float(len(hi_items))
    v_hi = sum(v for _, v in hi_items) / float(len(hi_items))
    return c_lo, v_lo, c_hi, v_hi


def compute_calibration(targets, raw_points, width=272, height=480, lv_rot=270):
    """Compute `(min_x, min_y, max_x, max_y)` and `swap_axes` from 4 corner taps."""
    if len(targets) != 4 or len(raw_points) != 4:
        raise ValueError("Kalibrasyon için 4 köşe dokunuşu gerekli")
    w_phys, h_phys = physical_resolution(width, height, lv_rot)
    phys = [logical_to_physical(tx, ty, width, height, lv_rot) for tx, ty in targets]
    px_coords = [p[0] for p in phys]
    py_coords = [p[1] for p in phys]
    raw_xs = [float(p[0]) for p in raw_points]
    raw_ys = [float(p[1]) for p in raw_points]

    _, rx_px_lo, _, rx_px_hi = _split_axis_means(px_coords, raw_xs)
    _, ry_px_lo, _, ry_px_hi = _split_axis_means(px_coords, raw_ys)
    _, rx_py_lo, _, rx_py_hi = _split_axis_means(py_coords, raw_xs)
    _, ry_py_lo, _, ry_py_hi = _split_axis_means(py_coords, raw_ys)

    dx_for_px = abs(rx_px_hi - rx_px_lo)
    dy_for_px = abs(ry_px_hi - ry_px_lo)
    dx_for_py = abs(rx_py_hi - rx_py_lo)
    dy_for_py = abs(ry_py_hi - ry_py_lo)

    swap_axes = (dy_for_px + dx_for_py) > (dx_for_px + dy_for_py)
    sx_vals = raw_ys if swap_axes else raw_xs
    sy_vals = raw_xs if swap_axes else raw_ys

    px_lo, sx_lo, px_hi, sx_hi = _split_axis_means(px_coords, sx_vals)
    py_lo, sy_lo, py_hi, sy_hi = _split_axis_means(py_coords, sy_vals)

    slope_x = (sx_hi - sx_lo) / float(px_hi - px_lo)
    slope_y = (sy_hi - sy_lo) / float(py_hi - py_lo)

    min_x = int(round(sx_lo - slope_x * px_lo))
    max_x = int(round(sx_lo + slope_x * ((w_phys - 1) - px_lo)))
    min_y = int(round(sy_lo - slope_y * py_lo))
    max_y = int(round(sy_lo + slope_y * ((h_phys - 1) - py_lo)))

    if abs(max_x - min_x) < 20 or abs(max_y - min_y) < 20:
        raise ValueError("Köşe noktaları yeterince ayrı değil; tekrar deneyin")

    return (min_x, min_y, max_x, max_y), bool(swap_axes)


def model_default_calibration(env=None):
    env = os.environ if env is None else env
    model_code = (env.get("KOBRA_MODEL_CODE") or "").strip().upper()
    if model_code in MODEL_TOUCH_CALIBRATIONS:
        return MODEL_TOUCH_CALIBRATIONS[model_code], False
    qpa = env.get("QT_QPA_PLATFORM", "")
    for token in qpa.split(":"):
        if token.startswith("size=") and token.split("=", 1)[1].strip().lower() == "800x480":
            return (0, 0, 800, 480), False
    return TOUCH_CALIBRATION, False


def resolve_persistent_touch_config_path(env=None):
    env = os.environ if env is None else env
    explicit = (env.get("KOBRA_TOUCH_CONFIG") or "").strip()
    if explicit:
        return explicit
    app_name = env.get("KOBRA_APP_NAME", "kobra-screen")
    rinkhals_home = env.get("RINKHALS_HOME", "/useremain/home/rinkhals")
    return os.path.join(rinkhals_home, "apps", app_name + ".config")


# One lock for every read-modify-write of the shared .config / events files (UI, poll and worker threads).
CONFIG_LOCK = threading.RLock()


def _atomic_write_text(path, content):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    # mkstemp: every writer gets its own temp file (threads share one PID, so "<path>.tmp.<pid>" collided)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".tmp.", dir=parent or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    if hasattr(os, "sync"):
        try:
            os.sync()
        except OSError:
            pass


def _read_json_dict(path):
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        if raw.startswith("{"):
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
    except (OSError, ValueError):
        pass
    return {}


def update_json_config(path, updates, ensure_ascii=True):
    """Merge `updates` into the JSON config at `path` atomically; concurrent callers never lose each other's keys."""
    with CONFIG_LOCK:
        data = _read_json_dict(path)
        data.update(updates)
        _atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=ensure_ascii) + "\n")
        return data


def save_touch_config(cal, swap_axes, env=None, config_path=None):
    """Persist touch calibration into Rinkhals `.config` JSON (or KOBRA_TOUCH_CONFIG)."""
    env = os.environ if env is None else env
    path = config_path or resolve_persistent_touch_config_path(env)
    cal_str = "%d,%d,%d,%d" % tuple(int(v) for v in cal)
    swap_str = "true" if swap_axes else "false"
    update_json_config(path, {"touch_calibration": cal_str, "touch_swap_axes": swap_str})
    if env is os.environ:
        env["KOBRA_TOUCH_CALIBRATION"] = cal_str
        env["KOBRA_TOUCH_SWAP_AXES"] = swap_str
    return path


def reset_touch_config(env=None, config_path=None):
    """Reset persistent touch calibration to model defaults and return `(cal, swap_axes)`."""
    env = os.environ if env is None else env
    default_cal, default_swap = model_default_calibration(env)
    save_touch_config(default_cal, default_swap, env=env, config_path=config_path)
    return default_cal, default_swap


def parse_raw_evdev_packet(data):
    """Unpack one Linux input_event (16B on 32-bit ARM, 24B on 64-bit) into (sec, usec, type, code, value)."""
    if len(data) == 16:
        return struct.unpack("<iiHHi", data)
    if len(data) == 24:
        return struct.unpack("<qqHHi", data)
    raise ValueError("Unsupported evdev packet size: %d" % len(data))


class RawEvdevReader:
    """Non-blocking direct `/dev/input/event0` reader for uncalibrated corner calibration taps."""

    def __init__(self, evdev_path="/dev/input/event0", packet_size=None, open_fn=None, read_fn=None, close_fn=None):
        self.evdev_path = evdev_path
        self.packet_size = packet_size or struct.calcsize("llHHi")
        self._open = open_fn or (lambda p: os.open(p, os.O_RDONLY | os.O_NONBLOCK))
        self._read = read_fn or os.read
        self._close = close_fn or os.close
        self.fd = self._open(evdev_path)
        self._cur_x = None
        self._cur_y = None
        self._pressed = False
        self._samples_x = []
        self._samples_y = []

    def close(self):
        if self.fd is not None:
            try:
                self._close(self.fd)
            except OSError:
                pass
            self.fd = None

    def poll(self):
        """Drain pending evdev packets and return `(raw_x, raw_y)` when a tap completes."""
        if self.fd is None:
            return None
        completed = None
        while True:
            try:
                buf = self._read(self.fd, self.packet_size * 32)
            except BlockingIOError:
                break
            except OSError:
                break
            if not buf:
                break
            for off in range(0, len(buf) - (len(buf) % self.packet_size), self.packet_size):
                _, _, ev_type, code, value = parse_raw_evdev_packet(buf[off:off + self.packet_size])
                if ev_type == 3:  # EV_ABS
                    if code in (0, 53):  # ABS_X / ABS_MT_POSITION_X
                        self._cur_x = int(value)
                    elif code in (1, 54):  # ABS_Y / ABS_MT_POSITION_Y
                        self._cur_y = int(value)
                elif ev_type == 0 and code == 0 and self._pressed:  # EV_SYN SYN_REPORT
                    if self._cur_x is not None and self._cur_y is not None:
                        self._samples_x.append(self._cur_x)
                        self._samples_y.append(self._cur_y)
                elif ev_type == 1 and code == 330:  # EV_KEY BTN_TOUCH
                    if value:
                        if not self._pressed:
                            self._pressed = True
                            self._samples_x = []
                            self._samples_y = []
                            # No sample from the old _cur_x/_cur_y here: the driver may send BTN_TOUCH before the
                            # new ABS_X/ABS_Y of the same packet, and the old position belongs to the last tap.
                    else:
                        if self._pressed:
                            xs = sorted(self._samples_x) if self._samples_x else ([self._cur_x] if self._cur_x is not None else [])
                            ys = sorted(self._samples_y) if self._samples_y else ([self._cur_y] if self._cur_y is not None else [])
                            if xs and ys:
                                completed = (xs[len(xs) // 2], ys[len(ys) // 2])
                        self._pressed = False
                        self._samples_x = []
                        self._samples_y = []
            if len(buf) < self.packet_size * 32:
                break
        return completed
