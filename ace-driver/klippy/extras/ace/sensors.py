from .config import (
    SENSOR_TOOLHEAD,
    SENSOR_RDM,
)


class FilamentTrackerAdapter:
    """Thin shim that exposes a filament_tracker's RunoutHelper.

    The ACE manager stores sensors[SENSOR_TOOLHEAD] and expects the
    RunoutHelper interface (.filament_present, .sensor_enabled).  For a
    standard filament_switch_sensor we store its .runout_helper directly.
    For a filament_tracker (which now *has* a RunoutHelper) we do the
    same, but keep the full tracker reference so callers can still reach
    encoder-specific data if needed.
    """

    def __init__(self, tracker):
        self._tracker = tracker
        # Expose the embedded RunoutHelper so the manager can treat
        # this object identically to a plain RunoutHelper.
        self.runout_helper = tracker.runout_helper

    # Delegate the two attributes the manager touches directly.
    @property
    def filament_present(self):
        return self.runout_helper.filament_present

    @property
    def sensor_enabled(self):
        return self.runout_helper.sensor_enabled

    @sensor_enabled.setter
    def sensor_enabled(self, value):
        self.runout_helper.sensor_enabled = value

    def is_instantly_clear(self):
        """Instantaneous raw channel state — no absence timeout.

        Returns True when both encoder channels are currently open,
        bypassing the normal absence_timeout delay.  Use during active
        retraction where the caller *knows* filament was recently moving.

        Returns:
            bool: True if the sensor reads clear right now.
            None:  If the underlying tracker does not support this query
                   (graceful fallback — caller should use filament_present).
        """
        if hasattr(self._tracker, 'are_both_channels_open'):
            return self._tracker.are_both_channels_open
        return None


