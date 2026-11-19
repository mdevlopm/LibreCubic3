# LeviQ3 support for Anycubic Kobra 3 on mainline Klipper
#
# Clean-room port of the LEVIQ* commands of the stock firmware (gklib 2.4.6.7):
# automatic z-offset by nozzle contact, nozzle temperature compensation and
# the leveling commands the touchscreen calls.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import collections, contextlib, hashlib, json, logging, os

# LEVIQ_TEMP_OFFSET reference points, constants in the stock firmware
TEMP_OFFSET_BASE = 140.
TEMP_OFFSET_SPAN = 80.

QUERY_TIMEOUT = 2.
# z_offset values go to the config file with 3 decimals; two values closer
# than this are the same value
Z_OFFSET_TOLERANCE = 0.0006


class LeviQ3:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        # Same keys and defaults as the stock [leviQ3] section
        self.bed_temp = config.getfloat('bed_temp', 60.)
        self.extru_temp = config.getfloat('extru_temp', 190.)
        self.extru_end_temp = config.getfloat('extru_end_temp', 140.)
        self.z_up = config.getfloat('z_up', 30.)
        self.to_z_up_speed = config.getfloat('to_z_up_speed', 15., above=0.)
        self.platform_offset_extru = config.getfloatlist(
            'platform_offset_extru', (245., 245., 0.2))
        self.return_distance = config.getfloat('return_distance', 0.075)
        self.expansion_factor = config.getfloat('expansion_factor', 0.0375)
        self.max_diff = config.getfloat('max_diff', 5000.)
        self.noise_diff = config.getfloat('noise_diff', 2500., above=0.)
        self.auto_step = config.getfloat('auto_step', 0.03, above=0.)
        self.zoffset_factor = config.getfloat('zoffset_factor', 0.03)
        self.auto_zoffset_on_off = config.getint('auto_zoffset_on_off', 0)
        # Additions of this port
        # The stock loop has no travel bound; stop if contact never clears
        self.max_auto_travel = config.getfloat('max_auto_travel', 2., above=0.)
        self.bed_soak_time = config.getfloat('bed_soak_time', 0., minval=0.)
        self.wipe_gcode = config.get('wipe_gcode', 'NOZZLE_WIPE_SEQUENCE').strip()
        self.rewipe_gcode = config.get('rewipe_gcode', self.wipe_gcode).strip()
        self.mesh_gcode = config.get('mesh_gcode',
                                     'BED_MESH_CALIBRATE ADAPTIVE=1').strip()
        self.state_file = os.path.expanduser(config.get(
            'state_file', '~/printer_data/config/leviq3_state.json'))
        self.cold_retract = config.getboolean('cold_retract', True)
        self.restore_zoffset = config.getboolean('restore_zoffset', True)
        # Dirty-nozzle re-wipes of LEVIQ_AUTO_ZOFFSET when RETRY_COUNT is not
        # given.  1 is the stock behaviour (at least one re-wipe); 0 = never.
        self.retry_count = config.getint('retry_count', 1, minval=0)
        # Keep the mesh of LEVIQ_PROBE across restarts (see _persist_mesh)
        self.mesh_persist = config.getboolean('mesh_persist', True)
        self.mesh_autoload = config.getboolean('mesh_autoload', True)
        self.mesh_save_config = config.getboolean('mesh_save_config', False)
        self.temp_comp = 0.
        self.saved_z_offset = 0.
        self.saved_temp_comp = 0.
        # z_offset the config file holds (read at connect) and the value last
        # offered to SAVE_CONFIG; they tell the state file from config edits
        self.cfg_z_offset = 0.
        self.offered_z_offset = None
        self._zeroed_for_leveling = False
        # fingerprints of the bed_mesh profiles the config file holds
        self.mesh_cfg_fps = {}
        self.probe = self.cs1237 = None
        self.printer.register_event_handler('klippy:connect',
                                            self._handle_connect)
        self.printer.register_event_handler('klippy:ready',
                                            self._handle_ready)
        for name, func, desc in (
                ('LEVIQ', self.cmd_LEVIQ, self.cmd_LEVIQ_help),
                ('LEVIQ_SET_ZERO', self.cmd_LEVIQ_SET_ZERO,
                 self.cmd_LEVIQ_SET_ZERO_help),
                ('LEVIQ_PREHEATING', self.cmd_LEVIQ_PREHEATING,
                 self.cmd_LEVIQ_PREHEATING_help),
                ('LEVIQ_WIPING', self.cmd_LEVIQ_WIPING,
                 self.cmd_LEVIQ_WIPING_help),
                ('LEVIQ_PROBE', self.cmd_LEVIQ_PROBE,
                 self.cmd_LEVIQ_PROBE_help),
                ('LEVIQ_AUTO_ZOFFSET', self.cmd_LEVIQ_AUTO_ZOFFSET,
                 self.cmd_LEVIQ_AUTO_ZOFFSET_help),
                ('LEVIQ_TEMP_OFFSET', self.cmd_LEVIQ_TEMP_OFFSET,
                 self.cmd_LEVIQ_TEMP_OFFSET_help),
                ('LEVIQ_AUTO_ZOFFSET_ON_OFF',
                 self.cmd_LEVIQ_AUTO_ZOFFSET_ON_OFF,
                 self.cmd_LEVIQ_AUTO_ZOFFSET_ON_OFF_help)):
            self.gcode.register_command(name, func, desc=desc)

    def _handle_connect(self):
        self.probe = (self.printer.lookup_object('probe', None)
                      or self.printer.lookup_object('probe_ks1', None))
        if self.probe is None:
            raise self.printer.config_error(
                "leviq3 requires a [probe], [load_cell_probe], "
                "or [probe_ks1] section")
        self.cs1237 = self.printer.lookup_object('cs1237', None)
        # The probe holds the z_offset of the config file right now
        self.cfg_z_offset = self._get_zoffset()
        state = self._load_state()
        if 'auto_zoffset_on_off' in state:
            self.auto_zoffset_on_off = int(state['auto_zoffset_on_off'])
        self.temp_comp = 0.
        stale = False
        z_offset = state.get('z_offset')
        if z_offset is not None:
            if abs(z_offset) > self.max_auto_travel:
                logging.warning("leviq3: ignoring saved z_offset %s", z_offset)
            elif not self._state_matches_config(state):
                # SAVE_CONFIG / Z_OFFSET_APPLY_PROBE / a hand edit changed
                # the config file after the state file was written
                logging.warning(
                    "leviq3: z_offset in the config file (%.4f) changed since"
                    " the state file was written, ignoring saved z_offset %s",
                    self.cfg_z_offset, z_offset)
                stale = True
            else:
                self.temp_comp = float(state.get('temp_comp', 0.))
                self.offered_z_offset = state.get('offered_z_offset')
                self._set_zoffset(float(z_offset))
        self.saved_z_offset = self._get_zoffset()
        self.saved_temp_comp = self.temp_comp
        self._zeroed_for_leveling = False
        if stale:
            self._save_state()

    def _state_matches_config(self, state):
        # The saved z_offset is current if the config file still holds the
        # value it held when the state file was written or the value this
        # module offered to SAVE_CONFIG.  A state file of an older version
        # has neither key and is trusted.
        refs = [state.get(key) for key in ('config_z_offset',
                                           'offered_z_offset')]
        refs = [float(ref) for ref in refs if ref is not None]
        if not refs:
            return True
        return any(abs(self.cfg_z_offset - ref) <= Z_OFFSET_TOLERANCE
                   for ref in refs)

    def get_status(self, eventtime):
        return {'z_offset': self._get_zoffset() if self.probe else 0.,
                'temp_comp': self.temp_comp,
                'auto_zoffset_on_off': self.auto_zoffset_on_off}

    # Saved state (the stock firmware keeps these in printer_mutable.cfg)
    def _load_state(self):
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except IOError:
            return {}
        except ValueError:
            logging.exception("leviq3: unreadable state file %s",
                              self.state_file)
            return {}

    def _probe_section_name(self):
        if hasattr(self.probe, 'name'):
            return self.probe.name
        for helper_attr in ('cmd_helper', '_cmd_helper'):
            helper = getattr(self.probe, helper_attr, None)
            if helper is not None and hasattr(helper, 'name'):
                return helper.name
        if hasattr(self.probe, 'get_status'):
            name = self.probe.get_status(self.reactor.monotonic()).get('name')
            if name:
                return name
        return 'probe'

    def _can_write_state_file(self):
        abs_path = os.path.abspath(self.state_file)
        if (abs_path == '/userdata' or abs_path.startswith('/userdata/')) \
                and not os.path.ismount('/userdata/app/gk/printer_data'):
            logging.warning(
                "leviq3: refusing to write %s without /userdata/app/gk/printer_data bind-mount",
                self.state_file)
            return False
        parent = os.path.dirname(abs_path)
        if parent and not os.path.isdir(parent):
            logging.warning(
                "leviq3: state directory does not exist, skipping write: %s",
                parent)
            return False
        return True

    def _write_state(self, updates, remove=()):
        # Update keys of the state file; the other keys (the saved mesh) stay
        if not self._can_write_state_file():
            return
        state = self._load_state()
        state.update(updates)
        for key in remove:
            state.pop(key, None)
        tmp = self.state_file + '.tmp'
        try:
            with open(tmp, 'w') as f:
                json.dump(state, f, indent=2)
            os.rename(tmp, self.state_file)
        except OSError:
            logging.exception("leviq3: unable to write %s", self.state_file)

    def _save_state(self, update_configfile=False):
        # update_configfile offers the calibrated z_offset to SAVE_CONFIG
        # (like Z_OFFSET_APPLY_PROBE).  Only a real calibration does that:
        # the other callers (the auto z-offset switch, the temperature
        # compensation) must not leave a SAVE_CONFIG pending, and a saved
        # z_offset that includes the temperature compensation is not the
        # calibrated value.
        z_offset = round(self._get_zoffset(), 4)
        if update_configfile:
            value = "%.3f" % (z_offset + self.temp_comp,)
            configfile = self.printer.lookup_object('configfile')
            configfile.set(self._probe_section_name(), 'z_offset', value)
            self.offered_z_offset = float(value)
        state = {'z_offset': z_offset,
                 'temp_comp': round(self.temp_comp, 4),
                 'auto_zoffset_on_off': self.auto_zoffset_on_off,
                 'config_z_offset': round(self.cfg_z_offset, 4)}
        remove = ()
        if self.offered_z_offset is not None:
            state['offered_z_offset'] = self.offered_z_offset
        else:
            remove = ('offered_z_offset',)
        self._write_state(state, remove)

    # Bed mesh persistence.  BED_MESH_CALIBRATE already stores the mesh as a
    # [bed_mesh <profile>] profile, but only for this session (SAVE_CONFIG is
    # what writes it to the config file, and it restarts Klipper), and Klipper
    # never loads a profile by itself.  So the mesh LEVIQ_PROBE measured is
    # also kept in the state file and made active again after a restart.  The
    # config file stays the master: a profile SAVE_CONFIG wrote wins over an
    # older copy in the state file, see _handle_ready.
    @staticmethod
    def _mesh_fingerprint(points, params):
        # Same value for the matrix of a ZMesh and of the stored profile
        text = json.dumps(
            [["%.6f" % (round(z, 6) + 0.,) for z in row] for row in points]
            + [["%.4f" % (round(float(params[key]), 4) + 0.,)
                for key in ('min_x', 'max_x', 'min_y', 'max_y')]])
        return hashlib.sha1(text.encode()).hexdigest()[:16]

    def _handle_ready(self):
        if not self.mesh_persist:
            return
        bed_mesh = self.printer.lookup_object('bed_mesh', None)
        pmgr = getattr(bed_mesh, 'pmgr', None)
        if pmgr is None:
            return
        profiles = pmgr.get_profiles()
        self.mesh_cfg_fps = {
            name: self._mesh_fingerprint(prof['points'], prof['mesh_params'])
            for name, prof in profiles.items()}
        saved = self._load_state().get('mesh')
        if not isinstance(saved, dict):
            return
        try:
            name, points, params = (saved['profile'], saved['points'],
                                    saved['params'])
            saved_fp = self._mesh_fingerprint(points, params)
        except (KeyError, TypeError, ValueError):
            logging.exception("leviq3: unreadable saved bed mesh, dropping it")
            self._write_state({}, remove=('mesh',))
            return
        cfg_fp = self.mesh_cfg_fps.get(name)
        if cfg_fp == saved_fp:
            pass    # SAVE_CONFIG wrote this very mesh
        elif cfg_fp is None or cfg_fp == saved.get('baseline'):
            # not in the config file, or still the profile the config file
            # held when this mesh was measured: the state file is newer
            profiles = dict(profiles)
            profiles[name] = {
                'points': points,
                'mesh_params': collections.OrderedDict(params)}
            pmgr.profiles = profiles
            bed_mesh.update_status()
        else:
            logging.info("leviq3: bed_mesh profile %s of the config file "
                         "changed since LEVIQ_PROBE, dropping the saved mesh",
                         name)
            self._write_state({}, remove=('mesh',))
            return
        if self.mesh_autoload:
            try:
                pmgr.load_profile(name)
            except Exception:
                logging.exception("leviq3: unable to load bed mesh %s", name)

    def _persist_mesh(self, gcmd):
        # Called after the mesh was probed, with the probe z_offset restored
        if not self.mesh_persist:
            return
        bed_mesh = self.printer.lookup_object('bed_mesh', None)
        z_mesh = bed_mesh.get_mesh() if bed_mesh is not None else None
        name = z_mesh.get_profile_name() if z_mesh is not None else None
        if not name or name.startswith('adaptive-'):
            # nothing probed, or a mesh without a profile name
            gcmd.respond_info("leviq3: no named bed mesh to keep")
            return
        points = z_mesh.get_probed_matrix()
        params = dict(z_mesh.get_mesh_params())
        self._write_state({'mesh': {
            'profile': name, 'points': points, 'params': params,
            'baseline': self.mesh_cfg_fps.get(name)}})
        gcmd.respond_info("leviq3: bed mesh '%s' kept for the next start"
                          % (name,))

    # Probe z_offset handling
    def _run(self, script):
        self.gcode.run_script_from_command(script)

    def _get_zoffset(self):
        return self.probe.get_offsets()[2]

    def _z_rail(self):
        kin = self.printer.lookup_object('toolhead').get_kinematics()
        rails = getattr(kin, 'rails', None)
        return rails[2] if rails is not None and len(rails) > 2 else None

    def _set_zoffset(self, z_offset):
        # Change the probe z_offset in place.  The toolhead frame moves with
        # it so the nozzle keeps its physical position and stays homed.
        delta = z_offset - self._get_zoffset()
        for offsets_attr in ('probe_offsets', '_probe_offsets'):
            offsets = getattr(self.probe, offsets_attr, None)
            if offsets is not None and hasattr(offsets, 'z_offset'):
                offsets.z_offset = z_offset
        if hasattr(self.probe, 'z_offset'):
            self.probe.z_offset = z_offset
        for endstop_attr in ('mcu_probe', 'mcu_endstop'):
            endstop = getattr(self.probe, endstop_attr, None)
            if endstop is not None and hasattr(endstop, 'position_endstop'):
                endstop.position_endstop = z_offset
        rail = self._z_rail()
        if rail is not None:
            rail.position_endstop = z_offset
        toolhead = self.printer.lookup_object('toolhead')
        curtime = self.reactor.monotonic()
        if delta and 'z' in toolhead.get_status(curtime)['homed_axes']:
            toolhead.wait_moves()
            pos = toolhead.get_position()
            pos[2] += delta
            toolhead.set_position(pos)

    def _apply_gcode_offset(self, amount=None):
        # Z_OFFSET_APPLY_PROBE that takes effect now: fold the gcode Z
        # offset into the probe z_offset and clear it.  With `amount` only
        # that part is folded and the rest stays, for instance the live
        # fine adjustment the user made on the touchscreen.
        gcode_move = self.printer.lookup_object('gcode_move')
        if amount is None:
            offset = gcode_move.get_status()['homing_origin'][2]
        else:
            offset = amount
        if not offset:
            return
        z_offset = self._get_zoffset() - offset
        if amount is None:
            self._run("SET_GCODE_OFFSET Z=0 MOVE=0")
        else:
            self._run("SET_GCODE_OFFSET Z_ADJUST=%f MOVE=0" % (-offset,))
        self._set_zoffset(z_offset)

    def _set_zero(self):
        self._run("SET_GCODE_OFFSET Z=0 MOVE=0")
        self._set_zoffset(0.)
        self.temp_comp = 0.

    def _snapshot_zoffset(self):
        # Mirror stock gklib +0xac: retain the calibrated/active z_offset
        # across _set_zero() so LEVIQ_PROBE can restore it after meshing.
        if not self._zeroed_for_leveling:
            self.saved_z_offset = self._get_zoffset()
            self.saved_temp_comp = self.temp_comp
            self._zeroed_for_leveling = True

    def _restore_zoffset(self):
        # Mirror stock CMD_LEVIQ3_PROBE: restore probe/endstop z_offset
        # without losing thermal compensation bookkeeping.
        gcode_move = self.printer.lookup_object('gcode_move', None)
        if gcode_move is not None and gcode_move.get_status()['homing_origin'][2]:
            self._run("SET_GCODE_OFFSET Z=0 MOVE=0")
        self._set_zoffset(self.saved_z_offset)
        self.temp_comp = self.saved_temp_comp
        self._zeroed_for_leveling = False

    # Strain gauge access
    def _strain_sensor(self):
        cs = self.cs1237
        if (cs is not None and hasattr(cs, '_enable_cs1237')
                and (hasattr(cs, 'cs1237_diff_process')
                     or hasattr(cs, '_cmd_query_diff'))):
            return ('cs1237', cs)
        lc = (self.printer.lookup_object('load_cell', None)
              or getattr(self.probe, '_load_cell', None))
        if lc is not None and hasattr(lc, 'avg_counts'):
            return ('load_cell', lc)
        raise self.printer.command_error(
            "leviq3: neither [cs1237] nor [load_cell] sensor is available")

    def _enable_strain_gauge(self, enable):
        kind, sensor = self._strain_sensor()
        if kind == 'cs1237':
            sensor._enable_cs1237(enable)

    def _query_diff(self):
        kind, sensor = self._strain_sensor()
        if kind == 'load_cell':
            return int(round(sensor.avg_counts(8)))
        cs = sensor
        if hasattr(cs, 'cs1237_diff_process'):
            return int(cs.cs1237_diff_process(timeout=QUERY_TIMEOUT)['diff'])
        cs._query_complete = self.reactor.completion()
        try:
            cs._cmd_query_diff.send([cs._oid])
            params = cs._query_complete.wait(
                self.reactor.monotonic() + QUERY_TIMEOUT)
        finally:
            cs._query_complete = None
        if params is None:
            raise self.printer.command_error("cs1237_query response timeout")
        return cs._int32_conversion(int(params['diff']))

    # Automatic z-offset
    def _measure_zoffset(self):
        # Home on the bed, then lift the nozzle in auto_step increments
        # until the strain gauge stops changing: that is where the nozzle
        # leaves the bed.  Z=0 is put return_distance below that point.
        self._enable_strain_gauge(1)
        try:
            self._set_zero()
            self._run("G28 Z")
            self._run("M400")
            # ProbeKS1.multi_probe_end() disables CS1237 when G28 Z finishes
            self._enable_strain_gauge(1)
            self._run("G4 P1000")
            base = self._query_diff()
            speed = self.probe.get_probe_params()['probe_speed']
            self._run("G90")
            self._run("G1 Z0.0 F%f" % (speed * 60.,))
            self._run("M400")
            self._run("G4 P200")
            prev = self._query_diff()
            logging.info("leviq3: auto z-offset base=%d contact=%d limit=%d",
                         base, prev, prev - int(self.max_diff))
            lifted = 0.
            first, stable = True, False
            while True:
                if lifted + self.auto_step > self.max_auto_travel:
                    raise self.printer.command_error(
                        "leviq3: nozzle still loaded after %.3fmm"
                        % (lifted,))
                self._run("SET_GCODE_OFFSET Z_ADJUST=%f MOVE=1"
                          % (self.auto_step,))
                self._run("M400")
                self._run("G4 P200")
                cur = self._query_diff()
                lifted += self.auto_step
                logging.info("leviq3: lifted=%.4f adc=%d change=%d",
                             lifted, cur, abs(prev - cur))
                if first:
                    first = False
                elif abs(prev - cur) > self.noise_diff:
                    stable = False
                elif stable:
                    break
                else:
                    stable = True
                prev = cur
            self._run("SET_GCODE_OFFSET Z_ADJUST=%f MOVE=1"
                      % (-self.return_distance,))
            self._apply_gcode_offset()
            self._run("G1 Z5.0 F4800")
        finally:
            self._enable_strain_gauge(0)

    def _restore_orig(self, orig, orig_comp):
        # _measure_zoffset() -> _set_zero() cleared temp_comp; orig already
        # includes it, so bring temp_comp back too or a later
        # LEVIQ_TEMP_OFFSET would compensate a second time (ERR-19)
        self._set_zoffset(orig)
        self.temp_comp = orig_comp

    def _measure_or_restore(self, gcmd, orig, orig_comp):
        try:
            self._measure_zoffset()
        except self.printer.command_error as e:
            gcmd.respond_info("leviq3: measurement failed (%s), "
                              "keeping z_offset %.4f" % (e, orig))
            self._run("SET_GCODE_OFFSET Z=0 MOVE=0")
            self._restore_orig(orig, orig_comp)
            return None
        return self._get_zoffset()

    def _measure_pair(self, gcmd, orig, orig_comp):
        first = self._measure_or_restore(gcmd, orig, orig_comp)
        second = self._measure_or_restore(gcmd, orig, orig_comp)
        gcmd.respond_info("leviq3: z_offset %s / %s" % (
            "ERR" if first is None else ("%.4f" % first),
            "ERR" if second is None else ("%.4f" % second)))
        if first is None or second is None:
            return False, None
        return abs(first - second) <= 2. * self.auto_step + 0.0001, second

    def _active_heater(self):
        toolhead = self.printer.lookup_object('toolhead', None)
        extruder = (toolhead.get_extruder() if toolhead is not None
                    and hasattr(toolhead, 'get_extruder') else None)
        if extruder is None:
            extruder = self.printer.lookup_object('extruder', None)
        if extruder is None or not hasattr(extruder, 'get_heater'):
            return None
        return extruder.get_heater()

    def _run_cold_retract(self, gcmd, script):
        heater = self._active_heater()
        if heater is None or getattr(heater, 'can_extrude', True):
            self._run(script)
            return
        if not self.cold_retract:
            gcmd.respond_info(
                "leviq3: skipping %s below min_extrude_temp (cold_retract=False)"
                % (script,))
            return
        lock = getattr(heater, 'lock', None) or contextlib.nullcontext()
        has_min = hasattr(heater, 'min_extrude_temp')
        # Temporarily bypass min_extrude_temp only for this stock cold retract move
        with lock:
            orig_can = heater.can_extrude
            orig_min = heater.min_extrude_temp if has_min else None
            heater.can_extrude = True
            if has_min:
                heater.min_extrude_temp = 0.
            try:
                self._run(script)
            finally:
                if has_min:
                    heater.min_extrude_temp = orig_min
                heater.can_extrude = orig_can

    cmd_LEVIQ_AUTO_ZOFFSET_help = "Calculates the z_offset automatically"
    def cmd_LEVIQ_AUTO_ZOFFSET(self, gcmd):
        is_save = gcmd.get_int('IS_SAVE', 1)
        retries = gcmd.get_int('RETRY_COUNT', self.retry_count)
        orig = self._get_zoffset()
        orig_comp = self.temp_comp
        self._run("M109 S%f" % (self.extru_end_temp,))
        self._run("M107")
        self._run("M83")
        try:
            self._run("G92 E0")
            self._run_cold_retract(gcmd, "G1 E-0.05 F100")
            self._run("M400")
        finally:
            self._run("M82")
        # FORCE=1 measures once without touching the saved on/off switch
        # (the screen's z-offset wizard); stock callers never pass it
        if (self.auto_zoffset_on_off <= 0
                and gcmd.get_int('FORCE', 0) <= 0):
            self._run("G28 Z")
            return
        calibrated = True
        consistent, z_offset = self._measure_pair(gcmd, orig, orig_comp)
        if not consistent:
            consistent, z_offset = self._measure_pair(gcmd, orig, orig_comp)
            if not consistent:
                gcmd.respond_info("leviq3: readings disagree, "
                                  "keeping z_offset %.4f" % (orig,))
                self._restore_orig(orig, orig_comp)
                calibrated = False
        if calibrated and -self.zoffset_factor < z_offset <= 0.:
            # Contact cleared almost at once: the stock firmware takes this
            # as a dirty nozzle, wipes again and measures once more.
            # RETRY_COUNT=0 means no re-wipe
            while retries > 0:
                self._run("G90")
                self._run("G1 X%f Y%f F9600"
                          % tuple(self.platform_offset_extru[:2]))
                self._run("M400")
                self._run("M109 S%f" % (self.extru_temp,))
                if self.rewipe_gcode:
                    self._run(self.rewipe_gcode)
                self._run("M109 S%f" % (self.extru_end_temp,))
                self._run("M107")
                if not self._measure_pair(gcmd, orig, orig_comp)[0]:
                    self._restore_orig(orig, orig_comp)
                    calibrated = False
                    break
                retries -= 1
        if calibrated and is_save:
            self._save_state(update_configfile=True)
        self._zeroed_for_leveling = False
        self.saved_z_offset = self._get_zoffset()
        self.saved_temp_comp = self.temp_comp
        gcmd.respond_info("leviq3: z_offset: %.4f" % (self._get_zoffset(),))

    cmd_LEVIQ_AUTO_ZOFFSET_ON_OFF_help = "Enable or disable LEVIQ_AUTO_ZOFFSET"
    def cmd_LEVIQ_AUTO_ZOFFSET_ON_OFF(self, gcmd):
        self.auto_zoffset_on_off = gcmd.get_int('ON_OFF', 0)
        self._save_state()

    cmd_LEVIQ_SET_ZERO_help = "Reset the probe z_offset and gcode Z offset"
    def cmd_LEVIQ_SET_ZERO(self, gcmd):
        self._set_zero()
        self._zeroed_for_leveling = False

    cmd_LEVIQ_TEMP_OFFSET_help = "Compensate z_offset for nozzle expansion"
    def cmd_LEVIQ_TEMP_OFFSET(self, gcmd):
        temp = gcmd.get_float('TEMP', None)
        if temp is None:
            heater = self._active_heater()
            if heater is None:
                raise self.printer.command_error(
                    "leviq3: active extruder has no heater")
            temp = heater.get_status(self.reactor.monotonic())['target']
        offset = 0.
        if temp > 0.:
            offset = ((temp - TEMP_OFFSET_BASE)
                      * (self.expansion_factor / TEMP_OFFSET_SPAN))
        # The stock firmware adds the offset on every call; only the change
        # is applied here so repeated calls do not accumulate.
        change = offset - self.temp_comp
        if change:
            toolhead = self.printer.lookup_object('toolhead', None)
            homed = (toolhead.get_status(self.reactor.monotonic()).get('homed_axes', '')
                     if toolhead is not None else '')
            move_flag = 1 if 'z' in homed else 0
            self._run("SET_GCODE_OFFSET Z_ADJUST=%f MOVE=%d"
                      % (change, move_flag))
            # fold only the compensation, not a fine adjustment of the user
            self._apply_gcode_offset(change)
            self.temp_comp = offset
        self._run("M400")
        self._save_state()
        self._zeroed_for_leveling = False
        self.saved_z_offset = self._get_zoffset()
        self.saved_temp_comp = self.temp_comp
        gcmd.respond_info("leviq3: temp offset %.4f at %.0fC, z_offset: %.4f"
                          % (offset, temp, self._get_zoffset()))

    # Leveling sequence
    cmd_LEVIQ_PREHEATING_help = "Automatic leveling function preheating"
    def cmd_LEVIQ_PREHEATING(self, gcmd):
        # Each call starts from 60/170 like the stock command does
        self.bed_temp = gcmd.get_float('B', 60.)
        self.extru_temp = gcmd.get_float('S', 170.)
        self.extru_end_temp = gcmd.get_float('E', self.extru_end_temp)
        self._snapshot_zoffset()
        self._set_zero()
        try:
            self._run("M104 S%f" % (self.extru_temp,))
            self._run("M190 S%f" % (self.bed_temp,))
            if self.bed_soak_time:
                self._run("G4 P%d" % (int(round(self.bed_soak_time * 1000.)),))
            self._run("M109 S%f" % (self.extru_temp,))
            self._run("M83")
            try:
                self._run_cold_retract(gcmd, "G1 E-10 F400")
            finally:
                self._run("M82")
            self._run("G92 E0")
            self._run("M400")
            toolhead = self.printer.lookup_object('toolhead')
            homed = toolhead.get_status(self.reactor.monotonic())['homed_axes']
            if 'x' in homed and 'y' in homed:
                self._run("G90")
                self._run("G1 X%f Y%f F9600"
                          % tuple(self.platform_offset_extru[:2]))
                self._run("M400")
        except Exception:
            if self.restore_zoffset:
                self._restore_zoffset()
            raise

    cmd_LEVIQ_WIPING_help = "Automatic leveling function wiping"
    def cmd_LEVIQ_WIPING(self, gcmd):
        try:
            if self.wipe_gcode:
                self._run(self.wipe_gcode)
            self._run("M400")
            self._run("BED_MESH_CLEAR")
            self._run("M109 S%f" % (self.extru_end_temp,))
            self._run("M190 S%f" % (self.bed_temp,))
            self._run("M107")
            self._run("G28 Z")
            self._run("M400")
            self._run("G4 P1000")
        except Exception:
            if self.restore_zoffset:
                self._restore_zoffset()
            raise

    cmd_LEVIQ_PROBE_help = "Automatic leveling function probe"
    def cmd_LEVIQ_PROBE(self, gcmd):
        meshed = False
        try:
            if self.mesh_gcode:
                self._run(self.mesh_gcode)
                meshed = True
            self._run("M400")
        finally:
            if self.restore_zoffset:
                self._restore_zoffset()
        if meshed:
            self._persist_mesh(gcmd)
        self._run("M106 S0")
        self._run("G90")
        self._run("G1 Z%f F%f" % (self.z_up, self.to_z_up_speed * 60.))
        self._run("M400")
        if meshed and self.mesh_persist and self.mesh_save_config:
            # Writes the pending [bed_mesh <profile>] (and a pending
            # z_offset) to the config file; Klipper restarts, so it is last
            self._run("SAVE_CONFIG")

    cmd_LEVIQ_help = "Preheat, wipe the nozzle and probe the bed mesh"
    def cmd_LEVIQ(self, gcmd):
        try:
            self.cmd_LEVIQ_PREHEATING(gcmd)
            self.cmd_LEVIQ_WIPING(gcmd)
            self.cmd_LEVIQ_PROBE(gcmd)
        except Exception:
            if self.restore_zoffset:
                self._restore_zoffset()
            raise


def load_config(config):
    return LeviQ3(config)