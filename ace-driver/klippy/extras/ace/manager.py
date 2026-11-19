from .config import (
    ACE_INSTANCES,
    INSTANCE_MANAGERS,
    SLOTS_PER_ACE,
    SENSOR_TOOLHEAD,
    SENSOR_RDM,
    FILAMENT_STATE_SPLITTER,
    FILAMENT_STATE_BOWDEN,
    FILAMENT_STATE_NOZZLE,
    FILAMENT_STATE_TOOLHEAD,
    OVERRIDABLE_PARAMS,
    CHOICE_OVERRIDABLE_PARAMS,
    get_instance_from_tool,
    get_local_slot,
    get_tool_offset,
    get_ace_instance_and_slot_for_tool,
    parse_instance_config,
    parse_instance_baud_config,
    parse_instance_choice_config,
    create_inventory,
)
from .persistent_state import PersistentState

from .instance import AceInstance
from .endless_spool import EndlessSpool
from .runout_monitor import RunoutMonitor
from .moonraker_lane_sync import MoonrakerLaneSyncAdapter
from . import commands
from .config import read_ace_config
from .protocol import create_protocol_adapter, resolve_protocol_name
import logging
import serial
from .sensors import FilamentTrackerAdapter, SensorCoordinator
from .shared_bus_coordinator import SharedBusCoordinator
from .health_monitor import ConnectionHealthMonitor
from .toolchange import toolchange_in_progress_guard, ToolchangeOrchestrator
from .toolchange_unload import ToolchangeUnloadHelper
from .toolchange_spool import ToolchangeSpoolHelper


