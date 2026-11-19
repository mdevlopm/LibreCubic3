"""Test doubles for the ACE driver: fake pyserial and fake Klipper objects.

``import _fakes`` (before importing ``ace``) puts a fake ``serial`` package in
``sys.modules`` (the real pyserial is only on the printer) and adds
``klippy/extras`` to ``sys.path``. No real serial port, Klipper or network is
ever touched.
"""

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
EXTRAS = os.path.normpath(os.path.join(HERE, "..", "klippy", "extras"))
if EXTRAS not in sys.path:
    sys.path.insert(0, EXTRAS)


# ---------------------------------------------------------------- fake pyserial

class SerialException(Exception):
    pass


class SerialTimeoutException(SerialException):
    pass


class FakeSerial:
    """Records constructor args and written frames; reads scripted bytes."""

    instances = []

    def __init__(self, port=None, baudrate=9600, timeout=None, write_timeout=None,
                 rtscts=False, dsrdtr=False, xonxoff=False, **kwargs):
        self.port = port
        self.baudrate = baudrate
        self.kwargs = dict(timeout=timeout, write_timeout=write_timeout,
                           rtscts=rtscts, dsrdtr=dsrdtr, xonxoff=xonxoff, **kwargs)
        self.is_open = True
        self.written = []
        self.rx = bytearray()
        self.closed = False
        self.read_error = None   # set to an exception to make read() raise
        self.write_error = None  # set to an exception to make write() raise
        self.events = []  # ordered log: ("open",), ("close",)
        FakeSerial.instances.append(self)

    def fileno(self):
        return 99

    def write(self, data):
        if self.write_error is not None:
            raise self.write_error
        self.written.append(bytes(data))
        return len(data)

    def read(self, size=1):
        if getattr(self, "read_error", None) is not None:
            raise self.read_error
        out = bytes(self.rx[:size])
        del self.rx[:size]
        return out

    def reset_input_buffer(self):
        self.rx.clear()

    def reset_output_buffer(self):
        pass

    def close(self):
        self.is_open = False
        self.closed = True
        self.events.append(("close",))


class FakeListPorts:
    ports = []

    @staticmethod
    def comports():
        return list(FakeListPorts.ports)


def install_fake_serial():
    serial_mod = types.ModuleType("serial")
    serial_mod.Serial = FakeSerial
    serial_mod.SerialException = SerialException
    serial_mod.SerialTimeoutException = SerialTimeoutException
    tools_mod = types.ModuleType("serial.tools")
    lp_mod = types.ModuleType("serial.tools.list_ports")
    lp_mod.comports = FakeListPorts.comports
    tools_mod.list_ports = lp_mod
    serial_mod.tools = tools_mod
    sys.modules["serial"] = serial_mod
    sys.modules["serial.tools"] = tools_mod
    sys.modules["serial.tools.list_ports"] = lp_mod


install_fake_serial()


# ---------------------------------------------------------------- fake termios

class FakeTermios:
    HUPCL = 0x400
    TCSANOW = 0

    def __init__(self, cflag=0x4B0 | 0x400):
        self.cflag = cflag
        self.set_calls = []
        self.fail = False

    def tcgetattr(self, fd):
        if self.fail:
            raise OSError("no tty")
        return [0, 0, self.cflag, 0, 0, 0, []]

    def tcsetattr(self, fd, when, attrs):
        if self.fail:
            raise OSError("no tty")
        self.cflag = attrs[2]
        self.set_calls.append((fd, when, list(attrs)))


# ---------------------------------------------------------------- fake Klipper

_SENTINEL = object()


class ReactorPauseInTimer(AssertionError):
    """pause() was called from inside a timer callback.

    In Klipper's SelectReactor a running timer has waketime=NEVER and
    timer_is_running=True until its callback RETURNS (reactor.py:179-191), so a
    timer that pauses can not fire again meanwhile. For the serial reader that
    means no replies are read while it waits for a reply (docs/32 C-01).
    Blocking code must run in ``register_callback`` (own greenlet) instead.
    """


