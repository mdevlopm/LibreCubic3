"""System status for the Tools → System status page: host CPU/memory, process shares and MCU load.

Host numbers come from Moonraker's /machine/proc_stats, Klipper's CPU time from its
system_stats object and the MCU figures from the mcu objects. The screen's own share is
read from /proc/self. CPU percentages are deltas between two samples, so the first sample
after opening the page only fills the baseline.

The Kobra 3 MCU firmware has no debug_read command (temperature_mcu fails) and the SoC
thermal zone is disabled, so no temperatures are available.
"""

import os
import time

MCU_OBJECTS = (("mcu", "Ana kart"), ("mcu nozzle_mcu", "Nozul kartı"))
QUERY = {"system_stats": None, "mcu": ["last_stats"], "mcu nozzle_mcu": ["last_stats"]}


def _self_cpu_seconds(proc_root="/proc"):
    try:
        with open(os.path.join(proc_root, "self", "stat")) as f:
            fields = f.read().rsplit(")", 1)[1].split()
        ticks = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
        return (int(fields[11]) + int(fields[12])) / float(ticks)  # utime + stime
    except (OSError, ValueError, IndexError):
        return None


def _self_rss_kb(proc_root="/proc"):
    try:
        with open(os.path.join(proc_root, "self", "status")) as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def mcu_load_percent(stats):
    """Mainsail's MCU load figure: (task_avg + 3 * task_stddev) / 2.5 ms."""
    try:
        return (float(stats["mcu_task_avg"]) + 3.0 * float(stats["mcu_task_stddev"])) / 0.0025 * 100.0
    except (KeyError, TypeError, ValueError):
        return None


class SysInfoSampler:
    """Keeps the previous sample so CPU shares can be computed as rates."""

    def __init__(self, clock=time.monotonic, proc_root="/proc"):
        self._clock = clock
        self._proc_root = proc_root
        self._prev = None  # (t, klippy_cputime, self_cpu)

    def sample(self, proc_stats, objects):
        now = self._clock()
        proc_stats = proc_stats if isinstance(proc_stats, dict) else {}
        objects = objects if isinstance(objects, dict) else {}
        sysstats = objects.get("system_stats") or {}
        klippy_cpu = sysstats.get("cputime")
        self_cpu = _self_cpu_seconds(self._proc_root)

        info = {
            "cpu": (proc_stats.get("system_cpu_usage") or {}).get("cpu"),
            "uptime": proc_stats.get("system_uptime"),
            "cpu_temp": proc_stats.get("cpu_temp"),
            "mem_total_kb": None, "mem_used_kb": None,
            "screen_cpu": None, "screen_rss_kb": _self_rss_kb(self._proc_root),
            "klipper_cpu": None, "moonraker_cpu": None, "moonraker_mem": None,
            "mcus": [],
        }
        mem = proc_stats.get("system_memory") or {}
        if mem.get("total"):
            info["mem_total_kb"] = mem["total"]
            avail = mem.get("available")
            info["mem_used_kb"] = mem["total"] - avail if avail is not None else mem.get("used")
        mstats = proc_stats.get("moonraker_stats") or []
        if mstats:
            last = mstats[-1] or {}
            info["moonraker_cpu"] = last.get("cpu_usage")
            if last.get("memory") is not None:
                info["moonraker_mem"] = "%s %s" % (last.get("memory"), last.get("mem_units", "kB"))

        if self._prev is not None:
            dt = now - self._prev[0]
            if dt > 0.2:
                if klippy_cpu is not None and self._prev[1] is not None:
                    info["klipper_cpu"] = max(0.0, (klippy_cpu - self._prev[1]) / dt * 100.0)
                if self_cpu is not None and self._prev[2] is not None:
                    info["screen_cpu"] = max(0.0, (self_cpu - self._prev[2]) / dt * 100.0)
        self._prev = (now, klippy_cpu, self_cpu)

        for name, label in MCU_OBJECTS:
            stats = (objects.get(name) or {}).get("last_stats") or {}
            if not stats:
                continue
            info["mcus"].append({
                "label": label,
                "load": mcu_load_percent(stats),
                "rtt_ms": float(stats.get("srtt", 0.0)) * 1000.0,
                "retransmit": int(stats.get("bytes_retransmit", 0) or 0),
                "invalid": int(stats.get("bytes_invalid", 0) or 0),
            })
        return info
