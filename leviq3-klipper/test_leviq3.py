"""Tests for leviq3.py against a small stand-in for Klipper and the printer.

The stand-in models the physics that matter: `world_z` is the real nozzle
height (0 = touching the bed without load, negative = pressed into it), the
strain gauge reads in proportion to the load, and homing stops at a fixed
load.  Run: python3 -m unittest -v
"""

import collections
import json
import os
import random
import sys
import tempfile
import unittest

import leviq3

PRESS = 0.20  # mm the nozzle is pressed into the bed when homing triggers
COUNTS_PER_MM = 100000.


class CommandError(Exception):
    pass


class Completion:
    def __init__(self):
        self.result = None

    def complete(self, result):
        self.result = result

    def wait(self, waketime):
        return self.result


class Reactor:
    def __init__(self):
        self.now = 0.

    def monotonic(self):
        self.now += 0.001
        return self.now

    def completion(self):
        return Completion()

    def pause(self, waketime):
        self.now = max(self.now, waketime)
        return self.now

    def mutex(self):
        class _DummyMutex:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return _DummyMutex()


class Rail:
    def __init__(self, position_endstop):
        self.position_endstop = position_endstop


class Kinematics:
    def __init__(self, z_offset):
        self.rails = [Rail(0.), Rail(0.), Rail(z_offset)]


class Toolhead:
    def __init__(self, printer, z_offset):
        self.printer = printer
        self.kin = Kinematics(z_offset)
        self.pos = [0., 0., 0., 0.]
        self.frame = 10.  # world_z - toolhead z
        self.homed = ''

    world_z = property(lambda self: self.pos[2] + self.frame)

    def get_kinematics(self):
        return self.kin

    def get_status(self, eventtime):
        return {'homed_axes': self.homed}

    def get_position(self):
        return list(self.pos)

    def set_position(self, newpos, homing_axes=""):
        world = self.world_z
        self.pos = list(newpos)
        self.frame = world - self.pos[2]
        self.printer.lookup_object('gcode_move').reset_last_position()

    def wait_moves(self):
        pass


class GCodeMove:
    """Offset bookkeeping copied from Klipper's gcode_move."""

    def __init__(self, printer):
        self.printer = printer
        self.base_position = [0., 0., 0., 0.]
        self.last_position = [0., 0., 0., 0.]
        self.homing_position = [0., 0., 0., 0.]

    def get_status(self, eventtime=None):
        return {'homing_origin': tuple(self.homing_position)}

    def reset_last_position(self):
        self.last_position = self.printer.lookup_object('toolhead').get_position()

    def move(self):
        self.printer.lookup_object('toolhead').pos = list(self.last_position)

    def g1(self, params):
        for pos, axis in enumerate('XYZ'):
            if axis in params:
                self.last_position[pos] = float(params[axis]) + self.base_position[pos]
        self.move()

    def set_gcode_offset(self, params):
        for pos, axis in enumerate('XYZE'):
            if axis in params:
                offset = float(params[axis])
            elif axis + '_ADJUST' in params:
                offset = float(params[axis + '_ADJUST']) + self.homing_position[pos]
            else:
                continue
            delta = offset - self.homing_position[pos]
            self.base_position[pos] += delta
            self.homing_position[pos] = offset
            if int(params.get('MOVE', 0)):
                self.last_position[pos] += delta
        if int(params.get('MOVE', 0)):
            self.move()


class Probe:
    name = 'probe_ks1'

    def __init__(self, z_offset):
        self.z_offset = z_offset
        self.probe_offsets = type('Offsets', (), {'z_offset': z_offset})()

    def get_offsets(self, gcmd=None):
        return 0., 0., self.probe_offsets.z_offset

    def get_probe_params(self, gcmd=None):
        return {'probe_speed': 6.}


class Command:
    def __init__(self, cs):
        self.cs = cs

    def send(self, args):
        self.cs.answer()


class CS1237:
    _oid = 0

    def __init__(self, printer):
        self.printer = printer
        self.enabled = 0
        self.enable_log = []
        self.silent = False  # simulate a sensor that stops answering
        self.stuck_load = 0.  # mm of load that never clears (debris)
        self.rng = random.Random(7)
        self._query_complete = None
        self._cmd_query_diff = Command(self)

    def _enable_cs1237(self, state):
        self.enabled = state
        self.enable_log.append(state)

    def _int32_conversion(self, value):
        return value

    def answer(self):
        if self.silent:
            return
        toolhead = self.printer.lookup_object('toolhead')
        load = max(0., -toolhead.world_z)
        if self.stuck_load:
            load = self.stuck_load + toolhead.pos[2] * 2.  # keeps changing
        diff = int(-load * COUNTS_PER_MM + self.rng.uniform(-300, 300))
        self._query_complete.complete({'diff': diff, 'raw': diff})


class Heater:
    def __init__(self):
        self.target = 0.

    def get_status(self, eventtime):
        return {'target': self.target, 'temperature': self.target}


class Extruder:
    def __init__(self):
        self.heater = Heater()

    def get_heater(self):
        return self.heater


class ConfigFile:
    def __init__(self):
        self.pending = {}

    def set(self, section, option, value):
        self.pending[(section, option)] = value


class GCmd:
    def __init__(self, params):
        self.params = params
        self.responses = []

    def get_int(self, name, default=None):
        return int(self.params.get(name, default))

    def get_float(self, name, default=None):
        value = self.params.get(name, default)
        return None if value is None else float(value)

    def respond_info(self, msg):
        self.responses.append(msg)


class GCode:
    def __init__(self, printer):
        self.printer = printer
        self.commands = {}
        self.log = []
        self.info = []

    def register_command(self, name, func, desc=None):
        self.commands[name] = func

    def respond_info(self, msg, log=True):
        self.info.append(msg)

    def run(self, line):
        """Run a command the way a console would; returns the GCmd."""
        words = line.split()
        gcmd = GCmd(dict(w.split('=', 1) for w in words[1:]))
        self.commands[words[0]](gcmd)
        return gcmd

    def run_script_from_command(self, script):
        for line in script.split('\n'):
            self.log.append(line)
            words = line.split()
            cmd = words[0].upper()
            if '=' in line:
                params = dict(w.upper().split('=', 1) for w in words[1:])
            else:
                params = {w[0].upper(): w[1:] for w in words[1:]}
            toolhead = self.printer.lookup_object('toolhead')
            gcode_move = self.printer.lookup_object('gcode_move')
            if cmd == 'G28':
                # Stop at the trigger load, then report the endstop position
                toolhead.frame = -PRESS - toolhead.pos[2]
                pos = toolhead.get_position()
                pos[2] = toolhead.kin.rails[2].position_endstop
                toolhead.set_position(pos)
                toolhead.homed = 'xyz'
                gcode_move.g1({'Z': 5.})
            elif cmd == 'G1':
                gcode_move.g1(params)
            elif cmd == 'SET_GCODE_OFFSET':
                gcode_move.set_gcode_offset(params)
            elif cmd in ('M104', 'M109'):
                self.printer.lookup_object('extruder').heater.target = float(params['S'])
            elif cmd in self.commands:
                self.commands[cmd](GCmd(params))


class Config:
    def __init__(self, printer, values):
        self.printer = printer
        self.values = values

    def get_printer(self):
        return self.printer

    def get(self, name, default=None):
        return self.values.get(name, default)

    def getfloat(self, name, default=None, above=None, minval=None):
        return float(self.values.get(name, default))

    def getint(self, name, default=None, minval=None, maxval=None):
        return int(self.values.get(name, default))

    def getboolean(self, name, default=None):
        val = self.values.get(name, default)
        if isinstance(val, str):
            return val.lower() in ('true', '1', 'yes')
        return bool(val)

    def getfloatlist(self, name, default=None):
        return self.values.get(name, default)


class Printer:
    command_error = CommandError

    def __init__(self, state_file, z_offset=-0.1, objects=None, **config):
        self.reactor = Reactor()
        self.handlers = {}
        self.objects = {}
        self.objects['gcode'] = GCode(self)
        self.objects['gcode_move'] = GCodeMove(self)
        self.objects['toolhead'] = Toolhead(self, z_offset)
        self.objects['probe'] = Probe(z_offset)
        self.objects['cs1237'] = CS1237(self)
        self.objects['extruder'] = Extruder()
        self.objects['configfile'] = ConfigFile()
        self.objects.update(objects or {})
        config.setdefault('state_file', state_file)
        self.leviq = leviq3.load_config(Config(self, config))
        self.handlers['klippy:connect']()
        self.handlers['klippy:ready']()

    def get_reactor(self):
        return self.reactor

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def register_event_handler(self, event, callback):
        self.handlers[event] = callback

    # helpers for the tests
    def run(self, line):
        return self.objects['gcode'].run(line)

    def world_z_at_gcode_zero(self):
        self.objects['gcode'].run_script_from_command("G1 Z0")
        return self.objects['toolhead'].world_z

    def z_offset(self):
        return self.objects['probe'].get_offsets()[2]


class FakeZMesh:
    """The part of bed_mesh.ZMesh leviq3 uses."""

    def __init__(self, name, points, params):
        self.name, self.points, self.params = name, points, params

    def get_profile_name(self):
        return self.name

    def get_probed_matrix(self):
        return [list(row) for row in self.points]

    def get_mesh_params(self):
        return self.params


class FakeProfileManager:
    """bed_mesh.ProfileManager: profiles is replaced as a whole, not mutated."""

    def __init__(self, bed_mesh, profiles):
        self.bed_mesh = bed_mesh
        self.profiles = profiles

    def get_profiles(self):
        return self.profiles

    def load_profile(self, name):
        self.bed_mesh.load(name)


class FakeBedMesh:
    PARAMS = {'min_x': 15., 'max_x': 240., 'min_y': 15., 'max_y': 235.,
              'x_count': 3, 'y_count': 3, 'mesh_x_pps': 2, 'mesh_y_pps': 2,
              'algo': 'bicubic', 'tension': 0.2}

    @classmethod
    def profile(cls, points):
        return {'points': [list(row) for row in points],
                'mesh_params': collections.OrderedDict(cls.PARAMS)}

    def __init__(self, config_profiles=None):
        self.z_mesh = None
        self.status_updates = 0
        self.pmgr = FakeProfileManager(self, dict(config_profiles or {}))

    def get_mesh(self):
        return self.z_mesh

    def update_status(self):
        self.status_updates += 1

    def load(self, name):
        profile = self.pmgr.profiles[name]
        self.z_mesh = FakeZMesh(name, profile['points'], dict(profile['mesh_params']))

    def calibrate(self, name, points):
        # BED_MESH_CALIBRATE: set the mesh, then save_profile (session only)
        self.z_mesh = FakeZMesh(name, points, dict(self.PARAMS))
        profiles = dict(self.pmgr.profiles)
        profiles[name] = self.profile(points)
        self.pmgr.profiles = profiles


