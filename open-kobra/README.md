# LibreCubic3 — Kobra 3 USB install package

Builds, installs and uninstalls `kobra-screen`, `ace-standalone`, the ACE Pro driver, `leviq3`, numpy and the boot guard (`05-open-kobra-guard`) as a single Rinkhals SWU package for the Anycubic Kobra 3 (+ ACE Pro) running **Rinkhals** + **vanilla-klipper** + **Moonraker**.

For end-user installation steps see the [main README](../README.md). This file covers what the package does in detail.

> **Important:** the package **never** writes to Anycubic's stock partitions or binaries (`/userdata/app/gk/K3SysUi`, `gklib`, `gkapi`, stock `printer.cfg`). It only writes to `/useremain/home/rinkhals/` and to the vanilla-klipper app's `klippy/extras` and `printer_data/config`.

## Package contents

| Component | Destination | Description |
|---|---|---|
| `kobra-screen` | `/useremain/home/rinkhals/apps/kobra-screen` | Touchscreen UI for mainline Klipper + Moonraker; fonts are used from Rinkhals' `opt/rinkhals/ui/assets/` (default: **disabled**) |
| `ace-standalone` | `/useremain/home/rinkhals/apps/ace-standalone` | ACE Pro drying/filament web panel for use without the Klipper ACE driver, port `8890`, printer-local only (`127.0.0.1`; network access needs `bind_host` + access key) (default: **disabled**) |
| `05-open-kobra-guard` | `/useremain/home/rinkhals/apps/05-open-kobra-guard` | Lightweight boot guard that runs before vanilla-klipper; restores `leviq3.py` / `open_kobra_homing.cfg` from a persistent copy if deleted or corrupted, otherwise temporarily disables the matching `[include ...]` line (opens no UI or ports; default: **enabled**) |
| ACE Pro driver | `<vanilla-klipper>/klippy/extras/ace/`, `temperature_ace.py` | Replaces the vanilla-klipper app's ACE driver (originals are backed up) |
| numpy | `<vanilla-klipper>/lib/python3.11/site-packages/` | numpy 1.24.4 for uClibc, for Klipper's resonance tools; skipped if already present, failures only warn |
| `leviq3.py` | `<vanilla-klipper>/klippy/extras/leviq3.py` | Klipper LeviQ3 extension (works with `probe` / `probe_ks1` and optionally `cs1237`) |
| Persistent LeviQ3 copy | `/useremain/home/rinkhals/open-kobra/leviq3.py`, `leviq3.cfg`, `restore-leviq3.sh` | Restore source after vanilla-klipper or Rinkhals updates |
| `leviq3.cfg` | `/useremain/home/rinkhals/printer_data/config/leviq3.cfg` | Default Kobra 3 `[leviq3]` configuration (state file: `printer_data/config/leviq3_state.json`) |
| `open_kobra_homing.cfg` | `/useremain/home/rinkhals/printer_data/config/open_kobra_homing.cfg` | Sensorless XY homing protection (no re-drive of an already homed axis, back-off before/after homing, stall-clear wait and TMC current protection; `[gcode_macro G28]` compatible with `[safe_z_home]`), the stock Kobra 3 Y limit (`position_max: 260`) and X interpolation, and overrides of the vanilla-klipper print start: `NOZZLE_WIPE_SEQUENCE` (probe next to the wiper, then scrub 0.35 mm above that height), `G9111` (heat and purge over the chute, retract before travelling, probe at 140 °C, `bed_mesh`/`home_z` options set by the screen), a shorter `UNDERLINE` purge line, `_OK_PARK_PURGE` and `_OK_RETRACT_IF_HOT` |
| `open_kobra_limits.cfg` | `/useremain/home/rinkhals/printer_data/config/open_kobra_limits.cfg` | User limits managed by kobra-screen (empty at first; kept on reinstall) |
| Include block | `/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg` | `[include leviq3.cfg]`, `[include open_kobra_homing.cfg]` and `[include open_kobra_limits*.cfg]` are added just above the `SAVE_CONFIG` block of the `printer.klipper.cfg` read only by vanilla-klipper |
| Version marker | `/useremain/home/rinkhals/.open-kobra-version` | Installed package version (from `VERSION`) |

