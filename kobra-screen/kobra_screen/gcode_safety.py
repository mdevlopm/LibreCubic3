"""G-code heating/extrusion safety guards, execution and emergency-stop mixin for ``App``."""

import re

from . import wizards
from .app_models import Confirm, HEATING_COMMANDS
from .moonraker import MoonrakerError


class _AppGcodeMixin:
    @staticmethod
    def script_heats_printer(script):
        """Return True if a G-code script heats the nozzle or heated bed (ACE dryer excluded)."""
        if not script:
            return False
        for raw_line in str(script).splitlines():
            line = raw_line.split(";", 1)[0].strip()
            if not line:
                continue
            tokens = line.split()
            cmd = tokens[0].upper()
            # Handle compact form like M104S200
            for mcmd in ("M104", "M109", "M140", "M190"):
                if cmd.startswith(mcmd) and len(cmd) > len(mcmd):
                    rest = cmd[len(mcmd):]
                    cmd = mcmd
                    tokens = [mcmd, rest] + tokens[1:]
                    break
            if cmd in ("M104", "M109", "M140", "M190"):
                for tok in tokens[1:]:
                    tu = tok.upper()
                    if tu.startswith("S"):
                        val_str = tu[1:]
                        if "{" in val_str:
                            return True
                        try:
                            if float(val_str) > 0.0:
                                return True
                        except ValueError:
                            pass
            elif cmd == "SET_HEATER_TEMPERATURE":
                for tok in tokens[1:]:
                    tu = tok.upper()
                    if tu.startswith("TARGET="):
                        val_str = tu.split("=", 1)[1]
                        if "{" in val_str:
                            return True
                        try:
                            if float(val_str) > 0.0:
                                return True
                        except ValueError:
                            pass
            elif cmd in HEATING_COMMANDS:
                return True
        return False

    @classmethod
    def wizard_heats(cls, key):
        w = wizards.WIZARDS.get(key)
        if w is None:
            return False
        for step in w.steps:
            if cls.script_heats_printer(step.enter):
                return True
            for _, gcode in (step.actions or ()):
                if cls.script_heats_printer(gcode):
                    return True
        return False

    _LINE_NUM_RE = re.compile(r"^N\d+(?:$|(?=[A-Z_]))")
    _CHECKSUM_RE = re.compile(r"\*\d+\s*$")
    _G_MOVE_RE = re.compile(r"^G0*([0123])(?=$|[^0-9])(.*)$")
    _G_UNRETRACT_RE = re.compile(r"^G0*11(?=$|[^0-9])")
    _TOOL_MACRO_RE = re.compile(r"^T\d+$")
    _E_PARAM_RE = re.compile(r"E\s*(\{[^}]*\}|[+-]?(?:\d+(?:\.\d*)?|\.\d+))")
    _EXTRUDE_COMMANDS = frozenset({
        "ACE_CHANGE_TOOL",
        "_EXTRUDE",
        "SDCARD_PRINT_FILE",
        "PRINT_START",
        "START_PRINT",
        "RESUME",
        "M23",
        "M24",
        "G9111",
        "LOAD_FILAMENT",
    })
    _EXTRUDE_PREFIXES = (
        "ACE_CHANGE_TOOL",
        "_EXTRUDE",
        "SDCARD_PRINT_FILE",
        "PRINT_START",
        "START_PRINT",
        "M23",
        "M24",
    )

    @classmethod
    def script_extrudes(cls, script):
        """Return True if a G-code script extrudes from the nozzle (positive E or extrusion commands)."""
        if not script:
            return False
        for raw_line in str(script).splitlines():
            line = raw_line.split(";", 1)[0].split("#", 1)[0].strip()
            if not line:
                continue
            line = cls._CHECKSUM_RE.sub("", line).strip()
            if not line:
                continue
            tokens = line.split()
            upper0 = tokens[0].upper()
            m_ln = cls._LINE_NUM_RE.match(upper0)
            if m_ln:
                rest0 = upper0[m_ln.end():]
                tokens = ([rest0] + tokens[1:]) if rest0 else tokens[1:]
                if not tokens:
                    continue
                upper0 = tokens[0].upper()

            cmd = upper0
            if (
                cmd in cls._EXTRUDE_COMMANDS
                or cls._TOOL_MACRO_RE.match(cmd)
                or cls._G_UNRETRACT_RE.match(cmd)
                or any(
                    cmd.startswith(prefix) and (len(cmd) == len(prefix) or not cmd[len(prefix)].isalnum())
                    for prefix in cls._EXTRUDE_PREFIXES
                )
            ):
                return True

            if cmd == "FORCE_MOVE":
                for tok in tokens[1:]:
                    tu = tok.upper()
                    if tu.startswith("STEPPER="):
                        stepper = tu.split("=", 1)[1].strip("\"'")
                        if stepper.startswith("EXTRUDER"):
                            return True

            m_g = cls._G_MOVE_RE.match(cmd)
            if m_g:
                tail = " ".join([m_g.group(2)] + [t.upper() for t in tokens[1:]])
                for m_e in cls._E_PARAM_RE.finditer(tail):
                    val_str = m_e.group(1)
                    if "{" in val_str:
                        return True
                    try:
                        if float(val_str) > 0.0:
                            return True
                    except ValueError:
                        pass
        return False

    @classmethod
    def wizard_extrudes(cls, w):
        if not w:
            return False
        for step in w.steps:
            if cls.script_extrudes(step.enter):
                return True
            for _, gcode in (step.actions or ()):
                if cls.script_extrudes(gcode):
                    return True
        return False

    def do(self, call, done=None, on_error=None, urgent=False):
        """Run a Moonraker call in the background and report failures on screen."""
        def work():
            with self._lock:
                self.busy += 1
                self.dirty = True
            try:
                call()
                self.refresh()  # show the effect now, not at the next poll
                if done:
                    self.notify(done, 3, "active")
            except MoonrakerError as exc:
                if on_error:
                    on_error(str(exc))
                else:
                    self.notify(self.tr_error(str(exc)))
            finally:
                with self._lock:
                    self.busy -= 1
                    self.dirty = True
        (self.run_urgent if urgent else self.run_async)(work)

    def _check_extrusion_lock(self, cmd):
        """Check if command triggers extrusion lock.
        Returns False if blocked, True if permitted.
        """
        if not getattr(self, "extrusion_lock", False):
            return True
        if self.script_extrudes(cmd):
            first_line = str(cmd).strip().splitlines()[0] if str(cmd).strip() else ""
            self.log_event("Ekstrüzyon engellendi: %s" % (first_line[:60],),
                           severity="warn", source="gcode")
            self.notify(self.tr("Ekstrüzyon kilidi açık"))
            return False
        return True

    def gcode(self, script, done=None, on_error=None, urgent=False, timeout=300):
        if not self._check_extrusion_lock(script):
            return False
        if self.state.klippy in ("shutdown", "error"):
            # Moonraker answers G-code in these states with a malformed HTTP header; do not even send it.
            self.notify(self.tr("Klipper hazır değil"))
            return False
        if self.heating_lock and self.script_heats_printer(script):
            self.notify(self.tr("Isıtma kilidi açık"))
            return False

        def _default_err(err):
            synced = self._sync_gcode_errors()
            self.notify(self.tr_error(err), record=not bool(synced))

        self.do(lambda: self.client.gcode(script, timeout=timeout), done, on_error or _default_err, urgent=urgent)
        return True

    def handle_prompt_button(self, gcode_script: str):
        """Handle a button press from an action:prompt dialog.
        Sends the gcode through the normal gcode() path which enforces extrusion_lock.
        """
        if not gcode_script:
            return False
        return self.gcode(gcode_script)

    def start_print(self, path):
        if getattr(self, "extrusion_lock", False):
            self.notify(self.tr("Ekstrüzyon kilidi açık"))
            return False
        if self.heating_lock:
            self.notify(self.tr("Isıtma kilidi açık"))
            return False
        with self._lock:
            self.pages = []
            self.tab = "home"
            self.dirty = True
        self.do(lambda: self.client.print_start(path), self.tr("Baskı başlatıldı"))
        return True

    def resume_print(self):
        if getattr(self, "extrusion_lock", False):
            self.log_event("Ekstrüzyon engellendi: print_resume", severity="warn", source="gcode")
            self.notify(self.tr("Ekstrüzyon kilidi açık"))
            return False
        if self.heating_lock:
            self.notify(self.tr("Isıtma kilidi açık"))
            return False
        self.do(self.client.print_resume)
        return True

    def heating_started_already(self):
        return self.get("extruder", "target") > 0 or self.get("heater_bed", "target") > 0

    def request_heat(self, targets, done=None):
        """Set heater targets ({"extruder": C, "heater_bed": C}); asks first when this would start heating.

        The question is skipped when the heating lock is on (gcode() refuses anyway), when something is already
        heating, when every target is 0 (switching off) or after "don't ask again" was ticked.
        """
        clean = {}
        for name, value in targets.items():
            limit = self.setting(name, "max_temp", 0)
            clean[name] = max(0, min(limit, int(round(value)))) if limit else max(0, int(round(value)))
        lines = []
        if "extruder" in clean:
            lines.append("M104 S%d" % clean["extruder"])
        if "heater_bed" in clean:
            lines.append("M140 S%d" % clean["heater_bed"])
        script = "\n".join(lines)

        def run():
            return self.gcode(script, done)

        if (not any(clean.values()) or self.heating_lock or self.heating_confirm_skip
                or self.heating_started_already()):
            return run()
        noz, bed = clean.get("extruder", 0), clean.get("heater_bed", 0)
        if noz and bed:
            text = self.tr("Isıtma başlatılsın mı? (nozul %d°C, tabla %d°C)") % (noz, bed)
        elif noz:
            text = self.tr("Isıtma başlatılsın mı? (nozul %d°C)") % noz
        else:
            text = self.tr("Isıtma başlatılsın mı? (tabla %d°C)") % bed
        holder = {}

        def accepted():
            if holder["dialog"].option_state:
                self.set_heating_confirm_skip(True)
            run()

        self.confirm(text, accepted, "primary", yes_label="Isıt",
                     option_label="Bir daha sorma")
        with self._lock:
            holder["dialog"] = self.modal
        return True

    BED_MESH_REMEASURE_MINUTES = 8  # VARSAYIM: LeviQ duration was not measured on the printer yet

    def request_bed_remeasure(self):
        """"Yeniden ölç": heats the bed and nozzle, so it is locked by the heating lock and needs a confirmation."""
        if self.heating_lock:
            self.notify(self.tr("Isıtma kilidi açık"))
            return False
        if not self.idle:
            return False
        self.confirm(
            self.tr("Tabla ısıtılacak, ölçüm yaklaşık %d dk sürer. Tabla boş ve temiz olsun. Başlatılsın mı?")
            % self.BED_MESH_REMEASURE_MINUTES,
            lambda: self.start_wizard("level"), "primary", yes_label="Başlat")
        return True

    def request_emergency_stop(self):
        with self._lock:
            if isinstance(self.modal, Confirm) and self.modal.is_estop:
                return
        self.confirm(
            self.tr(
                "Acil durdurma yapılsın mı?\n\nYazıcı hemen durur, ısıtıcılar kapanır. "
                "Devam etmek için Klipper yeniden başlatılmalıdır."
            ),
            lambda: self.do(self.client.emergency_stop, self.tr("Acil durdurma gönderildi")),
            kind="danger",
            yes_label="Evet, durdur",
            no_label="Vazgeç",
            is_estop=True,
        )
