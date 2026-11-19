"""Tools → System status: sampler math and page rendering."""

import os
import tempfile
import unittest

from kobra_screen import sysinfo
from test_test_session_fixes import FakeClient, make_app

PROC = {
    "system_cpu_usage": {"cpu": 28.5, "cpu0": 28.5},
    "system_memory": {"total": 217876, "available": 106296, "used": 111580},
    "system_uptime": 9645.4,
    "cpu_temp": None,
    "moonraker_stats": [{"time": 1.0, "cpu_usage": 14.2, "memory": 51000, "mem_units": "kB"}],
}


def objects(cputime):
    stats = {"mcu_task_avg": 6e-06, "mcu_task_stddev": 6e-06, "srtt": 0.001,
             "bytes_retransmit": 9, "bytes_invalid": 0}
    return {"system_stats": {"cputime": cputime, "sysload": 11.8},
            "mcu": {"last_stats": stats}, "mcu nozzle_mcu": {"last_stats": dict(stats, bytes_retransmit=3)}}


class SamplerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, "self"))
        self._write_self(utime=100, stime=50, rss=36572)
        self.t = [100.0]
        self.sampler = sysinfo.SysInfoSampler(clock=lambda: self.t[0], proc_root=self.tmp.name)

    def _write_self(self, utime, stime, rss):
        fields = ["S"] + ["0"] * 10 + [str(utime), str(stime)] + ["0"] * 30
        with open(os.path.join(self.tmp.name, "self", "stat"), "w") as f:
            f.write("123 (python3 -m kobra) " + " ".join(fields))
        with open(os.path.join(self.tmp.name, "self", "status"), "w") as f:
            f.write("Name:\tpython3\nVmRSS:\t   %d kB\n" % rss)

    def test_first_sample_has_host_numbers_and_no_rates(self):
        info = self.sampler.sample(PROC, objects(400.0))
        self.assertEqual(info["cpu"], 28.5)
        self.assertEqual(info["mem_used_kb"], 217876 - 106296)
        self.assertEqual(info["moonraker_cpu"], 14.2)
        self.assertIsNone(info["klipper_cpu"])
        self.assertIsNone(info["screen_cpu"])
        self.assertEqual(info["screen_rss_kb"], 36572)
        self.assertEqual([m["label"] for m in info["mcus"]], ["Ana kart", "Nozul kartı"])
        self.assertAlmostEqual(info["mcus"][0]["load"], (6e-06 + 3 * 6e-06) / 0.0025 * 100)
        self.assertEqual(info["mcus"][1]["retransmit"], 3)

    def test_second_sample_computes_cpu_shares(self):
        self.sampler.sample(PROC, objects(400.0))
        self.t[0] += 2.0
        ticks = os.sysconf("SC_CLK_TCK")
        self._write_self(utime=100 + ticks // 5, stime=50, rss=36572)  # +0.2 s CPU in 2 s
        info = self.sampler.sample(PROC, objects(400.5))  # +0.5 s CPU in 2 s
        self.assertAlmostEqual(info["klipper_cpu"], 25.0)
        self.assertAlmostEqual(info["screen_cpu"], (ticks // 5) / ticks / 2.0 * 100.0)

    def test_missing_sources_do_not_crash(self):
        info = self.sampler.sample(None, None)
        self.assertIsNone(info["cpu"])
        self.assertEqual(info["mcus"], [])


class PageTests(unittest.TestCase):
    def test_page_renders_and_tools_tile_opens_it(self):
        from kobra_screen.screens_tools import sysinfo_page, tools
        app = make_app(FakeClient())
        self.assertIn("Okunuyor...", [w.text for card in sysinfo_page(app, None) for row in card.rows for w in row.widgets
                                      if hasattr(w, "text")])
        app.sysinfo = sysinfo.SysInfoSampler(proc_root="/nonexistent").sample(PROC, objects(1.0))
        rows = sysinfo_page(app, None)
        self.assertTrue(rows)
        tile = next(w for row in tools(app)[0].rows for w in row.widgets if getattr(w, "text", "") == "Sistem izleme")
        opened = []
        app.open = lambda page, *a, **k: opened.append(page)
        tile.action()
        self.assertEqual(opened, ["sysinfo"])


if __name__ == "__main__":
    unittest.main()