class AceManager:
    """
    Main orchestrator for multiple ACE Pro units.

    Responsibilities:
    - Create and manage multiple AceInstance objects (1 per ACE unit)
    - Tool mapping: T0-T3 → instance 0, T4-T7 → instance 1, etc.
    - Global filament runout monitoring and endless spool coordination
    - Sensor management and state tracking
    - Tool change coordination including unload/load/cut/store sequences
    - Inventory management with persistent storage
    - Register T<n> tool macro commands
    - Register ACE_* gcode commands
    - Manage lifecycle: startup, printing, shutdown

    DESIGN: Single AceManager creates N AceInstance objects (one per physical ACE unit).
    """
    def __init__(self, config, dummy_ace_count=1):
        """
        Initialize THE AceManager.

        Called ONCE from load_config() with total ACE count.
        Creates all AceInstance objects internally.

        Args:
            config: Klipper config object
        """
        self.config = config
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.ace_config = read_ace_config(config)

        self.toolhead_retraction_speed = float(self.ace_config["toolhead_retraction_speed"])
        self.toolhead_retraction_length = float(self.ace_config["toolhead_retraction_length"])
        self.default_color_change_purge_length = float(self.ace_config["default_color_change_purge_length"])
        self.default_color_change_purge_speed = float(self.ace_config["default_color_change_purge_speed"])
        self.toolchange_purge_length = self.default_color_change_purge_length
        self.toolchange_purge_speed = self.default_color_change_purge_speed
        self.purge_max_chunk_length = float(self.ace_config["purge_max_chunk_length"])
        self.pre_cut_retract_length = float(self.ace_config["pre_cut_retract_length"])
        self.ace_count = self.ace_config["ace_count"]
        self.purge_multiplier = float(self.ace_config.get("purge_multiplier", 1.0))
        self.toolhead_extrusion_allowed = bool(
            self.ace_config.get("toolhead_extrusion_allowed", False)
        )
        self._extrusion_suppressed_logged = False
        self._feed_speed_override = None
        self._retract_speed_override = None
        self._pre_toolchange_done = False
        self._pre_toolchange_fan_pwm = None
        self._shared_bus_init_pending = set()

        if self.ace_count < 1:
            raise config.error(f"ace_count must be >= 1, got {self.ace_count}")

        self.gcode.respond_info(f"ACE: Creating {self.ace_count} instance(s) with single AceManager")

        persistence_mode = self.ace_config.get("persistence_mode", "deferred")
        self.state = PersistentState(self.printer, self.gcode, persistence_mode=persistence_mode)
        self.gcode.respond_info(f"ACE: Persistence mode: {persistence_mode}")
        self.variables = self.state.get_all()
        initial_ace_enabled = bool(self.state.get("ace_global_enabled", True))

        self.gcode.respond_info(
            f"ACE: Initializing with ace_global_enabled={initial_ace_enabled} "
            f"(from saved variables)"
        )

        self._ace_pro_enabled = initial_ace_enabled
        self._shutdown_in_progress = False  # set by klippy:shutdown, never cleared
        self._shared_transport_contexts = {}
        self._shared_bus_coordinator = SharedBusCoordinator(self)
        self._toolchange_orchestrator = ToolchangeOrchestrator(self)
        self._toolchange_unload = ToolchangeUnloadHelper(self)
        self._toolchange_spool = ToolchangeSpoolHelper(self)

        # Create all AceInstance objects
        self.instances = []
        for instance_num in range(self.ace_count):
            instance_config = self._resolve_instance_config(instance_num)
            protocol = self._create_instance_protocol(instance_config)
            shared_kwargs = self._build_shared_transport_kwargs(
                instance_num,
                instance_config,
                initial_ace_enabled,
                protocol,
            )

            instance = AceInstance(
                instance_num,
                instance_config,
                self.printer,
                ace_enabled=initial_ace_enabled,  # Pass initial state
                protocol=protocol,
                active_protocol_name=instance_config["active_protocol_name"],
                **shared_kwargs,
            )
            instance.toolhead_extrusion_allowed = self.toolhead_extrusion_allowed
            instance._extruder_move = self._extruder_move
            orig_get_status = instance.get_status
            def make_instance_get_status(inst, orig):
                def _status_wrapper(eventtime=None):
                    st = orig(eventtime)
                    st["toolhead_extrusion_allowed"] = self.toolhead_extrusion_allowed
                    st["feed_speed"] = (
                        self._feed_speed_override
                        if getattr(self, "_feed_speed_override", None) is not None
                        else inst.feed_speed
                    )
                    st["retract_speed"] = (
                        self._retract_speed_override
                        if getattr(self, "_retract_speed_override", None) is not None
                        else inst.retract_speed
                    )
                    return st
                return _status_wrapper
            instance.get_status = make_instance_get_status(instance, orig_get_status)

            self.instances.append(instance)

            # Register in global registry
            ACE_INSTANCES[instance_num] = instance
            INSTANCE_MANAGERS[instance_num] = self

            # Register tool macros for this instance
            self.register_tool_macros(instance_num)

            self.gcode.respond_info(
                f"ACE[{instance_num}]: Loaded instance (T{instance.tool_offset}-T{instance.tool_offset + 3})"
            )

        # Load persisted inventory for all instances
        self._load_all_inventories()

        # Optional adapter for Orca/Moonraker filament sync.
        self._moonraker_lane_sync = MoonrakerLaneSyncAdapter(
            self.gcode, self, self.ace_config
        )

        self._ace_state_timer = None

        # Initialize global filament position
        # Tracks physical filament location: 'bowden', 'splitter',
        # 'toolhead', or 'nozzle'
        if self.state.get("ace_filament_pos") is None:
            self.state.set("ace_filament_pos", FILAMENT_STATE_BOWDEN)

        self.ace_pin = self.printer.lookup_object("output_pin ACE_Pro")

        self.sensors = {}
        self._prev_sensors_enabled_state = {}
        self._sensor_coordinator = SensorCoordinator(self)

        # Create endless spool handler (passing self for sensor access)
        self.endless_spool = EndlessSpool(self.printer, self.gcode, self)

        # Create runout monitor (passing self for sensor queries)
        self.runout_monitor = RunoutMonitor(
            self.printer,
            self.gcode,
            self.reactor,
            self.endless_spool,
            self,  # Pass manager for sensor access and state
            runout_debounce_count=self.ace_config.get("runout_debounce_count", 1),
            tangle_detection=self.ace_config.get("tangle_detection", False),
            tangle_detection_length=self.ace_config.get("tangle_detection_length", 15.0)
        )

        self.toolchange_in_progress = False

        # Expose manager state for Moonraker/KlipperScreen JSON-RPC queries
        # (distinct from per-instance printer objects).
        try:
            self.printer.add_object("ace_state", self)
        except Exception:
            # Non-fatal; fall back to per-instance status only.
            pass

        # Connection health monitoring state
        self._connection_supervision_enabled = self.ace_config.get(
            "ace_connection_supervision", True
        )
        self._connection_issue_shown = False  # Track if dialog is currently shown
        self._last_connection_status = {}     # Track per-instance connection state
        self._shared_bus_last_connected_time = {}
        self._shared_bus_retry_timers = {}
        self._shared_bus_retry_delays = {}
        self._shared_bus_retry_min_delay = 2.0
        self._shared_bus_retry_max_delay = 15.0
        self._health_monitor = ConnectionHealthMonitor(self)

        # Register event handlers
        handler = self.printer.register_event_handler
        handler("klippy:ready", self._handle_ready)
        handler("klippy:disconnect", self._handle_disconnect)
        handler("klippy:shutdown", self._handle_shutdown)

    def _get_config_for_tool(self, tool_index, param_name):
        """
        Get config value for a specific tool (resolves to correct instance).

        Args:
            tool_index: Global tool index (e.g., 0-11)
            param_name: Config parameter name

        Returns:
            Config value for the instance managing this tool

        Raises:
            Exception if tool_index is invalid or param not found
        """
        instance_num = get_instance_from_tool(tool_index)
        if instance_num < 0 or instance_num >= len(self.instances):
            raise Exception(f"Invalid tool index {tool_index}")

        instance = self.instances[instance_num]

        # Access instance's resolved config
        if not hasattr(instance, param_name):
            raise Exception(f"Config parameter '{param_name}' not found for instance {instance_num}")

        return getattr(instance, param_name)

    def get_printer(self):
        """Get the printer object (Klipper API)."""
        return self.printer

    # ========== Lifecycle ==========

    def _handle_ready(self):
        """
        Called when Klipper is ready.
        Sets up toolhead reference, connects ACE instances, initializes sensors, starts monitoring.
        """

        self._ready_time = self.reactor.monotonic()

        # Set toolhead on all instances
        toolhead = self.printer.lookup_object("toolhead")
        for instance in self.instances:
            instance.toolhead = toolhead

        self.gcode.respond_info(
            f"ACE: Syncing virtual pin to saved state: {self._ace_pro_enabled}"
        )
        pin_value = 1.0 if self._ace_pro_enabled else 0.0
        self.gcode.run_script_from_command(f"SET_PIN PIN=ACE_Pro VALUE={pin_value}")

        if self._ace_pro_enabled:
            for instance in self._iter_unique_transport_instances():
                instance.serial_mgr.connect_to_ace(instance.baud, 2)
                if instance.bus_session is not None and instance.serial_mgr._port:
                    instance.bus_session.port = instance.serial_mgr._port
            self._setup_sensors()
        else:
            self.gcode.respond_info("ACE: ACE Pro disabled on startup - skipping connections")

        # Validate persisted tool state against live sensor readings.
        # Catches stale state from manual filament removal while powered off.
        # self._validate_startup_tool_state()  # disabled pending timing-free rewrite

        # Publish initial lane_data snapshot for Orca pull-mode sync.
        self._sync_moonraker_lane_data(force=True, reason="klippy_ready")

        self._start_monitoring()

    def _handle_shutdown(self):
        """Called on Klipper emergency stop or fatal shutdown.

        Uses ``flush_direct()`` to write directly to ``saved_variables.cfg``
        without going through the GCode queue, which is unavailable (or
        rejected with "Printer is shutdown") at this point.
        """
        # From here on nothing may treat the shutdown as a user decision:
        # update_ace_support_active_state() must not write ace_global_enabled
        # and no reconnect may be scheduled (docs/30 6.2, B-06).
        self._shutdown_in_progress = True

        pending = list(self.state._dirty)
        logging.info(
            "ACE: klippy:shutdown received — flushing %d dirty variable(s) directly: %s",
            len(pending), pending
        )
        try:
            self.state.flush_direct()
        except Exception:
            logging.exception("ACE: Failed to flush state on shutdown")

        # Stop periodic work (runout monitor + 2 s state monitor) ...
        try:
            self._stop_monitoring()
        except Exception:
            logging.exception("ACE: Failed to stop monitoring on shutdown")

        # ... and quiet the serial links: no heartbeat, no queued requests, no
        # reconnect; the port is closed with HUPCL cleared so DTR does not drop.
        # Nothing is sent to the ACE (the bus may be in a bad state).
        for instance in self._iter_unique_transport_instances():
            try:
                instance.serial_mgr.shutdown()
            except Exception:
                logging.exception("ACE[%s]: serial shutdown failed", instance.instance_num)
        for instance in self.instances:
            timer = getattr(instance, "_shared_bus_heartbeat_timer", None)
            if timer is not None:
                try:
                    self.reactor.unregister_timer(timer)
                except Exception:
                    pass
                instance._shared_bus_heartbeat_timer = None

    def _handle_disconnect(self):
        """Called on Klipper disconnect. Stops monitoring and disconnects all ACE instances."""
        self.gcode.respond_info("ACE: Disconnecting")

        # Flush any dirty persistent state to disk before we tear down.
        # After a shutdown flush_direct() already wrote everything and the gcode
        # queue rejects SAVE_VARIABLE, so skip it.
        if not self._shutdown_in_progress:
            try:
                self.state.flush()
            except Exception:
                logging.exception("ACE: Failed to flush state on disconnect")

        for instance in self._iter_unique_transport_instances():
            instance.serial_mgr.disconnect()

        self._stop_monitoring()
        self._restore_sensors()
        adapter = getattr(self, "_moonraker_lane_sync", None)
        if adapter:
            try:
                adapter.shutdown()
            except Exception as e:
                logging.warning("ACE: Moonraker lane sync shutdown failed: %s", e)

    def _validate_startup_tool_state(self):
        """Validate persisted tool state against live sensor readings at startup.

        If ``ace_current_index`` indicates a tool is loaded and
        ``ace_filament_pos`` is not ``"bowden"``, but **all** filament
        sensors report clear and the printer is idle, the persisted state
        is stale (e.g. user manually removed filament while powered off).

        In that case, reset both variables so that the next toolchange
        does not attempt a phantom retraction on a tool that is no longer
        physically loaded.

        .. note:: Klipper's ``RunoutHelper`` initialises ``filament_present``
            to ``False`` and only updates it once the MCU button-state
            callbacks have completed — two asynchronous reactor hops after
            the MCU reports the initial pin state.  Reading the sensors
            *without* first yielding to the reactor can therefore produce a
            false "all-clear" result even when filament is physically present.
            ``reactor.pause()`` is called below specifically to drain those
            pending callbacks before the sensor values are read.
        """
        current_index = self.state.get("ace_current_index", -1)
        filament_pos = self.state.get("ace_filament_pos", FILAMENT_STATE_BOWDEN)

        # Nothing to validate when no tool is recorded as loaded.
        if current_index < 0:
            return

        # Position already "bowden" means state considers filament retracted.
        if filament_pos == FILAMENT_STATE_BOWDEN:
            return

        # Do not touch state during an active or paused print — the
        # persisted values may be intentionally set by the print flow.
        try:
            print_stats = self.printer.lookup_object("print_stats", None)
            if print_stats:
                stats = print_stats.get_status(self.reactor.monotonic())
                state = (stats.get("state") or "").lower()
                if state in ("printing", "paused"):
                    self.gcode.respond_info(
                        f"ACE: Startup validation skipped — printer is {state}"
                    )
                    return
        except Exception:
            pass  # If print_stats unavailable, assume idle.

        # Sensors may not be set up (ACE Pro disabled).  When no sensors
        # are registered we cannot validate, so bail out.
        if SENSOR_TOOLHEAD not in self.sensors:
            return

        # Klipper's RunoutHelper initialises filament_present=False and updates
        # it through two layers of reactor async callbacks:
        #   MCU → register_async_callback → DebounceButton.button_handler
        #       → register_callback(_debounce_event) → note_filament_present
        # Both hops must complete before we read the sensors.  At klippy:ready
        # time those callbacks may still be queued.  reactor.pause() yields
        # back to the reactor loop for 0.5 s, which is more than enough for
        # any pending MCU button-state callbacks to drain and update
        # filament_present before we read it.
        self.reactor.pause(self.reactor.monotonic() + 0.5)

        toolhead_has_filament = self.get_switch_state(SENSOR_TOOLHEAD)
        rdm_has_filament = (
            self.get_switch_state(SENSOR_RDM)
            if self.has_rdm_sensor()
            else False
        )

        if toolhead_has_filament or rdm_has_filament:
            # At least one sensor confirms filament — state looks plausible.
            self.gcode.respond_info(
                f"ACE: Startup validation — T{current_index} state "
                f"(filament_pos='{filament_pos}') confirmed by sensors "
                f"(toolhead={'present' if toolhead_has_filament else 'clear'}, "
                f"rdm={'present' if rdm_has_filament else 'clear'})"
            )
            return

        # All sensors are clear while state claims a tool is loaded.
        self.gcode.respond_info(
            f"ACE: \u26a0 STARTUP VALIDATION — Saved state indicated "
            f"T{current_index} was loaded (filament_pos='{filament_pos}'), "
            f"but ALL filament sensors report CLEAR and printer is idle. "
            f"Resetting ace_current_index to -1 and ace_filament_pos to "
            f"'bowden'. (Likely cause: filament was manually removed "
            f"while printer was off)"
        )
        self.state.set_and_save("ace_current_index", -1)
        self.state.set_and_save("ace_filament_pos", FILAMENT_STATE_BOWDEN)

    def _setup_sensors(self):
        """Register shared sensor access (delegates to SensorCoordinator)."""
        return self._sensor_coordinator._setup_sensors()

    def _disable_all_sensor_detection(self):
        """Disable automatic pause for all sensors."""
        return self._sensor_coordinator._disable_all_sensor_detection()

    def _restore_sensors(self):
        """Restore original sensor state."""
        return self._sensor_coordinator._restore_sensors()

    # ========== Sensor Query Methods ==========

    def get_switch_state(self, sensor_name):
        """Get sensor state directly from Klipper."""
        return self._sensor_coordinator.get_switch_state(sensor_name)

    def get_instant_switch_state(self, sensor_name):
        """Get sensor state using instantaneous raw channel reading."""
        return self._sensor_coordinator.get_instant_switch_state(sensor_name)

    def is_filament_path_free_instant(self):
        """Check if filament path is clear using instantaneous sensor reads."""
        return self._sensor_coordinator.is_filament_path_free_instant()

    def is_filament_path_free(self):
        """Check if filament path is clear."""
        return self._sensor_coordinator.is_filament_path_free()

    def _turn_off_heater_if_idle(self):
        """Turn off extruder heater if the printer is not currently printing."""
        return self._toolchange_orchestrator._turn_off_heater_if_idle()

    def prepare_toolhead_for_filament_retraction(self, tool_index=-1):
        """Prepare toolhead (extruder/nozzle) for filament retraction."""
        return self._toolchange_orchestrator.prepare_toolhead_for_filament_retraction(
            tool_index=tool_index
        )

    def _ensure_hot_for_recovery_unload(self, current_tool, target_temp):
        """Heat the extruder before a plausibility-mismatch recovery unload."""
        return self._toolchange_orchestrator._ensure_hot_for_recovery_unload(
            current_tool, target_temp
        )

    def execute_coordinated_retraction(self, retract_length, retract_speed, retract_speed_mmmin, current_tool):
        """Perform coordinated retraction of ACE and extruder."""
        return self._toolchange_unload.execute_coordinated_retraction(
            retract_length, retract_speed, retract_speed_mmmin, current_tool
        )

    def _wait_toolhead_move_finished(self):
        return self._toolchange_orchestrator._wait_toolhead_move_finished()

    def _extruder_move(self, length, speed, wait_for_move_end=False):
        """Move extruder (relative) via motion planner, synchronously."""
        return self._toolchange_orchestrator._extruder_move(
            length, speed, wait_for_move_end=wait_for_move_end
        )

    def smart_unload(self, tool_index=-1, prepare_toolhead=True, keep_heater=False):
        """Unload with slot cycling when tool is unknown."""
        return self._toolchange_unload.smart_unload(
            tool_index=tool_index,
            prepare_toolhead=prepare_toolhead,
            keep_heater=keep_heater,
        )

    def _identify_and_unload_by_cycling(
        self,
        current_tool_index,
        attempted_tool_index,
        retract_length,
        retract_speed,
        retract_speed_mmmin,
        full_unload_length,
    ):
        """Identify loaded tool with three-case sensor strategy."""
        return self._toolchange_unload._identify_and_unload_by_cycling(
            current_tool_index,
            attempted_tool_index,
            retract_length,
            retract_speed,
            retract_speed_mmmin,
            full_unload_length,
        )

    def _cycle_slots_with_sensor_check(
        self,
        current_tool_index,
        attempted_tool_index,
        retract_length,
        retract_speed,
        retract_speed_mmmin,
        full_unload_length,
        sensor_name,
        use_extruder,
        sensor_to_parking_length=None,
    ):
        """Unified slot cycling with sensor monitoring."""
        return self._toolchange_unload._cycle_slots_with_sensor_check(
            current_tool_index,
            attempted_tool_index,
            retract_length,
            retract_speed,
            retract_speed_mmmin,
            full_unload_length,
            sensor_name,
            use_extruder,
            sensor_to_parking_length=sensor_to_parking_length,
        )

    def smart_load(self):
        """Load all non-empty slots to verification sensor."""
        return self._toolchange_spool.smart_load()

    # ========== Inventory Management (Manager owns persistence) ==========

    def _load_all_inventories(self):
        """
        Load persisted inventory for all instances.

        Called on startup. Manager owns the persistent variables,
        not instances. Instances get their inventory set here.
        """
        for instance in self.instances:
            varname = f"ace_inventory_{instance.instance_num}"
            saved_inv = self.state.get(varname, None)
            if saved_inv:
                # Clean up legacy rgba field from saved inventory
                for slot in saved_inv:
                    slot.pop("rgba", None)
                instance.inventory = saved_inv
                self.gcode.respond_info(f"ACE[{instance.instance_num}]: Loaded persisted inventory")
            else:
                instance.inventory = create_inventory(SLOTS_PER_ACE)
                self.gcode.respond_info(f"ACE[{instance.instance_num}]: " f"Initialized new inventory")

    def _sync_inventory_to_persistent(self, instance_num=None, flush=True):
        """
        Sync instance inventory to persistent storage.

        Manager owns the persistent variables. Instances modify
        their inventory in-memory, then manager persists changes.

        Args:
            instance_num: Specific instance to sync, or None to sync all
            flush: If True (default), call set_and_save() for immediate
                   disk write.  Pass False for mid-print / batch paths
                   where the caller will flush() later.
        """

        if instance_num is not None:
            if instance_num >= len(self.instances):
                self.gcode.respond_info(f"ACE: Invalid instance number {instance_num}")
                return

            instance = self.instances[instance_num]
            varname = f"ace_inventory_{instance_num}"
            if flush:
                self.state.set_and_save(varname, instance.inventory)
            else:
                self.state.set(varname, instance.inventory)
            self._sync_moonraker_lane_data(
                force=False, reason=f"inventory_update_instance_{instance_num}"
            )

            # self.gcode.respond_info(f"ACE[{instance_num}]: Inventory synced to persistent")
        else:
            # Sync all instances
            for inst in self.instances:
                self._sync_inventory_to_persistent(inst.instance_num, flush=flush)

    def _sync_moonraker_lane_data(self, force=False, reason="manual"):
        """Push ACE slot metadata to Moonraker DB lane_data for Orca sync."""
        adapter = getattr(self, "_moonraker_lane_sync", None)
        if not adapter:
            return False
        try:
            return adapter.sync_now(force=force, reason=reason)
        except Exception as e:
            logging.warning("ACE: Moonraker lane sync failed (%s): %s", reason, e)
            return False

    def _start_monitoring(self):
        """Start runout detection monitor loop."""
        self.runout_monitor.start_monitoring()

        self.gcode.respond_info("ACE: Starting ACE support state monitor")
        self._ace_state_timer = self.reactor.register_timer(self._monitor_ace_state, self.reactor.NOW)

    def _stop_monitoring(self):
        """Stop runout monitoring."""
        self.runout_monitor.stop_monitoring()

        # Stop ACE support state monitoring timer
        if hasattr(self, "_ace_state_timer") and self._ace_state_timer:
            try:
                self.reactor.unregister_timer(self._ace_state_timer)
            except Exception:
                pass
            self._ace_state_timer = None

    def set_runout_detection_active(self, active):
        """Enable/disable runout detection (delegates to monitor)."""
        return self.runout_monitor.set_detection_active(active)

    def set_ace_global_enabled(self, enabled):
        """Set global ACE Pro enabled state and persist it."""
        self.state.set_and_save("ace_global_enabled", enabled)
        self._ace_pro_enabled = enabled

    def get_ace_global_enabled(self):
        """Get global ACE Pro enabled state from persistent storage."""
        return bool(self.state.get("ace_global_enabled", True))

    # An ACE that dropped out this long ago while no print is running counts
    # as unplugged; a drop during a print keeps the pause/recovery path.
    ACE_ABSENT_AFTER_S = 120.0
    # After Klipper start the ACE needs a few seconds to enumerate and connect.
    ACE_STARTUP_GRACE_S = 20.0

    def is_ace_connected(self):
        """True if any ACE instance has a live serial connection."""
        for instance in self.instances:
            serial_mgr = getattr(instance, "serial_mgr", None)
            try:
                if serial_mgr is not None and serial_mgr.is_connected():
                    return True
            except Exception:
                continue
        return False

    def wait_for_ace_detection(self):
        """Block (reactor-friendly) until an ACE connects or the startup grace ends."""
        ready = getattr(self, "_ready_time", None)
        if ready is None or not self._ace_pro_enabled:
            return
        deadline = ready + self.ACE_STARTUP_GRACE_S
        while not self.is_ace_connected():
            now = self.reactor.monotonic()
            if now >= deadline:
                return
            self.reactor.pause(min(deadline, now + 0.5))

    def is_ace_absent(self):
        """True when ACE support is on but no ACE is plugged in (external spool mode).

        Absent means: not connected now and either never connected since
        Klipper started (after a short startup grace), or last connected more
        than ACE_ABSENT_AFTER_S ago while idle. During a print the mode decided
        while idle is kept, so an ACE dropping out mid-print still takes the
        pause/recovery path and an unplugged one stays in external spool mode.
        T<n>, print-end unload and ACE runout handling are skipped in this mode
        so a printer without an ACE works like stock.
        """
        if not self._ace_pro_enabled or self.is_ace_connected():
            return False
        last_seen = 0.0
        for instance in self.instances:
            serial_mgr = getattr(instance, "serial_mgr", None)
            last_seen = max(last_seen, float(getattr(serial_mgr, "_last_connected_time", 0.0) or 0.0))
        now = self.reactor.monotonic()
        if last_seen <= 0.0:
            ready = getattr(self, "_ready_time", None)
            return ready is None or (now - ready) >= self.ACE_STARTUP_GRACE_S
        if self._print_state() in ("printing", "paused"):
            return bool(getattr(self, "_external_spool_mode", False))
        return (now - last_seen) > self.ACE_ABSENT_AFTER_S

    def _print_state(self):
        """Lower-case print_stats state ("" if unavailable)."""
        try:
            print_stats = self.printer.lookup_object("print_stats", None)
            if not print_stats:
                return ""
            stats = print_stats.get_status(self.reactor.monotonic())
            return (stats.get("state") or "").lower()
        except Exception:
            return ""

    def _printer_is_shutdown(self):
        """True if Klipper is in the shutdown state."""
        checker = getattr(self.printer, "is_shutdown", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return False
        return False

    # ========== Tangle protection (runtime switch) ==========

    TANGLE_ENABLED_VAR = "ace_tangle_protection_enabled"
    TANGLE_THRESHOLD_VAR = "ace_tangle_protection_threshold"
    TANGLE_THRESHOLD_MIN = 1.0
    TANGLE_THRESHOLD_MAX = 120.0

    def get_tangle_protection(self):
        """Effective tangle-protection policy.

        A value stored by ACE_TANGLE_PROTECTION (saved_variables) wins; with no
        stored value the ``[ace]`` config defaults (``tangle_protection``,
        ``tangle_protection_threshold``; default OFF / 8.0 s) apply.
        """
        enabled = bool(self.ace_config.get("tangle_protection", False))
        threshold = float(self.ace_config.get("tangle_protection_threshold", 8.0))
        source = "config"
        try:
            saved_enabled = self.state.get(self.TANGLE_ENABLED_VAR, None)
            saved_threshold = self.state.get(self.TANGLE_THRESHOLD_VAR, None)
        except Exception:
            saved_enabled = saved_threshold = None
        if saved_enabled is not None:
            enabled = bool(saved_enabled)
            source = "saved"
        if saved_threshold is not None:
            try:
                threshold = float(saved_threshold)
                source = "saved"
            except (TypeError, ValueError):
                pass
        return {"enabled": enabled, "threshold": threshold, "source": source}

    def set_tangle_protection(self, enabled=None, threshold=None):
        """Persist a runtime tangle-protection choice; returns the new policy."""
        if enabled is not None:
            self.state.set_and_save(self.TANGLE_ENABLED_VAR, bool(enabled))
        if threshold is not None:
            threshold = float(threshold)
            if not (self.TANGLE_THRESHOLD_MIN <= threshold <= self.TANGLE_THRESHOLD_MAX):
                raise ValueError(
                    f"threshold must be between {self.TANGLE_THRESHOLD_MIN:g} and "
                    f"{self.TANGLE_THRESHOLD_MAX:g} seconds"
                )
            self.state.set_and_save(self.TANGLE_THRESHOLD_VAR, threshold)
        return self.get_tangle_protection()

    def reset_tangle_protection(self):
        """Forget the stored choice so the config defaults apply again."""
        variables = self.state.get_all()
        for name in (self.TANGLE_ENABLED_VAR, self.TANGLE_THRESHOLD_VAR):
            variables.pop(name, None)
            self.state._dirty.discard(name)
            try:
                self.gcode.run_script_from_command(
                    f"SAVE_VARIABLE VARIABLE={name} VALUE=None"
                )
            except Exception:
                logging.exception("ACE: could not clear %s", name)
        return self.get_tangle_protection()

    def is_ace_enabled(self):
        """Check if ACE Pro unit is enabled via output pin."""
        return self._health_monitor.is_ace_enabled()

    def update_ace_support_active_state(self):
        """Update ACE support state based on ACE_Pro pin."""
        return self._health_monitor.update_ace_support_active_state()

    def _monitor_ace_state(self, eventtime):
        """Monitor ACE Pro enable/disable state and connection health (2 second interval)."""
        return self._health_monitor._monitor_ace_state(eventtime)

    def _flush_if_idle(self, eventtime):
        """Flush dirty persistent state to disk when the printer is idle."""
        return self._health_monitor._flush_if_idle(eventtime)

    def _check_connection_health(self, eventtime):
        """Check connection stability for all ACE instances."""
        return self._health_monitor._check_connection_health(eventtime)

    def _handle_connection_issue(self, unstable_instances, eventtime):
        """Handle detected connection issues."""
        return self._health_monitor._handle_connection_issue(unstable_instances, eventtime)

    def _run_gcode_from_event(self, script):
        """Run a G-code script safely from a reactor callback greenlet."""
        return self._health_monitor._run_gcode_from_event(script)

    def _pause_for_connection_issue(self, unstable_instances):
        """Pause print due to ACE connection issue (deferred out of timer)."""
        return self._health_monitor._pause_for_connection_issue(unstable_instances)

    def _show_connection_issue_dialog(self, unstable_instances, is_printing):
        """Show Mainsail dialog for connection issue."""
        return self._health_monitor._show_connection_issue_dialog(unstable_instances, is_printing)

    def _close_connection_dialog(self):
        """Close the connection issue dialog."""
        return self._health_monitor._close_connection_dialog()

    def perform_tool_change(self, current_tool, target_tool, is_endless_spool=False):
        """Execute complete tool change sequence."""
        return self._toolchange_orchestrator.perform_tool_change(
            current_tool, target_tool, is_endless_spool=is_endless_spool
        )

    def restore_pre_toolchange_state(self):
        """Restore Z-hop and fan state after a failed toolchange before PAUSE (ERR-09 / C-21)."""
        return self._toolchange_orchestrator.restore_pre_toolchange_state()

    def register_tool_macros(self, instance_num):
        """
        Register T<n> commands for given instance.

        Instance 0: T0, T1, T2, T3
        Instance 1: T4, T5, T6, T7
        Etc.

        If user has defined a gcode_macro for a tool (e.g., for Spoolman integration),
        skip auto-registration to allow user's macro to take precedence.
        """
        for local_slot in range(SLOTS_PER_ACE):
            global_tool = get_tool_offset(instance_num) + local_slot
            macro_name = f"T{global_tool}"

            # Check if user has defined this macro (e.g., for Spoolman support)
            existing_macro = self.printer.lookup_object(f"gcode_macro {macro_name}", None)
            if existing_macro is not None:
                # User defined their own macro - skip auto-registration
                continue

            def make_tool_macro(tool_idx):
                def tool_macro(gcmd):
                    # Delegate to command handler
                    commands.cmd_ACE_CHANGE_TOOL(self, gcmd, tool_idx)

                return tool_macro

            desc = f"Select tool {global_tool} " f"(ACE instance {instance_num})"
            self.gcode.register_command(macro_name, make_tool_macro(global_tool), desc=desc)

    # ========== Status and Reporting ==========

    @property
    def config_feed_speed(self):
        """Default feed speed from config (mm/s)."""
        raw = self.ace_config.get("feed_speed", 60.0)
        try:
            return float(parse_instance_config(raw, 0, "feed_speed"))
        except (TypeError, ValueError):
            return 60.0

    @property
    def config_retract_speed(self):
        """Default retract speed from config (mm/s)."""
        raw = self.ace_config.get("retract_speed", 50.0)
        try:
            return float(parse_instance_config(raw, 0, "retract_speed"))
        except (TypeError, ValueError):
            return 50.0

    @property
    def feed_speed_override(self):
        """Runtime override for feed speed (mm/s), or None if using config/instance default."""
        return getattr(self, "_feed_speed_override", None)

    @feed_speed_override.setter
    def feed_speed_override(self, value):
        self._feed_speed_override = float(value) if value is not None else None
        for inst in getattr(self, "instances", ()):
            inst.feed_speed = (
                self._feed_speed_override
                if self._feed_speed_override is not None
                else getattr(inst, "_config_feed_speed", inst.feed_speed)
            )

    @property
    def retract_speed_override(self):
        """Runtime override for retract speed (mm/s), or None if using config/instance default."""
        return getattr(self, "_retract_speed_override", None)

    @retract_speed_override.setter
    def retract_speed_override(self, value):
        self._retract_speed_override = float(value) if value is not None else None
        for inst in getattr(self, "instances", ()):
            inst.retract_speed = (
                self._retract_speed_override
                if self._retract_speed_override is not None
                else getattr(inst, "_config_retract_speed", inst.retract_speed)
            )

    @property
    def feed_speed(self):
        """Active feed speed (mm/s) considering runtime override, instance, and config."""
        if getattr(self, "_feed_speed_override", None) is not None:
            return float(self._feed_speed_override)
        if self.instances:
            return float(self.instances[0].feed_speed)
        return self.config_feed_speed

    @feed_speed.setter
    def feed_speed(self, value):
        self.feed_speed_override = value

    @property
    def retract_speed(self):
        """Active retract speed (mm/s) considering runtime override, instance, and config."""
        if getattr(self, "_retract_speed_override", None) is not None:
            return float(self._retract_speed_override)
        if self.instances:
            return float(self.instances[0].retract_speed)
        return self.config_retract_speed

    @retract_speed.setter
    def retract_speed(self, value):
        self.retract_speed_override = value

    def get_status(self, eventtime=None):
        try:
            # Sensor states: True/False when sensor is registered, None when unavailable.
            toolhead_sensor = (
                self.get_switch_state(SENSOR_TOOLHEAD)
                if SENSOR_TOOLHEAD in self.sensors
                else None
            )
            rdm_sensor = (
                self.get_switch_state(SENSOR_RDM)
                if SENSOR_RDM in self.sensors
                else None
            )
            feed_speed = self.feed_speed
            retract_speed = self.retract_speed
            return {
                "ace_instances": len(self.instances),
                "current_index": self.state.get("ace_current_index", -1),
                "endless_spool_enabled": bool(
                    self.state.get("ace_endless_spool_enabled", False)
                ),
                "endless_spool_match_mode": self.state.get(
                    "ace_endless_spool_match_mode", "exact"
                ),
                "ace_pro_enabled": bool(self._ace_pro_enabled),
                "ace_connected": self.is_ace_connected(),
                "external_spool_mode": self.is_ace_absent(),
                "toolhead_sensor": toolhead_sensor,
                "rdm_sensor": rdm_sensor,
                "tangle_protection": self.get_tangle_protection(),
                "feed_speed": feed_speed,
                "retract_speed": retract_speed,
                "toolhead_extrusion_allowed": bool(getattr(self, "toolhead_extrusion_allowed", False)),
            }
        except Exception:
            return {
                "ace_instances": len(self.instances),
                "current_index": -1,
                "endless_spool_enabled": False,
                "endless_spool_match_mode": "exact",
                "ace_pro_enabled": False,
                "toolhead_sensor": None,
                "rdm_sensor": None,
                "tangle_protection": {"enabled": False, "threshold": 8.0, "source": "config"},
                "feed_speed": 60.0,
                "retract_speed": 50.0,
                "toolhead_extrusion_allowed": False,
            }

    def _resolve_instance_config(self, instance_num):
        """
        Resolve per-instance config by parsing override syntax.

        All keys from self.ace_config are copied, and only the keys
        listed in OVERRIDABLE_PARAMS are instance-resolved.
        """
        # Start with a shallow copy of the global ACE config
        resolved = dict(self.ace_config)

        # Resolve overridable params for this instance
        for param in OVERRIDABLE_PARAMS:
            if param in self.ace_config:
                raw_value = self.ace_config[param]
                resolved[param] = parse_instance_config(raw_value, instance_num, param)

        for param in CHOICE_OVERRIDABLE_PARAMS:
            if param in self.ace_config:
                raw_value = self.ace_config[param]
                resolved[param] = parse_instance_choice_config(raw_value, instance_num, param)

        resolved["active_protocol_name"] = resolve_protocol_name(
            resolved.get("protocol", "auto"),
            instance_num=instance_num,
            available_port_descriptions=self._get_available_port_descriptions(),
        )

        if "baud" in self.ace_config:
            resolved["baud"] = parse_instance_baud_config(
                self.ace_config["baud"],
                instance_num,
                resolved["active_protocol_name"],
            )

        return resolved

    def _create_instance_protocol(self, instance_config):
        """Create protocol adapter for this instance."""
        return create_protocol_adapter(instance_config["active_protocol_name"])

    def _get_available_port_descriptions(self):
        """List visible serial-port signatures for protocol auto-selection."""
        try:
            signatures = []
            for portinfo in serial.tools.list_ports.comports():
                signature = getattr(portinfo, "description", "")
                if not signature:
                    signature = getattr(portinfo, "product", "")
                if not signature:
                    signature = getattr(portinfo, "interface", "")
                if not signature:
                    signature = getattr(portinfo, "hwid", "")
                signatures.append(signature)
            return signatures
        except Exception:
            return []

    def _iter_unique_transport_instances(self):
        """Yield one representative instance per underlying serial transport."""
        seen_managers = set()
        for instance in self.instances:
            serial_mgr = getattr(instance, "serial_mgr", None)
            serial_mgr_id = id(serial_mgr)
            if serial_mgr_id in seen_managers:
                continue
            seen_managers.add(serial_mgr_id)
            yield instance

    def _get_instances_for_bus_session(self, bus_session):
        """Return logical instances that share one ACE2 bus session."""
        return self._shared_bus_coordinator._get_instances_for_bus_session(bus_session)

    def _get_shared_bus_bindings_varname(self, shared_instances):
        """Build stable persistent-state variable name for one ACE2 bus group."""
        return self._shared_bus_coordinator._get_shared_bus_bindings_varname(shared_instances)

    def _load_shared_bus_bindings(self, bus_session, shared_instances):
        """Restore persisted ACE2 UID bindings for one shared bus group."""
        return self._shared_bus_coordinator._load_shared_bus_bindings(bus_session, shared_instances)

    def _persist_shared_bus_bindings(self, bus_session, shared_instances):
        """Store current ACE2 UID bindings for one shared bus group."""
        return self._shared_bus_coordinator._persist_shared_bus_bindings(bus_session, shared_instances)

    def _get_shared_bus_ready_instances(self, bus_session):
        """Return shared-bus instances that currently have an assigned target device id."""
        return self._shared_bus_coordinator._get_shared_bus_ready_instances(bus_session)

    def _cancel_shared_bus_retry(self, bus_session):
        """Cancel pending discovery retry timer for one shared bus."""
        return self._shared_bus_coordinator._cancel_shared_bus_retry(bus_session)

    def _schedule_shared_bus_retry(self, bus_session, reason):
        """Schedule a bounded backoff retry for ACE2 shared-bus discovery."""
        return self._shared_bus_coordinator._schedule_shared_bus_retry(bus_session, reason)

    def _start_shared_bus_runtime(self, bus_session):
        """Start per-instance ACE2 status polling for one shared bus group."""
        return self._shared_bus_coordinator._start_shared_bus_runtime(bus_session)

    def _queue_shared_bus_instance_setup(self, bus_session):
        """Queue ACE2 startup setup commands for each logical instance on one bus."""
        return self._shared_bus_coordinator._queue_shared_bus_instance_setup(bus_session)

    def _get_transport_last_connected_time(self, serial_mgr):
        """Return last successful connect timestamp for one serial manager."""
        return self._shared_bus_coordinator._get_transport_last_connected_time(serial_mgr)

    def _monitor_transport_reconnects(self):
        """Keep reconnect timers alive and reinitialize shared buses after reconnect."""
        return self._shared_bus_coordinator._monitor_transport_reconnects()

    def _handle_shared_bus_unsolicited(self, bus_session, response):
        """Route unmatched ACE2 shared-bus responses to their logical instance."""
        return self._shared_bus_coordinator._handle_shared_bus_unsolicited(bus_session, response)

    def _send_shared_bus_request(self, instance, request, timeout_s=5.0):
        """Send one manager-owned request over shared ACE2 transport and wait for reply."""
        return self._shared_bus_coordinator._send_shared_bus_request(instance, request, timeout_s=timeout_s)

    def _initialize_shared_bus_transport(self, instance):
        """Discover and assign ACE2 devices on a shared bus transport."""
        return self._shared_bus_coordinator._initialize_shared_bus_transport(instance)

    def _on_shared_bus_connected(self, bus_session):
        """Schedule ACE2 shared-bus discovery/init outside reactor timers."""
        return self._shared_bus_coordinator._on_shared_bus_connected(bus_session)

    def _run_shared_bus_connected(self, bus_session):
        """Reinitialize and restart one ACE2 shared bus after connect."""
        return self._shared_bus_coordinator._run_shared_bus_connected(bus_session)

    def _build_shared_transport_kwargs(self, instance_num, instance_config, ace_enabled, protocol):
        """Create or reuse transport objects for protocols that share a physical bus."""
        return self._shared_bus_coordinator._build_shared_transport_kwargs(
            instance_num, instance_config, ace_enabled, protocol
        )

    @staticmethod
    def _slot_info_status(instance, local_slot):
        """Safely read hardware slot status from ``instance._info['slots']``."""
        return ToolchangeSpoolHelper._slot_info_status(instance, local_slot)

    def check_and_wait_for_spool_ready(self, target_tool, timeout_s=300, check_interval_s=1.0, stable_ready_s=3.0):
        """Check if the spool for target_tool is ready before feeding."""
        return self._toolchange_spool.check_and_wait_for_spool_ready(
            target_tool,
            timeout_s=timeout_s,
            check_interval_s=check_interval_s,
            stable_ready_s=stable_ready_s,
        )

    def _show_spool_not_ready_prompt(self, tool_index, instance_num, local_slot, inventory_status, ace_status):
        """Show Mainsail prompt when spool is not ready."""
        return self._toolchange_spool._show_spool_not_ready_prompt(
            tool_index, instance_num, local_slot, inventory_status, ace_status
        )

    def set_and_save_variable(self, varname, value):
        """
        Set a variable in persistent storage (deferred flush).

        Convenience wrapper around ``self.state.set()``.
        Kept so that callers (commands, instances) that hold a manager
        reference can use ``manager.set_and_save_variable(...)``.

        Args:
            varname: Variable name (string)
            value: Value to save (any JSON-serializable type)
        """
        self.state.set(varname, value)

    def has_rdm_sensor(self):
        """Check if RDM sensor is configured and available."""
        return self._sensor_coordinator.has_rdm_sensor()

    def is_feed_assist_active(self):
        """Check if any ACE instance has feed assist active.

        Returns:
            bool: True if at least one instance has feed_assist enabled.
        """
        for instance in self.instances:
            if instance._feed_assist_index >= 0:
                return True
        return False

    def get_rdm_encoder_pulse(self):
        """Return the RDM filament_tracker encoder_pulse count, or None."""
        return self._sensor_coordinator.get_rdm_encoder_pulse()

    def full_unload_slot(self, tool_index):
        """Fully unload a slot from the ACE."""
        return self._toolchange_spool.full_unload_slot(tool_index)
