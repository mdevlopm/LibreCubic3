"""Tests for Wi-Fi management, QWERTY keyboard, password secrecy, mode switching and crash fallback."""

import os
import signal
import subprocess
import tempfile
import time
import unittest

from fake_moonraker import FakePrinter, FakeWifiManager, serve
from kobra_screen import network
from kobra_screen.app import App
from kobra_screen.moonraker import Moonraker
from kobra_screen.widgets import Label


class FakeWpaBackend:
    """Simulated wpa_cli / ifconfig / ip runner backed by a real temp wpa_supplicant.conf."""

    def __init__(self, conf_path):
        self.conf_path = conf_path
        self.calls = []
        self.state = "COMPLETED"
        self.ssid = "OldNet"
        self.bssid = "11:22:33:44:55:66"
        self.ip_address = "192.168.1.55"
        self.rssi = -54
        self.iface_up = True
        self.scan_rows = [
            "11:22:33:44:55:66\t2437\t-54\t[WPA2-PSK-CCMP][ESS]\tOldNet",
            "aa:bb:cc:dd:ee:02\t5180\t-60\t[WPA2-PSK-CCMP][ESS]\tAtolye5G",
            "aa:bb:cc:dd:ee:03\t2412\t-72\t[ESS]\tMisafir",
            "aa:bb:cc:dd:ee:04\t2462\t-80\t[WPA2-PSK-CCMP][ESS]\tCaf\\xc3\\xa9",
            "aa:bb:cc:dd:ee:05\t2462\t-85\t[WPA2-PSK-CCMP][ESS]\t",
        ]
        self.signals_sent = []
        self.spawned = []
        self.wpa_running = True

    def runner(self, cmd, input_text=None, timeout=5.0):
        self.calls.append((list(cmd), input_text))
        exe = os.path.basename(cmd[0])
        if exe == "ifconfig":
            if len(cmd) >= 3 and cmd[2] == "up":
                self.iface_up = True
                return 0, "", ""
            flags = "UP BROADCAST RUNNING MULTICAST" if self.iface_up else "BROADCAST"
            inet = ("inet addr:%s  Bcast:192.168.1.255" % self.ip_address) if (self.iface_up and self.ip_address) else ""
            return 0, "wlan0     Link encap:Ethernet  %s\n          %s\n" % (flags, inet), ""
        if exe == "wpa_cli":
            if not self.wpa_running:
                return 255, "", "Failed to connect to non-global ctrl_ifname"
            args = cmd[3:] if len(cmd) >= 3 and cmd[1] == "-i" else cmd[1:]
            sub = args[0] if args else ""
            if sub == "status":
                lines = ["wpa_state=%s" % self.state]
                if self.ssid:
                    lines.append("ssid=%s" % self.ssid)
                if self.bssid:
                    lines.append("bssid=%s" % self.bssid)
                if self.ip_address:
                    lines.append("ip_address=%s" % self.ip_address)
                return 0, "\n".join(lines) + "\n", ""
            if sub == "signal_poll":
                return 0, "RSSI=%d\nLINKSPEED=72\n" % self.rssi, ""
            if sub == "scan":
                return 0, "OK\n", ""
            if sub == "scan_results":
                header = "bssid / frequency / signal level / flags / ssid"
                return 0, header + "\n" + "\n".join(self.scan_rows) + "\n", ""
            if sub == "reconfigure":
                return 0, "OK\n", ""
            if sub == "list_networks":
                ssids = network.list_saved_ssids(self.conf_path)
                lines = ["network id / ssid / bssid / flags"]
                for idx, s in enumerate(ssids):
                    cur = "[CURRENT]" if s == self.ssid else ""
                    lines.append("%d\t%s\tany\t%s" % (idx, s, cur))
                return 0, "\n".join(lines) + "\n", ""
            if sub == "select_network":
                ssids = network.list_saved_ssids(self.conf_path)
                idx = int(args[1])
                if 0 <= idx < len(ssids):
                    self.ssid = ssids[idx]
                    self.state = "COMPLETED"
                    self.ip_address = "192.168.1.99"
                    return 0, "OK\n", ""
                return 1, "FAIL\n", ""
            if sub in ("enable_network", "reconnect"):
                if self.ssid:
                    self.state = "COMPLETED"
                return 0, "OK\n", ""
            if sub == "disconnect":
                self.state = "DISCONNECTED"
                self.ssid = ""
                self.ip_address = ""
                return 0, "OK\n", ""
        return 0, "", ""

    def signal_fn(self, pid, sig):
        self.signals_sent.append((pid, sig))

    def spawn_fn(self, cmd):
        self.spawned.append(list(cmd))
        exe = os.path.basename(cmd[0])
        if exe == "wpa_supplicant":
            self.wpa_running = True


