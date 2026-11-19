"""Editable Klipper safety limits stored in open_kobra_limits.cfg."""

import configparser
import math
import os
from dataclasses import dataclass

from . import i18n, touch_cal


class _ConfigSchemaMeta(type):
    extrusion_lock: bool = False

    def __getitem__(cls, item):
        if item == "extrusion_lock":
            return getattr(cls, item)
        raise KeyError(item)


class _GetDescriptor:
    def __get__(self, obj, objtype=None):
        if obj is None:
            def _class_get(key, default=None):
                if key == "extrusion_lock":
                    return getattr(objtype, key, default)
                return default
            return _class_get
        return obj._instance_get


class ConfigSchema(dict, metaclass=_ConfigSchemaMeta):
    """Configuration schema with typed defaults."""
    extrusion_lock: bool = False
    get = _GetDescriptor()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "extrusion_lock" not in self:
            self["extrusion_lock"] = False

    def _instance_get(self, key, default=None):
        return super().get(key, getattr(self.__class__, key, default))

    def __getattr__(self, name):
        if name in self:
            return self[name]
        return getattr(self.__class__, name)

    def __setattr__(self, name, value):
        self[name] = value

    @classmethod
    def defaults(cls):
        return {"extrusion_lock": False}


def load_config(env=None, rinkhals_home=None, app_name=None, config_path=None):
    """Load config from temporary and persistent config files (ERR-21)."""
    env = os.environ if env is None else env
    app = app_name or env.get("KOBRA_APP_NAME", "kobra-screen")
    data = {"extrusion_lock": False}

    perm_path = i18n.resolve_persistent_config_path(
        env=env, rinkhals_home=rinkhals_home, app_name=app, config_path=config_path
    )
    tmp_path = os.path.join("/tmp/rinkhals/apps", app + ".config")
    if os.path.isfile(tmp_path) and os.path.abspath(tmp_path) != os.path.abspath(perm_path):
        tmp_data = touch_cal._read_json_dict(tmp_path)
        tmp_data.pop("extrusion_lock", None)
        data.update(tmp_data)

    if os.path.isfile(perm_path):
        perm_data = touch_cal._read_json_dict(perm_path)
        if "extrusion_lock" in perm_data and perm_data["extrusion_lock"] is None:
            perm_data.pop("extrusion_lock", None)
        data.update(perm_data)

    raw_lock = data.get("extrusion_lock", False)
    if isinstance(raw_lock, bool):
        data["extrusion_lock"] = raw_lock
    elif isinstance(raw_lock, str):
        data["extrusion_lock"] = raw_lock.strip().lower() in ("1", "true", "yes", "on")
    elif isinstance(raw_lock, (int, float)):
        data["extrusion_lock"] = bool(raw_lock)
    else:
        data["extrusion_lock"] = True

    return data

LIMITS_FILE = "open_kobra_limits.cfg"
LIMITS_HEADER = (
    "# kobra-screen tarafindan yonetilen sinir gecersiz kilmalari\n"
    "# printer.cfg sonuna (SAVE_CONFIG ustune) ekleyin:\n"
    "# [include open_kobra_limits*.cfg]\n"
)

VERIFY_BYPASS = {
    "max_error": 999999.0,
    "check_gain_time": 999999.0,
    "hysteresis": 999.0,
    "heating_gain": 0.01,
}

SECTION_ORDER = (
    "extruder",
    "heater_bed",
    "verify_heater extruder",
    "verify_heater heater_bed",
    "printer",
    "leviq3",
    "ace",
    "temperature_sensor ace_temp",
)


@dataclass(frozen=True)
class LimitSpec:
    section: str
    option: str
    label: str
    unit: str = ""
    minval: float = None
    above: float = None
    max_ref: tuple = None  # optional (section, option) Klipper upper bound
    allow_float: bool = False
    is_bool: bool = False
    digits: int = 5
    danger: str = "up"  # "up" | "down"
    risk: str = ""


