"""ACE Pro status, stop-reconciliation and pending-command mixin for ``App``."""

from collections import deque

from .app_models import ACE_RESULT_WAIT_SEC, ACE_STOP_BACKUP_SEC


class _AppAceMixin:
    ACE_ACTIVE_SLOT_STATES = frozenset({
        "feeding", "unwinding", "shifting", "preload", "identifying", "searching", "busy",
    })

    def handle_ace_response(self, unique_id, message=None, is_error=False):
        """ERR-17: Handle response for an ACE command by unique_id, removing ONLY that entry from ace_pending."""
        with self._lock:
            found = None
            for item in self.ace_pending:
                if item.get("unique_id") == unique_id or item.get("id") == unique_id or item == unique_id:
                    found = item
                    break
            if found is not None:
                self.ace_pending.remove(found)
                stop_kind = found.get("stop_kind")
                if stop_kind and self.ace_stopping and self.ace_stopping[0] == stop_kind:
                    if is_error or (message and "stop" in str(message).lower()):
                        self.ace_stopping = None
                        self._fast_poll_until = 0.0
                self.dirty = True
                return found
            return None

    def _finish_ace_pending_timeout(self):
        """The driver said nothing about the ACE command: show the command's own result text."""
        expired = []
        now = self._clock()
        with self._lock:
            remaining = deque() if isinstance(self.ace_pending, deque) else []
            for item in list(self.ace_pending):
                if now >= item.get("deadline", 0.0):
                    expired.append(item)
                else:
                    remaining.append(item)
            self.ace_pending = remaining
            self._ace_pending_queue = self.ace_pending
        for pend in expired:
            if pend.get("done"):
                self.notify(pend["done"], 3, "active", record=False)
        return bool(expired)

    def ace_stopping_kind(self):
        with self._lock:
            if not self.ace_stopping:
                return None
            kind, deadline = self.ace_stopping
            if self._clock() >= deadline:
                self.ace_stopping = None
                return None
            return kind

    @property
    def ace_slots(self):
        if hasattr(self, "_active_reconcile_slots") and self._active_reconcile_slots is not None:
            return self._active_reconcile_slots
        if hasattr(self, "_custom_ace_slots") and self._custom_ace_slots is not None:
            return self._custom_ace_slots
        ace = (self.state.status or {}).get("ace_instance_0") or {}
        return (ace.get("slots") or ()) if isinstance(ace, dict) else ()

    @ace_slots.setter
    def ace_slots(self, value):
        self._custom_ace_slots = value

    def _update_dryer_cooling_locked(self, status=None):
        st_dict = self.state.status if status is None else status
        if not isinstance(st_dict, dict):
            return
        ace = st_dict.get("ace_instance_0")
        if not isinstance(ace, dict):
            return
        conn = str(ace.get("connection_state") or "").strip().lower()
        if conn and conn != "connected":
            return
        dry = ace.get("dryer_status")
        cur = str(dry.get("status") or "").strip().lower() if isinstance(dry, dict) else ""
        prev = self._prev_dryer_status
        self._prev_dryer_status = cur
        if cur == "drying":
            self._dryer_stopped_ts = None
        elif prev == "drying" and cur != "drying":
            self._dryer_stopped_ts = self._clock()

    def _reconcile_ace_stopping_locked(self, status):
        self._update_dryer_cooling_locked(status)
        if not self.ace_stopping:
            return
        kind, deadline = self.ace_stopping
        if self._clock() >= deadline:
            self.ace_stopping = None
            return
        ace = (status or {}).get("ace_instance_0") or {}
        if not isinstance(ace, dict) or str(ace.get("connection_state") or "").strip().lower() != "connected":
            self.ace_stopping = None
            self._fast_poll_until = 0.0
            return
        if isinstance(ace, dict) and "slots" in ace:
            self._active_reconcile_slots = ace.get("slots") or ()
        else:
            self._active_reconcile_slots = None
        try:
            done = False
            if kind == "drying":
                dry = ace.get("dryer_status") or {}
                st = str(dry.get("status") or "").strip().lower()
                done = st in ("", "stop", "stopped", "idle", "off")
            elif kind == "assist":
                slot = ace.get("feed_assist_slot")
                try:
                    done = slot is None or int(slot) < 0
                except (TypeError, ValueError):
                    done = True
            elif kind == "feed":
                done = True
                for slot in self.ace_slots:
                    if slot is None:
                        continue
                    if isinstance(slot, dict) and str(slot.get("status") or "").strip().lower() == "feeding":
                        done = False
                        break
            elif kind == "retract":
                done = True
                for slot in self.ace_slots:
                    if slot is None:
                        continue
                    if isinstance(slot, dict) and str(slot.get("status") or "").strip().lower() == "unwinding":
                        done = False
                        break
            else:
                ace_st = str(ace.get("status") or "ready").strip().lower()
                done = ace_st == "ready"
                if done:
                    for slot in self.ace_slots:
                        if slot is None:
                            continue
                        if isinstance(slot, dict) and str(slot.get("status") or "").strip().lower() in self.ACE_ACTIVE_SLOT_STATES:
                            done = False
                            break
            if done:
                self.ace_stopping = None
                self._fast_poll_until = 0.0
                if kind == "drying" and self._dryer_stopped_ts is None:
                    self._dryer_stopped_ts = self._clock()
        finally:
            self._active_reconcile_slots = None

    @staticmethod
    def _classify_ace_stop(script):
        upper = str(script or "").upper()
        if "ACE_STOP_DRYING" in upper:
            return "drying"
        if "ACE_STOP_FEED" in upper:
            return "feed"
        if "ACE_STOP_RETRACT" in upper:
            return "retract"
        if "ACE_DISABLE_FEED_ASSIST" in upper:
            return "assist"
        return None

    def ace_data(self):
        with self._lock:
            raw = self.state.status.get("ace_instance_0")
            return raw if isinstance(raw, dict) and raw else {}

    def ace_connection_state(self):
        return str(self.ace_data().get("connection_state") or "").strip().lower()

    @property
    def ace_driver_present(self):
        return bool(self.ace_data())

    @property
    def ace_connected(self):
        ace = self.ace_data()
        return bool(ace) and str(ace.get("connection_state") or "").strip().lower() == "connected"

    def ace_active_slot_ops(self):
        ace = self.ace_data()
        if not ace or str(ace.get("connection_state") or "").strip().lower() != "connected":
            return []
        ops = []
        for s in (ace.get("slots") or ()):
            if not isinstance(s, dict):
                continue
            st = str(s.get("status") or "").strip().lower()
            if st in self.ACE_ACTIVE_SLOT_STATES:
                ops.append((s, st))
        return ops

    def ace_busy(self):
        if not self.ace_connected:
            return False
        ace = self.ace_data()
        ace_st = str(ace.get("status") or "ready").strip().lower()
        return ace_st != "ready" or bool(self.ace_active_slot_ops())

    def ace_gcode(self, script, done=None, on_error=None, require_connected=True):
        """Send an ACE G-code command only when Klipper is ready and ACE connection checks pass."""
        if not self.ready:
            self.notify(self.tr("Klipper hazır değil"))
            return False
        if require_connected:
            if not self.ace_connected:
                self.notify(self.tr("ACE Pro bağlı değil"))
                return False
        elif not self.ace_driver_present:
            self.notify(self.tr("ACE Pro bağlı değil"))
            return False
        stop_kind = self._classify_ace_stop(script)
        now = self._clock()
        self._ace_cmd_counter = getattr(self, "_ace_cmd_counter", 0) + 1
        cmd_id = self._ace_cmd_counter
        entry = {"id": cmd_id, "unique_id": cmd_id, "script": script, "done": done, "wall": self._wall_clock(),
                 "deadline": now + ACE_RESULT_WAIT_SEC, "stop_kind": stop_kind}
        with self._lock:
            if not hasattr(self, "ace_pending") or not isinstance(self.ace_pending, (list, deque)):
                self.ace_pending = deque()
            self.ace_pending.append(entry)
            self._ace_pending_queue = self.ace_pending
            if stop_kind is not None:
                # The window ends when the ACE status confirms the stop (see _reconcile_ace_stopping_locked) or
                # when the driver answers; this long timeout is only the backup.
                self.ace_stopping = (stop_kind, now + ACE_STOP_BACKUP_SEC)
            self._fast_poll_until = max(self._fast_poll_until, now + 3.0)
            self.dirty = True
        self._poll_wakeup.set()

        def failed(err):
            self.handle_ace_response(cmd_id, message=err, is_error=True)
            if on_error:
                on_error(err)
            else:
                synced = self._sync_gcode_errors()
                self.notify(self.tr_error(err), record=not bool(synced))

        # No immediate "done" toast: Moonraker answers 200 even when the driver refuses ("!! ACE[n]: ...").
        # The toast comes from the driver's answer, or from `done` after ACE_RESULT_WAIT_SEC of silence.
        sent = self.gcode(script, None, failed, urgent=stop_kind is not None)
        if not sent:
            self.handle_ace_response(cmd_id, is_error=True)
        return sent
