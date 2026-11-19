"""Audible alert helper: prefers Klipper beeper pin if configured, else Linux PWM sysfs."""

import os
import threading
import time

DEFAULT_PWM_CHIP = "/sys/class/pwm/pwmchip0"
DEFAULT_PWM_INDEX = 0
KLIPPER_BEEPER_CANDIDATES = (
    ("output_pin beeper", "beeper"),
    ("pwm_cycle_time beeper", "beeper"),
    ("output_pin buzzer", "buzzer"),
    ("pwm_cycle_time buzzer", "buzzer"),
)

# (freq_hz, duration_ms, pause_after_ms)
PATTERNS = {
    "confirm": ((1046, 70, 0),),
    "complete": ((392, 140, 40), (523, 140, 40), (659, 200, 0)),
    "error": ((220, 220, 90), (220, 220, 90), (220, 220, 0)),
    "test": ((1000, 140, 60), (800, 140, 0)),
}


def find_klipper_beeper_pin(settings):
    """Return `(section_name, pin_name, is_pwm)` if printer.cfg defines a beeper pin, else None."""
    if not isinstance(settings, dict):
        return None
    for section, pin_name in KLIPPER_BEEPER_CANDIDATES:
        cfg = settings.get(section)
        if isinstance(cfg, dict):
            is_pwm = section.startswith("pwm_cycle_time") or bool(cfg.get("pwm", False))
            return section, pin_name, is_pwm
    return None


def _write_sysfs(path, value):
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(value))


def _read_sysfs(path, default=""):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return default


def _positive_int(text):
    try:
        return int(str(text).strip()) > 0
    except (TypeError, ValueError):
        return False


class Beeper:
    """Plays short tone patterns without blocking the UI thread."""

    def __init__(self, pwm_chip=DEFAULT_PWM_CHIP, pwm_index=DEFAULT_PWM_INDEX, sleep_fn=None):
        self.pwm_chip = pwm_chip
        self.pwm_index = int(pwm_index)
        self._sleep = sleep_fn or time.sleep
        self._lock = threading.Lock()

    @property
    def pwm_base(self):
        return os.path.join(self.pwm_chip, "pwm%d" % self.pwm_index)

    def pwm_available(self):
        base = self.pwm_base
        enable_path = os.path.join(base, "enable")
        if os.path.exists(enable_path):
            return os.access(enable_path, os.W_OK)
        export_path = os.path.join(self.pwm_chip, "export")
        return os.path.exists(export_path) and os.access(export_path, os.W_OK)

    def method_name(self, settings=None):
        pin = find_klipper_beeper_pin(settings)
        if pin is not None:
            return "Klipper (%s)" % pin[1]
        if self.pwm_available():
            return "PWM (pwmchip%d)" % self.pwm_index
        return "PWM"

    def _ensure_pwm_exported(self):
        base = self.pwm_base
        if os.path.isdir(base):
            return True, False
        export_path = os.path.join(self.pwm_chip, "export")
        if not os.path.exists(export_path):
            return False, False
        try:
            _write_sysfs(export_path, str(self.pwm_index))
            self._sleep(0.05)
        except OSError:
            return False, False
        return os.path.isdir(base), True

    def _set_pwm_note(self, base, freq_hz, duty_pct=40):
        period_path = os.path.join(base, "period")
        duty_path = os.path.join(base, "duty_cycle")
        enable_path = os.path.join(base, "enable")
        if not (os.path.exists(period_path) and os.path.exists(duty_path)):
            return
        raw_p = max(1000, int(round(1000000000.0 / max(1, int(freq_hz)))))
        candidates = (
            ((raw_p + 500) // 1000) * 1000,
            ((raw_p + 5000) // 10000) * 10000,
            400000,
        )
        for period in candidates:
            if period <= 0:
                continue
            duty = max(1, min(period - 1, int(period * duty_pct / 100.0)))
            try:
                _write_sysfs(enable_path, "0")
                _write_sysfs(duty_path, "0")
                _write_sysfs(period_path, str(period))
                _write_sysfs(duty_path, str(duty))
                return
            except OSError:
                continue

    def _play_pwm(self, notes):
        ok, did_export = self._ensure_pwm_exported()
        if not ok:
            return False
        base = self.pwm_base
        enable_path = os.path.join(base, "enable")
        period_path = os.path.join(base, "period")
        duty_path = os.path.join(base, "duty_cycle")
        orig_enable = _read_sysfs(enable_path, "0") or "0"
        orig_period = _read_sysfs(period_path, "")
        orig_duty = _read_sysfs(duty_path, "")
        try:
            for freq, dur_ms, pause_ms in notes:
                self._set_pwm_note(base, freq)
                _write_sysfs(enable_path, "1")
                self._sleep(max(0.01, dur_ms / 1000.0))
                _write_sysfs(enable_path, "0")
                if pause_ms > 0:
                    self._sleep(pause_ms / 1000.0)
            return True
        except OSError:
            return False
        finally:
            # Restoring the old period is best effort (a period of 0 is refused by the kernel with EINVAL) and must
            # never stop the channel from being unexported again.
            try:
                _write_sysfs(enable_path, "0")
                if not did_export and _positive_int(orig_period) and orig_duty and os.path.exists(period_path):
                    _write_sysfs(duty_path, "0")
                    _write_sysfs(period_path, orig_period)
                    _write_sysfs(duty_path, orig_duty)
                if not did_export and orig_enable != "0" and os.path.exists(enable_path):
                    _write_sysfs(enable_path, orig_enable)
            except OSError:
                pass
            if did_export:
                try:
                    unexport = os.path.join(self.pwm_chip, "unexport")
                    if os.path.exists(unexport):
                        _write_sysfs(unexport, str(self.pwm_index))
                except OSError:
                    pass

    @staticmethod
    def _play_klipper(gcode_fn, pin_info, notes):
        _, pin_name, is_pwm = pin_info
        on_val = "0.5" if is_pwm else "1"
        lines = []
        for freq, dur_ms, pause_ms in notes:
            if is_pwm:
                lines.append("SET_PIN PIN=%s VALUE=%s CYCLE_TIME=%.6f" % (pin_name, on_val, 1.0 / max(1, int(freq))))
            else:
                lines.append("SET_PIN PIN=%s VALUE=%s" % (pin_name, on_val))
            lines.append("G4 P%d" % int(dur_ms))
            lines.append("SET_PIN PIN=%s VALUE=0" % pin_name)
            if pause_ms > 0:
                lines.append("G4 P%d" % int(pause_ms))
        try:
            gcode_fn("\n".join(lines))
            return True
        except Exception:
            return False

    def play_sync(self, pattern_name, settings=None, gcode_fn=None):
        notes = PATTERNS.get(pattern_name)
        if not notes:
            return False
        with self._lock:
            pin_info = find_klipper_beeper_pin(settings)
            if pin_info is not None and gcode_fn is not None:
                if self._play_klipper(gcode_fn, pin_info, notes):
                    return True
            return self._play_pwm(notes)

    def play_async(self, pattern_name, settings=None, gcode_fn=None):
        t = threading.Thread(
            target=self.play_sync,
            args=(pattern_name, settings, gcode_fn),
            name="kobra-beep",
            daemon=True,
        )
        t.start()
        return t
