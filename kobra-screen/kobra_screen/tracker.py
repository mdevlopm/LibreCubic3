"""Persistent event log, maintenance reminders, filament spool tracking, and version helpers."""

import json
import math
import os
import re

from . import i18n, touch_cal

MAX_EVENT_LOG = 100

FILAMENT_DIAMETER_MM = 1.75
FILAMENT_AREA_CM2 = math.pi * ((FILAMENT_DIAMETER_MM / 2.0 / 10.0) ** 2)
MATERIAL_DENSITIES = {
    "PLA": 1.24,
    "PETG": 1.27,
    "ABS": 1.04,
    "TPU": 1.21,
    "PA": 1.14,
}
SPOOL_MATERIALS = ("PLA", "PETG", "ABS", "TPU", "PA")
DEFAULT_LOW_FILAMENT_G = 100.0

# Default maintenance items (reasonable starting estimates; user-editable)
DEFAULT_MAINTENANCE_ITEMS = (
    {"id": "nozzle", "label": "Nozul kontrolü", "metric": "hours", "threshold": 100.0},
    {"id": "belts", "label": "Kayış gerginliği", "metric": "hours", "threshold": 150.0},
    {"id": "lubrication", "label": "Eksen yağlama", "metric": "hours", "threshold": 200.0},
    {"id": "ace_filter", "label": "ACE kurutucu filtresi", "metric": "prints", "threshold": 50.0},
)


def material_density(material):
    key = str(material or "PLA").strip().upper()
    return MATERIAL_DENSITIES.get(key, MATERIAL_DENSITIES["PLA"])


def mm_to_meters(mm):
    return max(0.0, float(mm or 0.0)) / 1000.0


def meters_to_mm(meters):
    return max(0.0, float(meters or 0.0)) * 1000.0


def mm_to_grams(mm, material="PLA"):
    length_cm = max(0.0, float(mm or 0.0)) / 10.0
    return length_cm * FILAMENT_AREA_CM2 * material_density(material)


def grams_to_mm(grams, material="PLA"):
    density = material_density(material)
    length_cm = max(0.0, float(grams or 0.0)) / (FILAMENT_AREA_CM2 * density)
    return length_cm * 10.0


def resolve_events_path(env=None, rinkhals_home=None, app_name=None, config_path=None):
    cfg_path = i18n.resolve_persistent_config_path(
        env=env, rinkhals_home=rinkhals_home, app_name=app_name, config_path=config_path
    )
    cfg_dir = os.path.dirname(cfg_path)
    base = os.path.basename(cfg_path)
    if base.endswith(".config"):
        stem = base[:-7]
    else:
        stem, _ = os.path.splitext(base)
        stem = stem or base
    return os.path.join(cfg_dir, stem + ".events.json")


def load_event_log_state(path):
    """Load `(events_list, gcode_watermark_ts)` from persistent event-log JSON."""
    if not path or not os.path.isfile(path):
        return [], 0.0
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
        watermark = 0.0
        has_explicit_watermark = False
        raw_items = data
        if isinstance(data, dict):
            has_explicit_watermark = "watermark" in data
            watermark = float(data.get("watermark") or 0.0)
            raw_items = data.get("events")
        if isinstance(raw_items, list):
            cleaned = []
            for item in raw_items[-MAX_EVENT_LOG:]:
                if isinstance(item, dict) and item.get("message"):
                    ts = float(item.get("ts") or 0.0)
                    src = str(item.get("source") or "app")
                    sev = str(item.get("severity") or "info")
                    if not has_explicit_watermark and src == "gcode" and sev == "error" and ts > watermark:
                        watermark = ts
                    cleaned.append({
                        "ts": ts,
                        "severity": sev,
                        "source": src,
                        "message": str(item["message"]),
                    })
            return cleaned, watermark
    except (OSError, ValueError, TypeError):
        pass
    return [], 0.0


def load_event_log(path):
    events, _ = load_event_log_state(path)
    return events


def save_event_log(path, events, watermark=None):
    if not path:
        return False
    items = list(events[-MAX_EVENT_LOG:])
    payload = items if watermark is None else {"watermark": float(watermark), "events": items}
    try:
        with touch_cal.CONFIG_LOCK:
            touch_cal._atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def default_maintenance_state():
    return {
        item["id"]: {
            "id": item["id"],
            "label": item["label"],
            "metric": item["metric"],
            "threshold": float(item["threshold"]),
            "last_done": 0.0,
        }
        for item in DEFAULT_MAINTENANCE_ITEMS
    }


