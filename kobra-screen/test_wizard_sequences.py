"""G-code order of the calibration wizards (z-offset, nozzle clean)."""

import unittest

from kobra_screen import wizards


def _script(key):
    return [line for step in wizards.WIZARDS[key].steps for line in (step.enter or "").splitlines()]


class WizardSequenceTests(unittest.TestCase):
    def test_zoffset_heats_bed_wipes_then_measures_without_flipping_the_switch(self):
        lines = _script("zoffset")
        self.assertLess(lines.index("_OK_PARK_PURGE"), lines.index("M140 S60"))  # homes if needed, heats over the chute
        self.assertLess(lines.index("M140 S60"), lines.index("LEVIQ_WIPING"))
        self.assertLess(lines.index("LEVIQ_WIPING"), lines.index("LEVIQ_AUTO_ZOFFSET FORCE=1"))
        self.assertFalse(any(line.startswith("LEVIQ_AUTO_ZOFFSET_ON_OFF") for line in lines))
        self.assertEqual(lines[-2:], ["M104 S0", "M140 S0"])
        self.assertEqual(wizards.WIZARDS["zoffset"].cancel, "M104 S0\nM140 S0")

    def test_zoffset_waits_for_bed_and_nozzle(self):
        class App:
            temps = {"heater_bed": 30.0, "extruder": 170.0}

            def get(self, obj, key):
                return self.temps[obj]

            def tr(self, text):
                return text

        app = App()
        wait = wizards.WIZARDS["zoffset"].steps[1].wait
        self.assertFalse(wait(app, {})[0])
        app.temps["heater_bed"] = 59.0
        done, fraction, label = wait(app, {})
        self.assertTrue(done)
        self.assertIn("59°C / 60°C", label)

    def test_clean_homes_before_heating(self):
        lines = _script("clean")
        self.assertLess(lines.index("_OK_PARK_PURGE"), lines.index("M104 S170"))
        self.assertLess(lines.index("M104 S170"), lines.index("NOZZLE_WIPE_SEQUENCE"))


class _StubApp:
    """Just enough of App for wizards.Run and App.advance_wizard_if_ready."""

    def __init__(self, status):
        import threading
        self._lock = threading.RLock()
        self.status = status
        self.modal = None
        self.wizard = None
        self.heating_lock = self.extrusion_lock = False
        self.sent = []
        self.dirty = False

    def get(self, obj, key, default=0):
        value = self.status.get(obj, {}).get(key, default)
        return default if value is None else value

    def tr(self, text):
        return text

    def gcode(self, script):
        self.sent.append(script)
        return True

    def do(self, call, on_error=None):
        self.sent.append("<do>")

    def notify(self, text):
        pass


class AutoAdvanceTests(unittest.TestCase):
    def start(self, key, index, status):
        from kobra_screen.app import App
        app = _StubApp(status)
        app.wizard = wizards.Run(wizards.WIZARDS[key])
        app.wizard.index = index
        return App, app

    def test_zoffset_moves_on_when_bed_and_nozzle_are_hot(self):
        App, app = self.start("zoffset", 1, {"heater_bed": {"temperature": 40.0}, "extruder": {"temperature": 170.0}})
        self.assertFalse(App.advance_wizard_if_ready(app))
        self.assertEqual(app.wizard.index, 1)
        app.status["heater_bed"]["temperature"] = 59.0
        self.assertTrue(App.advance_wizard_if_ready(app))
        self.assertEqual(app.wizard.index, 2)          # "Ölçüm": LEVIQ_WIPING + LEVIQ_AUTO_ZOFFSET started
        self.assertTrue(app.wizard.running)
        self.assertFalse(App.advance_wizard_if_ready(app))   # the blocking step is not skipped

    def test_clean_moves_on_to_wiping_when_hot(self):
        App, app = self.start("clean", 1, {"extruder": {"temperature": 169.0}})
        self.assertTrue(App.advance_wizard_if_ready(app))
        self.assertEqual(wizards.WIZARDS["clean"].steps[app.wizard.index].title, "Silme")

    def test_steps_waiting_for_the_user_never_auto_advance(self):
        # load: after heating the user feeds filament by hand, so İleri stays a button
        App, app = self.start("load", 1, {"extruder": {"temperature": 300.0}})
        app.wizard.params = {"hot": 215}
        self.assertFalse(App.advance_wizard_if_ready(app))
        self.assertEqual(app.wizard.index, 1)

    def test_no_advance_under_a_dialog(self):
        App, app = self.start("zoffset", 1, {"heater_bed": {"temperature": 60.0}, "extruder": {"temperature": 170.0}})
        app.modal = object()
        self.assertFalse(App.advance_wizard_if_ready(app))


class LeviqPhaseTextTests(unittest.TestCase):
    def text(self, phase, **status):
        app = _StubApp(dict({"leviq3": {"phase": phase}}, **status))
        return wizards.leviq_phase_text(app)

    def test_each_phase_has_its_own_text(self):
        self.assertEqual(self.text("wiping"), "Nozul siliniyor")
        self.assertEqual(self.text("cooling"), "Nozul soğutuluyor")
        self.assertEqual(self.text("homing"), "Z ekseni sıfırlanıyor")
        self.assertEqual(self.text("mesh"), "Tabla ağı ölçülüyor")
        self.assertEqual(self.text("zoffset"), "Z-offset ölçülüyor")

    def test_heating_shows_the_bed_while_it_is_behind(self):
        self.assertEqual(self.text("heating", heater_bed={"temperature": 40.0, "target": 60.0}), "Tabla ısınıyor: 40°C / 60°C")
        self.assertEqual(self.text("heating", heater_bed={"temperature": 60.0, "target": 60.0}), "Isıtılıyor")

    def test_unknown_or_missing_phase_falls_back(self):
        self.assertEqual(self.text(""), "Çalışıyor...")
        self.assertEqual(wizards.leviq_phase_text(_StubApp({})), "Çalışıyor...")

    def test_level_step_no_longer_just_says_leviq(self):
        step = wizards.WIZARDS["level"].steps[1]
        self.assertEqual(step.enter, "G28\nLEVIQ")
        self.assertNotIn("LeviQ çalışıyor", step.text)


if __name__ == "__main__":
    unittest.main()
