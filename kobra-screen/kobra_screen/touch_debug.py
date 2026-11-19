"""Optional touch counters for finding lost presses (KOBRA_TOUCH_DEBUG=1, off by default).

It counts what the kernel delivers on /dev/input/event0 (BTN_TOUCH down / up, read with a second, non-grabbing
file descriptor) and what LVGL turns it into (PRESSED / RELEASED / CLICKED), and writes a short line to
/tmp/rinkhals/kobra-touch-debug.log whenever the numbers changed.  A kernel press that LVGL never reported
is a "missed press".

A device that one file descriptor has grabbed (EVIOCGRAB) sends events to that descriptor only, so a second reader
would see nothing.  While debugging, LvglScreen therefore does not grab; every reader of the device then sees the
same events and the two counts can be compared.  Nothing here runs unless the variable is set.
"""

import os
import struct
import time

from .touch_cal import parse_raw_evdev_packet

DEFAULT_LOG = "/tmp/rinkhals/kobra-touch-debug.log"
QUIET_SEC = 1.0      # a press is "missed" only after the touch panel was quiet this long (LVGL had its chance)
FLUSH_SEC = 5.0      # at most one line per this time while counts keep changing
EV_KEY = 1
BTN_TOUCH = 330


def enabled(env=None):
    env = os.environ if env is None else env
    return str(env.get("KOBRA_TOUCH_DEBUG", "")).strip().lower() in ("1", "true", "yes", "on")


class TouchDebug:
    def __init__(self, evdev_path="/dev/input/event0", log_path=None, clock=time.monotonic, wall=time.time,
                 open_fn=None, read_fn=None, close_fn=None):
        self.log_path = log_path or os.environ.get("KOBRA_TOUCH_DEBUG_LOG", DEFAULT_LOG)
        self._clock = clock
        self._wall = wall
        self._read = read_fn or os.read
        self._close = close_fn or os.close
        self.packet = struct.calcsize("llHHi")
        self.raw_down = self.raw_up = 0
        self.lv_pressed = self.lv_released = self.lv_clicked = 0
        self.missed = 0
        self._counted_missed_for = 0  # raw_down value up to which missed presses were already evaluated
        self._last_raw = 0.0
        self._last_flush = -1e9
        self._dirty = True
        self.fd = None
        self.error = ""
        try:
            self.fd = (open_fn or (lambda p: os.open(p, os.O_RDONLY | os.O_NONBLOCK)))(evdev_path)
        except OSError as exc:
            self.error = "cannot open %s: %s" % (evdev_path, exc)
        self.log("start touch debug device=%s %s" % (evdev_path, self.error or "raw reader open"))

    def log(self, text):
        line = "%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._wall())), text)
        try:
            parent = os.path.dirname(self.log_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass

    def note_lv(self, code, pressed_code, released_code, clicked_code):
        """Called for every LVGL input event (cheap: three comparisons)."""
        if code == pressed_code:
            self.lv_pressed += 1
        elif code == released_code:
            self.lv_released += 1
        elif code == clicked_code:
            self.lv_clicked += 1
        else:
            return
        self._dirty = True

    def poll(self):
        """Read what the kernel delivered since the last call and write a line when something changed."""
        now = self._clock()
        if self.fd is not None:
            while True:
                try:
                    buf = self._read(self.fd, self.packet * 32)
                except (BlockingIOError, OSError):
                    break
                if not buf:
                    break
                for off in range(0, len(buf) - (len(buf) % self.packet), self.packet):
                    _, _, ev_type, code, value = parse_raw_evdev_packet(buf[off:off + self.packet])
                    if ev_type == EV_KEY and code == BTN_TOUCH:
                        if value:
                            self.raw_down += 1
                        else:
                            self.raw_up += 1
                        self._last_raw = now
                        self._dirty = True
                if len(buf) < self.packet * 32:
                    break
        if (now - self._last_raw) >= QUIET_SEC and self.raw_down > self._counted_missed_for:
            self._counted_missed_for = self.raw_down
            self.missed = max(0, self.raw_down - self.lv_pressed)
            self._dirty = True
        if self._dirty and now - self._last_flush >= FLUSH_SEC:
            self.flush(now)

    def summary(self):
        return ("raw_down=%d raw_up=%d lv_pressed=%d lv_released=%d lv_clicked=%d missed_press=%d"
                % (self.raw_down, self.raw_up, self.lv_pressed, self.lv_released, self.lv_clicked, self.missed))

    def flush(self, now=None):
        self._last_flush = self._clock() if now is None else now
        self._dirty = False
        self.log(self.summary())

    def close(self):
        if self.fd is not None:
            try:
                self._close(self.fd)
            except OSError:
                pass
            self.fd = None
        self.missed = max(0, self.raw_down - self.lv_pressed)
        self.log("stop " + self.summary())
