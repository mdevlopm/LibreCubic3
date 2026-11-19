"""
Low-level motion, feed-assist, RPC wait, and extruder move mixin for AceInstance.
"""

import logging
from .config import (
    AceSlotStateMachineState,
    MAX_RETRIES,
    normalize_ace_slot_state,
)
from .reporting import is_cancelled_response


class InstanceMotionMixin:
    """Provides reactor waits, feed-assist control, and feed/retract RPC primitives."""

    def _check_abort(self):
        """Raise if the printer is shutting down.

        Blocking waits (wait_ready, retract/feed loops, dwell) call this so a
        M112/klippy shutdown does not leave them sleeping for up to minutes on a
        port that is already closed.
        """
        manager = self.manager
        if self.serial_mgr.is_shutdown() or (
                manager is not None and getattr(manager, "_shutdown_in_progress", False)):
            raise RuntimeError(f"ACE[{self.instance_num}]: aborted - printer shutdown")

    def _pause(self, seconds):
        """Reactor sleep that aborts on shutdown."""
        self._check_abort()
        self.reactor.pause(self.reactor.monotonic() + seconds)

    def _rpc_budget(self, minimum):
        """Seconds a synchronous wait may take for one request.

        The serial layer retries queries/stops MAX_ATTEMPTS times (timeout_s
        each) and gives "start" commands one START_TIMEOUT_S wait, so a caller
        must wait at least that long or it gives up while the request is still
        legitimately in flight.
        """
        # Worst case: the request first waits behind ONE other request that uses
        # its whole retry window, then needs its own whole window.
        window = max(
            self.serial_mgr.timeout_s * self.serial_mgr.MAX_ATTEMPTS,
            self.serial_mgr.START_TIMEOUT_S,
        )
        return max(float(minimum), 2.0 * window + 1.0)

    def _give_up_waiting(self, callback):
        """The wait budget is spent: cancel the request so it cannot run later."""
        self.serial_mgr.discard_request(callback)

    def wait_ready(self, on_wait_cycle=None, timeout_s=60.0):
        """Wait for ACE unit to be ready with a hard timeout."""
        waited = 0.0
        interval = 0.5
        total_wait = 0.0

        while self._info.get("status") != "ready":
            self._pause(interval)
            waited += interval
            total_wait += interval

            if waited >= 25.0:
                self.send_high_prio_request(
                    request=self.protocol.build_get_status_request(),
                    callback=self._status_update_callback
                )
                waited = 0.0

            if on_wait_cycle is not None:
                on_wait_cycle()

            if total_wait >= timeout_s:
                raise TimeoutError(
                    f"ACE[{self.instance_num}]: wait_ready timed out after {timeout_s:.0f}s"
                )

    def is_ready(self):
        """Check if ACE is ready."""
        return self._info.get("status") == "ready"

    def _update_feed_assist(self, slot_index):
        """Update feed assist state: enable if slot >= 0, disable if -1."""
        if slot_index == -1:
            self._disable_feed_assist(slot_index)
        else:
            self._enable_feed_assist(slot_index)

    def _get_current_feed_assist_index(self):
        """Get the current feed assist slot index (-1 if disabled)."""
        return self._feed_assist_index

    def _is_slot_empty(self, slot_index):
        """Return True if ACE reports the given slot as empty."""
        for slot in self._info.get("slots", []):
            if slot.get("index") == slot_index:
                slot_status = normalize_ace_slot_state(slot.get("status"))
                return slot_status == AceSlotStateMachineState.EMPTY.value
        return False

    def _is_printing_or_paused(self):
        """Check if printer is in a printing or paused state.

        Returns:
            bool: True if printing or paused, False otherwise
        """
        print_stats = self.printer.lookup_object("print_stats", None)
        if not print_stats:
            return False

        try:
            stats = print_stats.get_status(self.reactor.monotonic())
            state = (stats.get("state") or "").lower()
            return state in ["printing", "paused"]
        except Exception:
            return False

    def _enable_feed_assist(self, slot_index):
        """Enable feed assist for smooth filament loading."""
        previous_index = self._feed_assist_index
        previous_topology = self._feed_assist_topology_position
        self._feed_assist_index = slot_index
        self._feed_assist_topology_position = self.serial_mgr.get_usb_topology_position()

        def rollback():
            # the ACE did not accept it: do not keep claiming it is active
            if self._feed_assist_index == slot_index:
                self._feed_assist_index = previous_index
                self._feed_assist_topology_position = previous_topology

        def callback(response):
            if response and response.get("code") == 0:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Feed assist enabled on slot {slot_index}"
                )
                self.state.set(
                    f"ace_feed_assist_index_{self.instance_num}",
                    slot_index
                )
            elif is_cancelled_response(response):
                rollback()
                logging.info(
                    f"ACE[{self.instance_num}]: Feed assist enable cancelled by stop request"
                )
            else:
                rollback()
                msg = response.get("msg", "Unknown") if response else "no response"
                logging.warning(
                    f"ACE[{self.instance_num}]: Feed assist enable failed: {msg}"
                )
                self._report_error(
                    f"ACE[{self.instance_num}]: Feed assist enable failed on slot {slot_index}: {msg}"
                )

        self.wait_ready()
        request = self.protocol.build_start_feed_assist_request(slot_index)
        if self.send_request(request, callback) is False:
            callback(None)  # not connected / dropped
        # ACE1: stays 'ready' during feed assist, so wait confirms the command was processed.
        # ACE2: transitions to 'busy' the moment feed assist starts and never returns to
        # 'ready' until STOP_FEED_ASSIST is received.  Calling wait_ready() here on ACE2
        # would block forever.
        if not self.protocol.feed_assist_causes_busy():
            self.wait_ready()

    def _disable_feed_assist(self, slot_index):
        """Disable feed assist."""
        if slot_index < 0:
            # Feed assist is never active on a negative slot index; nothing to disable.
            # This guards against callers that pass _feed_assist_index directly when
            # feed assist was already off (-1), which would otherwise bypass the check
            # below and send a spurious STOP_FEED_OR_ROLLBACK command.
            return
        if self._feed_assist_index != slot_index:
            logging.warning(
                f"ACE[{self.instance_num}]: Feed assist not active on slot {slot_index}"
            )
            return

        self._feed_assist_index = -1
        self._feed_assist_topology_position = None

        def callback(response):
            if response and response.get("code") == 0:
                logging.info(
                    f"ACE[{self.instance_num}]: Feed assist disabled for slot {slot_index}"
                )
                self.state.set(
                    f"ace_feed_assist_index_{self.instance_num}",
                    -1
                )
            else:
                msg = response.get("msg", "Unknown") if response else "no response"
                logging.warning(
                    f"ACE[{self.instance_num}]: Feed assist disable failed: {msg}"
                )
                self._report_error(
                    f"ACE[{self.instance_num}]: Feed assist disable failed on slot {slot_index}: {msg}"
                )

        # ACE1: stays 'ready' during feed assist, so this pre-send wait guards against
        # sending STOP while the device is processing something else.
        # ACE2: is 'busy' *because* feed assist is active.  The only way to make it ready
        # again is to send STOP_FEED_ASSIST, so waiting for 'ready' first is a deadlock.
        if not self.protocol.feed_assist_causes_busy():
            self.wait_ready()
        request = self.protocol.build_stop_feed_assist_request(slot_index)
        self.send_urgent_request(request, callback)
        self.dwell(1.0)
        # ACE2: the device only leaves 'busy' once it has processed STOP_FEED_ASSIST,
        # and the cached status is refreshed via the 1 Hz heartbeat.  If a heartbeat
        # times out around print-end, wait_ready() can stall for up to 60s before
        # giving up
        if not self.protocol.feed_assist_causes_busy():
            self.wait_ready()

    def _feed(self, slot, length, speed, callback=None):
        """Feed filament from slot."""
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: _feed() -> slot={slot}, "
            f"length={length}mm, speed={speed}mm/s"
        )

        if callback is None:
            def callback(response):
                if response is None:
                    self._report_error(
                        f"ACE[{self.instance_num}]: Feed failed: no response from ACE"
                    )
                elif is_cancelled_response(response):
                    logging.info(
                        f"ACE[{self.instance_num}]: Feed cancelled by stop request"
                    )
                elif response.get("msg") == "FORBIDDEN":
                    msg = f"ACE[{self.instance_num}]: Feed forbidden"
                    self._report_error(f"{msg}: {response}")
                elif response.get("code", 0) != 0:
                    msg = f"ACE[{self.instance_num}]: Feed error: {response.get('msg')}"
                    self._report_error(msg)

        request = self.protocol.build_feed_filament_request(slot, length, speed)
        self.gcode.respond_info(f"ACE[{self.instance_num}]: Sending request: {request}")
        self.send_request(request, callback)

    def _stop_feed(self, slot):
        """Stop feeding filament."""
        def callback(response):
            if response is None:
                self._report_error(
                    f"ACE[{self.instance_num}]: Stop feed failed: no response from ACE"
                )
            elif response.get("code") != 0:
                msg = response.get("msg", "Unknown error")
                self._report_error(
                    f"ACE[{self.instance_num}]: Stop feed error: {msg}"
                )
            else:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Stop feed successful: {response}"
                )

        request = self.protocol.build_stop_feed_filament_request(slot)
        self.send_urgent_request(request, callback)

    def _retract(self, slot, length, speed, on_retract_started=None, on_wait_for_ready=None, early_stop_callback=None):
        """
        Retract filament from slot with automatic retry on FORBIDDEN errors.

        Args:
            slot: Local slot index (0-3)
            length: Distance to retract (mm)
            speed: Retract speed (mm/s)

        Returns:
            dict: Response from ACE

        Raises:
            ValueError: If retraction fails after all retries
        """
        max_retries = MAX_RETRIES
        retry_delay_s = 2.0

        self.wait_ready()
        self._last_retract_early_stopped = False

        # If the slot already reports empty, there is nothing to retract.
        if self._is_slot_empty(slot):
            self._last_retract_early_stopped = True
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Retract skipped - slot {slot} is empty"
            )
            return {"code": 0, "msg": "Retract skipped: slot empty"}

        for attempt in range(1, max_retries + 1):
            ace_status_before = self._info.get('status', 'unknown')
            retract_start_time = self.reactor.monotonic()
            early_stop_state = {"triggered": False, "elapsed": None}

            if attempt > 1:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: _retract() attempt {attempt}/{max_retries}:\n"
                    f"  Slot: {slot}\n"
                    f"  Length: {length}mm\n"
                    f"  Speed: {speed}mm/s\n"
                    f"  ACE status before: {ace_status_before}"
                )

            request = self.protocol.build_unwind_filament_request(slot, length, speed)

            response_container = {"response": None, "done": False}

            def check_slot_empty():
                if early_stop_state["triggered"]:
                    return
                if self._is_slot_empty(slot):
                    early_stop_state["triggered"] = True
                    self._last_retract_early_stopped = True
                    early_stop_state["elapsed"] = self.reactor.monotonic() - retract_start_time
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: Retract stopped after "
                        f"{early_stop_state['elapsed']:.2f}s - slot {slot} reports empty"
                    )
                    self._stop_retract(slot)

            def callback(response):
                response_container["response"] = response
                response_container["done"] = True

            if self.send_request(request, callback) is False:
                callback(None)  # not connected: fail now instead of waiting

            timeout = self.reactor.monotonic() + self._rpc_budget(5.0)
            while not response_container["done"] and self.reactor.monotonic() < timeout:
                self._pause(0.1)
            if not response_container["done"]:
                self._give_up_waiting(callback)

            response = response_container["response"]
            ace_status_after = self._info.get('status', 'unknown')

            if not response:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: _retract() attempt {attempt}/{max_retries} - "
                    f"No response from ACE"
                )
                if attempt < max_retries:
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: Waiting {retry_delay_s}s before retry..."
                    )
                    self._pause(retry_delay_s)
                    continue
                else:
                    raise ValueError(
                        f"ACE[{self.instance_num}]: Retract failed - no response after "
                        f"{max_retries} attempts. Check ACE connection"
                    )

            result_code = response.get('code', -1)
            result_msg = response.get('msg', 'unknown')

            if result_code == 0 and result_msg != 'FORBIDDEN':
                # self.gcode.respond_info(
                #     f"ACE[{self.instance_num}]: _retract() command accepted on attempt {attempt}"
                # )

                # Execute callback immediately after retract command accepted
                if on_retract_started is not None:
                    try:
                        on_retract_started()
                    except Exception as cb_error:
                        self.gcode.respond_info(
                            f"ACE[{self.instance_num}]: Retract started callback error: {cb_error}"
                        )

                expected_time_s = length / speed
                dwell_time_s = expected_time_s

                # Waiting 1/4 of the expected time before waiting for ready to avoid any
                # timing issues with the ACE reporting ready inbetween gear shifting
                # Call callback during dwell in case sensor changes early
                dwell_end = self.reactor.monotonic() + (dwell_time_s)
                while self.reactor.monotonic() < dwell_end:
                    if on_wait_for_ready is not None:
                        on_wait_for_ready()
                    check_slot_empty()
                    if early_stop_state["triggered"]:
                        self.wait_ready()
                        return {"code": 0, "msg": "Retract stopped early: slot empty"}
                    if early_stop_callback is not None:
                        stop_reason = early_stop_callback()
                        if stop_reason:
                            self._stop_retract(slot)
                            self.wait_ready()
                            return {"code": 0, "msg": f"Retract stopped early: {stop_reason}"}
                    self._pause(0.2)

                def wait_cycle():
                    if on_wait_for_ready is not None:
                        on_wait_for_ready()
                    check_slot_empty()
                    if early_stop_callback is not None:
                        stop_reason = early_stop_callback()
                        if stop_reason:
                            self._stop_retract(slot)

                self.wait_ready(on_wait_cycle=wait_cycle)

                if early_stop_state["triggered"]:
                    return {"code": 0, "msg": "Retract stopped early: slot empty"}

                return response

            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Retract rejected - Attempt {attempt}/{max_retries}\n"
                f"  Response Code: {result_code}\n"
                f"  Response Message: '{result_msg}'\n"
                f"  Slot: {slot}\n"
                f"  Length: {length}mm\n"
                f"  Speed: {speed}mm/s\n"
                f"  ACE status before: {ace_status_before}\n"
                f"  ACE status after: {ace_status_after}"
            )

            if attempt < max_retries:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Waiting {retry_delay_s}s for ACE to finish..."
                )
                self._pause(retry_delay_s)

                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Verifying ACE is ready for retry {attempt + 1}..."
                )
            else:
                total_wait = max_retries * retry_delay_s
                raise ValueError(
                    f"ACE[{self.instance_num}]: Retract failed after {max_retries} attempts\n"
                    f"  Final Code: {result_code}\n"
                    f"  Final Message: '{result_msg}'\n"
                    f"  Slot: {slot}\n"
                    f"  Length: {length}mm\n"
                    f"  Speed: {speed}mm/s\n"
                    f"  Total wait time: {total_wait}s\n"
                    f"  Final ACE status: {ace_status_after}"
                )

        raise ValueError(
            f"ACE[{self.instance_num}]: Retract logic error - exhausted retries without exception"
        )

    def _stop_retract(self, slot):
        """Stop retracting filament."""
        def callback(response):
            if response is None:
                self._report_error(
                    f"ACE[{self.instance_num}]: Stop retract failed: no response from ACE"
                )
            elif response.get("code") != 0:
                msg = response.get("msg", "Unknown error")
                self._report_error(
                    f"ACE[{self.instance_num}]: Stop retract error: {msg}"
                )
            else:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Stop retract successful: {response}"
                )

        request = self.protocol.build_stop_unwind_filament_request(slot)
        self.send_urgent_request(request, callback)

    def _change_retract_speed(self, slot, retract_speed):
        """
        Request to update active retract speed while unwind command is running.

        Returns:
            bool: True if firmware acknowledged, False otherwise
        """
        logging.info(
            f"ACE[{self.instance_num}]: Requesting retract speed change to "
            f"{retract_speed}mm/s (slot {slot})"
        )

        request = self.protocol.build_update_unwinding_speed_request(slot, retract_speed)
        response_container = {"response": None, "done": False}

        def callback(response):
            response_container["response"] = response
            response_container["done"] = True

        try:
            queued = self.send_request(request, callback)
        except Exception as exc:
            self._report_error(
                f"ACE[{self.instance_num}]: Retract speed change request error: {exc}"
            )
            return False
        if queued is False:
            self._report_error(
                f"ACE[{self.instance_num}]: Retract speed change not sent (ACE not connected)"
            )
            return False

        timeout = self.reactor.monotonic() + self._rpc_budget(3.0)
        while not response_container["done"] and self.reactor.monotonic() < timeout:
            self.dwell(0.05)
        if not response_container["done"]:
            self._give_up_waiting(callback)

        response = response_container["response"]
        if not response:
            self._report_error(
                f"ACE[{self.instance_num}]: Retract speed change returned no response"
            )
            return False

        code = response.get("code", -1)
        msg = response.get("msg", "Unknown")
        if code != 0:
            self._report_error(
                f"ACE[{self.instance_num}]: Retract speed change failed: "
                f"code={code}, msg='{msg}'"
            )
            return False

        return True

    def _change_feed_speed(self, slot, feed_speed):
        """
        Request to update active feed speed while feeding is running.

        Returns:
            bool: True if firmware acknowledged, False otherwise
        """
        logging.info(
            f"ACE[{self.instance_num}]: Requesting feed speed change to "
            f"{feed_speed}mm/s (slot {slot})"
        )

        request = self.protocol.build_update_feeding_speed_request(slot, feed_speed)
        response_container = {"response": None, "done": False}

        def callback(response):
            response_container["response"] = response
            response_container["done"] = True

        try:
            queued = self.send_request(request, callback)
        except Exception as exc:
            self._report_error(
                f"ACE[{self.instance_num}]: Feed speed change request error: {exc}"
            )
            return False
        if queued is False:
            self._report_error(
                f"ACE[{self.instance_num}]: Feed speed change not sent (ACE not connected)"
            )
            return False

        timeout = self.reactor.monotonic() + self._rpc_budget(1.0)
        while not response_container["done"] and self.reactor.monotonic() < timeout:
            self.dwell(0.05)
        if not response_container["done"]:
            self._give_up_waiting(callback)

        response = response_container["response"]
        if not response:
            self._report_error(
                f"ACE[{self.instance_num}]: Feed speed change returned no response"
            )
            return False

        code = response.get("code", -1)
        msg = response.get("msg", "Unknown")
        if code != 0:
            self._report_error(
                f"ACE[{self.instance_num}]: Feed speed change failed: "
                f"code={code}, msg='{msg}'"
            )
            return False

        return True

    def feed_filament_with_wait_for_response(self, slot, length, speed):
        """
        Synchronously feed filament and wait for response.

        Returns:
            dict: Response from ACE
        """
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: feed_filament_with_wait_for_response() -> slot={slot}, "
            f"length={length}mm, speed={speed}mm/s"
        )

        response_container = {"response": None, "done": False}

        def callback(response):
            response_container["response"] = response
            response_container["done"] = True
            # if response:
            #     code = response.get("code", -1)
            #     msg = response.get("msg", "Unknown")
            #     self.gcode.respond_info(
            #         f"ACE[{self.instance_num}]: feed_filament_with_wait_for_response() response -> "
            #         f"code={code}, msg='{msg}'"
            #     )

        request = self.protocol.build_feed_filament_request(slot, length, speed)
        # self.gcode.respond_info(
        #     f"ACE[{self.instance_num}]: Sending sync request: {request}"
        # )

        self.wait_ready()
        if self.send_request(request, callback) is False:
            callback(None)  # not connected: fail now instead of waiting

        timeout = self.reactor.monotonic() + self._rpc_budget(10.0)
        poll_count = 0
        while not response_container["done"] and self.reactor.monotonic() < timeout:
            self.dwell(delay=0.2)
            poll_count += 1

        if not response_container["done"]:
            self._give_up_waiting(callback)
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: feed_filament_with_wait_for_response() TIMEOUT after "
                f"{poll_count * 0.2:.1f}s - no response received"
            )
            return {"code": -1, "msg": "Feed command timeout - no response"}

        final_response = response_container["response"] or {"code": -1, "msg": "No response"}
        logging.info(
            f"ACE[{self.instance_num}]: feed_filament_with_wait_for_response() completed -> "
            f"slot={slot}, length={length}mm, result_code={final_response.get('code')}"
        )

        return final_response

    def _wait_for_condition(self, condition_fn, timeout_s, poll_interval=0.02):
        """
        Poll condition_fn until it returns True or timeout expires.

        Returns:
            float | None: Time (s) until condition met, or None on timeout
        """
        start = self.reactor.monotonic()
        deadline = start + timeout_s

        while self.reactor.monotonic() < deadline:
            if condition_fn():
                return self.reactor.monotonic() - start
            self.dwell(poll_interval)

        return None

    def dwell(self, delay=1.0, verbose=False):
        """Sleep in reactor time."""
        self._check_abort()
        start_time = self.reactor.monotonic()
        currTs = self.reactor.monotonic()
        self.reactor.pause(currTs + delay)
        actual_delay = self.reactor.monotonic() - start_time

        if verbose and delay > 0.5:  # Only log significant delays
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Dwell complete: "
                f"requested={delay:.2f}s, actual={actual_delay:.2f}s"
            )

    def _extruder_move(self, length, speed, wait_for_move_end=False):
        """Move extruder (relative) via motion planner, synchronously."""
        if length == 0:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: _extruder_move() -> Skipping zero-length move"
            )
            return

        if length > 0 and not getattr(self, "toolhead_extrusion_allowed", False):
            if not getattr(self, "_extrusion_suppressed_logged", False):
                self._extrusion_suppressed_logged = True
                self.gcode.respond_info(
                    "ACE: Toolhead extrusion is disabled (toolhead_extrusion_allowed=False); skipping extruder move"
                )
            return

        toolhead = self.printer.lookup_object('toolhead')
        cur_pos = list(toolhead.get_position())  # [X, Y, Z, E]

        new_pos = cur_pos[:]
        new_pos[3] += length

        toolhead.move(new_pos, speed)
        if wait_for_move_end:
            toolhead.wait_moves()

    def reset_feed_assist_state(self):
        """Reset feed assist state."""
        if self._feed_assist_index != -1:
            self._feed_assist_index = -1
            self.state.set(
                f"ace_feed_assist_index_{self.instance_num}",
                -1
            )

            self.gcode.respond_info(f"ACE[{self.instance_num}]: Feed assist state reset")
