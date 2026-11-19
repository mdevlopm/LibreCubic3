# ace-standalone

Runs an Anycubic ACE Pro (Gen 1, JSON protocol) without a printer, from a web panel: start/stop drying, chamber temperature, slots, feed/retract filament. Needs only the Python standard library (Linux). On the printer it is installed as a Rinkhals app by the LibreCubic3 package (disabled by default).

## Running

```bash
python3 server.py --fake          # try it without hardware (software ACE)
python3 server.py                 # real ACE, port detected automatically
python3 server.py --port /dev/ttyACM0 --host 0.0.0.0   # network access (access key required)
```

Panel: http://127.0.0.1:8090/ (command-line default 8090; the Rinkhals app's `http_port` default is 8890). By default it listens on this computer only (the Rinkhals app's `bind_host` default is also `127.0.0.1`). For network access pass `--host 0.0.0.0` (`bind_host` in Rinkhals); an **access key is then mandatory**: `--access-key KEY` or `access_key` in the config file. If neither is set, the server generates a random key, writes it to the config file and prints it once to the log. All `/api/*` requests must send the key in the `X-Access-Key` header; the web page asks for it once and remembers it in the browser. No key is required on loopback (if one is configured it is still checked).

On Rinkhals the app refuses to start while vanilla-klipper is enabled and its configuration uses the ACE driver (at boot this app starts about 15 s before Klipper; two processes cannot share the serial port).

Tests: `python3 -m unittest -v`

## Wiring (computer use)

The ACE has its own mains supply; only the USB data lines go to the computer. The ACE side is a 4-pin Molex Micro-Fit; according to ACEResearch: 1 = 3.5 V (purpose unknown), 2 = GND, 3 = D-, 4 = D+. Connect only GND, D- and D+; do not connect 5 V (VBUS). The source marks the pin order as "untested": check it with a multimeter before making a cable.

## Limits

Maximum temperature and duration are set in the panel's Settings section (defaults 55 °C, 1440 min; `max_temp` at most 100 °C; file: `~/.config/ace-standalone/config.json`). They only limit what the panel sends.

## API

`GET /api/state`, `POST /api/dry {temp, duration[, fan_speed]}`, `/api/dry/stop`, `/api/feed`, `/api/unwind` `{index, length, speed}`, `/api/feed/stop`, `/api/unwind/stop`, `/api/feed_assist/start|stop {index}`, `/api/filament_info {index}`, `/api/config`, `/api/raw {method, params}` (any ACE command; `drying`/`set_fan_speed` and the `temp`/`duration`/`fan_speed` fields go through the same limits as the dedicated routes). The config file is written with mode 0600.

## Unverified points

The panel itself has been tested against `fake_ace.py`, not end to end on a real ACE. Unknowns:

- Whether the USB product name always contains "ACE" (automatic port detection relies on it; pass `--port` if it fails).
- Whether the ACE firmware has its own temperature ceiling, and whether it rejects or clamps a high target.
- Whether drying continues if the host connection drops.
- The effect of the `fan_speed` field of the `drying` command (the panel does not send it by default).

## References

- Protocol: [printers-for-people/ACEResearch](https://github.com/printers-for-people/ACEResearch) (CC0)
- Cross-check: [ACEPRO Klipper driver](https://github.com/Kobra-S1/ACEPRO) (GPL-3.0; no code copied, CRC results and command formats compared)
