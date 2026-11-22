"""G-code order of the calibration wizards (z-offset, nozzle clean)."""

import unittest

from kobra_screen import wizards


def _script(key):
    return [line for step in wizards.WIZARDS[key].steps for line in (step.enter or "").splitlines()]


class WizardSequenceTests(unittest.TestCase):
    def test_zoffset_heats_bed_wipes_then_measures_without_flipping_the_switch(self):
        lines = _script("zoffset")
        self.assertLess(lines.index("_OK_PARK_PURGE"), lines.index("M140 S60"))  # homes if needed, heats over the chute
        self.assertLess(lines.index("M104 S170"), lines.index("_OK_RETRACT_IF_HOT LENGTH=10"))
        self.assertLess(lines.index("_OK_RETRACT_IF_HOT LENGTH=10"), lines.index("LEVIQ_WIPING"))
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

    def test_clean_homes_before_heating_and_retracts_before_wipe(self):
        lines = _script("clean")
        self.assertLess(lines.index("_OK_PARK_PURGE"), lines.index("M104 S170"))
        self.assertLess(lines.index("M104 S170"), lines.index("_OK_RETRACT_IF_HOT LENGTH=10"))
        self.assertLess(lines.index("_OK_RETRACT_IF_HOT LENGTH=10"), lines.index("NOZZLE_WIPE_SEQUENCE"))
        self.assertEqual(lines[-2:], ["M104 S0", "M106 S0"])

    def test_load_retracts_cools_and_flushes_poop(self):
        last = wizards.WIZARDS["load"].steps[-1]
        lines = last.enter.splitlines()
        self.assertTrue(lines[0].startswith("_OK_RETRACT_IF_HOT"))
        self.assertLess(lines.index(lines[0]), lines.index("M104 S0"))
        self.assertLess(lines.index("M104 S0"), lines.index("M106 S255"))
        self.assertLess(lines.index("M106 S255"), lines.index("FLUSH_POOP"))
        self.assertEqual(lines[-1], "M106 S0")

    def test_unload_cuts_tip_then_pushes_and_retracts_at_controlled_speed(self):
        step = wizards.WIZARDS["unload"].steps[2]
        lines = step.enter.splitlines()
        self.assertTrue(step.blocking)
        self.assertEqual(lines[0], "CUT_TIP")
        self.assertLess(lines.index("CUT_TIP"), lines.index("G1 E5 F200"))
        retract = next(l for l in lines if l.startswith("G1 E-"))
        self.assertLess(lines.index("G1 E5 F200"), lines.index(retract))
        feed = int(retract.split("F", 1)[1])
        self.assertGreaterEqual(feed, 180)
        self.assertLessEqual(feed, 600)
        self.assertIn("M400", lines)

    def test_level_parks_before_heating_with_cancellable_heat_step(self):
        steps = wizards.WIZARDS["level"].steps
        heat, measure = steps[1], steps[2]
        self.assertFalse(heat.blocking)
        self.assertTrue(heat.auto)
        heat_lines = heat.enter.splitlines()
        self.assertEqual(heat_lines[0], "_OK_PARK_PURGE")
        self.assertLess(heat_lines.index("_OK_PARK_PURGE"), heat_lines.index("M140 S60"))
        self.assertLess(heat_lines.index("_OK_PARK_PURGE"), heat_lines.index("M104 S170"))
        self.assertTrue(measure.blocking)
        self.assertEqual(measure.enter, "LEVIQ")

    def test_pid_parks_both_heaters_heats_before_retract_and_targets_230(self):
        steps = wizards.WIZARDS["pid"].steps
        choices = dict(steps[0].choices)
        self.assertIn("Nozul 230°", choices)
        nozzle_lines = steps[1].enter.format(**choices["Nozul 230°"]).splitlines()
        bed_lines = steps[1].enter.format(**choices["Tabla 60°"]).splitlines()

        # Nozzle: park (X278 + Y127, Z>=15 via _OK_PARK_PURGE) -> heat -> retract -> PID 230
        self.assertEqual(nozzle_lines[0], "_OK_PARK_PURGE")
        self.assertIn("G1 Y127 F9600", nozzle_lines)
        heat_idx = next(i for i, l in enumerate(nozzle_lines) if l.startswith("M109 S"))
        retract_idx = next(i for i, l in enumerate(nozzle_lines) if l.startswith("_OK_RETRACT_IF_HOT"))
        pid_idx = nozzle_lines.index("PID_CALIBRATE HEATER=extruder TARGET=230")
        self.assertLess(nozzle_lines.index("G1 Y127 F9600"), heat_idx)
        self.assertLess(heat_idx, retract_idx)
        self.assertLess(retract_idx, pid_idx)

        # Bed: park (X278 + Y127, Z>=15 via _OK_PARK_PURGE) -> PID 60
        self.assertEqual(bed_lines[0], "_OK_PARK_PURGE")
        self.assertIn("G1 Y127 F9600", bed_lines)
        self.assertLess(bed_lines.index("G1 Y127 F9600"), bed_lines.index("PID_CALIBRATE HEATER=heater_bed TARGET=60"))

    def test_manual_extrude_saves_gcode_state_and_waits_m400(self):
        from kobra_screen import screens_home
        from kobra_screen.widgets import Button

        app = _StubApp({"extruder": {"temperature": 210.0}})
        app.idle = True
        app.ready = True
        app.extrude_step = 10
        app.setting = lambda obj, key, default=0: default
        app.when = lambda cond, fn: fn if cond else None

        def collect_buttons(items):
            found = []
            for item in items:
                if isinstance(item, Button):
                    found.append(item)
                for attr in ("rows", "widgets"):
                    children = getattr(item, attr, None)
                    if children:
                        found.extend(collect_buttons(children))
            return found

        btns = {b.text: b for b in collect_buttons(screens_home.control_extra(app))}
        btns["Ekstrüde"].action()
        lines = app.sent[-1].splitlines()
        self.assertEqual(lines[0], "SAVE_GCODE_STATE NAME=_screen_extrude")
        self.assertIn("M83", lines)
        self.assertIn("G1 E10 F300", lines)
        self.assertIn("M400", lines)
        self.assertEqual(lines[-1], "RESTORE_GCODE_STATE NAME=_screen_extrude")
        self.assertLess(lines.index("G1 E10 F300"), lines.index("M400"))


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

    def test_level_moves_on_when_bed_and_nozzle_are_hot(self):
        App, app = self.start("level", 1, {"heater_bed": {"temperature": 40.0}, "extruder": {"temperature": 170.0}})
        self.assertFalse(App.advance_wizard_if_ready(app))
        app.status["heater_bed"]["temperature"] = 59.0
        self.assertTrue(App.advance_wizard_if_ready(app))
        self.assertEqual(app.wizard.index, 2)
        self.assertEqual(wizards.WIZARDS["level"].steps[2].title, "Ölçüm")
        self.assertTrue(app.wizard.running)

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
        step = wizards.WIZARDS["level"].steps[2]
        self.assertEqual(step.enter, "LEVIQ")
        self.assertNotIn("LeviQ çalışıyor", step.text)


if __name__ == "__main__":
    unittest.main()
