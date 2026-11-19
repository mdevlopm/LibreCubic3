"""Touchscreen UI for the Kobra 3 running mainline Klipper.

On the printer:   python -m kobra_screen
PC preview:       python -m kobra_screen --preview out.png [--tab control] [--press "Gir" ...]
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import os
import sys
import threading
import time

from . import i18n
from .moonraker import Moonraker
from .network import WifiManager
from .app import App


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=os.environ.get("MOONRAKER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MOONRAKER_PORT", "7125")))
    parser.add_argument("--preview", metavar="PNG", help="render one frame to a file instead of the screen")
    parser.add_argument("--tab", choices=[tab for _, tab, _ in App.TABS])
    parser.add_argument("--lang", choices=i18n.language_codes(), help="override UI language")
    parser.add_argument("--press", action="append", default=[], metavar="TEXT",
                        help="press the button with this label before rendering (repeatable)")
    args = parser.parse_args()
    client = Moonraker(args.host, args.port)

    if args.preview:
        from .render_pil import render

        app = App(client, lang=args.lang)
        app.refresh()
        if args.tab:
            app.select_tab(args.tab)
        for text in args.press:
            if not app.press(text):
                parser.error("basılabilir düğme yok: %r" % text)
            app.refresh()
        render(app.layout()).save(args.preview)
        return

    from .render_lvgl import LvglScreen

    executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="kobra-worker")
    stop_event = threading.Event()
    wifi = WifiManager()
    screen = LvglScreen(on_k3sysui_stopped=wifi.ensure_connected)
    def run_urgent(fn):
        threading.Thread(target=fn, name="kobra-urgent", daemon=True).start()

    app = App(
        client,
        run_async=lambda fn: executor.submit(fn),
        run_urgent=run_urgent,
        on_return_to_stock=screen.request_stop,
        wifi=wifi,
        touch_backend=screen.touch_backend(),
        display_backend=screen.display_backend(),
    )

    def poll():
        while not stop_event.is_set():
            try:
                app.refresh()
            except Exception as exc:  # noqa: BLE001 - one bad answer must not end the poll thread for good
                sys.stderr.write("kobra-screen: poll error: %r\n" % (exc,))
                stop_event.wait(1.0)
            app.wait_poll(stop_event)

    def wifi_supervise():
        wifi.ensure_connected()
        app.refresh_wifi_status()
        while not stop_event.is_set():
            stop_event.wait(10.0)
            if stop_event.is_set():
                break
            wifi.ensure_connected()
            app.refresh_wifi_status()

    threading.Thread(target=poll, name="kobra-poll", daemon=True).start()
    threading.Thread(target=wifi_supervise, name="kobra-wifi", daemon=True).start()
    try:
        screen.run(app)
    finally:
        stop_event.set()
        executor.shutdown(wait=False)


if __name__ == "__main__":
    main()