class FakeReactor:
    """Models the parts of SelectReactor the driver depends on.

    * timers are fired with :meth:`fire` / :meth:`fire_due` and are marked as
      running during the callback (``running_timer``);
    * :meth:`pause` inside a timer callback raises ``ReactorPauseInTimer``;
    * :meth:`register_callback` callbacks run in their own "greenlet" context
      (immediately, or deferred with ``run_callbacks_immediately=False``) where
      pausing is legal;
    * ``on_pause`` (optional) is called on every pause so a test can deliver
      serial replies while the code under test sleeps.
    """

    NOW = 0.0
    NEVER = 9999999999.0

    def __init__(self):
        self.now = 1000.0
        self.timers = {}
        self._next = 1
        self.callbacks = []
        # Real register_callback runs the callback in its own greenlet AFTER the
        # current timer/event returns, never inline: default is deferred.
        self.run_callbacks_immediately = False
        self.running_timer = None
        self.in_callback = 0
        self.on_pause = None
        self.async_callbacks = []

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds

    def register_timer(self, callback, waketime=0.0):
        handle = self._next
        self._next += 1
        self.timers[handle] = (callback, waketime)
        return handle

    def unregister_timer(self, handle):
        self.timers.pop(handle, None)

    def update_timer(self, handle, waketime):
        if handle in self.timers:
            self.timers[handle] = (self.timers[handle][0], waketime)

    def fire(self, handle):
        """Run one timer like SelectReactor._check_timers does."""
        callback, waketime = self.timers[handle]
        self.timers[handle] = (callback, self.NEVER)  # NEVER while running
        previous = self.running_timer
        self.running_timer = handle
        try:
            result = callback(self.now)
        finally:
            self.running_timer = previous
        if handle in self.timers:
            self.timers[handle] = (callback, result)
        return result

    def pause(self, waketime):
        if self.running_timer is not None and not self.in_callback:
            raise ReactorPauseInTimer(
                f"reactor.pause() called inside timer {self.running_timer}"
            )
        if self.on_pause is not None:
            self.on_pause(waketime)
        if waketime > self.now:
            self.now = waketime

    def _run_callback(self, callback):
        previous = self.running_timer
        self.running_timer = None
        self.in_callback += 1
        try:
            callback(self.now)
        finally:
            self.in_callback -= 1
            self.running_timer = previous

    def register_callback(self, callback, waketime=0.0):
        if self.run_callbacks_immediately:
            self._run_callback(callback)
        else:
            self.callbacks.append(callback)

    def run_callbacks(self):
        cbs, self.callbacks = self.callbacks, []
        for cb in cbs:
            self._run_callback(cb)

    def register_async_callback(self, callback, waketime=0.0):
        """Thread-safe entry point; the test delivers it on demand."""
        self.async_callbacks.append(callback)

    def run_async_callbacks(self):
        cbs, self.async_callbacks = self.async_callbacks, []
        for cb in cbs:
            self._run_callback(cb)


class FakeGcode:
    """Records every response channel separately."""

    def __init__(self):
        self.info = []      # respond_info texts (Klipper adds "// " on the wire)
        self.raw = []       # respond_raw texts (sent as-is on the wire)
        self.scripts = []   # every script, whichever method ran it
        self.script_log = []  # (method, script): "run_script" holds gcode.mutex,
                              # "run_script_from_command" does NOT (Klipper gcode.py:237-241)
        self.commands = {}
        self.fail_scripts = set()
        self.save_model = None  # optional KlipperSaveVariables

    def respond_info(self, msg, log=True):
        self.info.append(msg)

    def respond_raw(self, msg):
        self.raw.append(msg)

    def _run(self, method, script):
        if script in self.fail_scripts:
            raise RuntimeError("Printer is shutdown")
        self.scripts.append(script)
        self.script_log.append((method, script))
        if self.save_model is not None and script.startswith("SAVE_VARIABLE"):
            self.save_model.run(script)

    def run_script_from_command(self, script):
        self._run("run_script_from_command", script)

    def run_script(self, script):
        self._run("run_script", script)

    def register_command(self, name, handler, desc=None):
        self.commands[name] = handler

    def wire(self):
        """What a client sees: info lines get the '// ' prefix."""
        return ["// " + m for m in self.info] + list(self.raw)


class FakeError(Exception):
    pass


class FakeGcmd:
    """Minimal GCodeCommand."""

    error = FakeError

    def __init__(self, gcode, params=None):
        self._gcode = gcode
        self._params = {k.upper(): str(v) for k, v in (params or {}).items()}
        self.respond_info = gcode.respond_info
        self.respond_raw = gcode.respond_raw

    def get_command_parameters(self):
        return self._params

    def _conv(self, name, default, parser, minval, maxval, above=None, below=None):
        raw = self._params.get(name)
        if raw is None:
            if default is _SENTINEL:
                raise FakeError(f"Error: missing {name}")
            return default
        value = parser(raw)
        if minval is not None and value < minval:
            raise FakeError(f"Error: {name} must have minimum of {minval}")
        if maxval is not None and value > maxval:
            raise FakeError(f"Error: {name} must have maximum of {maxval}")
        if above is not None and value <= above:
            raise FakeError(f"Error: {name} must be above {above}")
        if below is not None and value >= below:
            raise FakeError(f"Error: {name} must be below {below}")
        return value

    def get(self, name, default=_SENTINEL, **kw):
        return self._conv(name, default, str, None, None)

    # Like Klipper: no default argument => the parameter is required.
    def get_int(self, name, default=_SENTINEL, minval=None, maxval=None, above=None, below=None, **kw):
        return self._conv(name, default, int, minval, maxval, above, below)

    def get_float(self, name, default=_SENTINEL, minval=None, maxval=None, above=None, below=None, **kw):
        return self._conv(name, default, float, minval, maxval, above, below)


