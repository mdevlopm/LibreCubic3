"""
High-level filament loading, unloading, and verification sensor mixin for AceInstance.
"""

import logging
from .config import (
    SENSOR_RDM,
    SENSOR_TOOLHEAD,
    FILAMENT_STATE_SPLITTER,
    FILAMENT_STATE_TOOLHEAD,
    FILAMENT_STATE_NOZZLE,
    MAX_RETRIES,
)


class InstanceLoadingMixin:
    """Provides coordinated feed-to-toolhead, smart unload, and sensor verification."""

    def _make_sensor_trigger_monitor(self, sensor_type):
        """
        Create a sensor trigger time monitor callback.

        Args:
            sensor_type: SENSOR_TOOLHEAD or SENSOR_RDM
            expected_length: Expected retraction distance (mm)
            expected_speed: Expected retraction speed (mm/s)

        Returns:
            Callable that monitors sensor state changes and returns timing data
        """
        state_data = {
            "start_time": None,
            "trigger_time": None,
            "initial_state": None,
            "call_count": 0
        }

        def monitor():
            state_data["call_count"] += 1
            current_state = self.manager.get_switch_state(sensor_type)

            # First call - record initial state and start time
            if state_data["start_time"] is None:
                state_data["start_time"] = self.reactor.monotonic()
                state_data["initial_state"] = current_state
                return

            # Sensor state changed - record trigger time
            if state_data["trigger_time"] is None and current_state != state_data["initial_state"]:
                state_data["trigger_time"] = self.reactor.monotonic()

        # Return both the monitor function and the state data
        monitor.get_timing = lambda: (
            state_data["trigger_time"] - state_data["start_time"]
            if state_data["trigger_time"] and state_data["start_time"]
            else None
        )
        monitor.get_call_count = lambda: state_data["call_count"]
        monitor.state_data = state_data

        return monitor

    def _feed_to_toolhead_with_extruder_assist(self, local_slot, feed_length, feed_speed,
                                               extruder_feeding_length, extruder_feeding_speed):
        """
        Feed filament to toolhead using ACE feed + extruder assist.

        Starts ACE feed, polls toolhead sensor until triggered (or timeout),
        then slows to extruder_feeding_speed and drives the extruder to seat
        the filament, finally switches to feed_assist mode.

        Args:
            local_slot: Slot index to feed from
            feed_length: Total length to feed (mm)
            feed_speed: Initial ACE feed speed (mm/s)
            extruder_feeding_length: Extruder assist distance (mm)
            extruder_feeding_speed: Slow speed for final extruder push (mm/s)

        Returns:
            float: Extruder distance pushed during assist

        Raises:
            ValueError: If feed command fails or sensor times out
        """
        self._disable_feed_assist(local_slot)
        self.execute_feed_with_retries(local_slot, feed_length, feed_speed)

        expected_time = feed_length / feed_speed
        timeout_s = expected_time * self.timeout_multiplier

        # Coordinated extruder nudges during ACE feed
        start_time = self.reactor.monotonic()

        while not self.manager.get_switch_state(SENSOR_TOOLHEAD):
            now = self.reactor.monotonic()
            if now - start_time > timeout_s:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Feed timeout for {feed_length}mm after {timeout_s} seconds"
                )
                break
            self.dwell(0.1)

        # Final sanity check
        if not self.manager.get_switch_state(SENSOR_TOOLHEAD):
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Toolhead sensor not triggered after feed. "
                f"Running extruder assist for up-to 60s..."
            )
            self._enable_feed_assist(local_slot)

            timeout = self.reactor.monotonic() + 60.0
            while not self.manager.get_switch_state(SENSOR_TOOLHEAD) and self.reactor.monotonic() < timeout:
                self.dwell(1)
            self._disable_feed_assist(local_slot)

            if not self.manager.get_switch_state(SENSOR_TOOLHEAD):
                raise ValueError(
                    f"ACE[{self.instance_num}]: Feeding filament to toolhead failed. "
                    f"Toolhead filament sensor is not triggering. Filament may be jammed."
                )
            else:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Toolhead sensor finally triggered after "
                    f"running feed-assist for 60s. Continuing..."
                )
        pushed_length = 0.0
        if getattr(self, "toolhead_extrusion_allowed", True) and extruder_feeding_length > 0:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Slowing feedspeed down {extruder_feeding_speed:.2f} for toolhead load"
            )

            max_speed_change_retries = 3
            speed_changed = False
            while not speed_changed and (max_speed_change_retries > 0):
                speed_changed = self._change_feed_speed(local_slot, extruder_feeding_speed)
                self.dwell(delay=0.2)
                max_speed_change_retries -= 1

            if not speed_changed:
                self._stop_feed(local_slot)
                raise ValueError(
                    f"ACE[{self.instance_num}]: Failed to change feed speed to "
                    f"{extruder_feeding_speed}mm/s after multiple attempts"
                )

            self._extruder_move(extruder_feeding_length, extruder_feeding_speed, wait_for_move_end=True)
            pushed_length = float(extruder_feeding_length)
        elif not getattr(self, "toolhead_extrusion_allowed", True):
            if not getattr(self, "_extrusion_suppressed_logged", False):
                self._extrusion_suppressed_logged = True
                self.gcode.respond_info(
                    "ACE: Toolhead extrusion is disabled (toolhead_extrusion_allowed=False); skipping extruder assist move"
                )

        self._stop_feed(local_slot)
        self.wait_ready()
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: Switching from feeding to feed_assist mode"
        )
        self._enable_feed_assist(local_slot)
        # _enable_feed_assist already contains its own post-send wait_ready() (guarded by
        # feed_assist_causes_busy), so a second wait here is redundant for ACE1 and would
        # deadlock on ACE2.

        return pushed_length

    def execute_feed_with_retries(self, local_slot, feed_length, feed_speed):
        max_retries = MAX_RETRIES
        for attempt in range(max_retries):
            response = self.feed_filament_with_wait_for_response(local_slot, feed_length, feed_speed)

            if response.get("code") == 0:
                break
            elif response.get("msg") == "FORBIDDEN" and attempt < max_retries - 1:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Feed FORBIDDEN, waiting 1s before retry "
                    f"(attempt {attempt + 1}/{max_retries})..."
                )
                self.dwell(delay=1.0)
            else:
                raise ValueError(
                    f"ACE[{self.instance_num}]: Feed failed: {response.get('msg')}"
                )

    def _feed_filament_into_toolhead(self, tool, check_pre_condition=True):
        """Feed filament from slot to toolhead sensor, then extruder to nozzle."""
        self.wait_ready()
        local_slot = tool - self.tool_offset

        if local_slot < 0 or local_slot >= self.SLOT_COUNT:
            raise ValueError(f"Tool {tool} not managed by this ACE instance.")

        if check_pre_condition:
            has_rdm = self.manager.has_rdm_sensor()

            if has_rdm and self.manager.get_switch_state(SENSOR_RDM):
                raise ValueError("Cannot feed, filament stuck in RMS")

            if self.manager.get_switch_state(SENSOR_TOOLHEAD):
                raise ValueError("Cannot feed, filament in nozzle")

        try:
            self._feed_to_toolhead_with_extruder_assist(
                local_slot,
                self.toolchange_load_length,
                self.feed_speed,
                self.extruder_feeding_length,
                self.extruder_feeding_speed
            )
        except Exception as e:
            # Perform your custom action here, e.g., log, cleanup, etc.
            self._report_error(
                f"ACE[{self.instance_num}]: Exception during feed to toolhead: {e}, "
                f"retracting filament 150mm back in case it got squished and stuck "
                f"in the filament-hub"
            )
            self._retract(local_slot, 150, self.retract_speed)

            raise  # Re-raise the original exception

        self.state.set(
            "ace_filament_pos",
            FILAMENT_STATE_TOOLHEAD
        )

        if not getattr(self, "toolhead_extrusion_allowed", True):
            if not getattr(self, "_extrusion_suppressed_logged", False):
                self._extrusion_suppressed_logged = True
                self.gcode.respond_info(
                    "ACE: Toolhead extrusion is disabled (toolhead_extrusion_allowed=False); skipping toolhead purge/extrude"
                )
            return 0.0

        self.gcode.run_script_from_command("G92 E0")
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: Feeding from sensor to nozzle..."
        )

        self._extruder_move(
            self.toolhead_full_purge_length,
            self.toolhead_slow_loading_speed
        )

        toolhead = self.printer.lookup_object('toolhead')
        toolhead.wait_moves()

        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: Feeding from sensor to nozzle "
            f"({self.toolhead_full_purge_length}mm) finished"
        )
        self.state.set(
            "ace_filament_pos", FILAMENT_STATE_NOZZLE
        )

        return self.toolhead_full_purge_length

    def rmd_triggered_unload_slot(self, manager, slot, length, overshoot_length):
        """Unload slot with RDM sensor monitoring during retraction.

        Monitors the RDM sensor via _retract(early_stop_callback=...) and
        stops retraction as soon as the filament clears, plus overshoot.

        Args:
            manager: AceManager instance (for sensor access)
            slot: Local slot index (0-3)
            length: Maximum retract distance (mm) — safety limit
            overshoot_length: Extra mm to retract after sensor clears
        Returns:
            True if sensor cleared and retract stopped, False on timeout
        """
        if not manager.has_rdm_sensor():
            return False

        f_index = self._get_current_feed_assist_index()
        self._disable_feed_assist(slot)

        # Shared state for the callback
        rdm_state = {
            "cleared": False,
            "clear_time": None,
            "start_time": self.reactor.monotonic(),
            "last_log": 0,
        }

        def rdm_early_stop_check():
            """Called every ~200ms from _retract()'s dwell loop.
            Returns a truthy string to trigger early stop, or None to continue."""
            elapsed = self.reactor.monotonic() - rdm_state["start_time"]

            rdm_has_filament = manager.get_instant_switch_state(SENSOR_RDM)

            # Log sensor state every 2 seconds
            if elapsed - rdm_state["last_log"] >= 2.0:
                state_str = "TRIGGERED" if rdm_has_filament else "CLEAR"
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: [{elapsed:.1f}s] RDM={state_str}"
                )
                rdm_state["last_log"] = elapsed

            if not rdm_has_filament and not rdm_state["cleared"]:
                rdm_state["cleared"] = True
                rdm_state["clear_time"] = elapsed

                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: RDM CLEAR after {elapsed:.1f}s — "
                    f"applying {overshoot_length}mm overshoot"
                )

                # Overshoot: let the motor run a bit longer before stopping
                overshoot_time = overshoot_length / self.retract_speed
                if overshoot_time > 0:
                    self._pause(overshoot_time)
                return f"RDM clear at {elapsed:.1f}s"

            return None

        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: RDM-triggered unload — "
            f"slot {slot}, max {length}mm @ {self.retract_speed}mm/s, "
            f"overshoot {overshoot_length}mm"
        )

        try:
            result = self._retract(
                slot, length, self.retract_speed,
                early_stop_callback=rdm_early_stop_check,
            )
        except Exception as e:
            self._stop_retract(slot)
            self._report_error(
                f"ACE[{self.instance_num}]: Retract error: {e}"
            )
            self._update_feed_assist(f_index)
            # If the sensor already cleared, the physical unload succeeded
            # even if wait_ready timed out afterward
            return rdm_state["cleared"]

        self._update_feed_assist(f_index)

        # Slot was already empty before retraction started
        if result and "slot empty" in result.get("msg", ""):
            return True

        if rdm_state["cleared"]:
            total = self.reactor.monotonic() - rdm_state["start_time"]
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: RMD triggered unload slot {slot} "
                f"completed in {total:.1f}s "
                f"(sensor cleared at {rdm_state['clear_time']:.1f}s)"
            )
            return True
        else:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: RDM triggered unload — "
                f"sensor never cleared during {length}mm retract!"
            )
            return False

    def _smart_unload_slot(self, slot, length=100, on_retract_started=None):
        """
        Fixed-length retraction with optional sensor validation.

        **SIMPLIFIED MODE:**
        - Always retracts exactly 'length' mm (ignores overshoot_length)
        - No sensor polling during retraction
        - RDM sensor used only for post-retraction validation (if available)

        Args:
            slot: Slot index to retract from
            length: Retraction length in mm (exact distance)
            on_retract_started: Optional callback after retract starts

        Returns:
            bool: True if retraction completed successfully

        Raises:
            ValueError: If path still blocked after retraction (RDM available only)
        """
        has_rdm = self.manager.has_rdm_sensor()

        timeout_seconds = (length / self.retract_speed) * self.timeout_multiplier

        mode_str = "with RDM validation" if has_rdm else "toolhead-only mode"
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: Fixed-length unload slot {slot} ({mode_str}):\n"
            f"  Length: {length}mm\n"
            f"  Speed: {self.retract_speed}mm/s\n"
            f"  Timeout: {timeout_seconds:.1f}s"
        )

        try:
            self._disable_feed_assist(slot)

            # Create sensor monitor for toolhead sensor (primary sensor for retraction)
            sensor_monitor = self._make_sensor_trigger_monitor(
                SENSOR_TOOLHEAD
            )

            start_time = self.reactor.monotonic()

            # Start retraction with sensor monitoring
            self._retract(
                slot,
                length,
                self.retract_speed,
                on_retract_started,
                on_wait_for_ready=sensor_monitor
            )

            elapsed_s = self.reactor.monotonic() - start_time
            sensor_trigger_time = sensor_monitor.get_timing()
            call_count = sensor_monitor.get_call_count()
            expected_time = length / self.retract_speed

            # Log retraction results with timing data
            if sensor_trigger_time is not None:
                sensor_efficiency = (sensor_trigger_time / expected_time * 100) if expected_time > 0 else 0
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Retraction completed in {elapsed_s:.2f}s "
                    f"(sensor: {sensor_trigger_time:.2f}s, {sensor_efficiency:.1f}% of expected, "
                    f"{call_count} polls)"
                )

                if sensor_efficiency > 90:
                    extra_length = self.parkposition_to_toolhead_length
                    extra_speed = self.retract_speed / 2
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: Suspicious late sensor trigger - "
                        f"Retracting extra parkposition_to_toolhead_length to ensure clear path: "
                        f"(Length: {extra_length:.2f}mm, "
                        f"{extra_speed:.1f}mm/s)"
                    )

                    self._retract(
                        slot,
                        extra_length,
                        extra_speed
                    )

            else:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Retraction completed in {elapsed_s:.2f}s "
                    f"(sensor did not change state, {call_count} polls)"
                )

            # Give klipper time to process any pending state changes and avoid reporting not updated sensor state
            self.dwell(1)
            # Consistency check: Validate with sensors (if RDM available)
            toolhead_clear = not self.manager.get_switch_state(SENSOR_TOOLHEAD)

            if has_rdm:
                rdm_clear = not self.manager.get_switch_state(SENSOR_RDM)
                path_clear = toolhead_clear and rdm_clear

                if path_clear:
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: ✓ Path clear (both sensors)"
                    )
                    return True
                else:
                    if not self.rmd_triggered_unload_slot(self.manager, slot, length, self.parkposition_to_rdm_length):
                        slot_status = self.inventory[slot].get("status", "unknown")
                        raise ValueError(
                            "ACE[%d]: ✗ Retraction failed - path still blocked after %.1fmm\n"
                            "  Slot: %d\n"
                            "  Spool status: %s\n"
                            "  Time: %.2fs\n"
                            "  RDM sensor: %s\n"
                            "  Toolhead sensor: %s\n"
                            "  → Manual intervention required"
                            % (
                                self.instance_num,
                                float(length),
                                slot,
                                slot_status,
                                float(elapsed_s),
                                "BLOCKED" if not rdm_clear else "clear",
                                "BLOCKED" if not toolhead_clear else "clear",
                            )
                        )
                    else:
                        self.gcode.respond_info(
                            f"ACE[{self.instance_num}]: ✓ RDM-triggered unload succeeded"
                        )
                        return True
            else:
                # No RDM - validate with toolhead sensor only
                if toolhead_clear:
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: ✓ Toolhead sensor clear"
                    )
                    return True
                else:
                    self.gcode.respond_info(
                        "ACE[%d]: ⚠ WARNING - Toolhead sensor still triggered after %.1fmm retraction\n"
                        "  This may indicate:\n"
                        "  - Insufficient retraction length\n"
                        "  - Filament stuck in path\n"
                        "  - Sensor malfunction\n"
                        "  Proceeding anyway (no RDM sensor for validation)"
                        % (self.instance_num, float(length))
                    )
                    return False
        except ValueError as e:
            # Validation error - already logged
            self._report_error(
                f"ACE[{self.instance_num}]: Smart unload validation failed: {e}"
            )
            try:
                self._stop_retract(slot)
            except Exception:
                pass
            raise

        except Exception as e:
            # Unexpected error
            self._report_error(
                f"ACE[{self.instance_num}]: Unexpected error during unload: {e}"
            )
            try:
                self._stop_retract(slot)
            except Exception:
                pass
            raise

    def _feed_filament_to_verification_sensor(self, slot, target_sensor, feed_length):
        """
        Feed filament from slot until target sensor triggers.

        Args:
            slot: Slot index to feed from
            target_sensor: SENSOR_RDM or SENSOR_TOOLHEAD
            feed_length: Max feed distance to sensor (mm)

        Raises:
            ValueError: If feeding fails or sensors are in wrong state
        """
        target_sensor_name = "RDM" if target_sensor == SENSOR_RDM else "toolhead"

        logging.info(
            f"ACE[{self.instance_num}]: Feeding to {target_sensor_name} sensor "
            f"(length={feed_length}mm)"
        )

        self.wait_ready()

        # Pre-check: target sensor must be clear
        if self.manager.get_switch_state(target_sensor):
            raise ValueError(
                f"ACE[{self.instance_num}]: Cannot feed, filament already at {target_sensor_name} sensor"
            )

        # Start feeding
        self._feed(slot, feed_length, self.feed_speed)

        # Wait for target sensor to trigger
        expected_time = feed_length / self.feed_speed
        timeout_s = expected_time * self.timeout_multiplier
        start_time = self.reactor.monotonic()

        while not self.manager.get_switch_state(target_sensor):
            if self.reactor.monotonic() - start_time > timeout_s:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Feed timeout after {timeout_s:.1f}s "
                    f"(target: {target_sensor_name})"
                )
                break
            self.dwell(0.01)

        self._stop_feed(slot)

        # Incremental feeding if sensor not reached
        accumulated_feed_length = feed_length

        if not self.manager.get_switch_state(target_sensor):

            while (not self.manager.get_switch_state(target_sensor) and
                   accumulated_feed_length < self.total_max_feeding_length):
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Incremental feed to {target_sensor_name} "
                    f"({self.incremental_feeding_length}mm at "
                    f"{self.incremental_feeding_speed}mm/s)"
                )

                self._feed(slot, self.incremental_feeding_length, self.incremental_feeding_speed)

                accumulated_feed_length += self.incremental_feeding_length

                self.dwell((self.incremental_feeding_length / self.incremental_feeding_speed) + 0.1)

            if not self.manager.get_switch_state(target_sensor):
                raise ValueError(
                    f"ACE[{self.instance_num}]: Fed {accumulated_feed_length}mm, "
                    f"but {target_sensor_name} sensor not triggered"
                )

            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Filament reached {target_sensor_name} sensor after feeding "
                f"{accumulated_feed_length}mm. Consider updating 'feed_length' if needed."
            )

        self.wait_ready()

        # Set final filament position state
        if target_sensor == SENSOR_RDM:
            self.state.set(
                "ace_filament_pos",
                FILAMENT_STATE_SPLITTER
            )
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Filament position set to splitter (at RDM)"
            )
        else:
            self.state.set(
                "ace_filament_pos",
                FILAMENT_STATE_TOOLHEAD
            )
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Filament position set to toolhead"
            )
