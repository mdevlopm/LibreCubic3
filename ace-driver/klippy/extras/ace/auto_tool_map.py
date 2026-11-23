"""Automatic ACE tool-map selection for external (non-screen) prints such as Upload & Print.

Reuses ``kobra_screen/tool_map.py`` directly (no duplicated matching logic) and runs inside
Klipper right before the first ``T<n>`` of a print so it cannot lose the G-code mutex race
against ``G9111`` or early ``T0`` lines.
"""

import importlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import threading
import time
from urllib import parse, request

_TOOL_MAP_MOD = None
_MISSING_WARNED = False


def _candidate_tool_map_paths():
    paths = []
    here = Path(__file__).resolve()
    if len(here.parents) > 4:
        paths.append(here.parents[4] / "kobra-screen" / "kobra_screen" / "tool_map.py")
    rinkhals_home = os.environ.get("RINKHALS_HOME")
    if rinkhals_home:
        paths.append(Path(rinkhals_home) / "apps" / "kobra-screen" / "kobra_screen" / "tool_map.py")
    paths.append(Path("/useremain/home/rinkhals/apps/kobra-screen/kobra_screen/tool_map.py"))
    paths.append(Path("/useremain/rinkhals/apps/kobra-screen/kobra_screen/tool_map.py"))
    seen = set()
    unique = []
    for p in paths:
        s = str(p)
        if s not in seen:
            seen.add(s)
            unique.append(p)
    return unique


def load_tool_map_module(search_paths=None):
    """Load ``kobra_screen/tool_map.py`` without copying its matching logic."""
    global _TOOL_MAP_MOD, _MISSING_WARNED
    if search_paths is None and _TOOL_MAP_MOD is not None:
        return _TOOL_MAP_MOD

    if search_paths is None:
        try:
            mod = importlib.import_module("kobra_screen.tool_map")
            if hasattr(mod, "auto_plan") and hasattr(mod, "format_summary"):
                _TOOL_MAP_MOD = mod
                return mod
        except Exception:
            pass

    candidates = [Path(p) for p in search_paths] if search_paths is not None else _candidate_tool_map_paths()
    for path in candidates:
        if not path.is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location("_ace_kobra_tool_map", str(path))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "auto_plan") and hasattr(mod, "format_summary"):
                    if search_paths is None:
                        _TOOL_MAP_MOD = mod
                    return mod
        except Exception as exc:
            logging.warning("ACE: Failed to load tool_map from %s: %s", path, exc)

    if search_paths is None and not _MISSING_WARNED:
        _MISSING_WARNED = True
        logging.warning("ACE: kobra_screen/tool_map.py not found; auto tool mapping disabled")
    return None


class _PrefetchEntry:
    def __init__(self, filename):
        self.filename = filename
        self.done = threading.Event()
        self.meta = None


