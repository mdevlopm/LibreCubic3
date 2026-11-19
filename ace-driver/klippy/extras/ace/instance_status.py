"""
Status, heartbeat, RFID, and inventory handling mixin for AceInstance.
"""

import logging
import json
from .config import (
    AceSlotStateMachineState,
    RFID_STATE_NO_INFO,
    RFID_STATE_IDENTIFIED,
    create_inventory,
    normalize_ace_slot_state,
)
from .reporting import is_cancelled_response


class InstanceStatusMixin:
    """Handles ACE status heartbeats, RFID queries, inventory sync, and reconnect restore."""

    def _handle_rfid_info_response(self, slot_idx, response):
        """Apply a get_filament_info response to the local inventory."""
        self._pending_rfid_queries.discard(slot_idx)

        if response and response.get("code") == 0 and "result" in response:
            result = response["result"]
            self._rfid_error_reported.discard(slot_idx)

            # Check if actual RFID tag is present (not just non-RFID spool)
            rfid_state = result.get("rfid", 0)

            # Don't overwrite manual data for non-RFID spools or empty slots.
            if rfid_state != RFID_STATE_IDENTIFIED:
                logging.info(
                    f"ACE[{self.instance_num}]: Slot {slot_idx} - No RFID tag (rfid={rfid_state}), "
                    f"skipping inventory update to preserve manual data"
                )
                return

            sku = result.get("sku", "")
            brand = result.get("brand", "")
            material = result.get("type", "")
            icon_type = result.get("icon_type")
            colors_array = result.get("colors")
            rfid_color = None
            if colors_array and len(colors_array) > 0:
                first_color = (
                    colors_array[0]
                    if isinstance(colors_array[0], (list, tuple))
                    else colors_array
                )
                if len(first_color) >= 3:
                    rfid_color = [first_color[0], first_color[1], first_color[2]]
            else:
                direct_color = result.get("color")
                if (direct_color and len(direct_color) >= 3
                        and any(component > 0 for component in direct_color[:3])):
                    rfid_color = [direct_color[0], direct_color[1], direct_color[2]]
            extruder_temp = result.get("extruder_temp", {})
            hotbed_temp = result.get("hotbed_temp", {})
            diameter = result.get("diameter")
            total = result.get("total")
            current = result.get("current")

            temp_min = extruder_temp.get("min", 0)
            temp_max = extruder_temp.get("max", 0)
            temp_mode = self.ace_config.get("rfid_temp_mode", "average")

            if temp_min > 0 or temp_max > 0:
                if temp_mode == "min" and temp_min > 0:
                    rfid_temp = temp_min
                elif temp_mode == "max" and temp_max > 0:
                    rfid_temp = temp_max
                elif temp_min > 0 and temp_max > 0:
                    rfid_temp = (temp_min + temp_max) // 2
                elif temp_max > 0:
                    rfid_temp = temp_max
                else:
                    rfid_temp = temp_min
            else:
                rfid_temp = self.MATERIAL_TEMPS.get(material, self.DEFAULT_TEMP)

            if 0 <= slot_idx < self.SLOT_COUNT:
                inv = self.inventory[slot_idx]

                if material:
                    inv["material"] = material

                old_temp = inv.get("temp", 0)
                if rfid_temp != old_temp:
                    inv["temp"] = rfid_temp

                if rfid_color:
                    inv["color"] = rfid_color

                if sku:
                    inv["sku"] = sku
                if brand:
                    inv["brand"] = brand
                if icon_type is not None:
                    inv["icon_type"] = icon_type
                if extruder_temp:
                    inv["extruder_temp"] = extruder_temp
                if hotbed_temp:
                    inv["hotbed_temp"] = hotbed_temp
                if diameter is not None:
                    inv["diameter"] = diameter
                if total is not None:
                    inv["total"] = total
                if current is not None:
                    inv["current"] = current

                color_str = (
                    f"RGB({rfid_color[0]},{rfid_color[1]},{rfid_color[2]})"
                    if rfid_color else "none"
                )
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Slot {slot_idx} RFID full data -> "
                    f"sku={sku}, temp={rfid_temp}°C (min={temp_min}, max={temp_max}), "
                    f"color={color_str}, hotbed={hotbed_temp}, brand={brand}"
                )

                if self.manager:
                    self.manager._sync_inventory_to_persistent(self.instance_num, flush=False)
        else:
            msg = response.get("msg", "Unknown") if response else "No response"
            if is_cancelled_response(response):
                return
            # The read failed: the status loop had already marked the slot as
            # "rfid detected", which would stop any further automatic read.
            # Clear the flag and retry after a pause (no per-second spam).
            if 0 <= slot_idx < len(self.inventory):
                self.inventory[slot_idx]["rfid"] = False
            self._rfid_retry_after[slot_idx] = self.reactor.monotonic() + self.RFID_RETRY_DELAY_S
            if slot_idx not in self._rfid_error_reported:
                self._rfid_error_reported.add(slot_idx)
                self._report_error(
                    f"ACE[{self.instance_num}]: get_filament_info failed for slot {slot_idx}: {msg}"
                )
            else:
                logging.info(
                    f"ACE[{self.instance_num}]: get_filament_info failed again for slot {slot_idx}: {msg}"
                )

    def handle_shared_bus_filament_info_response(self, response):
        """Replay a late shared-bus get_filament_info reply if the slot is still pending."""
        if not self.transport_spec.shared_bus:
            return False

        result = response.get("result") or {}
        slot_idx = result.get("index")
        if not isinstance(slot_idx, int) or slot_idx not in self._pending_rfid_queries:
            return False

        self._handle_rfid_info_response(slot_idx, response)
        return True

    def _query_rfid_full_data(self, slot_idx):
        """
        Query full RFID tag data via get_filament_info.

        This gets the complete RFID data including:
        - extruder_temp: {min, max}
        - hotbed_temp: {min, max}
        - colors (RGBA array)
        - icon_type
        - diameter, total, current
        """
        # Prevent duplicate queries while one is in-flight
        if slot_idx in self._pending_rfid_queries:
            return True
        self._pending_rfid_queries.add(slot_idx)

        def rfid_callback(response):
            self._handle_rfid_info_response(slot_idx, response)

        request = self.protocol.build_get_filament_info_request(slot_idx)
        if self.send_request(request, rfid_callback) is False:
            # not queued (disconnected/disabled): no callback will ever clear it
            self._pending_rfid_queries.discard(slot_idx)
            return False
        return True

    def _emit_inventory_update(self):
        """Emit JSON inventory update for KlipperScreen."""
        try:
            slots_out = []
            for i in range(self.SLOT_COUNT):
                inv = self.inventory[i]
                slot_data = {
                    "status": inv.get("status"),
                    "color": inv.get("color"),
                    "material": inv.get("material"),
                    "temp": inv.get("temp"),
                    "rfid": inv.get("rfid", False),
                }
                # Include optional RFID metadata fields if present
                for key in [
                    "sku",
                    "brand",
                    "icon_type",
                    "rgba",
                    "extruder_temp",
                    "hotbed_temp",
                    "diameter",
                    "total",
                        "current"]:
                    if key in inv:
                        slot_data[key] = inv[key]
                slots_out.append(slot_data)
            self.gcode.respond_info("// " + json.dumps({"instance": self.instance_num, "slots": slots_out}))
        except Exception:
            pass

    def _status_update_callback(self, response):
        """Handle status updates from ACE hardware."""
        inventory_changed = False
        feed_assist_was_active = self._feed_assist_index
        filament_loaded = False

        # LOG RAW status RESPONSE (only slots data for readability)
        # if response and "result" in response:
        #     slots_data = response.get("result", {}).get("slots", [])
        #     if slots_data:
        #         for slot in slots_data:
        #             idx = slot.get("index")
        #             rfid_val = slot.get("rfid")
        #             status_val = slot.get("status")
        #             self.gcode.respond_info(
        #                 f"ACE[{self.instance_num}]: Slot {idx} status RAW -> rfid={rfid_val}, status={status_val}"
        #             )

        if response and "result" in response:
            raw_result = response["result"]
            if isinstance(raw_result, dict) and "raw_fields" in raw_result:
                self._info = {k: v for k, v in raw_result.items() if k != "raw_fields"}
            else:
                self._info = raw_result
            self._info_time = self.reactor.monotonic()

            dryer_info = (
                (self._info.get("dryer_status") or self._info.get("dryer"))
                if isinstance(self._info, dict) else None
            )
            if isinstance(dryer_info, dict) and "status" in dryer_info:
                raw_dry_status = dryer_info.get("status")
                dry_status_str = "" if raw_dry_status is None else str(raw_dry_status).strip()
                dry_lower = dry_status_str.lower()
                if dry_lower not in ("", "stop", "drying", "stopping"):
                    detail = str(dryer_info.get("state_detail") or "").strip()
                    err_val = detail if (dry_lower == "error" and detail) else dry_status_str
                    self._dryer_error = err_val
                    if self._last_dryer_error_reported != err_val:
                        self._last_dryer_error_reported = err_val
                        self._report_error(
                            f"ACE[{self.instance_num}]: Dryer error: {err_val}"
                        )
                        logging.warning(
                            "ACE[%s]: Dryer error: %s",
                            self.instance_num,
                            err_val,
                        )
                else:
                    self._dryer_error = None
                    self._last_dryer_error_reported = None

            # Handle pending RFID refresh after reconnect
            if self._pending_rfid_refresh:
                self._pending_rfid_refresh = False
                if self.rfid_inventory_sync_enabled:
                    # Unconditionally query all slots to catch any spool changes during disconnect
                    for slot_idx in range(self.SLOT_COUNT):
                        logging.info(
                            f"ACE[{self.instance_num}]: Reconnect - querying RFID data for slot {slot_idx}"
                        )
                        self._query_rfid_full_data(slot_idx)

            slots = self._info.get("slots", [])
            for slot in slots:
                idx = slot.get("index")
                if idx is not None and 0 <= idx < self.SLOT_COUNT:
                    # Get saved metadata (material/color/temp)
                    saved_color = self.inventory[idx].get("color", [0, 0, 0])
                    saved_material = self.inventory[idx].get("material", "")
                    saved_temp = self.inventory[idx].get("temp", 0)
                    saved_rfid = self.inventory[idx].get("rfid", False)

                    updated_color = saved_color
                    updated_material = saved_material
                    updated_temp = saved_temp
                    updated_rfid = None  # Will be set based on transition or status

                    # Get current states
                    old_status = self.inventory[idx].get("status")
                    new_status = normalize_ace_slot_state(
                        slot.get("status"),
                        default=AceSlotStateMachineState.EMPTY.value,
                    )

                    # Detect state changes
                    if old_status != new_status:
                        inventory_changed = True

                        # Log the state transition
                        if (
                            old_status == AceSlotStateMachineState.EMPTY.value
                            and new_status == AceSlotStateMachineState.READY.value
                        ):
                            # Check if the new spool is non-RFID (RFID_STATE_NO_INFO = 0)
                            rfid_state = slot.get("rfid")
                            if rfid_state == RFID_STATE_NO_INFO and saved_rfid:
                                # RFID → non-RFID transition: clear RFID-specific fields only
                                # (User may have replaced empty RFID spool with matching non-RFID spool)
                                # Keep material/color/temp for print continuity
                                updated_rfid = False
                                # Clear all optional RFID-specific fields from inventory
                                for key in [
                                    "sku",
                                    "brand",
                                    "icon_type",
                                    "extruder_temp",
                                    "hotbed_temp",
                                    "diameter",
                                    "total",
                                        "current"]:
                                    self.inventory[idx].pop(key, None)
                                # Clear query state
                                self._pending_rfid_queries.discard(idx)
                                self.gcode.respond_info(
                                    f"ACE[{self.instance_num}]: Slot {idx} RFID→non-RFID swap detected -> "
                                    f"empty → ready (clearing RFID fields, preserving material={saved_material})"
                                )
                            else:
                                # RFID spool (identifying/identified) OR same non-RFID refilled: preserve metadata
                                self.gcode.respond_info(
                                    f"ACE[{self.instance_num}]: Slot {idx} auto-restored: "
                                    f"empty → ready (material={saved_material})"
                                )
                            filament_loaded = True

                        elif (
                            old_status == AceSlotStateMachineState.READY.value
                            and new_status == AceSlotStateMachineState.EMPTY.value
                        ):
                            self.gcode.respond_info(
                                f"ACE[{self.instance_num}]: Slot {idx} marked empty "
                                f"(was: material={saved_material})"
                            )

                    # If slot is empty, clear RFID marker and metadata
                    if new_status == AceSlotStateMachineState.EMPTY.value:
                        updated_rfid = False
                        # Clear all optional RFID fields
                        for key in [
                            "sku",
                            "brand",
                            "icon_type",
                            "extruder_temp",
                            "hotbed_temp",
                            "diameter",
                            "total",
                                "current"]:
                            self.inventory[idx].pop(key, None)
                        self._pending_rfid_queries.discard(idx)

                        # If NOT printing/paused, also clear material/color/temp to defaults
                        # (For endless spool/runout during printing, we preserve this info)
                        if not self._is_printing_or_paused():
                            updated_material = ""
                            updated_color = [0, 0, 0]
                            updated_temp = 0
                            # Persist only when clearing actually changes something;
                            # an already-empty slot must not dirty the inventory (and
                            # trigger a Moonraker lane_data POST) on every heartbeat.
                            if (saved_material != ""
                                    or list(saved_color or [])[:3] != [0, 0, 0]
                                    or saved_temp != 0
                                    or saved_rfid):
                                inventory_changed = True

                    # Handle RFID tag detection - only query get_filament_info, don't use status metadata
                    elif new_status == AceSlotStateMachineState.READY.value:
                        rfid_state = slot.get("rfid")

                        # When RFID sync enabled: only use data from get_filament_info callback
                        # Don't read material/color/sku from get_status - it may be stale
                        if self.rfid_inventory_sync_enabled and rfid_state == RFID_STATE_IDENTIFIED:
                            # Query on RFID state transition (false → detected)
                            # Skip if query is already in-flight
                            query_pending = idx in self._pending_rfid_queries

                            retry_ok = (
                                self.reactor.monotonic()
                                >= self._rfid_retry_after.get(idx, 0.0)
                            )
                            if not saved_rfid and not query_pending and retry_ok:
                                # Query get_filament_info - callback will populate all
                                # metadata. Only claim "rfid" if the query was queued.
                                updated_rfid = bool(self._query_rfid_full_data(idx))
                                self.gcode.respond_info(
                                    f"ACE[{self.instance_num}]: Slot {idx} RFID detected -> "
                                    f"querying get_filament_info..."
                                )
                                inventory_changed = True
                            else:
                                # RFID already detected - keep existing data
                                if updated_rfid is None:
                                    updated_rfid = saved_rfid
                        else:
                            # RFID sync disabled or no RFID: keep inventory as source of truth
                            if updated_rfid is None:
                                updated_rfid = saved_rfid

                        # If still missing metadata, fall back to defaults for ready slots
                        missing_material = not updated_material.strip()
                        missing_temp = updated_temp <= 0
                        missing_color = (
                            not updated_color
                            or len(updated_color) < 3
                            or all(c == 0 for c in updated_color[:3])
                        )

                        # When RFID sync is disabled AND the slot reports RFID data (rfid_state != RFID_STATE_NO_INFO),
                        # do not auto-fill defaults; leave values as-is to satisfy "ignore RFID data".
                        allow_default_fill = not (
                            not self.rfid_inventory_sync_enabled and rfid_state not in (
                                None, RFID_STATE_NO_INFO))

                        # Never overwrite with defaults while an RFID query is in-flight;
                        # the callback will populate the real data when it arrives.
                        rfid_query_in_flight = idx in self._pending_rfid_queries

                        if allow_default_fill and not rfid_query_in_flight and (missing_material or missing_temp):
                            # Check if we're actually changing anything before setting inventory_changed
                            needs_update = False
                            if missing_material and updated_material != self.DEFAULT_MATERIAL:
                                updated_material = self.DEFAULT_MATERIAL
                                needs_update = True
                            elif missing_material:
                                updated_material = self.DEFAULT_MATERIAL

                            if missing_temp and updated_temp != self.DEFAULT_TEMP:
                                updated_temp = self.DEFAULT_TEMP
                                needs_update = True
                            elif missing_temp:
                                updated_temp = self.DEFAULT_TEMP

                            if missing_color and updated_color != self.DEFAULT_COLOR:
                                updated_color = list(self.DEFAULT_COLOR)
                                needs_update = True
                            elif missing_color:
                                updated_color = list(self.DEFAULT_COLOR)

                            if needs_update:
                                inventory_changed = True
                                self.gcode.respond_info(
                                    f"ACE[{self.instance_num}]: Slot {idx} ready with no metadata -> "
                                    f"defaulting to {updated_material} {updated_temp}C, color {updated_color}"
                                )
                    else:
                        # Don't overwrite updated_rfid if it was already set
                        if updated_rfid is None:
                            updated_rfid = saved_rfid

                    # Final safety check: ensure updated_rfid has a value
                    if updated_rfid is None:
                        updated_rfid = saved_rfid

                    # Only write to inventory if values actually changed
                    inv = self.inventory[idx]
                    if (inv.get("status") != new_status or
                        inv.get("color") != updated_color or
                        inv.get("material") != updated_material or
                        inv.get("temp") != updated_temp or
                            inv.get("rfid") != updated_rfid):
                        inv["status"] = new_status
                        inv["color"] = updated_color
                        inv["material"] = updated_material
                        inv["temp"] = updated_temp
                        inv["rfid"] = updated_rfid

        # Persist changes if any status changed (deferred; flushed at print end)
        if inventory_changed:
            if self.manager:
                self.manager._sync_inventory_to_persistent(self.instance_num, flush=False)

            # Emit inventory update for KlipperScreen
            # self._emit_inventory_update()

            # Restore feed assist if it was active before filament loading
            # (ACE hardware disables feed assist when loading filament on any slot)
            if filament_loaded and feed_assist_was_active >= 0:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Re-enabling feed assist on slot "
                    f"{feed_assist_was_active} after automatic disable during slot loading"
                )
                # _enable_feed_assist() blocks (wait_ready -> reactor.pause). This
                # code runs inside the serial reader timer; pausing there keeps
                # the reader from ever running again (Klipper marks a running
                # timer as NEVER), so wait_ready could never see "ready".
                # Run it in its own reactor greenlet instead.
                def _restore_feed_assist(eventtime, slot=feed_assist_was_active):
                    try:
                        self._enable_feed_assist(slot)
                    except Exception as e:
                        self._report_error(
                            f"ACE[{self.instance_num}]: Failed to restore feed assist: {e}"
                        )

                self.reactor.register_callback(_restore_feed_assist)

    def _on_heartbeat_response(self, response):
        """Handle heartbeat response."""
        if response is None:
            self._record_status_failure("no response")
            return

        if response.get("code") == 0 and "result" in response:
            self._reset_status_failure_tracking()
            self._hb_error_reported = False
            self._status_update_callback(response)
            self._check_tangle_protection()

            # Restore pending feed assist after first successful heartbeat
            self._maybe_restore_pending_feed_assist()
        else:
            msg = response.get("msg", "Unknown error")
            # one "!!" per run of failing status replies, not one per second
            if not self._hb_error_reported:
                self._hb_error_reported = True
                self._report_error(
                    f"ACE[{self.instance_num}]: Heartbeat response error: {msg}"
                )
            else:
                logging.info(f"ACE[{self.instance_num}]: Heartbeat response error: {msg}")
            self._record_status_failure(str(msg))

    def _reset_status_failure_tracking(self):
        """Clear heartbeat/status failure tracking after successful communication."""
        self._status_failure_streak = 0
        self._status_recovery_in_progress = False

    def _record_status_failure(self, reason):
        """Track one failed heartbeat/status response and trigger reconnect if needed."""
        self._status_failure_streak += 1
        if self._status_failure_streak < self.status_failure_threshold:
            return
        if self._status_recovery_in_progress:
            return

        self._status_recovery_in_progress = True
        self._report_error(
            f"ACE[{self.instance_num}]: Heartbeat/status failed "
            f"{self._status_failure_streak} times ({reason}) - reconnecting"
        )

        reconnect = getattr(self.serial_mgr, "reconnect", None)
        if callable(reconnect):
            try:
                reconnect()
                return
            except Exception as exc:
                logging.warning(
                    "ACE[%s]: reconnect() after status failures failed: %s",
                    self.instance_num,
                    exc,
                )

        disconnect = getattr(self.serial_mgr, "disconnect", None)
        if callable(disconnect):
            try:
                disconnect()
            except Exception as exc:
                logging.warning(
                    "ACE[%s]: disconnect() after status failures failed: %s",
                    self.instance_num,
                    exc,
                )

    def _maybe_restore_pending_feed_assist(self):
        """
        Restore feed assist if pending after reconnect.

        Called after first successful heartbeat to ensure connection is stable.
        Only restores if reconnecting to same position in daisy chain (topology).
        """
        if self._pending_feed_assist_restore < 0:
            return

        slot = self._pending_feed_assist_restore

        # Runs inside the serial reader timer: must never block/pause here (see
        # _status_update_callback). If the ACE is busy (RFID read/preload) try
        # again on the next heartbeat, for at most ~30 s.
        if not self.is_ready():
            self._feed_assist_restore_defers += 1
            if self._feed_assist_restore_defers > 30:
                self._pending_feed_assist_restore = -1
                self._feed_assist_restore_defers = 0
                logging.info(
                    f"ACE[{self.instance_num}]: Skipping feed assist restoration on slot {slot} "
                    f"(ACE stayed busy)"
                )
            return
        self._feed_assist_restore_defers = 0
        self._pending_feed_assist_restore = -1  # Clear before attempting

        # Check if we're at the same position in the daisy chain
        current_position = self.serial_mgr.get_usb_topology_position()

        if self._feed_assist_topology_position is not None and current_position != self._feed_assist_topology_position:
            # Connected to different position in chain, don't restore
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Skipping feed assist restore - "
                f"different chain position (was: {self._feed_assist_topology_position}, "
                f"now: {current_position})"
            )
            self._feed_assist_index = -1  # Clear stale state
            self._feed_assist_topology_position = None
            return

        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: Restoring feed assist on slot {slot} "
            f"(chain position: {current_position})"
        )
        try:
            request = self.protocol.build_start_feed_assist_request(slot)
            self.send_request(
                request,
                lambda response: self._on_feed_assist_restore_response(response, slot)
            )
        except Exception as e:
            self._report_error(
                f"ACE[{self.instance_num}]: Failed to restore feed assist: {e}"
            )

    def _on_ace_connect(self):
        """
        Handle ACE connection/reconnection.

        Refreshes RFID data for all slots with detected tags to catch any
        spool changes that occurred while disconnected.

        Defers feed assist restoration and RFID refresh until after first
        successful status update to ensure connection is stable.
        """
        self._reset_status_failure_tracking()

        # Requests of the previous session were dropped without callbacks, so
        # nothing would ever clear these in-flight markers.
        self._shared_bus_hb_outstanding = False
        self._pending_rfid_queries.clear()
        self._rfid_retry_after.clear()

        # Set flag to refresh RFID data on next status update
        # This ensures we have current data if spools were changed during disconnect
        self._pending_rfid_refresh = True
        logging.info(
            f"ACE[{self.instance_num}]: Connected - will refresh RFID data after first status update"
        )

        if not self.feed_assist_active_after_ace_connect:
            logging.info(
                f"ACE[{self.instance_num}]: Connected - feed assist restoration disabled"
            )
            return

        # Check if feed assist was active before disconnect
        if self._feed_assist_index >= 0:
            slot = self._feed_assist_index
            # Defer restoration until after first heartbeat confirms communication
            self._pending_feed_assist_restore = slot
            logging.info(
                f"ACE[{self.instance_num}]: Connected - "
                f"will restore feed assist on slot {slot} after heartbeat"
            )
        else:
            logging.info(
                f"ACE[{self.instance_num}]: Connected - "
                f"no previous feed assist to restore"
            )

    def _on_feed_assist_restore_response(self, response, slot):
        """Handle response from feed assist restoration after reconnect."""
        if response and response.get("code") == 0:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Feed assist restored on slot {slot}"
            )
        else:
            msg = response.get("msg", "Unknown") if response else "No response"
            self._report_error(
                f"ACE[{self.instance_num}]: Feed assist restoration failed: {msg}"
            )

    def reset_persistent_inventory(self):
        """Reset persistent inventory to empty slots."""
        self.inventory = create_inventory(self.SLOT_COUNT)
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: Persistent inventory reset to empty"
        )
