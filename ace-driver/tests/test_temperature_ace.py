"""Tests for temperature_ace sensor max_temp derivation from max_dryer_temperature."""

import unittest

try:
    from tests import _fakes
    from tests._fakes import make_manager
except ImportError:
    import _fakes
    from _fakes import make_manager

import temperature_ace
from ace import config as ace_config_mod


class TemperatureAceTests(unittest.TestCase):
    def setUp(self):
        ace_config_mod.ACE_INSTANCES.clear()
        ace_config_mod.INSTANCE_MANAGERS.clear()

    def _make_temp_sensor(self, printer, ace_instance, max_dryer_temp=60, config_max_temp=None):
        """Create a TemperatureACE sensor linked to the given ACE instance."""

        class SensorConfig:
            def __init__(self, printer, ace_instance, max_temp):
                self._printer = printer
                self._ace_instance = ace_instance
                self._max_temp = max_temp

            def get_printer(self):
                return self._printer

            def get_name(self):
                return "temperature_ace test_sensor"

            def getint(self, name, default=0):
                if name == "ace_instance":
                    return self._ace_instance
                return default

            def getfloat(self, name, default=0.0):
                if name == "min_temp":
                    return default
                return default

            def get(self, name, default=None):
                if name == "max_temp":
                    return self._max_temp
                return default

            def get_start_args(self):
                return {}

        config = SensorConfig(printer, ace_instance, config_max_temp)
        return temperature_ace.TemperatureACE(config)

    def test_max_temp_derived_from_max_dryer_temperature_default_60(self):
        """Default max_dryer_temperature=60 -> max_temp should be 90 (min 90)."""
        printer, manager = make_manager({"max_dryer_temperature": "60"})
        ace = manager.instances[0]
        sensor = self._make_temp_sensor(printer, 0, max_dryer_temp=60)
        sensor.handle_ready()
        self.assertEqual(sensor.max_temp, 90.0)

    def test_max_temp_derived_from_max_dryer_temperature_80(self):
        """max_dryer_temperature=80 -> max_temp should be 90 (80+10=90)."""
        printer, manager = make_manager({"max_dryer_temperature": "80"})
        ace = manager.instances[0]
        sensor = self._make_temp_sensor(printer, 0, max_dryer_temp=80)
        sensor.handle_ready()
        self.assertEqual(sensor.max_temp, 90.0)

    def test_max_temp_derived_from_max_dryer_temperature_85(self):
        """max_dryer_temperature=85 -> max_temp should be 95 (85+10=95 > 90)."""
        printer, manager = make_manager({"max_dryer_temperature": "85"})
        ace = manager.instances[0]
        sensor = self._make_temp_sensor(printer, 0, max_dryer_temp=85)
        sensor.handle_ready()
        self.assertEqual(sensor.max_temp, 95.0)

    def test_max_temp_explicit_config_overrides_derived(self):
        """Explicit max_temp in config should override derived value when >= max_dryer_temperature+10."""
        printer, manager = make_manager({"max_dryer_temperature": "60"})
        ace = manager.instances[0]
        sensor = self._make_temp_sensor(printer, 0, max_dryer_temp=60, config_max_temp="75")
        sensor.handle_ready()
        self.assertEqual(sensor.max_temp, 75.0)

    def test_setup_minmax_respects_explicit_config(self):
        """setup_minmax should not override explicitly configured max_temp when >= max_dryer_temperature+10."""
        printer, manager = make_manager({"max_dryer_temperature": "60"})
        ace = manager.instances[0]
        sensor = self._make_temp_sensor(printer, 0, max_dryer_temp=60, config_max_temp="75")
        sensor.handle_ready()
        sensor.setup_minmax(0, 100)
        self.assertEqual(sensor.max_temp, 75.0)

    def test_setup_minmax_updates_when_not_explicit(self):
        """setup_minmax should update max_temp when not explicitly configured."""
        printer, manager = make_manager({"max_dryer_temperature": "80"})
        ace = manager.instances[0]
        sensor = self._make_temp_sensor(printer, 0, max_dryer_temp=80, config_max_temp=None)
        sensor.handle_ready()
        self.assertEqual(sensor.max_temp, 90.0)
        sensor.setup_minmax(0, 100)
        self.assertEqual(sensor.max_temp, 100.0)

    def test_max_temp_safety_margin_for_open_kobra_limits_80c(self):
        """When max_dryer_temperature=80 (open_kobra_limits), max_temp > 80 prevents shutdown."""
        printer, manager = make_manager({"max_dryer_temperature": "80"})
        ace = manager.instances[0]
        sensor = self._make_temp_sensor(printer, 0, max_dryer_temp=80)
        sensor.handle_ready()
        self.assertGreater(sensor.max_temp, 80.0)
        self.assertGreaterEqual(sensor.max_temp, 90.0)


if __name__ == "__main__":
    unittest.main()