TEMP_LIMITS = (
    LimitSpec(
        "extruder", "max_temp", "Nozul en çok", "°",
        above=-273.15, max_ref=None, digits=4, danger="up",
        risk="Nozul sınırı artarsa yüksek sıcaklıkta termal veya donanım hasarı oluşabilir.",
    ),
    LimitSpec(
        "heater_bed", "max_temp", "Tabla en çok", "°",
        above=-273.15, max_ref=None, digits=4, danger="up",
        risk="Tabla sınırı artarsa yatak ısıtıcısı ve tesisat aşırı ısınabilir.",
    ),
    LimitSpec(
        "extruder", "min_extrude_temp", "Soğuk ekstrüzyon alt", "°",
        minval=0.0, max_ref=("extruder", "max_temp"), digits=4, danger="down",
        risk="Alt sınır düşürülürse soğuk nozulla filament itilip dişli veya hotend zorlanabilir.",
    ),
    LimitSpec(
        "extruder", "max_extrude_only_distance", "Tek ekstrüzyon en çok", " mm",
        minval=0.0, digits=6, danger="up",
        risk="Tek seferlik ekstrüzyon sınırı büyürse denetimsiz uzun itme yapılabilir.",
    ),
)

MOTION_LIMITS = (
    LimitSpec(
        "printer", "max_velocity", "En yüksek hız", " mm/s",
        above=0.0, digits=5, danger="up",
        risk="Hız sınırı artarsa adım kaçırma veya mekanik zorlanma oluşabilir.",
    ),
    LimitSpec(
        "printer", "max_accel", "En yüksek ivme", " mm/s²",
        above=0.0, digits=6, danger="up",
        risk="İvme sınırı artarsa kayış ve motor yükü artar, baskı kalitesi düşebilir.",
    ),
    LimitSpec(
        "printer", "max_z_velocity", "En yüksek Z hızı", " mm/s",
        above=0.0, max_ref=("printer", "max_velocity"), digits=5, danger="up",
        risk="Z hızı artarsa Z ekseni adım kaçırabilir veya tablaya çarpabilir.",
    ),
)

LEVIQ3_LIMITS = (
    LimitSpec(
        "leviq3", "max_auto_travel", "Otomatik Z kalkış", " mm",
        above=0.0, allow_float=True, digits=5, danger="up",
        risk="Kalkış sınırı artarsa sensör arızasında nozul gereğinden fazla yükselir.",
    ),
    LimitSpec(
        "leviq3", "noise_diff", "Sensör gürültü eşiği", "",
        above=0.0, digits=6, danger="up",
        risk="Gürültü eşiği artarsa temas algılama hassasiyeti düşer.",
    ),
)


def _verify_specs(section, risk_err, risk_time, risk_hyst, risk_gain):
    return (
        LimitSpec(
            section, "max_error", "Hata eşiği", "°s",
            minval=0.0, digits=6, danger="up",
            risk=risk_err,
        ),
        LimitSpec(
            section, "check_gain_time", "Kontrol süresi", " sn",
            minval=1.0, digits=6, danger="up",
            risk=risk_time,
        ),
        LimitSpec(
            section, "hysteresis", "Tolerans (hysteresis)", "°",
            minval=0.0, allow_float=True, digits=5, danger="up",
            risk=risk_hyst,
        ),
        LimitSpec(
            section, "heating_gain", "En az artış", "°",
            above=0.0, allow_float=True, digits=5, danger="down",
            risk=risk_gain,
        ),
    )


VERIFY_EXTRUDER_LIMITS = _verify_specs(
    "verify_heater extruder",
    "Nozul hata eşiği büyürse termal kaçak daha geç algılanır.",
    "Nozul kontrol süresi uzarsa arızalı ısıtıcı geç durdurulur.",
    "Nozul sıcaklık bandı genişlerse ısıl sapmalar gizlenebilir.",
    "Nozul en az artış düşürülürse yavaş ısıtma arızası yakalanmayabilir.",
)
VERIFY_BED_LIMITS = _verify_specs(
    "verify_heater heater_bed",
    "Tabla hata eşiği büyürse termal kaçak daha geç algılanır.",
    "Tabla kontrol süresi uzarsa arızalı ısıtıcı geç durdurulur.",
    "Tabla sıcaklık bandı genişlerse ısıl sapmalar gizlenebilir.",
    "Tabla en az artış düşürülürse yavaş ısıtma arızası yakalanmayabilir.",
)

