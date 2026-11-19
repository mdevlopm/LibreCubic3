"""
Tests for ERR-03 / ERR-04: toolhead_extrusion_allowed guard and extrusion suppression.

Verifies:
1. Default toolhead_extrusion_allowed is True (the guard is opt-in).
2. Positive E moves via _extruder_move and assist/purge are blocked when False,
   and a single informational log line is emitted.
3. Negative E moves (retraction) remain permitted.
4. Positive E moves are permitted when toolhead_extrusion_allowed is True.
5. In ACE_CHANGE_TOOL flow, no positive E moves are sent to the toolhead when False,
   toolhead purge is set to 0.0, ace_filament_pos remains 'toolhead', and
   "Extrude 100mm" prompt button is omitted.
6. In ACE_CHANGE_TOOL flow, positive E moves are sent when True, purge length is non-zero,
   ace_filament_pos advances to 'nozzle', and "Extrude 100mm" prompt button is present.
7. get_status() contains feed_speed, retract_speed, and toolhead_extrusion_allowed.
"""

import unittest

from tests._fakes import (
    make_manager,
    open_fake_port,
    sent_frames,
    FakeGcmd,
    FakeReactor,
    FakeGcode,
)
import ace.serial_manager as sm_mod
import tests._fakes as _fakes
import ace.commands as commands
import ace.reporting as reporting

sm_mod.termios = _fakes.FakeTermios()


class FakeToolhead:
    def __init__(self):
        self.pos = [0.0, 0.0, 0.0, 0.0]
        self.moves = []

    def get_position(self):
        return list(self.pos)

    def move(self, new_pos, speed):
        self.moves.append((list(new_pos), speed))
        self.pos = list(new_pos)

    def wait_moves(self):
        pass

    def get_kinematics(self):
        return self

    def get_status(self, monotonic_time):
        return {'homed_axes': 'xyz'}


class FakeGcodeMove:
    def reset_last_position(self):
        pass


