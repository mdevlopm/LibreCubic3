"""Moonraker polling, snapshots, gcode_store/temperature_store sync and cache mixin for ``App``."""

from collections import deque
import os
import time
import zlib

from . import prompts, tracker
from .app_models import (
    ACE_INFO_DEDUPE_SEC, GCODE_STORE_COUNT, MAX_CACHED_META, META_RETRY_SEC, PNG_SIGNATURE,
    POLL_OBJECTS, THUMBNAIL_SIZE,
)
from .moonraker import MoonrakerError
from .prompts import PromptDialog


class _AppPollingMixin:
    def temperature_series(self, sensor, points=60):
        with self._lock:
            data = self.temp_history.get(sensor) or {}
            temps = list(data.get("temps") or ())
            targets = list(data.get("targets") or ())
        if not temps:
            cur_t = self.get(sensor, "temperature", 0.0)
            cur_g = self.get(sensor, "target", 0.0)
            return [cur_t], [cur_g]
        return self._downsample_series(temps, points), self._downsample_series(targets, points)

    @staticmethod
    def _downsample_series(values, max_points):
        n = len(values)
        if n <= max_points or max_points < 2:
            return list(values)
        out = []
        for i in range(max_points):
            idx = int(round(i * (n - 1) / float(max_points - 1)))
            out.append(values[idx])
        return out

    # ---- data ----

    @classmethod
    def _freeze_value(cls, value):
        """Convert nested dict/list status payloads into hashable comparable tuples."""
        if isinstance(value, dict):
            return tuple(sorted((k, cls._freeze_value(v)) for k, v in value.items()))
        if isinstance(value, (list, tuple)):
            return tuple(cls._freeze_value(v) for v in value)
        return value

    def _visual_snapshot(self):
        """Return a comparable tuple of all Moonraker-backed values rendered by the UI."""
        if getattr(self, "_visual_snapshot_taken", False):
            return
        st = self.state.status or {}
        ext = st.get("extruder") or {}
        bed = st.get("heater_bed") or {}
        ps = st.get("print_stats") or {}
        vsd = st.get("virtual_sdcard") or {}
        gm = st.get("gcode_move") or {}
        th = st.get("toolhead") or {}
        fan = st.get("fan") or {}
        lq = st.get("leviq3") or {}
        eo = st.get("exclude_object") or {}
        ace0 = st.get("ace_instance_0")
        aces = st.get("ace_state") or {}
        pin_noz = st.get("output_pin nozzle_light") or {}
        pin_logo = st.get("output_pin logo_light") or {}
        min_ext = float(self.setting("extruder", "min_extrude_temp", 170) or 170)
        ext_temp = float(ext.get("temperature") or 0.0)
        ps_info = ps.get("info") or {}

        on_heat = (
            (not self.pages and self.tab == "control" and self.control_seg == "heat")
            or (bool(self.pages) and self.pages[-1][0] == "heat_chart")
        )
        heat_chart = None
        if on_heat:
            noz_t, noz_g = self.temperature_series("extruder", 60)
            bed_t, bed_g = self.temperature_series("heater_bed", 60)
            heat_chart = (
                tuple(int(round(v)) for v in noz_t),
                tuple(int(round(v)) for v in noz_g),
                tuple(int(round(v)) for v in bed_t),
                tuple(int(round(v)) for v in bed_g),
            )
        on_bed_mesh = bool(self.pages) and self.pages[-1][0] == "bed_mesh"
        bm_snap = self._freeze_value(st.get("bed_mesh")) if on_bed_mesh else None

        snap = (
            self.state.connected,
            self.state.klippy,
            self.state.klippy_message if (not self.state.connected or self.state.klippy != "ready") else "",
            self.state.version,
            self.state.hostname,
            bool(self.state.settings),
            len(self.state.settings),
            int(round(ext_temp)),
            int(round(float(ext.get("target") or 0.0))),
            ext_temp >= min_ext,
            int(round(float(bed.get("temperature") or 0.0))),
            int(round(float(bed.get("target") or 0.0))),
            ps.get("state"),
            ps.get("filename"),
            ps.get("message"),
            int(float(ps.get("print_duration") or 0.0)),
            ps_info.get("current_layer") if isinstance(ps_info, dict) else None,
            ps_info.get("total_layer") if isinstance(ps_info, dict) else None,
            int(round(float(vsd.get("progress") or 0.0) * 1000)),
            tuple(round(float(v), 2) for v in (gm.get("gcode_position") or ())[:3]),
            round(float(gm.get("speed_factor") or 1.0), 2),
            round(float(gm.get("extrude_factor") or 1.0), 2),
            tuple(round(float(v), 3) for v in (gm.get("homing_origin") or ())[:3]),
            tuple(round(float(v), 2) for v in (th.get("position") or ())[:3]),
            th.get("homed_axes"),
            int(round(float(fan.get("speed") or 0.0) * 100)),
            bool(lq),
            round(float(lq.get("z_offset") or 0.0), 3),
            int(float(lq.get("auto_zoffset_on_off") or 0)),
            self._freeze_value(eo) if eo else None,
            self._freeze_value(ace0) if ace0 else None,
            aces.get("current_index"),
            aces.get("toolhead_sensor"),
            aces.get("rdm_sensor"),
            aces.get("endless_spool_enabled"),
            aces.get("endless_spool_match_mode"),
            float(pin_noz.get("value") or 0.0) > 0,
            float(pin_logo.get("value") or 0.0) > 0,
            heat_chart,
            bm_snap,
            self.ace_stopping_kind(),
            bool(self.heating_lock),
        )
        self._visual_snapshot_taken = True
        return snap

    def refresh(self):
        """Poll Moonraker; called about once a second from a worker thread."""
        self._visual_snapshot_taken = False
        rollback_prev = None
        events_to_log = []
        beep_to_play = None
        deduct_usages = []
        need_totals = False
        with self._refresh_lock:
            with self._lock:
                need_settings = not bool(self.state.settings)
                self._poll_counter += 1
                poll_idx = self._poll_counter
            try:
                info = self.client.info()
                klippy = info.get("state", "?")
                klippy_message = info.get("state_message", "")
                version = info.get("software_version", "")
                hostname = info.get("hostname", "")
                new_settings = None
                new_status = None
                if klippy == "ready":
                    if need_settings:
                        new_settings = self.client.query({"configfile": ["settings"]})["configfile"]["settings"]
                    new_status = self.client.query(POLL_OBJECTS)
                with self._lock:
                    prev_conn = self._prev_connected
                    prev_klippy = self._prev_klippy
                    prev_pstate = self._prev_print_state
                    prev_perr = self._prev_print_error
                    self.state.klippy = klippy
                    self.state.klippy_message = klippy_message
                    self.state.version = version
                    self.state.hostname = hostname
                    if klippy == "ready":
                        if new_settings is not None:
                            self.state.settings = new_settings
                        if new_status is not None:
                            self.state.status = new_status
                        self._limits_rollback = None
                    else:
                        self.state.settings = {}
                        if self._limits_rollback is not None and not self._rolling_back:
                            prev_content, deadline = self._limits_rollback
                            if klippy == "error" or time.monotonic() >= deadline:
                                self._limits_rollback = None
                                self._rolling_back = True
                                rollback_prev = prev_content
                    self.state.connected = True
                    self._prev_connected = True
                    self._prev_klippy = klippy

                    if prev_conn is False:
                        events_to_log.append(("Moonraker bağlantısı kuruldu", "ok", "klipper"))
                    if prev_klippy is None:
                        if klippy in ("error", "shutdown"):
                            msg = klippy_message or ("Klipper: %s" % klippy)
                            events_to_log.append((msg, "error", "klipper"))
                    elif klippy != prev_klippy and klippy in ("ready", "shutdown", "error"):
                        sev = "ok" if klippy == "ready" else "error"
                        msg = ("Klipper hazır" if klippy == "ready" else (klippy_message or ("Klipper: %s" % klippy)))
                        events_to_log.append((msg, sev, "klipper"))
                        if klippy in ("shutdown", "error"):
                            beep_to_play = "error"

                    if klippy == "ready" and new_status is not None:
                        for sensor in ("extruder", "heater_bed"):
                            sdata = new_status.get(sensor) or {}
                            if "temperature" in sdata:
                                self.temp_history[sensor]["temps"].append(float(sdata.get("temperature") or 0.0))
                                self.temp_history[sensor]["targets"].append(float(sdata.get("target") or 0.0))

                        ps = new_status.get("print_stats") or {}
                        cur_pstate = ps.get("state", "standby") or "standby"
                        cur_perr = str(ps.get("message") or "")
                        cur_fname = str(ps.get("filename") or "")
                        cur_fil = float(ps.get("filament_used") or 0.0)
                        ace_inst = new_status.get("ace_instance_0")
                        ace_present = isinstance(ace_inst, dict) and str(ace_inst.get("connection_state") or "").strip().lower() == "connected"
                        cur_tool = (new_status.get("ace_state") or {}).get("current_index", -1)

                        was_active = self.print_tracker.active
                        prev_status_tool = self.print_tracker._last_status_tool
                        step_deductions = self.print_tracker.update(
                            cur_pstate,
                            filename=cur_fname,
                            filament_used=cur_fil,
                            current_tool=cur_tool,
                            ace_present=ace_present,
                            wall_now=self._wall_clock(),
                        )
                        if step_deductions:
                            deduct_usages.extend(step_deductions)
                        if self.print_tracker.active:
                            if not was_active or self.print_tracker._last_status_tool != prev_status_tool:
                                self._last_status_tool_wall = self._wall_clock()
                            self._print_session = {
                                "filename": self.print_tracker.filename,
                                "active_tool": self.print_tracker.active_tool,
                                "max_filament": self.print_tracker.total_filament_used,
                            }
                        else:
                            self._print_session = None

                        if prev_pstate in ("printing", "paused") and cur_pstate not in ("printing", "paused"):
                            need_totals = True
                            if cur_pstate == "complete":
                                beep_to_play = "complete"
                            elif cur_pstate == "error":
                                beep_to_play = "error"

                        if cur_pstate == "error" and (prev_pstate != "error" or (cur_perr and cur_perr != prev_perr)):
                            events_to_log.append((cur_perr or "Baskı hatası", "error", "print"))
                            if beep_to_play is None:
                                beep_to_play = "error"
                        self._prev_print_state = cur_pstate
                        self._prev_print_error = cur_perr
                        if not self.history_totals or (self._clock() - self._last_totals_poll >= 60.0):
                            need_totals = True
                    if klippy == "ready" and new_status is not None:
                        self._update_dryer_cooling_locked(new_status)
                        self._reconcile_ace_stopping_locked(new_status)
                    new_snap = self._visual_snapshot()
                    if new_snap is not None and new_snap != self._last_visual_snapshot:
                        self._last_visual_snapshot = new_snap
                        self.dirty = True
            except (MoonrakerError, KeyError, TypeError, AttributeError, ValueError) as exc:
                # TypeError/AttributeError/ValueError: Moonraker answered with a shape we do not expect (null fields);
                # treat it like a failed poll instead of letting the poll thread die.
                with self._lock:
                    prev_conn = self._prev_connected
                    self.state.connected = False
                    self.state.settings = {}
                    self.state.klippy_message = str(exc)
                    self._prev_connected = False
                    if prev_conn is not False:
                        events_to_log.append(("Moonraker bağlantısı kesildi: %s" % exc, "error", "klipper"))
                    new_snap = self._visual_snapshot()
                    if new_snap is not None and new_snap != self._last_visual_snapshot:
                        self._last_visual_snapshot = new_snap
                        self.dirty = True

            # Determine secondary syncs inside lock, then run them OUTSIDE _refresh_lock so urgent
            # status refreshes never block behind temperature_store/history/metadata requests.
            with self._lock:
                on_heat_view = (
                    (not self.pages and self.tab == "control" and self.control_seg == "heat")
                    or (bool(self.pages) and self.pages[-1][0] == "heat_chart")
                )
                try_temp_store = (
                    self.state.connected
                    and self.state.klippy == "ready"
                    and self._temp_store_supported
                    and (on_heat_view or poll_idx == 1 or poll_idx % 10 == 0)
                    and (self._clock() - self._last_temp_store_poll >= self._temp_update_interval)
                )
                try_gcode_store = (
                    self.state.connected
                    and (
                        poll_idx == 1
                        or self.state.klippy != "ready"
                        or bool(self.ace_pending)
                        or self.print_state in ("printing", "paused")
                        or isinstance(self.modal, PromptDialog)
                        or (self._clock() - self._last_gcode_store_poll >= self._gcode_store_interval - 0.1)
                    )
                )
                try_sysinfo = (
                    self.state.connected
                    and bool(self.pages) and self.pages[-1][0] == "sysinfo"
                    and self._clock() - self._last_sysinfo_poll >= 2.0
                )
                filename = self.get("print_stats", "filename", "")
                should_load_meta = bool(
                    self.state.connected
                    and filename
                    and filename not in self.meta
                    and filename not in self._meta_inflight
                    and self._clock() - self._meta_failed.get(filename, -1e9) >= META_RETRY_SEC
                )

        if self._secondary_lock.acquire(blocking=False):
            try:
                if try_temp_store:
                    self._sync_temperature_store()
                if try_gcode_store:
                    self._sync_gcode_errors(events_to_log)
                if need_totals and self.state.connected:
                    self.refresh_maintenance_totals()
                if should_load_meta:
                    self.load_metadata(filename)
                if try_sysinfo:
                    self.refresh_sysinfo()
            finally:
                self._secondary_lock.release()

        for msg, sev, src in events_to_log:
            self.log_event(msg, severity=sev, source=src)
        for spool_key, used_mm in deduct_usages:
            self._deduct_filament_usage(spool_key, used_mm)
        if beep_to_play is not None:
            self.trigger_beep(beep_to_play)
        if rollback_prev is not None:
            self.run_async(lambda c=rollback_prev: self._rollback_limits(c))
        if self.display_asleep and self.should_inhibit_sleep():
            self.wake_display()

    def _sync_temperature_store(self):
        self._last_temp_store_poll = self._clock()
        try:
            store = self.client.temperature_store(include_monitors=False)
        except MoonrakerError:
            with self._lock:
                self._temp_store_supported = False
            return
        if not isinstance(store, dict):
            return
        with self._lock:
            if store == self._last_temp_store_data:
                return
            self._last_temp_store_data = store
            for sensor in ("extruder", "heater_bed"):
                sdata = store.get(sensor)
                if not isinstance(sdata, dict):
                    continue
                temps = sdata.get("temperatures")
                targets = sdata.get("targets")
                if isinstance(temps, list) and temps:
                    cleaned_t = [float(v) for v in temps[-300:] if v is not None]
                    if cleaned_t:
                        self.temp_history[sensor]["temps"] = deque(cleaned_t, maxlen=300)
                if isinstance(targets, list) and targets:
                    cleaned_g = [float(v) for v in targets[-300:] if v is not None]
                    if cleaned_g:
                        self.temp_history[sensor]["targets"] = deque(cleaned_g, maxlen=300)
            self.dirty = True

    @staticmethod
    def _ace_info_severity(text):
        low = text.lower()
        if any(w in low for w in ("fail", "error", "timeout", "timed out", "not found", "no ace", "retry", "lost")):
            return "warn"
        if any(w in low for w in ("started", "stopped", "loaded", "unloaded", "saved", "reconnect", "connected")):
            return "ok"
        return "info"

    def _record_dismissed_prompt_locked(self, ts, title):
        """Remember a dismissed prompt_begin so it is never re-opened on subsequent polls."""
        ts_val = float(ts or 0.0)
        if ts_val > 0.0 and (self._dismissed_prompt_ts is None or ts_val > self._dismissed_prompt_ts):
            self._dismissed_prompt_ts = ts_val
        self._dismissed_prompt_keys.add((round(ts_val, 4), str(title or "")))
        if len(self._dismissed_prompt_keys) > 50:
            keep = sorted(self._dismissed_prompt_keys, key=lambda item: item[0])[-25:]
            self._dismissed_prompt_keys = set(keep)

    def _sync_gcode_errors(self, out_events=None):
        """Read the Klipper console (gcode_store) and act on what the ACE driver and Klipper said.

        "!! ..." lines are errors (the ACE driver sends "!! ACE[n]: ..."); "// ACE[n]: ..." lines are the driver's
        information.  Both go to the event log (repeated identical info lines are filtered).  An answer to the ACE
        command the user just sent (`ace_pending`) becomes a short notice; other new errors raise a notice and a
        beep.  Returns the new errors as [(text, ts)].
        """
        self._last_gcode_store_poll = self._clock()
        try:
            res = self.client.gcode_store(count=GCODE_STORE_COUNT)
        except MoonrakerError:
            return []
        entries = (res or {}).get("gcode_store") if isinstance(res, dict) else None
        if not isinstance(entries, list):
            return []
        new_errs = []
        new_infos = []
        toolchange_deductions = []
        curr_prompt_keys = []
        curr_prompt_items = []
        pend_err = pend_info = None
        pend = None
        with self._lock:
            if isinstance(getattr(self, "ace_pending", None), (list, deque)):
                pend = self.ace_pending[0] if self.ace_pending else None
            elif isinstance(getattr(self, "ace_pending", None), dict):
                pend = self.ace_pending
            else:
                pend = None
            for item in entries:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type")
                msg = str(item.get("message") or "").strip()
                item_ts = float(item.get("time") or 0.0)
                if item_type in ("command", "response") and self.print_tracker.active and item_ts > 0.0:
                    if item_ts >= self.print_tracker.session_start_wall - 1.0 and item_ts > self._last_status_tool_wall:
                        tc_key = (round(item_ts, 3), msg)
                        if tc_key not in self._seen_toolchange_gcodes and tracker.parse_toolchange_gcode(msg) is not None:
                            self._seen_toolchange_gcodes[tc_key] = item_ts
                            if len(self._seen_toolchange_gcodes) > 400:
                                keep = sorted(self._seen_toolchange_gcodes.items(), key=lambda kv: kv[1])[-200:]
                                self._seen_toolchange_gcodes = dict(keep)
                            toolchange_deductions.extend(self.print_tracker.handle_gcode_response(msg))
                if item_type != "response":
                    continue
                if prompts.is_prompt_line(msg):
                    curr_prompt_keys.append((round(item_ts, 4), msg))
                    curr_prompt_items.append(item)
                    continue
                if msg.startswith("!!"):
                    kind, clean = "error", msg[2:].strip()
                elif msg.startswith("//") and msg[2:].strip().startswith("ACE["):
                    kind, clean = "info", msg[2:].strip()
                else:
                    continue
                if not clean:
                    continue
                if item_ts > 0.0 and item_ts <= self._gcode_watermark:
                    continue
                key = (round(item_ts, 3), clean)
                if key in self._seen_gcode_errors:
                    continue
                self._seen_gcode_errors[key] = item_ts
                if len(self._seen_gcode_errors) > 400:
                    keep = sorted(self._seen_gcode_errors.items(), key=lambda kv: kv[1])[-200:]
                    self._seen_gcode_errors = dict(keep)
                answers = bool(pend) and (item_ts <= 0.0 or item_ts >= pend["wall"] - 1.0)
                if kind == "error":
                    new_errs.append((clean, item_ts or self._wall_clock()))
                    if answers and pend_err is None:
                        pend_err = clean
                else:
                    if answers and pend_info is None:
                        pend_info = clean
                    last = self._ace_info_logged.get(clean)
                    if last is None or (item_ts or self._wall_clock()) - last >= ACE_INFO_DEDUPE_SEC:
                        self._ace_info_logged[clean] = item_ts or self._wall_clock()
                        new_infos.append((clean, item_ts or self._wall_clock()))
                    if len(self._ace_info_logged) > 200:
                        self._ace_info_logged = dict(sorted(self._ace_info_logged.items(), key=lambda kv: kv[1])[-100:])
            resolved = None
            if pend is not None and (pend_err is not None or pend_info is not None):
                resolved = ("error", pend_err) if pend_err is not None else ("info", pend_info)
                self.handle_ace_response(pend.get("id"), message=resolved[1], is_error=(resolved[0] == "error"))

            # On startup, treat any action:prompt lines already in gcode_store as seen.
            if not self._prompt_initialized:
                self._prompt_initialized = True
                self._last_prompt_history = curr_prompt_keys
                has_unclosed = False
                for item in curr_prompt_items:
                    if not isinstance(item, dict):
                        continue
                    p_msg = str(item.get("message") or "").strip()
                    p_ts = float(item.get("time") or 0.0)
                    p_title = prompts.parse_prompt_begin(p_msg)
                    if p_title is not None:
                        self._record_dismissed_prompt_locked(p_ts, p_title)
                        has_unclosed = True
                    elif prompts._extract_prompt_cmd(p_msg) == "end":
                        has_unclosed = False
                    elif prompts.is_prompt_line(p_msg):
                        has_unclosed = True
                if has_unclosed:
                    self.prompt_dialog.dismissed = True
            else:
                # Parse only unseen action:prompt commands from gcode_store responses
                new_prompt_items = prompts.slice_new_prompt_items(
                    self._last_prompt_history, curr_prompt_keys, curr_prompt_items
                )
                self._last_prompt_history = curr_prompt_keys
                if new_prompt_items:
                    prompts.try_parse_prompt_lines(
                        new_prompt_items,
                        self.prompt_dialog,
                        dismissed_begins=self._dismissed_prompt_keys,
                        min_begin_ts=self._dismissed_prompt_ts,
                    )
                    if self.prompt_dialog.visible:
                        # Only take over modal if no other modal is open, or if it's already a PromptDialog
                        if self.modal is None or isinstance(self.modal, PromptDialog):
                            self.modal = self.prompt_dialog
                            self.dirty = True
                    elif self.prompt_dialog.complete:
                        if isinstance(self.modal, PromptDialog):
                            self.modal = None
                            self.dirty = True
                        self.prompt_dialog.reset()
        for clean, ts in new_errs:
            src = "ace" if clean.startswith("ACE[") else "gcode"
            self.log_event(clean, severity="error", source=src, ts=ts)
        for clean, ts in new_infos:
            self.log_event(clean, severity=self._ace_info_severity(clean), source="ace", ts=ts)
        for spool_key, used_mm in toolchange_deductions:
            self._deduct_filament_usage(spool_key, used_mm)
        if resolved is not None:
            if resolved[0] == "error":
                self.notify(self.tr_error(resolved[1]), 6, "outline_danger", record=False)
                self.trigger_beep("error")
            else:
                self.notify(pend["done"] if pend["done"] else resolved[1], 3, "active", record=False)
        else:
            fresh = [(c, t) for c, t in new_errs if t >= self._app_start_wall - 1.0]
            if fresh:
                self.notify(self.tr_error(fresh[-1][0]), 6, "outline_danger", record=False)
                self.trigger_beep("error")
        return new_errs

    def load_metadata(self, filename):
        """Fetch slicer metadata and a validated PNG thumbnail closest to what the screen shows."""
        with self._lock:
            self._meta_inflight.add(filename)
        fetched_meta = None
        try:
            meta = dict(self.client.metadata(filename) or {})
            thumbs = meta.get("thumbnails") or []
            png_thumbs = [
                t for t in thumbs
                if str(t.get("relative_path", "")).lower().endswith(".png")
            ]
            if png_thumbs:
                best = min(png_thumbs, key=lambda t: abs(t.get("width", 0) - THUMBNAIL_SIZE))
                folder = os.path.dirname(filename)
                data = self.client.download(os.path.join(folder, best["relative_path"]))
                if isinstance(data, (bytes, bytearray)) and bytes(data[:8]) == PNG_SIGNATURE:
                    os.makedirs(self.cache_dir, exist_ok=True)
                    path = os.path.join(self.cache_dir, "thumb-%08x.png" % zlib.crc32(filename.encode("utf-8")))
                    with open(path, "wb") as f:
                        f.write(data)
                    meta["thumbnail"] = path
                    meta["thumbnail_width"] = best.get("width", THUMBNAIL_SIZE)
            fetched_meta = meta
        except (MoonrakerError, OSError, KeyError, TypeError, AttributeError, ValueError):
            pass
        finally:
            with self._lock:
                self._meta_inflight.discard(filename)
                if fetched_meta is not None:
                    self.meta[filename] = fetched_meta
                    self._meta_failed.pop(filename, None)
                    self._prune_cache(keep_filename=filename)
                else:
                    # no empty placeholder: opening the file page retries; the poll waits before trying again
                    self._meta_failed[filename] = self._clock()
                self.dirty = True

    def _prune_cache(self, keep_filename=""):
        """Keep /tmp thumbnail cache and metadata dictionary bounded in RAM."""
        with self._lock:
            active = {keep_filename, self.get("print_stats", "filename", "")}
            if self.pages and self.pages[-1][0] == "file":
                active.add(self.pages[-1][1] or "")
            active.discard("")

            while len(self.meta) > MAX_CACHED_META:
                victim = next((k for k in self.meta if k not in active), None)
                if victim is None:
                    break
                old = self.meta.pop(victim, None) or {}
                thumb = old.get("thumbnail")
                if thumb and os.path.exists(thumb):
                    try:
                        os.remove(thumb)
                    except OSError:
                        pass

            if not os.path.isdir(self.cache_dir):
                return
            try:
                entries = [
                    os.path.join(self.cache_dir, name)
                    for name in os.listdir(self.cache_dir)
                    if name.startswith("thumb-") and name.endswith(".png")
                ]
            except OSError:
                return
            if len(entries) <= self.max_thumbnails:
                return

            protected_paths = {
                (self.meta.get(name) or {}).get("thumbnail")
                for name in active
            }
            protected_paths.discard(None)
            entries.sort(key=lambda p: (p in protected_paths, os.path.getmtime(p) if os.path.exists(p) else 0.0))
            path_to_key = {
                (info or {}).get("thumbnail"): key
                for key, info in list(self.meta.items())
                if (info or {}).get("thumbnail")
            }
            for victim_path in entries[: max(0, len(entries) - self.max_thumbnails)]:
                if victim_path in protected_paths:
                    continue
                try:
                    os.remove(victim_path)
                except OSError:
                    pass
                key = path_to_key.get(victim_path)
                if key and key not in active:
                    self.meta.pop(key, None)

    def refresh_files(self):
        try:
            files = self.client.files()
            sorted_files = sorted(files, key=lambda f: f.get("modified", 0), reverse=True)
            with self._lock:
                self.state.files = sorted_files
                self.dirty = True
        except MoonrakerError as exc:
            self.notify(self.tr_error(str(exc)))

    def refresh_history(self):
        try:
            totals_res = self.client.history_totals() or {}
            list_res = self.client.history_list(limit=50, start=0, order="desc") or {}
            totals = totals_res.get("job_totals") if isinstance(totals_res, dict) else {}
            jobs = list_res.get("jobs") if isinstance(list_res, dict) else []
            with self._lock:
                self.history_totals = dict(totals or {})
                self.history_jobs = list(jobs or [])
                self.history_error = ""
                self.dirty = True
        except MoonrakerError as exc:
            with self._lock:
                self.history_error = self.tr_error(str(exc))
                self.dirty = True

    @property
    def fast_poll_active(self):
        with self._lock:
            return self._clock() < self._fast_poll_until or self.ace_stopping_kind() is not None

    @property
    def poll_interval(self):
        return 0.2 if self.fast_poll_active else 1.0

    def wait_poll(self, stop_event):
        """Wait until the next poll cycle or an urgent wakeup event."""
        interval = self.poll_interval
        if stop_event.is_set():
            return
        self._poll_wakeup.wait(interval)
        self._poll_wakeup.clear()

    def request_fast_poll(self, seconds=3.0):
        with self._lock:
            self._fast_poll_until = max(self._fast_poll_until, self._clock() + float(seconds))
        self._poll_wakeup.set()