ACE_FEED_LIMITS = (
    LimitSpec(
        "ace", "max_dryer_temperature", "Kurutma en çok", "°",
        minval=1.0, digits=3, danger="up",
        risk="Kurutma üst sınırı artarsa kurutucu ve makara aşırı ısınabilir.",
    ),
    LimitSpec(
        "temperature_sensor ace_temp", "max_temp", "Gövde ısı en çok", "°",
        above=-273.15, digits=3, danger="up",
        risk="ACE gövde sıcaklık sınırı artarsa aşırı ısınma daha geç durdurulur.",
    ),
    LimitSpec(
        "ace", "total_max_feeding_length", "Toplam sürme sınırı", " mm",
        minval=10.0, digits=5, danger="up",
        risk="Sürme sınırı aşırı büyürse sensöre ulaşmayan filament uzun süre itilir.",
    ),
    LimitSpec(
        "ace", "timeout_multiplier", "Zaman aşımı çarpanı", "x",
        minval=1.0, digits=3, danger="up",
        risk="Çarpan artarsa takılan ACE hareketi daha geç zaman aşımına düşer.",
    ),
)

ACE_SENSE_LIMITS = (
    LimitSpec(
        "ace", "tangle_detection", "Dolaşma algılama",
        is_bool=True, danger="down",
        risk="Dolaşma algılama kapatılırsa sıkışan filament baskıyı durdurmaz.",
    ),
    LimitSpec(
        "ace", "tangle_detection_length", "Dolaşma uzunluğu", " mm",
        minval=1.0, allow_float=True, digits=5, danger="up",
        risk="Dolaşma mesafesi büyürse sıkışma daha geç fark edilir.",
    ),
    LimitSpec(
        "ace", "runout_debounce_count", "Bitiş süzme sayısı", "",
        minval=1.0, digits=3, danger="up",
        risk="Süzme sayısı çok büyürse filament bitişi gecikmeli algılanır.",
    ),
    LimitSpec(
        "ace", "ace_connection_supervision", "Bağlantı denetimi",
        is_bool=True, danger="down",
        risk="Bağlantı denetimi kapatılırsa kopan USB bağlantısı baskıyı durdurmaz.",
    ),
)

ACE_LIMITS = ACE_FEED_LIMITS + ACE_SENSE_LIMITS

ALL_LIMITS = (
    TEMP_LIMITS + MOTION_LIMITS + LEVIQ3_LIMITS + VERIFY_EXTRUDER_LIMITS + VERIFY_BED_LIMITS + ACE_LIMITS
)
LIMITS_BY_KEY = {(s.section.lower(), s.option.lower()): s for s in ALL_LIMITS}