## Preconditions

The installer checks these before touching anything; if one fails it exits with an error **without changing any file**:

1. **Rinkhals is installed** (`/useremain/rinkhals/.version` and the running directory exist).
2. **The printer is an Anycubic Kobra 3** (`"modelId": "20024"` in `/userdata/app/gk/config/api.cfg`).
3. **The vanilla-klipper app is installed** (`klippy/extras` and `klippy/klippy.py` exist).
4. **The Moonraker app (`40-moonraker`) is installed.**
5. **At least 10 MB is free** on `/useremain`.

## LeviQ3 persistence and the boot guard

Mainline Klipper loads extensions only from its own `klippy/extras/` directory, and vanilla-klipper does not rebuild it at start-up. Therefore:

- updating vanilla-klipper deletes `apps/vanilla-klipper/klippy/extras/leviq3.py`,
- while `printer.klipper.cfg`, `leviq3.cfg`, `open_kobra_limits.cfg` and `leviq3_state.json` under `printer_data/config/` survive (bind mount).

If `[include leviq3.cfg]` stays active without `leviq3.py`, Klipper stops at start-up with `Unknown config object 'leviq3'`. `05-open-kobra-guard` prevents that:

- Rinkhals starts apps in `sort -uV` order, so `05-open-kobra-guard` runs before vanilla-klipper (which also starts 15 s later).
- At boot, if `leviq3.py` is missing or does not match the packaged MD5 (`leviq3.py.md5`), it is restored atomically from the persistent copy, and `[include leviq3.cfg]` and the `[leviq3]` section in `open_kobra_limits.cfg` are re-enabled if needed.
- If restoring fails, the guard comments out `[include leviq3.cfg]` (`# open-kobra/guard-disabled`) and the `[leviq3]` section (`# open-kobra/guard-disabled-leviq3`) so Klipper still starts.
- On timeout or error the guard logs and exits with `0`; it never blocks Rinkhals start-up.

**Limits of the design:**

1. If you update vanilla-klipper and start it from Manage apps without rebooting, the guard has not run yet; run `sh /useremain/home/rinkhals/open-kobra/restore-leviq3.sh` or reboot.
2. If you disable `05-open-kobra-guard` in Manage apps, there is no automatic protection at boot.
3. If you put a `[leviq3]` section directly into `printer.klipper.cfg` instead of using `[include leviq3.cfg]`, the guard cannot disable it.

## Building and testing (computer)

No Docker needed; standard Linux tools (`sh`, `tar`, `gzip`, `zip`, `md5sum`, `python3`) are enough.

```sh
./build.sh
```

Outputs:
- Install package: `dist/install/aGVscF9zb3Nf/update.swu`
- Uninstall package: `dist/uninstall/aGVscF9zb3Nf/update.swu`

Run the test suite:

```sh
./test.sh
```

The Klipper config parser test needs a Klipper checkout and the vanilla-klipper app sources; point `KLIPPER_SRC` and `VANILLA_KLIPPER_SRC` at them, otherwise that test is skipped.

## Preparing the USB stick and installing

1. Format a USB stick **FAT32** with an **MBR** partition table (GPT is not supported).
2. Copy the `dist/install/aGVscF9zb3Nf` folder to the root of the stick (`aGVscF9zb3Nf/update.swu`).
3. Insert the stick into the printer:
   - The first beep means the printer found `update.swu` and started.
   - A **second beep** means it finished.
   - **Three short beeps** mean a precondition or install error; see `aGVscF9zb3Nf/open-kobra-install.log` on the stick. Changes from that run are rolled back automatically.
4. Remove the stick.
5. **Reload Klipper:** if vanilla-klipper was running during installation, run **`FIRMWARE_RESTART`** in Mainsail/Fluidd or reboot the printer so the new extensions and `[include ...]` lines are loaded.

