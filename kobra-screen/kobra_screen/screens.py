"""Every screen: facade re-exporting modular screen builders, ``TABS`` and ``PAGES``."""

# Raw `dryer_status.remain_time` multiplier to convert to seconds (1 = seconds; measured on the device: seconds).

from .screens_ace import (  # noqa: F401
    _ace_disconnected_card, _ace_slot_button, _ace_speeds, _active_ace_row, _active_ace_slot,
    _conn_state_tr, _dryer_progress, _dryer_remain_seconds, _dryer_temp_limit, _open_ace_limits,
    _rfid_label, _sensor_tr, _slot_status_tr, _slots, _start_drying, _tangle_protection,
    ace_home_card, dry_presets_page, dryer, filament, spool_page,
)
from .screens_ace_detail import (  # noqa: F401
    _ace_features_view, _ace_ops_view, _ace_status_view, ace_page, slot, slot_detail,
)
from .screens_common import (  # noqa: F401
    ACE_FEED_STEPS, BRIGHTNESS_STEPS, DRY_PRESETS, DRYER_REMAIN_TIME_SCALE_SEC, EVENTS_PER_PAGE,
    EXTRUDE_STEPS, FILES_PER_PAGE, HISTORY_PER_PAGE, JOG_STEPS, OBJECTS_PER_PAGE, PAGE_TITLES, PRESETS,
    SLEEP_TIMEOUTS, SLOT_COLORS, SLOT_COLOR_NAMES, SLOT_MATERIALS, SPEED_MODES, WIFI_PER_PAGE,
    _format_date, _format_filament_mm, _format_size, _go_heat, _z_offset_text, blank,
    clamp_dryer_temperature, duration, fan_card, light_buttons, print_info, section, segmented,
    status_badge, temps,
)
from .screens_files import (  # noqa: F401
    _history_status, bed_mesh_badge, bed_mesh_page, file, files, history_detail_page, history_page,
)
from .screens_home import (  # noqa: F401
    TemperatureScreen, control, control_extra, control_heat, control_move, heat_chart_page,
    heater_card, home, home_temps_card, objects_page, offline, set_temp, tune,
)
from .screens_modals import (  # noqa: F401
    _KEYBOARD_LAYERS, _PROMPT_KIND_MAP, _make_prompt_button, format_keypad_number, keypad_range_text,
    modal, prompt_dialog_modal,
)
from .screens_settings import (  # noqa: F401
    _open_wifi_connect, _wifi_state_tr, display_page, language_page, settings, sound_page,
    system_page, touch_calibration_view, wifi_net_page, wifi_page,
)
from .screens_tools import (  # noqa: F401
    _event_source_label, _event_style, events_page, limits_page, maintenance_page, tools,
    updates_page, wizard,
)

TABS = {"home": home, "control": control, "filament": filament, "tools": tools, "settings": settings}

PAGES = {"files": files, "file": file, "tune": tune, "slot": slot, "slot_detail": slot_detail,
         "ace": ace_page, "dry_presets": dry_presets_page, "heat_chart": heat_chart_page,
         "bed_mesh": bed_mesh_page, "limits": limits_page,
         "wifi": wifi_page, "wifi_net": wifi_net_page,
         "language": language_page, "history": history_page, "history_detail": history_detail_page,
         "events": events_page, "maintenance": maintenance_page, "updates": updates_page, "system": system_page,
         "sound": sound_page, "spool": spool_page,
         "objects": objects_page, "display": display_page, "wizard": wizard}