class NetworkLayerTests(unittest.TestCase):
    def _make_fake_proc(self, root, entries):
        proc = os.path.join(root, "proc")
        os.makedirs(proc, exist_ok=True)
        for pid, (comm, cmd_parts) in entries.items():
            pdir = os.path.join(proc, str(pid))
            os.makedirs(pdir, exist_ok=True)
            with open(os.path.join(pdir, "comm"), "w", encoding="utf-8") as f:
                f.write(comm + "\n")
            with open(os.path.join(pdir, "cmdline"), "wb") as f:
                f.write(b"\x00".join(p.encode("utf-8") for p in cmd_parts) + b"\x00")
        return proc

    def test_wpa_conf_detection_backup_connect_and_udhcpc_coexistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf_path = os.path.join(tmp, "userdata", "wifi_cfg", "wpa_supplicant.conf")
            os.makedirs(os.path.dirname(conf_path), exist_ok=True)
            initial_conf = 'ctrl_interface=/var/run/wpa_supplicant\n\nnetwork={\n\tssid="OldNet"\n\tpsk="oldsecret1"\n}\n'
            with open(conf_path, "w", encoding="utf-8") as f:
                f.write(initial_conf)

            proc = self._make_fake_proc(tmp, {
                "201": ("wpa_supplicant", ["wpa_supplicant", "-Dnl80211", "-i", "wlan0", "-c", conf_path]),
                "202": ("udhcpc", ["udhcpc", "-i", "wlan0", "-x", "hostname:kobra3", "-b"]),
                "203": ("udhcpc", ["udhcpc", "-i", "eth0", "-b"]),
            })
            sys_net = os.path.join(tmp, "sys_net")
            os.makedirs(os.path.join(sys_net, "wlan0"), exist_ok=True)
            with open(os.path.join(sys_net, "wlan0", "flags"), "w", encoding="utf-8") as f:
                f.write("0x1003\n")

            self.assertEqual(network.resolve_wpa_conf_path(proc_root=proc), conf_path)
            self.assertEqual(network.find_udhcpc_pids(proc_root=proc, interface="wlan0"), [202])

            backend = FakeWpaBackend(conf_path)
            mgr = network.WifiManager(
                proc_root=proc,
                sys_net_root=sys_net,
                runner=backend.runner,
                signal_fn=backend.signal_fn,
                spawn_fn=backend.spawn_fn,
                scan_wait=0.0,
            )

            st = mgr.get_status()
            self.assertEqual(st.state, "COMPLETED")
            self.assertEqual(st.ssid, "OldNet")
            self.assertEqual(st.signal_dbm, -54)
            self.assertTrue(st.udhcpc_running)

            nets = mgr.scan()
            ssids = [n.ssid for n in nets]
            self.assertIn("Atolye5G", ssids)
            self.assertIn("Café", ssids)
            self.assertNotIn("", ssids)

            secret_pw = 'My"Pass\\123!'
            mgr.connect("Atolye5G", password=secret_pw, flags="[WPA2-PSK-CCMP][ESS]")

            # .bak backup created from previous config before modification
            bak_path = conf_path + ".bak"
            self.assertTrue(os.path.isfile(bak_path))
            with open(bak_path, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), initial_conf)

            # New config contains escaped password and 0600 permissions
            self.assertEqual(os.stat(conf_path).st_mode & 0o777, 0o600)
            with open(conf_path, "r", encoding="utf-8") as f:
                updated = f.read()
            self.assertIn('ssid="Atolye5G"', updated)
            self.assertIn('psk="My\\"Pass\\\\123!"', updated)
            self.assertIn('ssid="OldNet"', updated)

            # Password must never appear in any executed command args
            for cmd, _ in backend.calls:
                self.assertNotIn(secret_pw, " ".join(cmd))

            # Must send SIGUSR1 only to wlan0 udhcpc (202), NEVER kill it or touch eth0 (203)
            self.assertEqual(backend.signals_sent, [(202, signal.SIGUSR1)])
            self.assertEqual(backend.spawned, [])

            # Forget removes network block and creates updated backup
            mgr.forget("Atolye5G")
            with open(conf_path, "r", encoding="utf-8") as f:
                after_forget = f.read()
            self.assertNotIn('ssid="Atolye5G"', after_forget)
            self.assertIn('ssid="OldNet"', after_forget)

    def test_ensure_connected_recovers_down_iface_dead_wpa_and_missing_udhcpc(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf_path = os.path.join(tmp, "wpa_supplicant.conf")
            with open(conf_path, "w", encoding="utf-8") as f:
                f.write('ctrl_interface=/var/run/wpa_supplicant\n\nnetwork={\n\tssid="Home"\n\tpsk="12345678"\n}\n')
            # Empty proc (wpa_supplicant and wlan0 udhcpc are dead; eth0 udhcpc is running)
            proc = self._make_fake_proc(tmp, {
                "303": ("udhcpc", ["udhcpc", "-i", "eth0", "-b"]),
            })
            sys_net = os.path.join(tmp, "sys_net")
            os.makedirs(os.path.join(sys_net, "wlan0"), exist_ok=True)
            with open(os.path.join(sys_net, "wlan0", "flags"), "w", encoding="utf-8") as f:
                f.write("0x1002\n")  # IFF_UP bit 0 is clear (down)

            backend = FakeWpaBackend(conf_path)
            backend.iface_up = False
            backend.wpa_running = False
            now = [100.0]
            mgr = network.WifiManager(
                conf_path=conf_path,
                proc_root=proc,
                sys_net_root=sys_net,
                runner=backend.runner,
                signal_fn=backend.signal_fn,
                spawn_fn=backend.spawn_fn,
                clock_fn=lambda: now[0],
                scan_wait=0.0,
            )
            acted = mgr.ensure_connected()
            self.assertTrue(acted)
            self.assertTrue(backend.iface_up)
            self.assertTrue(any(os.path.basename(cmd[0]) == "wpa_supplicant" and conf_path in cmd for cmd in backend.spawned))
            self.assertTrue(any(os.path.basename(cmd[0]) == "udhcpc" and "wlan0" in cmd for cmd in backend.spawned))

    def test_non_ascii_ssid_dhcp_renew_and_wrong_password_fallback(self):
        """REV-24: Non-ASCII SSID triggers DHCP renew and failed connect re-enables saved networks."""
        with tempfile.TemporaryDirectory() as tmp:
            conf_path = os.path.join(tmp, "wpa_supplicant.conf")
            with open(conf_path, "w", encoding="utf-8") as f:
                f.write('ctrl_interface=/var/run/wpa_supplicant\n\nnetwork={\n\tssid="OldNet"\n\tpsk="oldsecret1"\n}\n')
            proc = self._make_fake_proc(tmp, {
                "201": ("wpa_supplicant", ["wpa_supplicant", "-i", "wlan0", "-c", conf_path]),
                "202": ("udhcpc", ["udhcpc", "-i", "wlan0", "-x", "hostname:kobra3", "-b"]),
            })
            sys_net = os.path.join(tmp, "sys_net")
            os.makedirs(os.path.join(sys_net, "wlan0"), exist_ok=True)
            with open(os.path.join(sys_net, "wlan0", "flags"), "w", encoding="utf-8") as f:
                f.write("0x1003\n")

            backend = FakeWpaBackend(conf_path)
            now = [100.0]
            mgr = network.WifiManager(
                conf_path=conf_path,
                proc_root=proc,
                sys_net_root=sys_net,
                runner=backend.runner,
                signal_fn=backend.signal_fn,
                spawn_fn=backend.spawn_fn,
                clock_fn=lambda: now[0],
                scan_wait=0.0,
            )

            # 1. Non-ASCII SSID ("Café"): wpa_cli status reports escaped UTF-8 (Caf\xc3\xa9)
            mgr.connect("Café", password="secretpass1")
            backend.signals_sent.clear()
            backend.ssid = r"Caf\xc3\xa9"
            backend.state = "COMPLETED"
            backend.ip_address = "192.168.1.55"
            now[0] += 20.0
            self.assertTrue(mgr.ensure_connected())
            self.assertEqual(backend.signals_sent, [(202, signal.SIGUSR1)])
            self.assertIsNone(mgr._pending_dhcp_renew)

            # 2. Wrong password on new network: select_network disabled OldNet, then wpa_supplicant
            # stays DISCONNECTED/SCANNING -> ensure_connected must call enable_network all + reconnect.
            mgr.connect("WrongPwNet", password="badpassword")
            backend.calls.clear()
            backend.ssid = ""
            backend.ip_address = ""
            backend.state = "DISCONNECTED"
            now[0] += 20.0
            self.assertTrue(mgr.ensure_connected())
            wpa_cmds = [
                cmd[3:] for cmd, _ in backend.calls
                if os.path.basename(cmd[0]) == "wpa_cli"
            ]
            self.assertIn(["enable_network", "all"], wpa_cmds)
            self.assertIn(["reconnect"], wpa_cmds)
            self.assertIsNone(mgr._pending_dhcp_renew)

            # Also verify fallback when stuck in SCANNING after failed auth
            mgr.connect("WrongPwNet2", password="badpassword")
            backend.calls.clear()
            backend.ssid = ""
            backend.ip_address = ""
            backend.state = "SCANNING"
            now[0] += 20.0
            self.assertTrue(mgr.ensure_connected())
            wpa_cmds = [
                cmd[3:] for cmd, _ in backend.calls
                if os.path.basename(cmd[0]) == "wpa_cli"
            ]
            self.assertIn(["enable_network", "all"], wpa_cmds)
            self.assertIn(["reconnect"], wpa_cmds)


class WifiUiAndModeSwitchTests(unittest.TestCase):
    def setUp(self):
        self.printer = FakePrinter()
        self.httpd = serve(self.printer, simulate=False)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wifi = FakeWifiManager()
        self.app = App(Moonraker("127.0.0.1", self.httpd.server_address[1]), cache_dir=self.tmp.name, wifi=self.wifi)
        self.app.refresh()

    def test_wifi_connect_flow_and_password_never_shown_or_logged(self):
        self.assertTrue(self.app.press("Ayar"))
        self.assertTrue(self.app.press("Wi-Fi"))
        labels = [i.widget.text for i in self.app.layout() if hasattr(i.widget, "text")]
        self.assertIn("AtolyeWiFi", labels)
        self.assertIn("Ev_5G", labels)
        self.assertIn("192.168.1.42 · -52 dBm", labels)

        # Select secured network -> opens QWERTY keyboard
        self.assertTrue(self.app.press("Ev_5G"))
        # Enter mixed-case + digit + symbol password: "Ab1!2345"
        self.assertTrue(self.app.press("Shift"))
        self.assertTrue(self.app.press("A"))
        self.assertTrue(self.app.press("abc"))
        self.assertTrue(self.app.press("b"))
        self.assertTrue(self.app.press("123"))
        self.assertTrue(self.app.press("1"))
        self.assertTrue(self.app.press("!"))
        for d in "2345":
            self.assertTrue(self.app.press(d))

        # Verify password is NEVER shown plaintext on screen (only masked asterisks)
        shown_texts = [
            i.widget.text for i in self.app.layout()
            if isinstance(i.widget, Label)
        ]
        self.assertIn("********", shown_texts)
        joined = "\n".join(shown_texts)
        self.assertNotIn("Ab1!2345", joined)

        self.assertTrue(self.app.press("Bağlan"))
        self.assertEqual(self.wifi.ssid, "Ev_5G")
        self.assertEqual(self.wifi.saved["Ev_5G"], "Ab1!2345")
        self.assertNotIn("Ab1!2345", self.app.message)

        # Forget via network detail page
        self.assertTrue(self.app.press("Ev_5G"))
        self.assertTrue(self.app.press("Unut"))
        self.assertTrue(self.app.press("Evet"))
        self.assertNotIn("Ev_5G", self.wifi.saved)

    def test_offline_return_to_stock_falls_back_to_system_reboot(self):
        rinkhals_home = os.path.join(self.tmp.name, "rinkhals_home")
        os.makedirs(os.path.join(rinkhals_home, "apps", "kobra-screen"))
        os.makedirs(os.path.join(rinkhals_home, "apps", "vanilla-klipper"))
        reboots = []
        app = App(
            self.app.client,
            cache_dir=self.tmp.name,
            rinkhals_home=rinkhals_home,
            wifi=self.wifi,
        )
        app._system_reboot = lambda: reboots.append(True)
        app.refresh()
        self.httpd.shutdown()
        self.httpd.server_close()
        app.refresh()
        self.assertFalse(app.state.connected)
        self.assertTrue(app.press("Stok moda dön"))
        self.assertTrue(app.press("Evet"))
        self.assertEqual(reboots, [True])
        self.assertTrue(os.path.exists(os.path.join(rinkhals_home, "apps", "kobra-screen", ".disabled")))
        self.assertTrue(os.path.exists(os.path.join(rinkhals_home, "apps", "vanilla-klipper", ".disabled")))

    def test_app_sh_crash_loop_disables_open_mode_and_restores_stock_ui_while_preserving_wifi(self):
        app_sh = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.sh")
        with tempfile.TemporaryDirectory() as tmp:
            rinkhals_root = os.path.join(tmp, "rinkhals")
            rinkhals_home = os.path.join(tmp, "rinkhals_home")
            os.makedirs(os.path.join(rinkhals_root, "opt/rinkhals/ui"))
            os.makedirs(os.path.join(rinkhals_home, "apps", "kobra-screen"))
            os.makedirs(os.path.join(rinkhals_home, "apps", "vanilla-klipper"))
            proc_root = os.path.join(tmp, "proc")
            os.makedirs(proc_root)
            log_dir = os.path.join(tmp, "logs")
            os.makedirs(log_dir)
            stock_gk_dir = os.path.join(tmp, "stock_gk")
            os.makedirs(stock_gk_dir)
            k3_restored = os.path.join(tmp, "k3_restored")
            k3_bin = os.path.join(stock_gk_dir, "K3SysUi")
            with open(k3_bin, "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\n: > %r\n" % k3_restored)
            os.chmod(k3_bin, 0o755)

            vk_stopped = os.path.join(tmp, "vk_stopped")
            with open(os.path.join(rinkhals_root, "tools.sh"), "w", encoding="utf-8") as f:
                f.write(
                    "APP_STATUS_STARTED='started'\n"
                    "APP_STATUS_STOPPED='stopped'\n"
                    "log() { echo \"$1\"; }\n"
                    "report_status() { echo \"Status: $1,$2,$3\"; }\n"
                    "get_app_property() { echo \"\"; }\n"
                    "stop_app() { [ \"$1\" = \"vanilla-klipper\" ] && : > %r; }\n" % vk_stopped
                )

            # Fake running wpa_supplicant and udhcpc for wlan0 + eth0
            dummy_wpa = subprocess.Popen(["sleep", "60"])
            dummy_udhcpc_wlan = subprocess.Popen(["sleep", "60"])
            dummy_udhcpc_eth = subprocess.Popen(["sleep", "60"])
            try:
                for pid, comm, parts in (
                    (dummy_wpa.pid, "wpa_supplicant", ["wpa_supplicant", "-Dnl80211", "-i", "wlan0", "-c", "/userdata/wifi_cfg/wpa_supplicant.conf"]),
                    (dummy_udhcpc_wlan.pid, "udhcpc", ["udhcpc", "-i", "wlan0", "-x", "hostname:kobra3", "-b"]),
                    (dummy_udhcpc_eth.pid, "udhcpc", ["udhcpc", "-i", "eth0", "-b"]),
                ):
                    pdir = os.path.join(proc_root, str(pid))
                    os.makedirs(pdir)
                    with open(os.path.join(pdir, "comm"), "w", encoding="utf-8") as f:
                        f.write(comm + "\n")
                    with open(os.path.join(pdir, "cmdline"), "wb") as f:
                        f.write(b"\x00".join(p.encode("utf-8") for p in parts) + b"\x00")

                # Fake python stays alive until killed (by SIGKILL), then on 2nd run exits non-zero immediately
                count_file = os.path.join(tmp, "launch_count")
                fake_python = os.path.join(tmp, "fake_python.sh")
                with open(fake_python, "w", encoding="utf-8") as f:
                    f.write(
                        "#!/bin/sh\n"
                        "echo x >> %r\n"
                        "CNT=$(wc -l < %r | tr -d ' ')\n"
                        "if [ \"$CNT\" -ge 2 ]; then exit 1; fi\n"
                        "trap 'exit 0' TERM INT\n"
                        "while :; do sleep 0.05; done\n" % (count_file, count_file)
                    )
                os.chmod(fake_python, 0o755)

                pid_file = os.path.join(tmp, "kobra-screen.pid")
                env = dict(os.environ)
                env.update({
                    "RINKHALS_ROOT": rinkhals_root,
                    "RINKHALS_HOME": rinkhals_home,
                    "RINKHALS_LOGS": log_dir,
                    "KOBRA_PID_FILE": pid_file,
                    "KOBRA_HEARTBEAT_FILE": os.path.join(tmp, "kobra-screen.hb"),
                    "KOBRA_STOP_MARKER": os.path.join(tmp, "kobra-screen.stopping"),
                    "KOBRA_TRIAL_MARKER": os.path.join(tmp, "kobra-screen.trial_expired"),
                    "KOBRA_SUPERVISOR_PID_FILE": os.path.join(tmp, "kobra-screen-sup.pid"),
                    "KOBRA_PROC_ROOT": proc_root,
                    "KOBRA_SYS_NET": os.path.join(tmp, "sys_net"),
                    "KOBRA_STOCK_GK_DIR": stock_gk_dir,
                    "KOBRA_PYTHON_BIN": fake_python,
                    "KOBRA_START_VERIFY_SEC": "0.15",
                    "KOBRA_MAX_CRASHES": "2",
                    "KOBRA_CRASH_WINDOW_SEC": "30",
                })

                subprocess.run(["sh", app_sh, "start"], env=env, capture_output=True, text=True, check=True)
                # wpa_supplicant and udhcpc must remain untouched
                self.assertIsNone(dummy_wpa.poll())
                self.assertIsNone(dummy_udhcpc_wlan.poll())
                self.assertIsNone(dummy_udhcpc_eth.poll())

                # Wait for kobra_screen to start (supervisor launches it in background)
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline and not os.path.exists(pid_file):
                    time.sleep(0.02)
                self.assertTrue(os.path.exists(pid_file))

                with open(pid_file, "r", encoding="utf-8") as f:
                    child_pid = int(f.read().strip())
                # Kill kobra-screen with SIGKILL -> supervisor restarts once, 2nd exit triggers stock mode fallback
                os.kill(child_pid, signal.SIGKILL)

                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    if os.path.exists(k3_restored) and os.path.exists(os.path.join(rinkhals_home, "apps", "kobra-screen", ".disabled")):
                        break
                    time.sleep(0.05)

                self.assertTrue(os.path.exists(os.path.join(rinkhals_home, "apps", "kobra-screen", ".disabled")))
                self.assertTrue(os.path.exists(os.path.join(rinkhals_home, "apps", "vanilla-klipper", ".disabled")))
                self.assertTrue(os.path.exists(vk_stopped))
                self.assertTrue(os.path.exists(k3_restored))
            finally:
                for p in (dummy_wpa, dummy_udhcpc_wlan, dummy_udhcpc_eth):
                    if p.poll() is None:
                        p.terminate()
                        p.wait()


if __name__ == "__main__":
    unittest.main()
