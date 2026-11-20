"""Application state, navigation and the frame every screen sits in."""

from collections import deque
import os
import sys
import tempfile
import threading
import math
import time

from . import bed_mesh, beeper, i18n, limits, prompts, screens, sysinfo, touch_cal, tracker, wizards
from .app_ace import _AppAceMixin
from .app_models import (
    ACE_FEED_STEPS, ACE_INFO_DEDUPE_SEC, ACE_RESULT_WAIT_SEC, ACE_STOP_BACKUP_SEC, ACE_STOP_COMMANDS,
    Confirm, EXTRUDE_STEPS, GCODE_STORE_COUNT, GCODE_STORE_POLL_SEC, GOTO_CHECK_SEC, HEATING_COMMANDS,
    Keyboard, Keypad, MAX_CACHED_META, MAX_CACHED_THUMBNAILS, META_RETRY_SEC, PNG_SIGNATURE,
    POLL_OBJECTS, State, THUMBNAIL_SIZE, TouchCalibration, disable_rinkhals_app,
)
from .app_polling import _AppPollingMixin
from .app_settings import _AppSettingsMixin
from .app_spools import _AppSpoolsMixin
from .app_system import _AppSystemMixin
from .app_touch_cal import _AppTouchCalMixin
from .gcode_safety import _AppGcodeMixin
from .prompts import PromptButton, PromptDialog
from .moonraker import MoonrakerError
from .network import NetworkError, NullWifiManager, WifiStatus
from .widgets import (GAP, HEIGHT, MARGIN, TABS_H, TITLE_H, WIDTH, Button, Crosshair, Label, Panel,
                      Placed, Rule, place)

GOTO_FILE = os.environ.get("KOBRA_GOTO_FILE", "/tmp/kobra-screen.goto")  # debug hook, see App.check_goto_file