> Each install run makes a dated backup under `/useremain/bak/open-kobra-<date>/`; the last 2 are kept. `ace-standalone/config.json`, `open_kobra_limits.cfg`, `*.config` (`kobra-screen.config`) and the app enable/disable markers are kept across reinstalls (and `*.config` also across uninstall). A clean install creates `kobra-screen.config` with English as the UI language.
>
> Earlier versions installed `open_kobra_limits_zz_ekstruzyon_yasak.cfg` (`[extruder] min_extrude_temp: 320`, a temporary extrusion lock). It is no longer created; on upgrade the unmodified template file is deleted, a user-edited one is kept. The extrusion lock can still be turned on from the screen (`extrusion_lock`) or with `toolhead_extrusion_allowed: False` in the ACE driver config.

## Enabling the apps

So that the stock UI or the USB serial link is never taken over without the user's consent, `kobra-screen` and `ace-standalone` start **installed but disabled**.

To enable one:
1. On the printer's touchscreen open **Settings → Rinkhals → Manage apps**.
2. Tick the app (`kobra-screen` or `ace-standalone`):
   - **kobra-screen** needs vanilla-klipper and `40-moonraker` enabled. It takes over the screen from the stock `K3SysUi`.
   - **ace-standalone** is for managing only the ACE Pro without Klipper's ACE driver. By default the web panel is reachable only from the printer itself (`http://127.0.0.1:8890/`); for network access set `bind_host` to `0.0.0.0` (an access key becomes mandatory and appears in the log / `config.json`). `serial_port` and `bind_host` can be chosen on the touchscreen; to change `http_port` and `max_temp` edit `/useremain/home/rinkhals/apps/ace-standalone.config` (or `apps/ace-standalone/config.json`).

## Uninstalling

1. Delete the old `aGVscF9zb3Nf` folder from the stick.
2. Copy `dist/uninstall/aGVscF9zb3Nf` to the root of the stick.
3. Insert it and wait for the second beep.
4. The uninstaller first makes a safety backup (`open-kobra-pre-uninstall-<date>`, including `ace-standalone/config.json` and `*.config`), stops running LibreCubic3 apps, **first removes the `# open-kobra/begin ... # open-kobra/end` block from `printer.klipper.cfg`**, then removes `leviq3.py`, `leviq3.cfg`, `leviq3_state.json`, `open_kobra_homing.cfg`, `open_kobra_limits.cfg`, `/useremain/home/rinkhals/open-kobra`, `.open-kobra-version`, the numpy it installed (only if it installed it) and the `kobra-screen`, `ace-standalone`, `05-open-kobra-guard` apps. It **does not delete or roll back `printer.klipper.cfg`**, so `SAVE_CONFIG` results (Z-offset, bed mesh, PID) and your manual edits stay. The log is written to `aGVscF9zb3Nf/open-kobra-uninstall.log`.

## Recovery

If Rinkhals or an app makes the printer hang at boot, or you want to go back to the stock software:

- **Temporarily disable Rinkhals (`.disable-rinkhals`):** put an empty `.disable-rinkhals` file in the root of a FAT32 USB stick and reboot. Rinkhals does not start and the stock Anycubic software runs (`adbd` is started as a fallback). Rinkhals also leaves `/useremain/rinkhals/.disable-rinkhals` itself if its start-up fails.
- **Re-enable Rinkhals (`.enable-rinkhals`):** after fixing the problem, an empty `.enable-rinkhals` file on the stick turns Rinkhals back on at the next boot.
- **SSH / ADB:** while Rinkhals runs you can connect via SSH or ADB (port `5555`) and create `/useremain/home/rinkhals/apps/<app>/.disabled` to disable a misbehaving app.
- **Config reset or Rinkhals removal:** the Rinkhals touch menu or its `config-reset` / `rinkhals-uninstall` SWU tools reset the whole Rinkhals configuration.
