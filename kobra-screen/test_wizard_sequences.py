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


if __name__ == "__main__":
    unittest.main()
