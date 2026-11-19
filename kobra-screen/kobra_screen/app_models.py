"""Shared constants, dataclasses and Rinkhals helpers for ``kobra_screen.app``."""

import os
from dataclasses import dataclass, field

from . import touch_cal
from .widgets import HEIGHT, WIDTH

POLL_OBJECTS = {
    "print_stats": None, "virtual_sdcard": None, "extruder": None, "heater_bed": None, "toolhead": None,
    "fan": None, "gcode_move": None, "leviq3": None, "exclude_object": None, "bed_mesh": None,
    "ace_instance_0": None, "ace_state": None,
    "output_pin nozzle_light": None, "output_pin logo_light": None,
}
EXTRUDE_STEPS = (1, 5, 10, 50)
ACE_FEED_STEPS = (10, 50, 100, 500)
# Commands / macros that heat the nozzle or bed by themselves (they run M104/M109/M140/M190 inside).
# NOZZLE_WIPE_SEQUENCE is not here: it only moves; the "clean" wizard is covered by its own M104 step.
HEATING_COMMANDS = frozenset({
    "PID_CALIBRATE", "LEVIQ", "LEVIQ_AUTO_ZOFFSET", "LEVIQ_PREHEATING", "LEVIQ_WIPING", "G9111", "LOAD_FILAMENT",
    "_EXTRUDE", "_RETRACT", "_ACE_PREPARE_FOR_RETRACTION", "_ACE_PRE_TOOLCHANGE", "_ACE_POST_TOOLCHANGE", "RESUME",
    "ACE_CHANGE_TOOL", "ACE_FULL_UNLOAD", "ACE_SMART_LOAD", "ACE_SMART_UNLOAD",
})
ACE_STOP_BACKUP_SEC = 30.0   # ace_stopping never lasts longer than this; normally the ACE status ends it
ACE_RESULT_WAIT_SEC = 4.0    # after this the command's own "done" text is shown if the driver said nothing
ACE_INFO_DEDUPE_SEC = 120.0  # identical "// ACE[n]: ..." lines are logged at most once per this time
GCODE_STORE_COUNT = 20
GCODE_STORE_POLL_SEC = 2.0
GOTO_CHECK_SEC = 0.5
META_RETRY_SEC = 30.0  # wait this long before the poll asks again for metadata that failed to load
ACE_STOP_COMMANDS = ("ACE_STOP_DRYING", "ACE_STOP_FEED", "ACE_STOP_RETRACT", "ACE_DISABLE_FEED_ASSIST")
THUMBNAIL_SIZE = 92
MAX_CACHED_THUMBNAILS = 16
MAX_CACHED_META = 64
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def disable_rinkhals_app(app_name="kobra-screen", rinkhals_home=None, app_root=None):
    """Disable auto-start using Rinkhals tools.sh disable_app marker semantics."""
    home = rinkhals_home or os.environ.get("RINKHALS_HOME") or "/useremain/home/rinkhals"
    apps_dir = os.path.join(home, "apps")
    os.makedirs(apps_dir, exist_ok=True)
    user_app_dir = os.path.join(apps_dir, app_name)
    is_user_app = os.path.exists(user_app_dir)
    if not is_user_app and app_root:
        is_user_app = os.path.abspath(app_root) == os.path.abspath(user_app_dir)

    if is_user_app:
        for path in (
            os.path.join(apps_dir, app_name + ".enabled"),
            os.path.join(apps_dir, app_name + ".disabled"),
            os.path.join(user_app_dir, ".enabled"),
        ):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        with open(os.path.join(user_app_dir, ".disabled"), "w", encoding="utf-8"):
            pass
    else:
        try:
            os.remove(os.path.join(apps_dir, app_name + ".enabled"))
        except FileNotFoundError:
            pass
        with open(os.path.join(apps_dir, app_name + ".disabled"), "w", encoding="utf-8"):
            pass

    if hasattr(os, "sync"):
        try:
            os.sync()
        except OSError:
            pass


@dataclass
class Keypad:
    title: str
    low: float
    high: float
    callback: object
    digits: int = 3
    text: str = ""
    allow_float: bool = False
    above: float = None
    fresh: bool = False  # text came from the current value: the first digit replaces it


@dataclass
class Confirm:
    text: str
    callback: object
    kind: str = "primary"
    yes_label: str = "Evet"
    no_label: str = "Vazgeç"
    is_estop: bool = False
    option_label: str = ""  # optional checkbox row ("don't ask again"); on_option(state) is called when accepted
    option_state: bool = False
    on_option: object = None


@dataclass
class Keyboard:
    title: str
    callback: object
    text: str = ""
    secret: bool = True
    layer: str = "lower"
    submit_label: str = "Bağlan"


@dataclass
class TouchCalibration:
    stage: str = "intro"  # intro | corners | verify | confirm
    step: int = 0
    targets: tuple = ()
    center_target: tuple = ("Orta", WIDTH // 2, HEIGHT // 2)
    raw_points: list = field(default_factory=list)
    prev_cal: tuple = touch_cal.TOUCH_CALIBRATION
    prev_swap: bool = False
    new_cal: tuple = touch_cal.TOUCH_CALIBRATION
    new_swap: bool = False
    lv_rot: int = 270
    verify_error_px: object = None
    verify_threshold: float = touch_cal.DEFAULT_VERIFY_THRESHOLD_PX
    deadline: float = 0.0
    last_rem_sec: int = -1
    confirm_timeout: float = touch_cal.CONFIRM_TIMEOUT_SEC
    capture_timeout: float = touch_cal.CAPTURE_TIMEOUT_SEC
    error_msg: str = ""


@dataclass
class State:
    connected: bool = False
    klippy: str = "?"
    klippy_message: str = ""
    version: str = ""
    hostname: str = ""
    status: dict = field(default_factory=dict)
    settings: dict = field(default_factory=dict)  # configfile.settings, read once per Klipper start
    files: list = field(default_factory=list)
