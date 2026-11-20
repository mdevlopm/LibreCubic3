import logging

from .config import (
    SENSOR_TOOLHEAD,
    SENSOR_RDM,
    FILAMENT_STATE_SPLITTER,
    FILAMENT_STATE_BOWDEN,
    FILAMENT_STATE_NOZZLE,
    FILAMENT_STATE_TOOLHEAD,
    get_instance_from_tool,
    get_local_slot,
    get_ace_instance_and_slot_for_tool,
)
from .reporting import emit_error

# How long a tool change waits for an empty or settling slot. The wait holds Klipper's
# G-code queue (Stop/Pause from the screen queue behind it), so it stays short; after
# it the print pauses with a Retry dialog instead of blocking for minutes.
SPOOL_READY_WAIT_S = 15.0

def toolchange_in_progress_guard(method):
    """
    Decorator: Increment/decrement toolchange depth counter.
    Supports nested toolchange operations - flag stays True until all nested calls complete.
    """
    def wrapper(self, *args, **kwargs):
        target = getattr(self, "manager", self)
        target._toolchange_depth = getattr(target, '_toolchange_depth', 0) + 1
        target.toolchange_in_progress = True
        try:
            return method(self, *args, **kwargs)
        finally:
            target._toolchange_depth -= 1
            if target._toolchange_depth == 0:
                target.toolchange_in_progress = False
    return wrapper