def load_maintenance_state(raw_data):
    state = default_maintenance_state()
    if not isinstance(raw_data, dict):
        return state
    for item_id, entry in state.items():
        saved = raw_data.get(item_id)
        if not isinstance(saved, dict):
            continue
        if "threshold" in saved:
            try:
                entry["threshold"] = max(0.0, float(saved["threshold"]))
            except (TypeError, ValueError):
                pass
        if "last_done" in saved:
            try:
                entry["last_done"] = max(0.0, float(saved["last_done"]))
            except (TypeError, ValueError):
                pass
    return state


def maintenance_progress(item, total_print_time_sec, total_jobs):
    metric = item.get("metric", "hours")
    current = (max(0.0, float(total_print_time_sec or 0.0)) / 3600.0) if metric == "hours" else float(max(0, int(total_jobs or 0)))
    last_done = float(item.get("last_done") or 0.0)
    if current < last_done:
        # History counter reset or switched printer
        elapsed = current
    else:
        elapsed = current - last_done
    threshold = float(item.get("threshold") or 0.0)
    due = threshold > 0.0 and elapsed >= threshold
    return current, elapsed, threshold, due


def default_spools_state():
    keys = ("0", "1", "2", "3", "external")
    return {
        k: {
            "configured": False,
            "material": "PLA",
            "initial_mm": 0.0,
            "remaining_mm": 0.0,
            "unit": "g",
        }
        for k in keys
    }


def load_spools_state(raw_data):
    spools = default_spools_state()
    if not isinstance(raw_data, dict):
        return spools
    for key, entry in spools.items():
        saved = raw_data.get(key)
        if not isinstance(saved, dict):
            continue
        entry["configured"] = bool(saved.get("configured", False))
        mat = str(saved.get("material") or "PLA").strip().upper()
        entry["material"] = mat if mat in MATERIAL_DENSITIES else "PLA"
        try:
            entry["initial_mm"] = max(0.0, float(saved.get("initial_mm") or 0.0))
        except (TypeError, ValueError):
            pass
        try:
            entry["remaining_mm"] = max(0.0, float(saved.get("remaining_mm") or 0.0))
        except (TypeError, ValueError):
            pass
        unit = str(saved.get("unit") or "g").lower()
        entry["unit"] = unit if unit in ("g", "m") else "g"
    return spools


def read_open_kobra_version(rinkhals_home=None):
    home = rinkhals_home or os.environ.get("RINKHALS_HOME") or "/useremain/home/rinkhals"
    path = os.path.join(home, ".open-kobra-version")
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                val = f.read().strip()
            if val:
                return val, path
    except OSError:
        pass
    return "", path


def read_ace_driver_version(rinkhals_home=None):
    home = rinkhals_home or os.environ.get("RINKHALS_HOME") or "/useremain/home/rinkhals"
    primary_path = os.path.join(home, "open-kobra", "ace-driver.version")
    # Keep home/ace-driver.version fallback for existing test_updates_ace_version compatibility.
    for path in (primary_path, os.path.join(home, "ace-driver.version")):
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    val = f.read().strip()
                if val:
                    return val, path
        except OSError:
            pass
    return None, primary_path