class App(_AppSettingsMixin, _AppSpoolsMixin, _AppPollingMixin, _AppAceMixin,
          _AppSystemMixin, _AppGcodeMixin, _AppTouchCalMixin):
    # label, key, icon
    TABS = (("Ana", "home", "home"), ("Kontrol", "control", "tune"), ("Makara", "filament", "spool"),
            ("Araçlar", "tools", "build"), ("Ayar", "settings", "settings"))

    def __init__(self, client, run_async=None, cache_dir=None, max_thumbnails=MAX_CACHED_THUMBNAILS,
                 on_return_to_stock=None, rinkhals_home=None, app_root=None, app_name=None,
                 rollback_timeout=30.0, wifi=None, reboot_fn=None, touch_backend=None, clock_fn=None,
                 lang=None, config_path=None, display_backend=None, beeper_backend=None,
                 wall_clock_fn=None, run_urgent=None, config=None):
        self.client = client
        # run_async(fn) runs slow work off the UI thread; default: run inline
        self.run_async = run_async or (lambda fn: fn())
        if run_urgent is not None:
            self.run_urgent = run_urgent
        elif run_async is not None:
            self.run_urgent = lambda fn: threading.Thread(target=fn, name="kobra-urgent", daemon=True).start()
        else:
            self.run_urgent = lambda fn: fn()
        self.cache_dir = cache_dir or os.path.join(tempfile.gettempdir(), "kobra-screen")
        self.max_thumbnails = max_thumbnails
        self._on_return_to_stock = on_return_to_stock
        self._reboot_fn = reboot_fn
        self._touch_backend = touch_backend
        self._display_backend = display_backend
        self._clock = clock_fn or time.monotonic
        self._wall_clock = wall_clock_fn or time.time
        self.beeper = beeper_backend or beeper.Beeper()
        self.wifi = wifi or NullWifiManager()
        self.rinkhals_home = rinkhals_home
        self.app_root = app_root or os.environ.get("KOBRA_APP_ROOT")
        self.app_name = app_name or os.environ.get("KOBRA_APP_NAME", "kobra-screen")
        self.config_path = config_path
        if config is None:
            config = limits.load_config(
                rinkhals_home=self.rinkhals_home,
                app_name=self.app_name,
                config_path=self.config_path,
            )
        self.extrusion_lock = self._parse_cfg_bool(config.get('extrusion_lock', False), False)
        if lang is not None:
            self.lang = i18n.normalize_lang(lang)
        else:
            self.lang = i18n.load_language(
                rinkhals_home=self.rinkhals_home,
                app_name=self.app_name,
                config_path=self.config_path,
            )
        self.rollback_timeout = float(rollback_timeout)
        self.stock_requested = False
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._secondary_lock = threading.Lock()
        self._poll_wakeup = threading.Event()
        self._meta_inflight = set()
        self._meta_failed = {}  # filename -> clock time of the last failed metadata request
        self.state = State()
        self.tab = "home"
        self.pages = []  # stack of (page name, argument) above the current tab
        self.modal = None
        self.prompt_dialog = PromptDialog()
        self._last_prompt_history = []
        self._prompt_initialized = False
        self._dismissed_prompt_ts = None
        self._dismissed_prompt_keys = set()
        self.wizard = None
        self.touch_cal = None
        self.message = ""
        self.message_kind = "danger"
        self.message_until = 0.0
        self.busy = 0
        self.control_seg = "heat"
        self.ace_seg = "status"
        self.bed_mesh_rot = 0.0
        self.bed_mesh_tilt = bed_mesh.DEFAULT_TILT
        self.bed_mesh_z_scale = 100
        self.bed_mesh_mutable_raw = None
        self._bed_mesh_mutable_loaded = False
        self.file_page = 0
        self.file_sort = "newest"
        self.history_jobs = []
        self.history_totals = {}
        self.history_page = 0
        self.history_error = ""
        self.events_page = 0
        self.objects_page = 0
        self.limit_page = 0
        self.wifi_page = 0
        self.wifi_status = WifiStatus(state="UNAVAILABLE")
        self.wifi_networks = []
        self.wifi_scanning = False
        self.jog_step = 10
        self.extrude_step = 10
        self.ace_feed_step = 50
        self.ace_stopping = None
        self._fast_poll_until = 0.0
        self._dryer_stopped_ts = None  # timestamp when dryer stopped, for cooling message
        self._prev_dryer_status = None
        self.heating_lock = False
        self.heating_confirm_skip = False  # persistent "don't ask again" for the heating confirmation
        self.meta = {}  # filename -> metadata (with "thumbnail" path once downloaded)
        self.limits_overrides = {}
        self.limits_file_exists = False
        self.limits_dirty = False
        self._pending_rollback_prev = None
        self._limits_rollback = None
        self._rolling_back = False
        self.content_bottom = 0
        self.dirty = True
        self.display_brightness = 100
        self.print_bed_mesh = True  # print start options (G9111 bed_mesh / home_z), reset after each start
        self.print_home_z = True
        self.print_tool_overrides = {}  # file tool -> ACE tool picked by hand on the print screen
        self.print_tool_overrides_path = None  # the file those choices belong to
        self.display_sleep_sec = 0
        self.display_sleep_on_print = True
        self.display_asleep = False
        self._last_activity = self._clock()
        self.beep_enabled = True
        self.beep_on_complete = True
        self.beep_on_error = True
        self.beep_on_confirm = True
        self.maintenance_items = tracker.default_maintenance_state()
        self.spools = tracker.default_spools_state()
        self.filament_low_threshold_g = tracker.DEFAULT_LOW_FILAMENT_G
        self.ace_dryer_cooldown_s = None  # None = auto from ACE firmware (see dryer_cooldown_s)
        self._dryer_countdown_shown = None
        self.events_path = tracker.resolve_events_path(
            rinkhals_home=self.rinkhals_home, app_name=self.app_name, config_path=self.config_path
        )
        self.event_log, self._gcode_watermark = tracker.load_event_log_state(self.events_path)
        self._events_io_lock = threading.Lock()
        self._seen_gcode_errors = {}  # (ts, text) -> ts of gcode_store lines already handled
        self._seen_toolchange_gcodes = {}  # (ts, text) -> ts of toolchange gcode_store lines handled
        self._ace_info_logged = {}  # text -> wall time it was last put in the event log (duplicate filter)
        self._app_start_wall = self._wall_clock()
        self._last_goto_check = 0.0
        self.ace_speeds = {}  # feed / retract speed set from the screen (the driver status does not carry it)
        self._ace_cmd_counter = 0
        self.ace_pending = deque()  # ERR-17: queue of ACE commands waiting for responses
        self._ace_pending_queue = self.ace_pending
        self._ace_waiting_for_response = False  # ERR-17: track if we're waiting for an ACE response
        self._prev_connected = None
        self._prev_klippy = None
        self._prev_print_state = None
        self._prev_print_error = ""
        self._print_session = None
        self.print_tracker = tracker.PrintStatsTracker()
        self._last_status_tool_wall = 0.0
        self._poll_counter = 0
        self._last_totals_poll = 0.0
        self._temp_store_supported = True
        self._temp_update_interval = 5.0
        self._last_temp_store_poll = 0.0
        self.sysinfo = None  # Tools → System status sample (kobra_screen.sysinfo)
        self._sysinfo_sampler = sysinfo.SysInfoSampler(clock=self._clock)
        self._last_sysinfo_poll = 0.0
        self._gcode_store_interval = GCODE_STORE_POLL_SEC
        self._last_gcode_store_poll = -1e9
        self._last_temp_store_data = None
        self._visual_snapshot_taken = False
        self._last_visual_snapshot = None
        self.temp_history = {
            "extruder": {"temps": deque(maxlen=300), "targets": deque(maxlen=300)},
            "heater_bed": {"temps": deque(maxlen=300), "targets": deque(maxlen=300)},
        }
        self.update_status_data = None
        self.update_checked = False
        self.update_error = ""
        self.open_kobra_version, self.open_kobra_version_path = tracker.read_open_kobra_version(self.rinkhals_home)
        self.ace_driver_version = self.get_ace_driver_version(self.rinkhals_home)
        self.app_version = tracker.read_app_version(self.app_root)
        self._load_persistent_settings()
        if self._display_backend is not None and hasattr(self._display_backend, "set_brightness"):
            self._display_backend.set_brightness(self.display_brightness)

    def dryer_cooldown_s(self):
        """Seconds the ACE fan keeps cooling the heater plate after drying stops.

        Stock ACE firmware V1.3.863 runs it for ~180 s; our patched V1.3.86K for 60 s.
        A configured ace_dryer_cooldown_s overrides the detection.
        """
        if self.ace_dryer_cooldown_s:
            return self.ace_dryer_cooldown_s
        ace = (self.state.status or {}).get("ace_instance_0") or {}
        return 60 if str(ace.get("firmware") or "").endswith("86K") else 180

    def tick_timers(self):
        """Expire time-based UI elements (toast banners) so idle screens stay up to date."""
        changed = self._finish_ace_pending_timeout() if self.ace_pending else False
        if self.check_goto_file():
            changed = True
        with self._lock:
            if self._dryer_stopped_ts is not None:
                left = self.dryer_cooldown_s() - (self._clock() - self._dryer_stopped_ts)
                if left <= 0:
                    self._dryer_stopped_ts = None
                    self._dryer_countdown_shown = None
                    self.dirty = True
                    changed = True
                elif math.ceil(left) != self._dryer_countdown_shown:
                    # redraw once per second so the countdown on the dryer card moves
                    self._dryer_countdown_shown = math.ceil(left)
                    self.dirty = True
                    changed = True
            if self.message and time.monotonic() >= self.message_until:
                self.message = ""
                self.message_until = 0.0
                self.dirty = True
                return True
        return changed

    def check_goto_file(self, path=None):
        """Debug hook: if /tmp/kobra-screen.goto exists, read a screen name from it, go there and delete the file.

        `tab:tools`, `page:bed_mesh`, `page:system`, `page:slot:0`, `home` or `back`.  It only navigates: no
        G-code, no ACE command, no Wi-Fi scan, no wizard and no dialog is started.  Used to take a
        screenshot of every screen on the printer (docs/32-ui-tur5.md).
        """
        now = self._clock()
        if now - self._last_goto_check < GOTO_CHECK_SEC:
            return False
        self._last_goto_check = now
        path = path or GOTO_FILE
        try:
            with open(path, "r", encoding="utf-8") as f:
                spec = f.read(200).strip()
        except OSError:
            return False
        try:
            os.remove(path)  # first, so a bad name or a crash never repeats
        except OSError:
            pass
        return self.debug_goto(spec)

    def debug_goto(self, spec):
        kind, _, rest = str(spec or "").strip().partition(":")
        kind = kind.strip().lower()
        rest = rest.strip()
        if kind == "tab" and rest in dict((tab, name) for name, tab, _ in self.TABS):
            self.close_modal()
            self.select_tab(rest)
            return True
        if kind == "page":
            name, _, arg = rest.partition(":")
            name = name.strip()
            if name not in screens.PAGES or name == "wizard":  # a wizard would start sending G-code
                sys.stderr.write("kobra-screen: goto: unknown or refused page %r\n" % name)
                return False
            value = arg.strip() or None
            if value is not None and value.lstrip("-").isdigit():
                value = int(value)
            self.close_modal()
            with self._lock:
                self.pages = []
            self.open(name, value, passive=True)
            return True
        if kind in ("home", "back") and not rest:
            self.close_modal()
            if kind == "home":
                self.select_tab("home")
            else:
                self.back()
            return True
        if kind == "modal":
            # modal:estop, modal:confirm, modal:numpad, modal:keyboard
            # These only open the modal window; buttons inside must never send G-code.
            self.close_modal()
            modal_type = rest.strip().lower()
            if modal_type == "estop":
                self.confirm(
                    self.tr("Acil durdurma yapılsın mı?\n\nYazıcı hemen durur, ısıtıcılar kapanır."),
                    lambda: None,  # no-op: Evet butonu-asla emergency_stop gönderilmez
                    kind="danger",
                    yes_label="Evet",
                    no_label="Vazgeç",
                    is_estop=False,  # <-- critical: do NOT set is_estop=True so emergency_stop is NOT sent
                )
            elif modal_type == "confirm":
                self.confirm(
                    self.tr("Onaylamak istediğinizde yapılacak işlem iptal edilmiştir."),
                    lambda: None,  # no-op: 'Evet' butonu herhangi bir acción yapmaz
                    kind="primary",
                    yes_label="Evet",
                    no_label="Vazgeç",
                )
            elif modal_type == "numpad":
                self.keypad("Sayısal Giriş", 0, 9999, lambda: None)  # open keypad; no submission
            elif modal_type == "keyboard":
                self.keyboard("Metin Giriş", lambda: None)  # open keyboard; no submission
            return True
        if kind == "touch":
            # touch:<x>,<y> - tap at coordinates without triggering button actions
            try:
                x_str, y_str = rest.split(",", 1)
                x, y = int(x_str), int(y_str)
            except (ValueError, IndexError):
                sys.stderr.write("kobra-screen: goto: invalid touch spec %r\n" % rest)
                return False
            self.tap_at(x, y)  # new method: tap without triggering widget actions
            return True
        sys.stderr.write("kobra-screen: goto: cannot understand %r\n" % spec)
        return False

    def tap_at(self, x, y):
        """Tap at (x, y) but DO NOT call widget.action() if one exists.
      
        This is used by the 'touch:' goto format so that movement/heating/pressure
        buttons are never triggered during a debug goto touch action.
        """
        if self.display_asleep:
            self.wake_display()
            return True
        self.note_activity()
        with self._lock:
            items = list(reversed(self.layout()))
        for item in items:
            if isinstance(item.widget, (Button, Panel)) and item.x <= x < item.x + item.w and item.y <= y < item.y + item.h:
                # NOTE: we deliberately do NOT call item.widget.action()
                # so no G-code is sent from a touch: goto
                return True
        return False

    # ---- events, sound, maintenance, updates, spools, temp history ----

    def notify(self, text, seconds=6, kind="outline_danger", record=True):
        with self._lock:
            self.message = text
            self.message_kind = kind
            self.message_until = time.monotonic() + seconds
            self.dirty = True
        if text and record:
            sev = "error" if kind in ("danger", "outline_danger") else ("warn" if kind == "warn" else ("ok" if kind in ("active", "primary", "ok") else "info"))
            self.log_event(text, severity=sev, source="notify")

    # ---- shortcuts into the polled status ----

    def get(self, obj, key, default=0):
        with self._lock:
            value = (self.state.status.get(obj) or {}).get(key, default)
            return default if value is None else value

    def setting(self, section, key, default=0):
        with self._lock:
            return (self.state.settings.get(section) or {}).get(key, default)

    @property
    def print_state(self):
        return self.get("print_stats", "state", "standby")

    @property
    def printing(self):
        return self.print_state in ("printing", "paused")

    @property
    def ready(self):
        with self._lock:
            return self.state.connected and self.state.klippy == "ready"

    @property
    def idle(self):
        # a wizard step that is still running G-code (leveling, PID, ...) keeps the printer busy
        return self.ready and not self.printing and not (self.wizard is not None and self.wizard.running)

    def when(self, condition, action):
        return action if condition else None

    # ---- navigation ----

    def select_tab(self, tab):
        with self._lock:
            self.tab = tab
            self.pages = []
            self.dirty = True

    def open(self, page, arg=None, passive=False):
        """Go to a page.  `passive` only reads data (no Wi-Fi scan): used by the debug goto hook."""
        with self._lock:
            self.pages.append((page, arg))
            need_meta = page == "file" and arg not in self.meta and arg not in self._meta_inflight
            self.dirty = True
        if page == "files":
            self.run_async(self.refresh_files)
        elif page == "history":
            self.run_async(self.refresh_history)
        elif page == "maintenance":
            self.run_async(self.refresh_maintenance_totals)
        elif page == "updates":
            self.run_async(self.refresh_updates)
        elif page == "events":
            self.run_async(self._sync_gcode_errors)
        elif need_meta:
            self.run_async(lambda: self.load_metadata(arg))
        elif page == "limits":
            self.run_async(self.refresh_limits)
        elif page == "wifi":
            self.run_async(lambda: self.refresh_wifi(scan=not passive))
        elif page == "heat_chart" and self._temp_store_supported:
            self.run_async(self._sync_temperature_store)
        elif page == "bed_mesh":
            self.run_async(self.refresh_bed_mesh)
        elif page == "sysinfo":
            self.run_async(self.refresh_sysinfo)

    def back(self):
        with self._lock:
            if self.pages:
                self.pages.pop()
            self.dirty = True

    def confirm(self, text, callback, kind="primary", yes_label="Evet", no_label="Vazgeç", is_estop=False,
                option_label="", on_option=None):
        with self._lock:
            self.modal = Confirm(text, callback, kind, yes_label=yes_label, no_label=no_label, is_estop=is_estop,
                                 option_label=option_label, on_option=on_option)
            self.dirty = True

    def toggle_confirm_option(self):
        with self._lock:
            if isinstance(self.modal, Confirm) and self.modal.option_label:
                self.modal.option_state = not self.modal.option_state
                self.dirty = True

    def bed_mesh_drag(self, rot, tilt, final=False):
        """Finger drag on the 3D mesh: keeps the angles; only the final call asks for a redraw."""
        with self._lock:
            self.bed_mesh_rot = float(rot) % 360.0
            self.bed_mesh_tilt = bed_mesh.clamp_tilt(tilt)
            if final:
                self.dirty = True

    def refresh_sysinfo(self):
        """Sample host CPU/memory, process shares and MCU load for the System status page."""
        try:
            proc = self.client.proc_stats()
        except MoonrakerError:
            proc = None
        try:
            objects = self.client.query(dict(sysinfo.QUERY))
        except MoonrakerError:
            objects = None
        info = self._sysinfo_sampler.sample(proc, objects)
        with self._lock:
            self.sysinfo = info
            self._last_sysinfo_poll = self._clock()
            self.dirty = True

    def refresh_bed_mesh(self):
        """Fetch latest Klipper bed_mesh status and stock printer_mutable.cfg."""
        try:
            res = self.client.query({"bed_mesh": None})
            if isinstance(res, dict) and "bed_mesh" in res:
                with self._lock:
                    self.state.status["bed_mesh"] = res.get("bed_mesh")
        except MoonrakerError:
            pass
        try:
            raw = self.client.download_config("printer_mutable.cfg")
            with self._lock:
                self.bed_mesh_mutable_raw = raw
                self._bed_mesh_mutable_loaded = True
                self.dirty = True
        except MoonrakerError:
            with self._lock:
                self._bed_mesh_mutable_loaded = True
                self.dirty = True

    def bed_mesh_data(self):
        with self._lock:
            return bed_mesh.resolve_bed_mesh(
                self.state.status.get("bed_mesh"),
                self.bed_mesh_mutable_raw,
            )

    def keypad(self, title, low, high, callback, digits=3, allow_float=False, above=None, initial=None):
        text = ""
        fresh = False
        if initial is not None:
            try:
                text = screens.format_keypad_number(initial)
                fresh = True
            except (TypeError, ValueError):
                text = ""
        with self._lock:
            self.modal = Keypad(title, low, high, callback, digits, text, allow_float, above, fresh=fresh)
            self.dirty = True

    def keyboard(self, title, callback, secret=True, submit_label="Bağlan"):
        with self._lock:
            self.modal = Keyboard(title, callback, "", bool(secret), "lower", submit_label)
            self.dirty = True

    def close_modal(self):
        with self._lock:
            if isinstance(self.modal, PromptDialog):
                self.modal.visible = False
                self.modal.dismissed = True
                self._record_dismissed_prompt_locked(
                    getattr(self.modal, "begin_ts", 0.0),
                    getattr(self.modal, "title", ""),
                )
            self.modal = None
            self.dirty = True

    def start_wizard(self, key):
        if self.heating_lock and self.wizard_heats(key):
            self.notify(self.tr("Isıtma kilidi açık"))
            return False
        with self._lock:
            self.wizard = wizards.Run(wizards.WIZARDS[key])
            self.pages.append(("wizard", None))
            self.wizard.enter(self)
            self.dirty = True
        return True

    def end_wizard(self, cancelled):
        with self._lock:
            run, self.wizard = self.wizard, None
            self.pages = [page for page in self.pages if page[0] != "wizard"]
            self.dirty = True
        if not cancelled and run and run.wizard.key == "level":
            self.run_async(self.refresh_bed_mesh)  # a new mesh was just measured
        if cancelled and run and run.wizard.cancel:
            self.gcode(run.wizard.cancel)

    # ---- frame ----

    def _title(self):
        if self.modal:
            return None, "Kobra 3"
        if self.touch_cal:
            return None, self.tr("Ekran kalibrasyonu")
        if self.pages:
            page, arg = self.pages[-1]
            if page == "wizard":
                return None, self.tr(self.wizard.wizard.name)
            return self.back, self.tr(screens.PAGE_TITLES[page])
        tab_titles = {tab: self.tr(name) for name, tab, _ in self.TABS}
        return None, "Kobra 3" if self.tab == "home" else tab_titles.get(self.tab, "Kobra 3")

    def _status_chip(self):
        if not self.state.connected:
            return self.tr("Bağlantı yok"), "danger"
        if self.state.klippy != "ready":
            return self.state.klippy, "warn"
        names = {"printing": ("Yazdırılıyor", "ok"), "paused": ("Duraklatıldı", "warn"), "error": ("Hata", "danger"),
                 "complete": ("Tamamlandı", "ok"), "cancelled": ("İptal edildi", "muted")}
        label, color = names.get(self.print_state, ("Hazır", "ok"))
        return self.tr(label), color

    def body(self):
        if self.modal:
            return screens.modal(self)
        if self.touch_cal:
            return screens.touch_calibration_view(self)
        if self.pages:
            page, arg = self.pages[-1]
            return screens.PAGES[page](self, arg)
        if not self.ready:
            return screens.offline(self)
        return screens.TABS[self.tab](self)

    def layout(self):
        """Return every widget of the current view with its position."""
        with self._lock:
            placed = []
            back, title = self._title()
            x = MARGIN
            if back:
                placed.append(Placed(4, 0, 44, TITLE_H - 1, Button("", back, "ghost", icon="back")))
                x = 48
            title_w = WIDTH - 48 - x
            title_txt = title + (" ..." if self.busy else "")
            from .widgets import ellipsize, text_width
            from .theme import FONT_SIZES
            title_sz = "h2" if text_width(title_txt, FONT_SIZES["h2"]) * 1.15 <= title_w else "normal"
            title_txt = ellipsize(title_txt, FONT_SIZES[title_sz], title_w - 2)
            placed.append(Placed(x, 0, title_w, TITLE_H - 1, Label(title_txt, size=title_sz)))
            placed.append(Placed(WIDTH - 46, 0, 44, TITLE_H,
                                 Button("", self.request_emergency_stop, "estop", icon="stop")))
            placed.append(Placed(0, TITLE_H - 1, WIDTH, 1, Rule("divider")))
            show_tabs = not self.modal and not self.wizard and not self.touch_cal
            bottom = HEIGHT - TABS_H if show_tabs else HEIGHT
            self.content_bottom = place(self.body(), MARGIN, TITLE_H + GAP, WIDTH - 2 * MARGIN, placed) - GAP
            self.content_limit = bottom - GAP
            if self.touch_cal and self.touch_cal.stage in ("corners", "verify"):
                if self.touch_cal.stage == "corners":
                    _, tx, ty = self.touch_cal.targets[self.touch_cal.step]
                else:
                    _, tx, ty = self.touch_cal.center_target
                placed.append(Placed(tx - 16, ty - 16, 32, 32, Crosshair("warn")))
            if self.message and time.monotonic() < self.message_until:
                placed.append(Placed(MARGIN, bottom - 52, WIDTH - 2 * MARGIN, 44,
                                     Button(self.message, lambda: self.notify("", 0), self.message_kind)))
            if show_tabs:
                placed.append(Placed(0, HEIGHT - TABS_H, WIDTH, TABS_H, Panel("nav_bg", border=None, radius=0)))
                placed.append(Placed(0, HEIGHT - TABS_H, WIDTH, 1, Rule("divider")))
                tab_w = WIDTH / len(self.TABS)
                for i, (name, tab, icon) in enumerate(self.TABS):
                    left = round(i * tab_w)
                    right = round((i + 1) * tab_w)
                    placed.append(Placed(
                        left, HEIGHT - TABS_H + 1, right - left, TABS_H - 1,
                        Button(self.tr(name), lambda tab=tab: self.select_tab(tab),
                               "tab_active" if tab == self.tab else "tab",
                               icon=icon, vertical=True)))
            return placed

    # ---- input ----

    def tap(self, x, y):
        """Press whatever is at (x, y); returns True when a button or card took it."""
        if self.display_asleep:
            self.wake_display()
            return True
        self.note_activity()
        with self._lock:
            items = list(reversed(self.layout()))
        for item in items:
            if isinstance(item.widget, (Button, Panel)) and item.x <= x < item.x + item.w and item.y <= y < item.y + item.h:
                if item.widget.action:
                    item.widget.action()
                    with self._lock:
                        self.dirty = True
                    return True
                return False
        return False

    def press(self, text, icon=None):
        """Press the first enabled button with this label, and icon if given (tests, scripting)."""
        self.note_activity()
        with self._lock:
            items = list(self.layout())
        for item in items:
            widget = item.widget
            if (isinstance(widget, Button) and widget.text == text and widget.action
                    and (icon is None or widget.icon == icon)):
                item.widget.action()
                with self._lock:
                    self.dirty = True
                return True
        return False