class SensorCoordinator:
    """Coordinates shared toolhead and RDM filament sensors for AceManager."""

    def __init__(self, manager):
        self.manager = manager

    def _setup_sensors(self):
        """
        Register shared sensor access (done ONCE).

        All instances share the same sensors (toolhead + optional RDM).
        Manager owns the sensors, not instances.

        Toolhead sensor: looked up by the configured name in two forms:
            1. filament_switch_sensor <name>  (standard Klipper sensor)
            2. filament_tracker <name>        (encoder-based tracker)
        No implicit fallbacks — the name must match a section in printer.cfg.
        """
        mgr = self.manager
        instance = mgr.instances[0]

        # --- Toolhead sensor ---
        toolhead_sensor_name = instance.filament_runout_sensor_name_nozzle
        toolhead_resolved = False

        # Try standard filament_switch_sensor <name>
        try:
            toolhead_sensor = mgr.printer.lookup_object(
                f"filament_switch_sensor {toolhead_sensor_name}")
            mgr.sensors[SENSOR_TOOLHEAD] = toolhead_sensor.runout_helper
            mgr._prev_sensors_enabled_state[SENSOR_TOOLHEAD] = (
                toolhead_sensor.runout_helper.sensor_enabled)
            toolhead_resolved = True
            mgr.gcode.respond_info(
                f"ACE: Toolhead sensor '{toolhead_sensor_name}' "
                f"(filament_switch_sensor)")
        except Exception:
            pass

        # Try filament_tracker <name>
        if not toolhead_resolved:
            try:
                tracker = mgr.printer.lookup_object(
                    f"filament_tracker {toolhead_sensor_name}")
                adapter = FilamentTrackerAdapter(tracker)
                mgr.sensors[SENSOR_TOOLHEAD] = adapter
                mgr._prev_sensors_enabled_state[SENSOR_TOOLHEAD] = (
                    adapter.sensor_enabled)
                toolhead_resolved = True
                mgr.gcode.respond_info(
                    f"ACE: Toolhead sensor '{toolhead_sensor_name}' "
                    f"(filament_tracker)")
            except Exception:
                pass

        if not toolhead_resolved:
            mgr.gcode.respond_info(
                f"ACE: ERROR - No toolhead sensor '{toolhead_sensor_name}' "
                f"found in printer.cfg (tried [filament_switch_sensor "
                f"{toolhead_sensor_name}] and [filament_tracker "
                f"{toolhead_sensor_name}])")
            raise mgr.config.error(
                f"Missing sensor '{toolhead_sensor_name}' in printer.cfg. "
                f"Add [filament_switch_sensor {toolhead_sensor_name}] or "
                f"[filament_tracker {toolhead_sensor_name}].")

        # --- RDM sensor (optional) ---
        if instance.filament_runout_sensor_name_rdm is not None:
            rms_sensor_name = instance.filament_runout_sensor_name_rdm
            rdm_resolved = False

            # Try standard filament_switch_sensor <name>
            try:
                rms_sensor = mgr.printer.lookup_object(
                    f"filament_switch_sensor {rms_sensor_name}")
                mgr.sensors[SENSOR_RDM] = rms_sensor.runout_helper
                mgr._prev_sensors_enabled_state[SENSOR_RDM] = (
                    rms_sensor.runout_helper.sensor_enabled)
                rdm_resolved = True
                mgr.gcode.respond_info(
                    f"ACE: RDM sensor '{rms_sensor_name}' "
                    f"(filament_switch_sensor)")
            except Exception:
                pass

            # Try filament_tracker <name>
            if not rdm_resolved:
                try:
                    rdm_tracker = mgr.printer.lookup_object(
                        f"filament_tracker {rms_sensor_name}")
                    adapter = FilamentTrackerAdapter(rdm_tracker)
                    mgr.sensors[SENSOR_RDM] = adapter
                    mgr._prev_sensors_enabled_state[SENSOR_RDM] = (
                        adapter.sensor_enabled)
                    rdm_resolved = True
                    mgr.gcode.respond_info(
                        f"ACE: RDM sensor '{rms_sensor_name}' "
                        f"(filament_tracker)")
                except Exception:
                    pass

            if not rdm_resolved:
                mgr.gcode.respond_info(
                    f"ACE: WARNING - No RDM sensor '{rms_sensor_name}' "
                    f"found (tried [filament_switch_sensor "
                    f"{rms_sensor_name}] and [filament_tracker "
                    f"{rms_sensor_name}]). "
                    f"No RDM consistency check will be performed.")

        # Disable standard runout detection
        mgr._disable_all_sensor_detection()

    def _disable_all_sensor_detection(self):
        """Disable automatic pause for all sensors."""
        mgr = self.manager
        for name, sensor in mgr.sensors.items():
            if sensor.sensor_enabled:
                mgr.gcode.respond_info(f"ACE: Disabling runout detection for {name}")
                sensor.sensor_enabled = False

    def _restore_sensors(self):
        """Restore original sensor state."""
        mgr = self.manager
        for name, sensor in mgr.sensors.items():
            if name in mgr._prev_sensors_enabled_state:
                prev_state = mgr._prev_sensors_enabled_state[name]
                sensor.sensor_enabled = prev_state
                mgr.gcode.respond_info(f"ACE: Restored sensor {name} to enabled={prev_state}")

    # ========== Sensor Query Methods ==========

    def get_switch_state(self, sensor_name):
        """
        Get sensor state directly from Klipper.

        Supports sensor state injection for testing (via _sensor_override).

        Args:
            sensor_name: SENSOR_TOOLHEAD or SENSOR_RDM

        Returns:
            bool: True if filament is present (sensor triggered)
        """
        mgr = self.manager
        # Check for injected override (for testing)
        if hasattr(mgr, '_sensor_override') and mgr._sensor_override:
            if sensor_name in mgr._sensor_override:
                return mgr._sensor_override[sensor_name]

        if sensor_name not in mgr.sensors:
            return False

        sensor = mgr.sensors[sensor_name]
        return bool(sensor.filament_present)

    def get_instant_switch_state(self, sensor_name):
        """Get sensor state using instantaneous raw channel reading.

        Like :meth:`get_switch_state`, but bypasses the absence_timeout
        delay on filament_tracker sensors by reading the raw channel state.
        Falls back to the normal debounced ``filament_present`` when the
        sensor does not support instantaneous queries (e.g. a plain
        filament_switch_sensor).

        Use this during active retraction / unloading where the caller
        knows filament was recently moving and needs the fastest possible
        response.

        Args:
            sensor_name: SENSOR_TOOLHEAD or SENSOR_RDM

        Returns:
            bool: True if filament is present (sensor triggered)
        """
        mgr = self.manager
        # Check for injected override (for testing)
        if hasattr(mgr, '_sensor_override') and mgr._sensor_override:
            if sensor_name in mgr._sensor_override:
                return mgr._sensor_override[sensor_name]

        if sensor_name not in mgr.sensors:
            return False

        sensor = mgr.sensors[sensor_name]
        if hasattr(sensor, 'is_instantly_clear'):
            instant = sensor.is_instantly_clear()
            if instant is not None:
                return not instant  # clear=True means filament absent
        return bool(sensor.filament_present)

    def is_filament_path_free_instant(self):
        """Check if filament path is clear using instantaneous sensor reads.

        Same as :meth:`is_filament_path_free` but uses
        :meth:`get_instant_switch_state` for faster response during
        active retraction.

        Returns:
            bool: True if path is clear (no filament detected)
        """
        mgr = self.manager
        toolhead_blocked = mgr.get_instant_switch_state(SENSOR_TOOLHEAD)

        if mgr.has_rdm_sensor():
            rdm_blocked = mgr.get_instant_switch_state(SENSOR_RDM)
            return not (toolhead_blocked or rdm_blocked)
        else:
            return not toolhead_blocked

    def is_filament_path_free(self):
        """
        Check if filament path is clear.

        If RDM sensor available: checks both toolhead + RDM
        If RDM unavailable: checks only toolhead

        Returns:
            bool: True if path is clear (no filament detected)
        """
        mgr = self.manager
        toolhead_blocked = mgr.get_switch_state(SENSOR_TOOLHEAD)

        if mgr.has_rdm_sensor():
            rdm_blocked = mgr.get_switch_state(SENSOR_RDM)
            return not (toolhead_blocked or rdm_blocked)
        else:
            # RDM not available - check only toolhead
            return not toolhead_blocked

    def has_rdm_sensor(self):
        """Check if RDM sensor is configured and available."""
        mgr = self.manager
        return SENSOR_RDM in mgr.sensors and mgr.sensors[SENSOR_RDM] is not None

    def get_rdm_encoder_pulse(self):
        """Return the RDM filament_tracker encoder_pulse count, or None.

        Only works when the RDM sensor is a filament_tracker wrapped
        in a FilamentTrackerAdapter.  Returns None if the sensor is a
        plain filament_switch_sensor or not configured.
        """
        mgr = self.manager
        if not mgr.has_rdm_sensor():
            return None
        sensor = mgr.sensors[SENSOR_RDM]
        if isinstance(sensor, FilamentTrackerAdapter):
            return sensor._tracker.tracker_status.encoder_pulse
        return None