class LeviQ3Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_file = os.path.join(self.tmp.name, 'leviq3_state.json')

    def printer(self, **config):
        config.setdefault('auto_zoffset_on_off', 1)
        return Printer(self.state_file, **config)

    def assert_zero_just_below_contact(self, printer):
        # Z=0 must sit return_distance below where the nozzle left the bed;
        # the lift overshoots by up to three steps before it is detected.
        world = printer.world_z_at_gcode_zero()
        self.assertGreaterEqual(world, -0.075 - 1e-6)
        self.assertLessEqual(world, -0.075 + 3 * 0.03 + 1e-6)

    def test_auto_zoffset_finds_contact_point(self):
        printer = self.printer()
        printer.run("LEVIQ_AUTO_ZOFFSET")
        self.assert_zero_just_below_contact(printer)
        # same nozzle height after homing again with the new z_offset
        live = printer.world_z_at_gcode_zero()
        printer.objects['gcode'].run_script_from_command("G28 Z")
        self.assertAlmostEqual(printer.world_z_at_gcode_zero(), live, places=6)
        self.assertAlmostEqual(printer.z_offset(), -PRESS - live, places=6)
        self.assertEqual(printer.objects['gcode_move'].homing_position[2], 0.)
        self.assertEqual(printer.objects['cs1237'].enabled, 0)
        self.assertEqual(printer.objects['toolhead'].kin.rails[2].position_endstop,
                         printer.z_offset())

    def test_auto_zoffset_persists_and_reloads(self):
        printer = self.printer()
        printer.run("LEVIQ_AUTO_ZOFFSET")
        measured = printer.z_offset()
        with open(self.state_file) as f:
            self.assertAlmostEqual(json.load(f)['z_offset'], measured, places=4)
        self.assertEqual(printer.objects['configfile'].pending[('probe_ks1', 'z_offset')],
                         "%.3f" % measured)
        restarted = self.printer()
        self.assertAlmostEqual(restarted.z_offset(), measured, places=4)
        self.assertAlmostEqual(restarted.objects['toolhead'].kin.rails[2].position_endstop,
                               measured, places=4)

    def test_is_save_zero_does_not_write(self):
        printer = self.printer()
        printer.run("LEVIQ_AUTO_ZOFFSET IS_SAVE=0")
        self.assertFalse(os.path.exists(self.state_file))

    def test_disabled_only_homes(self):
        printer = self.printer(auto_zoffset_on_off=0)
        printer.run("LEVIQ_AUTO_ZOFFSET")
        self.assertEqual(printer.z_offset(), -0.1)
        log = printer.objects['gcode'].log
        self.assertEqual(log[-1], "G28 Z")
        self.assertNotIn("G4 P1000", log)

    def test_force_measures_without_changing_the_switch(self):
        printer = self.printer(auto_zoffset_on_off=0)
        printer.run("LEVIQ_AUTO_ZOFFSET FORCE=1")
        self.assertNotEqual(printer.objects['gcode'].log[-1], "G28 Z")
        self.assertNotAlmostEqual(printer.z_offset(), -0.1)
        self.assertEqual(printer.leviq.auto_zoffset_on_off, 0)
        self.assertEqual(self.printer(auto_zoffset_on_off=0).leviq.auto_zoffset_on_off, 0)

    def test_on_off_command_is_saved(self):
        printer = self.printer(auto_zoffset_on_off=0)
        printer.run("LEVIQ_AUTO_ZOFFSET_ON_OFF ON_OFF=1")
        self.assertEqual(self.printer(auto_zoffset_on_off=0).leviq.auto_zoffset_on_off, 1)

    def test_sensor_timeout_keeps_old_offset(self):
        printer = self.printer()
        printer.objects['cs1237'].silent = True
        gcmd = printer.run("LEVIQ_AUTO_ZOFFSET")
        self.assertAlmostEqual(printer.z_offset(), -0.1)
        self.assertTrue(any("timeout" in r for r in gcmd.responses))
        self.assertEqual(printer.objects['cs1237'].enabled, 0)
        self.assertEqual(printer.objects['gcode_move'].homing_position[2], 0.)

    def test_failed_auto_zoffset_restores_temp_comp(self):
        # ERR-19: a failed measurement must leave temp_comp as it was
        printer = self.printer()
        printer.run("LEVIQ_AUTO_ZOFFSET")
        printer.run("LEVIQ_TEMP_OFFSET TEMP=220")
        before_comp = printer.leviq.temp_comp
        before_offset = printer.z_offset()
        self.assertNotEqual(before_comp, 0.)
        printer.objects['cs1237'].silent = True
        gcmd = printer.run("LEVIQ_AUTO_ZOFFSET")
        self.assertTrue(any("timeout" in r for r in gcmd.responses))
        self.assertAlmostEqual(printer.leviq.temp_comp, before_comp, places=6)
        self.assertAlmostEqual(printer.z_offset(), before_offset, places=6)

    def test_failed_auto_zoffset_does_not_double_compensate(self):
        printer = self.printer()
        printer.run("LEVIQ_AUTO_ZOFFSET")
        printer.run("LEVIQ_TEMP_OFFSET TEMP=220")
        compensated = printer.z_offset()
        printer.objects['cs1237'].silent = True
        printer.run("LEVIQ_AUTO_ZOFFSET")
        printer.objects['cs1237'].silent = False
        printer.run("LEVIQ_TEMP_OFFSET TEMP=220")
        self.assertAlmostEqual(printer.z_offset(), compensated, places=6)
        self.assertAlmostEqual(printer.leviq.temp_comp, 0.0375, places=6)

    def test_contact_never_clears_is_bounded(self):
        printer = self.printer()
        printer.objects['cs1237'].stuck_load = 5.
        gcmd = printer.run("LEVIQ_AUTO_ZOFFSET")
        self.assertAlmostEqual(printer.z_offset(), -0.1)
        self.assertTrue(any("still loaded" in r for r in gcmd.responses))
        lifts = [l for l in printer.objects['gcode'].log if "Z_ADJUST=0.03" in l]
        self.assertLessEqual(len(lifts), 4 * 67)  # four attempts of at most 2 mm

    def test_early_release_wipes_again(self):
        # the nozzle leaves the bed almost at once: treated as a dirty nozzle
        global PRESS
        old, PRESS = PRESS, 0.01
        self.addCleanup(lambda: globals().__setitem__('PRESS', old))
        printer = self.printer(wipe_gcode='MY_WIPE', zoffset_factor=0.05)
        printer.run("LEVIQ_AUTO_ZOFFSET")
        self.assertEqual(printer.objects['gcode'].log.count('MY_WIPE'), 1)
        printer = self.printer(wipe_gcode='MY_WIPE', zoffset_factor=0.05)
        printer.run("LEVIQ_AUTO_ZOFFSET RETRY_COUNT=3")
        self.assertEqual(printer.objects['gcode'].log.count('MY_WIPE'), 3)

    def wipes_for(self, command, **config):
        # G-02: how many dirty-nozzle re-wipes a nozzle that leaves the bed
        # at once gets
        global PRESS
        old, PRESS = PRESS, 0.01
        self.addCleanup(lambda: globals().__setitem__('PRESS', old))
        printer = self.printer(wipe_gcode='MY_WIPE', zoffset_factor=0.05, **config)
        printer.run(command)
        return printer, printer.objects['gcode'].log.count('MY_WIPE')

    def test_retry_count_zero_means_no_rewipe(self):
        printer, wipes = self.wipes_for("LEVIQ_AUTO_ZOFFSET RETRY_COUNT=0")
        self.assertEqual(wipes, 0)
        self.assertNotIn("M109 S170.000000", printer.objects['gcode'].log)
        # the measurement still is accepted and saved
        self.assertTrue(os.path.exists(self.state_file))
        self.assertIn(('probe_ks1', 'z_offset'), printer.objects['configfile'].pending)

    def test_retry_count_default_comes_from_config(self):
        self.assertEqual(self.wipes_for("LEVIQ_AUTO_ZOFFSET")[1], 1)   # stock
        self.assertEqual(self.wipes_for("LEVIQ_AUTO_ZOFFSET", retry_count=0)[1], 0)
        self.assertEqual(self.wipes_for("LEVIQ_AUTO_ZOFFSET", retry_count=2)[1], 2)
        # the command parameter wins over the config
        self.assertEqual(self.wipes_for("LEVIQ_AUTO_ZOFFSET RETRY_COUNT=1",
                                        retry_count=0)[1], 1)
        self.assertEqual(self.wipes_for("LEVIQ_AUTO_ZOFFSET RETRY_COUNT=0",
                                        retry_count=2)[1], 0)

    def test_temp_offset(self):
        printer = self.printer()
        printer.run("LEVIQ_AUTO_ZOFFSET")
        base_offset, base_world = printer.z_offset(), printer.world_z_at_gcode_zero()
        printer.run("LEVIQ_TEMP_OFFSET TEMP=220")
        self.assertAlmostEqual(printer.z_offset(), base_offset - 0.0375, places=6)
        self.assertAlmostEqual(printer.world_z_at_gcode_zero(), base_world + 0.0375, places=6)
        printer.run("LEVIQ_TEMP_OFFSET TEMP=220")  # no accumulation
        self.assertAlmostEqual(printer.z_offset(), base_offset - 0.0375, places=6)
        printer.run("LEVIQ_TEMP_OFFSET TEMP=140")
        self.assertAlmostEqual(printer.z_offset(), base_offset, places=6)
        printer.run("LEVIQ_TEMP_OFFSET TEMP=0")
        self.assertAlmostEqual(printer.z_offset(), base_offset, places=6)

    def test_temp_offset_defaults_to_heater_target(self):
        printer = self.printer()
        printer.objects['extruder'].heater.target = 300.
        printer.run("LEVIQ_TEMP_OFFSET")
        self.assertAlmostEqual(printer.z_offset(), -0.1 - 160 * 0.0375 / 80, places=6)

    def test_set_zero(self):
        printer = self.printer()
        printer.objects['gcode'].run_script_from_command("G28 Z")
        printer.objects['gcode'].run_script_from_command("SET_GCODE_OFFSET Z=0.3 MOVE=1")
        world = printer.objects['toolhead'].world_z
        printer.run("LEVIQ_SET_ZERO")
        self.assertEqual(printer.z_offset(), 0.)
        self.assertEqual(printer.objects['gcode_move'].homing_position[2], 0.)
        self.assertAlmostEqual(printer.objects['toolhead'].world_z, world)  # nozzle did not move

    def test_leviq_sequence(self):
        seen = {}
        printer = self.printer(wipe_gcode='MY_WIPE', mesh_gcode='MY_MESH')
        gcode = printer.objects['gcode']
        gcode.register_command('MY_WIPE', lambda _g: seen.__setitem__('wipe', printer.z_offset()))
        gcode.register_command('MY_MESH', lambda _g: seen.__setitem__('mesh', printer.z_offset()))
        gcode.run_script_from_command("G28")
        before_world = printer.world_z_at_gcode_zero()
        printer.run("LEVIQ B=70 S=180")
        log = gcode.log
        order = [log.index(s) for s in ("M190 S70.000000", "M109 S180.000000", "MY_WIPE",
                                         "BED_MESH_CLEAR", "G28 Z", "MY_MESH")]
        self.assertEqual(order, sorted(order))
        # Stock gklib zeros probe z_offset for wiping/meshing, then restores +0xac after mesh
        self.assertEqual(seen, {'wipe': 0., 'mesh': 0.})
        self.assertAlmostEqual(printer.z_offset(), -0.1, places=6)
        self.assertAlmostEqual(printer.objects['toolhead'].kin.rails[2].position_endstop,
                               -0.1, places=6)
        self.assertAlmostEqual(printer.world_z_at_gcode_zero(), before_world, places=6)
        # The cool-down to extru_end_temp runs with the part fan on, probing with it off
        wipe = log.index("MY_WIPE")
        fan_on = log.index("M106 S255", wipe)
        cooled = log.index("M109 S140.000000", wipe)
        fan_off = log.index("M107", cooled)
        self.assertLess(fan_on, cooled)
        self.assertLess(fan_off, log.index("G28 Z", wipe))

    def test_preheating_parks_before_heating_without_lowering_z(self):
        # M05: when XY are homed, park at platform_offset_extru BEFORE M104/M190/M109,
        # lifting Z to 15 only if below 15 (never lowering Z towards a part).
        printer = self.printer(platform_offset_extru=(278., 260., 0.2))
        gcode = printer.objects['gcode']
        gcode.run_script_from_command("G28")  # leaves toolhead homed at Z=5
        gcode.log.clear()
        printer.run("LEVIQ_PREHEATING B=60 S=170")
        log = gcode.log
        park_idx = log.index("G1 X278.000000 Y260.000000 F9600")
        lift_idx = log.index("G1 Z15.0 F4800")
        self.assertLess(lift_idx, park_idx)
        self.assertLess(park_idx, log.index("M104 S170.000000"))
        self.assertLess(park_idx, log.index("M190 S60.000000"))
        self.assertLess(park_idx, log.index("M109 S170.000000"))

        # Already above Z=15 -> do NOT lower Z
        gcode.run_script_from_command("G1 Z30")
        gcode.log.clear()
        printer.run("LEVIQ_PREHEATING B=60 S=170")
        log = gcode.log
        self.assertNotIn("G1 Z15.0 F4800", log)
        self.assertLess(log.index("G1 X278.000000 Y260.000000 F9600"), log.index("M104 S170.000000"))

        # Unhomed XY -> never move X/Y
        printer.objects['toolhead'].homed = ''
        gcode.log.clear()
        printer.run("LEVIQ_PREHEATING B=60 S=170")
        self.assertFalse(any("X278" in l for l in gcode.log))

    def test_cool_to_end_temp_skips_fan_pulse_when_already_cool(self):
        # M06: skip M106 S255 when nozzle is already <= extru_end_temp + 2
        printer = self.printer(extru_end_temp=140.)
        gcode = printer.objects['gcode']
        heater = printer.objects['extruder'].heater

        heater.target = 140.
        gcode.log.clear()
        printer.leviq._cool_to_end_temp()
        self.assertNotIn("M106 S255", gcode.log)
        self.assertIn("M109 S140.000000", gcode.log)
        self.assertIn("M107", gcode.log)

        heater.target = 170.
        gcode.log.clear()
        printer.leviq._cool_to_end_temp()
        self.assertIn("M106 S255", gcode.log)
        self.assertLess(gcode.log.index("M106 S255"), gcode.log.index("M109 S140.000000"))
        self.assertLess(gcode.log.index("M109 S140.000000"), gcode.log.index("M107"))

    def test_leviq_preserves_measured_and_temp_offset_and_three_step_flow(self):
        printer = self.printer(wipe_gcode='MY_WIPE', mesh_gcode='MY_MESH')
        printer.run("LEVIQ_AUTO_ZOFFSET")
        measured = printer.z_offset()
        printer.run("LEVIQ_TEMP_OFFSET TEMP=220")
        with_temp = printer.z_offset()
        self.assertAlmostEqual(with_temp, measured - 0.0375, places=6)
        expected_world = printer.world_z_at_gcode_zero()

        # Full LEVIQ keeps the calibrated + temp-compensated offset after completion
        printer.run("LEVIQ B=60 S=170")
        self.assertAlmostEqual(printer.z_offset(), with_temp, places=6)
        self.assertAlmostEqual(printer.world_z_at_gcode_zero(), expected_world, places=6)
        # Re-applying LEVIQ_TEMP_OFFSET TEMP=220 must not double-add 0.0375
        printer.run("LEVIQ_TEMP_OFFSET TEMP=220")
        self.assertAlmostEqual(printer.z_offset(), with_temp, places=6)

        # Standalone 3-step flow (LEVIQ_PREHEATING -> LEVIQ_WIPING -> LEVIQ_PROBE)
        printer.run("LEVIQ_PREHEATING")
        self.assertEqual(printer.z_offset(), 0.)
        printer.run("LEVIQ_WIPING")
        self.assertEqual(printer.z_offset(), 0.)
        printer.run("LEVIQ_PROBE")
        self.assertAlmostEqual(printer.z_offset(), with_temp, places=6)

        # Explicitly disabling restore_zoffset keeps the zeroed offset
        p_no_restore = self.printer(restore_zoffset=False, wipe_gcode='MY_WIPE', mesh_gcode='MY_MESH')
        p_no_restore.run("LEVIQ B=60 S=170")
        self.assertEqual(p_no_restore.z_offset(), 0.)

    def test_leviq_restores_zoffset_on_error(self):
        printer = self.printer(wipe_gcode='MY_WIPE', mesh_gcode='MY_MESH')
        printer.run("LEVIQ_AUTO_ZOFFSET")
        printer.run("LEVIQ_TEMP_OFFSET TEMP=220")
        expected_offset = printer.z_offset()
        expected_comp = printer.leviq.temp_comp
        gcode = printer.objects['gcode']

        def fail(_gcmd):
            raise CommandError("simulated failure")

        # 1) Failure in wipe phase of LEVIQ
        gcode.register_command('MY_WIPE', fail)
        with self.assertRaises(CommandError):
            printer.run("LEVIQ B=60 S=170")
        self.assertAlmostEqual(printer.z_offset(), expected_offset, places=6)
        self.assertAlmostEqual(printer.objects['toolhead'].kin.rails[2].position_endstop,
                               expected_offset, places=6)
        self.assertAlmostEqual(printer.leviq.temp_comp, expected_comp, places=6)

        # 2) Failure in mesh phase of LEVIQ
        gcode.commands.pop('MY_WIPE', None)
        gcode.register_command('MY_MESH', fail)
        with self.assertRaises(CommandError):
            printer.run("LEVIQ B=60 S=170")
        self.assertAlmostEqual(printer.z_offset(), expected_offset, places=6)
        self.assertAlmostEqual(printer.leviq.temp_comp, expected_comp, places=6)

        # 3) Failure in preheating phase (before wiping starts)
        gcode.commands.pop('MY_MESH', None)
        orig_script = gcode.run_script_from_command

        def fail_retract(script):
            if "G1 E-10" in script:
                raise CommandError("retract blocked")
            return orig_script(script)

        gcode.run_script_from_command = fail_retract
        with self.assertRaises(CommandError):
            printer.run("LEVIQ B=60 S=170")
        gcode.run_script_from_command = orig_script
        self.assertAlmostEqual(printer.z_offset(), expected_offset, places=6)
        self.assertAlmostEqual(printer.leviq.temp_comp, expected_comp, places=6)

    def test_bad_saved_offset_is_ignored(self):
        with open(self.state_file, 'w') as f:
            json.dump({'z_offset': -40}, f)
        self.assertEqual(self.printer().z_offset(), -0.1)

    def test_probe_without_name_attr_uses_helper_name(self):
        class RealLikeProbe:
            def __init__(self, z_offset):
                self.probe_offsets = type('Offsets', (), {'z_offset': z_offset})()
                self.cmd_helper = type('CmdHelper', (), {'name': 'probe'})()
                self.mcu_probe = type('McuProbe', (), {'position_endstop': z_offset})()

            def get_offsets(self, gcmd=None):
                return 0., 0., self.probe_offsets.z_offset

            def get_probe_params(self, gcmd=None):
                return {'probe_speed': 6.}

            def get_status(self, eventtime):
                return {'name': 'probe', 'z_offset': self.probe_offsets.z_offset}

        printer = self.printer()
        printer.objects['probe'] = RealLikeProbe(-0.1)
        printer.handlers['klippy:connect']()
        printer.run("LEVIQ_AUTO_ZOFFSET")
        measured = printer.z_offset()
        self.assertEqual(printer.objects['configfile'].pending[('probe', 'z_offset')],
                         "%.3f" % measured)
        self.assertAlmostEqual(printer.objects['probe'].mcu_probe.position_endstop,
                               measured, places=6)

    def test_g4_uses_milliseconds_and_empty_wipe_is_skipped(self):
        printer = self.printer(bed_soak_time=2.5, wipe_gcode='', mesh_gcode='MY_MESH')
        printer.run("LEVIQ B=60 S=170")
        log = printer.objects['gcode'].log
        self.assertIn("G4 P2500", log)
        self.assertIn("G4 P1000", log)
        self.assertFalse(any(line.startswith("G4 S") for line in log))
        self.assertNotIn("", log)

    def test_load_cell_probe_adapter_fallback(self):
        class LoadCellProbe:
            def __init__(self, printer, z_offset):
                self._probe_offsets = type('Offsets', (), {'z_offset': z_offset})()
                self._cmd_helper = type('CmdHelper', (), {'name': 'load_cell_probe'})()

                class _LoadCell:
                    def __init__(self, p):
                        self.p = p
                        self.rng = random.Random(7)

                    def avg_counts(self, num_samples=None):
                        load = max(0., -self.p.lookup_object('toolhead').world_z)
                        return int(-load * COUNTS_PER_MM + self.rng.uniform(-300, 300))

                self._load_cell = _LoadCell(printer)

            def get_offsets(self, gcmd=None):
                return 0., 0., self._probe_offsets.z_offset

            def get_probe_params(self, gcmd=None):
                return {'probe_speed': 6.}

            def get_status(self, eventtime):
                return {'name': 'load_cell_probe'}

        printer = self.printer()
        del printer.objects['cs1237']
        printer.objects['probe'] = LoadCellProbe(printer, -0.1)
        printer.handlers['klippy:connect']()
        printer.run("LEVIQ_AUTO_ZOFFSET")
        self.assert_zero_just_below_contact(printer)
        self.assertEqual(
            printer.objects['configfile'].pending[('load_cell_probe', 'z_offset')],
            "%.3f" % printer.z_offset())

    def test_leviq3_cfg_uses_rinkhals_persistent_state_path(self):
        import configparser
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'leviq3.cfg')
        cp = configparser.RawConfigParser(strict=False, inline_comment_prefixes=(';', '#'))
        cp.read(cfg_path, encoding='utf-8')
        state_path = cp.get('leviq3', 'state_file')
        self.assertEqual(state_path, '/useremain/home/rinkhals/printer_data/config/leviq3_state.json')
        self.assertFalse(state_path.startswith('/userdata/'))

    def test_save_state_skips_missing_parent_without_makedirs(self):
        missing_parent = os.path.join(self.tmp.name, 'no_such_dir')
        missing_state = os.path.join(missing_parent, 'leviq3_state.json')
        printer = Printer(missing_state, auto_zoffset_on_off=0)
        with self.assertLogs(level='WARNING') as cm:
            printer.run("LEVIQ_AUTO_ZOFFSET_ON_OFF ON_OFF=1")
        self.assertFalse(os.path.exists(missing_parent))
        self.assertFalse(os.path.exists(missing_state))
        self.assertTrue(any('skipping write' in m for m in cm.output))
        self.assertEqual(printer.objects['configfile'].pending, {})

    def test_save_state_refuses_unmounted_userdata(self):
        from unittest import mock
        userdata_state = '/userdata/app/gk/printer_data/config/leviq3_state.json'
        printer = Printer(userdata_state, auto_zoffset_on_off=0)
        with mock.patch('leviq3.os.path.ismount', return_value=False) as m_ismount, \
                mock.patch('leviq3.os.makedirs') as m_makedirs, \
                self.assertLogs(level='WARNING') as cm:
            printer.run("LEVIQ_AUTO_ZOFFSET_ON_OFF ON_OFF=1")
        m_ismount.assert_called_with('/userdata/app/gk/printer_data')
        m_makedirs.assert_not_called()
        self.assertTrue(any('bind-mount' in m for m in cm.output))
        self.assertEqual(printer.objects['configfile'].pending, {})


    # G-01: z_offset, SAVE_CONFIG, the gcode offset and the state file
    def test_only_calibration_offers_zoffset_to_save_config(self):
        printer = self.printer(auto_zoffset_on_off=0)
        pending = printer.objects['configfile'].pending
        printer.run("LEVIQ_AUTO_ZOFFSET_ON_OFF ON_OFF=1")
        printer.run("LEVIQ_TEMP_OFFSET TEMP=220")
        printer.run("LEVIQ_AUTO_ZOFFSET_ON_OFF ON_OFF=0")
        self.assertEqual(pending, {})
        self.assertTrue(os.path.exists(self.state_file))   # state still kept
        printer.run("LEVIQ_AUTO_ZOFFSET_ON_OFF ON_OFF=1")
        printer.run("LEVIQ_AUTO_ZOFFSET")
        self.assertEqual(pending, {('probe_ks1', 'z_offset'): "%.3f" % printer.z_offset()})

    def test_temp_offset_does_not_change_the_value_offered_to_save_config(self):
        printer = self.printer()
        printer.run("LEVIQ_AUTO_ZOFFSET")
        pending = printer.objects['configfile'].pending
        offered = dict(pending)
        calibrated = printer.z_offset()
        printer.run("LEVIQ_TEMP_OFFSET TEMP=220")
        self.assertEqual(pending, offered)            # no thermal shift folded in
        with open(self.state_file) as f:
            state = json.load(f)
        self.assertAlmostEqual(state['z_offset'], calibrated - 0.0375, places=4)
        self.assertAlmostEqual(state['temp_comp'], 0.0375, places=4)
        self.assertAlmostEqual(state['offered_z_offset'], calibrated, places=3)

    def test_save_config_value_excludes_temp_comp_on_recalibration_path(self):
        # a failed measurement keeps the old offset; offering must not add
        # the compensation twice (z_offset + temp_comp is the calibrated one)
        printer = self.printer()
        printer.run("LEVIQ_AUTO_ZOFFSET")
        calibrated = printer.z_offset()
        printer.run("LEVIQ_TEMP_OFFSET TEMP=220")
        printer.leviq._save_state(update_configfile=True)
        self.assertEqual(printer.objects['configfile'].pending[('probe_ks1', 'z_offset')],
                         "%.3f" % calibrated)

    def test_temp_offset_keeps_user_fine_adjustment(self):
        printer = self.printer()
        printer.run("LEVIQ_AUTO_ZOFFSET")
        base_offset, base_world = printer.z_offset(), printer.world_z_at_gcode_zero()
        # the touchscreen's fine adjustment while printing
        printer.objects['gcode'].run_script_from_command(
            "SET_GCODE_OFFSET Z_ADJUST=0.05 MOVE=1")
        gcode_move = printer.objects['gcode_move']
        tuned_world = printer.world_z_at_gcode_zero()
        self.assertAlmostEqual(tuned_world, base_world + 0.05, places=6)
        printer.run("LEVIQ_TEMP_OFFSET TEMP=220")
        # only the compensation went into the probe z_offset
        self.assertAlmostEqual(printer.z_offset(), base_offset - 0.0375, places=6)
        self.assertAlmostEqual(gcode_move.homing_position[2], 0.05, places=6)
        self.assertAlmostEqual(printer.world_z_at_gcode_zero(),
                               base_world + 0.05 + 0.0375, places=6)
        # the compensation is removed again without touching the adjustment
        printer.run("LEVIQ_TEMP_OFFSET TEMP=0")
        self.assertAlmostEqual(printer.z_offset(), base_offset, places=6)
        self.assertAlmostEqual(gcode_move.homing_position[2], 0.05, places=6)
        self.assertAlmostEqual(printer.world_z_at_gcode_zero(), tuned_world, places=6)

    def test_state_file_wins_while_config_is_unchanged(self):
        printer = self.printer()
        printer.run("LEVIQ_AUTO_ZOFFSET")
        measured = printer.z_offset()
        # restart without SAVE_CONFIG: config still holds the old value
        restarted = self.printer()
        self.assertAlmostEqual(restarted.z_offset(), measured, places=4)
        # restart after SAVE_CONFIG wrote the offered value
        saved = self.printer(z_offset=float("%.3f" % measured))
        self.assertAlmostEqual(saved.z_offset(), measured, places=4)

    def test_state_file_with_temp_comp_survives_restart(self):
        printer = self.printer()
        printer.run("LEVIQ_AUTO_ZOFFSET")
        printer.run("LEVIQ_TEMP_OFFSET TEMP=220")
        compensated = printer.z_offset()
        restarted = self.printer()
        self.assertAlmostEqual(restarted.z_offset(), compensated, places=4)
        self.assertAlmostEqual(restarted.leviq.temp_comp, 0.0375, places=4)
        # still no accumulation
        restarted.run("LEVIQ_TEMP_OFFSET TEMP=220")
        self.assertAlmostEqual(restarted.z_offset(), compensated, places=4)

    def test_config_changed_after_state_file_wins(self):
        printer = self.printer()
        printer.run("LEVIQ_AUTO_ZOFFSET")
        printer.run("LEVIQ_TEMP_OFFSET TEMP=220")
        # Z_OFFSET_APPLY_PROBE + SAVE_CONFIG (or a hand edit) wrote -0.2
        with self.assertLogs(level='WARNING') as cm:
            restarted = self.printer(z_offset=-0.2)
        self.assertTrue(any('changed since' in m for m in cm.output))
        self.assertAlmostEqual(restarted.z_offset(), -0.2, places=6)
        self.assertEqual(restarted.leviq.temp_comp, 0.)
        self.assertAlmostEqual(
            restarted.objects['toolhead'].kin.rails[2].position_endstop, -0.2, places=6)
        with open(self.state_file) as f:
            state = json.load(f)
        self.assertAlmostEqual(state['z_offset'], -0.2, places=4)
        self.assertAlmostEqual(state['config_z_offset'], -0.2, places=4)
        self.assertNotIn('offered_z_offset', state)
        # and it stays that way on the next start
        again = self.printer(z_offset=-0.2)
        self.assertAlmostEqual(again.z_offset(), -0.2, places=6)

    def test_state_file_of_older_version_is_trusted(self):
        with open(self.state_file, 'w') as f:
            json.dump({'z_offset': -0.3, 'temp_comp': 0.0, 'auto_zoffset_on_off': 1}, f)
        self.assertAlmostEqual(self.printer().z_offset(), -0.3, places=6)

    # G-03: the bed mesh of LEVIQ_PROBE survives a restart
    MESH1 = [[0.01, 0.02, 0.03], [0.00, -0.01, 0.02], [-0.02, 0.01, 0.04]]
    MESH2 = [[0.05, 0.02, 0.03], [0.00, -0.03, 0.02], [-0.02, 0.01, 0.01]]

    def bed_mesh_printer(self, mesh=None, config_profiles=None, **config):
        bed_mesh = FakeBedMesh(config_profiles)
        config.setdefault('mesh_gcode', 'MY_MESH')
        printer = self.printer(objects={'bed_mesh': bed_mesh}, **config)
        printer.objects['gcode'].register_command(
            'MY_MESH', lambda _g: bed_mesh.calibrate('default', mesh or self.MESH1))
        return printer, bed_mesh

    def state(self):
        with open(self.state_file) as f:
            return json.load(f)

    def test_leviq_probe_keeps_mesh_in_state_file_and_zoffset_untouched(self):
        printer, bed_mesh = self.bed_mesh_printer()
        printer.run("LEVIQ_AUTO_ZOFFSET")
        measured = printer.z_offset()
        gcmd = printer.run("LEVIQ B=60 S=170")
        state = self.state()
        self.assertEqual(state['mesh']['profile'], 'default')
        self.assertEqual(state['mesh']['points'], self.MESH1)
        self.assertEqual(state['mesh']['params'], FakeBedMesh.PARAMS)
        self.assertIsNone(state['mesh']['baseline'])
        # the state file z_offset is the calibrated one, not the zeroed one
        self.assertAlmostEqual(state['z_offset'], measured, places=4)
        self.assertTrue(any("kept" in r for r in gcmd.responses))
        # the standard Klipper path (calibrate's profile) was used, no SAVE_CONFIG
        self.assertNotIn("SAVE_CONFIG", printer.objects['gcode'].log)

    def test_mesh_without_prior_state_file_does_not_zero_zoffset_on_restart(self):
        printer, _ = self.bed_mesh_printer()
        printer.run("LEVIQ B=60 S=170")
        self.assertNotIn('z_offset', self.state())
        restarted, _ = self.bed_mesh_printer()
        self.assertEqual(restarted.z_offset(), -0.1)

    def test_mesh_is_active_after_restart(self):
        printer, _ = self.bed_mesh_printer()
        printer.run("LEVIQ B=60 S=170")
        restarted, bed_mesh = self.bed_mesh_printer()
        mesh = bed_mesh.get_mesh()
        self.assertIsNotNone(mesh)
        self.assertEqual(mesh.get_profile_name(), 'default')
        self.assertEqual(mesh.get_probed_matrix(), self.MESH1)
        self.assertEqual(bed_mesh.pmgr.profiles['default']['points'], self.MESH1)
        self.assertEqual(bed_mesh.status_updates, 1)
        # a mesh kept in the state file is not written to the config file
        self.assertEqual(restarted.objects['configfile'].pending, {})

    def test_mesh_autoload_off_only_registers_profile(self):
        printer, _ = self.bed_mesh_printer()
        printer.run("LEVIQ B=60 S=170")
        restarted, bed_mesh = self.bed_mesh_printer(mesh_autoload=False)
        self.assertIsNone(bed_mesh.get_mesh())
        self.assertEqual(bed_mesh.pmgr.profiles['default']['points'], self.MESH1)

    def test_mesh_persist_off_keeps_nothing(self):
        printer, _ = self.bed_mesh_printer(mesh_persist=False)
        printer.run("LEVIQ B=60 S=170")
        self.assertFalse(os.path.exists(self.state_file))
        restarted, bed_mesh = self.bed_mesh_printer(mesh_persist=False)
        self.assertIsNone(bed_mesh.get_mesh())

    def test_failed_mesh_is_not_kept(self):
        printer, _ = self.bed_mesh_printer()
        printer.run("LEVIQ B=60 S=170")
        def fail(_gcmd):
            raise CommandError("probe samples exceed samples_tolerance")
        printer.objects['gcode'].register_command('MY_MESH', fail)
        with self.assertRaises(CommandError):
            printer.run("LEVIQ B=60 S=170")
        self.assertEqual(self.state()['mesh']['points'], self.MESH1)   # last good one

    def test_unnamed_or_missing_mesh_is_not_kept(self):
        printer, bed_mesh = self.bed_mesh_printer()
        printer.objects['gcode'].register_command('MY_MESH', lambda _g: None)
        gcmd = printer.run("LEVIQ_PROBE")
        self.assertTrue(any("no named bed mesh" in r for r in gcmd.responses))
        bed_mesh.z_mesh = FakeZMesh('adaptive-7F', self.MESH1, FakeBedMesh.PARAMS)
        printer.run("LEVIQ_PROBE")
        self.assertFalse(os.path.exists(self.state_file))
        # no [bed_mesh] at all
        printer = self.printer(mesh_gcode='MY_MESH')
        printer.objects['gcode'].register_command('MY_MESH', lambda _g: None)
        printer.run("LEVIQ_PROBE")
        self.assertFalse(os.path.exists(self.state_file))

    def test_save_config_profile_is_the_master_when_it_is_the_same_mesh(self):
        printer, _ = self.bed_mesh_printer()
        printer.run("LEVIQ B=60 S=170")
        # SAVE_CONFIG wrote exactly this mesh: nothing is injected, the
        # config's profile is loaded
        stored = {'default': FakeBedMesh.profile(self.MESH1)}
        restarted, bed_mesh = self.bed_mesh_printer(config_profiles=stored)
        self.assertIs(bed_mesh.pmgr.profiles['default'], stored['default'])
        self.assertEqual(bed_mesh.get_mesh().get_probed_matrix(), self.MESH1)
        self.assertEqual(bed_mesh.status_updates, 0)

    def test_newer_state_mesh_beats_older_config_profile(self):
        # config holds MESH1 (SAVE_CONFIG after the first LEVIQ); a second
        # LEVIQ measured MESH2 and no SAVE_CONFIG followed
        stored = {'default': FakeBedMesh.profile(self.MESH1)}
        printer, _ = self.bed_mesh_printer(mesh=self.MESH2, config_profiles=stored)
        printer.run("LEVIQ B=60 S=170")
        self.assertEqual(self.state()['mesh']['points'], self.MESH2)
        restarted, bed_mesh = self.bed_mesh_printer(config_profiles=stored)
        self.assertEqual(bed_mesh.get_mesh().get_probed_matrix(), self.MESH2)
        self.assertEqual(bed_mesh.pmgr.profiles['default']['points'], self.MESH2)

    def test_profile_changed_in_config_after_leviq_beats_state_mesh(self):
        printer, _ = self.bed_mesh_printer(mesh=self.MESH1)
        printer.run("LEVIQ B=60 S=170")
        # the user ran BED_MESH_CALIBRATE PROFILE=default + SAVE_CONFIG later
        stored = {'default': FakeBedMesh.profile(self.MESH2)}
        with self.assertLogs(level='INFO') as cm:
            restarted, bed_mesh = self.bed_mesh_printer(config_profiles=stored)
        self.assertTrue(any('changed since LEVIQ_PROBE' in m for m in cm.output))
        self.assertIs(bed_mesh.pmgr.profiles['default'], stored['default'])
        self.assertIsNone(bed_mesh.get_mesh())      # nothing of ours is loaded
        self.assertNotIn('mesh', self.state())

    def test_unreadable_saved_mesh_is_dropped(self):
        with open(self.state_file, 'w') as f:
            json.dump({'z_offset': -0.1, 'mesh': {'profile': 'default'}}, f)
        with self.assertLogs(level='ERROR'):
            restarted, bed_mesh = self.bed_mesh_printer()
        self.assertIsNone(bed_mesh.get_mesh())
        self.assertNotIn('mesh', self.state())

    def test_mesh_that_klipper_rejects_does_not_stop_startup(self):
        printer, _ = self.bed_mesh_printer()
        printer.run("LEVIQ B=60 S=170")
        class Rejecting(FakeBedMesh):
            def load(self, name):
                raise CommandError("Mesh extends outside of the fade range")
        bed_mesh = Rejecting()
        with self.assertLogs(level='ERROR') as cm:
            self.printer(objects={'bed_mesh': bed_mesh}, mesh_gcode='MY_MESH')
        self.assertTrue(any('unable to load bed mesh' in m for m in cm.output))
        self.assertIsNone(bed_mesh.get_mesh())

    def test_mesh_save_config_is_opt_in_and_last(self):
        printer, _ = self.bed_mesh_printer()
        printer.run("LEVIQ B=60 S=170")
        self.assertNotIn("SAVE_CONFIG", printer.objects['gcode'].log)
        printer, _ = self.bed_mesh_printer(mesh_save_config=True)
        printer.run("LEVIQ B=60 S=170")
        log = printer.objects['gcode'].log
        self.assertEqual(log.count("SAVE_CONFIG"), 1)
        self.assertEqual(log[-1], "SAVE_CONFIG")
        # a failed mesh never reaches SAVE_CONFIG
        printer, _ = self.bed_mesh_printer(mesh_save_config=True)
        printer.objects['gcode'].register_command(
            'MY_MESH', lambda _g: (_ for _ in ()).throw(CommandError("x")))
        with self.assertRaises(CommandError):
            printer.run("LEVIQ_PROBE")
        self.assertNotIn("SAVE_CONFIG", printer.objects['gcode'].log)


