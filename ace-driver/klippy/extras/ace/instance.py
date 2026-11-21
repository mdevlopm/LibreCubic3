"""
AceInstance: Manages a single physical ACE Pro unit.
"""

import logging
import time
import copy
from .config import (
    INSTANCE_MANAGERS,
    SLOTS_PER_ACE,
    get_tool_offset,
    create_inventory,
    create_status_dict,
)
from .protocol import create_protocol_adapter, normalize_protocol_name, resolve_protocol_name
from .serial_manager import AceSerialManager
from .reporting import emit_error
from .klipper_utils import pause_print_from_event
from .instance_status import InstanceStatusMixin
from .instance_motion import InstanceMotionMixin
from .instance_loading import InstanceLoadingMixin


class AceInstance(InstanceStatusMixin, InstanceMotionMixin, InstanceLoadingMixin):
    """Manages a single physical ACE Pro unit with 4 slots."""

    # Defaults for slots that report ready but provide no metadata
    DEFAULT_MATERIAL = "Unknown"
    DEFAULT_COLOR = [0, 0, 0]
    DEFAULT_TEMP = 0
    RFID_RETRY_DELAY_S = 10.0  # pause before re-reading a slot whose RFID read failed

    # Material temperature defaults (from RFID tags)
    MATERIAL_TEMPS = {
        "PLA": 200,
        "PLA+": 210,
        "PLA Glow": 210,
        "PLA High Speed": 215,
        "PLA Marble": 205,
        "PLA Matte": 205,
        "PLA SE": 210,
        "PLA Silk": 215,
        "ABS": 240,
        "ASA": 245,
        "PETG": 235,
        "TPU": 210,
        "PVA": 185,
        "HIPS": 230,
        "PC": 260,
    }

    def __init__(
        self,
        instance_num,
        ace_config,
        printer,
        ace_enabled=True,
        protocol=None,
        active_protocol_name=None,
        serial_mgr=None,
        bus_session=None,
    ):
        """
        Initialize ACE instance.

        Args:
            instance_num: Instance number (0, 1, 2, ...)
            ace_config: Configuration dict
            printer: Klipper printer object
            ace_enabled: Initial ACE Pro enabled state
        """
        self.variables = {}
        self.SLOT_COUNT = SLOTS_PER_ACE
        self.instance_num = instance_num
        self.ace_config = ace_config  # Store for later access (e.g., rfid_temp_mode)
        self.baud = ace_config["baud"]
        self.printer = printer
        self.reactor = printer.get_reactor()
        self.gcode = printer.lookup_object("gcode")
        self.timeout_multiplier = ace_config["timeout_multiplier"]
        self.filament_runout_sensor_name_rdm = ace_config["filament_runout_sensor_name_rdm"]
        self.filament_runout_sensor_name_nozzle = ace_config["filament_runout_sensor_name_nozzle"]
        self.feed_speed = float(ace_config["feed_speed"])
        self.retract_speed = float(ace_config["retract_speed"])
        self._config_feed_speed = self.feed_speed
        self._config_retract_speed = self.retract_speed
        self.toolhead_extrusion_allowed = bool(
            ace_config.get("toolhead_extrusion_allowed", False)
        )
        self.total_max_feeding_length = float(ace_config["total_max_feeding_length"])
        self.parkposition_to_toolhead_length = float(ace_config["parkposition_to_toolhead_length"])
        self.toolchange_load_length = float(ace_config["toolchange_load_length"])
        self.parkposition_to_rdm_length = float(ace_config["parkposition_to_rdm_length"])
        self.rdm_overshoot_length = float(ace_config["rdm_overshoot_length"])
        self.incremental_feeding_length = float(ace_config["incremental_feeding_length"])
        self.incremental_feeding_speed = float(ace_config["incremental_feeding_speed"])
        self.extruder_feeding_length = float(ace_config["extruder_feeding_length"])
        self.extruder_feeding_speed = float(ace_config["extruder_feeding_speed"])
        self.toolhead_slow_loading_speed = float(ace_config["toolhead_slow_loading_speed"])
        self.heartbeat_interval = float(ace_config["heartbeat_interval"])
        self.max_dryer_temperature = float(ace_config["max_dryer_temperature"])
        # Dryer fan speed sent with a "drying" request (unit VARSAYIM: RPM).
        # 0 (default) = omit the field, i.e. the ACE's own default.
        self.dryer_fan_speed = int(ace_config.get("dryer_fan_speed", 0))

        self.rfid_inventory_sync_enabled = ace_config.get("rfid_inventory_sync_enabled", True)
        self.feed_assist_active_after_ace_connect = ace_config.get(
            "feed_assist_active_after_ace_connect", True
        )

        # Not overridable per instance
        self.toolhead_full_purge_length = float(ace_config["toolhead_full_purge_length"])

        self.toolhead = None
        self._info = create_status_dict(self.SLOT_COUNT)
        self._cached_info_ref = None
        self._cached_info_copy = None
        self._info_time = None  # monotonic time of the last status reply
        self._rfid_retry_after = {}  # slot -> monotonic time before which no re-query
        self.inventory = create_inventory(self.SLOT_COUNT)
        self._feed_assist_index = -1
        self._feed_assist_topology_position = None  # Track chain position (0, 1, 2...)
        self._pending_feed_assist_restore = -1  # Slot to restore after first heartbeat
        self._feed_assist_restore_defers = 0
        self._pending_rfid_refresh = False  # Flag to refresh all RFID data after reconnect
        self._pending_reset_report = False  # Log the diagnostic image's reset cause once per connect
        self._last_retract_early_stopped = False  # Slot sensor confirmed empty during _retract() (or slot was already empty)
        self._dryer_error = None
        self._last_dryer_error_reported = None
        self._rfid_mode_sent = None  # 0/1 last enable_rfid state the ACE accepted
        self._rfid_mode_pending = False
        self._pending_rfid_queries = set()  # Track slots with in-flight RFID queries
        self.status_failure_threshold = max(
            1,
            int(ace_config.get("status_failure_threshold", 4)),
        )
        self._status_failure_streak = 0
        self._status_recovery_in_progress = False

        self.status_debug_logging = bool(ace_config.get("status_debug_logging", False))
        self.supervision_enabled = bool(ace_config.get("ace_connection_supervision", True))
        self.configured_protocol_name = normalize_protocol_name(
            ace_config.get("protocol", "auto")
        )
        self.protocol_name = active_protocol_name or resolve_protocol_name(self.configured_protocol_name)
        self.protocol = protocol or create_protocol_adapter(self.protocol_name)
        self.transport_spec = self.protocol.get_transport_spec()
        self.bus_session = bus_session

        self.serial_mgr = serial_mgr or AceSerialManager(
            self.gcode,
            self.reactor,
            instance_num,
            ace_enabled=ace_enabled,
            status_debug_logging=self.status_debug_logging,
            supervision_enabled=self.supervision_enabled,
            protocol=self.protocol,
        )
        self.serial_mgr.printer = self.printer
        # Baud used when ACE is re-enabled before any connection was made
        try:
            self.serial_mgr.default_baud = int(self.baud)
        except (TypeError, ValueError):
            pass
        # Link tuning from [ace] (defaults = stock gklib behaviour)
        self.serial_mgr.timeout_s = float(ace_config.get("serial_request_timeout", 1.0))
        self.serial_mgr.COMM_TIMEOUT_THRESHOLD = int(ace_config.get("comm_timeout_threshold", 5))
        self.tool_offset = get_tool_offset(self.instance_num)
        if not self.transport_spec.shared_bus:
            self.serial_mgr.set_heartbeat_callback(self._on_heartbeat_response)
        self.serial_mgr.set_on_connect_callback(self._on_ace_connect)
        self._shared_bus_heartbeat_timer = None
        self._shared_bus_hb_outstanding = False
        self._shared_bus_hb_sent_at = 0.0
        self._hb_error_reported = False
        self._rfid_error_reported = set()  # slots whose RFID failure was already shown

        # Tangle protection runtime state (policy lives in AceManager)
        self._tangle_latched = False
        self._tangle_event_count = 0
        self._last_tangle_event = None

    def _prepare_request(self, request):
        """Normalize request and attach ACE2 shared-bus target when known."""
        prepared_request = self.protocol.normalize_request(request)
        if not self.transport_spec.shared_bus or self.bus_session is None:
            return prepared_request

        command_name = str(prepared_request.get("command", "")).strip().upper()
        if command_name in {"DISCOVER_DEVICE", "ASSIGN_DEVICE_ID"}:
            return prepared_request

        device = self.bus_session.get_device_for_instance(self.instance_num)
        if device is None or device.device_id is None:
            raise RuntimeError(
                f"ACE[{self.instance_num}]: Shared-bus request '{command_name or 'UNKNOWN'}' "
                "requires an assigned target device_id"
            )

        prepared_request["target_device_id"] = device.device_id
        return prepared_request

    @property
    def manager(self):
        """Get the AceManager instance for this ACE unit."""
        return INSTANCE_MANAGERS.get(self.instance_num)

    @property
    def state(self):
        """Shortcut to the centralised :class:`PersistentState`."""
        return self.manager.state

    def _register_tool_macros(self):
        """Register T0-T3 (or T4-T7, etc.) macros for this instance."""
        try:
            for slot_idx in range(self.SLOT_COUNT):
                tool_num = self.tool_offset + slot_idx

                # Create closure to capture current slot_idx
                def make_tool_handler(idx):
                    def handler(gcmd):
                        gcmd.respond_info(
                            f"ACE: Tool change to T{tool_num} "
                            f"(slot {idx}, instance {self.instance_num})"
                        )
                    return handler

                desc = f"ACE tool macro - slot {slot_idx} of instance {self.instance_num}"
                self.gcode.register_command(f"T{tool_num}", make_tool_handler(slot_idx), desc=desc)

            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Registered tool macros "
                f"T{self.tool_offset}-T{self.tool_offset + self.SLOT_COUNT - 1}"
            )
        except Exception as e:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Failed to register tool macros: {e}"
            )

    def send_request(self, request, callback):
        """Queue a normal request."""
        return self.serial_mgr.send_request(
            self._prepare_request(request),
            callback,
        )

    def send_high_prio_request(self, request, callback):
        """Queue high-priority request."""
        return self.serial_mgr.send_high_prio_request(
            self._prepare_request(request),
            callback,
        )

    def send_urgent_request(self, request, callback):
        """Queue a stop-type request ahead of all waiting requests.

        If the ACE is not connected the stop cannot be delivered now: the
        serial manager remembers it and re-sends it after the next connect, and
        the user is told with a ``!!`` line (it used to vanish silently).
        """
        prepared = self._prepare_request(request)
        queued = self.serial_mgr.send_urgent_request(prepared, callback)
        if queued is False and not self.serial_mgr.is_shutdown():
            if self.serial_mgr.has_pending_stop(prepared):
                req_name = prepared.get("method") or prepared.get("command")
                self._report_error(
                    f"ACE[{self.instance_num}]: Stop request '{req_name}' not sent - "
                    f"ACE not connected; it will be sent when the ACE reconnects"
                )
            else:
                self._report_error(
                    f"ACE[{self.instance_num}]: Stop request not sent - ACE not connected "
                    f"(or disabled)"
                )
        return queued

    def _report_error(self, msg):
        """Send a ``!! ...`` console line (reaches the screen event log)."""
        emit_error(self.gcode, msg)

    def _send_shared_bus_heartbeat_request(self):
        """Send one targeted ACE2 status poll over shared transport."""
        if not self.transport_spec.shared_bus or not self.serial_mgr.is_connected():
            return
        if self.serial_mgr.is_shutdown():
            return
        # Same rule as the ACE1 heartbeat: no new poll while one is unanswered
        # (otherwise a silent bus fills the queue one status request per second).
        now = self.reactor.monotonic()
        if self._shared_bus_hb_outstanding and now - self._shared_bus_hb_sent_at < 10.0:
            return

        request = self.protocol.build_get_status_request()

        def _heartbeat_reply(response):
            self._shared_bus_hb_outstanding = False
            self._on_heartbeat_response(response)

        queued = self.send_high_prio_request(request, _heartbeat_reply)
        self._shared_bus_hb_outstanding = bool(queued)
        self._shared_bus_hb_sent_at = now

    def _shared_bus_heartbeat_tick(self, eventtime):
        """Periodically poll one ACE2 logical instance on shared transport."""
        try:
            self._send_shared_bus_heartbeat_request()
        except Exception as exc:
            logging.warning(
                "ACE[%s]: Shared-bus heartbeat error: %s",
                self.instance_num,
                exc,
            )
        return eventtime + self.heartbeat_interval

    def start_shared_bus_heartbeat(self):
        """Start or refresh shared-bus status polling after bus init completes."""
        if not self.transport_spec.shared_bus:
            return

        self._send_shared_bus_heartbeat_request()
        if self._shared_bus_heartbeat_timer is None:
            self._shared_bus_heartbeat_timer = self.reactor.register_timer(
                self._shared_bus_heartbeat_tick,
                self.reactor.NOW,
            )

    def request_shared_bus_info_refresh(self):
        """Refresh device info through targeted ACE2 get_info after bus init."""
        if not self.transport_spec.shared_bus or not self.serial_mgr.is_connected():
            return

        request = self.protocol.build_get_info_request()
        self.send_high_prio_request(request, self.serial_mgr.handle_info_response)

    def start_drying(self, temperature, duration, callback=None, fan_speed=None):
        """Start the ACE dryer using the active protocol adapter.

        ``fan_speed`` overrides the configured ``dryer_fan_speed`` (default 0 =
        field not sent).
        """
        if fan_speed is None:
            fan_speed = self.dryer_fan_speed
        request = self.protocol.build_start_drying_request(
            temperature, duration, fan_speed=fan_speed
        )
        self.send_request(request, callback or (lambda response: None))

    def stop_drying(self, callback=None):
        """Stop the ACE dryer using the active protocol adapter.

        Sent as an urgent request: it jumps ahead of queued requests and
        cancels a still-queued (unsent) start request.
        """
        request = self.protocol.build_stop_drying_request()
        self.send_urgent_request(request, callback or (lambda response: None))

    def set_fan_speed(self, fan_speed, callback=None):
        """Change the ACE fan speed (``set_fan_speed`` RPC, ACE1 only)."""
        request = self.protocol.build_set_fan_speed_request(int(fan_speed))
        self.send_request(request, callback or (lambda response: None))

    def sync_rfid_print_mode(self, printing):
        """RFID recognition off while printing, on otherwise.

        Same as stock gklib: start_print / resume disable it, pause / cancel /
        finish enable it. Sent once per change; a reconnect (the ACE boots with
        RFID on) clears ``_rfid_mode_sent`` so the state is sent again.
        """
        want = 0 if printing else 1
        if self._rfid_mode_pending or self._rfid_mode_sent == want:
            return
        if not self.serial_mgr.is_connected():
            return
        try:
            if printing:
                request = self.protocol.build_disable_rfid_request()
            else:
                request = self.protocol.build_enable_rfid_request()
        except NotImplementedError:
            self._rfid_mode_sent = want
            return
        self._rfid_mode_pending = True

        def _done(response):
            self._rfid_mode_pending = False
            if response and response.get("code") == 0:
                self._rfid_mode_sent = want
            else:
                logging.info(
                    "ACE[%s]: %s failed: %s", self.instance_num, request["method"],
                    response.get("msg") if response else "no response",
                )

        self.send_request(request, _done)

    def _tangle_policy(self):
        """Return ``(enabled, threshold_s)`` from the manager (off by default)."""
        manager = self.manager
        getter = getattr(manager, "get_tangle_protection", None)
        if not callable(getter):
            return False, 8.0
        try:
            policy = getter()
            return bool(policy.get("enabled", False)), float(policy.get("threshold", 8.0))
        except Exception:
            return False, 8.0

    def _is_printing(self):
        """True only while a print is actively running (not paused)."""
        print_stats = self.printer.lookup_object("print_stats", None)
        if not print_stats:
            return False
        try:
            stats = print_stats.get_status(self.reactor.monotonic())
            return (stats.get("state") or "").lower() == "printing"
        except Exception:
            return False

    def _check_tangle_protection(self):
        """Stock-gklib style tangle guard (docs/30 1.2 update_status 0x5cab38).

        The ACE1 reports ``cont_assist_time`` (seconds of uninterrupted
        feed-assist push). Stock gklib pauses the print when it reaches
        ``contassistTime_v1`` (8.0 s). Here it is opt-in: enabled and threshold
        come from the manager (config default OFF, runtime command
        ACE_TANGLE_PROTECTION). Triggers once per excursion: re-armed when the
        value falls below the threshold or the print is no longer running.
        """
        enabled, threshold = self._tangle_policy()
        raw = (self._info or {}).get("cont_assist_time")
        try:
            cont = float(raw)
        except (TypeError, ValueError):
            cont = None

        printing = self._is_printing()
        if not enabled or cont is None or cont < threshold or not (
                printing or self._is_printing_or_paused()):
            # Re-arm only when the value recovered, the feature was switched off
            # or the print ended. NOT while merely paused: the PAUSE we issued
            # must keep triggered=True for the UI, and a RESUME with the counter
            # still high must not immediately pause again.
            self._tangle_latched = False
            return False
        if not printing:
            return False  # paused: stay latched, do not trigger

        if self._tangle_latched:
            return False

        manager = self.manager
        if manager is not None and getattr(manager, "toolchange_in_progress", False):
            # Feed-assist is paused/reset around tool changes; do not interfere.
            return False
        if manager is not None and getattr(manager, "_shutdown_in_progress", False):
            return False

        self._tangle_latched = True
        self._tangle_event_count += 1
        self._last_tangle_event = {
            "time": time.time(),
            "cont_assist_time": cont,
            "threshold": threshold,
        }
        self._report_error(
            f"ACE[{self.instance_num}]: Filament tangle suspected: continuous "
            f"feed-assist {cont:.1f}s >= {threshold:.1f}s - pausing print"
        )
        # PAUSE blocks (toolhead moves): run it from its own greenlet instead of
        # inside the serial reader timer callback.
        self.reactor.register_callback(self._tangle_pause)
        return True

    def _tangle_pause(self, eventtime=None):
        """Issue PAUSE for a tangle event (runs in a reactor callback)."""
        try:
            pause_print_from_event(self.printer, self.gcode)
        except Exception as e:
            self._report_error(
                f"ACE[{self.instance_num}]: Could not pause print after tangle: {e}"
            )

    def get_tangle_status(self):
        """Tangle protection state for status/UI."""
        enabled, threshold = self._tangle_policy()
        return {
            "enabled": enabled,
            "threshold": threshold,
            "triggered": bool(self._tangle_latched),
            "event_count": self._tangle_event_count,
            "last_event": (
                dict(self._last_tangle_event)
                if isinstance(self._last_tangle_event, dict)
                else (copy.deepcopy(self._last_tangle_event) if self._last_tangle_event is not None else None)
            ),
        }

    def get_status(self, eventtime=None):
        """Return status dict for Klipper/Moonraker queries."""
        # Debug logging reserved for status_debug_logging; keep silent by default

        # Optimize get_status: avoid copy.deepcopy if self._info hasn't changed (ERR-24)
        if (
            getattr(self, "_cached_info_ref", None) is self._info
            and getattr(self, "_cached_info_copy", None) is not None
        ):
            status = self._cached_info_copy.copy()
        else:
            raw_info = self._info
            if isinstance(raw_info, dict) and "raw_fields" in raw_info:
                clean_info = {k: v for k, v in raw_info.items() if k != "raw_fields"}
            else:
                clean_info = raw_info
            if (
                getattr(self, "_cached_info_copy", None) is not None
                and clean_info == self._cached_info_copy
            ):
                self._cached_info_ref = self._info
                status = self._cached_info_copy.copy()
            else:
                self._cached_info_ref = self._info
                self._cached_info_copy = copy.deepcopy(clean_info)
                status = self._cached_info_copy.copy()
        status.pop("raw_fields", None)
        status["instance"] = self.instance_num
        status["protocol"] = self.protocol_name
        status["rfid_sync_enabled"] = bool(self.rfid_inventory_sync_enabled)
        status["feed_assist_slot"] = self._get_current_feed_assist_index()
        status["tangle_protection"] = self.get_tangle_status()
        status["dryer_error"] = self._dryer_error

        # Attach device info from last get_info response, if available
        device_info = getattr(self.serial_mgr, "device_info", {})
        if isinstance(device_info, dict):
            normalized_info = {}
            for key in ("model", "firmware", "boot_firmware", "structure_version"):
                if key in device_info and device_info[key] is not None:
                    normalized_info[key] = device_info[key]

            # ACE2 get_info currently reports version and boot_version keys.
            if "firmware" not in normalized_info and device_info.get("version"):
                normalized_info["firmware"] = device_info["version"]
            if "boot_firmware" not in normalized_info and device_info.get("boot_version"):
                normalized_info["boot_firmware"] = device_info["boot_version"]

            for key, value in normalized_info.items():
                status.setdefault(key, value)

        # Protocol-aware fallback label when the device does not report model metadata.
        if not status.get("model") and self.protocol_name == "ace2_proto":
            port_description = getattr(self.serial_mgr, "_port_description", None)
            if port_description:
                status["model"] = f"ACE2 ({port_description})"
            else:
                status["model"] = "ACE2 (Shared Bus)"
        # Attach connection info (port / usb path) for diagnostics
        port = getattr(self.serial_mgr, "serial_name", None) or getattr(self.serial_mgr, "_port", None)
        usb_path = getattr(self.serial_mgr, "_usb_location", None)
        if port:
            status.setdefault("usb_port", port)
        if usb_path:
            status.setdefault("usb_path", usb_path)

        # Expose UI-friendly slot inventory (material/color/temp/status/RFID metadata)
        slots_out = []
        for i in range(self.SLOT_COUNT):
            inv = self.inventory[i]
            slot_data = {
                "index": i,
                "tool": self.tool_offset + i,
                "status": inv.get("status"),
                "color": inv.get("color"),
                "material": inv.get("material"),
                "temp": inv.get("temp"),
                "rfid": inv.get("rfid", False),
            }
            for key in [
                "sku",
                "brand",
                "icon_type",
                "rgba",
                "extruder_temp",
                "hotbed_temp",
                "diameter",
                "total",
                "current",
            ]:
                if key in inv:
                    slot_data[key] = inv[key]
            slots_out.append(slot_data)

        status["slots"] = slots_out

        # Expose communication state machine for KlipperScreen connection indicator
        status["connection_state"] = getattr(self.serial_mgr, "connection_state", "unknown")

        # Expose speed and extrusion allowance
        mgr = self.manager
        if mgr is not None:
            status["toolhead_extrusion_allowed"] = bool(
                getattr(mgr, "toolhead_extrusion_allowed", False)
            )
            status["feed_speed"] = (
                mgr.feed_speed_override
                if getattr(mgr, "feed_speed_override", None) is not None
                else self.feed_speed
            )
            status["retract_speed"] = (
                mgr.retract_speed_override
                if getattr(mgr, "retract_speed_override", None) is not None
                else self.retract_speed
            )
        else:
            status["toolhead_extrusion_allowed"] = bool(
                getattr(self, "toolhead_extrusion_allowed", False)
            )
            status["feed_speed"] = self.feed_speed
            status["retract_speed"] = self.retract_speed

        return status