class ExtrusionGuardTests(unittest.TestCase):
    def build(self, overrides=None, state="standby", save_file=None):
        # The guard is off by default now; these tests exercise it, so default to the locked setting
        overrides = dict({"toolhead_extrusion_allowed": False}, **(overrides or {}))
        self.printer, self.manager = make_manager(overrides, print_state=state, save_file=save_file)
        self.ace = self.manager.instances[0]
        self.mgr = self.ace.serial_mgr
        self.reactor = self.printer.reactor
        self.gcode = self.printer.gcode
        self.ser = open_fake_port(self.mgr)
        self.toolhead = FakeToolhead()
        self.printer.add_object("toolhead", self.toolhead)
        self.printer.add_object("gcode_move", FakeGcodeMove())

    def test_default_config_flag_is_true(self):
        self.printer, self.manager = make_manager(None)
        self.assertTrue(self.manager.toolhead_extrusion_allowed)
        self.assertTrue(self.manager.instances[0].toolhead_extrusion_allowed)

    def test_config_flag_can_be_disabled(self):
        self.build()
        self.assertFalse(self.manager.toolhead_extrusion_allowed)
        self.assertFalse(self.ace.toolhead_extrusion_allowed)

    def test_config_flag_can_be_enabled(self):
        self.build({"toolhead_extrusion_allowed": True})
        self.assertTrue(self.manager.toolhead_extrusion_allowed)
        self.assertTrue(self.ace.toolhead_extrusion_allowed)

    def test_get_status_contains_required_fields(self):
        self.build()
        st_mgr = self.manager.get_status()
        self.assertIn("toolhead_extrusion_allowed", st_mgr)
        self.assertIs(st_mgr["toolhead_extrusion_allowed"], False)
        self.assertIn("feed_speed", st_mgr)
        self.assertIn("retract_speed", st_mgr)

        st_ace = self.ace.get_status()
        self.assertIn("toolhead_extrusion_allowed", st_ace)
        self.assertIs(st_ace["toolhead_extrusion_allowed"], False)
        self.assertIn("feed_speed", st_ace)
        self.assertIn("retract_speed", st_ace)

        st_rep = reporting.get_status(self.manager)
        self.assertIn("toolhead_extrusion_allowed", st_rep)
        self.assertIs(st_rep["toolhead_extrusion_allowed"], False)
        self.assertIn("feed_speed", st_rep)
        self.assertIn("retract_speed", st_rep)

    def test_extruder_move_zero_length(self):
        self.build()
        self.manager._extruder_move(0.0, 300)
        self.assertEqual(len(self.toolhead.moves), 0)
        self.assertTrue(any("Skipping zero-length move" in m for m in self.gcode.info))

    def test_extruder_move_positive_blocked_when_false(self):
        self.build()
        self.manager._extruder_move(50.0, 300)
        self.assertEqual(len(self.toolhead.moves), 0)
        suppressed_msgs = [
            m for m in self.gcode.info
            if "Toolhead extrusion is disabled" in m
        ]
        self.assertEqual(len(suppressed_msgs), 1)

        # Second call does not spam the message again (logged once)
        self.manager._extruder_move(20.0, 300)
        self.assertEqual(len(self.toolhead.moves), 0)
        suppressed_msgs2 = [
            m for m in self.gcode.info
            if "Toolhead extrusion is disabled" in m
        ]
        self.assertEqual(len(suppressed_msgs2), 1)

    def test_extruder_move_negative_allowed_when_false(self):
        self.build()
        self.manager._extruder_move(-40.0, 300)
        self.assertEqual(len(self.toolhead.moves), 1)
        self.assertEqual(self.toolhead.moves[0][0][3], -40.0)

    def test_extruder_move_positive_allowed_when_true(self):
        self.build({"toolhead_extrusion_allowed": True})
        self.manager._extruder_move(50.0, 300)
        self.assertEqual(len(self.toolhead.moves), 1)
        self.assertEqual(self.toolhead.moves[0][0][3], 50.0)

    def test_ace_instance_extruder_move_delegation(self):
        self.build()
        # When false, positive moves via ace._extruder_move are blocked
        self.ace._extruder_move(30.0, 300)
        self.assertEqual(len(self.toolhead.moves), 0)

        # Negative moves via ace._extruder_move are allowed
        self.ace._extruder_move(-25.0, 300)
        self.assertEqual(len(self.toolhead.moves), 1)
        self.assertEqual(self.toolhead.moves[0][0][3], -25.0)

    def test_ace_change_tool_no_positive_e_moves(self):
        self.build()
        # Mock spool ready & toolhead sensor
        self.manager.check_and_wait_for_spool_ready = lambda tool: True
        self.manager.sensors = {}

        # Mock feed to toolhead assist so it completes without hardware
        self.ace.wait_ready = lambda: None
        self.ace._feed_to_toolhead_with_extruder_assist = lambda *args, **kw: None

        # Execute tool change to T0
        commands.cmd_ACE_CHANGE_TOOL(self.manager, FakeGcmd(self.gcode, {}), 0)

        # Verify no positive E moves reached toolhead
        positive_e_moves = [m for m in self.toolhead.moves if m[0][3] > 0]
        self.assertEqual(positive_e_moves, [])

        # Verify post-toolchange macro was called with PURGELENGTH=0.0
        post_scripts = [s for s in self.gcode.scripts if "_ACE_POST_TOOLCHANGE" in s]
        self.assertTrue(len(post_scripts) > 0)
        self.assertIn("PURGELENGTH=0.0", post_scripts[0])
        self.assertIn("PURGED_AMOUNT=0.0", post_scripts[0])

        # Verify filament position is marked as toolhead (not nozzle)
        self.assertEqual(self.ace.state.get("ace_filament_pos"), "toolhead")
        self.assertTrue(any("Toolhead extrusion is disabled" in m for m in self.gcode.info))

    def test_ace_change_tool_positive_e_moves_allowed_when_true(self):
        self.build({"toolhead_extrusion_allowed": True})
        self.manager.check_and_wait_for_spool_ready = lambda tool: True
        self.manager.sensors = {}

        self.ace.wait_ready = lambda: None
        self.ace._feed_to_toolhead_with_extruder_assist = lambda *args, **kw: None

        commands.cmd_ACE_CHANGE_TOOL(self.manager, FakeGcmd(self.gcode, {}), 0)

        positive_e_moves = [m for m in self.toolhead.moves if m[0][3] > 0]
        self.assertTrue(len(positive_e_moves) > 0)
        self.assertEqual(self.toolhead.moves[0][0][3], 22.0)

        post_scripts = [s for s in self.gcode.scripts if "_ACE_POST_TOOLCHANGE" in s]
        self.assertTrue(len(post_scripts) > 0)
        self.assertIn("PURGELENGTH=50.0", post_scripts[0])
        self.assertIn("PURGED_AMOUNT=22.0", post_scripts[0])
        self.assertEqual(self.ace.state.get("ace_filament_pos"), "nozzle")

    def test_ace_change_tool_error_dialog_omits_extrude_button_when_false(self):
        self.build(state="printing", overrides={"toolhead_extrusion_allowed": False})
        self.manager.state.set("ace_current_index", 0)
        self.manager.state.set("ace_filament_pos", "bowden")

        def boom(cur, tgt, **kw):
            raise RuntimeError("load error")
        self.manager.perform_tool_change = boom

        commands.cmd_ACE_CHANGE_TOOL(self.manager, FakeGcmd(self.gcode, {}), 1)
        self.assertFalse(any("Extrude 100mm" in s for s in self.gcode.scripts))
        self.assertTrue(any("Retract 100mm" in s for s in self.gcode.scripts))

    def test_ace_change_tool_error_dialog_includes_extrude_button_when_true(self):
        self.build(state="printing", overrides={"toolhead_extrusion_allowed": True})
        self.manager.state.set("ace_current_index", 0)
        self.manager.state.set("ace_filament_pos", "bowden")

        def boom(cur, tgt, **kw):
            raise RuntimeError("load error")
        self.manager.perform_tool_change = boom

        commands.cmd_ACE_CHANGE_TOOL(self.manager, FakeGcmd(self.gcode, {}), 1)
        self.assertTrue(any("Extrude 100mm" in s for s in self.gcode.scripts))
        self.assertTrue(any("Retract 100mm" in s for s in self.gcode.scripts))

    def test_rev01_runout_prompt_omits_extrude_when_false(self):
        self.build(overrides={"toolhead_extrusion_allowed": False})
        self.manager.runout_monitor._show_runout_prompt(0, 0, 0, "PLA", [255, 0, 0])
        self.assertFalse(any("Extrude" in s for s in self.gcode.scripts))
        self.assertFalse(any("_EXTRUDE" in s for s in self.gcode.scripts))
        self.assertTrue(any("Retract 100mm" in s for s in self.gcode.scripts))
        self.assertTrue(any("Retry T0" in s for s in self.gcode.scripts))
        self.assertTrue(any("action:prompt_show" in s for s in self.gcode.scripts))

    def test_rev01_runout_prompt_includes_extrude_when_true(self):
        self.build(overrides={"toolhead_extrusion_allowed": True})
        self.manager.runout_monitor._show_runout_prompt(0, 0, 0, "PLA", [255, 0, 0])
        self.assertTrue(any("Extrude 100mm" in s for s in self.gcode.scripts))
        self.assertTrue(any("_EXTRUDE" in s for s in self.gcode.scripts))

    def test_rev05_standalone_ace_instance_extruder_move_is_guarded(self):
        from ace.instance import AceInstance
        from ace.config import read_ace_config
        import ace.config as ace_config_mod

        ace_config_mod.ACE_INSTANCES.clear()
        ace_config_mod.INSTANCE_MANAGERS.clear()
        printer = _fakes.FakePrinter()
        toolhead = FakeToolhead()
        printer.add_object("toolhead", toolhead)
        cfg = read_ace_config(_fakes.FakeConfig(printer, {"moonraker_lane_sync_enabled": False, "toolhead_extrusion_allowed": False}))
        inst = AceInstance(0, cfg, printer)

        self.assertFalse(inst.toolhead_extrusion_allowed)
        inst._extruder_move(25.0, 300)
        self.assertEqual(toolhead.moves, [])
        self.assertTrue(any("Toolhead extrusion is disabled" in m for m in printer.gcode.info))

        # Negative (retract) move remains allowed
        inst._extruder_move(-15.0, 300)
        self.assertEqual(len(toolhead.moves), 1)
        self.assertEqual(toolhead.moves[0][0][3], -15.0)

        # Positive move works when toolhead_extrusion_allowed is True
        cfg_allowed = read_ace_config(_fakes.FakeConfig(printer, {
            "moonraker_lane_sync_enabled": False,
            "toolhead_extrusion_allowed": True,
        }))
        inst_allowed = AceInstance(0, cfg_allowed, printer)
        inst_allowed._extruder_move(10.0, 300)
        self.assertEqual(len(toolhead.moves), 2)
        self.assertEqual(toolhead.moves[1][0][3], -5.0)

    def test_rev05_real_feed_to_toolhead_assist_skips_extruder_move_when_false(self):
        from ace.config import SENSOR_TOOLHEAD
        self.build(overrides={"toolhead_extrusion_allowed": False})
        self.ace.wait_ready = lambda: None
        self.ace._disable_feed_assist = lambda s: None
        self.ace._enable_feed_assist = lambda s: None
        self.ace._stop_feed = lambda s: None
        self.ace.feed_filament_with_wait_for_response = lambda s, l, sp: {"code": 0}

        speed_changes = []
        self.ace._change_feed_speed = lambda s, sp: speed_changes.append((s, sp)) or True

        # Toolhead sensor triggers immediately after feed starts
        sensor_reads = [False, True, True]
        self.manager.get_switch_state = lambda sensor: (
            sensor_reads.pop(0) if sensor == SENSOR_TOOLHEAD and sensor_reads else (sensor == SENSOR_TOOLHEAD)
        )
        self.manager.check_and_wait_for_spool_ready = lambda tool: True

        commands.cmd_ACE_CHANGE_TOOL(self.manager, FakeGcmd(self.gcode, {}), 0)
        self.assertEqual(speed_changes, [])
        self.assertEqual([m for m in self.toolhead.moves if m[0][3] > 0], [])
        self.assertEqual(self.ace.state.get("ace_filament_pos"), "toolhead")


if __name__ == "__main__":
    unittest.main()