_TMP_DIRS = []


def _tmp_file(name="saved_variables.cfg"):
    import atexit
    import shutil
    import tempfile
    d = tempfile.mkdtemp(prefix="ace-test-")
    _TMP_DIRS.append(d)
    atexit.register(shutil.rmtree, d, True)
    return os.path.join(d, name)


class FakeSaveVariables:
    """Plain dict stand-in (see KlipperSaveVariables for the faithful model)."""

    def __init__(self):
        self.allVariables = {}
        self.filename = _tmp_file()


class FakePin:
    def __init__(self, value=1.0):
        self.value = value

    def get_status(self, eventtime):
        return {"value": self.value}


class FakePrintStats:
    def __init__(self, state="standby"):
        self.state = state

    def get_status(self, eventtime):
        return {"state": self.state}


class KlipperShutdown(Exception):
    """An unhandled exception inside a G-code handler: Klipper shuts down."""


class KlipperSaveVariables:
    """Faithful model of klippy/extras/save_variables.py + the G-code parser.

    Uses the same machinery as Klipper: the command name comes from the line
    cut at ``;`` (gcode.py _process_commands), but the extended parameters are
    taken from the ORIGINAL line (``get_raw_command_parameters``), so a ``;``
    inside quotes survives; shlex (posix, whitespace_split, commenters '#;')
    extracts VALUE, ``ast.literal_eval``
    evaluates it and the file goes through ``configparser.ConfigParser()``
    (basic interpolation, so a lone ``%`` raises ``ValueError`` which is NOT
    handled by Klipper -> shutdown).
    """

    def __init__(self, filename):
        import ast
        import configparser
        self._ast = ast
        self._configparser = configparser
        self.filename = filename
        self.allVariables = {}
        open(filename, "a").close()
        self.loadVariables()

    def loadVariables(self):
        allvars = {}
        varfile = self._configparser.ConfigParser()
        varfile.read(self.filename)  # may raise like Klipper's startup
        if varfile.has_section("Variables"):
            for name, val in varfile.items("Variables"):
                allvars[name] = self._ast.literal_eval(val)
        self.allVariables = allvars

    def run(self, line):
        import shlex
        line = line.strip()
        cut = line[:line.find(";")] if ";" in line else line
        cmd = cut.split(" ", 1)[0]
        assert cmd == "SAVE_VARIABLE", cmd
        raw = line.partition(" ")[2]   # original line, quotes protect ';'/'#'
        lexer = shlex.shlex(raw, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = "#;"
        try:
            params = {k.upper(): v for k, v in (a.split("=", 1) for a in lexer)}
        except ValueError:
            raise FakeError(f"Malformed command '{line}'")
        varname = params["VARIABLE"]
        if varname.lower() != varname:
            raise FakeError("VARIABLE must not contain upper case")
        try:
            value = self._ast.literal_eval(params["VALUE"])
        except ValueError:
            raise FakeError(f"Unable to parse '{params['VALUE']}' as a literal")
        newvars = dict(self.allVariables)
        newvars[varname] = value
        varfile = self._configparser.ConfigParser()
        varfile.add_section("Variables")
        try:
            for name, val in sorted(newvars.items()):
                varfile.set("Variables", name, repr(val))  # ValueError on lone '%'
        except Exception as exc:
            raise KlipperShutdown(f"Internal error on SAVE_VARIABLE: {exc!r}")
        with open(self.filename, "w") as fh:
            varfile.write(fh)
        self.loadVariables()


class FakePauseResume:
    def __init__(self):
        self.calls = []

    def send_pause_command(self):
        self.calls.append("send_pause_command")


class FakePrinter:
    def __init__(self):
        self.reactor = FakeReactor()
        self.gcode = FakeGcode()
        self.save_variables = FakeSaveVariables()
        self.pin = FakePin(1.0)
        self.print_stats = FakePrintStats()
        self.pause_resume = FakePauseResume()
        self.objects = {}
        self.events = {}
        self.shutdown_state = False

    def get_reactor(self):
        return self.reactor

    def get_start_args(self):
        return {}

    def register_event_handler(self, name, cb):
        self.events.setdefault(name, []).append(cb)

    def send_event(self, name, *args):
        for cb in self.events.get(name, []):
            cb(*args)

    def add_object(self, name, obj):
        self.objects[name] = obj

    def is_shutdown(self):
        return self.shutdown_state

    def lookup_object(self, name, default=_SENTINEL):
        table = {
            "gcode": self.gcode,
            "save_variables": self.save_variables,
            "output_pin ACE_Pro": self.pin,
            "print_stats": self.print_stats,
            "pause_resume": self.pause_resume,
        }
        if name in table:
            return table[name]
        if name in self.objects:
            return self.objects[name]
        if default is _SENTINEL:
            raise KeyError(name)
        return default


class FakeConfig:
    """Config reader returning each option's default unless overridden."""

    def __init__(self, printer, overrides=None):
        self._printer = printer
        self._o = dict(overrides or {})

    def get_printer(self):
        return self._printer

    def error(self, msg):
        return FakeError(msg)

    def _val(self, name, default, cast):
        if name in self._o:
            return cast(self._o[name])
        return default if default is None else cast(default)

    def get(self, name, default=None, **kw):
        return self._val(name, default, str)

    def getint(self, name, default=None, minval=None, maxval=None, **kw):
        return self._val(name, default, int)

    def getfloat(self, name, default=None, minval=None, maxval=None, above=None, **kw):
        return self._val(name, default, float)

    def getboolean(self, name, default=None, **kw):
        if name in self._o:
            return bool(self._o[name])
        return default


def make_manager(overrides=None, print_state="standby", save_file=None, ace_present=True):
    """Build a real AceManager on fake Klipper objects (no ACE needed).

    ``save_file``: path of a saved_variables.cfg; when given, the printer uses
    the faithful Klipper save_variables model instead of the plain dict.
    ``ace_present``: most tests model a plugged-in ACE whose serial link is
    not opened; keep external spool mode off for them. Pass False to test
    the real no-ACE detection.
    """
    import ace.config as ace_config_mod
    ace_config_mod.ACE_INSTANCES.clear()
    ace_config_mod.INSTANCE_MANAGERS.clear()
    from ace.manager import AceManager

    printer = FakePrinter()
    printer.print_stats.state = print_state
    if save_file is not None:
        model = KlipperSaveVariables(save_file)
        printer.save_variables = model
        printer.gcode.save_model = model
    base = {"moonraker_lane_sync_enabled": False}
    base.update(overrides or {})
    manager = AceManager(FakeConfig(printer, base))
    if ace_present:
        manager.is_ace_absent = lambda: False
    return printer, manager


def frame_for(protocol, serial_mgr, payload):
    """Serialize ``payload`` as the ACE would send it (ACE1 JSON frame)."""
    return protocol.serialize_request_frame(payload, serial_mgr._calc_crc)


# ---------------------------------------------------------------- wire helpers

import json as _json


def open_fake_port(serial_mgr):
    """Attach a fake open port to a serial manager (no timers, no heartbeat)."""
    ser = FakeSerial("/dev/ttyACM0")
    serial_mgr._serial = ser
    serial_mgr._connected = True
    return ser


def sent_frames(ser):
    """Decode the JSON requests written to a fake port."""
    out = []
    for raw in ser.written:
        n = int.from_bytes(raw[2:4], "little")
        out.append(_json.loads(raw[4:4 + n].decode()))
    return out


def feed_reply(serial_mgr, ser, rid, result=None, code=0, msg="OK"):
    payload = {"id": rid, "code": code, "msg": msg}
    if result is not None:
        payload["result"] = result
    ser.rx += frame_for(serial_mgr.protocol, serial_mgr, payload)


def run_io(serial_mgr, reactor, writer=True):
    """One writer tick followed by one reader tick."""
    if writer:
        serial_mgr._writer(reactor.now)
    serial_mgr._reader(reactor.now)


def status_result(cont_assist_time=0.0, slot_states=None, dryer="stop", temp=25):
    states = slot_states or ["empty"] * 4
    return {
        "status": "ready",
        "action": "none",
        "temp": temp,
        "dryer_status": {"status": dryer, "target_temp": 0, "duration": 0, "remain_time": 0},
        "feed_assist_count": 0,
        "cont_assist_time": cont_assist_time,
        "slots": [{"index": i, "status": st, "rfid": 0} for i, st in enumerate(states)],
    }
