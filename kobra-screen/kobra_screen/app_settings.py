"""Settings persistence, i18n helpers, display power and event/beep mixin for ``App``."""

import os

from . import i18n, touch_cal, tracker


class _AppSettingsMixin:
    @staticmethod
    def get_ace_driver_version(rinkhals_home=None):
        ver, _ = tracker.read_ace_driver_version(rinkhals_home)
        return ver if ver else "-"

    def tr(self, text):
        return i18n.tr(text, self.lang)

    def tr_short(self, text):
        return i18n.tr_short(text, self.lang)

    def tr_error(self, text):
        return i18n.tr_error(text, self.lang)

    def set_language(self, lang, persist=True):
        code = i18n.normalize_lang(lang)
        if persist:
            try:
                i18n.save_language(
                    code,
                    rinkhals_home=self.rinkhals_home,
                    app_name=self.app_name,
                    config_path=self.config_path,
                )
            except OSError as exc:
                self.notify(self.tr("Dil ayarı kaydedilemedi: %s") % exc)
                return False
        with self._lock:
            self.lang = code
            self.dirty = True
        return True

    def _display_config_read_candidates(self):
        env = os.environ
        if self.config_path or (env.get("KOBRA_CONFIG") or env.get("KOBRA_TOUCH_CONFIG") or "").strip():
            return [i18n.resolve_persistent_config_path(
                env=env, rinkhals_home=self.rinkhals_home, app_name=self.app_name, config_path=self.config_path
            )]
        cands = []
        cands.append(i18n.resolve_persistent_config_path(
            env=env, rinkhals_home=self.rinkhals_home, app_name=self.app_name, config_path=None
        ))
        if self.rinkhals_home is None:
            cands.append(os.path.join("/tmp/rinkhals/apps", self.app_name + ".config"))
        return cands

    def _load_persistent_settings(self):
        # Every candidate file contributes: the persistent file first, a temporary /tmp one on top key by key
        # (it often holds just one temporary property and must not hide the saved settings).
        # Safety: /tmp must never override extrusion_lock (REV-04).
        perm_path = os.path.abspath(i18n.resolve_persistent_config_path(
            env=os.environ, rinkhals_home=self.rinkhals_home, app_name=self.app_name, config_path=self.config_path
        ))
        tmp_path = os.path.abspath(os.path.join("/tmp/rinkhals/apps", self.app_name + ".config"))
        data = {}
        for path in reversed(self._display_config_read_candidates()):
            cfg = touch_cal._read_json_dict(path)
            if os.path.abspath(path) == tmp_path and tmp_path != perm_path:
                cfg.pop("extrusion_lock", None)
            data.update(cfg)
        if data:
            if "display_brightness" in data:
                try:
                    self.display_brightness = max(10, min(100, int(data["display_brightness"])))
                except (TypeError, ValueError):
                    pass
            if "display_sleep_sec" in data:
                try:
                    self.display_sleep_sec = max(0, int(data["display_sleep_sec"]))
                except (TypeError, ValueError):
                    pass
            if "display_sleep_on_print" in data:
                self.display_sleep_on_print = self._parse_cfg_bool(
                    data["display_sleep_on_print"], self.display_sleep_on_print
                )
            if "beep_enabled" in data:
                self.beep_enabled = self._parse_cfg_bool(data["beep_enabled"], self.beep_enabled)
            if "beep_on_complete" in data:
                self.beep_on_complete = self._parse_cfg_bool(data["beep_on_complete"], self.beep_on_complete)
            if "beep_on_error" in data:
                self.beep_on_error = self._parse_cfg_bool(data["beep_on_error"], self.beep_on_error)
            if "beep_on_confirm" in data:
                self.beep_on_confirm = self._parse_cfg_bool(data["beep_on_confirm"], self.beep_on_confirm)
            if "maintenance" in data:
                self.maintenance_items = tracker.load_maintenance_state(data["maintenance"])
            if "filament_spools" in data:
                self.spools = tracker.load_spools_state(data["filament_spools"])
            if "filament_low_threshold_g" in data:
                try:
                    self.filament_low_threshold_g = max(0.0, float(data["filament_low_threshold_g"]))
                except (TypeError, ValueError):
                    pass
            if "heating_lock" in data:
                self.heating_lock = self._parse_cfg_bool(data["heating_lock"], False)
            if "heating_confirm_skip" in data:
                self.heating_confirm_skip = self._parse_cfg_bool(data["heating_confirm_skip"], False)
            if "extrusion_lock" in data:
                self.extrusion_lock = self._parse_cfg_bool(data["extrusion_lock"], False)
            if "ace_dryer_cooldown_s" in data:
                self.ace_dryer_cooldown_s = self._parse_cooldown(data["ace_dryer_cooldown_s"])

    @staticmethod
    def _parse_cfg_bool(raw, default=False):
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        if isinstance(raw, (int, float)):
            return bool(raw)
        return default

    def _save_persistent_config(self, updates):
        path = i18n.resolve_persistent_config_path(
            rinkhals_home=self.rinkhals_home, app_name=self.app_name, config_path=self.config_path
        )
        touch_cal.update_json_config(path, updates, ensure_ascii=False)

    def _save_display_settings(self):
        self._save_persistent_config({
            "display_brightness": int(self.display_brightness),
            "display_sleep_sec": int(self.display_sleep_sec),
            "display_sleep_on_print": bool(self.display_sleep_on_print),
        })

    def _save_beep_settings(self):
        self._save_persistent_config({
            "beep_enabled": bool(self.beep_enabled),
            "beep_on_complete": bool(self.beep_on_complete),
            "beep_on_error": bool(self.beep_on_error),
            "beep_on_confirm": bool(self.beep_on_confirm),
        })

    def _save_heating_lock_settings(self):
        self._save_persistent_config({"heating_lock": bool(self.heating_lock)})

    def toggle_heating_lock(self):
        with self._lock:
            self.heating_lock = not self.heating_lock
            enabled = self.heating_lock
            self.dirty = True
        try:
            self._save_heating_lock_settings()
        except OSError as exc:
            self.notify(self.tr("Ayar kaydedilemedi: %s") % exc)
        self.log_event("Isıtma kilidi açıldı" if enabled else "Isıtma kilidi kapatıldı",
                       severity="warn" if enabled else "info", source="system")
        return enabled

    def _save_extrusion_lock_settings(self):
        self._save_persistent_config({"extrusion_lock": bool(self.extrusion_lock)})

    @staticmethod
    def _parse_cooldown(raw):
        """10..600 s, anything else means auto-detect from the ACE firmware (None)."""
        try:
            val = int(raw)
        except (TypeError, ValueError):
            return None
        return val if 10 <= val <= 600 else None

    def _save_ace_dryer_cooldown_settings(self):
        self._save_persistent_config({"ace_dryer_cooldown_s": self.ace_dryer_cooldown_s or 0})

    def set_ace_dryer_cooldown_s(self, seconds):
        with self._lock:
            self.ace_dryer_cooldown_s = self._parse_cooldown(seconds)
            self.dirty = True
        try:
            self._save_ace_dryer_cooldown_settings()
        except OSError as exc:
            self.notify(self.tr("Ayar kaydedilemedi: %s") % exc)
        return self.ace_dryer_cooldown_s

    def set_extrusion_lock(self, enabled):
        with self._lock:
            self.extrusion_lock = bool(enabled)
            self.dirty = True
        try:
            self._save_extrusion_lock_settings()
        except OSError as exc:
            self.notify(self.tr("Ayar kaydedilemedi: %s") % exc)
        self.log_event("Ekstrüzyon kilidi açıldı" if self.extrusion_lock else "Ekstrüzyon kilidi kapatıldı",
                       severity="warn" if self.extrusion_lock else "info", source="system")
        return self.extrusion_lock

    def toggle_extrusion_lock(self):
        return self.set_extrusion_lock(not self.extrusion_lock)

    def _save_heating_confirm_settings(self):
        self._save_persistent_config({"heating_confirm_skip": bool(self.heating_confirm_skip)})

    def set_heating_confirm_skip(self, skip):
        with self._lock:
            self.heating_confirm_skip = bool(skip)
            self.dirty = True
        try:
            self._save_heating_confirm_settings()
        except OSError as exc:
            self.notify(self.tr("Ayar kaydedilemedi: %s") % exc)
        return self.heating_confirm_skip

    def _save_maintenance_settings(self):
        self._save_persistent_config({"maintenance": self.maintenance_items})

    def _save_spool_settings(self, raise_on_error=False):
        payload = {
            "filament_spools": self.spools,
            "filament_low_threshold_g": float(self.filament_low_threshold_g),
        }
        try:
            self._save_persistent_config(payload)
            return True
        except OSError:
            if raise_on_error:
                raise
            return False

    def set_display_brightness(self, percent, persist=True):
        pct = max(10, min(100, int(round(percent))))
        with self._lock:
            prev = self.display_brightness
            self.display_brightness = pct
            self._last_activity = self._clock()
            self.dirty = True
        if persist:
            try:
                self._save_display_settings()
            except OSError as exc:
                with self._lock:
                    self.display_brightness = prev
                    self.dirty = True
                self.notify(self.tr("Ekran ayarı kaydedilemedi: %s") % exc)
                return False
        if self._display_backend is not None and hasattr(self._display_backend, "set_brightness"):
            self._display_backend.set_brightness(pct)
        return True

    def set_display_sleep(self, seconds, persist=True):
        sec = max(0, int(seconds))
        with self._lock:
            prev = self.display_sleep_sec
            self.display_sleep_sec = sec
            self._last_activity = self._clock()
            self.dirty = True
        if persist:
            try:
                self._save_display_settings()
            except OSError as exc:
                with self._lock:
                    self.display_sleep_sec = prev
                    self.dirty = True
                self.notify(self.tr("Ekran ayarı kaydedilemedi: %s") % exc)
                return False
        if sec == 0 and self.display_asleep:
            self.wake_display()
        return True

    def set_display_sleep_on_print(self, enabled, persist=True):
        val = bool(enabled)
        with self._lock:
            prev = self.display_sleep_on_print
            self.display_sleep_on_print = val
            self._last_activity = self._clock()
            self.dirty = True
        if persist:
            try:
                self._save_display_settings()
            except OSError as exc:
                with self._lock:
                    self.display_sleep_on_print = prev
                    self.dirty = True
                self.notify(self.tr("Ekran ayarı kaydedilemedi: %s") % exc)
                return False
        if not val and self.display_asleep and self.printing:
            self.wake_display()
        return True

    def should_inhibit_sleep(self):
        with self._lock:
            if self.modal is not None or self.touch_cal is not None:
                return True
            if not self.state.connected or self.state.klippy != "ready":
                return True
            if self.print_state == "error":
                return True
            if self.printing and not self.display_sleep_on_print:
                return True
            return False

    def note_activity(self):
        woke = False
        with self._lock:
            self._last_activity = self._clock()
            if self.display_asleep:
                self.display_asleep = False
                self.dirty = True
                woke = True
        if woke and self._display_backend is not None and hasattr(self._display_backend, "set_sleep"):
            self._display_backend.set_sleep(False)

    def sleep_display(self):
        with self._lock:
            if self.display_asleep:
                return False
            self.display_asleep = True
            self.dirty = True
        if self._display_backend is not None and hasattr(self._display_backend, "set_sleep"):
            self._display_backend.set_sleep(True)
        return True

    def wake_display(self):
        changed = False
        with self._lock:
            self._last_activity = self._clock()
            if self.display_asleep:
                self.display_asleep = False
                self.dirty = True
                changed = True
        if changed and self._display_backend is not None and hasattr(self._display_backend, "set_sleep"):
            self._display_backend.set_sleep(False)
        return changed

    def tick_display(self):
        with self._lock:
            asleep = self.display_asleep
            timeout = self.display_sleep_sec
            if not asleep and timeout <= 0:
                return None
            elapsed = self._clock() - self._last_activity
            if not asleep and elapsed < timeout:
                return None
            inhibited = self.should_inhibit_sleep()
        if asleep and inhibited:
            self.wake_display()
            return "woke"
        if not asleep and timeout > 0 and not inhibited and elapsed >= timeout:
            self.sleep_display()
            return "slept"
        return None

    def log_event(self, message, severity="info", source="app", ts=None, persist=True):
        text = str(message or "").strip()
        if not text:
            return None
        sev = str(severity or "info").lower()
        if sev not in ("error", "warn", "ok", "info"):
            sev = "info"
        entry = {
            "ts": float(self._wall_clock() if ts is None else ts),
            "severity": sev,
            "source": str(source or "app"),
            "message": text,
        }
        with self._lock:
            self.event_log.append(entry)
            if len(self.event_log) > tracker.MAX_EVENT_LOG:
                self.event_log = self.event_log[-tracker.MAX_EVENT_LOG:]
            if entry["source"] in ("gcode", "ace") and entry["ts"] > self._gcode_watermark:
                self._gcode_watermark = entry["ts"]
            self.dirty = True
        if persist:
            self._persist_event_log()
        return entry

    def _persist_event_log(self):
        """Write the newest event log; the snapshot is taken under the io lock so an older one never overwrites it."""
        with self._events_io_lock:
            with self._lock:
                snapshot = list(self.event_log)
                watermark = self._gcode_watermark
            tracker.save_event_log(self.events_path, snapshot, watermark=watermark)

    def clear_events(self):
        with self._lock:
            self.event_log = []
            self.events_page = 0
            self._gcode_watermark = max(self._gcode_watermark, float(self._wall_clock()))
            self.dirty = True
        self._persist_event_log()
        self.notify(self.tr("Olay geçmişi temizlendi"), 3, "active", record=False)

    def _beep_gcode_fn(self):
        if not self.ready:
            return None
        return lambda script: self.client.gcode(script)

    def trigger_beep(self, event_type, force=False):
        with self._lock:
            if not force:
                if not self.beep_enabled:
                    return False
                if event_type == "complete" and not self.beep_on_complete:
                    return False
                if event_type == "error" and not self.beep_on_error:
                    return False
                if event_type == "confirm" and not self.beep_on_confirm:
                    return False
            settings_snap = dict(self.state.settings) if self.state.settings else None
        gcode_fn = self._beep_gcode_fn()
        self.beeper.play_async(event_type, settings=settings_snap, gcode_fn=gcode_fn)
        return True

    def set_beep_option(self, field_name, enabled, persist=True):
        allowed = ("beep_enabled", "beep_on_complete", "beep_on_error", "beep_on_confirm")
        if field_name not in allowed:
            raise ValueError("Unknown beep setting: %r" % (field_name,))
        val = bool(enabled)
        with self._lock:
            prev = getattr(self, field_name)
            setattr(self, field_name, val)
            self.dirty = True
        if persist:
            try:
                self._save_beep_settings()
            except OSError as exc:
                with self._lock:
                    setattr(self, field_name, prev)
                    self.dirty = True
                self.notify(self.tr("Ses ayarı kaydedilemedi: %s") % exc)
                return False
        return True
