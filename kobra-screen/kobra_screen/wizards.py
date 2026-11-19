"""Guided step-by-step procedures (cold pull, filament change, calibration).

A wizard is a list of steps.  A step may run G-code when it opens, wait for a
condition (a temperature, or the G-code finishing), offer extra action buttons
or ask for a choice that fills in parameters for the following steps.
"""

from dataclasses import dataclass, field

# name: (print temperature, cold pull temperature)
MATERIALS = {"PLA": (215, 90), "PETG": (240, 110), "ABS": (250, 130), "PA": (260, 140)}
MATERIAL_CHOICES = tuple((name, {"material": name, "hot": hot, "pull": pull})
                         for name, (hot, pull) in MATERIALS.items())


@dataclass
class Step:
    title: str
    text: str
    enter: str = None  # G-code run when the step opens; {name} is filled from the chosen parameters
    wait: object = None  # fn(app, params) -> (done, fraction, label)
    blocking: bool = False  # `enter` takes long; the step is done when it returns
    actions: tuple = ()  # ((label, gcode), ...) extra buttons
    choices: tuple = ()  # ((label, {param: value}), ...) picking one moves on
    repeat_from: int = None  # offers "Tekrarla", jumping back to this step


@dataclass
class Wizard:
    key: str
    name: str
    icon: str
    steps: tuple
    cancel: str = None  # G-code run when the wizard is abandoned


def nozzle(app):
    return app.get("extruder", "temperature")


def heated(key):
    def wait(app, params):
        target = params[key]
        temp = nozzle(app)
        return temp >= target - 2, min(1.0, temp / target), "%.0f°C / %d°C" % (temp, target)
    return wait


def cooled(key, start_key):
    def wait(app, params):
        target, start = params[key], params[start_key]
        temp = nozzle(app)
        fraction = (start - temp) / float(start - target)
        return temp <= target + 3, max(0.0, min(1.0, fraction)), "%.0f°C / %d°C" % (temp, target)
    return wait


def bed_and_nozzle(bed_target, nozzle_target):
    def wait(app, params):
        bed, noz = app.get("heater_bed", "temperature"), nozzle(app)
        fraction = min(1.0, bed / bed_target, noz / nozzle_target)
        return (bed >= bed_target - 2 and noz >= nozzle_target - 2, max(0.0, fraction),
                app.tr("Tabla %.0f°C / %d°C · Nozul %.0f°C / %d°C") % (bed, bed_target, noz, nozzle_target))
    return wait


HEAT_OFF = "M104 S0\nM106 S0"
PUSH = "M83\nG1 E10 F150\nM82"