def parse_bool_value(raw):
    """Parse Klipper/Python boolean value into bool or None."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        token = raw.strip().lower()
        if token in ("true", "yes", "on", "1"):
            return True
        if token in ("false", "no", "off", "0"):
            return False
    return None


def is_per_instance_value(raw):
    """Return True for multi-instance ACE override strings such as '60,1:55'."""
    return isinstance(raw, str) and ("," in raw or ":" in raw)


def ace_instance_value(raw, instance=0, default=None):
    """Value of an ACE option that may be per instance: '60' or '60,1:55' (default 60, instance 1 -> 55)."""
    if raw is None:
        return default
    if not isinstance(raw, str):
        return raw
    plain = None
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            idx, _, val = part.partition(":")
            if idx.strip() == str(instance):
                return val.strip()
        elif plain is None:
            plain = part
    return plain if plain is not None else default


def clamp_dryer_temperature(temp, max_dryer_temp=None):
    """Clamp requested dryer temperature to the maximum allowed dryer temperature limit."""
    if max_dryer_temp is None:
        return temp
    try:
        limit = float(max_dryer_temp)
        return min(float(temp), limit)
    except (TypeError, ValueError):
        return temp


def coerce_limit_value(spec, raw):
    """Normalize raw setting or config string into typed override value, or None if unsupported."""
    if raw is None or is_per_instance_value(raw):
        return None
    if spec.is_bool:
        return parse_bool_value(raw)
    try:
        return float(str(raw).strip() if isinstance(raw, str) else raw)
    except (TypeError, ValueError):
        return None


def parse_limits_cfg(text):
    """Parse open_kobra_limits.cfg text into {(section_lower, option_lower): value}."""
    if not text:
        return {}
    parser = configparser.RawConfigParser(strict=False, inline_comment_prefixes=(";", "#"))
    parser.read_string(text)
    out = {}
    for section in parser.sections():
        sec_l = section.strip().lower()
        for option, raw in parser.items(section):
            opt_l = option.strip().lower()
            spec = LIMITS_BY_KEY.get((sec_l, opt_l))
            if spec is None:
                continue
            val = coerce_limit_value(spec, raw)
            if val is not None:
                out[(sec_l, opt_l)] = val
    return out


def format_num(value):
    return "%g" % (round(float(value), 4),)


def dump_limits_cfg(overrides):
    """Serialize {(section_lower, option_lower): value} as Klipper INI text."""
    grouped = {}
    for (sec, opt), val in overrides.items():
        spec = LIMITS_BY_KEY.get((sec, opt))
        if spec is None:
            continue
        norm = coerce_limit_value(spec, val)
        if norm is not None:
            grouped.setdefault(sec, {})[opt] = norm
    if not grouped:
        return LIMITS_HEADER
    lines = [LIMITS_HEADER.rstrip(), ""]
    for sec in SECTION_ORDER:
        opts = grouped.get(sec)
        if not opts:
            continue
        lines.append("[%s]" % sec)
        for spec in ALL_LIMITS:
            if spec.section == sec and spec.option in opts:
                val = opts[spec.option]
                if spec.is_bool:
                    text = "True" if val else "False"
                elif not spec.allow_float and abs(float(val) - round(float(val))) < 1e-9:
                    text = str(int(round(float(val))))
                else:
                    text = format_num(val)
                lines.append("%s: %s" % (spec.option, text))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_limit_value(spec, value, lang=i18n.DEFAULT_LANG):
    if value is None:
        return "-"
    if spec.is_bool:
        return i18n.tr("açık", lang) if value else i18n.tr("kapalı", lang)
    if spec.section == "extruder" and spec.option == "min_extrude_temp" and float(value) <= 0.0:
        return i18n.tr("kapalı", lang)
    unit = i18n.tr(spec.unit, lang) if spec.unit else ""
    return "%s%s" % (format_num(value), unit)


def is_dangerous_change(spec, current, new_val):
    if current is None:
        return False
    if spec.is_bool:
        cur_b = bool(current)
        nxt_b = bool(new_val)
        if spec.danger == "down":
            return cur_b and not nxt_b
        if spec.danger == "up":
            return (not cur_b) and nxt_b
        return False
    cur = float(current)
    nxt = float(new_val)
    if spec.danger == "up":
        return nxt > cur
    if spec.danger == "down":
        return nxt < cur
    return False


_INT_OPTIONS = {
    ("ace", "timeout_multiplier"),
    ("ace", "runout_debounce_count"),
}


def _effective_num(overrides, base_settings, section, option):
    key = (section.lower(), option.lower())
    if key in overrides:
        val = overrides[key]
    elif base_settings:
        sec_map = base_settings.get(key[0])
        if sec_map is None:
            for k, v in base_settings.items():
                if isinstance(k, str) and k.lower() == key[0]:
                    sec_map = v
                    break
        raw = None
        if isinstance(sec_map, dict):
            if key[1] in sec_map:
                raw = sec_map[key[1]]
            else:
                for k, v in sec_map.items():
                    if isinstance(k, str) and k.lower() == key[1]:
                        raw = v
                        break
        spec = LIMITS_BY_KEY.get(key)
        if spec is not None:
            val = coerce_limit_value(spec, raw)
        else:
            try:
                val = float(raw) if raw is not None else None
            except (TypeError, ValueError):
                val = None
    else:
        val = None
    if isinstance(val, bool) or val is None:
        return None
    try:
        num = float(val)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def validate_limits(overrides, base_settings=None, lang=i18n.DEFAULT_LANG):
    """Validate overrides against Klipper config bounds and cross-option constraints.

    Returns a list of human-readable error strings; an empty list means Klipper
    config loading constraints are satisfied. High values are never artificially
    capped beyond Klipper's own checks.
    """
    errors = []
    norm_overrides = {}
    for (sec, opt), raw in (overrides or {}).items():
        key = (sec.lower(), opt.lower())
        spec = LIMITS_BY_KEY.get(key)
        if spec is None:
            errors.append(i18n.tr("Desteklenmeyen sınır anahtarı: %s.%s", lang) % (sec, opt))
            continue
        label = i18n.tr(spec.label, lang)
        if spec.is_bool:
            bval = coerce_limit_value(spec, raw)
            if bval is None:
                errors.append(i18n.tr("%s için geçersiz mantıksal değer.", lang) % label)
            else:
                norm_overrides[key] = bval
            continue
        num = coerce_limit_value(spec, raw)
        if num is None or not math.isfinite(num):
            errors.append(i18n.tr("%s için geçerli bir sayı girin.", lang) % label)
            continue
        if spec.minval is not None and num < spec.minval:
            errors.append(i18n.tr("%s en az %s olabilir.", lang) % (label, format_num(spec.minval)))
        if spec.above is not None and num <= spec.above:
            errors.append(i18n.tr("%s %s değerinden büyük olmalıdır.", lang) % (label, format_num(spec.above)))
        if key in _INT_OPTIONS and abs(num - round(num)) >= 1e-9:
            errors.append(i18n.tr("%s tam sayı olmalıdır.", lang) % label)
        norm_overrides[key] = num

    # Klipper cartesian.py:40-44: max_z_velocity <= max_velocity, max_z_accel <= max_accel
    max_vel = _effective_num(norm_overrides, base_settings, "printer", "max_velocity")
    max_z_vel = _effective_num(norm_overrides, base_settings, "printer", "max_z_velocity")
    if max_vel is not None and max_z_vel is not None and max_z_vel > max_vel:
        errors.append(
            i18n.tr("En yüksek hız (%s), en yüksek Z hızından (%s) küçük olamaz.", lang)
            % (format_num(max_vel), format_num(max_z_vel))
        )

    max_acc = _effective_num(norm_overrides, base_settings, "printer", "max_accel")
    max_z_acc = _effective_num(norm_overrides, base_settings, "printer", "max_z_accel")
    if max_acc is not None and max_z_acc is not None and max_z_acc > max_acc:
        errors.append(
            i18n.tr("En yüksek ivme (%s), en yüksek Z ivmesinden (%s) küçük olamaz.", lang)
            % (format_num(max_acc), format_num(max_z_acc))
        )

    # Klipper heaters.py:28-36: max_temp > min_temp; min_temp <= min_extrude_temp <= max_temp
    ext_min = _effective_num(norm_overrides, base_settings, "extruder", "min_temp")
    ext_max = _effective_num(norm_overrides, base_settings, "extruder", "max_temp")
    min_ext = _effective_num(norm_overrides, base_settings, "extruder", "min_extrude_temp")
    if ext_min is not None and ext_max is not None and ext_max <= ext_min:
        errors.append(
            i18n.tr("Nozul en çok (%s°), min_temp (%s°) değerinden büyük olmalıdır.", lang)
            % (format_num(ext_max), format_num(ext_min))
        )
    if ext_min is not None and min_ext is not None and min_ext < ext_min:
        errors.append(
            i18n.tr("Soğuk ekstrüzyon alt (%s°), min_temp (%s°) değerinden küçük olamaz.", lang)
            % (format_num(min_ext), format_num(ext_min))
        )
    if ext_max is not None and min_ext is not None and min_ext > ext_max:
        errors.append(
            i18n.tr("Soğuk ekstrüzyon alt (%s°), nozul üst sınırından (%s°) büyük olamaz.", lang)
            % (format_num(min_ext), format_num(ext_max))
        )

    bed_min = _effective_num(norm_overrides, base_settings, "heater_bed", "min_temp")
    bed_max = _effective_num(norm_overrides, base_settings, "heater_bed", "max_temp")
    if bed_min is not None and bed_max is not None and bed_max <= bed_min:
        errors.append(
            i18n.tr("Tabla en çok (%s°), min_temp (%s°) değerinden büyük olmalıdır.", lang)
            % (format_num(bed_max), format_num(bed_min))
        )

    ace_min = _effective_num(norm_overrides, base_settings, "temperature_sensor ace_temp", "min_temp")
    ace_max = _effective_num(norm_overrides, base_settings, "temperature_sensor ace_temp", "max_temp")
    if ace_min is not None and ace_max is not None and ace_max <= ace_min:
        errors.append(
            i18n.tr("Gövde ısı en çok (%s°), min_temp (%s°) değerinden büyük olmalıdır.", lang)
            % (format_num(ace_max), format_num(ace_min))
        )

    return errors
