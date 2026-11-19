"""Maintenance, update-status and filament spool tracking mixin for ``App``."""

from . import tracker
from .moonraker import MoonrakerError


class _AppSpoolsMixin:
    def refresh_maintenance_totals(self):
        try:
            totals_res = self.client.history_totals() or {}
            totals = totals_res.get("job_totals") if isinstance(totals_res, dict) else {}
            new_totals = dict(totals or {})
            with self._lock:
                changed = new_totals != self.history_totals
                self.history_totals = new_totals
                self._last_totals_poll = self._clock()
                if changed:
                    self.dirty = True
        except MoonrakerError:
            pass

    def maintenance_summary(self):
        with self._lock:
            totals = dict(self.history_totals or {})
            items = dict(self.maintenance_items)
        total_print_time = float(totals.get("total_print_time") or 0.0)
        total_jobs = int(totals.get("total_jobs") or 0)
        rows = []
        for item_id, entry in items.items():
            cur, elapsed, threshold, due = tracker.maintenance_progress(entry, total_print_time, total_jobs)
            rows.append({
                "id": item_id,
                "label": entry["label"],
                "metric": entry["metric"],
                "current": cur,
                "elapsed": elapsed,
                "threshold": threshold,
                "due": due,
            })
        return rows, total_print_time, total_jobs

    def due_maintenance_items(self):
        rows, _, _ = self.maintenance_summary()
        return [r for r in rows if r["due"]]

    def mark_maintenance_done(self, item_id):
        with self._lock:
            entry = self.maintenance_items.get(item_id)
            if entry is None:
                return False
            totals = dict(self.history_totals or {})
            metric = entry.get("metric", "hours")
            cur = (
                float(totals.get("total_print_time") or 0.0) / 3600.0
                if metric == "hours"
                else float(int(totals.get("total_jobs") or 0))
            )
            prev = entry["last_done"]
            entry["last_done"] = cur
            self.dirty = True
        try:
            self._save_maintenance_settings()
        except OSError as exc:
            with self._lock:
                entry["last_done"] = prev
                self.dirty = True
            self.notify(self.tr("Bakım ayarı kaydedilemedi: %s") % exc)
            return False
        self.notify(self.tr("%s yapıldı olarak işaretlendi") % self.tr(entry["label"]), 3, "active")
        return True

    def set_maintenance_threshold(self, item_id, value):
        with self._lock:
            entry = self.maintenance_items.get(item_id)
            if entry is None:
                return False
            prev = entry["threshold"]
            entry["threshold"] = max(0.0, float(value))
            self.dirty = True
        try:
            self._save_maintenance_settings()
        except OSError as exc:
            with self._lock:
                entry["threshold"] = prev
                self.dirty = True
            self.notify(self.tr("Bakım ayarı kaydedilemedi: %s") % exc)
            return False
        self.notify(self.tr("Bakım eşiği güncellendi"), 3, "active")
        return True

    def refresh_updates(self):
        ok_ver, ok_path = tracker.read_open_kobra_version(self.rinkhals_home)
        ace_ver = self.get_ace_driver_version(self.rinkhals_home)
        app_ver = tracker.read_app_version(self.app_root)
        try:
            data = self.client.update_status()
            err = ""
        except MoonrakerError as exc:
            data = None
            err = self.tr_error(str(exc))
        with self._lock:
            self.open_kobra_version = ok_ver
            self.open_kobra_version_path = ok_path
            self.ace_driver_version = ace_ver
            self.app_version = app_ver
            self.update_status_data = data
            self.update_checked = True
            self.update_error = err
            self.dirty = True

    def active_spool_key(self):
        with self._lock:
            if self.ace_connected:
                idx = self.get("ace_state", "current_index", -1)
                if isinstance(idx, int) and 0 <= idx <= 3:
                    return str(idx)
            return "external"

    def spool_info(self, key):
        k = str(key)
        with self._lock:
            entry = dict(self.spools.get(k) or tracker.default_spools_state()["external"])
            thresh = float(self.filament_low_threshold_g)
            if k in ("0", "1", "2", "3") and self.ace_connected:
                ace = self.ace_data()
                slot = next((s for s in (ace.get("slots") or ()) if isinstance(s, dict) and s.get("index") == int(k)), None)
                if slot and slot.get("material"):
                    mat = str(slot["material"]).strip().upper()
                    if mat in tracker.MATERIAL_DENSITIES:
                        entry["material"] = mat
        mat = entry["material"]
        rem_mm = float(entry["remaining_mm"])
        init_mm = float(entry["initial_mm"])
        rem_g = tracker.mm_to_grams(rem_mm, mat)
        rem_m = tracker.mm_to_meters(rem_mm)
        init_g = tracker.mm_to_grams(init_mm, mat)
        init_m = tracker.mm_to_meters(init_mm)
        frac = max(0.0, min(1.0, rem_mm / init_mm)) if init_mm > 0 else 0.0
        low = bool(entry["configured"] and thresh > 0.0 and rem_g <= thresh)
        return {
            "key": k,
            "configured": bool(entry["configured"]),
            "material": mat,
            "unit": entry["unit"],
            "initial_mm": init_mm,
            "remaining_mm": rem_mm,
            "initial_g": init_g,
            "initial_m": init_m,
            "remaining_g": rem_g,
            "remaining_m": rem_m,
            "fraction": frac,
            "low": low,
            "threshold_g": thresh,
        }

    def low_filament_spools(self):
        with self._lock:
            keys = ("0", "1", "2", "3") if self.ace_connected else ("external",)
        out = []
        for k in keys:
            info = self.spool_info(k)
            if info["low"]:
                out.append(info)
        return out

    def set_spool_amount(self, key, value, unit="g", material=None, persist=True):
        k = str(key)
        with self._lock:
            if k not in self.spools:
                return False
            entry = self.spools[k]
            if material:
                mat = str(material).strip().upper()
                if mat in tracker.MATERIAL_DENSITIES:
                    entry["material"] = mat
            elif k in ("0", "1", "2", "3"):
                ace = self.state.status.get("ace_instance_0") or {}
                slot = next((s for s in (ace.get("slots") or ()) if isinstance(s, dict) and s.get("index") == int(k)), None)
                if slot and slot.get("material"):
                    mat = str(slot["material"]).strip().upper()
                    if mat in tracker.MATERIAL_DENSITIES:
                        entry["material"] = mat
            u = "m" if str(unit).lower() == "m" else "g"
            mm = tracker.meters_to_mm(value) if u == "m" else tracker.grams_to_mm(value, entry["material"])
            entry["configured"] = True
            entry["unit"] = u
            entry["initial_mm"] = mm
            entry["remaining_mm"] = mm
            self.dirty = True
        if persist:
            try:
                self._save_spool_settings(raise_on_error=True)
            except OSError as exc:
                self.notify(self.tr("Makara ayarı kaydedilemedi: %s") % exc)
                return False
        return True

    def set_spool_material(self, key, material, persist=True):
        k = str(key)
        mat = str(material or "PLA").strip().upper()
        if mat not in tracker.MATERIAL_DENSITIES:
            return False
        with self._lock:
            if k not in self.spools:
                return False
            self.spools[k]["material"] = mat
            self.dirty = True
        if persist:
            self._save_spool_settings()
        return True

    def clear_spool(self, key, persist=True):
        k = str(key)
        with self._lock:
            if k not in self.spools:
                return False
            self.spools[k].update(configured=False, initial_mm=0.0, remaining_mm=0.0)
            self.dirty = True
        if persist:
            self._save_spool_settings()
        return True

    def set_low_filament_threshold(self, grams, persist=True):
        val = max(0.0, float(grams))
        with self._lock:
            prev = self.filament_low_threshold_g
            self.filament_low_threshold_g = val
            self.dirty = True
        if persist:
            try:
                self._save_spool_settings(raise_on_error=True)
            except OSError as exc:
                with self._lock:
                    self.filament_low_threshold_g = prev
                    self.dirty = True
                self.notify(self.tr("Makara ayarı kaydedilemedi: %s") % exc)
                return False
        return True

    def _deduct_filament_usage(self, spool_key, used_mm):
        if used_mm <= 0.0:
            return False
        k = str(spool_key)
        low_warning = None
        with self._lock:
            entry = self.spools.get(k)
            if not entry or not entry.get("configured"):
                return False
            before = float(entry["remaining_mm"])
            after = max(0.0, before - float(used_mm))
            entry["remaining_mm"] = after
            mat = entry["material"]
            thresh = float(self.filament_low_threshold_g)
            rem_g = tracker.mm_to_grams(after, mat)
            if thresh > 0.0 and rem_g <= thresh:
                low_warning = (k, rem_g)
            self.dirty = True
        self._save_spool_settings()
        if low_warning is not None:
            sk, rg = low_warning
            if sk == "external":
                self.notify(self.tr("Düşük filament: Harici makara (%.0f g)") % rg, 6, "warn")
            else:
                self.notify(self.tr("Düşük filament: Yuva %d (%.0f g)") % (int(sk) + 1, rg), 6, "warn")
        return True

    def handle_gcode_response(self, line, filament_used=None):
        """Process a G-code / response line for tool-change filament tracking."""
        with self._lock:
            deductions = self.print_tracker.handle_gcode_response(line, filament_used=filament_used)
            if self.print_tracker.active:
                self._print_session = {
                    "filename": self.print_tracker.filename,
                    "active_tool": self.print_tracker.active_tool,
                    "max_filament": self.print_tracker.total_filament_used,
                }
        for spool_key, used_mm in deductions:
            self._deduct_filament_usage(spool_key, used_mm)
        return deductions

    def check_print_filament_warning(self, filename, meta=None):
        info_meta = meta if meta is not None else (self.meta.get(filename) or {})
        needed_mm = float(info_meta.get("filament_total") or 0.0)
        if needed_mm <= 0.0:
            return None
        key = self.active_spool_key()
        sp = self.spool_info(key)
        if not sp["configured"]:
            return None
        if needed_mm > sp["remaining_mm"]:
            needed_g = tracker.mm_to_grams(needed_mm, sp["material"])
            return {
                "spool": sp,
                "needed_mm": needed_mm,
                "needed_g": needed_g,
                "needed_m": tracker.mm_to_meters(needed_mm),
            }
        return None
