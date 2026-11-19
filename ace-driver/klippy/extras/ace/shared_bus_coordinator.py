import logging

from .ace2_bus import Ace2BusSession
from .serial_manager import AceSerialManager


class SharedBusCoordinator:
    """Coordinates ACE2 shared-bus discovery, UID bindings, reconnects, and routing."""

    def __init__(self, manager):
        self.manager = manager

    def _get_instances_for_bus_session(self, bus_session):
        """Return logical instances that share one ACE2 bus session."""
        mgr = self.manager
        return [
            instance for instance in mgr.instances
            if getattr(instance, "bus_session", None) is bus_session
        ]

    def _get_shared_bus_bindings_varname(self, shared_instances):
        """Build stable persistent-state variable name for one ACE2 bus group."""
        instance_ids = "_".join(
            str(instance.instance_num)
            for instance in sorted(shared_instances, key=lambda item: item.instance_num)
        )
        return f"ace2_bus_bindings_{instance_ids}"

    def _load_shared_bus_bindings(self, bus_session, shared_instances):
        """Restore persisted ACE2 UID bindings for one shared bus group."""
        mgr = self.manager
        raw_mapping = mgr.state.get(
            mgr._get_shared_bus_bindings_varname(shared_instances),
            {},
        )
        normalized_mapping = {}
        for instance_num, uid_tuple in dict(raw_mapping or {}).items():
            try:
                normalized_instance = int(instance_num)
                uid1, uid2, uid3 = uid_tuple
                normalized_mapping[normalized_instance] = (
                    int(uid1),
                    int(uid2),
                    int(uid3),
                )
            except (TypeError, ValueError):
                continue
        bus_session.bind_persisted_instances(normalized_mapping)

    def _persist_shared_bus_bindings(self, bus_session, shared_instances):
        """Store current ACE2 UID bindings for one shared bus group."""
        mgr = self.manager
        mgr.state.set(
            mgr._get_shared_bus_bindings_varname(shared_instances),
            bus_session.export_bindings(),
        )

    def _get_shared_bus_ready_instances(self, bus_session):
        """Return shared-bus instances that currently have an assigned target device id."""
        mgr = self.manager
        ready_instances = []
        for instance in mgr._get_instances_for_bus_session(bus_session):
            device = bus_session.get_device_for_instance(instance.instance_num)
            if device is not None and device.device_id is not None:
                ready_instances.append(instance)
        return ready_instances

    def _cancel_shared_bus_retry(self, bus_session):
        """Cancel pending discovery retry timer for one shared bus."""
        mgr = self.manager
        bus_key = id(bus_session)
        timer = mgr._shared_bus_retry_timers.pop(bus_key, None)
        if timer is not None:
            try:
                mgr.reactor.unregister_timer(timer)
            except Exception:
                pass
        mgr._shared_bus_retry_delays.pop(bus_key, None)

    def _schedule_shared_bus_retry(self, bus_session, reason):
        """Schedule a bounded backoff retry for ACE2 shared-bus discovery."""
        mgr = self.manager
        bus_key = id(bus_session)
        if mgr._shared_bus_retry_timers.get(bus_key) is not None:
            return

        delay = mgr._shared_bus_retry_delays.get(bus_key, mgr._shared_bus_retry_min_delay)
        next_delay = min(delay * 2.0, mgr._shared_bus_retry_max_delay)
        mgr._shared_bus_retry_delays[bus_key] = next_delay

        shared_instances = mgr._get_instances_for_bus_session(bus_session)
        if not shared_instances:
            return
        lead_instance = sorted(shared_instances, key=lambda item: item.instance_num)[0]
        mgr.gcode.respond_info(
            f"ACE[{lead_instance.instance_num}]: ACE2 bus discovery retry in {delay:.1f}s ({reason})"
        )

        def _retry_callback(eventtime):
            mgr._shared_bus_retry_timers.pop(bus_key, None)
            shared = sorted(
                mgr._get_instances_for_bus_session(bus_session),
                key=lambda item: item.instance_num,
            )
            if not shared:
                return mgr.reactor.NEVER
            lead = shared[0]
            is_connected = getattr(lead.serial_mgr, "is_connected", None)
            if callable(is_connected):
                try:
                    if not is_connected():
                        return mgr.reactor.NEVER
                except Exception:
                    return mgr.reactor.NEVER
            mgr._on_shared_bus_connected(bus_session)
            return mgr.reactor.NEVER

        mgr._shared_bus_retry_timers[bus_key] = mgr.reactor.register_timer(
            _retry_callback,
            mgr.reactor.monotonic() + delay,
        )

    def _start_shared_bus_runtime(self, bus_session):
        """Start per-instance ACE2 status polling for one shared bus group."""
        mgr = self.manager
        for instance in mgr._get_shared_bus_ready_instances(bus_session):
            instance.request_shared_bus_info_refresh()
            instance.start_shared_bus_heartbeat()

    def _queue_shared_bus_instance_setup(self, bus_session):
        """Queue ACE2 startup setup commands for each logical instance on one bus."""
        mgr = self.manager
        shared_instances = sorted(
            mgr._get_instances_for_bus_session(bus_session),
            key=lambda item: item.instance_num,
        )
        for instance in shared_instances:
            device = bus_session.get_device_for_instance(instance.instance_num)
            if device is None or device.device_id is None:
                logging.info(
                    "ACE[%s]: skipping ACE2 setup requests until shared-bus device_id is assigned",
                    instance.instance_num,
                )
                continue

            protocol = getattr(instance, "protocol", None)
            if protocol is None:
                continue

            rfid_enable = bool(getattr(instance, "rfid_inventory_sync_enabled", True))
            requests = (
                protocol.build_debug_request(
                    "SET_RFID_ENABLE",
                    {"index": 0, "enable": rfid_enable},
                ),
                protocol.build_debug_request(
                    "SET_RFID_ENABLE",
                    {"index": 2, "enable": rfid_enable},
                ),
                protocol.build_debug_request(
                    "SET_FEED_CHECK",
                    {
                        "check_length": int(mgr.ace_config.get("ace2_feed_check_length", 110)),
                        "error_length": int(mgr.ace_config.get("ace2_feed_error_length", 100)),
                    },
                ),
            )

            for request in requests:
                try:
                    instance.send_high_prio_request(request, lambda response: response)
                except Exception as exc:
                    logging.warning(
                        "ACE[%s]: failed to queue ACE2 setup request %s: %s",
                        instance.instance_num,
                        request.get("command"),
                        exc,
                    )

    def _get_transport_last_connected_time(self, serial_mgr):
        """Return last successful connect timestamp for one serial manager."""
        get_status = getattr(serial_mgr, "get_connection_status", None)
        if not callable(get_status):
            return None

        try:
            status = get_status() or {}
            last_connected_time = status.get("last_connected_time")
            if isinstance(last_connected_time, (int, float)) and last_connected_time > 0:
                return float(last_connected_time)
        except Exception:
            return None
        return None

    def _monitor_transport_reconnects(self):
        """Keep reconnect timers alive and reinitialize shared buses after reconnect."""
        mgr = self.manager
        for instance in mgr._iter_unique_transport_instances():
            serial_mgr = getattr(instance, "serial_mgr", None)
            if serial_mgr is None:
                continue

            ensure_connect_timer = getattr(serial_mgr, "ensure_connect_timer", None)
            if callable(ensure_connect_timer):
                try:
                    ensure_connect_timer()
                except Exception as exc:
                    logging.debug(
                        "ACE[%s]: ensure_connect_timer failed: %s",
                        instance.instance_num,
                        exc,
                    )

            bus_session = getattr(instance, "bus_session", None)
            if bus_session is None:
                continue

            is_connected = getattr(serial_mgr, "is_connected", None)
            if not callable(is_connected):
                continue
            try:
                if not is_connected():
                    continue
            except Exception:
                continue

            last_connected_time = mgr._get_transport_last_connected_time(serial_mgr)
            if last_connected_time is None:
                continue

            bus_key = id(bus_session)
            if mgr._shared_bus_last_connected_time.get(bus_key) == last_connected_time:
                continue

            if getattr(serial_mgr, "_port", None):
                bus_session.port = serial_mgr._port
            mgr._shared_bus_last_connected_time[bus_key] = last_connected_time
            mgr._on_shared_bus_connected(bus_session)

    def _handle_shared_bus_unsolicited(self, bus_session, response):
        """Route unmatched ACE2 shared-bus responses to their logical instance."""
        mgr = self.manager
        device_id = response.get("device_id")
        if not device_id:
            return False

        device = bus_session.get_device_for_device_id(device_id)
        if device is None or device.logical_instance is None:
            return False

        for instance in mgr._get_instances_for_bus_session(bus_session):
            if instance.instance_num == device.logical_instance:
                return bool(instance.protocol.handle_bound_shared_bus_unsolicited(instance, response))

        return False

    def _send_shared_bus_request(self, instance, request, timeout_s=5.0):
        """Send one manager-owned request over shared ACE2 transport and wait for reply."""
        mgr = self.manager
        response_container = {"done": False, "response": None}

        def callback(response):
            response_container["response"] = response
            response_container["done"] = True

        if not instance.serial_mgr.send_high_prio_request(request, callback):
            return None

        timeout_at = mgr.reactor.monotonic() + timeout_s
        while not response_container["done"] and mgr.reactor.monotonic() < timeout_at:
            if mgr._shutdown_in_progress or mgr._printer_is_shutdown():
                break
            mgr.reactor.pause(mgr.reactor.monotonic() + 0.05)

        if not response_container["done"]:
            instance.serial_mgr.discard_request(callback)
        return response_container["response"]

    def _initialize_shared_bus_transport(self, instance):
        """Discover and assign ACE2 devices on a shared bus transport."""
        mgr = self.manager
        bus_session = getattr(instance, "bus_session", None)
        if bus_session is None:
            return 0

        shared_instances = mgr._get_instances_for_bus_session(bus_session)
        if not shared_instances:
            return 0

        bus_session.reset()
        mgr._load_shared_bus_bindings(bus_session, shared_instances)

        protocol = getattr(instance.serial_mgr, "protocol", None)
        if protocol is None:
            return 0

        discovered_devices = []
        for _ in range(len(shared_instances)):
            response = mgr._send_shared_bus_request(
                instance,
                protocol.build_discover_device_request(),
            )
            if not response or "result" not in response:
                break

            result = response["result"]
            device = bus_session.record_discovered_device(
                result.get("uid1", 0),
                result.get("uid2", 0),
                result.get("uid3", 0),
            )
            discovered_devices.append(device)

        if not discovered_devices:
            mgr.gcode.respond_info(
                f"ACE[{instance.instance_num}]: ACE2 discovery returned no devices on shared bus"
            )
            return 0

        discovered_identities = {device.identity for device in discovered_devices}
        ordered_instances = sorted(shared_instances, key=lambda item: item.instance_num)
        ordered_devices = [
            d for d in bus_session.iter_discovered_devices()
            if d.identity in discovered_identities
        ]
        for logical_instance, device in zip(ordered_instances, ordered_devices):
            if device.logical_instance is None:
                bus_session.bind_logical_instance(
                    logical_instance.instance_num,
                    device.identity.uid1,
                    device.identity.uid2,
                    device.identity.uid3,
                )

        # Assign bus IDs ONLY to devices physically discovered on this bus.
        # Persisted-but-offline UIDs keep their logical_instance mapping without
        # receiving a device_id or being treated as ready.
        for device in bus_session.build_assignment_plan(start_device_id=1):
            if device.identity not in discovered_identities:
                device.device_id = None
                continue
            response = mgr._send_shared_bus_request(
                instance,
                protocol.build_assign_device_id_request(
                    device.identity.uid1,
                    device.identity.uid2,
                    device.identity.uid3,
                    device.device_id,
                ),
            )
            if not response or response.get("code") != 0:
                device.device_id = None
                mgr.gcode.respond_info(
                    f"ACE[{instance.instance_num}]: ACE2 device-id assignment failed for UID={device.identity.uid_tuple}: {response}"
                )

        mgr._persist_shared_bus_bindings(bus_session, shared_instances)
        return len(mgr._get_shared_bus_ready_instances(bus_session))

    def _on_shared_bus_connected(self, bus_session):
        """Schedule ACE2 shared-bus discovery/init outside reactor timers."""
        mgr = self.manager
        if mgr._shutdown_in_progress or mgr._printer_is_shutdown():
            return
        bus_key = id(bus_session)
        if not hasattr(mgr, "_shared_bus_init_pending"):
            mgr._shared_bus_init_pending = set()
        if bus_key in mgr._shared_bus_init_pending:
            return
        mgr._shared_bus_init_pending.add(bus_key)

        def _run(_eventtime):
            mgr._shared_bus_init_pending.discard(bus_key)
            mgr._run_shared_bus_connected(bus_session)

        mgr.reactor.register_callback(_run)

    def _run_shared_bus_connected(self, bus_session):
        """Reinitialize and restart one ACE2 shared bus after connect."""
        mgr = self.manager
        if mgr._shutdown_in_progress or mgr._printer_is_shutdown():
            return
        shared_instances = mgr._get_instances_for_bus_session(bus_session)
        if not shared_instances:
            return

        instance = sorted(shared_instances, key=lambda item: item.instance_num)[0]
        is_connected = getattr(instance.serial_mgr, "is_connected", None)
        if callable(is_connected) and not is_connected():
            return
        if instance.serial_mgr._port:
            bus_session.port = instance.serial_mgr._port
        last_connected_time = mgr._get_transport_last_connected_time(instance.serial_mgr)
        if last_connected_time is not None:
            mgr._shared_bus_last_connected_time[id(bus_session)] = last_connected_time

        ready_count = mgr._initialize_shared_bus_transport(instance)
        if ready_count <= 0:
            mgr._schedule_shared_bus_retry(
                bus_session,
                "discovery returned no assignable devices",
            )
            return

        mgr._cancel_shared_bus_retry(bus_session)
        mgr._queue_shared_bus_instance_setup(bus_session)
        mgr._start_shared_bus_runtime(bus_session)

    def _build_shared_transport_kwargs(self, instance_num, instance_config, ace_enabled, protocol):
        """Create or reuse transport objects for protocols that share a physical bus."""
        mgr = self.manager
        transport_spec = protocol.get_transport_spec()
        if not transport_spec.shared_bus:
            return {}

        transport_key = (
            instance_config["active_protocol_name"],
            instance_config["baud"],
            transport_spec.port_description,
        )
        context = mgr._shared_transport_contexts.get(transport_key)
        if context is None:
            serial_mgr = AceSerialManager(
                mgr.gcode,
                mgr.reactor,
                instance_num,
                ace_enabled=ace_enabled,
                status_debug_logging=bool(instance_config.get("status_debug_logging", False)),
                supervision_enabled=bool(instance_config.get("ace_connection_supervision", True)),
                protocol=protocol,
            )
            bus_session = Ace2BusSession(port="", baud=instance_config["baud"])
            context = {
                "serial_mgr": serial_mgr,
                "bus_session": bus_session,
            }
            serial_mgr.set_on_connect_callback(
                lambda bus_session=bus_session: mgr._on_shared_bus_connected(bus_session)
            )
            serial_mgr.set_unsolicited_response_callback(
                lambda response, bus_session=bus_session: mgr._handle_shared_bus_unsolicited(
                    bus_session,
                    response,
                )
            )
            mgr._shared_transport_contexts[transport_key] = context

        return dict(context)
