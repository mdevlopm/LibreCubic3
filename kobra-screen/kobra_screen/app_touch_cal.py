"""Touch calibration wizard mixin for ``App``."""

import math

from . import touch_cal
from .app_models import TouchCalibration
from .widgets import HEIGHT, WIDTH


class _AppTouchCalMixin:
    def _touch_state(self):
        if self._touch_backend is not None and hasattr(self._touch_backend, "get_state"):
            return self._touch_backend.get_state()
        cal, swap = touch_cal.model_default_calibration()
        return cal, swap, 270

    def _touch_apply(self, cal, swap_axes):
        if self._touch_backend is not None and hasattr(self._touch_backend, "apply"):
            self._touch_backend.apply(cal, swap_axes)

    def _touch_set_raw(self, enabled):
        if self._touch_backend is not None and hasattr(self._touch_backend, "set_raw"):
            self._touch_backend.set_raw(bool(enabled))

    def _touch_save(self, cal, swap_axes):
        if self._touch_backend is not None and hasattr(self._touch_backend, "save"):
            return self._touch_backend.save(cal, swap_axes)
        return touch_cal.save_touch_config(cal, swap_axes)

    def _touch_reset(self):
        if self._touch_backend is not None and hasattr(self._touch_backend, "reset"):
            return self._touch_backend.reset()
        return touch_cal.reset_touch_config()

    def open_touch_calibration(self, confirm_timeout=touch_cal.CONFIRM_TIMEOUT_SEC,
                               capture_timeout=touch_cal.CAPTURE_TIMEOUT_SEC,
                               verify_threshold=touch_cal.DEFAULT_VERIFY_THRESHOLD_PX):
        cur_cal, cur_swap, lv_rot = self._touch_state()
        corners, center = touch_cal.calibration_targets(WIDTH, HEIGHT)
        with self._lock:
            self.touch_cal = TouchCalibration(
                stage="intro",
                targets=corners,
                center_target=center,
                prev_cal=tuple(cur_cal),
                prev_swap=bool(cur_swap),
                new_cal=tuple(cur_cal),
                new_swap=bool(cur_swap),
                lv_rot=int(lv_rot),
                verify_threshold=float(verify_threshold),
                confirm_timeout=float(confirm_timeout),
                capture_timeout=float(capture_timeout),
            )
            self.dirty = True

    def start_touch_calibration(self):
        with self._lock:
            cal = self.touch_cal
            if cal is None:
                return
            cur_cal, cur_swap, lv_rot = self._touch_state()
            cal.prev_cal = tuple(cur_cal)
            cal.prev_swap = bool(cur_swap)
            cal.lv_rot = int(lv_rot)
            cal.stage = "corners"
            cal.step = 0
            cal.raw_points = []
            cal.verify_error_px = None
            cal.error_msg = ""
            cal.deadline = self._clock() + cal.capture_timeout
            self.dirty = True
        self._touch_set_raw(True)

    def record_calibration_tap(self, rx, ry):
        with self._lock:
            cal = self.touch_cal
            if cal is None or cal.stage != "corners":
                return False
            cal.raw_points.append((int(rx), int(ry)))
            cal.deadline = self._clock() + cal.capture_timeout
            if len(cal.raw_points) < len(cal.targets):
                cal.step = len(cal.raw_points)
                self.dirty = True
                return True
            targets_xy = [(tx, ty) for _, tx, ty in cal.targets]
            raw_pts = list(cal.raw_points)
            lv_rot = cal.lv_rot
        self._touch_set_raw(False)
        try:
            new_cal, new_swap = touch_cal.compute_calibration(targets_xy, raw_pts, WIDTH, HEIGHT, lv_rot=lv_rot)
        except ValueError as exc:
            with self._lock:
                if self.touch_cal is not None:
                    self.touch_cal.stage = "intro"
                    self.touch_cal.step = 0
                    self.touch_cal.raw_points = []
                    self.touch_cal.error_msg = self.tr(str(exc))
                    self.dirty = True
            return False
        self._touch_apply(new_cal, new_swap)
        with self._lock:
            if self.touch_cal is not None:
                self.touch_cal.new_cal = new_cal
                self.touch_cal.new_swap = new_swap
                self.touch_cal.stage = "verify"
                self.touch_cal.deadline = self._clock() + self.touch_cal.capture_timeout
                self.dirty = True
        return True

    def record_calibration_verify(self, px, py):
        with self._lock:
            cal = self.touch_cal
            if cal is None or cal.stage != "verify":
                return False
            _, cx, cy = cal.center_target
            err = math.hypot(float(px) - float(cx), float(py) - float(cy))
            prev_cal, prev_swap = cal.prev_cal, cal.prev_swap
            threshold = cal.verify_threshold
        if err > threshold:
            self._touch_apply(prev_cal, prev_swap)
            with self._lock:
                if self.touch_cal is not None:
                    self.touch_cal.stage = "intro"
                    self.touch_cal.step = 0
                    self.touch_cal.raw_points = []
                    self.touch_cal.verify_error_px = err
                    self.touch_cal.error_msg = self.tr(
                        "Sapma yüksek (%.0f px > %.0f px). Tekrar deneyin."
                    ) % (err, threshold)
                    self.dirty = True
            return False
        with self._lock:
            if self.touch_cal is not None:
                self.touch_cal.verify_error_px = err
                self.touch_cal.stage = "confirm"
                self.touch_cal.deadline = self._clock() + self.touch_cal.confirm_timeout
                self.touch_cal.last_rem_sec = int(math.ceil(self.touch_cal.confirm_timeout))
                self.dirty = True
        return True

    def confirm_touch_calibration(self):
        with self._lock:
            cal = self.touch_cal
            if cal is None or cal.stage != "confirm":
                return False
            new_cal, new_swap = cal.new_cal, cal.new_swap
        try:
            self._touch_save(new_cal, new_swap)
        except OSError as exc:
            self.notify(self.tr_error("Kalibrasyon kaydedilemedi: %s" % exc))
            return False
        with self._lock:
            self.touch_cal = None
            self.dirty = True
        self.notify(self.tr("Dokunmatik kalibrasyon kaydedildi"), 4, "active")
        return True

    def cancel_touch_calibration(self, auto_expired=False):
        with self._lock:
            cal = self.touch_cal
            if cal is None:
                return
            stage = cal.stage
            prev_cal, prev_swap = cal.prev_cal, cal.prev_swap
            self.touch_cal = None
            self.dirty = True
        if stage == "corners":
            self._touch_set_raw(False)
        if stage in ("verify", "confirm"):
            self._touch_apply(prev_cal, prev_swap)
        if auto_expired:
            self.notify(self.tr("Onaylanmadı, önceki kalibrasyona dönüldü"), 5, "outline_danger")

    def reset_touch_calibration_to_default(self):
        try:
            default_cal, default_swap = self._touch_reset()
        except OSError as exc:
            self.notify(self.tr_error("Varsayılan kalibrasyon yazılamadı: %s" % exc))
            return False
        self._touch_apply(default_cal, default_swap)
        with self._lock:
            self.touch_cal = None
            self.dirty = True
        self.notify(self.tr("Varsayılan kalibrasyona dönüldü"), 4, "active")
        return True

    def tick_calibration(self):
        expired = False
        with self._lock:
            cal = self.touch_cal
            if cal is None:
                return False
            now = self._clock()
            if cal.stage in ("corners", "verify", "confirm") and cal.deadline > 0 and now >= cal.deadline:
                expired = True
            elif cal.stage == "confirm":
                rem = max(0, int(math.ceil(cal.deadline - now)))
                if rem != cal.last_rem_sec:
                    cal.last_rem_sec = rem
                    self.dirty = True
        if expired:
            self.cancel_touch_calibration(auto_expired=True)
            return True
        return False
