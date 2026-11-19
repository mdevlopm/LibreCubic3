"""System/configuration helpers for LVGL rendering on Kobra 3."""

import json
import os
import signal
import struct
import subprocess
import sys

from . import bed_mesh, touch_debug
from .touch_cal import (RawEvdevReader, lv_rotation_degrees, reset_touch_config, save_touch_config)
from .widgets import (DOT_SIZE, HEIGHT, WIDTH, Bar, Button, Chart, Crosshair, Icon, Label, MeshSurface, Panel,  # noqa: F401
                      Picture, Ring, Rule, text_width, button_border, button_border_width,
                      button_dot_radius, button_fill, button_icon_color, button_icon_size, button_label,
                      button_parts, button_radius, button_sub_label, button_text_color,
                      label_unit_size, swatch_check_color)
from .theme import COLORS, FONT_SIZES, ICON_SIZES, ICONS  # noqa: F401

RINKHALS_ROOT = os.environ.get("RINKHALS_ROOT", "/useremain/rinkhals/.current")
RINKHALS_UI = os.environ.get("RINKHALS_UI", os.path.join(RINKHALS_ROOT, "opt/rinkhals/ui"))
if RINKHALS_UI not in sys.path:
    sys.path.insert(0, RINKHALS_UI)

LOCAL_FONTS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fonts") + "/"
FONT_CANDIDATES = (
    os.environ.get("KOBRA_SCREEN_FONT", ""),
    os.path.join(RINKHALS_UI, "assets/AlibabaSans-Regular.ttf"),
    "/useremain/rinkhals/.current/opt/rinkhals/ui/assets/AlibabaSans-Regular.ttf",
    "/ac_lib/lib/third_party_libs/freetype/font/AlibabaSans-Regular.ttf",
    LOCAL_FONTS + "AlibabaSans-Regular.ttf",
    "/usr/share/fonts/google-noto/NotoSans-Medium.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
ICON_FONT_CANDIDATES = (
    os.environ.get("KOBRA_SCREEN_ICON_FONT", ""),
    os.path.join(RINKHALS_UI, "assets/MaterialIcons-Regular.ttf"),
    "/useremain/rinkhals/.current/opt/rinkhals/ui/assets/MaterialIcons-Regular.ttf",
    LOCAL_FONTS + "MaterialIcons-Regular.ttf",
)
FONT_FILE = next((p for p in FONT_CANDIDATES if p and os.path.exists(p)),
                 "/useremain/rinkhals/.current/opt/rinkhals/ui/assets/AlibabaSans-Regular.ttf")
ICON_FONT_FILE = next((p for p in ICON_FONT_CANDIDATES if p and os.path.exists(p)),
                      "/useremain/rinkhals/.current/opt/rinkhals/ui/assets/MaterialIcons-Regular.ttf")

# Kobra 3 panel: 480x272 mounted rotated 90 deg; values from Rinkhals common.py ScreenInfo.get()
DEFAULT_FBDEV = "/dev/fb0"
DEFAULT_EVDEV = "/dev/input/event0"
DEFAULT_DPI = 130
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
DEFAULT_INDEV_READ_PERIOD_MS = 10
DEFAULT_SCROLL_LIMIT = 12
DEFAULT_EXT_CLICK_AREA = 4
MAIN_LOOP_SLEEP_SEC = 0.005
IDLE_LOOP_SLEEP_SEC = 0.035
IDLE_SLEEP_MIN_SEC = 0.010
IDLE_SLEEP_MAX_SEC = 0.050
ACTIVE_TOUCH_WINDOW_SEC = 0.75
HOUSEKEEPING_INTERVAL_SEC = 0.25
COEXIST_CHECK_ACTIVE_SEC = 2.0
COEXIST_CHECK_YIELDED_SEC = 1.0
MAX_PRESS_SYNC_DEFER_SEC = 1.5
BACKLIGHT_PATH = "/sys/class/backlight/backlight/brightness"
KOBRA_PID_FILE = os.environ.get("KOBRA_PID_FILE", "/tmp/kobra-screen.pid")
KOBRA_HEARTBEAT_FILE = os.environ.get("KOBRA_HEARTBEAT_FILE", "/tmp/kobra-screen.hb")
DEFAULT_HANG_TIMEOUT = 20.0
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

RADIUS_CIRCLE = 0x7FFF
# In Rinkhals opt/rinkhals/ui/lvgl/__init__.py:314-327, `class PART(Flag)` is defined
# twice and the second definition clobbers MAIN, INDICATOR and KNOB. Passing LVGL 9's
# integer selector (or the enum member if present) works across all set_style_* calls.
LV_PART_MAIN = 0x000000
LV_PART_INDICATOR = 0x020000
LV_PART_KNOB = 0x030000
LV_PART_ITEMS = 0x050000
LV_CHART_POINT_NONE = 2147483647

_WATCHDOG_SH = r"""
pid_file="$1"
owner_pid="$2"
hb_file="$3"
hang_timeout="$4"
hb_poll="$5"
log_file="$6"

hb_mon_pid=""
if [ -n "$hb_file" ] && [ -n "$hang_timeout" ] && awk -v t="$hang_timeout" 'BEGIN { exit !(t > 0) }'; then
    (
        last_hb=""
        stale="0"
        while kill -0 "$owner_pid" 2>/dev/null; do
            sleep "$hb_poll" 2>/dev/null || sleep 1
            kill -0 "$owner_pid" 2>/dev/null || exit 0
            cur_hb=""
            if [ -r "$hb_file" ]; then
                cur_hb=$(cat "$hb_file" 2>/dev/null | tr -d '[:space:]')
            fi
            if [ -n "$cur_hb" ] && [ "$cur_hb" != "$last_hb" ]; then
                last_hb="$cur_hb"
                stale="0"
            else
                stale=$(awk -v s="$stale" -v p="$hb_poll" 'BEGIN { printf "%.3f", s + p }')
                if awk -v s="$stale" -v t="$hang_timeout" 'BEGIN { exit !(s >= t) }'; then
                    if [ -n "$log_file" ]; then
                        printf '%s: kobra-screen heartbeat stalled for %ss; killing PID %s\n' \
                            "$(date '+%Y-%m-%d %H:%M:%S')" "$hang_timeout" "$owner_pid" >> "$log_file" 2>/dev/null || true
                    fi
                    kill -TERM "$owner_pid" 2>/dev/null || true
                    sleep 0.2 2>/dev/null || sleep 1
                    if kill -0 "$owner_pid" 2>/dev/null; then
                        kill -KILL "$owner_pid" 2>/dev/null || true
                    fi
                    exit 0
                fi
            fi
        done
    ) </dev/null &
    hb_mon_pid=$!
fi

IFS= read -r mode || true
if [ -n "$hb_mon_pid" ]; then
    kill "$hb_mon_pid" 2>/dev/null || true
    wait "$hb_mon_pid" 2>/dev/null || true
fi
if [ "$mode" = "graceful" ]; then
    exit 0
fi

cleaned_owner=0
if [ -n "$pid_file" ] && [ -f "$pid_file" ]; then
    cur=$(cat "$pid_file" 2>/dev/null | tr -d '[:space:]')
    if [ -z "$cur" ] || [ "$cur" = "$owner_pid" ]; then
        rm -f "$pid_file" 2>/dev/null || true
        cleaned_owner=1
    fi
elif [ -z "$pid_file" ] || [ ! -e "$pid_file" ]; then
    cleaned_owner=1
fi
if [ "$cleaned_owner" -eq 1 ] && [ -n "$hb_file" ]; then
    rm -f "$hb_file" 2>/dev/null || true
fi
"""


def _lv():
    mod = sys.modules.get(__package__ + ".render_lvgl")
    if mod is not None and getattr(mod, "lv", None) is not None:
        return mod.lv
    import lvgl as lv
    if mod is not None:
        mod.lv = lv
    return lv


def _part_indicator():
    lv = _lv()
    part = getattr(lv, "PART", None)
    return getattr(part, "INDICATOR", LV_PART_INDICATOR)


def _part_knob():
    lv = _lv()
    part = getattr(lv, "PART", None)
    return getattr(part, "KNOB", LV_PART_KNOB)


def _default_state():
    lv = _lv()
    return lv.STATE.DEFAULT


def _text_align(align):
    lv = _lv()
    return {
        "left": lv.TEXT_ALIGN.LEFT,
        "right": lv.TEXT_ALIGN.RIGHT,
        "center": lv.TEXT_ALIGN.CENTER,
    }[align]


def color(value):
    lv = _lv()
    return lv.color_make(*(COLORS[value] if isinstance(value, str) else value))


def _enum_val(val):
    return val.value if hasattr(val, "value") else int(val)


def parse_calibration_str(value):
    """Parse 4 calibration integers (min_x, min_y, max_x, max_y); return tuple or None."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        parts = [p for p in text.replace(",", " ").split() if p]
    if len(parts) != 4:
        return None
    try:
        nums = tuple(int(p) for p in parts)
    except (TypeError, ValueError):
        return None
    if nums[0] == nums[2] or nums[1] == nums[3]:
        return None
    return nums


def _parse_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return default


def _load_touch_config(env=None):
    """Read optional touch settings from KOBRA_TOUCH_CONFIG, Rinkhals .config, or touch.conf."""
    env = os.environ if env is None else env
    app_name = env.get("KOBRA_APP_NAME", "kobra-screen")
    app_root = env.get("KOBRA_APP_ROOT") or os.path.dirname(os.path.dirname(__file__))
    rinkhals_home = env.get("RINKHALS_HOME", "/useremain/home/rinkhals")

    candidates = []
    explicit = env.get("KOBRA_TOUCH_CONFIG")
    if explicit:
        candidates.append(explicit)
    else:
        candidates.extend([
            os.path.join(rinkhals_home, "apps", app_name + ".config"),
            os.path.join("/tmp/rinkhals/apps", app_name + ".config"),
            os.path.join(app_root, "touch.conf"),
        ])

    merged = {}
    for path in reversed(candidates):
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read().strip()
        except OSError:
            continue
        if not raw:
            continue
        if raw.startswith("{"):
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    merged.update(data)
                continue
            except ValueError:
                pass
        for line in raw.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                key = k.strip().lower()
                val = v.strip().strip('"').strip("'")
                if key in ("calibration", "touch_calibration"):
                    merged["touch_calibration"] = val
                elif key in ("swap_axes", "touch_swap_axes"):
                    merged["touch_swap_axes"] = val
                elif key in ("poll_ms", "touch_poll_ms"):
                    merged["touch_poll_ms"] = val
                elif key in ("scroll_limit", "touch_scroll_limit"):
                    merged["touch_scroll_limit"] = val
            elif parse_calibration_str(line) is not None:
                merged["touch_calibration"] = line
    return merged


def parse_screen_config(env=None):
    """Parse QT_QPA_PLATFORM and touch calibration, defaulting to Kobra 3."""
    env = os.environ if env is None else env
    qpa = env.get("QT_QPA_PLATFORM", "linuxfb:fb=/dev/fb0:size=480x272:rotation=90")
    fbdev = DEFAULT_FBDEV
    rotation = 90
    panel_size = None
    for token in qpa.split(":"):
        if "=" not in token:
            continue
        key, val = token.split("=", 1)
        if key == "fb" and val:
            fbdev = val
        elif key == "rotation":
            try:
                rotation = int(val)
            except ValueError:
                rotation = 90
        elif key == "size" and "x" in val:
            panel_size = val.strip().lower()
    evdev = env.get("KOBRA_SCREEN_EVDEV", DEFAULT_EVDEV)

    model_code = (env.get("KOBRA_MODEL_CODE") or "").strip().upper()
    if model_code in MODEL_TOUCH_CALIBRATIONS:
        default_cal = MODEL_TOUCH_CALIBRATIONS[model_code]
        dpi = MODEL_DPIS.get(model_code, DEFAULT_DPI)
    elif panel_size == "800x480":
        default_cal = (0, 0, 800, 480)
        dpi = 180
    else:
        default_cal = TOUCH_CALIBRATION
        dpi = DEFAULT_DPI

    cfg = _load_touch_config(env)
    raw_cal = env.get("KOBRA_TOUCH_CALIBRATION") or cfg.get("touch_calibration")
    calibration = default_cal
    if raw_cal:
        parsed_cal = parse_calibration_str(raw_cal)
        if parsed_cal is not None:
            calibration = parsed_cal
        else:
            sys.stderr.write(
                "kobra-screen: warning: invalid touch_calibration %r; using default %r\n"
                % (raw_cal, default_cal)
            )
    return fbdev, evdev, rotation, dpi, calibration


def parse_touch_options(env=None):
    """Return evdev/LVGL tuning options from env and optional config file."""
    env = os.environ if env is None else env
    cfg = _load_touch_config(env)
    swap_raw = env.get("KOBRA_TOUCH_SWAP_AXES")
    if swap_raw is None or swap_raw == "":
        swap_raw = cfg.get("touch_swap_axes")
    swap_axes = _parse_bool(swap_raw, default=False)

    def _clamp_int(raw, default, low, high):
        if raw is None or str(raw).strip() == "":
            return default
        try:
            return max(low, min(high, int(raw)))
        except (TypeError, ValueError):
            return default

    poll_ms = _clamp_int(
        env.get("KOBRA_TOUCH_POLL_MS") or cfg.get("touch_poll_ms"),
        DEFAULT_INDEV_READ_PERIOD_MS, 1, 50,
    )
    scroll_limit = _clamp_int(
        env.get("KOBRA_TOUCH_SCROLL_LIMIT") or cfg.get("touch_scroll_limit"),
        DEFAULT_SCROLL_LIMIT, 1, 50,
    )
    ext_click = _clamp_int(
        env.get("KOBRA_TOUCH_EXT_CLICK"),
        DEFAULT_EXT_CLICK_AREA, 0, 16,
    )
    return {
        "swap_axes": swap_axes,
        "indev_period_ms": poll_ms,
        "scroll_limit": scroll_limit,
        "ext_click_max": ext_click,
    }


def _matches_process_name(target_name, comm, args):
    base0 = os.path.basename(args[0]) if args else ""
    if target_name == "K3SysUi":
        return comm == "K3SysUi" or base0 == "K3SysUi"
    if target_name.endswith(".py"):
        if base0 == target_name:
            return True
        if base0.startswith("python"):
            return any(os.path.basename(a) == target_name for a in args[1:])
        return False
    if target_name == "kobra_screen":
        if base0 == "kobra_screen":
            return True
        if base0.startswith("python"):
            for i in range(1, len(args)):
                if os.path.basename(args[i]) == "kobra_screen":
                    return True
                if args[i - 1] == "-m" and args[i] == "kobra_screen":
                    return True
        return False
    return comm == target_name or base0 == target_name


def _find_pids_multi(target_names, proc_root="/proc", exclude_pid=None):
    """Scan procfs once and return {target_name: [pid, ...]} for all requested targets."""
    results = {name: [] for name in target_names}
    if not target_names or not os.path.isdir(proc_root):
        return results
    if exclude_pid is None:
        exclude_pid = os.getpid()
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return results
    known_only = all(t in ("K3SysUi", "rinkhals-ui.py", "kobra_screen") for t in target_names)
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == exclude_pid:
            continue
        proc_dir = os.path.join(proc_root, entry)
        comm = ""
        try:
            with open(os.path.join(proc_dir, "comm"), "r", encoding="utf-8", errors="ignore") as f:
                comm = f.read().strip()
        except OSError:
            pass
        if known_only and comm and comm != "K3SysUi" and not comm.startswith("python") and comm not in target_names:
            continue
        args = []
        try:
            with open(os.path.join(proc_dir, "cmdline"), "rb") as f:
                raw = f.read()
            args = [part.decode("utf-8", "ignore") for part in raw.split(b"\x00") if part]
        except OSError:
            pass
        for name in target_names:
            if _matches_process_name(name, comm, args):
                results[name].append(pid)
    return results


def _find_pids(target_name, proc_root="/proc", exclude_pid=None):
    """Return PIDs whose executable or script matches `target_name` exactly."""
    return _find_pids_multi((target_name,), proc_root=proc_root, exclude_pid=exclude_pid)[target_name]


def _pid_state(pid, proc_root="/proc"):
    """Return single-character Linux process state (e.g. 'T', 'S', 'R') or '' if unavailable."""
    proc_dir = os.path.join(proc_root, str(pid))
    try:
        with open(os.path.join(proc_dir, "stat"), "r", encoding="utf-8", errors="ignore") as f:
            stat_text = f.read().strip()
        rparen = stat_text.rfind(")")
        if rparen != -1:
            after = stat_text[rparen + 1:].lstrip()
            if after:
                return after[0]
    except OSError:
        pass
    try:
        with open(os.path.join(proc_dir, "status"), "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("State:"):
                    parts = line.split(":", 1)[1].strip()
                    if parts:
                        return parts[0]
    except OSError:
        pass
    return ""


def _signal_pids(pids, sig):
    for pid in pids:
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def read_max_backlight(backlight_path=BACKLIGHT_PATH):
    """Read sibling max_brightness sysfs file if present, defaulting to 255."""
    if not backlight_path:
        return 255
    max_path = os.path.join(os.path.dirname(backlight_path), "max_brightness")
    try:
        with open(max_path, "r") as f:
            val = int(f.read().strip())
        if val > 0:
            return val
    except (OSError, ValueError):
        pass
    return 255


def set_backlight_percent(percent, backlight_path=BACKLIGHT_PATH):
    """Write scaled brightness (0..max_brightness) to sysfs backlight; returns True on success."""
    if not backlight_path or not os.path.exists(backlight_path):
        return False
    pct = max(0, min(100, int(round(percent))))
    if pct <= 0:
        target = 0
    else:
        max_b = read_max_backlight(backlight_path)
        target = max(1, min(max_b, int(round(pct * max_b / 100.0))))
    try:
        with open(backlight_path, "w") as f:
            f.write("%d\n" % target)
        return True
    except OSError:
        return False


def _wake_backlight(backlight_path=BACKLIGHT_PATH, target_brightness=None):
    """Wake the LCD backlight if it was turned off (<= 0), matching Rinkhals common.py:720-724."""
    if not backlight_path or not os.path.exists(backlight_path):
        return False
    try:
        with open(backlight_path, "r") as f:
            brightness = int(f.read().strip() or "255")
        if brightness <= 0:
            val = read_max_backlight(backlight_path) if target_brightness is None else max(1, int(target_brightness))
            with open(backlight_path, "w") as f:
                f.write("%d\n" % val)
            return True
    except (OSError, ValueError):
        pass
    return False


def _is_valid_png(path):
    if not path or not os.path.exists(path):
        return False
    try:
        with open(path, "rb") as f:
            return f.read(8) == PNG_SIGNATURE
    except OSError:
        return False


def parse_raw_evdev_packet(data):
    """Unpack one Linux input_event (16B on 32-bit ARM, 24B on 64-bit) into (sec, usec, type, code, value)."""
    if len(data) == 16:
        return struct.unpack("<iiHHi", data)
    if len(data) == 24:
        return struct.unpack("<qqHHi", data)
    raise ValueError("Unsupported evdev packet size: %d" % len(data))


def _evdev_packet_size():
    return struct.calcsize("llHHi")


def _install_signal_stop(stop_fn):
    """Call stop_fn(signum) on SIGTERM / SIGINT / SIGHUP."""
    def _handle_signal(signum, frame):
        stop_fn(signum)

    sigs = [signal.SIGTERM, signal.SIGINT]
    if hasattr(signal, "SIGHUP"):
        sigs.append(signal.SIGHUP)
    for sig in sigs:
        try:
            signal.signal(sig, _handle_signal)
        except (OSError, ValueError):
            pass