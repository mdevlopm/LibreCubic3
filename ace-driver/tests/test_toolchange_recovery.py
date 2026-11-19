"""
Unit tests for ERR-09 / C-21:
Pre-toolchange state restoration (Z-hop and fan speed) on toolchange failure.

Verifies:
1. When a toolchange fails during active printing, RESTORE_GCODE_STATE NAME=purge_state MOVE=0
   and fan speed restoration (M106 S{saved_fan_pwm}) are executed BEFORE PAUSE is triggered.
2. The pre-toolchange last_position in gcode_move is restored from purge_state so that
   PAUSE_STATE does not capture a corrupted Z-hop or throw position.
3. Idle and startup toolchange failures do not execute print PAUSE or corrupt recovery state.
4. Standalone state restoration helper function correctly restores fan and axis positions.
"""

import unittest
from unittest.mock import MagicMock

from tests._fakes import (
    make_manager,
    open_fake_port,
    FakeGcmd,
    FakeReactor,
    FakeGcode,
)
import ace.serial_manager as sm_mod
import tests._fakes as _fakes
import ace.commands as commands

sm_mod.termios = _fakes.FakeTermios()


class FakeGcodeMove:
    def __init__(self, initial_pos=None):
        self.last_position = list(initial_pos or [0.0, 0.0, 0.0, 0.0])
        self.saved_states = {}

    def reset_last_position(self):
        pass


class FakeMacroState:
    def __init__(self, variables=None):
        self.variables = dict(variables or {})


class FakeFan:
    def __init__(self, speed=0.0):
        self.speed = float(speed)
        self.speed_history = [float(speed)]

    def get_status(self, monotonic_time=0.0):
        return {'speed': self.speed}

    def set_speed(self, speed):
        self.speed = float(speed)
        self.speed_history.append(float(speed))


class ToolchangeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.printer, self.manager = make_manager(print_state="printing")
        self.gcode = self.printer.gcode
        self.reactor = self.printer.reactor
        self.ace = self.manager.instances[0]
        self.ser = open_fake_port(self.ace.serial_mgr)

    def test_state_restored_before_pause_on_toolchange_failure(self):
        # Setup pre-toolchange conditions:
        # Fan had been running at 180 PWM (saved in _ACE_STATE.pre_toolchange_fan)
        # purge_state had saved the print layer position (Z = 5.0 mm)
        # But after PRE macro, toolhead Z-hopped to 7.0 mm at throw position [250, 20, 7.0, 10.0]
        ace_state = FakeMacroState({'pre_toolchange_fan': 180, 'active': 0})
        self.printer.add_object('gcode_macro _ACE_STATE', ace_state)

        initial_print_pos = [100.0, 100.0, 5.0, 10.0]
        hopped_throw_pos = [250.0, 20.0, 7.0, 10.0]
        gcode_move = FakeGcodeMove(initial_pos=hopped_throw_pos)
        gcode_move.saved_states['purge_state'] = {
            'last_position': list(initial_print_pos),
            'base_position': [0.0, 0.0, 0.0, 0.0],
            'absolute_coord': True,
            'absolute_extrude': True,
        }
        self.printer.add_object('gcode_move', gcode_move)

        fan = FakeFan(speed=0.0)  # Fan was turned off (M106 S0) by _ACE_PRE_TOOLCHANGE
        self.printer.add_object('fan', fan)

        self.manager.state.set("ace_current_index", 0)
        self.manager.state.set("ace_filament_pos", "nozzle")

        # Simulate toolchange failure after PRE macro has executed
        def fail_toolchange(cur, tgt):
            self.manager._pre_toolchange_done = True
            raise RuntimeError("Filament jammed in path")

        self.manager.perform_tool_change = fail_toolchange

        # Execute tool change to T1
        gcmd = FakeGcmd(self.gcode, {})
        commands.cmd_ACE_CHANGE_TOOL(self.manager, gcmd, 1)

        # Verify scripts executed
        scripts = self.gcode.scripts
        self.assertIn("M106 S180", scripts)
        self.assertIn("RESTORE_GCODE_STATE NAME=purge_state MOVE=0", scripts)
        self.assertIn("PAUSE", scripts)

        # Verify strict order: fan and purge_state restoration MUST precede PAUSE
        fan_restore_idx = scripts.index("M106 S180")
        purge_restore_idx = scripts.index("RESTORE_GCODE_STATE NAME=purge_state MOVE=0")
        pause_idx = scripts.index("PAUSE")

        self.assertLess(fan_restore_idx, pause_idx, "Fan restoration must occur before PAUSE")
        self.assertLess(purge_restore_idx, pause_idx, "purge_state restoration must occur before PAUSE")

        # Verify gcode_move last_position was restored from purge_state
        self.assertEqual(gcode_move.last_position[:3], initial_print_pos[:3])

        # Verify fan speed object was updated
        self.assertAlmostEqual(fan.speed, 180 / 255.0, places=2)

    def test_state_restored_using_manager_tracked_fan_when_macro_variable_missing(self):
        # When _ACE_STATE macro object does not have pre_toolchange_fan,
        # fallback to fan speed captured by manager before _ACE_PRE_TOOLCHANGE
        self.printer.objects.pop('gcode_macro _ACE_STATE', None)

        fan = FakeFan(speed=0.0)
        self.printer.add_object('fan', fan)

        gcode_move = FakeGcodeMove([250.0, 20.0, 8.0, 0.0])
        gcode_move.saved_states['purge_state'] = {
            'last_position': [50.0, 50.0, 6.0, 0.0],
        }
        self.printer.add_object('gcode_move', gcode_move)

        self.manager._pre_toolchange_fan_pwm = 200

        def fail_toolchange(cur, tgt):
            self.manager._pre_toolchange_done = True
            raise RuntimeError("Feed failed")

        self.manager.perform_tool_change = fail_toolchange

        gcmd = FakeGcmd(self.gcode, {})
        commands.cmd_ACE_CHANGE_TOOL(self.manager, gcmd, 1)

        scripts = self.gcode.scripts
        self.assertIn("M106 S200", scripts)
        self.assertIn("RESTORE_GCODE_STATE NAME=purge_state MOVE=0", scripts)
        self.assertIn("PAUSE", scripts)
        self.assertEqual(gcode_move.last_position[:3], [50.0, 50.0, 6.0])

    def test_idle_failure_does_not_pause_print(self):
        # When not printing (standby), failure does not execute PAUSE
        self.printer.print_stats.state = "standby"

        def fail_toolchange(cur, tgt):
            raise RuntimeError("Motor jammed")

        self.manager.perform_tool_change = fail_toolchange

        gcmd = FakeGcmd(self.gcode, {})
        with self.assertRaises(Exception):
            commands.cmd_ACE_CHANGE_TOOL(self.manager, gcmd, 1)

        self.assertNotIn("PAUSE", self.gcode.scripts)
        self.assertIn("M104 S0", self.gcode.scripts)

    def test_startup_toolchange_failure_does_not_pause(self):
        # During startup toolchange (G9111, startup_toolchange=1), do not pause
        ace_state = FakeMacroState({'startup_toolchange': 1})
        self.printer.add_object('gcode_macro _ACE_STATE', ace_state)

        def fail_toolchange(cur, tgt):
            raise RuntimeError("Startup fail")

        self.manager.perform_tool_change = fail_toolchange

        gcmd = FakeGcmd(self.gcode, {})
        with self.assertRaises(Exception):
            commands.cmd_ACE_CHANGE_TOOL(self.manager, gcmd, 1)

        self.assertNotIn("PAUSE", self.gcode.scripts)

    def test_standalone_restore_toolchange_state_with_dict(self):
        # Verify standalone helper _restore_toolchange_state with mock toolhead and fan
        toolhead = MagicMock()
        toolhead.manual_move = MagicMock()
        toolhead.set_extruder_allowed = MagicMock()
        self.printer.add_object('toolhead', toolhead)

        fan = MagicMock()
        fan.set_speed = MagicMock()
        self.printer.add_object('fan', fan)

        test_state = {
            'z_hop': 15.0,
            'fan_speed': 0.75,
            'toolhead_extrusion_allowed': False,
        }

        # Standalone function call
        commands._restore_toolchange_state(test_state, printer=self.printer)

        toolhead.manual_move.assert_called_once_with([None, None, 15.0], 5.0)
        fan.set_speed.assert_called_once_with(0.75)
        toolhead.set_extruder_allowed.assert_called_once_with(False)


if __name__ == '__main__':
    unittest.main()
