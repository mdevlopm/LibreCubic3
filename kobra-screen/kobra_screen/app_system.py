"""Limits, system reboot / return-to-stock and Wi-Fi actions mixin for ``App``."""

import os
import subprocess
import time

from . import limits
from .app_models import disable_rinkhals_app
from .moonraker import MoonrakerError
from .network import NetworkError


class _AppSystemMixin:
    def refresh_limits(self):
        try:
            text = self.client.download_config(limits.LIMITS_FILE)
            parsed = limits.parse_limits_cfg(text) if text else {}
            with self._lock:
                self.limits_file_exists = text is not None
                self.limits_overrides = parsed
                self.dirty = True
        except MoonrakerError as exc:
            self.notify(self.tr_error(str(exc)))

    def limit_value(self, section, option):
        with self._lock:
            key = (section.lower(), option.lower())
            spec = limits.LIMITS_BY_KEY.get(key)
            if key in self.limits_overrides:
                return self.limits_overrides[key]
            raw = self.setting(section.lower(), option.lower(), None)
        if raw is None:
            return None
        if spec is not None:
            return limits.coerce_limit_value(spec, raw)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def limit_is_from_config(self, section, option):
        with self._lock:
            key = (section.lower(), option.lower())
            if key in self.limits_overrides:
                return False
            raw = self.setting(section.lower(), option.lower(), None)
        return limits.is_per_instance_value(raw)

    def limits_pending(self):
        with self._lock:
            if self.limits_dirty:
                return True
            items = list(self.limits_overrides.items())
            for (sec, opt), val in items:
                cur = self.setting(sec, opt, None)
                if cur is None:
                    continue
                spec = limits.LIMITS_BY_KEY.get((sec, opt))
                if spec is not None and spec.is_bool:
                    cur_val = limits.parse_bool_value(cur)
                    if cur_val is None or bool(val) != cur_val:
                        return True
                    continue
                try:
                    if abs(float(val) - float(cur)) > 1e-6:
                        return True
                except (TypeError, ValueError):
                    return True
            return False

    def restart_klipper(self):
        with self._lock:
            prev_content = self._pending_rollback_prev
            self._pending_rollback_prev = None
            if prev_content is not None:
                self._limits_rollback = (prev_content, time.monotonic() + self.rollback_timeout)
        self.client.firmware_restart()
        with self._lock:
            self.state.settings = {}
            self.limits_dirty = False
            self.dirty = True

    def _rollback_limits(self, prev_content):
        try:
            content = prev_content if prev_content is not None else limits.dump_limits_cfg({})
            self.client.upload_config(limits.LIMITS_FILE, content)
            parsed = limits.parse_limits_cfg(content) if content else {}
            with self._lock:
                self.limits_overrides = parsed
                self.limits_file_exists = bool(parsed)
                self.limits_dirty = False
                self.state.settings = {}
                self._pending_rollback_prev = None
                self.dirty = True
            self.client.firmware_restart()
            self.refresh()
            self.notify(self.tr("Geçersiz sınır nedeniyle önceki ayarlara dönüldü"), 6, "outline_danger")
        except MoonrakerError as exc:
            self.notify(self.tr_error(str(exc)))
        finally:
            with self._lock:
                self._rolling_back = False

    def save_limits(self, updates):
        with self._lock:
            if self.printing:
                return False
            merged = dict(self.limits_overrides)
            base_settings = dict(self.state.settings)
        for (sec, opt), val in updates.items():
            key = (sec.lower(), opt.lower())
            if val is None:
                merged.pop(key, None)
            else:
                spec = limits.LIMITS_BY_KEY.get(key)
                norm = limits.coerce_limit_value(spec, val) if spec is not None else float(val)
                if norm is not None:
                    merged[key] = norm
        errors = limits.validate_limits(merged, base_settings, lang=self.lang)
        if errors:
            self.notify(errors[0])
            return False
        content = limits.dump_limits_cfg(merged)

        def write():
            prev_raw = self.client.download_config(limits.LIMITS_FILE)
            self.client.upload_config(limits.LIMITS_FILE, content)
            with self._lock:
                if self._pending_rollback_prev is None:
                    self._pending_rollback_prev = prev_raw if prev_raw is not None else limits.dump_limits_cfg({})
                self.limits_overrides = merged
                self.limits_file_exists = bool(merged)
                self.limits_dirty = True
                self.confirm(
                    self.tr("Sınır kaydedildi. Etkili olması için Klipper yeniden başlatılsın mı?"),
                    lambda: self.do(self.restart_klipper, self.tr("Klipper yeniden başlatıldı")),
                    "primary",
                )

        self.do(write)
        return True

    def reset_limits(self):
        with self._lock:
            if self.printing:
                return

        def clear():
            self.client.upload_config(limits.LIMITS_FILE, limits.dump_limits_cfg({}))
            with self._lock:
                self.limits_overrides = {}
                self.limits_file_exists = False
                self.limits_dirty = True
                self._pending_rollback_prev = None
                self._limits_rollback = None
                self.confirm(
                    self.tr("Özel sınırlar temizlendi. Varsayılanlar için Klipper yeniden başlatılsın mı?"),
                    lambda: self.do(self.restart_klipper, self.tr("Klipper yeniden başlatıldı")),
                    "primary",
                )

        self.do(clear)

    def recover_limits_and_restart(self):
        with self._lock:
            if self.printing:
                return
            self._pending_rollback_prev = None
            self._limits_rollback = None

        def recover():
            self.client.upload_config(limits.LIMITS_FILE, limits.dump_limits_cfg({}))
            with self._lock:
                self.limits_overrides = {}
                self.limits_file_exists = False
                self.limits_dirty = False
                self.state.settings = {}
                self.dirty = True
            self.client.firmware_restart()

        self.do(recover, self.tr("Sınırlar sıfırlandı ve Klipper yeniden başlatıldı"))

    @staticmethod
    def _system_reboot():
        if hasattr(os, "sync"):
            try:
                os.sync()
            except OSError:
                pass
        rinkhals_reboot = os.path.join(
            os.environ.get("RINKHALS_ROOT", "/useremain/rinkhals/.current"),
            "usr/bin/reboot",
        )
        cmd = [rinkhals_reboot] if os.path.isfile(rinkhals_reboot) and os.access(rinkhals_reboot, os.X_OK) else ["reboot"]
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _trigger_stock_reboot(self):
        try:
            if self._reboot_fn is not None:
                self._reboot_fn()
            else:
                try:
                    self.client.machine_reboot()
                except MoonrakerError:
                    self._system_reboot()
        except Exception as exc:
            self.notify(self.tr_error("Yeniden başlatılamadı: %s" % exc))
            return
        if self._on_return_to_stock is not None:
            self._on_return_to_stock()

    def return_to_stock(self):
        if self.printing:
            self.notify(self.tr("Baskı sırasında stok moda geçilemez"))
            return
        targets = [self.app_name]
        if self.app_name != "vanilla-klipper":
            targets.append("vanilla-klipper")
        try:
            for app_id in targets:
                disable_rinkhals_app(
                    app_name=app_id,
                    rinkhals_home=self.rinkhals_home,
                    app_root=self.app_root if app_id == self.app_name else None,
                )
        except OSError as exc:
            self.notify(self.tr_error("Uygulama devre dışı bırakılamadı: %s" % exc))
            return
        with self._lock:
            self.stock_requested = True
            self.dirty = True
        stop_marker = os.environ.get("KOBRA_STOP_MARKER")
        if stop_marker:
            try:
                with open(stop_marker, "w", encoding="utf-8"):
                    pass
            except OSError:
                pass
        self.notify(self.tr("Stok moda geçiliyor, yeniden başlatılıyor..."), 3, "active")
        self.run_async(self._trigger_stock_reboot)

    def refresh_wifi(self, scan=False):
        with self._lock:
            if scan:
                self.wifi_scanning = True
            self.dirty = True
        try:
            if scan:
                try:
                    nets = self.wifi.scan()
                except NetworkError as exc:
                    self.notify(self.tr_error(str(exc)))
                    nets = self.wifi.scan_results()
            else:
                nets = self.wifi.scan_results()
            status = self.wifi.get_status()
            with self._lock:
                self.wifi_status = status
                self.wifi_networks = list(nets)
                self.dirty = True
        except NetworkError as exc:
            self.notify(self.tr_error(str(exc)))
        finally:
            with self._lock:
                self.wifi_scanning = False
                self.dirty = True

    def refresh_wifi_status(self):
        try:
            status = self.wifi.get_status()
            with self._lock:
                if status != self.wifi_status:
                    self.wifi_status = status
                    self.dirty = True
        except NetworkError:
            pass

    def connect_wifi(self, ssid, password=None, flags=""):
        def work():
            with self._lock:
                self.busy += 1
                self.dirty = True
            try:
                self.wifi.connect(ssid, password=password, flags=flags)
                status = self.wifi.get_status()
                nets = self.wifi.scan_results()
                with self._lock:
                    self.wifi_status = status
                    self.wifi_networks = list(nets)
                    self.dirty = True
                self.notify(self.tr("%s ağına bağlanılıyor") % ssid, 4, "active")
            except NetworkError as exc:
                self.notify(self.tr_error(str(exc)))
            finally:
                with self._lock:
                    self.busy -= 1
                    self.dirty = True

        self.run_async(work)

    def forget_wifi(self, ssid):
        def work():
            with self._lock:
                self.busy += 1
                self.dirty = True
            try:
                self.wifi.forget(ssid)
                status = self.wifi.get_status()
                nets = self.wifi.scan_results()
                with self._lock:
                    self.wifi_status = status
                    self.wifi_networks = list(nets)
                    self.dirty = True
                self.notify(self.tr("%s ağı unutuldu") % ssid, 3, "active")
            except NetworkError as exc:
                self.notify(self.tr_error(str(exc)))
            finally:
                with self._lock:
                    self.busy -= 1
                    self.dirty = True

        self.run_async(work)
