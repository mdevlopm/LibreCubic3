"""
Centralised persistent variable access for the ACE Pro module.

Wraps Klipper's ``save_variables`` object so that every read, in-memory
write and persist-to-disk operation goes through a single gateway.

**Deferred-flush strategy (Option A)**

``set()`` updates RAM and marks the variable *dirty* for later flushing.
``set_and_save()`` updates RAM and — depending on *persistence_mode* —
either flushes to disk immediately or defers the write like ``set()``.

Use ``set()`` in time-critical paths (toolchanges, mid-print callbacks)
where blocking Klipper's single-threaded reactor with synchronous
``configparser.write()`` must be avoided.  Call ``flush()`` at a safe
moment (print end, disconnect) to persist all dirty variables.

Persistence modes (set via ``persistence_mode`` in printer.cfg):

- ``deferred`` *(default)*: ``set_and_save()`` behaves like ``set()`` —
  RAM + dirty mark only.  Disk writes happen only in ``flush()``.
  Never blocks the reactor mid-print.
- ``immediate``: ``set_and_save()`` writes to disk right away (legacy).
  Use when you want key state persisted even without a clean shutdown.

Typical usage::

    state = PersistentState(printer, gcode)

    # Read (always fresh from Klipper)
    tool = state.get("ace_current_index", -1)

    # In-memory + marked dirty (disk write deferred until flush)
    state.set("ace_filament_pos", "bowden")

    # In-memory + immediate disk write (for user-facing commands)
    state.set_and_save("ace_current_index", 2)

    # Persist all dirty variables from prior set() calls
    state.flush()
"""

import configparser
import logging
import os
import shlex