def read_app_version(app_root=None):
    root = app_root or os.environ.get("KOBRA_APP_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "app.json")
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
            ver = str(data.get("version") or "").strip()
            if ver:
                return ver
    except (OSError, ValueError, TypeError):
        pass
    return ""


_ACE_TOOL_CMD_RE = re.compile(
    r"(?:^|\b)(?:ACE_CHANGE_TOOL|_ACE_PRE_TOOLCHANGE|_ACE_POST_TOOLCHANGE)\b.*?\b(?:TOOL|T|INDEX)\s*=\s*(-?\d+)",
    re.IGNORECASE,
)
_STANDALONE_T_RE = re.compile(r"^(?:(?://|echo:)\s*)?T([0-3])(?:\s*;.*|\s*)$", re.IGNORECASE)
_ACE_TOOL_RESP_RE = re.compile(
    r"^//\s*ACE\[\d+\]:\s*.*?\b(?:toolchange|tool\s+change|changed\s+tool|active\s+tool)\b"
    r"(?:.*(?:->|\bto\b|=)|\s*:?)\s*(-?\d+)\b",
    re.IGNORECASE,
)


def parse_toolchange_gcode(line):
    """Extract target tool index (0..3, or -1 for unload) from a G-code or ACE response line."""
    text = str(line or "").strip()
    if not text:
        return None
    m = _STANDALONE_T_RE.match(text)
    if m:
        return int(m.group(1))
    m = _ACE_TOOL_CMD_RE.search(text) or _ACE_TOOL_RESP_RE.match(text)
    if m:
        idx = int(m.group(1))
        if idx == -1 or 0 <= idx <= 3:
            return idx
    return None


class PrintStatsTracker:
    """Track filament_used across tool changes during a print and split usage per spool."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.active = False
        self.filename = ""
        self.active_tool = None
        self.last_known_tool = None
        self._last_status_tool = None
        self.ace_present = False
        self.total_filament_used = 0.0
        self.tool_start_filament = 0.0
        self.usage_by_spool = {}
        self.session_start_wall = 0.0
        self._seen_update = False

    @staticmethod
    def _normalize_tool(tool, ace_present):
        if ace_present and isinstance(tool, int) and not isinstance(tool, bool) and 0 <= tool <= 3:
            return tool
        return None

    def _flush_active_delta(self, fallback_external=False):
        """Record delta since tool_start_filament for the current active (or last known) tool."""
        delta = max(0.0, self.total_filament_used - self.tool_start_filament)
        self.tool_start_filament = self.total_filament_used
        if delta <= 0.0:
            return None
        tool_idx = self.active_tool
        if tool_idx is None and fallback_external:
            tool_idx = self.last_known_tool
        if tool_idx is not None:
            key = str(tool_idx)
        elif fallback_external:
            key = "external"
        else:
            return None
        self.usage_by_spool[key] = self.usage_by_spool.get(key, 0.0) + delta
        return (key, delta)

    def update(self, print_state, filename="", filament_used=0.0, current_tool=-1, ace_present=False, wall_now=0.0):
        """Update tracker from polled print_stats/ace_state; return list of (spool_key, delta_mm)."""
        first_poll = not self._seen_update
        self._seen_update = True
        state = str(print_state or "standby")
        fname = str(filename or "")
        fil = max(0.0, float(filament_used or 0.0))
        norm_tool = self._normalize_tool(current_tool, ace_present)
        deductions = []

        if state in ("printing", "paused"):
            if not self.active or self.filename != fname:
                if self.active:
                    deductions.extend(self.finish(filament_used=self.total_filament_used))
                self.active = True
                self.filename = fname
                self.active_tool = norm_tool
                self.last_known_tool = norm_tool
                self._last_status_tool = norm_tool
                self.ace_present = bool(ace_present)
                self.total_filament_used = fil
                # Mid-print startup must not attribute filament already extruded prior to tracker start.
                self.tool_start_filament = fil if first_poll else 0.0
                self.usage_by_spool = {}
                self.session_start_wall = float(wall_now or 0.0)
                return deductions

            self.ace_present = bool(ace_present)
            if fil > self.total_filament_used:
                self.total_filament_used = fil

            if norm_tool != self._last_status_tool:
                self._last_status_tool = norm_tool
                if norm_tool is None:
                    if self.active_tool is not None:
                        flushed = self._flush_active_delta(fallback_external=False)
                        if flushed is not None:
                            deductions.append(flushed)
                        self.active_tool = None
                else:
                    if self.active_tool is not None and norm_tool != self.active_tool:
                        flushed = self._flush_active_delta(fallback_external=False)
                        if flushed is not None:
                            deductions.append(flushed)
                    self.active_tool = norm_tool
                    self.last_known_tool = norm_tool
            return deductions

        if self.active:
            if norm_tool is not None and self.active_tool is None and self.last_known_tool is None:
                self.active_tool = norm_tool
                self.last_known_tool = norm_tool
            deductions.extend(self.finish(filament_used=fil))
        return deductions

    def handle_gcode_response(self, line, filament_used=None):
        """Process a G-code command or response line for tool changes; return [(spool_key, delta_mm)]."""
        if not self.active:
            return []
        if filament_used is not None:
            fil = max(0.0, float(filament_used))
            if fil > self.total_filament_used:
                self.total_filament_used = fil
        deductions = []
        for raw_line in str(line or "").splitlines():
            target = parse_toolchange_gcode(raw_line)
            if target is None:
                continue
            if target == -1:
                if self.active_tool is not None:
                    flushed = self._flush_active_delta(fallback_external=False)
                    if flushed is not None:
                        deductions.append(flushed)
                    self.active_tool = None
            else:
                if self.active_tool is not None and target != self.active_tool:
                    flushed = self._flush_active_delta(fallback_external=False)
                    if flushed is not None:
                        deductions.append(flushed)
                self.active_tool = target
                self.last_known_tool = target
        return deductions

    def finish(self, filament_used=None):
        """Finalize the current print session and return remaining [(spool_key, delta_mm)]."""
        if not self.active:
            return []
        if filament_used is not None:
            fil = max(0.0, float(filament_used))
            if fil > self.total_filament_used:
                self.total_filament_used = fil
        deductions = []
        flushed = self._flush_active_delta(fallback_external=True)
        if flushed is not None:
            deductions.append(flushed)
        self.active = False
        self.active_tool = None
        self._last_status_tool = None
        return deductions
