"""Tests for UI render performance and throttling (ERR-25)."""

import tempfile
import unittest
from unittest import mock

from fake_moonraker import FakePrinter, FakeWifiManager, serve
from kobra_screen.app import App
from kobra_screen.moonraker import MoonrakerClient
from kobra_screen.screens import TemperatureScreen


class TestPerf(unittest.TestCase):
    def setUp(self):
        self.printer = FakePrinter()
        self.httpd = serve(self.printer, simulate=False)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client = MoonrakerClient("127.0.0.1", self.httpd.server_address[1])
        self.addCleanup(self.client.close)
        self.app = App(
            self.client,
            cache_dir=self.tmp.name,
            wifi=FakeWifiManager(),
            rinkhals_home=self.tmp.name,
        )

    def test_refresh_snapshot_once(self):
        """ERR-25: _visual_snapshot must be executed at most once per refresh cycle."""
        with mock.patch.object(self.app, "_visual_snapshot", wraps=self.app._visual_snapshot) as mock_snap:
            self.app.refresh()
            self.assertLessEqual(mock_snap.call_count, 1)

        # Guard check: if self._visual_snapshot_taken: return
        self.app._visual_snapshot_taken = True
        res = self.app._visual_snapshot()
        self.assertIsNone(res)

    def test_temp_throttle_5s(self):
        """ERR-25: TemperatureScreen update interval is 5s and avoids dirty flag when unchanged."""
        screen = TemperatureScreen(self.app)
        self.assertEqual(screen._temp_update_interval, 5.0)

        data1 = {"temp": 200, "target": 200}
        self.assertTrue(screen.update(data1))
        self.assertTrue(screen.dirty)
        screen.dirty = False

        # Throttled within 5 seconds
        data2 = {"temp": 205, "target": 200}
        self.assertFalse(screen.update(data2))
        self.assertFalse(screen.dirty)

        # After interval expired, identical data does not trigger dirty
        screen._last_update_time -= 6.0
        self.assertFalse(screen.update(data1))
        self.assertFalse(screen.dirty)

        # After interval expired, new data updates and marks dirty
        screen._last_update_time -= 6.0
        self.assertTrue(screen.update(data2))
        self.assertTrue(screen.dirty)

        # App-level throttling check
        self.assertEqual(self.app._temp_update_interval, 5.0)


if __name__ == "__main__":
    unittest.main()
