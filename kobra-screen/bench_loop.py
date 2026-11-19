"""Benchmark idle CPU, layout/sync frequency, and loop iterations (Before vs After).

Usage:
    python3 bench_loop.py [--duration 10] [--profile]
"""

import argparse
import cProfile
import io
import os
import pstats
import tempfile
import threading
import time

import test_screen
from fake_moonraker import FakePrinter, serve
from kobra_screen import render_lvgl
from kobra_screen.app import App
from kobra_screen.moonraker import Moonraker


def _run_legacy_baseline(app, screen, duration):
    """Simulate the pre-optimization 5ms fixed loop + 0.5s full layout/sync + unconditional dirty."""
    last_sync = 0.0
    last_tick = time.monotonic()
    deadline = last_tick + duration
    while time.monotonic() < deadline:
        now = time.monotonic()
        elapsed = max(1, int((now - last_tick) * 1000))
        last_tick = now
        render_lvgl.lv.tick_inc(elapsed)
        render_lvgl.lv.timer_handler()
        if app.touch_cal is not None:
            app.tick_calibration()
        app.tick_display()
        screen._is_touch_active()
        with app._lock:
            if app.dirty or now - last_sync > 0.5:
                app.dirty = False
                last_sync = now
                placed = app.layout()
            else:
                placed = None
        if placed is not None:
            # Legacy sync updated every widget unconditionally
            if screen._widget_keys:
                screen._widget_keys = [None] * len(placed)
            screen.sync(placed)
        time.sleep(render_lvgl.MAIN_LOOP_SLEEP_SEC)


def measure_scenario(name, duration=10.0, legacy=False, tab="home", control_seg="heat",
                     temp_jitter=True, timer_return_ms=0, profile=False):
    fake_lv = test_screen.StrictFakeLvgl()
    orig_lv = render_lvgl.lv
    render_lvgl.lv = fake_lv

    loop_iters = [0]
    lv_calls = [0]
    orig_dispatch = fake_lv._dispatch_top_fn

    def counted_dispatch(fn_name, args):
        lv_calls[0] += 1
        if fn_name == "timer_handler":
            loop_iters[0] += 1
            return timer_return_ms
        return orig_dispatch(fn_name, args)

    object.__setattr__(fake_lv, "_dispatch_top_fn", counted_dispatch)

    printer = FakePrinter()
    httpd = serve(printer, simulate=False)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            screen = render_lvgl.LvglScreen(manage_system=False, grab_touch=True)
            # Clear startup touch window only after initial frame so idle behavior dominates 10s
            screen._last_touch = time.monotonic() - 2.0
            app = App(Moonraker("127.0.0.1", httpd.server_address[1]), cache_dir=tmp)
            app.refresh()
            if tab != "home":
                app.select_tab(tab)
            if tab == "control":
                app.control_seg = control_seg

            layout_calls = [0]
            layout_time = [0.0]
            sync_calls = [0]
            sync_time = [0.0]
            update_calls = [0]

            orig_layout = app.layout
            orig_sync = screen.sync
            orig_update = screen._update

            def counted_layout():
                t0 = time.perf_counter()
                res = orig_layout()
                layout_time[0] += time.perf_counter() - t0
                layout_calls[0] += 1
                return res

            def counted_sync(placed):
                t0 = time.perf_counter()
                res = orig_sync(placed)
                sync_time[0] += time.perf_counter() - t0
                sync_calls[0] += 1
                return res

            def counted_update(obj, widget):
                update_calls[0] += 1
                return orig_update(obj, widget)

            app.layout = counted_layout
            screen.sync = counted_sync
            screen._update = counted_update

            stop_evt = threading.Event()
            poll_idx = [0]

            def poller():
                while not stop_evt.is_set():
                    if temp_jitter:
                        # Simulate ±0.12 C ADC noise when idle at ~25 C
                        delta = 0.12 if (poll_idx[0] % 2 == 0) else -0.08
                        printer.status["extruder"]["temperature"] = 24.0 + delta
                        printer.status["heater_bed"]["temperature"] = 23.0 + delta
                    poll_idx[0] += 1
                    app.refresh()
                    if legacy:
                        with app._lock:
                            app.dirty = True
                    stop_evt.wait(1.0)

            t = threading.Thread(target=poller, daemon=True)
            t.start()

            if not legacy:
                def stopper():
                    time.sleep(duration)
                    screen.request_stop()
                threading.Thread(target=stopper, daemon=True).start()

            pr = cProfile.Profile() if profile else None
            cpu0 = time.process_time()
            wall0 = time.monotonic()
            if pr:
                pr.enable()
            if legacy:
                _run_legacy_baseline(app, screen, duration)
            else:
                screen.run(app)
            if pr:
                pr.disable()
            wall_dt = time.monotonic() - wall0
            cpu_dt = time.process_time() - cpu0
            stop_evt.set()
            t.join(timeout=2.0)
            screen.close()

            prof_text = ""
            if pr:
                buf = io.StringIO()
                pstats.Stats(pr, stream=buf).sort_stats("tottime").print_stats(15)
                prof_text = buf.getvalue()

            return {
                "name": name,
                "wall_s": wall_dt,
                "cpu_s": cpu_dt,
                "cpu_pct": (cpu_dt / wall_dt * 100.0) if wall_dt > 0 else 0.0,
                "iters": loop_iters[0],
                "layout_calls": layout_calls[0],
                "layout_ms": layout_time[0] * 1000.0,
                "sync_calls": sync_calls[0],
                "sync_ms": sync_time[0] * 1000.0,
                "update_calls": update_calls[0],
                "lv_top_calls": lv_calls[0],
                "profile": prof_text,
            }
    finally:
        render_lvgl.lv = orig_lv
        httpd.shutdown()
        httpd.server_close()


