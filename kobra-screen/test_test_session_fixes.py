"""Fixes from the 19 Nov printer test session (start options, stop, wizards)."""

import tempfile
import unittest
from unittest import mock

from kobra_screen.app import App
from kobra_screen.moonraker import MoonrakerError
from kobra_screen import wizards


class FakeClient:
    def __init__(self, macro_status=None, cancel_error=None):
        self.calls = []
        self.macro_status = macro_status
        self.cancel_error = cancel_error

    def query(self, objects):
        self.calls.append(("query", tuple(objects)))
        if "gcode_macro G9111" in objects:
            return {"gcode_macro G9111": self.macro_status or {}}
        return {}

    def gcode(self, script, timeout=300):
        self.calls.append(("gcode", script))

    def print_start(self, path):
        self.calls.append(("print_start", path))

    def print_cancel(self):
        self.calls.append(("print_cancel",))
        if self.cancel_error:
            raise MoonrakerError(self.cancel_error)

    def print_pause(self):
        self.calls.append(("print_pause",))

    def emergency_stop(self, timeout=None):
        self.calls.append(("emergency_stop",))

    def __getattr__(self, name):  # anything else the app polls
        return lambda *a, **k: {}


def make_app(client):
    tmp = tempfile.TemporaryDirectory()
    app = App(client, cache_dir=tmp.name, run_async=lambda fn: fn(), run_urgent=lambda fn: fn())
    app._tmp = tmp
    app.extrusion_lock = False
    app.heating_lock = False
    app.refresh = lambda: None
    return app


class PrintStartOptionsTests(unittest.TestCase):
    def test_options_sent_before_start_and_reset(self):
        client = FakeClient(macro_status={"bed_mesh": 1, "home_z": 1})
        app = make_app(client)
        app.print_bed_mesh = False
        app.print_home_z = True
        self.assertTrue(app.start_print("benchy.gcode"))
        gcodes = [c for c in client.calls if c[0] in ("gcode", "print_start")]
        self.assertEqual(gcodes[0][0], "gcode")
        self.assertIn("VARIABLE=bed_mesh VALUE=0", gcodes[0][1])
        self.assertIn("VARIABLE=home_z VALUE=1", gcodes[0][1])
        self.assertEqual(gcodes[1], ("print_start", "benchy.gcode"))
        self.assertTrue(app.print_bed_mesh and app.print_home_z)

    def test_old_macro_without_options_still_prints(self):
        client = FakeClient(macro_status={"wipe_temp": 170})
        app = make_app(client)
        app.print_bed_mesh = False
        app.start_print("a.gcode")
        self.assertFalse(any(c[0] == "gcode" for c in client.calls))
        self.assertIn(("print_start", "a.gcode"), client.calls)

    def test_option_query_failure_does_not_block_print(self):
        client = FakeClient()
        client.query = mock.Mock(side_effect=MoonrakerError("boom"))
        app = make_app(client)
        app.start_print("a.gcode")
        self.assertIn(("print_start", "a.gcode"), client.calls)

    def test_file_screen_has_both_toggles_on(self):
        from kobra_screen.screens_files import print_options
        app = make_app(FakeClient())
        row = print_options(app)
        toggles = [w.toggle for w in row.widgets]
        self.assertEqual(toggles, [True, True])
        row.widgets[0].action()
        self.assertFalse(app.print_bed_mesh)


class StopTests(unittest.TestCase):
    def test_cancel_timeout_offers_estop_without_sending_it(self):
        client = FakeClient(cancel_error="Moonraker'a ulaşılamıyor: timed out")
        app = make_app(client)
        app.cancel_print()
        self.assertNotIn(("emergency_stop",), client.calls)
        self.assertIsNotNone(app.modal)
        self.assertTrue(app.modal.is_estop)
        app.modal.callback()
        self.assertIn(("emergency_stop",), client.calls)

    def test_cancel_ok_sends_cancel_only(self):
        client = FakeClient()
        app = make_app(client)
        app.cancel_print()
        self.assertIn(("print_cancel",), client.calls)
        self.assertNotIn(("emergency_stop",), client.calls)


class WizardFixTests(unittest.TestCase):
    def _steps(self, key):
        return wizards.WIZARDS[key].steps

    def test_zoffset_heat_label_pairs_bed_with_bed_target(self):
        app = make_app(FakeClient())
        temps = {("heater_bed", "temperature"): 51.0, ("extruder", "temperature"): 170.0}
        app.get = lambda obj, key, default=0: temps.get((obj, key), default)
        done, _, label = wizards.bed_and_nozzle(60, 170)(app, {})
        self.assertFalse(done)
        current, target = label.split(" / ")[0], label.split(" / ")[-1]
        self.assertIn("51", current)
        self.assertEqual(target, "60°C")

    def test_heating_wizards_park_before_heating(self):
        for key in ("load", "unload", "cold_pull", "clean", "zoffset"):
            heat = next(s for s in self._steps(key) if s.enter and "M104 S" in s.enter and "S0" not in s.enter)
            lines = heat.enter.split("\n")
            self.assertEqual(lines[0], wizards.PARK, key)
            self.assertNotIn("G28", lines, key)

    def test_load_retracts_at_the_end(self):
        last = self._steps("load")[-1]
        self.assertTrue(last.enter.startswith("_OK_RETRACT_IF_HOT"))

    def test_pid_parks_only_for_nozzle(self):
        step = self._steps("pid")[1]
        choices = dict(self._steps("pid")[0].choices)
        nozzle = step.enter.format(**choices["Nozul 220°"])
        bed = step.enter.format(**choices["Tabla 60°"])
        self.assertTrue(nozzle.startswith("_OK_PARK_PURGE RETRACT=3\nPID_CALIBRATE HEATER=extruder"))
        self.assertEqual(bed.strip().split("\n")[0], "PID_CALIBRATE HEATER=heater_bed TARGET=60")


if __name__ == "__main__":
    unittest.main()