WIZARDS = {wizard.key: wizard for wizard in (
    Wizard("cold_pull", "Cold pull", "heal", (
        Step("Malzeme", "Nozulda en son hangi filament vardı?", choices=MATERIAL_CHOICES),
        Step("Hazırlık", "ACE kullanıyorsan filamenti geri çektir ve boruyu ekstruder girişinden ayır.\n"
                         "Elinde 30 cm kadar {material} filament olsun."),
        Step("Isıtma", "Nozul {hot}°'ye ısıtılıyor.", enter="M104 S{hot}", wait=heated("hot")),
        Step("Filamenti sür", "Filamenti ekstrudere tak. Nozuldan temiz akana kadar sür.",
             actions=(("10 mm sür", PUSH),)),
        Step("Soğutma", "Nozul {pull}°'ye soğutuluyor. Filamente dokunma.",
             enter="M106 S255\nM104 S{pull}", wait=cooled("pull", "hot")),
        Step("Çek", "Ekstruder motoru serbest. Varsa ekstruder kolunu aç ve filamenti tek hamlede, "
                    "sıkıca yukarı çek.", enter="M106 S0\nSET_STEPPER_ENABLE STEPPER=extruder ENABLE=0"),
        Step("Uç kontrolü", "Çıkan uç nozulun iç şeklini almış ve temiz olmalı. Üzerinde kir varsa tekrarla.",
             enter="M104 S0", repeat_from=2),
    ), cancel=HEAT_OFF),
    Wizard("load", "Filament yükle", "load", (
        Step("Malzeme", "Hangi filament yüklenecek?", choices=MATERIAL_CHOICES),
        Step("Isıtma", "Nozul {hot}°'ye ısıtılıyor.", enter="M104 S{hot}", wait=heated("hot")),
        Step("Yükle", "Filamenti ekstrudere tak ve nozuldan yeni renk temiz akana kadar sür.",
             actions=(("10 mm sür", PUSH), ("50 mm sür", "M83\nG1 E50 F240\nM82"))),
        Step("Bitti", "Nozul ucunu temizle. Isıtıcı kapatıldı.", enter="M104 S0"),
    ), cancel=HEAT_OFF),
    Wizard("unload", "Filament çıkar", "unload", (
        Step("Malzeme", "Nozulda hangi filament var?", choices=MATERIAL_CHOICES),
        Step("Isıtma", "Nozul {hot}°'ye ısıtılıyor.", enter="M104 S{hot}", wait=heated("hot")),
        Step("Çıkar", "Filament geri çekiliyor.", enter="M83\nG1 E5 F200\nG1 E-80 F1500\nM82", blocking=True),
        Step("Bitti", "Filamenti ekstruderden al. Isıtıcı kapatıldı.", enter="M104 S0"),
    ), cancel=HEAT_OFF),
    Wizard("clean", "Nozul temizle", "clean", (
        Step("Hazırlık", "Tablanın boş olduğundan emin ol. Nozul 170°'ye ısınıp silme pedinde silinecek."),
        # Home while the nozzle is still cold: Z homes by nozzle contact, a hot unwiped nozzle oozes onto the bed
        Step("Isıtma", "Eksenler sıfırlanıyor, nozul 170°C'ye ısıtılıyor.", enter="G28\nM104 S170",
             wait=lambda app, params: (
                 nozzle(app) >= 168, min(1.0, nozzle(app) / 170.0), "%.0f°C / 170°C" % nozzle(app))),
        Step("Silme", "Nozul siliniyor.", enter="NOZZLE_WIPE_SEQUENCE", blocking=True),
        Step("Bitti", "Isıtıcı kapatıldı.", enter="M104 S0"),
    ), cancel=HEAT_OFF),
    Wizard("level", "Tabla seviyele", "level", (
        Step("Hazırlık", "Tabla boş ve temiz olmalı. Nozul ve tabla ısınacak, nozul silinecek ve tabla "
                         "ağı ölçülecek. Birkaç dakika sürer."),
        Step("Seviyeleme", "LeviQ çalışıyor.", enter="G28\nLEVIQ", blocking=True),
        Step("Bitti", "Tabla ağı ölçüldü.", enter="M104 S0\nM140 S0"),
    ), cancel="M104 S0\nM140 S0"),
    # Same conditions as printing: bed at 60°C, nozzle wiped (LEVIQ_WIPING cools it to 140°C and re-homes Z).
    # FORCE=1 measures once without flipping the saved auto z-offset switch.
    Wizard("zoffset", "Z-offset ölç", "zoffset", (
        Step("Hazırlık", "Tabla boş ve temiz olmalı. Tabla 60°, nozul 170° ısınacak; nozul silinip "
                         "z-offset ölçülecek."),
        Step("Isıtma", "Eksenler sıfırlanıyor, tabla ve nozul ısıtılıyor.", enter="G28\nM140 S60\nM104 S170",
             wait=bed_and_nozzle(60, 170)),
        Step("Ölçüm", "Nozul siliniyor ve z-offset ölçülüyor.",
             enter="LEVIQ_WIPING\nLEVIQ_AUTO_ZOFFSET FORCE=1", blocking=True),
        Step("Bitti", "Yeni z-offset kaydedildi. Isıtıcılar kapatıldı.", enter="M104 S0\nM140 S0"),
    ), cancel="M104 S0\nM140 S0"),
    Wizard("pid", "PID ayarı", "chart", (
        Step("Isıtıcı", "Hangi ısıtıcı ayarlansın?", choices=(
            ("Nozul 220°", {"heater": "extruder", "target": 220}), ("Tabla 60°", {"heater": "heater_bed", "target": 60}))),
        Step("Ayarlama", "PID ayarı yapılıyor. Birkaç dakika sürer, yazıcıya dokunma.",
             enter="PID_CALIBRATE HEATER={heater} TARGET={target}", blocking=True),
        Step("Kaydet", "Sonuç kaydedilince Klipper yeniden başlar.", actions=(("Kaydet", "SAVE_CONFIG"),)),
    ), cancel="TURN_OFF_HEATERS"),
    Wizard("shaper", "Titreşim ayarı", "vibration", (
        Step("Hazırlık", "Tabla boş olmalı. Yazıcı eksenleri titreştirerek rezonansı ölçecek; ses normaldir."),
        Step("Ölçüm", "Rezonans ölçülüyor. Birkaç dakika sürer.", enter="G28\nSHAPER_CALIBRATE", blocking=True),
        Step("Kaydet", "Sonuç kaydedilince Klipper yeniden başlar.", actions=(("Kaydet", "SAVE_CONFIG"),)),
    )),
)}