def measure_proc_scan(duration_polls=10):
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(100, 200):
            d = os.path.join(tmp, str(i))
            os.mkdir(d)
            with open(os.path.join(d, "comm"), "w", encoding="utf-8") as f:
                f.write("kworker/0:1\n" if i % 5 else "python3\n")
            with open(os.path.join(d, "cmdline"), "wb") as f:
                f.write(b"" if i % 5 else b"python3\x00/usr/bin/something.py\x00")
        t0 = time.perf_counter()
        for _ in range(duration_polls):
            render_lvgl._find_pids("rinkhals-ui.py", tmp)
            render_lvgl._find_pids("K3SysUi", tmp)
        before_ms = (time.perf_counter() - t0) * 1000.0

        t1 = time.perf_counter()
        for _ in range(max(1, duration_polls // 2)):
            render_lvgl._find_pids_multi(("rinkhals-ui.py", "K3SysUi"), tmp)
        after_ms = (time.perf_counter() - t1) * 1000.0
    return before_ms, after_ms


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=10.0, help="seconds per scenario (default: 10)")
    parser.add_argument("--profile", action="store_true", help="print cProfile top functions")
    args = parser.parse_args()

    scenarios = [
        ("Home idle (BEFORE)", dict(legacy=True, tab="home")),
        ("Home idle (AFTER, 35ms)", dict(legacy=False, tab="home", timer_return_ms=0)),
        ("Home idle (AFTER, 10ms)", dict(legacy=False, tab="home", timer_return_ms=10)),
        ("Heat chart idle (BEFORE)", dict(legacy=True, tab="control", control_seg="heat")),
        ("Heat chart idle (AFTER)", dict(legacy=False, tab="control", control_seg="heat", timer_return_ms=0)),
    ]
    results = []
    for title, kw in scenarios:
        res = measure_scenario(title, duration=args.duration, profile=args.profile, **kw)
        results.append(res)

    proc_before_ms, proc_after_ms = measure_proc_scan(duration_polls=max(2, int(round(args.duration))))

    print("\n=== kobra-screen Idle Loop Benchmark (%.1fs per scenario) ===" % args.duration)
    print("%-26s %7s %7s %7s %13s %13s %8s %8s" % (
        "Scenario", "Iters", "CPU(s)", "CPU%", "layout() / ms", "sync() / ms", "_update", "lv_top"
    ))
    print("-" * 100)
    for r in results:
        print("%-26s %7d %7.3f %6.2f%% %5d / %5.1fms %5d / %5.1fms %8d %8d" % (
            r["name"], r["iters"], r["cpu_s"], r["cpu_pct"],
            r["layout_calls"], r["layout_ms"], r["sync_calls"], r["sync_ms"],
            r["update_calls"], r["lv_top_calls"],
        ))
    print("\nProc scan (100 mock PIDs, %.0fs): BEFORE=%.2fms (2x/1s) -> AFTER=%.2fms (1x/2s)" % (
        args.duration, proc_before_ms, proc_after_ms
    ))
    if args.profile:
        for r in results[:2]:
            print("\n--- cProfile: %s ---\n%s" % (r["name"], r["profile"]))


if __name__ == "__main__":
    main()