class RealKlipperRegistrationTests(unittest.TestCase):
    EXPECTED_COMMANDS = (
        'LEVIQ',
        'LEVIQ_SET_ZERO',
        'LEVIQ_PREHEATING',
        'LEVIQ_WIPING',
        'LEVIQ_PROBE',
        'LEVIQ_AUTO_ZOFFSET',
        'LEVIQ_TEMP_OFFSET',
        'LEVIQ_AUTO_ZOFFSET_ON_OFF',
    )

    def _use_klippy_dir(self, klippy_dir):
        if not os.path.isdir(klippy_dir):
            self.skipTest("%s not found" % klippy_dir)
        prefixes = ('configfile', 'gcode', 'pins', 'extras', 'klippy',
                    'kinematics', 'stepper', 'chelper')
        saved_modules = {}
        for modname in list(sys.modules):
            if modname in prefixes or modname.startswith(('extras.', 'kinematics.')):
                saved_modules[modname] = sys.modules.pop(modname)
        sys.path.insert(0, klippy_dir)

        def _cleanup():
            if klippy_dir in sys.path:
                sys.path.remove(klippy_dir)
            for modname in list(sys.modules):
                if modname in prefixes or modname.startswith(('extras.', 'kinematics.')):
                    sys.modules.pop(modname, None)
            sys.modules.update(saved_modules)

        self.addCleanup(_cleanup)

    def test_loads_cfg_with_real_klipper_configfile_and_gcode(self):
        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        klippy_dir = os.path.join(repo_dir, 'refs', 'klipper', 'klippy')
        self._use_klippy_dir(klippy_dir)

        import configfile
        import gcode

        class RealHarnessPrinter:
            config_error = configfile.error
            command_error = gcode.CommandError

            def __init__(self, cfg_path):
                self.start_args = {'config_file': cfg_path}
                self.reactor = Reactor()
                self.handlers = {}
                self.objects = {}
                self.objects['gcode'] = gcode.GCodeDispatch(self)
                self.objects['gcode_move'] = GCodeMove(self)
                self.objects['toolhead'] = Toolhead(self, -0.1)
                self.objects['extruder'] = Extruder()
                probe = Probe(-0.1)
                # Real PrinterProbe does not define .name on probe itself
                real_probe = type('RealProbe', (), {
                    'probe_offsets': probe.probe_offsets,
                    'cmd_helper': type('CmdHelper', (), {'name': 'probe'})(),
                    'get_offsets': probe.get_offsets,
                    'get_probe_params': probe.get_probe_params,
                })()
                self.objects['probe'] = real_probe
                self.objects['cs1237'] = CS1237(self)

            def get_start_args(self):
                return self.start_args

            def get_reactor(self):
                return self.reactor

            def register_event_handler(self, event, callback):
                self.handlers.setdefault(event, []).append(callback)

            def lookup_object(self, name, default=None):
                return self.objects.get(name, default)

            def lookup_objects(self, module=None):
                if module is None:
                    return list(self.objects.items())
                prefix = module + ' '
                return [(n, o) for n, o in self.objects.items()
                        if n == module or n.startswith(prefix)]

            def send_event(self, event, *params):
                return [cb(*params) for cb in self.handlers.get(event, [])]

        with tempfile.TemporaryDirectory() as tmpdir:
            leviq3_cfg_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'leviq3.cfg')
            state_path = os.path.join(tmpdir, 'leviq3_state.json')
            printer_cfg = os.path.join(tmpdir, 'printer.cfg')
            with open(printer_cfg, 'w') as f:
                f.write("[include %s]\n[leviq3]\nstate_file: %s\n"
                        % (leviq3_cfg_src, state_path))

            printer = RealHarnessPrinter(printer_cfg)
            pconfig = configfile.PrinterConfig(printer)
            printer.objects['configfile'] = pconfig
            cfg = pconfig.read_main_config()
            self.assertTrue(cfg.has_section('leviq3'))

            printer.objects['leviq3'] = leviq3.load_config(cfg.getsection('leviq3'))
            pconfig.check_unused_options(cfg)
            printer.send_event('klippy:connect')
            printer.send_event('klippy:ready')

            gdispatch = printer.objects['gcode']
            for cmd in self.EXPECTED_COMMANDS:
                self.assertIn(cmd, gdispatch.ready_gcode_handlers)
                self.assertIn(cmd, gdispatch.get_command_help())
                self.assertIn(cmd, gdispatch.get_status(0.)['commands'])

            # Execute a registered command through real GCodeDispatch parsing
            gdispatch.run_script_from_command("LEVIQ_AUTO_ZOFFSET_ON_OFF ON_OFF=1")
            self.assertEqual(printer.objects['leviq3'].auto_zoffset_on_off, 1)
            self.assertTrue(os.path.exists(state_path))
            # only a calibration offers z_offset to SAVE_CONFIG, not this switch
            self.assertNotIn('probe', pconfig.autosave.status_save_pending)

    def test_mesh_roundtrip_with_real_bed_mesh_profile_manager(self):
        # The mesh LEVIQ_PROBE keeps must equal what Klipper's own
        # BED_MESH_PROFILE / SAVE_CONFIG path stores, with the real ZMesh and
        # ProfileManager of the vanilla-klipper bed_mesh
        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._use_klippy_dir(os.path.join(repo_dir, 'refs', 'vanilla-klipper-app', 'klippy'))
        from extras import bed_mesh as real

        matrix = [[0.0123456, -0.02, 0.03], [0.0, -0.0000001, 0.021], [-0.02, 0.01, 0.04]]
        params = collections.OrderedDict(
            min_x=15., max_x=240., min_y=15., max_y=235., x_count=3, y_count=3,
            mesh_x_pps=2, mesh_y_pps=2, algo='bicubic', tension=0.2)

        class Section:
            def __init__(self, name, values):
                self.name, self.values = name, values

            def get_name(self):
                return self.name

            def getint(self, key, default=0):
                return int(self.values.get(key, default))

            def getfloat(self, key):
                return float(self.values[key])

            def get(self, key):
                return str(self.values[key])

            def getlists(self, key, seps=(',',), parser=float):
                return [[parser(v) for v in line.split(',')]
                        for line in str(self.values[key]).strip().split('\n')]

        class StubPrinter:
            def __init__(self):
                self.objects = {'gcode': GCode(self), 'configfile': ConfigFile()}

            def lookup_object(self, name, default=None):
                return self.objects.get(name, default)

        class Shim:
            """bed_mesh.BedMesh with the real ProfileManager and ZMesh."""

            def __init__(self, stored=None):
                self.pstub = StubPrinter()
                cfg = type('Cfg', (), {
                    'get_name': lambda c: 'bed_mesh',
                    'get_printer': lambda c: self.pstub,
                    'get_prefix_sections': lambda c, prefix: stored or []})()
                self.z_mesh = None
                self.pmgr = real.ProfileManager(cfg, self)

            def get_mesh(self):
                return self.z_mesh

            def set_mesh(self, mesh):
                self.z_mesh = mesh

            def update_status(self):
                pass

        def calibrate(shim, name):
            # BED_MESH_CALIBRATE: build the mesh, set it, save_profile(name)
            zmesh = real.ZMesh(collections.OrderedDict(params), name)
            zmesh.build_mesh([list(row) for row in matrix])
            shim.set_mesh(zmesh)
            shim.pmgr.save_profile(name)

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state_file = os.path.join(tmp.name, 'leviq3_state.json')
        shim = Shim()
        printer = Printer(self.state_file, objects={'bed_mesh': shim}, mesh_gcode='MY_MESH')
        printer.objects['gcode'].register_command('MY_MESH', lambda _g: calibrate(shim, 'default'))
        printer.run("LEVIQ_PROBE")
        save_config_pending = shim.pstub.objects['configfile'].pending   # what SAVE_CONFIG would write
        self.assertIn(('bed_mesh default', 'points'), save_config_pending)
        kept = json.load(open(self.state_file))['mesh']
        self.assertEqual(kept['profile'], 'default')

        # restart without SAVE_CONFIG: the kept mesh is injected and loaded
        fresh = Shim()
        Printer(self.state_file, objects={'bed_mesh': fresh}, mesh_gcode='MY_MESH')
        self.assertEqual(fresh.get_mesh().get_profile_name(), 'default')
        self.assertEqual(fresh.get_mesh().get_probed_matrix(),
                         shim.get_mesh().get_probed_matrix())
        # the profile holds the probed matrix rounded to 6 decimals, as
        # Klipper's own save_profile does, so the interpolation can differ
        # in the last digit
        for got, want in zip(fresh.get_mesh().get_mesh_matrix(),
                             shim.get_mesh().get_mesh_matrix()):
            for a, b in zip(got, want):
                self.assertAlmostEqual(a, b, delta=2e-6)
        self.assertEqual(fresh.pmgr.profiles['default']['points'],
                         shim.get_mesh().get_probed_matrix())

        # restart after SAVE_CONFIG: Klipper's own profile has the same
        # fingerprint, so it is used as it is and nothing is injected
        values = {key: value for (sec, key), value in save_config_pending.items()
                  if sec == 'bed_mesh default'}
        stored = Shim([Section('bed_mesh default', values)])
        config_profile = stored.pmgr.profiles['default']
        Printer(self.state_file, objects={'bed_mesh': stored}, mesh_gcode='MY_MESH')
        self.assertIs(stored.pmgr.profiles['default'], config_profile)
        self.assertEqual(stored.get_mesh().get_probed_matrix(),
                         shim.get_mesh().get_probed_matrix())

    def test_loads_real_cs1237_and_probe_ks1_with_fake_mcu(self):
        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        klippy_dir = os.path.join(repo_dir, 'refs', 'vanilla-klipper-app', 'klippy')
        self._use_klippy_dir(klippy_dir)

        import configfile
        import gcode
        from extras import cs1237 as real_cs1237_mod
        from extras import probe_ks1 as real_probe_ks1_mod

        class FakeMcuCmd:
            def __init__(self, fmt, handler=None):
                self.fmt = fmt
                self.handler = handler
                self.sent = []

            def send(self, args, minclock=0):
                self.sent.append(tuple(args))
                if self.handler is not None:
                    self.handler(args)

        class FakeNozzleMCU:
            def __init__(self, printer):
                self.printer = printer
                self.oids = 0
                self.config_callbacks = []
                self.config_cmds = []
                self.responses = {}
                self.enable_log = []
                self.rng = random.Random(7)

            def create_oid(self):
                oid = self.oids
                self.oids += 1
                return oid

            def register_config_callback(self, cb):
                self.config_callbacks.append(cb)

            def add_config_cmd(self, cmd):
                self.config_cmds.append(cmd)

            def seconds_to_clock(self, secs):
                return int(secs * 1000000)

            def register_serial_response(self, cb, msg_fmt, oid):
                self.responses[msg_fmt.split()[0]] = cb

            def check_valid_response(self, msg_fmt):
                return False

            def try_lookup_command(self, msg_fmt):
                return None

            def lookup_command(self, msg_fmt):
                name = msg_fmt.split()[0]
                if name == 'query_cs1237_diff':
                    def _on_diff(_args):
                        th = self.printer.lookup_object('toolhead')
                        load = max(0., -th.world_z)
                        diff = int(-load * COUNTS_PER_MM + self.rng.uniform(-300, 300))
                        self.responses['cs1237_diff']({'diff': diff, 'raw': diff})
                    return FakeMcuCmd(msg_fmt, _on_diff)
                if name == 'enable_cs1237':
                    return FakeMcuCmd(msg_fmt, lambda a: self.enable_log.append(a[1]))
                if name == 'checkself_cs1237':
                    return FakeMcuCmd(
                        msg_fmt,
                        lambda _a: self.responses['cs1237_checkself_flag']({'flag': 1}))
                return FakeMcuCmd(msg_fmt)

            def build_config(self):
                for cb in self.config_callbacks:
                    cb()

        class FakePins:
            def __init__(self):
                self.chips = {}

            def register_chip(self, name, chip):
                self.chips[name] = chip

            def lookup_pin(self, pin_desc, can_invert=False, can_pullup=False, share_type=None):
                return {'pin': pin_desc, 'invert': False, 'pullup': False, 'chip': None}

            def setup_pin(self, pin_type, pin_desc):
                class _FakeEndstop:
                    def get_mcu(self):
                        return None
                    def add_stepper(self, stepper):
                        pass
                    def get_steppers(self):
                        return []
                    def home_start(self, *args):
                        pass
                    def home_wait(self, *args):
                        return 0.
                    def query_endstop(self, print_time):
                        return 0
                return _FakeEndstop()

        class FakeGCodeMacro:
            def load_template(self, config, option, default=''):
                return type('Tmpl', (), {'run_gcode_from_command': lambda self: None})()

        class VanillaHarnessPrinter:
            config_error = configfile.error
            command_error = gcode.CommandError

            def __init__(self, cfg_path):
                self.start_args = {'config_file': cfg_path}
                self.reactor = Reactor()
                self.reactor.register_callback = lambda cb: cb(0.)
                self.handlers = {}
                self.objects = {}
                self.objects['pins'] = FakePins()
                self.objects['mcu nozzle_mcu'] = FakeNozzleMCU(self)
                self.objects['gcode'] = gcode.GCodeDispatch(self)
                self.objects['gcode_move'] = GCodeMove(self)
                self.objects['toolhead'] = Toolhead(self, -0.1)
                self.objects['extruder'] = Extruder()
                self.objects['gcode_macro'] = FakeGCodeMacro()
                self._wrap_gcode()

            def _wrap_gcode(self):
                orig_run = self.objects['gcode'].run_script_from_command
                printer = self

                def _script_runner(script):
                    for line in script.split('\n'):
                        line = line.strip()
                        if not line:
                            continue
                        words = line.split()
                        cmd = words[0].upper()
                        th = printer.lookup_object('toolhead')
                        gm = printer.lookup_object('gcode_move')
                        if cmd == 'G28':
                            probe_obj = printer.lookup_object('probe')
                            probe_obj.multi_probe_begin()
                            th.frame = -PRESS - th.pos[2]
                            pos = th.get_position()
                            pos[2] = th.kin.rails[2].position_endstop
                            th.set_position(pos)
                            th.homed = 'xyz'
                            probe_obj.multi_probe_end()
                            gm.g1({'Z': 5.})
                        elif cmd == 'G1':
                            params = ({w[0].upper(): w[1:] for w in words[1:]}
                                      if '=' not in line
                                      else dict(w.upper().split('=', 1) for w in words[1:]))
                            gm.g1(params)
                        elif cmd == 'SET_GCODE_OFFSET':
                            params = dict(w.upper().split('=', 1) for w in words[1:])
                            gm.set_gcode_offset(params)
                        elif cmd in ('M104', 'M109'):
                            params = {w[0].upper(): w[1:] for w in words[1:]}
                            printer.objects['extruder'].heater.target = float(params['S'])
                        elif cmd in ('M140', 'M190', 'M106', 'M107', 'M400',
                                     'G4', 'G90', 'G92', 'M82', 'M83',
                                     'BED_MESH_CLEAR', 'BED_MESH_CALIBRATE',
                                     'NOZZLE_WIPE_SEQUENCE'):
                            pass
                        else:
                            orig_run(line)

                self.objects['gcode'].run_script_from_command = _script_runner

            def get_start_args(self):
                return self.start_args

            def get_reactor(self):
                return self.reactor

            def register_event_handler(self, event, callback):
                self.handlers.setdefault(event, []).append(callback)

            def lookup_object(self, name, default=None):
                return self.objects.get(name, default)

            def lookup_objects(self, module=None):
                if module is None:
                    return list(self.objects.items())
                prefix = module + ' '
                return [(n, o) for n, o in self.objects.items()
                        if n == module or n.startswith(prefix)]

            def load_object(self, config, section, default=configfile.sentinel):
                if section in self.objects:
                    return self.objects[section]
                if default is not configfile.sentinel:
                    return default
                raise self.config_error("Unexpected load_object: %s" % section)

            def add_object(self, name, obj):
                self.objects[name] = obj

            def send_event(self, event, *params):
                return [cb(*params) for cb in self.handlers.get(event, [])]

        with tempfile.TemporaryDirectory() as tmpdir:
            leviq3_cfg_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'leviq3.cfg')
            state_path = os.path.join(tmpdir, 'leviq3_state.json')
            printer_cfg = os.path.join(tmpdir, 'printer.cfg')
            with open(printer_cfg, 'w') as f:
                f.write(
                    "[stepper_z]\nposition_min: -4\n"
                    "[cs1237]\nlevel_pin: nozzle_mcu:PA7\n"
                    "dout_pin: nozzle_mcu:PA6\nsclk_pin: nozzle_mcu:PA5\n"
                    "register: 60\nsensitivity: -3000\n"
                    "[probe_ks1]\npin: PB15\nx_offset: 0\ny_offset: 0\n"
                    "z_offset: -0.100\nspeed: 6.0\nlift_speed: 6.0\n"
                    "[include %s]\n[leviq3]\nauto_zoffset_on_off: 1\nstate_file: %s\n"
                    % (leviq3_cfg_src, state_path)
                )

            printer = VanillaHarnessPrinter(printer_cfg)
            pconfig = configfile.PrinterConfig(printer)
            printer.objects['configfile'] = pconfig
            cfg = pconfig.read_main_config()

            printer.objects['cs1237'] = real_cs1237_mod.load_config(cfg.getsection('cs1237'))
            printer.objects['probe_ks1'] = real_probe_ks1_mod.load_config(cfg.getsection('probe_ks1'))
            printer.objects['leviq3'] = leviq3.load_config(cfg.getsection('leviq3'))

            # Consume stepper_z position_min before unused-option validation
            cfg.getsection('stepper_z').getfloat('position_min', 0.0)
            pconfig.check_unused_options(cfg)

            printer.objects['mcu nozzle_mcu'].build_config()
            printer.send_event('klippy:connect')
            printer.send_event('klippy:ready')

            self.assertIsInstance(printer.lookup_object('probe'), real_probe_ks1_mod.ProbeKS1)
            self.assertIsInstance(printer.lookup_object('cs1237'), real_cs1237_mod.CS1237)

            gdispatch = printer.objects['gcode']
            for cmd in self.EXPECTED_COMMANDS:
                self.assertIn(cmd, gdispatch.ready_gcode_handlers)

            gcmd = gdispatch.create_gcode_command("LEVIQ_AUTO_ZOFFSET", "LEVIQ_AUTO_ZOFFSET", {})
            printer.objects['leviq3'].cmd_LEVIQ_AUTO_ZOFFSET(gcmd)

            measured = printer.objects['probe'].get_offsets()[2]
            self.assertGreaterEqual(measured, -PRESS + 0.075 - 3 * 0.0375 - 1e-4)
            self.assertAlmostEqual(printer.objects['probe'].get_position_endstop(), measured, places=6)
            self.assertAlmostEqual(printer.objects['probe'].mcu_endstop.position_endstop, measured, places=6)
            self.assertEqual(pconfig.autosave.status_save_pending['probe_ks1']['z_offset'],
                             "%.3f" % measured)
            # Last CS1237 command must disable sensor after measurement ends;
            # also verify leviq3 re-enabled CS1237 right after ProbeKS1 disabled it on G28 Z
            elog = printer.objects['mcu nozzle_mcu'].enable_log
            self.assertEqual(elog[-1], 0)
            self.assertIn((0, 1), list(zip(elog, elog[1:])))

    def test_cold_retract_with_real_extruder_and_heater(self):
        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        klippy_dir = os.path.join(repo_dir, 'refs', 'vanilla-klipper-app', 'klippy')
        self._use_klippy_dir(klippy_dir)

        import configfile
        import gcode
        from extras import heaters as real_heaters_mod
        from kinematics import extruder as real_extruder_mod

        class FakePwmPin:
            def get_mcu(self):
                return type('FakeMcu', (), {
                    'estimated_print_time': lambda self, et: et,
                })()

            def setup_cycle_time(self, cycle_time):
                pass

            def setup_max_duration(self, max_duration):
                pass

            def set_pwm(self, read_time, value):
                pass

        class FakePins:
            def setup_pin(self, pin_type, pin_desc):
                return FakePwmPin()

        class FakeSensor:
            def __init__(self, config):
                self.cb = None

            def setup_minmax(self, min_temp, max_temp):
                pass

            def setup_callback(self, cb):
                self.cb = cb

            def get_report_time_delta(self):
                return 0.3

        class FakeMove:
            def __init__(self, e_delta=-0.05):
                self.axes_r = [0., 0., 0., -1. if e_delta < 0 else 1.]
                self.axes_d = [0., 0., 0., e_delta]
                self.move_d = abs(e_delta)

            def limit_speed(self, velocity, accel):
                pass

        class ExtruderHarnessPrinter:
            config_error = configfile.error
            command_error = gcode.CommandError

            def __init__(self, cfg_path):
                self.start_args = {'config_file': cfg_path}
                self.reactor = Reactor()
                self.handlers = {}
                self.objects = {}
                self.messages = []
                self.e_moves = []
                self.raise_on_e_move = False
                self.objects['pins'] = FakePins()
                self.objects['gcode'] = gcode.GCodeDispatch(self)
                self.objects['gcode_move'] = GCodeMove(self)
                self.objects['probe'] = Probe(-0.1)
                self.objects['cs1237'] = CS1237(self)
                self.objects['motion_queuing'] = type('FakeMQ', (), {
                    'allocate_trapq': lambda self: None,
                    'lookup_trapq_append': lambda self: lambda *a: None,
                })()
                th = Toolhead(self, -0.1)
                th.get_max_velocity = lambda: (600., 20000.)
                th.set_extruder = lambda ext, pos: setattr(th, '_ext', ext)
                th.get_extruder = lambda: th._ext
                self.objects['toolhead'] = th
                self._wrap_gcode()

            def _wrap_gcode(self):
                printer = self

                def _script_runner(script):
                    for line in script.split('\n'):
                        line = line.strip()
                        if not line:
                            continue
                        words = line.split()
                        cmd = words[0].upper()
                        if cmd == 'G1':
                            params = {w[0].upper(): w[1:] for w in words[1:]}
                            if 'E' in params:
                                e_val = float(params['E'])
                                if printer.raise_on_e_move:
                                    raise printer.command_error("simulated move failure")
                                printer.objects['extruder'].check_move(FakeMove(e_val), 3)
                                printer.e_moves.append(e_val)
                        elif cmd == 'M109':
                            t = float(words[1][1:])
                            h = printer.objects['extruder'].get_heater()
                            h.set_temp(t)
                            # Drive smoothed_temp to requested target via real Heater callback
                            now = h.last_temp_time + 2.
                            h.temperature_callback(now, t)
                            h.temperature_callback(now + 2., t)

                self.objects['gcode'].run_script_from_command = _script_runner

            def get_start_args(self):
                return self.start_args

            def get_reactor(self):
                return self.reactor

            def register_event_handler(self, event, callback):
                self.handlers.setdefault(event, []).append(callback)

            def lookup_object(self, name, default=None):
                return self.objects.get(name, default)

            def lookup_objects(self, module=None):
                return list(self.objects.items())

            def load_object(self, config, section, default=configfile.sentinel):
                if section in self.objects:
                    return self.objects[section]
                if section == 'heaters':
                    ph = real_heaters_mod.PrinterHeaters(config)
                    ph.add_sensor_factory('fake_ntc', FakeSensor)
                    ph.have_load_sensors = True
                    self.objects['heaters'] = ph
                    return ph
                if section.startswith(('verify_heater ', 'pid_calibrate')):
                    return type('Stub', (), {})()
                raise self.config_error("Unexpected load_object: %s" % section)

            def is_shutdown(self):
                return False

            def send_event(self, event, *params):
                return [cb(*params) for cb in self.handlers.get(event, [])]

        def build_printer(tmpdir, extra_leviq_cfg=""):
            cfg_path = os.path.join(tmpdir, 'printer.cfg')
            state_path = os.path.join(tmpdir, 'state.json')
            with open(cfg_path, 'w') as f:
                f.write(
                    "[extruder]\n"
                    "nozzle_diameter: 0.400\n"
                    "filament_diameter: 1.750\n"
                    "heater_pin: nozzle_mcu:PB8\n"
                    "sensor_type: fake_ntc\n"
                    "control: pid\npid_Kp: 19\npid_Ki: 1\npid_Kd: 100\n"
                    "min_temp: -200\nmax_temp: 320\nmin_extrude_temp: 170\n"
                    "[leviq3]\n"
                    "auto_zoffset_on_off: 0\n"
                    "extru_end_temp: 140\n"
                    "state_file: %s\n%s" % (state_path, extra_leviq_cfg)
                )
            printer = ExtruderHarnessPrinter(cfg_path)
            pconfig = configfile.PrinterConfig(printer)
            printer.objects['configfile'] = pconfig
            cfg = pconfig.read_main_config()
            printer.objects['extruder'] = real_extruder_mod.PrinterExtruder(
                cfg.getsection('extruder'), 0)
            printer.objects['leviq3'] = leviq3.load_config(cfg.getsection('leviq3'))
            printer.send_event('klippy:connect')
            printer.send_event('klippy:ready')
            return printer

        with tempfile.TemporaryDirectory() as tmpdir:
            # 1) Cold retract at 140C (< min_extrude_temp 170C) succeeds and restores check
            p = build_printer(tmpdir)
            heater = p.objects['extruder'].get_heater()
            gcmd = p.objects['gcode'].create_gcode_command(
                "LEVIQ_AUTO_ZOFFSET", "LEVIQ_AUTO_ZOFFSET", {})
            p.objects['leviq3'].cmd_LEVIQ_AUTO_ZOFFSET(gcmd)
            self.assertEqual(p.e_moves, [-0.05])
            self.assertEqual(heater.min_extrude_temp, 170.)
            self.assertFalse(heater.can_extrude)
            with self.assertRaisesRegex(gcode.CommandError, "Extrude below minimum temp"):
                p.objects['extruder'].check_move(FakeMove(-0.05), 3)

            # 2) Exception inside the move still restores min_extrude_temp and can_extrude
            p.raise_on_e_move = True
            with self.assertRaises(gcode.CommandError):
                p.objects['leviq3'].cmd_LEVIQ_AUTO_ZOFFSET(gcmd)
            self.assertEqual(heater.min_extrude_temp, 170.)
            self.assertFalse(heater.can_extrude)

            # 3) cold_retract: False skips the move cleanly and informs the console
            p_off = build_printer(tmpdir, "cold_retract: False\n")
            gcmd_off = GCmd({})
            p_off.objects['leviq3'].cmd_LEVIQ_AUTO_ZOFFSET(gcmd_off)
            self.assertEqual(p_off.e_moves, [])
            self.assertTrue(any("cold_retract=False" in m for m in gcmd_off.responses))

            # 4) Hot enough (>= 170C): leaves heater state and lock untouched
            p_hot = build_printer(tmpdir, "extru_end_temp: 190\n")
            h_hot = p_hot.objects['extruder'].get_heater()
            gcmd_hot = p_hot.objects['gcode'].create_gcode_command(
                "LEVIQ_AUTO_ZOFFSET", "LEVIQ_AUTO_ZOFFSET", {})
            p_hot.objects['leviq3'].cmd_LEVIQ_AUTO_ZOFFSET(gcmd_hot)
            self.assertEqual(p_hot.e_moves, [-0.05])
            self.assertTrue(h_hot.can_extrude)
            self.assertEqual(h_hot.min_extrude_temp, 170.)
            orig_lock = h_hot.lock
            h_hot.lock = type('NoLock', (), {
                '__enter__': lambda s: self.fail("should not touch lock when can_extrude=True"),
                '__exit__': lambda s, *a: False,
            })()
            p_hot.objects['leviq3']._run_cold_retract(gcmd_hot, "G1 E-0.05 F100")
            self.assertEqual(p_hot.e_moves, [-0.05, -0.05])
            h_hot.lock = orig_lock