def _run_steps(app, cmd, on_blocked=None):
    """Run a wizard step command through app.gcode().

    If app.gcode() returns False (e.g. blocked by extrusion lock),
    the step is skipped and an 'Ekstrüzyon kilidi açık' notification is shown.
    """
    if not cmd:
        return True
    sent = app.gcode(cmd)
    if not sent:
        app.notify(app.tr("Ekstrüzyon kilidi açık"))
        if on_blocked:
            on_blocked()
        return False
    return True


@dataclass
class Run:
    wizard: Wizard
    index: int = 0
    params: dict = field(default_factory=dict)
    failed: str = ""
    running: bool = False

    @property
    def step(self):
        return self.wizard.steps[self.index]

    def text(self, template):
        return template.format(**self.params) if template else ""

    def _run_steps(self, app, cmd=None):
        if cmd is None and self.step and self.step.enter:
            cmd = self.text(self.step.enter)
        if not cmd:
            return True

        def on_blocked():
            self.running = False
            self.failed = app.tr("Ekstrüzyon kilidi açık")

        return _run_steps(app, cmd, on_blocked=on_blocked)

    def enter(self, app):
        self.failed = ""
        step = self.step
        if step.enter:
            cmd = self.text(step.enter)
            if getattr(app, "heating_lock", False) and app.script_heats_printer(cmd):
                self.running = False
                self.failed = app.tr("Isıtma kilidi açık")
                app.notify(self.failed)
                return
            if getattr(app, "extrusion_lock", False) and app.script_extrudes(cmd):
                self.running = False
                self.failed = app.tr("Ekstrüzyon kilidi açık")
                app.notify(self.failed)
                return
            self.running = True

            step_is_blocking = step.blocking
            timeout = 900 if step_is_blocking else 300  # leveling / PID can take longer than the 300 s default

            def finished(error=""):
                self.running = False
                self.failed = error
                if error and step_is_blocking and self.wizard.cancel:
                    # a failed long step may leave the heaters on: run the wizard's cancel script
                    app.gcode(self.wizard.cancel)
            app.do(lambda: (app.client.gcode(cmd, timeout=timeout), finished()), on_error=finished)

    def progress(self, app):
        """(done, fraction or None, label) for the current step."""
        step = self.step
        if self.failed:
            return False, None, self.failed
        if step.choices:
            return False, None, ""
        if step.blocking:
            return not self.running, None, app.tr("Çalışıyor...") if self.running else app.tr("Tamamlandı")
        if step.wait:
            return step.wait(app, self.params)
        return True, None, ""

    def goto(self, app, index):
        self.index = index
        self.enter(app)
        app.dirty = True

    def next(self, app):
        if self.index + 1 >= len(self.wizard.steps):
            app.end_wizard(cancelled=False)
        else:
            self.goto(app, self.index + 1)

    def choose(self, app, params):
        self.params.update(params)
        self.next(app)
