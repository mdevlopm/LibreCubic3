"""Lightweight runtime i18n layer and persistent language configuration (stdlib only)."""

import importlib
import json
import os
import re

from . import touch_cal

DEFAULT_LANG = "tr"
_BUILTIN_LANGUAGES = (("tr", "Türkçe"),)
_CACHE = None  # None or (languages_tuple, catalogs_dict)


def _discover_languages():
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    pkg_dir = os.path.dirname(__file__)
    found = {}
    try:
        names = sorted(os.listdir(pkg_dir))
    except OSError:
        names = []
    for fname in names:
        if not (fname.startswith("lang_") and fname.endswith(".py")):
            continue
        mod_name = fname[:-3]
        mod = importlib.import_module("." + mod_name, package=__package__)
        code = str(getattr(mod, "CODE", mod_name[5:])).strip().lower()
        name = str(getattr(mod, "NAME", code.upper())).strip()
        strings = dict(getattr(mod, "STRINGS", {}) or {})
        error_patterns = tuple(getattr(mod, "ERROR_PATTERNS", ()) or ())
        if code:
            found[code] = (name, strings, error_patterns)

    ordered = list(_BUILTIN_LANGUAGES)
    if "en" in found:
        ordered.append(("en", found["en"][0]))
    for code in sorted(found):
        if code != "en" and code != DEFAULT_LANG:
            ordered.append((code, found[code][0]))

    catalogs = {}
    if DEFAULT_LANG in found:
        catalogs[DEFAULT_LANG] = (found[DEFAULT_LANG][1], found[DEFAULT_LANG][2])
    else:
        catalogs[DEFAULT_LANG] = ({}, ())
    for code, (_, strings, patterns) in found.items():
        if code != DEFAULT_LANG:
            catalogs[code] = (strings, patterns)

    _CACHE = (tuple(ordered), catalogs)
    return _CACHE


def reload_languages():
    """Clear cached language catalogs (useful in tests when adding modules)."""
    global _CACHE
    _CACHE = None
    return _discover_languages()


def available_languages():
    """Return tuple of `(code, native_name)` pairs for all installed languages."""
    return _discover_languages()[0]


def language_codes():
    """Return tuple of supported language codes."""
    return tuple(code for code, _ in available_languages())


def is_supported_language(code):
    if not isinstance(code, str):
        return False
    return code.strip().lower() in dict(available_languages())


def normalize_lang(code, default=DEFAULT_LANG):
    if isinstance(code, str):
        token = code.strip().lower()
        if token in dict(available_languages()):
            return token
    return default


def language_name(code):
    code_norm = normalize_lang(code)
    return dict(available_languages()).get(code_norm, "Türkçe")


def tr(text, lang=DEFAULT_LANG):
    """Translate `text` into `lang`, safely falling back to the Turkish source text."""
    if not text or not isinstance(text, str):
        return text
    code = normalize_lang(lang, default=DEFAULT_LANG)
    _, catalogs = _discover_languages()
    strings, _ = catalogs.get(code, ({}, ()))
    translated = strings.get(text)
    if isinstance(translated, str) and translated:
        return translated
    if code == DEFAULT_LANG:
        if text == "confirm_disable_extrusion_lock":
            return "Hotend soğutma fanı takılı mı?"
        if text == "extrusion_locked_msg":
            return "Ekstrüzyon kilitli"
        return text
    return text


def tr_short(text, lang=DEFAULT_LANG):
    """Like `tr`, but prefers a shorter translation stored under `"~" + text` (for tight tiles); falls back to `tr`."""
    if not text or not isinstance(text, str):
        return text
    code = normalize_lang(lang, default=DEFAULT_LANG)
    if code != DEFAULT_LANG:
        _, catalogs = _discover_languages()
        strings, _ = catalogs.get(code, ({}, ()))
        short = strings.get("~" + text)
        if isinstance(short, str) and short:
            return short
    return tr(text, code)


def tr_error(text, lang=DEFAULT_LANG):
    """Translate known error messages and parameterized runtime error patterns."""
    if not text or not isinstance(text, str):
        return text
    code = normalize_lang(lang, default=DEFAULT_LANG)
    if code == DEFAULT_LANG:
        return text
    direct = tr(text, code)
    if direct != text:
        return direct
    _, catalogs = _discover_languages()
    _, patterns = catalogs.get(code, ({}, ()))
    for pattern, replacement in patterns:
        match = re.match(pattern, text)
        if match:
            return match.expand(replacement)
    return text


def missing_keys(lang, keys):
    """Return sorted list of keys from `keys` missing or empty in `lang`."""
    code = normalize_lang(lang, default="")
    if not code:
        return sorted(set(keys))
    if code == DEFAULT_LANG:
        return []
    _, catalogs = _discover_languages()
    strings, _ = catalogs.get(code, ({}, ()))
    return sorted({k for k in keys if not (isinstance(strings.get(k), str) and strings.get(k))})


def resolve_persistent_config_path(env=None, rinkhals_home=None, app_name=None, config_path=None):
    """Resolve the persistent Rinkhals `.config` JSON path (shared with touch calibration)."""
    if config_path:
        return config_path
    env = os.environ if env is None else env
    explicit = (env.get("KOBRA_CONFIG") or env.get("KOBRA_TOUCH_CONFIG") or "").strip()
    if explicit:
        return explicit
    name = app_name or env.get("KOBRA_APP_NAME") or "kobra-screen"
    home = rinkhals_home or env.get("RINKHALS_HOME") or "/useremain/home/rinkhals"
    return os.path.join(home, "apps", name + ".config")


def load_language(env=None, rinkhals_home=None, app_name=None, config_path=None):
    """Load persisted UI language from `.config`, falling back to `KOBRA_LANGUAGE` or `'tr'`."""
    env = os.environ if env is None else env
    candidates = []
    if config_path or (env.get("KOBRA_CONFIG") or env.get("KOBRA_TOUCH_CONFIG") or "").strip():
        candidates.append(resolve_persistent_config_path(
            env=env, rinkhals_home=rinkhals_home, app_name=app_name, config_path=config_path
        ))
    else:
        name = app_name or env.get("KOBRA_APP_NAME") or "kobra-screen"
        candidates.append(resolve_persistent_config_path(
            env=env, rinkhals_home=rinkhals_home, app_name=app_name, config_path=None
        ))
        if rinkhals_home is None:
            candidates.append(os.path.join("/tmp/rinkhals/apps", name + ".config"))

    for path in candidates:
        data = touch_cal._read_json_dict(path)
        raw_lang = data.get("language")
        if is_supported_language(raw_lang):
            return normalize_lang(raw_lang)

    env_lang = env.get("KOBRA_LANGUAGE")
    if is_supported_language(env_lang):
        return normalize_lang(env_lang)
    return DEFAULT_LANG


def save_language(lang, env=None, rinkhals_home=None, app_name=None, config_path=None):
    """Persist UI language into Rinkhals `.config` JSON while preserving existing keys."""
    if not is_supported_language(lang):
        raise ValueError("Unsupported language: %r" % (lang,))
    code = normalize_lang(lang)
    env = os.environ if env is None else env
    path = resolve_persistent_config_path(
        env=env, rinkhals_home=rinkhals_home, app_name=app_name, config_path=config_path
    )
    touch_cal.update_json_config(path, {"language": code})
    if env is os.environ:
        env["KOBRA_LANGUAGE"] = code
    return path