class Paket5HardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_file = os.path.join(self.tmp.name, 'leviq3_state.json')

    def printer(self, **config):
        config.setdefault('auto_zoffset_on_off', 1)
        return Printer(self.state_file, **config)

    def test_cold_retract_holds_heater_lock_during_script(self):
        printer = self.printer()
        events = []

        class TrackingLock:
            def __init__(self):
                self.held = False

            def __enter__(self):
                self.held = True
                events.append('lock_enter')
                return self

            def __exit__(self, *args):
                self.held = False
                events.append('lock_exit')
                return False

        heater = printer.objects['extruder'].get_heater()
        heater.can_extrude = False
        heater.min_extrude_temp = 170.
        heater.lock = TrackingLock()

        orig_run = printer.objects['gcode'].run_script_from_command

        def spy_run(script):
            if script == "G1 E-0.05 F100":
                self.assertTrue(heater.lock.held)
                self.assertTrue(heater.can_extrude)
                self.assertEqual(heater.min_extrude_temp, 0.)
                events.append('script')
            return orig_run(script)

        printer.objects['gcode'].run_script_from_command = spy_run
        printer.leviq._run_cold_retract(GCmd({}), "G1 E-0.05 F100")
        self.assertEqual(events, ['lock_enter', 'script', 'lock_exit'])
        self.assertFalse(heater.can_extrude)
        self.assertEqual(heater.min_extrude_temp, 170.)

    def test_m83_restored_to_m82_on_cold_retract_error(self):
        for cmd in ("LEVIQ_AUTO_ZOFFSET", "LEVIQ_PREHEATING"):
            printer = self.printer()
            gcode = printer.objects['gcode']
            orig_run = gcode.run_script_from_command

            def fail_retract(script):
                if script.startswith("G1 E-"):
                    raise CommandError("retract blocked")
                return orig_run(script)

            gcode.run_script_from_command = fail_retract
            with self.assertRaises(CommandError):
                printer.run(cmd)
            self.assertIn("M83", gcode.log)
            self.assertIn("M82", gcode.log)
            self.assertGreater(gcode.log.index("M82"), gcode.log.index("M83"))

    def test_double_probe_failure_in_rewipe_window_does_not_heat_or_save_config(self):
        printer = self.printer(
            z_offset=-0.02, extru_temp=170., wipe_gcode='MY_WIPE',
            rewipe_gcode='MY_WIPE', zoffset_factor=0.05, retry_count=1)
        printer.objects['cs1237'].silent = True
        gcmd = printer.run("LEVIQ_AUTO_ZOFFSET")
        self.assertAlmostEqual(printer.z_offset(), -0.02, places=6)
        self.assertNotIn("M109 S170.000000", printer.objects['gcode'].log)
        self.assertNotIn("MY_WIPE", printer.objects['gcode'].log)
        self.assertEqual(printer.objects['configfile'].pending, {})
        self.assertFalse(os.path.exists(self.state_file))
        self.assertTrue(any("readings disagree" in r for r in gcmd.responses))

    def test_half_failed_measure_pair_is_rejected(self):
        printer = self.printer(
            z_offset=-0.02, extru_temp=170., wipe_gcode='MY_WIPE',
            rewipe_gcode='MY_WIPE', zoffset_factor=0.05, retry_count=1)
        cs = printer.objects['cs1237']
        attempts = [0]
        orig_measure = printer.leviq._measure_zoffset

        def alternating_measure():
            attempts[0] += 1
            if attempts[0] in (1, 3):
                raise CommandError("simulated first probe failure")
            return orig_measure()

        printer.leviq._measure_zoffset = alternating_measure
        gcmd = printer.run("LEVIQ_AUTO_ZOFFSET")
        self.assertEqual(attempts[0], 4)
        self.assertAlmostEqual(printer.z_offset(), -0.02, places=6)
        self.assertNotIn("M109 S170.000000", printer.objects['gcode'].log)
        self.assertNotIn("MY_WIPE", printer.objects['gcode'].log)
        self.assertEqual(printer.objects['configfile'].pending, {})
        self.assertTrue(any("readings disagree" in r for r in gcmd.responses))

    def test_snapshot_zoffset_preserves_zero_offset(self):
        printer = self.printer(wipe_gcode='MY_WIPE', mesh_gcode='MY_MESH')
        printer.leviq.saved_z_offset = -0.15
        printer.run("LEVIQ_SET_ZERO")
        self.assertEqual(printer.z_offset(), 0.)
        printer.run("LEVIQ B=60 S=170")
        self.assertEqual(printer.z_offset(), 0.)

        # Calling LEVIQ_PREHEATING twice before LEVIQ_PROBE keeps the pre-preheat non-zero offset
        printer2 = self.printer(z_offset=-0.12, wipe_gcode='MY_WIPE', mesh_gcode='MY_MESH')
        printer2.run("LEVIQ_PREHEATING")
        self.assertEqual(printer2.z_offset(), 0.)
        printer2.run("LEVIQ_PREHEATING")
        printer2.run("LEVIQ_PROBE")
        self.assertAlmostEqual(printer2.z_offset(), -0.12, places=6)

    def test_temp_offset_uses_active_heater_and_move_zero_when_unhomed(self):
        printer = self.printer()
        alt_heater = Heater()
        alt_heater.target = 220.
        alt_extruder = type('AltExtruder', (), {'get_heater': lambda s: alt_heater})()
        printer.objects['extruder'].heater.target = 0.
        printer.objects['toolhead'].get_extruder = lambda: alt_extruder

        # Unhomed -> MOVE=0
        printer.objects['toolhead'].homed = ''
        printer.run("LEVIQ_TEMP_OFFSET")
        self.assertAlmostEqual(printer.z_offset(), -0.1 - 0.0375, places=6)
        set_lines = [l for l in printer.objects['gcode'].log if l.startswith("SET_GCODE_OFFSET")]
        self.assertTrue(any("MOVE=0" in l for l in set_lines))
        self.assertFalse(any("MOVE=1" in l for l in set_lines))

        # Homed -> MOVE=1
        printer.objects['gcode'].log.clear()
        printer.objects['toolhead'].homed = 'xyz'
        printer.run("LEVIQ_TEMP_OFFSET TEMP=140")
        set_lines = [l for l in printer.objects['gcode'].log if l.startswith("SET_GCODE_OFFSET")]
        self.assertTrue(any("MOVE=1" in l for l in set_lines))