class PersistentState:
    """
    Single access point for Klipper ``save_variables``.

    Every component (AceManager, AceInstance, commands, RunoutMonitor,
    EndlessSpool) should use this instead of touching
    ``printer.lookup_object("save_variables")`` directly.
    """

    def __init__(self, printer, gcode, persistence_mode="deferred"):
        """
        Args:
            printer:          Klipper printer object
            gcode:            Klipper gcode object (needed for SAVE_VARIABLE commands)
            persistence_mode: ``"deferred"`` (default) or ``"immediate"``.
                              Controls whether ``set_and_save()`` writes to disk
                              right away or defers to the next ``flush()`` call.
        """
        self.printer = printer
        self.gcode = gcode
        self._immediate = (persistence_mode == "immediate")
        self._dirty = set()  # variable names awaiting disk flush
        self._flush_failed = set()  # names whose last flush failed (log once)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _variables(self):
        """Return the live ``allVariables`` dict reference.

        This is intentionally a method (not a cached property) so that
        callers always see the latest dict even after Klipper reloads
        the save-variables file.
        """
        save_vars = self.printer.lookup_object("save_variables")
        return save_vars.allVariables

    PERCENT_REPLACEMENT = "\uff05"  # FULLWIDTH PERCENT SIGN

    @classmethod
    def _sanitize(cls, value):
        """Return ``value`` with every ``%`` in strings replaced by U+FF05.

        Klipper's save_variables cannot hold a ``%`` at all: it writes the
        file through ``ConfigParser()`` (basic interpolation), where a lone
        ``%`` raises ``ValueError`` - unhandled inside ``SAVE_VARIABLE``, i.e.
        a Klipper shutdown - and escaping it as ``%%`` only moves the problem:
        after the reload the dict holds a single ``%`` again and the NEXT
        ``SAVE_VARIABLE`` of ANY variable crashes. So no ``%`` may ever reach
        Klipper's variable dict (docs/32 C-04).
        """
        if isinstance(value, str):
            return value.replace("%", cls.PERCENT_REPLACEMENT)
        if isinstance(value, dict):
            return {cls._sanitize(k): cls._sanitize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [cls._sanitize(v) for v in value]
        if isinstance(value, tuple):
            return tuple(cls._sanitize(v) for v in value)
        return value

    @classmethod
    def _clean(cls, value):
        """``value`` itself if it has no ``%`` (keeps object aliasing), else a
        sanitized copy."""
        if "%" in repr(value):
            return cls._sanitize(value)
        return value

    @classmethod
    def _format_value(cls, value):
        """Render ``value`` as the single G-code token for ``SAVE_VARIABLE``.

        Klipper evaluates ``VALUE`` with ``ast.literal_eval`` after shell-style
        (``shlex``) splitting, so ``repr()`` + ``shlex.quote()`` round-trips any
        literal (the old ``json.dumps().replace("true", ...)`` rewrote text such
        as ``true_pla`` and broke on a single quote). Two traps of the Klipper
        side is handled on top:

        * ``%``: see :meth:`_sanitize` (replaced, never sent).

        (``;`` and ``#`` inside the quoted value are fine: Klipper takes the
        parameters from the original command line, and shlex keeps them inside
        quotes.)
        """
        text = repr(cls._sanitize(value))
        return shlex.quote(text)

    def _scrub_percent(self):
        """Remove any ``%`` that got into Klipper's variable dict.

        Covers live objects stored by reference (``set()`` of an object that
        later gained a ``%``) and files written by older versions.
        """
        try:
            variables = self._variables()
            for name in list(variables):
                if "%" in repr(variables[name]):
                    variables[name] = self._sanitize(variables[name])
        except Exception:
            logging.exception("ACE: could not scrub '%' from saved variables")

    def _write_to_disk(self, varname, value):
        """Issue one ``SAVE_VARIABLE`` gcode command for *varname*."""
        # Klipper rewrites its WHOLE dict on every SAVE_VARIABLE: a '%' in any
        # variable would crash this one too.
        self._scrub_percent()
        self.gcode.run_script_from_command(
            f"SAVE_VARIABLE VARIABLE={varname} VALUE={self._format_value(value)}"
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, varname, default=None):
        """Read a single variable (always fresh).

        Args:
            varname: Variable name.
            default: Returned when *varname* does not exist.

        Returns:
            The stored value, or *default*.
        """
        return self._variables().get(varname, default)

    def get_all(self):
        """Return the full variables dict (live reference)."""
        return self._variables()

    # ------------------------------------------------------------------
    # Write — in-memory only
    # ------------------------------------------------------------------

    def set(self, varname, value):
        """Update a variable **in memory** and mark it dirty.

        The variable is added to the dirty set and will be flushed
        to disk on the next ``flush()`` call.  No disk I/O happens
        here, keeping the Klipper reactor unblocked during
        time-critical paths such as toolchanges.

        Args:
            varname: Variable name.
            value:   Any value.
        """
        self._variables()[varname] = self._clean(value)
        self._dirty.add(varname)

    # ------------------------------------------------------------------
    # Write — in-memory + deferred disk persistence
    # ------------------------------------------------------------------

    def set_and_save(self, varname, value):
        """Update a variable in memory and persist according to the mode.

        - ``immediate`` mode: writes to disk right away via ``SAVE_VARIABLE``.
        - ``deferred`` mode: behaves like ``set()`` — RAM update + dirty mark
          only; the actual disk write is deferred to the next ``flush()``.

        For unconditionally time-critical paths, use ``set()`` directly.

        Args:
            varname: Variable name.
            value:   Any JSON-serialisable value.
        """
        value = self._clean(value)
        self._variables()[varname] = value
        if self._immediate:
            self._dirty.discard(varname)  # no longer dirty — writing now
            self._write_to_disk(varname, value)
        else:
            self._dirty.add(varname)  # deferred — will be flushed later

    # ------------------------------------------------------------------
    # Flush — persist all dirty variables to disk
    # ------------------------------------------------------------------

    @property
    def has_pending(self):
        """``True`` when there are dirty variables awaiting disk flush."""
        return bool(self._dirty)

    def flush(self):
        """Write every dirty variable to ``saved_variables.cfg``.

        Each variable is written via a ``SAVE_VARIABLE`` gcode command.
        The dirty set is cleared after all writes complete.

        Safe to call when nothing is dirty — it simply returns.
        """
        if not self._dirty:
            return

        for varname in list(self._dirty):
            # re-read each time: SAVE_VARIABLE replaces Klipper's dict object
            value = self._variables().get(varname)
            try:
                self._write_to_disk(varname, value)
            except Exception:
                # Keep it dirty so it is retried; log only the first failure
                if varname not in self._flush_failed:
                    self._flush_failed.add(varname)
                    logging.exception("ACE: Failed to flush variable %s", varname)
            else:
                self._dirty.discard(varname)
                self._flush_failed.discard(varname)

    def flush_direct(self):
        """Write ALL in-memory variables directly to ``saved_variables.cfg``.

        Bypasses the GCode queue entirely — safe to call during shutdown or
        emergency stop when ``run_script_from_command`` is unavailable or
        would be rejected with "Printer is shutdown".

        Writes the complete ``allVariables`` dict (not just dirty entries) so
        that even ``set_and_save()`` calls whose queued ``SAVE_VARIABLE``
        command was interrupted mid-shutdown are still persisted.
        """
        try:
            save_vars = self.printer.lookup_object("save_variables")
            filename = save_vars.filename
            variables = save_vars.allVariables

            # interpolation=None for writing; values are sanitized because
            # Klipper reads this file with a basic-interpolation ConfigParser
            # (a '%' would make the next Klipper start fail).
            cfg = configparser.ConfigParser(interpolation=None)
            cfg.add_section("Variables")
            for name, val in sorted(variables.items()):
                cfg.set("Variables", name, repr(self._sanitize(val)))

            # Atomic replace: a power cut mid-write must never leave a
            # truncated saved_variables.cfg (Klipper then refuses to start).
            tmp_path = f"{filename}.tmp.{os.getpid()}"
            try:
                with open(tmp_path, "w") as fh:
                    cfg.write(fh)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp_path, filename)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

            self._dirty.clear()
            logging.info(
                "ACE: flush_direct wrote %d variable(s) to %s",
                len(variables), filename
            )
        except Exception:
            logging.exception("ACE: flush_direct failed")
