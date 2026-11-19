"""
Tests for reporting.py and AceManager speed properties / status reporting.

Verifies:
1. reporting.get_status() contains feed_speed, retract_speed, and toolhead_extrusion_allowed.
2. reporting.get_status(sink) delegates to sink.get_status() and ensures fields exist.
3. AceManager provides config_feed_speed, config_retract_speed, feed_speed, retract_speed,
   and runtime override properties.
4. Overrides affect manager and instance get_status() outputs, while config values remain untouched.
5. Clearing overrides restores default speeds.
6. Error formatting and emit_error helpers function correctly.
"""

import copy
import unittest
from unittest.mock import patch

from tests._fakes import (
    make_manager,
    open_fake_port,
    FakeGcode,
)
from ace.instance import AceInstance
import ace.reporting as reporting


class DummySink:
    def __init__(self, status):
        self._status = dict(status)

    def get_status(self, eventtime=None):
        return dict(self._status)


class ReportingAndSpeedStatusTests(unittest.TestCase):
    def setUp(self):
        self.printer, self.manager = make_manager({
            "feed_speed": 60,
            "retract_speed": 50,
            "toolhead_extrusion_allowed": False,
        })
        self.ace = self.manager.instances[0]
        self.ser = open_fake_port(self.ace.serial_mgr)

    def test_reporting_get_status_default(self):
        st = reporting.get_status()
        self.assertIn("feed_speed", st)
        self.assertIn("retract_speed", st)
        self.assertIn("toolhead_extrusion_allowed", st)
        self.assertEqual(st["feed_speed"], 60.0)
        self.assertEqual(st["retract_speed"], 50.0)
        self.assertIs(st["toolhead_extrusion_allowed"], False)

    def test_reporting_get_status_with_manager(self):
        st = reporting.get_status(self.manager)
        self.assertIn("feed_speed", st)
        self.assertIn("retract_speed", st)
        self.assertIn("toolhead_extrusion_allowed", st)
        self.assertEqual(st["feed_speed"], 60.0)
        self.assertEqual(st["retract_speed"], 50.0)
        self.assertIs(st["toolhead_extrusion_allowed"], False)

    def test_reporting_get_status_with_instance(self):
        st = reporting.get_status(self.ace)
        self.assertIn("feed_speed", st)
        self.assertIn("retract_speed", st)
        self.assertIn("toolhead_extrusion_allowed", st)
        self.assertEqual(st["feed_speed"], 60.0)
        self.assertEqual(st["retract_speed"], 50.0)
        self.assertIs(st["toolhead_extrusion_allowed"], False)

    def test_direct_get_status_fields(self):
        # Manager get_status() exposes feed_speed, retract_speed, and toolhead_extrusion_allowed
        st_mgr = self.manager.get_status()
        self.assertIn("feed_speed", st_mgr)
        self.assertIn("retract_speed", st_mgr)
        self.assertIn("toolhead_extrusion_allowed", st_mgr)
        self.assertEqual(st_mgr["feed_speed"], 60.0)
        self.assertEqual(st_mgr["retract_speed"], 50.0)
        self.assertIs(st_mgr["toolhead_extrusion_allowed"], False)

        # Instance get_status() exposes feed_speed, retract_speed, and toolhead_extrusion_allowed
        st_ace = self.ace.get_status()
        self.assertIn("feed_speed", st_ace)
        self.assertIn("retract_speed", st_ace)
        self.assertIn("toolhead_extrusion_allowed", st_ace)
        self.assertEqual(st_ace["feed_speed"], 60.0)
        self.assertEqual(st_ace["retract_speed"], 50.0)
        self.assertIs(st_ace["toolhead_extrusion_allowed"], False)

        # Unwrapped AceInstance.get_status() also includes these fields
        st_unwrapped = AceInstance.get_status(self.ace)
        self.assertIn("feed_speed", st_unwrapped)
        self.assertIn("retract_speed", st_unwrapped)
        self.assertIn("toolhead_extrusion_allowed", st_unwrapped)
        self.assertEqual(st_unwrapped["feed_speed"], 60.0)
        self.assertEqual(st_unwrapped["retract_speed"], 50.0)
        self.assertIs(st_unwrapped["toolhead_extrusion_allowed"], False)

    def test_reporting_get_status_fills_missing_fields_on_sink(self):
        sink = DummySink({"other": 123})
        st = reporting.get_status(sink)
        self.assertEqual(st["other"], 123)
        self.assertIn("feed_speed", st)
        self.assertIn("retract_speed", st)
        self.assertIn("toolhead_extrusion_allowed", st)

    def test_manager_config_speeds_and_defaults(self):
        self.assertEqual(self.manager.config_feed_speed, 60.0)
        self.assertEqual(self.manager.config_retract_speed, 50.0)
        self.assertEqual(self.manager.feed_speed, 60.0)
        self.assertEqual(self.manager.retract_speed, 50.0)
        self.assertIsNone(self.manager.feed_speed_override)
        self.assertIsNone(self.manager.retract_speed_override)

    def test_manager_runtime_speed_overrides(self):
        # Apply runtime override via property setters
        self.manager.feed_speed = 85.0
        self.manager.retract_speed = 70.0

        self.assertEqual(self.manager.feed_speed, 85.0)
        self.assertEqual(self.manager.retract_speed, 70.0)
        self.assertEqual(self.manager.feed_speed_override, 85.0)
        self.assertEqual(self.manager.retract_speed_override, 70.0)

        # Config speeds remain unchanged
        self.assertEqual(self.manager.config_feed_speed, 60.0)
        self.assertEqual(self.manager.config_retract_speed, 50.0)

        # get_status() reflects overrides
        st_mgr = self.manager.get_status()
        self.assertEqual(st_mgr["feed_speed"], 85.0)
        self.assertEqual(st_mgr["retract_speed"], 70.0)

        st_ace = self.ace.get_status()
        self.assertEqual(st_ace["feed_speed"], 85.0)
        self.assertEqual(st_ace["retract_speed"], 70.0)

        # Clear override by setting to None
        self.manager.feed_speed = None
        self.manager.retract_speed_override = None

        self.assertIsNone(self.manager.feed_speed_override)
        self.assertIsNone(self.manager.retract_speed_override)
        self.assertEqual(self.manager.feed_speed, 60.0)
        self.assertEqual(self.manager.retract_speed, 50.0)

        st_mgr_restored = self.manager.get_status()
        self.assertEqual(st_mgr_restored["feed_speed"], 60.0)
        self.assertEqual(st_mgr_restored["retract_speed"], 50.0)

    def test_format_error_line(self):
        self.assertEqual(reporting.format_error_line("Something went wrong"), "!! Something went wrong")
        self.assertEqual(reporting.format_error_line("!! Already formatted"), "!! Already formatted")
        # Multiline returns first line as !! line
        self.assertEqual(reporting.format_error_line("First error\nDetail 1\nDetail 2"), "!! First error")

    def test_emit_error_multiline(self):
        gcode = FakeGcode()
        reporting.emit_error(gcode, "Failed operation\nExtra detail 1\nExtra detail 2")
        self.assertTrue(any(msg.startswith("!! Failed operation") for msg in gcode.raw))
        self.assertTrue(any("Extra detail 1" in msg for msg in gcode.info))

    def test_is_cancelled_response(self):
        self.assertTrue(reporting.is_cancelled_response({"code": -2, "msg": "CANCELLED"}))
        self.assertFalse(reporting.is_cancelled_response({"code": 0, "msg": "OK"}))
        self.assertFalse(reporting.is_cancelled_response(None))

    def test_consecutive_get_status_does_not_call_deepcopy_when_unchanged(self):
        """ERR-24: Repeated get_status calls return cached/shallow copies without copy.deepcopy."""
        reporting.clear_status_cache()

        # Prime the cache on the first call
        st1 = reporting.get_status(self.ace)
        self.assertIn("feed_speed", st1)

        # On consecutive call without status change, copy.deepcopy must not be called
        with patch("copy.deepcopy") as mock_deepcopy:
            st2 = reporting.get_status(self.ace)
            mock_deepcopy.assert_not_called()
        self.assertEqual(st1, st2)

        # Mutating caller's copy does not corrupt cached data
        st2["feed_speed"] = 999.0
        st3 = reporting.get_status(self.ace)
        self.assertEqual(st3["feed_speed"], 60.0)

        # Standalone call without sink
        reporting.clear_status_cache()
        st_none1 = reporting.get_status()
        with patch("copy.deepcopy") as mock_deepcopy:
            st_none2 = reporting.get_status()
            mock_deepcopy.assert_not_called()
        self.assertEqual(st_none1, st_none2)

        # Direct instance get_status() also caches when _info has not changed
        self.ace.get_status()
        with patch("copy.deepcopy") as mock_deepcopy:
            self.ace.get_status()
            mock_deepcopy.assert_not_called()

        # When status changes, cache updates
        self.manager.feed_speed = 95.0
        st4 = reporting.get_status(self.ace)
        self.assertEqual(st4["feed_speed"], 95.0)

    def test_instance_cached_info_copy_behavior(self):
        """ERR-24: AceInstance uses _cached_info_copy to avoid copy.deepcopy when _info is unchanged."""
        # Ensure clean initial state
        self.ace._cached_info_ref = None
        self.ace._cached_info_copy = None

        # First call populates _cached_info_copy via deepcopy
        with patch("copy.deepcopy", wraps=copy.deepcopy) as mock_deepcopy:
            st1 = self.ace.get_status()
            self.assertTrue(mock_deepcopy.called)
            self.assertIsNotNone(self.ace._cached_info_copy)
            self.assertIs(self.ace._cached_info_ref, self.ace._info)

        # Consecutive call with unchanged _info must NOT call copy.deepcopy
        with patch("copy.deepcopy", wraps=copy.deepcopy) as mock_deepcopy:
            st2 = self.ace.get_status()
            mock_deepcopy.assert_not_called()
        self.assertEqual(st1["status"], st2["status"])

        # Modifying returned status dictionary must NOT corrupt _cached_info_copy
        st2["status"] = "mutated_status"
        self.assertNotEqual(self.ace._cached_info_copy.get("status"), "mutated_status")
        st3 = self.ace.get_status()
        self.assertEqual(st3["status"], "ready")

        # When _info changes to a new object, deepcopy IS called and cache updates
        new_info = dict(self.ace._info)
        new_info["status"] = "busy"
        self.ace._info = new_info

        with patch("copy.deepcopy", wraps=copy.deepcopy) as mock_deepcopy:
            st4 = self.ace.get_status()
            self.assertTrue(mock_deepcopy.called)
        self.assertEqual(st4["status"], "busy")
        self.assertEqual(self.ace._cached_info_copy.get("status"), "busy")

    def test_adaptive_serial_polling_boost_and_idle(self):
        """ERR-24: Verify idle polling drops to 0.25s (4Hz) and boosts to 0.05s (20Hz)."""
        serial_mgr = self.ace.serial_mgr
        serial_mgr._boost_until = 0.0

        # When idle (no data, not printing, no active requests), poll interval is 0.25s
        self.assertEqual(serial_mgr.get_poll_interval(has_data=False), 0.25)
        self.assertEqual(serial_mgr.IDLE_POLL_INTERVAL, 0.25)
        self.assertEqual(serial_mgr.ACTIVE_POLL_INTERVAL, 0.05)

        # _reader returns eventtime + 0.25 when no data is read and idle
        t0 = 1000.0
        next_t = serial_mgr._reader(t0)
        self.assertAlmostEqual(next_t - t0, 0.25)

        # When boost_polling is invoked, interval switches to 0.05s (20Hz)
        serial_mgr.boost_polling(1.0)
        self.assertEqual(serial_mgr.get_poll_interval(has_data=False), 0.05)
        next_t_boost = serial_mgr._reader(t0)
        self.assertAlmostEqual(next_t_boost - t0, 0.05)

        # When data has arrived (has_data=True), interval is boosted to 0.05s
        serial_mgr._boost_until = 0.0
        self.assertEqual(serial_mgr.get_poll_interval(has_data=True), 0.05)

        # During print, polling is always boosted to 0.05s (no latency)
        serial_mgr._boost_until = 0.0
        self.printer.print_stats.state = "printing"
        self.assertTrue(serial_mgr.is_printing())
        self.assertEqual(serial_mgr.get_poll_interval(has_data=False), 0.05)
        next_t_print = serial_mgr._reader(t0)
        self.assertAlmostEqual(next_t_print - t0, 0.05)
        self.printer.print_stats.state = "standby"

        # When active requests exist in queue, polling is boosted to 0.05s
        serial_mgr._boost_until = 0.0
        serial_mgr.send_request({"method": "get_status"}, lambda r: None)
        self.assertTrue(serial_mgr.has_active_requests())
        self.assertEqual(serial_mgr.get_poll_interval(has_data=False), 0.05)


if __name__ == "__main__":
    unittest.main()
