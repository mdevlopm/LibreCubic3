# kobra-screen

A Moonraker-based touchscreen UI for the Anycubic Kobra 3 (272×480 portrait touchscreen) and the ACE Pro. On the printer it runs as a Rinkhals app (`app.sh` + `app.json`); on a development computer the same screen tree is rendered to pixel-exact PNG previews with Pillow and exercised by unit tests.

Normally it is installed by the open-kobra USB package (see the [main README](../README.md)) and enabled under **Settings → Rinkhals → Manage apps**.

## Runtime architecture (Kobra 3 / Rinkhals)

- **Interpreter and LVGL binding:** Rinkhals' built-in CPython 3.11 (`/useremain/rinkhals/.current/usr/bin/python3`) and the CFFI-based LVGL 9 binding used by the Rinkhals UI (`/useremain/rinkhals/.current/opt/rinkhals/ui/lvgl/__init__.py` + `lvgl-arm-linux-uclibc.so`), in one process (`python3 -m kobra_screen`).
- **Display and touch:** `/dev/fb0` (`lv.DISPLAY_ROTATION._270`, 130 DPI) and `/dev/input/event0` (`lv.evdev_grab_device`, calibration `(25, 235, 460, 25)`). The backlight (`/sys/class/backlight/backlight/brightness`) wakes on touch.
- **Open mode and screen ownership:**
  - kobra-screen runs in **open mode** (vanilla-klipper + kobra-screen). On start the stock `K3SysUi` process is stopped completely with `SIGTERM`/`SIGKILL` (not `SIGSTOP`).
  - If `rinkhals-ui.py` is opened, the touch grab and draw loop are released; when it closes, `K3SysUi` is stopped again and the screen is taken back.
  - After `K3SysUi` stops, the `app.sh` supervisor and `kobra_screen/network.py` keep `wlan0`, `wpa_supplicant` and `udhcpc` up and reconnect when the link drops.
  - If kobra-screen fails to start or crashes repeatedly, the supervisor disables both kobra-screen and vanilla-klipper and restarts `K3SysUi`, so the next boot is guaranteed to be **stock mode**.
  - **Settings → Return to stock** (also on the "no connection" screen) asks for confirmation, disables kobra-screen and vanilla-klipper and reboots into stock mode (`gklib` + `K3SysUi`).

## Package layout

| File | Role |
|---|---|
| `kobra_screen/moonraker.py` | Moonraker HTTP client based on `urllib.request` |
| `kobra_screen/network.py` | Wi-Fi management via `wpa_supplicant` / `wpa_cli` / `udhcpc`, `.bak` backup and link watchdog |
| `kobra_screen/touch_cal.py` | Touch calibration math, raw `/dev/input/event0` reader, `.config` storage |
| `kobra_screen/i18n.py`, `lang_en.py`, `lang_tr.py` | Translation layer, persistent `language` setting, language file discovery |
| `kobra_screen/app.py` | Main application facade (`App`, navigation, frame) |
| `kobra_screen/app_models.py` | Shared constants, data classes (`Keypad`, `Confirm`, `Keyboard`, `TouchCalibration`, `State`) and `disable_rinkhals_app` |
| `kobra_screen/app_settings.py`, `app_spools.py`, `app_polling.py`, `app_ace.py`, `app_system.py`, `gcode_safety.py`, `app_touch_cal.py` | `App` mixins: settings/power/events/beeper, maintenance/updates/spools, Moonraker polling/cache, ACE Pro state, limits/system/Wi-Fi, G-code safety locks, touch calibration |
| `kobra_screen/screens.py` and `screens_*.py` | The 5 main tabs (`TABS`) and sub-pages (`PAGES`): home/control/temperatures, files/history/bed mesh, ACE/drying/spools, tools/limits/events/maintenance/updates, settings/system/Wi-Fi/calibration, modal dialogs |
| `kobra_screen/limits.py` | `open_kobra_limits.cfg` parser/writer and safety validation |
| `kobra_screen/wizards.py` | Step-by-step wizards (cold pull, load/unload, nozzle cleaning, LeviQ, Z-offset, PID, input shaper) |
| `kobra_screen/widgets.py` | 272×480 layout engine and widgets (`Row`, `Col`, `Card`, `Button`, `Label`, `Bar`, `Ring`, `Picture`) |
| `kobra_screen/render_lvgl.py`, `lvgl_sys.py`, `lvgl_widgets.py` | On-printer LVGL 9 renderer, freeze watchdog, touch/backlight/evdev helpers |
| `kobra_screen/touch_diag.py` | `touch-diag` / `touch-raw` diagnostics (`python3 -m kobra_screen.render_lvgl touch-diag`) |
| `kobra_screen/render_pil.py` | Pillow PNG preview renderer for computers (not loaded on the printer) |
| `app.sh`, `app.json` | Rinkhals app lifecycle (`status`, `start`, `stop`, `trial`, `disable`, `version`), crash supervisor and settings schema |
| `fake_moonraker.py` | Fake Klipper + ACE Pro + LeviQ3 Moonraker server and fake Wi-Fi manager |
| `test_*.py` | Unit tests: HTTP, layout (text fit in both languages), i18n, Wi-Fi/mode switching and a strict LVGL 9 contract (`StrictFakeLvgl`) |

## Running and testing on a computer

```sh
# Unit tests (fake Moonraker + strict LVGL 9 binding checks; needs Pillow)
python3 -m unittest discover -p 'test_*.py'

# PNG previews of every screen in both languages, written to preview/
python3 make_preview.py

# Save one screen as PNG against the fake Moonraker
python3 fake_moonraker.py &
python3 -m kobra_screen --port 7125 --preview out.png --tab filament --lang en
```

Text-fit tests need the printer's UI font `AlibabaSans-Regular.ttf` (not included; see the main README). Without it they are skipped.

## Settings

The Moonraker host and port and the UI language (`language`: `"en"` / `"tr"`) can be changed in the Rinkhals app settings, on the screen under **Settings → Language**, or in `/useremain/home/rinkhals/apps/kobra-screen.config`.

## Adding a language

Create `kobra_screen/lang_<code>.py` (e.g. `lang_de.py`) defining `CODE`, `NAME`, `STRINGS` (source string → translation) and optionally `ERROR_PATTERNS`. The UI source strings are Turkish; use `lang_en.py` as the reference for the keys. `kobra_screen/i18n.py` discovers `lang_*.py` modules at start-up; the new language appears in the language list and is packaged automatically by `open-kobra/build.sh`.
