import time

from .config import (
    SENSOR_TOOLHEAD,
    SENSOR_RDM,
    FILAMENT_STATE_BOWDEN,
    get_instance_from_tool,
    get_local_slot,
)
from .reporting import emit_error
from .toolchange import toolchange_in_progress_guard


class ToolchangeUnloadHelper:
    """Handles coordinated retraction, smart_unload, and slot-cycling identification."""

    def __init__(self, manager):
        self.manager = manager

    def execute_coordinated_retraction(self, retract_length, retract_speed, retract_speed_mmmin, current_tool):
        """
        Perform coordinated retraction of ACE and extruder.

        Waits for ACE motion to fully complete before returning, ensuring
        sensor readings are accurate after retraction.

        Args:
            retract_length: Length to retract (mm)
            retract_speed: ACE retraction speed (mm/s)
            retract_speed_mmmin: Extruder retraction speed (mm/min)
            current_tool: Tool index to retract
        """
        mgr = self.manager
        instance_num = get_instance_from_tool(current_tool)
        if instance_num >= 0:
            ace_inst = mgr.instances[instance_num]
            local_slot = get_local_slot(current_tool, instance_num)

            mgr.gcode.respond_info(
                f"ACE: Synchronized retraction: extruder + ACE[{instance_num}] slot {local_slot}, "
                f"{retract_length:.2f}mm at {retract_speed:.2f}mm/s"
            )

            # Disable feed assist BEFORE retraction — feed assist pushes
            # filament forward, which fights the retraction and can cause jams.
            if ace_inst._feed_assist_index == local_slot:
                mgr.gcode.respond_info(
                    f"ACE[{instance_num}]: Disabling feed assist on slot {local_slot} before retraction"
                )
                ace_inst._disable_feed_assist(local_slot)

            ace_inst.wait_ready()
            ace_inst._retract(local_slot, length=retract_length, speed=retract_speed)

            mgr.gcode.run_script_from_command("M83")  # Relative extrusion
            mgr.gcode.run_script_from_command(f"G1 E-{retract_length} F{retract_speed_mmmin}")

            ace_inst.wait_ready()
            motion_time = retract_length / retract_speed
            safety_margin = 1.0  # 1 second extra
            total_wait_time = motion_time + safety_margin

            mgr.gcode.respond_info(
                f"ACE[{instance_num}]: Waiting {total_wait_time:.1f}s for retraction to complete "
                f"({motion_time:.1f}s motion + {safety_margin:.1f}s margin)"
            )
            ace_inst.dwell(total_wait_time)

            max_status_wait = 5.0  # Max 5 seconds to wait for status update
            status_check_start = mgr.reactor.monotonic()

            while True:
                slot_status = ace_inst.inventory[local_slot].get("status", "unknown")
                if slot_status == "ready":
                    mgr.gcode.respond_info(
                        f"ACE[{instance_num}]: Slot {local_slot} confirmed ready after retraction"
                    )
                    break

                elapsed = mgr.reactor.monotonic() - status_check_start
                if elapsed > max_status_wait:
                    mgr.gcode.respond_info(
                        f"ACE[{instance_num}]: WARNING - Slot {local_slot} status still '{slot_status}' "
                        f"after {elapsed:.1f}s (expected 'ready')"
                    )
                    break

                mgr.reactor.pause(mgr.reactor.monotonic() + 0.1)

            mgr.gcode.run_script_from_command("G92 E0")  # Reset extruder position
            mgr.gcode.respond_info(
                f"ACE[M]: CUT DONE + retraction {retract_length}mm"
            )
        else:
            mgr.gcode.respond_info(
                f"ACE: Warning - current tool {current_tool} has no instance, "
                f"cannot coordinate ACE retraction"
            )

    @toolchange_in_progress_guard
    def smart_unload(self, tool_index=-1, prepare_toolhead=True, keep_heater=False):
        """
        Unload with slot cycling when tool is unknown.

        USE CASE for cycling:
        - Current tool is unknown (-1)
        - Toolhead sensor is triggered
        - Need to identify which tool is loaded

        ALL OTHER CASES: Direct unload or fail with error.
        """
        mgr = self.manager
        current_tool_index = mgr.state.get("ace_current_index", -1)

        mgr.gcode.respond_info(f"ACE: Smart unload tool {tool_index} (current: {current_tool_index})")

        tool_for_temp = tool_index if tool_index >= 0 else current_tool_index
        if prepare_toolhead:
            mgr.gcode.respond_info("ACE: Preparing toolhead")
            mgr.prepare_toolhead_for_filament_retraction(tool_index=tool_for_temp)

        retract_length = mgr.toolhead_retraction_length
        retract_speed = mgr.toolhead_retraction_speed

        # ===== CASE 1: Tool is known - direct unload =====
        if tool_index >= 0:
            instance_num = get_instance_from_tool(tool_index)
            if instance_num < 0:
                raise Exception(f"Tool {tool_index} not managed by any ACE instance")

            instance = mgr.instances[instance_num]
            local_slot = get_local_slot(tool_index, instance_num)
            slot_status = instance.inventory[local_slot].get("status", "empty")

            # Check if slot is empty BEFORE attempting unload
            if slot_status == "empty":
                raise Exception(
                    f"Cannot unload T{tool_index} - ACE slot {local_slot} is EMPTY.\n"
                )

            # Sensor already clear - choose retract distance based on path state
            if not mgr.get_instant_switch_state(SENSOR_TOOLHEAD):
                if mgr.is_filament_path_free_instant():
                    # All sensors clear: filament was likely manually removed.
                    # A short safety retract is enough to pull back any tip sitting
                    # just inside the ACE hub above the sensor boundary.
                    param = "parkposition_to_rdm_length" if mgr.has_rdm_sensor() \
                        else "parkposition_to_toolhead_length"
                    retract_dist = mgr._get_config_for_tool(tool_index, param)
                    mgr.gcode.respond_info(
                        f"ACE: Filament path fully free for T{tool_index} "
                        f"(filament may have been manually removed) - "
                        f"short safety retract of {retract_dist}mm"
                    )
                    instance._smart_unload_slot(local_slot, length=retract_dist)
                else:
                    # Toolhead clear but RDM still triggered: retract until the
                    # RDM clears.  With an RDM sensor, monitor it during the
                    # retraction (early stop + overshoot) instead of blindly
                    # pulling the full park-to-toolhead distance — the latter can
                    # keep retracting long after the path is clear and pull the
                    # slot's filament back over the ACE entry sensor.
                    retract_dist = mgr._get_config_for_tool(
                        tool_index, "parkposition_to_toolhead_length"
                    )
                    if mgr.has_rdm_sensor():
                        mgr.gcode.respond_info(
                            f"ACE: Toolhead clear, RDM triggered - RDM-monitored "
                            f"retract of T{tool_index} (max {retract_dist}mm)"
                        )
                        unload_ok = instance.rmd_triggered_unload_slot(
                            mgr, local_slot,
                            length=retract_dist,
                            overshoot_length=instance.rdm_overshoot_length,
                        )
                        if not unload_ok:
                            raise Exception(
                                f"RDM-monitored unload of T{tool_index} failed"
                            )
                    else:
                        # No RDM sensor: a fixed-length retract is the best we can
                        # do — there is no sensor between ACE and toolhead to stop on.
                        mgr.gcode.respond_info(
                            f"ACE: Toolhead clear - full retract of T{tool_index} ({retract_dist}mm)"
                        )
                        instance._smart_unload_slot(local_slot, length=retract_dist)

                if mgr.is_filament_path_free_instant():
                    mgr.state.set("ace_filament_pos", FILAMENT_STATE_BOWDEN)
                    mgr.gcode.respond_info(f"ACE: Tool {tool_index} unloaded successfully")
                    if not keep_heater:
                        mgr._turn_off_heater_if_idle()
                    return True
                else:
                    raise Exception(f"Path still blocked after unload of T{tool_index}")

            # Sensor triggered - coordinated retraction
            try:
                # ACE2 may be 'busy' because feed assist is active — do not call
                # wait_ready() here, as that would deadlock.  _disable_feed_assist
                # handles correct sequencing (stop → dwell → wait_ready) internally.
                if not instance.protocol.feed_assist_causes_busy():
                    instance.wait_ready()
                parkposition_to_toolhead_length = mgr._get_config_for_tool(
                    tool_index, "parkposition_to_toolhead_length"
                )

                # Disable feed assist BEFORE any motion — feed assist pushes
                # filament forward and would fight both the extruder retract
                # and the ACE retract that follow.
                if instance._feed_assist_index == local_slot:
                    mgr.gcode.respond_info(
                        f"ACE: Disabling feed assist on slot {local_slot} before coordinated retract"
                    )
                    instance._disable_feed_assist(local_slot)

                mgr.gcode.respond_info(
                    f"ACE: Retracting T{tool_index} "
                    f"({retract_length:.3f}mm at {retract_speed:.3f}mm/s)"
                )

                # Start extruder retraction (10% faster for slack)
                mgr._extruder_move(-abs(retract_length), retract_speed * 1.10, wait_for_move_end=False)

                # Start ACE retraction — use RDM sensor for early stop if available
                if mgr.has_rdm_sensor():
                    unload_ok = instance.rmd_triggered_unload_slot(
                        mgr, local_slot,
                        length=parkposition_to_toolhead_length + retract_length,
                        overshoot_length=instance.rdm_overshoot_length
                    )
                else:
                    unload_ok = instance._smart_unload_slot(
                        local_slot,
                        length=parkposition_to_toolhead_length + retract_length,
                    )

                # Wait for extruder to finish
                mgr._wait_toolhead_move_finished()

                if unload_ok and mgr.is_filament_path_free_instant():
                    mgr.state.set("ace_filament_pos", FILAMENT_STATE_BOWDEN)
                    mgr.gcode.respond_info(f"ACE: Tool {tool_index} unloaded successfully")
                    if not keep_heater:
                        mgr._turn_off_heater_if_idle()
                    return True
                else:
                    raise Exception(f"Unload failed for T{tool_index}")

            except Exception as e:
                emit_error(mgr.gcode, f"ACE: Error during unload: {e}")
                raise
            finally:
                mgr.gcode.run_script_from_command("G92 E0")
                mgr.gcode.run_script_from_command("G90")

        # ===== CASE 2: Given toolindex is set to unknown
        # + any sensor triggered (toolhead or RDM) => CYCLE TO IDENTIFY =====
        toolhead_triggered = mgr.get_switch_state(SENSOR_TOOLHEAD)
        rdm_triggered = mgr.get_switch_state(SENSOR_RDM) if mgr.has_rdm_sensor() else False

        # If any sensor is triggered, we need to cycle to identify the tool,
        # we start with cycling with current_tool_index
        if toolhead_triggered or rdm_triggered:
            sensor_desc = "toolhead" if toolhead_triggered else "RDM"
            mgr.gcode.respond_info(
                f"ACE: Current tool unknown but {sensor_desc} sensor triggered - cycling slots to identify loaded tool"
            )

            # Distances for completing unload after identification
            park_to_toolhead_len = mgr._get_config_for_tool(
                0, "parkposition_to_toolhead_length"
            )
            park_to_rdm_len = (
                mgr._get_config_for_tool(0, "parkposition_to_rdm_length")
                if mgr.has_rdm_sensor() else park_to_toolhead_len
            )

            retract_speed_mmmin = retract_speed * 60

            # Use unified cycling that also handles RDM-only trigger
            full_unload_length = (
                park_to_rdm_len
                if (rdm_triggered and not toolhead_triggered and mgr.has_rdm_sensor())
                else park_to_toolhead_len
            )

            return mgr._identify_and_unload_by_cycling(
                current_tool_index,
                tool_index,
                retract_length,
                retract_speed,
                retract_speed_mmmin,
                full_unload_length
            )
        else:
            mgr.gcode.respond_info(
                "ACE: Not cycling - no sensor triggered")

        # ===== Normal case if no tool was loaded, nothing to do here
        if current_tool_index == -1 and not (toolhead_triggered or rdm_triggered):
            mgr.gcode.respond_info(
                "ACE: No tool loaded and sensor clear - nothing to unload"
            )
            mgr.state.set("ace_filament_pos", FILAMENT_STATE_BOWDEN)
            return True

        if current_tool_index >= 0 and not (toolhead_triggered or rdm_triggered):
            mgr.gcode.respond_info(
                f"ACE: Unplausible state in smart_unload detected. "
                f"Current_tool_index={current_tool_index} but sensor clear - "
                f"assuming already unloaded, updating state accordingly."
            )
            mgr.state.set("ace_current_index", -1)
            mgr.state.set("ace_filament_pos", FILAMENT_STATE_BOWDEN)
            return True

        # ===== Something is strange... Shouldn't reach here =====
        mgr.gcode.respond_info(
            f"ACE: Invalid state: current_tool_index={current_tool_index} "
            f"tool_index={tool_index} toolhead_triggered={toolhead_triggered} "
            f"rdm_triggered={rdm_triggered}"
        )
        raise Exception("Unexpected state in smart_unload")

    def _identify_and_unload_by_cycling(
        self,
        current_tool_index,
        attempted_tool_index,
        retract_length,
        retract_speed,
        retract_speed_mmmin,
        full_unload_length
    ):
        """
        Identify loaded tool with three-case sensor strategy.

        CASE 1: No sensors triggered → path clear, no unload needed
        CASE 2: Toolhead sensor triggered → cycle with extruder retractions
        CASE 3: RDM triggered (toolhead clear) → cycle with RDM monitoring
        """
        mgr = self.manager
        toolhead_triggered = mgr.get_switch_state(SENSOR_TOOLHEAD)
        rdm_triggered = mgr.get_switch_state(SENSOR_RDM) if mgr.has_rdm_sensor() else False

        # CASE 1: No sensors triggered - path is clear
        if not toolhead_triggered and not rdm_triggered:
            mgr.gcode.respond_info(
                "ACE: No sensors triggered - path clear, no unload needed"
            )
            mgr.state.set("ace_filament_pos", FILAMENT_STATE_BOWDEN)
            return True

        # CASE 2: Toolhead sensor triggered - cycle with extruder retractions to identify tool
        if toolhead_triggered:
            mgr.gcode.respond_info(
                f"ACE: Toolhead sensor triggered - cycling slots with test retractions "
                f"({retract_length}mm at {retract_speed}mm/s) to identify loaded tool"
            )

            # Use existing cycling logic
            return mgr._cycle_slots_with_sensor_check(
                current_tool_index,
                attempted_tool_index,
                retract_length,
                retract_speed,
                retract_speed_mmmin,
                full_unload_length,
                sensor_name=SENSOR_TOOLHEAD,
                use_extruder=True
            )

        # CASE 3: RDM triggered but toolhead clear - monitor RDM during ACE-only retraction
        if rdm_triggered and mgr.has_rdm_sensor():
            # Get RDM-specific config
            tool_for_config = attempted_tool_index if attempted_tool_index >= 0 else current_tool_index
            if tool_for_config < 0:
                parkposition_to_rdm_length = mgr.instances[0].parkposition_to_rdm_length
                parkposition_to_toolhead_length = mgr.instances[0].parkposition_to_toolhead_length
                rdm_retract_speed = mgr.instances[0].feed_speed  # Use faster feed_speed for long RDM retraction
            else:
                parkposition_to_rdm_length = mgr._get_config_for_tool(
                    tool_for_config, "parkposition_to_rdm_length"
                )
                parkposition_to_toolhead_length = mgr._get_config_for_tool(
                    tool_for_config, "parkposition_to_toolhead_length"
                )

                rdm_retract_speed = mgr._get_config_for_tool(
                    tool_for_config, "feed_speed"
                )
            # Filament could be just before toolhead - ensure full unload length covers toolhead to rdm sensor
            full_unload_length = parkposition_to_toolhead_length

            mgr.gcode.respond_info(
                "ACE: RDM sensor triggered but toolhead sensor not - "
                f"Retracting and monitoring RDM sensor during ACE-only retraction "
                f"({full_unload_length}mm at {rdm_retract_speed}mm/s)"
            )

            return mgr._cycle_slots_with_sensor_check(
                current_tool_index,
                attempted_tool_index,
                retract_length,
                rdm_retract_speed,  # Use feed_speed instead of retract_speed
                retract_speed_mmmin,
                full_unload_length,
                sensor_name=SENSOR_RDM,
                use_extruder=False,
                sensor_to_parking_length=parkposition_to_rdm_length
            )

        # Should never reach here
        mgr.gcode.respond_info("ACE: Unexpected sensor state in cycling")
        return False

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
        sensor_to_parking_length=None
    ):
        """
        Unified slot cycling with sensor monitoring.

        Args:
            current_tool_index: Current tool (-1 if unknown)
            attempted_tool_index: Target tool for direct attempt (-1 for full cycle)
            retract_length: Test retraction length (mm)
            retract_speed: ACE retraction speed (mm/s)
            retract_speed_mmmin: Extruder retraction speed (mm/min)
            full_unload_length: Total length to max. unload
            sensor_name: SENSOR_TOOLHEAD or SENSOR_RDM
            use_extruder: If True, use coordinated extruder+ACE retractions
                        If False, use ACE-only with sensor monitoring
            sensor_to_parking_length: Distance from sensor to parking position (for RDM mode)

        Returns:
            bool: True if tool identified and unloaded successfully
        """
        mgr = self.manager

        # Build slot list (prioritize current_tool if different from attempted)
        slots_to_try = []
        if current_tool_index >= 0 and current_tool_index != attempted_tool_index:
            start_instance_num = get_instance_from_tool(current_tool_index)
            start_slot = get_local_slot(current_tool_index, start_instance_num)
            if start_instance_num >= 0 and 0 <= start_slot < mgr.instances[start_instance_num].SLOT_COUNT:
                start_instance = mgr.instances[start_instance_num]
                start_slot_status = start_instance.inventory[start_slot].get("status", "empty")
                if start_slot_status == "empty":
                    mgr.gcode.respond_info(
                        f"ACE[{start_instance_num}]: Skipping prioritized current tool "
                        f"slot {start_slot} (T{current_tool_index}) - empty"
                    )
                else:
                    slots_to_try.append((start_instance_num, start_slot, current_tool_index))

        # Add all other non-empty slots
        for instance_num, instance in enumerate(mgr.instances):
            for slot in range(instance.SLOT_COUNT):
                tool_num = instance.tool_offset + slot
                if (instance_num, slot, tool_num) in slots_to_try:
                    continue
                slot_status = instance.inventory[slot].get("status", "empty")
                if slot_status == "empty":
                    mgr.gcode.respond_info(
                        f"ACE[{instance_num}]: Skipping slot {slot} (T{tool_num}) - empty"
                    )
                    continue
                slots_to_try.append((instance_num, slot, tool_num))

        # Cycle and test each slot
        identified_tool = None

        for instance_num, slot, tool_num in slots_to_try:
            mgr.gcode.respond_info(
                f"ACE[{instance_num}]: Testing slot {slot} (T{tool_num}) via {sensor_name}"
            )

            instance = mgr.instances[instance_num]

            try:
                if use_extruder:
                    # CASE 2: Coordinated extruder+ACE retraction
                    mgr.execute_coordinated_retraction(
                        retract_length, retract_speed, retract_speed_mmmin, tool_num
                    )

                    # Wait for motion to settle
                    settle_time = max(0.2, min((retract_length / retract_speed) * 0.1, 1.0))
                    mgr.reactor.pause(mgr.reactor.monotonic() + settle_time)

                    # Check sensor with multiple readings for stability
                    sensor_readings = []
                    for i in range(3):
                        sensor_readings.append(mgr.get_instant_switch_state(sensor_name))
                        if i < 2:
                            mgr.reactor.pause(mgr.reactor.monotonic() + 0.1)

                    sensor_state = sensor_readings[-1]
                    mgr.gcode.respond_info(
                        f"ACE[{instance_num}]: Sensor {sensor_name} after retraction: "
                        f"readings={sensor_readings}, final={'TRIGGERED' if sensor_state else 'CLEAR'}"
                    )

                    if not sensor_state:
                        mgr.gcode.respond_info(
                            f"ACE[{instance_num}]: ✓ Sensor cleared! T{tool_num} identified"
                        )
                        identified_tool = (instance_num, slot, tool_num)
                        break

                else:
                    # CASE 3: ACE-only retraction with sensor monitoring
                    # Uses early_stop_callback inside _retract() so the sensor
                    # is polled DURING retraction (not after) — same approach
                    # as rmd_triggered_unload_slot from PR #11.

                    # Disable feed assist BEFORE retraction — it pushes forward
                    # which fights the retraction.
                    if instance._feed_assist_index == slot:
                        mgr.gcode.respond_info(
                            f"ACE[{instance_num}]: Disabling feed assist on slot {slot} before retraction"
                        )
                        instance._disable_feed_assist(slot)

                    overshoot_length = instance.rdm_overshoot_length

                    # Limit retraction length for wrong-slot protection:
                    # if sensor_to_parking_length is known, the correct slot's
                    # filament must clear within (full - parking) mm.  Don't
                    # retract more than necessary to avoid pulling a wrong
                    # slot's filament completely out of the ACE unit.
                    if sensor_to_parking_length and sensor_to_parking_length < full_unload_length:
                        test_length = full_unload_length - sensor_to_parking_length + overshoot_length
                    else:
                        test_length = full_unload_length

                    # Shared state for the early_stop_callback
                    monitor_state = {
                        "cleared": False,
                        "start_time": time.time(),
                        "last_log": 0,
                    }

                    def make_sensor_callback(inst_num, sname, overshoot_len, overshoot_spd, mstate):
                        """Factory to capture loop variables in closure."""
                        def sensor_early_stop_check():
                            elapsed = time.time() - mstate["start_time"]

                            sensor_has_filament = mgr.get_instant_switch_state(sname)

                            # Log every 2 seconds
                            if elapsed - mstate["last_log"] >= 2.0:
                                state_str = "TRIGGERED" if sensor_has_filament else "CLEAR"
                                mgr.gcode.respond_info(
                                    f"ACE[{inst_num}]: [{elapsed:.1f}s] {sname}={state_str}"
                                )
                                mstate["last_log"] = elapsed

                            if not sensor_has_filament and not mstate["cleared"]:
                                mstate["cleared"] = True

                                mgr.gcode.respond_info(
                                    f"ACE[{inst_num}]: {sname} cleared after {elapsed:.1f}s — "
                                    f"applying {overshoot_len}mm overshoot"
                                )

                                overshoot_time = overshoot_len / overshoot_spd
                                if overshoot_time > 0:
                                    mgr.reactor.pause(
                                        mgr.reactor.monotonic() + overshoot_time
                                    )
                                return f"{sname} clear at {elapsed:.1f}s"

                            return None
                        return sensor_early_stop_check

                    early_stop_cb = make_sensor_callback(
                        instance_num, sensor_name, overshoot_length,
                        retract_speed, monitor_state
                    )

                    mgr.gcode.respond_info(
                        f"ACE[{instance_num}]: Cycling test — slot {slot} (T{tool_num}), "
                        f"max {test_length:.0f}mm @ {retract_speed}mm/s, "
                        f"overshoot {overshoot_length}mm"
                    )

                    instance.wait_ready()
                    try:
                        instance._retract(
                            slot, length=test_length, speed=retract_speed,
                            early_stop_callback=early_stop_cb,
                        )
                    except Exception as e:
                        instance._stop_retract(slot)
                        mgr.gcode.respond_info(
                            f"ACE[{instance_num}]: Retract error on slot {slot}: {e}"
                        )

                    if monitor_state["cleared"]:
                        elapsed = time.time() - monitor_state["start_time"]
                        mgr.gcode.respond_info(
                            f"ACE[{instance_num}]: ✓ T{tool_num} identified via "
                            f"{sensor_name} monitoring in {elapsed:.1f}s"
                        )
                        identified_tool = (instance_num, slot, tool_num)
                    else:
                        elapsed = time.time() - monitor_state["start_time"]
                        mgr.gcode.respond_info(
                            f"ACE[{instance_num}]: {sensor_name} not cleared by "
                            f"slot {slot} after {elapsed:.1f}s — wrong slot"
                        )

                    if identified_tool is not None:
                        break

            except Exception as e:
                mgr.gcode.respond_info(
                    f"ACE[{instance_num}]: Error testing slot {slot} via {sensor_name}: {e}"
                )
                continue

        if identified_tool is None:
            emit_error(mgr.gcode, f"ACE: Failed to identify loaded tool via {sensor_name}")
            return False

        # Complete unload if using extruder mode (CASE 2)
        if use_extruder:
            instance_num, slot, tool_num = identified_tool
            remaining_length = full_unload_length - retract_length
            instance = mgr.instances[instance_num]

            try:
                mgr.gcode.respond_info(
                    f"ACE[{instance_num}]: Completing unload of T{tool_num} "
                    f"(remaining: {remaining_length}mm)"
                )
                instance._smart_unload_slot(slot, length=remaining_length)
            except Exception as e:
                emit_error(mgr.gcode, f"ACE[{instance_num}]: Error during full unload: {e}")
                return False

        # Verify path is clear
        if mgr.is_filament_path_free():
            mgr.state.set(
                "ace_filament_pos", FILAMENT_STATE_BOWDEN
            )
            mgr.gcode.respond_info(
                f"ACE: Tool {identified_tool[2]} identified and unloaded successfully"
            )
            return True
        else:
            mgr.gcode.respond_info("ACE: Path still blocked after unload")
            return False
