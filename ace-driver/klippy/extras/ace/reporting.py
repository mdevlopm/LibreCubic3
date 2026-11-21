"""
User-facing message helpers for the ACE driver (message format contract).

Contract:

* success / information:  ``// ACE[n]: ...``
  Produced by ``gcode.respond_info()`` - Klipper itself prepends ``// ``.
* error that must reach the user:  ``!! ACE[n]: ...``
  Produced by ``gcode.respond_raw("!! ...")``. NOTE: ``respond_info("!! x")``
  would be sent as ``// !! x`` (Klipper prefixes ``// `` to every info line),
  which a client checking ``startswith("!!")`` never matches. ``raise
  gcmd.error(...)`` is also rendered by Klipper as a ``!! ...`` line.

Moonraker stores both kinds in ``gcode_store`` unchanged, and kobra-screen
logs only entries whose message starts with ``!!``.
"""

import copy

ERROR_PREFIX = "!!"

_STATUS_CACHE_MAX = 16
# key -> (sink_ref, raw_status_snapshot, cached_result)
_status_cache = {}


def clear_status_cache():
    """Clear internal status cache (for testing/resets)."""
    _status_cache.clear()


def format_error_line(msg):
    """Return the first line of ``msg`` as a ``!! ...`` console line."""
    text = str(msg).strip()
    first = text.split("\n", 1)[0].strip()
    if first.startswith(ERROR_PREFIX):
        first = first[len(ERROR_PREFIX):].strip()
    return f"{ERROR_PREFIX} {first}"


def emit_error(sink, msg):
    """Send an error line that clients can recognise by its ``!!`` prefix.

    ``sink`` is a Klipper ``gcode`` object or a ``gcmd`` (both expose
    ``respond_raw`` and ``respond_info``). Additional lines of a multi-line
    message are sent first as normal ``// `` info lines (same as Klipper's own
    error reporting) so the ``!!`` line stays a single clean sentence.
    """
    text = str(msg).strip()
    lines = text.split("\n")
    if len(lines) > 1:
        try:
            sink.respond_info("\n".join(lines[1:]))
        except Exception:
            pass
    line = format_error_line(text)
    raw = getattr(sink, "respond_raw", None)
    if callable(raw):
        raw(line)
    else:  # sink without respond_raw (should not happen on real Klipper)
        sink.respond_info(line)


def is_cancelled_response(response):
    """True for the synthetic response given to a request cancelled in queue.

    ``AceSerialManager.send_urgent_request`` drops queued start requests that a
    later stop request supersedes; their callbacks receive ``code=-2,
    msg="CANCELLED"``. That is not a device error and must not be reported as
    one.
    """
    return isinstance(response, dict) and response.get("msg") == "CANCELLED"


def get_status(sink=None):
    """Return status dictionary including feed_speed, retract_speed, and toolhead_extrusion_allowed.

    Optimized for repeated queries: caches per sink so alternating calls for
    ``AceManager`` and ``AceInstance`` do not thrash a single slot, and returns a
    shallow copy without invoking ``copy.deepcopy`` when unchanged.
    """
    target_st = None
    if sink is not None and hasattr(sink, "get_status"):
        try:
            st = sink.get_status()
            if isinstance(st, dict):
                target_st = st
        except Exception:
            pass

    if target_st is None:
        target_st = {
            "feed_speed": 60.0,
            "retract_speed": 50.0,
            "toolhead_extrusion_allowed": False,
        }

    cache_key = None if sink is None else id(sink)
    cached = _status_cache.get(cache_key)
    if cached is not None:
        cached_sink, cached_raw, cached_result = cached
        if cached_sink is sink and cached_raw == target_st:
            return cached_result.copy()

    # Status changed or first call: single deepcopy snapshot and cache result
    if len(_status_cache) >= _STATUS_CACHE_MAX:
        _status_cache.clear()
    raw_snapshot = copy.deepcopy(target_st)
    result = dict(raw_snapshot)
    result.setdefault("feed_speed", 60.0)
    result.setdefault("retract_speed", 50.0)
    result.setdefault("toolhead_extrusion_allowed", False)
    _status_cache[cache_key] = (sink, raw_snapshot, result)
    return result.copy()


# Diagnostic ACE image (tools/ace-fw/patch_diag.py) packs the reset cause into
# get_status.fan_speed; stock firmware reports a constant 7000 (bit 23 clear).
_RESET_FLAG_NAMES = ("pin", "power-on/low-voltage", "software", "watchdog",
                     "window-watchdog", "low-power")
_FAULT_NAMES = ("", "HardFault", "MemManage fault", "BusFault", "UsageFault", "assert")


def decode_reset_report(fan_speed):
    """Return ``(reset_text, fault_text_or_None)`` or None for a stock image."""
    try:
        value = int(fan_speed)
    except (TypeError, ValueError):
        return None
    if value < 0 or not value & (1 << 23) or value >> 30:
        return None
    flags = (value >> 24) & 0x3F
    fault = (value >> 20) & 7
    names = [n for i, n in enumerate(_RESET_FLAG_NAMES) if flags & (1 << i)]
    reset_text = ", ".join(names) if names else "unknown (flags cleared before the app)"
    fault_text = None
    if 0 < fault < len(_FAULT_NAMES):
        fault_text = "%s at 0x%08x" % (_FAULT_NAMES[fault], 0x08000000 | (value & 0xFFFFF))
    return reset_text, fault_text