class PhaseTests(unittest.TestCase):
    """get_status()['phase'] tells the touchscreen what a LEVIQ* command is doing."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_file = os.path.join(self.tmp.name, 'leviq3_state.json')

    def phases_during(self, line, **config):
        config.setdefault('auto_zoffset_on_off', 1)
        printer = Printer(self.state_file, **config)
        lq = printer.leviq
        seen = []
        orig_run = lq._run
        def run(script):
            if not seen or seen[-1] != lq.phase:
                seen.append(lq.phase)
            return orig_run(script)
        lq._run = run
        printer.run(line)
        return seen, lq.get_status(0)['phase']

    def test_leviq_reports_each_stage_and_clears_at_the_end(self):
        seen, after = self.phases_during("LEVIQ")
        self.assertEqual(seen, ['heating', 'wiping', 'cooling', 'homing', 'mesh'])
        self.assertEqual(after, '')

    def test_auto_zoffset_reports_cooling_then_measuring(self):
        seen, after = self.phases_during("LEVIQ_AUTO_ZOFFSET FORCE=1")
        self.assertEqual(seen[:2], ['cooling', 'zoffset'])
        self.assertEqual(after, '')

    def test_phase_is_idle_before_any_command(self):
        printer = Printer(self.state_file, auto_zoffset_on_off=1)
        self.assertEqual(printer.leviq.get_status(0)['phase'], '')


class ShaperCalcPriorityTests(unittest.TestCase):
    """The shaper fit runs in a child process; only the child drops to nice 19."""

    def fake_module(self):
        import multiprocessing

        class ShaperCalibrate:
            def __init__(self, in_process=False):
                self.in_process = in_process

            # Same shape as klippy/extras/shaper_calibrate.py: the method runs
            # in a forked child and its result comes back through a pipe
            def background_process_exec(self, method, args):
                if self.in_process:
                    return method(*args)
                parent_conn, child_conn = multiprocessing.Pipe()
                def wrapper():
                    child_conn.send(method(*args))
                    child_conn.close()
                proc = multiprocessing.get_context('fork').Process(target=wrapper)
                proc.start()
                res = parent_conn.recv()
                proc.join()
                return res

        return type('shaper_calibrate', (), {'ShaperCalibrate': ShaperCalibrate})

    def test_child_runs_at_lowest_priority_parent_keeps_its_own(self):
        mod = self.fake_module()
        leviq3.lower_shaper_calc_priority(mod)
        before = os.nice(0)
        child_nice, value = mod.ShaperCalibrate().background_process_exec(
            lambda a, b: (os.nice(0), a + b), (2, 3))
        self.assertEqual(child_nice, 19)
        self.assertEqual(value, 5)
        self.assertEqual(os.nice(0), before)

    def test_in_process_call_does_not_change_priority(self):
        mod = self.fake_module()
        leviq3.lower_shaper_calc_priority(mod)
        before = os.nice(0)
        self.assertEqual(mod.ShaperCalibrate(in_process=True).background_process_exec(
            lambda: os.nice(0), ()), before)
        self.assertEqual(os.nice(0), before)

    def test_patch_is_applied_once(self):
        mod = self.fake_module()
        leviq3.lower_shaper_calc_priority(mod)
        patched = mod.ShaperCalibrate.background_process_exec
        leviq3.lower_shaper_calc_priority(mod)
        self.assertIs(mod.ShaperCalibrate.background_process_exec, patched)


if __name__ == '__main__':
    unittest.main()
