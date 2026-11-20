from .config import (
    SENSOR_TOOLHEAD,
    SENSOR_RDM,
    FILAMENT_STATE_SPLITTER,
    FILAMENT_STATE_BOWDEN,
    FILAMENT_STATE_TOOLHEAD,
    get_instance_from_tool,
    get_local_slot,
)
from .reporting import emit_error


class ToolchangeSpoolHelper:
    """Handles smart_load, full_unload_slot, and spool readiness waiting."""

    def __init__(self, manager):
        self.manager = manager

    @staticmethod
    def _slot_info_status(instance, local_slot):
        """Safely read hardware slot status from ``instance._info['slots']``."""
        slots = instance._info.get("slots") if isinstance(instance._info, dict) else None
        if isinstance(slots, list) and 0 <= local_slot < len(slots) and isinstance(slots[local_slot], dict):
            return slots[local_slot].get("status", "empty")
        return "empty"

    def smart_load(self):
        """
        Load all non-empty slots to verification sensor.

        If RDM sensor available: feeds to RDM sensor (shorter distance)
        If RDM unavailable: feeds to toolhead sensor (original behavior)

        For each ACE instance and each non-empty slot:
        1. Feed filament to verification sensor (RDM if available, else toolhead)
        2. Verify sensor triggered
        3. Retract to park position
        4. Verify path is clear

        Result: All filament parked at bowden position, ready for tool selection

        Returns:
            bool: True if all slots loaded successfully, False otherwise
        """
        mgr = self.manager
        if not mgr.is_filament_path_free():
            emit_error(mgr.gcode, "ACE: Cannot start smart_load - filament path is blocked")
            return False

        # Determine which sensor to use for verification
        use_rdm = mgr.has_rdm_sensor()
        verification_sensor = SENSOR_RDM if use_rdm else SENSOR_TOOLHEAD
        sensor_name = "RDM" if use_rdm else "toolhead"

        mgr.gcode.respond_info(
            f"ACE: Smart load using {sensor_name} sensor for verification"
        )

        success_count = 0
        total_slots = 0

        for instance in mgr.instances:
            # Use toolchange_load_length for feeding (sensor will stop it when reached)
            feed_length = instance.toolchange_load_length

            # For each non-empty slot
            for slot in range(instance.SLOT_COUNT):
                # Check if slot has filament
                slot_status = instance.inventory[slot].get("status", "empty")
                if slot_status == "empty":
                    continue  # Skip empty slots

                total_slots += 1
                tool_num = instance.tool_offset + slot

                mgr.gcode.respond_info(f"ACE[{instance.instance_num}]: " f"Loading slot {slot} (T{tool_num})")

                try:
                    # Step 1: Feed to verification sensor
                    mgr.gcode.respond_info(f"ACE: Feeding slot {slot} to {sensor_name} sensor")
                    instance._feed_filament_to_verification_sensor(
                        slot,
                        verification_sensor,
                        feed_length
                    )

                    # Check if verify sensor triggered or not
                    if not mgr.get_switch_state(verification_sensor):
                        # Failure case
                        mgr.gcode.respond_info(
                            f"ACE[{instance.instance_num}]: "
                            f"{sensor_name} sensor not triggered after "
                            f"feeding slot {slot}"
                        )
                        instance._stop_feed(slot)

                        # We dont know how far the filament has moved, try retract to park directly to avoid jams
                        if use_rdm:
                            park_distance = instance.parkposition_to_rdm_length
                        else:
                            park_distance = instance.parkposition_to_toolhead_length

                        mgr.gcode.respond_info(
                            f"ACE: Safety retracting slot {slot} to park position to avoid jams"
                            f"({park_distance}mm)"
                        )
                        instance._retract(slot, length=park_distance, speed=instance.retract_speed)
                        continue

                    # Happy path
                    mgr.gcode.respond_info(f"ACE: {sensor_name} sensor triggered for slot {slot}")

                    if use_rdm:
                        mgr.state.set("ace_filament_pos", FILAMENT_STATE_SPLITTER)
                    else:
                        mgr.state.set("ace_filament_pos", FILAMENT_STATE_TOOLHEAD)

                    # Step 2: Retract to park position
                    # Use appropriate park distance based on sensor
                    if use_rdm:
                        park_distance = instance.parkposition_to_rdm_length
                    else:
                        park_distance = instance.parkposition_to_toolhead_length

                    mgr.gcode.respond_info(
                        f"ACE: Retracting slot {slot} to park "
                        f"({park_distance}mm from {sensor_name})"
                    )
                    instance._retract(slot, length=park_distance, speed=instance.retract_speed)

                    # Step 3: Verify path is still clear
                    if not mgr.is_filament_path_free():
                        mgr.gcode.respond_info(
                            f"ACE[{instance.instance_num}]: " f"Path not clear after parking slot {slot}"
                        )
                        continue

                    mgr.gcode.respond_info(f"ACE[{instance.instance_num}]: " f"Slot {slot} loaded successfully")
                    success_count += 1

                except Exception as e:
                    emit_error(mgr.gcode, f"ACE[{instance.instance_num}]: Error loading slot {slot}: {e}")

        if success_count > 0:
            mgr.state.set("ace_filament_pos", FILAMENT_STATE_BOWDEN)
            mgr.state.set("ace_current_index", -1)
            mgr.gcode.respond_info(f"ACE: Smart load complete - {success_count}/{total_slots} " f"slots loaded")
            return success_count == total_slots
        else:
            mgr.gcode.respond_info("ACE: Smart load - no slots loaded")
            return False

    def check_and_wait_for_spool_ready(self, target_tool, timeout_s=300, check_interval_s=1.0, stable_ready_s=3.0):
        """
        Check if the spool for target_tool is ready before feeding.

        Waits until the status is continuously 'ready' for at least stable_ready_s seconds,
        but only if the spool was not ready when the method was entered. If it was already
        ready initially, returns True immediately without waiting.

        Args:
            target_tool: Global tool index to check
            timeout_s: Maximum time to wait (default 300s / 5min)
            check_interval_s: How often to re-check status (default 1s)
            stable_ready_s: Time the status must stay 'ready' continuously (default 3s)

        Returns:
            bool: True if spool is ready (stably or initially), False if timeout without stability
        """
        mgr = self.manager
        # Find the ACE instance managing this tool
        instance_num = get_instance_from_tool(target_tool)
        if instance_num < 0 or instance_num >= len(mgr.instances):
            mgr.gcode.respond_info(f"ACE: Tool {target_tool} not managed by any ACE instance")
            return False

        instance = mgr.instances[instance_num]
        local_slot = get_local_slot(target_tool, instance_num)

        # Initial check
        instance.wait_ready()
        inventory_status = instance.inventory[local_slot].get("status", "empty")
        ace_status = mgr._slot_info_status(instance, local_slot)
        was_initially_ready = inventory_status == "ready" and ace_status == "ready"

        if was_initially_ready:
            return True
        else:
            mgr.gcode.respond_info(
                f"****************************************\n"
                f"* ACE[{instance_num}]: Spool for tool {target_tool} (slot {local_slot}) is not ready *\n"
                f"* (inventory: {inventory_status}, ACE: {ace_status}) *\n"
                f"* Please reload spool on ACE {instance_num}, index {local_slot} *\n"
                f"****************************************"
            )

            # Show Mainsail dialog prompt
            mgr._show_spool_not_ready_prompt(target_tool, instance_num, local_slot, inventory_status, ace_status,
                                             timeout_s=timeout_s)

        start_time = mgr.reactor.monotonic()
        ready_start_time = None  # Time when it first became ready
        was_printing = mgr._print_state() in ("printing", "paused")

        while True:
            # A shutdown/E-stop or a cancelled print must not leave this loop
            # sleeping for the remaining minutes of the 300 s timeout.
            if mgr._shutdown_in_progress or mgr._printer_is_shutdown():
                emit_error(mgr.gcode, f"ACE[{instance_num}]: Spool wait aborted - printer shutdown")
                return False
            if was_printing and mgr._print_state() not in ("printing", "paused"):
                mgr.gcode.respond_info(f"ACE[{instance_num}]: Spool wait aborted - print ended")
                try:
                    mgr.gcode.run_script_from_command('RESPOND TYPE=command MSG="action:prompt_end"')
                except Exception:
                    pass
                return False

            elapsed = mgr.reactor.monotonic() - start_time
            if elapsed > timeout_s:
                mgr.gcode.respond_info(
                    f"ACE[{instance_num}]: Timeout waiting for stable ready state on ACE {instance_num}, "
                    f"slot {local_slot} (waited {elapsed:.1f}s). Aborting spool check."
                )
                # Close the prompt on timeout
                mgr.gcode.run_script_from_command('RESPOND TYPE=command MSG="action:prompt_end"')
                return False

            # Re-check status
            inventory_status = instance.inventory[local_slot].get("status", "empty")
            ace_status = mgr._slot_info_status(instance, local_slot)
            is_ready = inventory_status == "ready" and ace_status == "ready"

            if is_ready:
                if ready_start_time is None:
                    # First time becoming ready - start the stability timer
                    ready_start_time = mgr.reactor.monotonic()
                    mgr.gcode.respond_info(
                        f"ACE[{instance_num}]: First time ready detected, waiting {stable_ready_s}s for stability..."
                    )
                else:
                    # Check if stable for required duration
                    time_ready = mgr.reactor.monotonic() - ready_start_time
                    if time_ready >= stable_ready_s:
                        mgr.gcode.respond_info(
                            f"ACE[{instance_num}]: Spool for tool {target_tool} (slot {local_slot}) "
                            f"stable and ready (waited {time_ready:.1f}s)"
                        )
                        # Close the prompt on success
                        mgr.gcode.run_script_from_command('RESPOND TYPE=command MSG="action:prompt_end"')
                        return True
            else:
                # Status changed back to not ready - reset timer
                if ready_start_time is not None:
                    mgr.gcode.respond_info(
                        f"ACE[{instance_num}]: Status changed back to not ready "
                        f"(inventory: {inventory_status}, ACE: {ace_status}), resetting stability timer"
                    )
                ready_start_time = None

            # Wait before next check
            mgr.reactor.pause(mgr.reactor.monotonic() + check_interval_s)

    def _show_spool_not_ready_prompt(self, tool_index, instance_num, local_slot, inventory_status, ace_status,
                                     timeout_s=None):
        """
        Show Mainsail prompt when spool is not ready.

        Args:
            tool_index: Global tool index
            instance_num: ACE instance number
            local_slot: Local slot number on instance
            inventory_status: Current inventory status
            ace_status: Current ACE hardware status
        """
        mgr = self.manager
        mgr.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_begin Spool Not Ready"'
        )

        prompt_text = (
            f"Spool not ready! ACE {instance_num}, Slot {local_slot + 1} (Tool T{tool_index}) - "
            f"Status: inventory={inventory_status}, ACE={ace_status} - "
            f"Please reload the spool on ACE {instance_num}, slot {local_slot + 1}. "
            + (f"Waiting {timeout_s:.0f}s for the spool, then the tool change stops."
               if timeout_s is not None and timeout_s < 60 else
               "The system will automatically continue when the spool is detected and stable.")
        )

        mgr.gcode.run_script_from_command(
            f'RESPOND TYPE=command MSG="action:prompt_text {prompt_text}"'
        )

        # Add a cancel button for emergency abort
        mgr.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_footer_button Cancel Print|CANCEL_PRINT|error"'
        )

        mgr.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_show"'
        )

    def full_unload_slot(self, tool_index):
        """
        Fully unload a slot from the ACE.

        Two modes depending on whether the tool is currently loaded
        in the toolhead:

        **ACTIVE TOOL** (tool_index == ace_current_index):
        Prepares the toolhead (heating, pre_cut_retract, CUT_TIP if
        available), retracts the extruder, then ACE-retracts (stops
        early when slot sensor clears).
        On success, resets ace_current_index and filament_pos.

        **NON-ACTIVE TOOL** (different tool loaded, or no tool loaded):
        Simple ACE-only retract (no heating/cutting needed).

        Args:
            tool_index: Global tool index to unload

        Returns:
            bool: True if unload successful
        """
        mgr = self.manager
        instance_num = get_instance_from_tool(tool_index)
        if instance_num < 0:
            emit_error(mgr.gcode, f"ACE: Tool {tool_index} not managed by any ACE instance")
            return False

        instance = mgr.instances[instance_num]
        local_slot = get_local_slot(tool_index, instance_num)

        # Check BOTH inventory AND hardware status
        inventory_status = instance.inventory[local_slot].get("status", "empty")
        hw_status = mgr._slot_info_status(instance, local_slot)

        if inventory_status == "empty" and hw_status == "empty":
            mgr.gcode.respond_info(
                f"ACE[{instance_num}]: Slot {local_slot} already empty, skipping full unload"
            )
            return True

        if instance._feed_assist_index == local_slot:
            mgr.gcode.respond_info(
                f"ACE[{instance_num}]: Disabling feed assist on slot {local_slot}"
            )
            instance._disable_feed_assist(local_slot)

        # Determine if this tool is currently loaded in the toolhead
        current_tool_index = mgr.state.get("ace_current_index", -1)
        is_active_tool = (tool_index == current_tool_index)

        if is_active_tool:
            # --- ACTIVE TOOL: Filament is in the toolhead ---
            # 1. Prepare toolhead (heat, pre_cut_retract, CUT_TIP)
            # 2. Extruder retract to clear the toolhead
            # 3. ACE retract (stops early when slot sensor clears)
            mgr.gcode.respond_info(
                f"ACE[{instance_num}]: Full unload of ACTIVE tool T{tool_index}"
            )

            try:
                mgr.prepare_toolhead_for_filament_retraction(tool_index=tool_index)

                # Extruder retract to clear the toolhead
                mgr._extruder_move(
                    -abs(mgr.toolhead_retraction_length),
                    mgr.toolhead_retraction_speed,
                    wait_for_move_end=True
                )

                # ACE retract — _retract() polls slot sensor every ~200ms
                # via check_slot_empty() and stops as soon as slot is empty
                if not instance.protocol.feed_assist_causes_busy():
                    instance.wait_ready()
                instance._retract(
                    local_slot,
                    length=instance.total_max_feeding_length,
                    speed=instance.retract_speed,
                )

                if instance._last_retract_early_stopped:
                    mgr.state.set("ace_current_index", -1)
                    mgr.state.set("ace_filament_pos", FILAMENT_STATE_BOWDEN)
                    mgr.gcode.respond_info(
                        f"ACE[{instance_num}]: ✓ Active tool T{tool_index} fully unloaded"
                    )
                    return True
                else:
                    emit_error(
                        mgr.gcode,
                        f"ACE[{instance_num}]: Active tool T{tool_index} - "
                        f"full retract completed but slot sensor never reported empty"
                    )
                    return False

            except Exception as e:
                emit_error(
                    mgr.gcode,
                    f"ACE[{instance_num}]: Full unload of active tool failed: {e}"
                )
                return False
            finally:
                mgr.gcode.run_script_from_command("M104 S0")
                mgr.gcode.run_script_from_command("G92 E0")
                mgr.gcode.run_script_from_command("G90")
        else:
            # --- NON-ACTIVE TOOL ---
            # Filament is only in ACE/tube, not in toolhead.
            # Simple ACE retract, no heating or cutting needed.
            total_length = instance.total_max_feeding_length
            retract_speed = instance.retract_speed

            mgr.gcode.respond_info(
                f"ACE[{instance_num}]: Full unload slot {local_slot} (non-active tool):\n"
                f"  Retracting: {total_length}mm at {retract_speed}mm/s\n"
                f"  Expected time: {(total_length / retract_speed):.1f}s"
            )

            try:
                instance.wait_ready()
                instance._retract(local_slot, length=total_length, speed=retract_speed)

                if instance._last_retract_early_stopped:
                    mgr.gcode.respond_info(
                        f"ACE[{instance_num}]: ✓ Full unload slot {local_slot} successful"
                    )
                    return True
                else:
                    emit_error(
                        mgr.gcode,
                        f"ACE[{instance_num}]: Full unload slot {local_slot} - "
                        f"full retract completed but slot sensor never reported empty"
                    )
                    return False

            except Exception as e:
                emit_error(
                    mgr.gcode,
                    f"ACE[{instance_num}]: Full unload failed: {e}"
                )
                return False
