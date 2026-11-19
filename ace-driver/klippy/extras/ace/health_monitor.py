import logging

from .klipper_utils import pause_print_from_event
from .reporting import emit_error


class ConnectionHealthMonitor:
    """Monitors ACE support pin state, serial connection health, and idle state flushing."""

    def __init__(self, manager):
        self.manager = manager

    def is_ace_enabled(self):
        """Check if ACE Pro unit is enabled via output pin."""
        mgr = self.manager
        try:
            # Get pin status from Klipper
            status = mgr.ace_pin.get_status(mgr.reactor.monotonic())
            return bool(status.get("value", 0))
        except Exception as e:
            mgr.gcode.respond_info(f"ACE: Error reading ACE_Pro pin: {e}")
            return False

    def update_ace_support_active_state(self):
        """
        Update ACE support state based on ACE_Pro pin.

        Also propagates enable/disable state to all serial managers
        to control reconnection behavior.
        """
        mgr = self.manager
        if mgr._shutdown_in_progress or mgr._printer_is_shutdown():
            # A Klipper shutdown/E-stop is not a user decision to disable ACE:
            # never touch the persistent ace_global_enabled flag here.
            return

        if mgr._ace_pro_enabled and not mgr.is_ace_enabled():
            mgr._restore_sensors()
            mgr.set_ace_global_enabled(False)

            # Disable reconnection attempts in all serial managers
            for instance in mgr.instances:
                instance.serial_mgr.disable_ace_pro()

            mgr.gcode.respond_info(
                "ACE: ACE Pro disabled - Standard Klipper sensors restored"
            )
            mgr._ace_pro_enabled = False

        elif not mgr._ace_pro_enabled and mgr.is_ace_enabled():
            mgr._setup_sensors()
            mgr._disable_all_sensor_detection()
            mgr.set_ace_global_enabled(True)

            # Enable reconnection attempts in all serial managers
            for instance in mgr.instances:
                instance.serial_mgr.enable_ace_pro()

            mgr.gcode.respond_info(
                "ACE: ACE Pro enabled - ACE runout monitoring active"
            )
            mgr._ace_pro_enabled = True

    def _monitor_ace_state(self, eventtime):
        """
        Monitor ACE Pro enable/disable state and connection health (2 second interval).

        Checks if ACE Pro unit is enabled/disabled via output pin and
        updates sensor state accordingly. Also monitors connection stability
        and pauses print if connection is unstable during printing.
        Flushes any pending dirty state to disk when idle.

        """
        mgr = self.manager
        try:
            mgr.update_ace_support_active_state()
            mgr._monitor_transport_reconnects()

            # Check connection health for all instances (if supervision enabled)
            if mgr._ace_pro_enabled and mgr._connection_supervision_enabled:
                mgr._check_connection_health(eventtime)

            # Safety net: flush deferred state to disk when not printing.
            # This catches any gcode command that used set() without a
            # matching flush() — the dirty vars will be persisted within
            # 2 seconds of the command completing.
            mgr._flush_if_idle(eventtime)

        except Exception as e:
            mgr.gcode.respond_info(f"ACE: Error in ACE state monitor: {e}")

        # Return next check time (2 seconds)
        return eventtime + 2.0

    def _flush_if_idle(self, eventtime):
        """Flush dirty persistent state to disk when the printer is idle.

        Called from the 2-second monitor timer.  During a print the
        explicit flush points (print-end, disconnect) are responsible;
        this method only acts as a safety net for idle / standalone
        commands that left variables dirty.
        """
        mgr = self.manager
        if not mgr.state.has_pending:
            return

        # Check whether a print is in progress — if so, leave dirty
        # state alone so we don't block the reactor with disk I/O
        # mid-print.  print-end / disconnect will handle it.
        print_stats = mgr.printer.lookup_object("print_stats", None)
        if print_stats:
            try:
                stats = print_stats.get_status(eventtime)
                state = (stats.get("state") or "").lower()
                if state in ("printing", "paused"):
                    return
            except Exception:
                pass

        try:
            mgr.state.flush()
        except Exception:
            logging.exception("ACE: Idle flush failed")

    def _check_connection_health(self, eventtime):
        """
        Check connection stability for all ACE instances.

        If any instance has an unstable connection:
        - During printing: Pause print and show dialog with resume/cancel
        - When idle: Show informational dialog
        """
        mgr = self.manager
        unstable_instances = []

        for instance in mgr.instances:
            status = instance.serial_mgr.get_connection_status()
            instance_num = instance.instance_num

            # Track if connection state changed
            prev_status = mgr._last_connection_status.get(instance_num, {})
            was_stable = prev_status.get("stable", True)
            is_stable = status["stable"]

            mgr._last_connection_status[instance_num] = status

            # Detect instability - only flag as unstable when reconnect threshold exceeded
            # This avoids false alarms for brief disconnects that quickly recover
            reconnect_threshold = instance.serial_mgr.INSTABILITY_THRESHOLD

            if status["recent_reconnects"] >= reconnect_threshold:
                unstable_instances.append({
                    "instance": instance_num,
                    "connected": status["connected"],
                    "recent_reconnects": status["recent_reconnects"],
                    "time_connected": status["time_connected"],
                })

            # Log when connection becomes stable again
            if is_stable and not was_stable and prev_status:
                mgr.gcode.respond_info(
                    f"ACE[{instance_num}]: Connection stabilized "
                    f"(connected for {status['time_connected']:.0f}s)"
                )
                # Clear dialog if all instances are now stable
                if mgr._connection_issue_shown:
                    all_stable = all(
                        mgr._last_connection_status.get(i.instance_num, {}).get("stable", True)
                        for i in mgr.instances
                    )
                    if all_stable:
                        mgr._close_connection_dialog()
                        mgr._connection_issue_shown = False

        # If we have unstable instances and haven't shown dialog yet
        if unstable_instances and not mgr._connection_issue_shown:
            mgr._handle_connection_issue(unstable_instances, eventtime)

    def _handle_connection_issue(self, unstable_instances, eventtime):
        """
        Handle detected connection issues.

        Args:
            unstable_instances: List of dicts with instance connection info
            eventtime: Current event time
        """
        mgr = self.manager
        # Check if we're printing
        print_stats = mgr.printer.lookup_object("print_stats", None)
        is_printing = False
        if print_stats:
            try:
                stats = print_stats.get_status(eventtime)
                state = (stats.get("state") or "").lower()
                is_printing = state in ["printing", "paused"]
            except Exception:
                pass

        # Build message
        instance_details = []
        for info in unstable_instances:
            if not info["connected"]:
                status = "disconnected"
            elif info["recent_reconnects"] >= 3:
                status = f"unstable ({info['recent_reconnects']} reconnects in 60s)"
            else:
                status = f"stabilizing ({info['time_connected']:.0f}s connected)"
            instance_details.append(f"ACE {info['instance']}: {status}")

        details_str = ", ".join(instance_details)

        if is_printing:
            # Pause print and show dialog with resume/cancel
            emit_error(
                mgr.gcode,
                f"ACE: Connection issue detected during print - {details_str}"
            )
            mgr._pause_for_connection_issue(unstable_instances)
        else:
            # Just show informational dialog
            emit_error(
                mgr.gcode,
                f"ACE: Connection issue detected - {details_str}"
            )
            mgr._show_connection_issue_dialog(unstable_instances, is_printing=False)

        mgr._connection_issue_shown = True

    def _run_gcode_from_event(self, script):
        """Run a G-code script safely from a reactor callback greenlet."""
        mgr = self.manager
        runner = getattr(mgr.gcode, "run_script", None) or mgr.gcode.run_script_from_command
        runner(script)

    def _pause_for_connection_issue(self, unstable_instances):
        """Pause print due to ACE connection issue (deferred out of timer)."""
        mgr = self.manager

        def _do_pause(_eventtime):
            try:
                mgr.gcode.respond_info("ACE: Pausing print due to connection issue")
                pause_print_from_event(mgr.printer, mgr.gcode)
            except Exception as e:
                emit_error(mgr.gcode, f"ACE: Error pausing print: {e}")

            mgr._show_connection_issue_dialog(unstable_instances, is_printing=True)

        mgr.reactor.register_callback(_do_pause)

    def _show_connection_issue_dialog(self, unstable_instances, is_printing):
        """
        Show Mainsail dialog for connection issue.

        Args:
            unstable_instances: List of instances with connection issues
            is_printing: If True, show resume/cancel buttons; if False, just info
        """
        mgr = self.manager
        mgr._run_gcode_from_event(
            'RESPOND TYPE=command MSG="action:prompt_begin ACE Connection Issue"'
        )

        # Build instance details
        instance_details = []
        for info in unstable_instances:
            if not info["connected"]:
                status = "disconnected"
            elif info["recent_reconnects"] >= 3:
                status = f"unstable ({info['recent_reconnects']} reconnects/min)"
            else:
                status = f"stabilizing ({info['time_connected']:.0f}s)"
            instance_details.append(f"ACE {info['instance']}: {status}")

        if is_printing:
            prompt_text = (
                f"Print paused: ACE connection unstable. {' | '.join(instance_details)}. "
                f"Please fix the issue, then use RESUME to continue or CANCEL_PRINT to abort."
            )
        else:
            prompt_text = (
                f"ACE connection issue detected. {' | '.join(instance_details)}. "
                f"Please check connections and verify ACE unit is powered on."
            )

        mgr._run_gcode_from_event(
            f'RESPOND TYPE=command MSG="action:prompt_text {prompt_text}"'
        )

        # Just a dismiss button for all cases
        mgr._run_gcode_from_event(
            'RESPOND TYPE=command MSG="action:prompt_footer_button Dismiss|'
            'RESPOND TYPE=command MSG=action:prompt_end|secondary"'
        )

        mgr._run_gcode_from_event(
            'RESPOND TYPE=command MSG="action:prompt_show"'
        )

    def _close_connection_dialog(self):
        """Close the connection issue dialog."""
        mgr = self.manager
        try:
            mgr._run_gcode_from_event(
                'RESPOND TYPE=command MSG="action:prompt_end"'
            )
            mgr.gcode.respond_info("ACE: Connection restored - dialog closed")
        except Exception as e:
            mgr.gcode.respond_info(f"ACE: Error closing dialog: {e}")