class AutoToolMapper:
    """Resolve and apply an automatic tool map for external prints before the first T<n>."""

    def __init__(self, manager, metadata_fetcher=None, tool_map_loader=None):
        self.manager = manager
        ace_config = getattr(manager, "ace_config", None) or {}
        self.base_url = str(
            ace_config.get("moonraker_lane_sync_url", "http://127.0.0.1:7125")
        ).rstrip("/")
        self.api_key = ace_config.get("moonraker_lane_sync_api_key")
        raw_timeout = float(ace_config.get("moonraker_lane_sync_timeout", 2.0) or 2.0)
        self.timeout_s = max(0.5, min(2.0, raw_timeout))
        self._metadata_fetcher = metadata_fetcher
        self._tool_map_loader = tool_map_loader or load_tool_map_module
        self._lock = threading.Lock()
        self._prefetch = None
        self._checked_filename = None
        self._checked_total_duration = 0.0
        self._meta_misses = 0

    def reset(self):
        """Forget per-print state once a print ends or the tool map is cleared."""
        self._checked_filename = None
        self._checked_total_duration = 0.0
        self._meta_misses = 0
        with self._lock:
            self._prefetch = None

    def on_tool_map_set(self):
        """Record that a map was explicitly set for the currently running print, if any."""
        state, filename, total_duration = self._print_info()
        if state in ("printing", "paused") and filename:
            self._checked_filename = filename
            self._checked_total_duration = total_duration

    def _print_info(self):
        try:
            print_stats = self.manager.printer.lookup_object("print_stats", None)
            if not print_stats:
                return "", "", 0.0
            stats = print_stats.get_status(self.manager.reactor.monotonic()) or {}
            state = str(stats.get("state") or "").lower()
            filename = str(stats.get("filename") or "").strip()
            total_duration = float(stats.get("total_duration") or 0.0)
            return state, filename, total_duration
        except Exception:
            return "", "", 0.0

    def _is_checked_for(self, filename, total_duration):
        if self._checked_filename != filename:
            return False
        # A drop in total_duration means a new print of the same file started before a lifecycle reset.
        return total_duration >= self._checked_total_duration - 1.0

    def _http_get_metadata(self, filename):
        url = f"{self.base_url}/server/files/metadata?filename={parse.quote(filename)}"
        headers = {}
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        req = request.Request(url, headers=headers, method="GET")
        try:
            with request.urlopen(req, timeout=self.timeout_s) as resp:
                body = resp.read()
                if not body:
                    return None
                payload = json.loads(body.decode("utf-8", errors="replace"))
        except Exception as exc:
            logging.debug("ACE: Metadata fetch failed for %s: %s", filename, exc)
            return None
        if not isinstance(payload, dict):
            return None
        result = payload.get("result")
        return result if isinstance(result, dict) else None

    def _start_prefetch(self, filename):
        with self._lock:
            if self._prefetch is not None and self._prefetch.filename == filename:
                return self._prefetch
            entry = _PrefetchEntry(filename)
            self._prefetch = entry

        fetcher = self._metadata_fetcher or self._http_get_metadata

        def _worker():
            try:
                meta = fetcher(filename)
                entry.meta = meta if isinstance(meta, dict) else None
            except Exception as exc:
                logging.debug("ACE: Metadata prefetch error for %s: %s", filename, exc)
                entry.meta = None
            finally:
                entry.done.set()

        thread = threading.Thread(
            target=_worker,
            name="AceAutoToolMapPrefetch",
            daemon=True,
        )
        thread.start()
        return entry

    def on_lifecycle_tick(self):
        """Non-blocking background hook called from the 2 s health timer."""
        state, filename, total_duration = self._print_info()
        if state not in ("printing", "paused"):
            self.reset()
            return
        if not filename or self.manager.get_tool_map():
            return
        if self._is_checked_for(filename, total_duration):
            return
        if self.manager.is_ace_absent() or not self.manager.is_ace_connected():
            return
        self._start_prefetch(filename)

    def _await_metadata(self, filename):
        with self._lock:
            entry = self._prefetch if (self._prefetch is not None and self._prefetch.filename == filename) else None

        if entry is not None and entry.done.is_set():
            return entry.meta

        # Custom fetchers injected by tests can run inline when not already prefetched.
        if entry is None and self._metadata_fetcher is not None:
            done_entry = _PrefetchEntry(filename)
            try:
                meta = self._metadata_fetcher(filename)
                done_entry.meta = meta if isinstance(meta, dict) else None
            except Exception as exc:
                logging.debug("ACE: Metadata fetch error for %s: %s", filename, exc)
                done_entry.meta = None
            done_entry.done.set()
            with self._lock:
                self._prefetch = done_entry
            return done_entry.meta

        if entry is None:
            entry = self._start_prefetch(filename)

        reactor = getattr(self.manager, "reactor", None)
        wait_s = self.timeout_s
        deadline = (reactor.monotonic() + wait_s) if reactor is not None else 0.0
        wall_deadline = time.monotonic() + wait_s
        while not entry.done.is_set():
            now = reactor.monotonic() if reactor is not None else deadline
            if now >= deadline and time.monotonic() >= wall_deadline:
                break
            if reactor is not None and now < deadline:
                reactor.pause(min(deadline, now + 0.05))
            else:
                rem = wall_deadline - time.monotonic()
                if rem <= 0:
                    break
                entry.done.wait(min(0.05, rem))

        return entry.meta if entry.done.is_set() else None

    def _collect_slots(self):
        slots = []
        for inst in getattr(self.manager, "instances", ()):
            serial_mgr = getattr(inst, "serial_mgr", None)
            if serial_mgr is not None and not serial_mgr.is_connected():
                continue
            tool_offset = int(getattr(inst, "tool_offset", 0))
            inventory = getattr(inst, "inventory", ()) or ()
            slot_count = int(getattr(inst, "SLOT_COUNT", len(inventory)))
            for local_slot in range(slot_count):
                inv = inventory[local_slot] if local_slot < len(inventory) else {}
                if not isinstance(inv, dict):
                    continue
                slots.append({
                    "index": local_slot,
                    "tool": tool_offset + local_slot,
                    "status": inv.get("status", "empty"),
                    "material": inv.get("material", ""),
                    "color": inv.get("color"),
                })
        return slots

    def ensure_for_toolchange(self, gcmd=None):
        """Ensure an auto tool map is applied before the first T<n> of an external print.

        Returns the newly applied ``{file_tool: ace_tool}`` dict, or ``None`` when no
        automatic mapping was needed or possible.
        """
        state, filename, total_duration = self._print_info()
        if state not in ("printing", "paused") or not filename:
            return None

        self.manager.update_tool_map_lifecycle(prefetch=False)
        if self.manager.get_tool_map():
            self._checked_filename = filename
            self._checked_total_duration = total_duration
            return None
        if self._is_checked_for(filename, total_duration):
            return None

        self._checked_filename = filename
        self._checked_total_duration = total_duration

        if self.manager.is_ace_absent() or not self.manager.is_ace_connected():
            return None

        tm = self._tool_map_loader()
        if tm is None:
            return None

        slots = self._collect_slots()
        if not slots:
            return None

        meta = self._await_metadata(filename)
        if not meta:
            # Moonraker may still be parsing a just-uploaded file: let the next T try
            # again (at most 3 times, each wait is capped at 2 s)
            self._meta_misses += 1
            if self._meta_misses < 3:
                self._checked_filename = None
            return None
        self._meta_misses = 0

        try:
            current_tool = int(self.manager.state.get("ace_current_index", -1))
        except Exception:
            current_tool = -1

        entries = tm.auto_plan(meta, slots, current_tool=current_tool)
        if not entries:
            return None

        mapping = {int(e["tool"]): int(e["slot"]) for e in entries if e.get("slot") is not None}
        if not mapping:
            return None

        self.manager.set_tool_map(mapping)
        self.manager._tool_map_active = True

        summary = tm.format_summary(entries)
        msg = f"ACE: Otomatik yuva eşleştirme: {summary}"
        sink = gcmd if gcmd is not None else getattr(self.manager, "gcode", None)
        if sink is not None:
            sink.respond_info(msg)
        return mapping