class ToolchangeOrchestrator:
    """Orchestrates toolchange sequences, toolhead preparation, and pre-toolchange state recovery."""

    def __init__(self, manager):
        self.manager = manager

    def _turn_off_heater_if_idle(self):
        """
        Turn off extruder heater if the printer is not currently printing.

        Called after successful unload operations to avoid leaving the
        heater on indefinitely when unloading outside of a print job.
        During printing, the heater must stay on for the next toolchange.
        """
        mgr = self.manager
        try:
            print_stats = mgr.printer.lookup_object("print_stats", None)
            if print_stats:
                stats = print_stats.get_status(mgr.reactor.monotonic())
                state = (stats.get("state") or "").lower()
                if state in ("printing", "paused"):
                    mgr.gcode.respond_info(
                        "ACE: Printer is printing/paused — keeping heater on"
                    )
                    return
            mgr.gcode.respond_info("ACE: Not printing — turning off extruder heater")
            mgr.gcode.run_script_from_command("M104 S0")
        except Exception as e:
            mgr.gcode.respond_info(f"ACE: Warning — could not turn off heater: {e}")

    def prepare_toolhead_for_filament_retraction(self, tool_index=-1):
        """
        Prepare toolhead (extruder/nozzle) for filament retraction.

        If filament is present at toolhead (sensor triggered):
        1. Call _ACE_PREPARE_FOR_RETRACTION macro (macro handles heating)

        Args:
            tool_index: Tool to prepare for retraction (-1 = unknown tool)

        Returns:
            bool: True if filament was present and handling succeeded,
                  False if no filament present or operation completed
        """
        mgr = self.manager
        if not mgr.get_switch_state(SENSOR_TOOLHEAD):
            mgr.gcode.respond_info("ACE: No filament at toolhead, skipping prep")
            return False

        target_temp = 0
        if tool_index >= 0:
            target_ace, target_slot = get_ace_instance_and_slot_for_tool(tool_index)
            if target_ace is not None:
                inv_temp = target_ace.inventory[target_slot].get("temp", 0)
                if inv_temp > 0:
                    target_temp = inv_temp
                    mgr.gcode.respond_info(
                        f"ACE: Using inventory temp for T{tool_index}: {target_temp}°C"
                    )

        mgr.gcode.respond_info(
            f"ACE: Filament at toolhead, preparing for retraction "
            f"(target_temp={target_temp}°C) pre_cut_retract={mgr.pre_cut_retract_length}mm"
        )

        try:
            # Call macro to handle heating + CUT_TIP
            mgr.gcode.run_script_from_command(
                f"_ACE_PREPARE_FOR_RETRACTION TARGET_TEMP={target_temp} PRE_CUT_RETRACT={mgr.pre_cut_retract_length}"
            )
            return True

        except Exception as e:
            emit_error(mgr.gcode, f"ACE: Error preparing toolhead for retraction: {e}")
            return False

    def _ensure_hot_for_recovery_unload(self, current_tool, target_temp):
        """Heat the extruder before a plausibility-mismatch recovery unload.

        The plausibility-mismatch unloads in :meth:`perform_tool_change` run
        *before* the PRE macro has heated the nozzle.  When the toolhead sensor
        is triggered, :meth:`smart_unload` drives extruder moves (pre-cut
        retract, coordinated retract) which Klipper rejects with
        "Extrude below minimum temp" on a cold nozzle.

        Picks the best available temperature — the stuck tool's material temp,
        then the incoming target temp, then ``min_extrude_temp`` as a last
        resort — and waits for it via ``M109`` when the nozzle is too cold.
        Independent of whether an RDM sensor is present.

        Args:
            current_tool: Tool currently recorded as loaded (-1 if unknown).
            target_temp:  Target tool temperature already resolved by the caller
                          (0 when unavailable, e.g. unload-only changes).
        """
        mgr = self.manager
        extruder = mgr.printer.lookup_object("extruder", None)
        if extruder is None:
            return
        heater = extruder.get_heater()
        cur_temp = heater.get_temp(mgr.reactor.monotonic())[0]
        min_temp = heater.min_extrude_temp
        if cur_temp >= min_temp:
            return  # already hot enough to move the extruder

        # The filament physically stuck in the path belongs to the current
        # tool, so prefer its material temperature.
        heat_temp = 0
        if current_tool >= 0:
            cur_ace, cur_slot = get_ace_instance_and_slot_for_tool(current_tool)
            if cur_ace is not None:
                heat_temp = cur_ace.inventory[cur_slot].get("temp", 0) or 0
        if heat_temp <= 0:
            heat_temp = target_temp
        if heat_temp <= 0:
            # Last resort: clear Klipper's cold-extrude guard so recovery can
            # proceed at all — better than crashing the whole toolchange.
            heat_temp = min_temp

        mgr.gcode.respond_info(
            f"ACE: Extruder too cold ({cur_temp:.0f}°C < {min_temp:.0f}°C) for "
            f"recovery unload — heating to {heat_temp:.0f}°C before clearing path"
        )
        mgr.gcode.run_script_from_command(f"M109 S{heat_temp:.0f}")

    def _wait_toolhead_move_finished(self):
        toolhead = self.manager.printer.lookup_object('toolhead')
        toolhead.wait_moves()

    def _extruder_move(self, length, speed, wait_for_move_end=False):
        """Move extruder (relative) via motion planner, synchronously."""
        mgr = self.manager
        if length == 0:
            mgr.gcode.respond_info(
                f"ACE[{getattr(mgr, 'instance_num', 0)}]: _extruder_move() -> Skipping zero-length move"
            )
            return

        if length > 0 and not getattr(mgr, "toolhead_extrusion_allowed", False):
            if not getattr(mgr, "_extrusion_suppressed_logged", False):
                mgr._extrusion_suppressed_logged = True
                mgr.gcode.respond_info(
                    "ACE: Toolhead extrusion is disabled (toolhead_extrusion_allowed=False); skipping extruder move"
                )
            return

        toolhead = mgr.printer.lookup_object('toolhead')
        cur_pos = list(toolhead.get_position())  # [X, Y, Z, E]

        new_pos = cur_pos[:]
        new_pos[3] += length

        toolhead.move(new_pos, speed)
        if wait_for_move_end:
            toolhead.wait_moves()

    @toolchange_in_progress_guard
    def perform_tool_change(self, current_tool, target_tool, is_endless_spool=False):
        """
        Execute complete tool change sequence.

        Args:
            current_tool: Current tool (-1 if none loaded)
            target_tool: Target tool (-1 to unload only)
            is_endless_spool: If True, skip unload of current tool (already empty)
        """
        mgr = self.manager
        status = None
        mgr._pre_toolchange_done = False
        mgr._pre_toolchange_fan_pwm = None
        gcode_move = mgr.printer.lookup_object("gcode_move")

        toolhead_sensor = mgr.get_switch_state(SENSOR_TOOLHEAD)
        rdm_sensor = mgr.get_switch_state(SENSOR_RDM) if mgr.has_rdm_sensor() else False
        filament_pos = mgr.state.get("ace_filament_pos", FILAMENT_STATE_BOWDEN)

        logging.info(
            f"ACE: Toolchange plausibility check - "
            f"Sensors: toolhead={toolhead_sensor}, rdm={'N/A (no RDM)' if not mgr.has_rdm_sensor() else rdm_sensor}, "
            f"State: filament_pos='{filament_pos}', current_tool=T{current_tool}"
        )

        # Resolve the target tool's temperature up-front.  The plausibility-
        # mismatch unloads below run before the PRE macro heats the nozzle and
        # need a temperature to fall back on when heating for a recovery unload.
        target_temp = 0
        if target_tool >= 0:
            target_ace, target_slot = get_ace_instance_and_slot_for_tool(target_tool)
            if target_ace is not None:
                inv_temp = target_ace.inventory[target_slot].get("temp", 0)
                if inv_temp > 0:
                    target_temp = inv_temp
                    mgr.gcode.respond_info(
                        f"ACE: Target tool T{target_tool} inventory temp: {target_temp}°C"
                    )

        if (toolhead_sensor or rdm_sensor) and (filament_pos == FILAMENT_STATE_BOWDEN):
            mgr.gcode.respond_info(
                f"ACE: PLAUSIBILITY MISMATCH - Sensors show filament present "
                f"but state='{filament_pos}'. Performing smart_unload to clear path. May help or not..."
            )

            # smart_unload may drive extruder moves: prepare_toolhead re-reads
            # the toolhead sensor live (it can differ from the read above), and
            # stale moves queued by a failed load can flush here.  Heat the
            # nozzle first regardless of the current sensor read.  This runs
            # before the PRE macro; the guard returns immediately when the
            # nozzle is already above min_extrude_temp.
            mgr._ensure_hot_for_recovery_unload(current_tool, target_temp)

            success = mgr.smart_unload(tool_index=current_tool if current_tool >= 0 else -1, keep_heater=True)
            if not success:
                raise Exception("Failed to clear filament path - plausibility check failed")
            # Reset extruder state after emergency unload — the cycling path
            # (Case 2/3) has no G92 E0 cleanup unlike the normal unload path.
            # Stale E position or queued moves can cause "Extrude below
            # minimum temp" when M109 flushes the move queue.
            mgr.gcode.run_script_from_command("G92 E0")
            mgr.gcode.run_script_from_command("M400")
            current_tool = -1

        if not toolhead_sensor and rdm_sensor and (filament_pos == FILAMENT_STATE_SPLITTER):
            mgr.gcode.respond_info(
                f"ACE: WARNING: Toolhead clear, but filament detected at RDM, "
                f"state='{filament_pos}'. Performing smart_unload to clear path."
            )

            success = mgr.smart_unload(tool_index=current_tool if current_tool >= 0 else -1, keep_heater=True)
            if not success:
                raise Exception("Failed to clear RMS filament path")
            mgr.gcode.run_script_from_command("G92 E0")
            mgr.gcode.run_script_from_command("M400")
            current_tool = -1

        # ===== HANDLE TOOL RESELECTION =====
        if current_tool == target_tool:
            filament_pos = mgr.state.get("ace_filament_pos", FILAMENT_STATE_BOWDEN)

            sensor_has_filament = mgr.get_switch_state(SENSOR_TOOLHEAD)

            if mgr.has_rdm_sensor():
                rdm_has_filament = mgr.get_switch_state(SENSOR_RDM)

                # ===== DETECT INVALID STATE: Nozzle has filament but RDM is empty =====
                if filament_pos == FILAMENT_STATE_NOZZLE and sensor_has_filament and not rdm_has_filament:
                    mgr.gcode.respond_info(
                        f"ACE: ⚠ INVALID STATE DETECTED - Tool {target_tool} marked as loaded\n"
                        f"  State: filament_pos='nozzle'\n"
                        f"  Toolhead sensor: {'TRIGGERED' if sensor_has_filament else 'clear'}\n"
                        f"  RDM sensor: {'TRIGGERED' if rdm_has_filament else 'CLEAR'}\n"
                        f"  PROBLEM: Filament stuck at nozzle but path is broken (no filament in RDM)\n"
                        f"  This indicates incomplete unload or broken filament in path.\n"
                        f"  SOLUTION: Manually unload/retract stuck filament, then retry toolchange."
                    )

                    raise Exception(
                        f"Invalid filament state for T{target_tool}: "
                        f"Filament stuck at nozzle but RDM sensor is empty. "
                        f"Cannot proceed - manual intervention required. "
                        f"Use ACE_CHANGE_TOOL TOOL=-1 to force unload, or manually clear the path."
                    )

            if filament_pos == FILAMENT_STATE_NOZZLE:
                if sensor_has_filament:
                    # State matches sensor - tool is truly loaded
                    # Ensure feed assist is active for this tool (may have been lost after ACE power cycle)
                    target_instance = get_instance_from_tool(target_tool)
                    target_local_slot = get_local_slot(target_tool, target_instance)
                    target_ace = mgr.instances[target_instance] if target_instance < len(mgr.instances) else None

                    if target_ace:
                        current_fa = target_ace._get_current_feed_assist_index()
                        if current_fa == target_local_slot:
                            # Feed assist already active on the correct slot - nothing to do.
                            # Do NOT call _enable_feed_assist here: on ACE2 the device is
                            # already 'busy' because feed assist is active, and _enable_feed_assist
                            # starts with wait_ready() which would deadlock.
                            mgr.gcode.respond_info(
                                f"ACE: Tool {target_tool} already loaded - "
                                f"feed assist already active on slot {target_local_slot}"
                            )
                        else:
                            # Feed assist lost (e.g. after ACE power cycle) - restore it.
                            mgr.gcode.respond_info(
                                f"ACE: Tool {target_tool} already loaded - "
                                f"re-enabling feed assist on slot {target_local_slot}"
                            )
                            target_ace._enable_feed_assist(target_local_slot)

                    return f"Tool {target_tool} (already loaded)"
                else:
                    # State says loaded but sensor is EMPTY - state is WRONG
                    mgr.gcode.respond_info(
                        "ACE: ✗ STATE MISMATCH - filament_pos='nozzle' but sensor is EMPTY! "
                        "Correcting state and proceeding with normal load."
                    )
                    if mgr.get_switch_state(SENSOR_RDM):
                        filament_pos = FILAMENT_STATE_SPLITTER
                        mgr.state.set("ace_filament_pos", filament_pos)
                    else:
                        filament_pos = FILAMENT_STATE_BOWDEN
                        mgr.state.set("ace_filament_pos", filament_pos)
                    mgr.gcode.respond_info(
                        f"ACE: filament_pos for Tool {target_tool} changed to "
                        f"assumed filament_pos='{filament_pos}'"
                    )
                    # Fall through to normal toolchange logic below

            # So, filament state is not NOZZLE if we reach this point, but we have
            # assumingly an active/current tool loaded. Try to find out the real state
            mgr.gcode.respond_info(
                f"ACE: Tool {target_tool} marked as current but "
                f"filament_pos='{filament_pos}', checking sensors..."
            )

            # Check toolhead sensor, if it shows filament we assume tool is loaded and persiststate was wrong
            if sensor_has_filament:
                mgr.gcode.respond_info(
                    "ACE: Toolhead sensor triggered - filament present. Correcting state to 'nozzle'"
                )
                mgr.state.set("ace_filament_pos", FILAMENT_STATE_NOZZLE)
                return f"Tool {target_tool} (state corrected)"
            else:
                # Again path check, if RDM sensor exists it will be used there as well
                # If either sensor shows filament, we assume tool is loaded
                if not mgr.is_filament_path_free():
                    mgr.gcode.respond_info(
                        f"ACE: WARNING - Tool {target_tool} marked as current but "
                        f"state is:'{filament_pos} and sensor report path is blocked. "
                        f"Attempting to clear path."
                    )
                    success = mgr.smart_unload(tool_index=-1, keep_heater=True)
                    if not success:
                        raise Exception(
                            f"Cannot proceed with tool {target_tool} - filament path is jammed. "
                            f"Manual intervention required."
                        )

                mgr.gcode.respond_info(
                    f"ACE: Tool {target_tool} path cleared, proceeding with normal load."
                )

        # Capture current fan speed before _ACE_PRE_TOOLCHANGE disables it
        fan = mgr.printer.lookup_object('fan', None)
        if fan is not None:
            try:
                cur_fan = fan.get_status(mgr.reactor.monotonic()).get('speed', 0.0)
                mgr._pre_toolchange_fan_pwm = int(cur_fan * 255)
            except Exception:
                mgr._pre_toolchange_fan_pwm = int(getattr(fan, 'speed', 0.0) * 255)
        else:
            mgr._pre_toolchange_fan_pwm = None

        mgr._pre_toolchange_done = True
        # ===== PRE-TOOLCHANGE (Macro handles heating) =====
        mgr.gcode.run_script_from_command(
            f"_ACE_PRE_TOOLCHANGE FROM={current_tool} TO={target_tool} TARGET_TEMP={target_temp}"
        )

        # ===== UNLOAD CURRENT TOOL =====
        if current_tool != -1 and not is_endless_spool:
            filament_pos = mgr.state.get("ace_filament_pos", FILAMENT_STATE_BOWDEN)
            mgr.gcode.respond_info(f"ACE: Current filament_pos before unload: {filament_pos}")
            if (filament_pos in [FILAMENT_STATE_NOZZLE, FILAMENT_STATE_SPLITTER]):
                # Trust sensors over persisted state.
                if mgr.is_filament_path_free():
                    # All sensors report clear - state is stale/wrong, correct it.
                    mgr.gcode.respond_info(
                        f"ACE: WARNING: State says '{filament_pos}' but sensors report path CLEAR - "
                        f"trusting sensors, correcting state to bowden, skipping unload."
                    )
                    mgr.state.set("ace_filament_pos", FILAMENT_STATE_BOWDEN)
                else:
                    # Sensors confirm filament is in the path - proceed with unload.
                    # Also fix state if toolhead sensor shows filament has advanced further.
                    if filament_pos == FILAMENT_STATE_SPLITTER and mgr.get_switch_state(SENSOR_TOOLHEAD):
                        mgr.gcode.respond_info(
                            f"ACE: State was '{FILAMENT_STATE_SPLITTER}' but toolhead sensor is "
                            f"TRIGGERED - correcting to '{FILAMENT_STATE_NOZZLE}' before unload."
                        )
                        filament_pos = FILAMENT_STATE_NOZZLE
                        mgr.state.set("ace_filament_pos", FILAMENT_STATE_NOZZLE)

                    mgr.gcode.respond_info(f"ACE: Tool {current_tool} marked as loaded, performing unload")
                    success = mgr.smart_unload(tool_index=current_tool, keep_heater=True)
                    if not success:
                        raise Exception(f"Failed to unload tool {current_tool}")
                    mgr.gcode.respond_info(f"ACE: Tool {current_tool} unloaded successfully")

            elif filament_pos == FILAMENT_STATE_BOWDEN:
                mgr.gcode.respond_info(
                    f"ACE: Tool {current_tool} not loaded (filament_pos='{filament_pos}'), skipping unload"
                )

            else:
                mgr.gcode.respond_info(f"ACE: Unknown filament_pos='{filament_pos}', checking sensors...")
                if mgr.get_switch_state(SENSOR_TOOLHEAD):
                    mgr.gcode.respond_info("ACE: Toolhead sensor triggered, performing unload")
                    success = mgr.smart_unload(tool_index=current_tool, keep_heater=True)
                    if not success:
                        raise Exception(f"Failed to unload tool {current_tool}")
                else:
                    mgr.gcode.respond_info("ACE: No filament at toolhead, correcting state to bowden (unloaded)")
                    mgr.state.set("ace_filament_pos", FILAMENT_STATE_BOWDEN)

        elif current_tool == -1:
            mgr.gcode.respond_info("ACE: No current tool loaded, skipping unload")
        elif is_endless_spool:
            mgr.gcode.respond_info(
                f"ACE: Endless spool mode - skipping unload of tool {current_tool} (already empty)"
            )
            mgr.state.set("ace_filament_pos", FILAMENT_STATE_BOWDEN)

        # ===== LOAD NEW TOOL =====
        if target_tool != -1:
            if not mgr.check_and_wait_for_spool_ready(target_tool, timeout_s=SPOOL_READY_WAIT_S):
                raise Exception(
                    f"Tool {target_tool} (slot {target_tool + 1}) has no filament ready. "
                    f"Load the spool, then Retry."
                )

            target_ace, target_slot = get_ace_instance_and_slot_for_tool(target_tool)

            if target_ace is None:
                raise Exception(f"Tool {target_tool} not managed by any ACE instance")

            # Safety: verify extruder is at operating temperature before
            # attempting to load.  The PRE macro should have heated via M109,
            # but after error-recovery paths (plausibility mismatch, jam
            # recovery) the nozzle may still be cold.
            #
            # Unlike the plausibility recovery guard (_ensure_hot_for_recovery_unload),
            # this guard fails fast by design: reaching here cold with no target
            # temperature means BOTH the PRE macro and the recovery guard failed
            # to heat — something is fundamentally wrong, so raising is safer than
            # guessing a temperature and loading blind.  Do not "unify" the two
            # guards: clearing a stuck path must degrade gracefully, loading must not.
            extruder = mgr.printer.lookup_object("extruder", None)
            if extruder:
                cur_temp = extruder.get_heater().get_temp(mgr.reactor.monotonic())[0]
                min_temp = extruder.get_heater().min_extrude_temp
                if cur_temp < min_temp:
                    mgr.gcode.respond_info(
                        f"ACE: Extruder too cold ({cur_temp:.0f}°C < {min_temp:.0f}°C) "
                        f"— waiting for temperature before load"
                    )
                    if target_temp > 0:
                        mgr.gcode.run_script_from_command(f"M109 S{target_temp}")
                    else:
                        raise Exception(
                            f"Extruder too cold ({cur_temp:.0f}°C) and no target "
                            f"temperature set — cannot load filament"
                        )

            mgr.gcode.respond_info(f"ACE[{target_ace.instance_num}]: Loading tool {target_tool}...")

            if not mgr.toolhead_extrusion_allowed:
                purged_amount = 0.0
                if not getattr(mgr, "_extrusion_suppressed_logged", False):
                    mgr._extrusion_suppressed_logged = True
                    mgr.gcode.respond_info(
                        "ACE: Toolhead extrusion is disabled (toolhead_extrusion_allowed=False); skipping toolhead purge/extrude"
                    )
                target_local_slot = target_tool - target_ace.tool_offset
                if not mgr.get_switch_state(SENSOR_TOOLHEAD):
                    target_ace._feed_to_toolhead_with_extruder_assist(
                        target_local_slot,
                        target_ace.toolchange_load_length,
                        target_ace.feed_speed,
                        target_ace.extruder_feeding_length,
                        target_ace.extruder_feeding_speed
                    )
                target_ace.state.set("ace_filament_pos", FILAMENT_STATE_TOOLHEAD)
            else:
                # Capture the amount purged during loading
                purged_amount = target_ace._feed_filament_into_toolhead(target_tool, check_pre_condition=False)

            mgr.state.set("ace_current_index", target_tool)
            mgr.gcode.run_script_from_command(
                f"SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=active VALUE={target_tool}"
            )
            mgr.gcode.respond_info(f"// Current tool index: {target_tool}")
            mgr.gcode.respond_info(f"ACE: State updated - current tool marked as T{target_tool}")

            gcode_move.reset_last_position()

            # Feed assist is already enabled at the end of _feed_filament_into_toolhead
            # (_feed_to_toolhead_with_extruder_assist always ends with _enable_feed_assist).
            # Calling it again here is redundant
            # Re-initialize runout detection baseline after successful load
            mgr.runout_monitor.prev_toolhead_sensor_state = mgr.get_switch_state(SENSOR_TOOLHEAD)
            logging.info(
                f"ACE: Runout detection baseline reset after load - "
                f"sensor: {'present' if mgr.runout_monitor.prev_toolhead_sensor_state else 'absent'}, "
                f"tool: T{target_tool}"
            )

            # Re-enable detection (in case it was disabled)
            if not mgr.runout_monitor.runout_detection_active:
                mgr.set_runout_detection_active(True)
                mgr.gcode.respond_info("ACE: Runout detection re-enabled after toolchange")

            toolchange_purge_length = mgr.toolchange_purge_length
            toolchange_purge_speed = mgr.toolchange_purge_speed

            if is_endless_spool and current_tool != -1:
                purge_length = int(toolchange_purge_length * 1.5)
            else:
                purge_length = toolchange_purge_length

            final_purge_length = purge_length * mgr.purge_multiplier
            if not mgr.toolhead_extrusion_allowed:
                final_purge_length = 0.0

            mgr.gcode.respond_info("ACE: Applying purge multiplier "
                                   f"{mgr.purge_multiplier:.2f} to purge length {purge_length}mm, "
                                   f"final purge length: {final_purge_length}mm")

            mgr.gcode.run_script_from_command(
                f"_ACE_POST_TOOLCHANGE FROM={current_tool} TO={target_tool} "
                f"PURGELENGTH={final_purge_length} PURGESPEED={toolchange_purge_speed} "
                f"TARGET_TEMP={target_temp} PURGED_AMOUNT={purged_amount:.1f} "
                f"PURGE_MAX_CHUNK_LENGTH={mgr.purge_max_chunk_length}"
            )

            gcode_move.reset_last_position()
            status = f"Tool {current_tool} → {target_tool} (ACE[{target_ace.instance_num}])"
        else:
            status = f"Unloaded tool {current_tool}"

        gcode_move.reset_last_position()

        if target_tool == -1:
            mgr.state.set("ace_current_index", -1)
            mgr.gcode.run_script_from_command(
                "SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=active VALUE=-1"
            )
            mgr.gcode.respond_info("ACE: State updated - no tool currently loaded")

        mgr._pre_toolchange_done = False
        mgr._pre_toolchange_fan_pwm = None
        return status

    def restore_pre_toolchange_state(self):
        """Restore Z-hop and fan state after a failed toolchange before PAUSE (ERR-09 / C-21)."""
        mgr = self.manager
        if not getattr(mgr, "_pre_toolchange_done", False):
            return False

        # 1. Restore fan speed
        saved_fan_pwm = None
        ace_state = mgr.printer.lookup_object('gcode_macro _ACE_STATE', None)
        if ace_state and hasattr(ace_state, 'variables'):
            saved_fan_pwm = ace_state.variables.get('pre_toolchange_fan', None)
        if saved_fan_pwm is None:
            saved_fan_pwm = getattr(mgr, '_pre_toolchange_fan_pwm', None)

        if saved_fan_pwm is not None:
            mgr.gcode.run_script_from_command(f"M106 S{int(saved_fan_pwm)}")
            fan = mgr.printer.lookup_object('fan', None)
            if fan is not None and hasattr(fan, 'set_speed'):
                try:
                    fan.set_speed(float(saved_fan_pwm) / 255.0)
                except Exception:
                    pass

        # 2. Restore purge_state (Z-hop and modal state)
        mgr.gcode.run_script_from_command("RESTORE_GCODE_STATE NAME=purge_state MOVE=0")

        # 3. Restore last_position in gcode_move so PAUSE_STATE saves pre-toolchange position
        gcode_move = mgr.printer.lookup_object('gcode_move', None)
        if gcode_move is not None:
            saved_states = getattr(gcode_move, 'saved_states', {})
            if 'purge_state' in saved_states:
                saved_pos = saved_states['purge_state'].get('last_position')
                if saved_pos and hasattr(gcode_move, 'last_position'):
                    gcode_move.last_position[:len(saved_pos)] = list(saved_pos)

        mgr._pre_toolchange_done = False
        mgr._pre_toolchange_fan_pwm = None
        return True
