"""Tests for extrusion lock and related fixes (ERR-01, ERR-02, ERR-18, ERR-21)."""

import json
import os
import tempfile
import unittest

from fake_moonraker import FakePrinter, FakeWifiManager, serve
from kobra_screen import limits, touch_cal, wizards
from kobra_screen.app import App
from kobra_screen.moonraker import Moonraker
from kobra_screen.widgets import Button


class ExtrusionLockTests(unittest.TestCase):
    def setUp(self):
        self.printer = FakePrinter()
        self.httpd = serve(self.printer, simulate=False)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wifi = FakeWifiManager()
        self.app = App(
            Moonraker("127.0.0.1", self.httpd.server_address[1]),
            cache_dir=self.tmp.name,
            wifi=self.wifi,
            rinkhals_home=self.tmp.name,
        )
        self.app.refresh()
        # The lock is off by default; these tests exercise the lock itself, so switch it on
        self.app.extrusion_lock = True

    def test_default_extrusion_lock_is_false(self):
        fresh = App(Moonraker("127.0.0.1", self.httpd.server_address[1]), cache_dir=self.tmp.name,
                    wifi=self.wifi, rinkhals_home=self.tmp.name)
        self.assertFalse(fresh.extrusion_lock)
        self.assertFalse(limits.ConfigSchema.extrusion_lock)
        self.assertTrue(fresh.gcode("M83\nG1 E10 F150"))

    def test_locked_extrusion_not_sent_to_moonraker(self):
        self.assertTrue(self.app.extrusion_lock)
        initial_gcode_count = len(self.printer.gcodes)
        res = self.app.gcode("M83\nG1 E10 F150")
        self.assertFalse(res)
        self.assertEqual(len(self.printer.gcodes), initial_gcode_count)
        self.assertIn("Ekstrüzyon kilidi açık", self.app.message)

    test_gcode_block_positive_e = test_locked_extrusion_not_sent_to_moonraker

    def test_wizard_block_positive_e(self):
        self.assertTrue(self.app.extrusion_lock)
        initial_gcode_count = len(self.printer.gcodes)
        # Unload wizard Step 2 ("Çıkar") has positive E enter script ("M83\nG1 E5 F200\nG1 E-80 F1500\nM82")
        wiz = wizards.WIZARDS["unload"]
        run = wizards.Run(wiz, index=2)
        run.enter(self.app)
        self.assertEqual(len(self.printer.gcodes), initial_gcode_count)
        self.assertFalse(run.running)
        self.assertEqual(run.failed, "Ekstrüzyon kilidi açık")
        self.assertIn("Ekstrüzyon kilidi açık", self.app.message)

    def test_locked_retraction_and_moves_allowed(self):
        self.assertTrue(self.app.extrusion_lock)
        self.assertTrue(self.app.gcode("G1 E-10"))
        self.assertTrue(self.app.gcode("G28"))
        self.assertTrue(self.app.gcode("M104 S200"))
        # Verify Moonraker received them
        sent = list(self.printer.gcodes)
        self.assertIn("G1 E-10", sent)
        self.assertIn("G28", sent)
        self.assertIn("M104 S200", sent)

    def test_locked_macros_rejected_and_allowed_when_unlocked(self):
        self.assertTrue(self.app.extrusion_lock)
        for cmd in ("ACE_CHANGE_TOOL TOOL=0", "_EXTRUDE LENGTH=10", "SDCARD_PRINT_FILE FILENAME=test.gcode"):
            self.assertFalse(self.app.gcode(cmd))

        # Unlock and verify they can be sent
        self.app.extrusion_lock = False
        for cmd in ("ACE_CHANGE_TOOL TOOL=0", "_EXTRUDE LENGTH=10", "SDCARD_PRINT_FILE FILENAME=test.gcode"):
            self.assertTrue(self.app.gcode(cmd))
            self.assertIn(cmd, self.printer.gcodes)

    def test_wizards_positive_e_not_sent_when_locked(self):
        self.assertTrue(self.app.extrusion_lock)
        # Check all wizard steps that extrude
        for name, wiz in wizards.WIZARDS.items():
            for step in wiz.steps:
                if App.script_extrudes(step.enter):
                    initial_count = len(self.printer.gcodes)
                    self.assertFalse(self.app.gcode(step.enter))
                    self.assertEqual(len(self.printer.gcodes), initial_count)
                for _, action_gcode in (step.actions or ()):
                    if App.script_extrudes(action_gcode):
                        initial_count = len(self.printer.gcodes)
                        self.assertFalse(self.app.gcode(action_gcode))
                        self.assertEqual(len(self.printer.gcodes), initial_count)

    def test_reconcile_ace_stopping_locked_with_none_slots(self):
        # ERR-18: slots containing None must not raise AttributeError
        self.app.ace_stopping = ("feed", self.app._clock() + 10.0)
        status_with_none = {
            "ace_instance_0": {
                "connection_state": "connected",
                "slots": [None, {"status": "ready"}, None],
            }
        }
        # Should not raise exception
        self.app._reconcile_ace_stopping_locked(status_with_none)

        self.app.ace_stopping = ("retract", self.app._clock() + 10.0)
        self.app._reconcile_ace_stopping_locked(status_with_none)

    def test_err21_persistent_touch_config_takes_precedence(self):
        # ERR-21: persistent config should take priority over /tmp/rinkhals/apps/...
        rh = self.tmp.name
        perm_path = os.path.join(rh, "apps", "kobra-screen.config")
        tmp_path = "/tmp/rinkhals/apps/kobra-screen.config"
        os.makedirs(os.path.dirname(perm_path), exist_ok=True)
        os.makedirs(os.path.dirname(tmp_path), exist_ok=True)
        try:
            with open(perm_path, "w", encoding="utf-8") as f:
                json.dump({"touch_calibration": "10,20,30,40", "extrusion_lock": False}, f)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump({"touch_calibration": "99,99,99,99", "extrusion_lock": True}, f)

            loaded = limits.load_config(rinkhals_home=rh, app_name="kobra-screen")
            self.assertEqual(loaded["touch_calibration"], "10,20,30,40")
            self.assertFalse(loaded["extrusion_lock"])
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    def test_ui_toggle_confirm_dialog(self):
        # 1. lock switched on in setUp
        self.assertTrue(self.app.extrusion_lock)

        # 2. Open Settings -> System
        self.assertTrue(self.app.press("Ayar"))
        self.assertTrue(self.app.press("Sistem"))

        # Verify Ekstrüzyon kilidi button is present
        ext_btn = next((i.widget for i in self.app.layout()
                        if isinstance(i.widget, Button) and i.widget.text == "Ekstrüzyon kilidi"), None)
        self.assertIsNotNone(ext_btn)
        self.assertTrue(ext_btn.toggle)

        # 3. Pressing Ekstrüzyon kilidi to turn it OFF triggers confirm dialog
        self.assertTrue(self.app.press("Ekstrüzyon kilidi"))
        self.assertIsNotNone(self.app.modal)
        self.assertIn("Hotend soğutma fanı takılı mı?", self.app.modal.text)
        # Lock should still be True before confirmation
        self.assertTrue(self.app.extrusion_lock)

        # 4. Confirming calls app.set_extrusion_lock(False)
        self.assertTrue(self.app.press("Evet"))
        self.assertFalse(self.app.extrusion_lock)
        self.assertIsNone(self.app.modal)

        # 5. Pressing Ekstrüzyon kilidi to turn it ON does NOT need confirmation
        self.assertTrue(self.app.press("Ekstrüzyon kilidi"))
        self.assertTrue(self.app.extrusion_lock)
        self.assertIsNone(self.app.modal)

    def test_control_buttons_disabled(self):
        self.printer.status["extruder"]["temperature"] = 200.0
        self.app.control_seg = "extra"
        self.app.select_tab("control")
        self.app.refresh()

        # With extrusion_lock = True
        self.assertTrue(self.app.extrusion_lock)
        ext_btn = next((i.widget for i in self.app.layout()
                        if isinstance(i.widget, Button) and i.widget.text == "Ekstrüde"), None)
        self.assertIsNotNone(ext_btn)
        self.assertTrue(ext_btn.disabled)
        self.assertEqual(ext_btn.icon, "lock")
        self.assertEqual(ext_btn.tooltip, "extrusion_locked_msg")
        self.assertIsNone(ext_btn.action)

        # Retract (Geri çek) should remain enabled
        retract_btn = next((i.widget for i in self.app.layout()
                            if isinstance(i.widget, Button) and i.widget.text == "Geri çek"), None)
        self.assertIsNotNone(retract_btn)
        self.assertFalse(getattr(retract_btn, "disabled", False))
        self.assertEqual(retract_btn.icon, "unload")
        self.assertIsNotNone(retract_btn.action)

        # Unlock extrusion
        self.app.set_extrusion_lock(False)
        self.app.refresh()

        # Ekstrüde should now be enabled
        ext_btn = next((i.widget for i in self.app.layout()
                        if isinstance(i.widget, Button) and i.widget.text == "Ekstrüde"), None)
        self.assertIsNotNone(ext_btn)
        self.assertFalse(ext_btn.disabled)
        self.assertEqual(ext_btn.icon, "load")
        self.assertEqual(ext_btn.tooltip, "")
        self.assertIsNotNone(ext_btn.action)

    def test_err17_consecutive_ace_commands_do_not_close_prematurely(self):
        # Two consecutive ACE commands with simulated delayed response
        self.printer.ace_info_lines = False
        self.app.ace_gcode("ACE_FEED SLOT=0")
        cmd1 = self.app.ace_pending[0]
        self.assertIsNotNone(cmd1)

        self.app.ace_gcode("ACE_FEED SLOT=1")
        self.assertEqual(len(self.app._ace_pending_queue), 2)
        self.assertIs(self.app._ace_pending_queue[0], cmd1)

        # First answer arrives
        self.printer.gcode_store.append({
            "message": "// ACE[0]: feed started",
            "type": "response",
            "time": self.app._wall_clock(),
        })
        self.app._sync_gcode_errors()

        # cmd2 must still be pending; wait for cmd2 was not closed by cmd1's response
        self.assertEqual(len(self.app._ace_pending_queue), 1)
        self.assertTrue(bool(self.app.ace_pending))
        self.assertEqual(self.app._ace_pending_queue[0]["script"], "ACE_FEED SLOT=1")

    def test_moonraker_reuses_single_connection(self):
        from unittest import mock
        import http.client
        client = Moonraker("127.0.0.1", self.httpd.server_address[1])
        with mock.patch("http.client.HTTPConnection", wraps=http.client.HTTPConnection) as mock_conn:
            client.info()
            client.info()
            self.assertEqual(mock_conn.call_count, 1)

    def test_rev02_paused_resume_button_blocked_when_extrusion_lock_enabled(self):
        self.printer.status["print_stats"]["state"] = "paused"
        self.printer.status["print_stats"]["filename"] = "test.gcode"
        self.app.refresh()
        self.assertTrue(self.app.extrusion_lock)

        devam_btn = next(
            (i.widget for i in self.app.layout() if isinstance(i.widget, Button) and i.widget.text == "Devam"),
            None,
        )
        self.assertIsNotNone(devam_btn)
        self.assertEqual(devam_btn.icon, "lock")

        self.assertTrue(self.app.press("Devam"))
        self.assertEqual(self.printer.status["print_stats"]["state"], "paused")
        self.assertIn("Ekstrüzyon kilidi açık", self.app.message)
        self.assertFalse(self.app.resume_print())

        # Unlocking allows resuming
        self.app.set_extrusion_lock(False)
        self.app.refresh()
        self.assertTrue(self.app.press("Devam"))
        self.assertEqual(self.printer.status["print_stats"]["state"], "printing")

    def test_rev03_script_extrudes_blocks_all_bypass_patterns(self):
        self.assertTrue(self.app.extrusion_lock)
        blocked = [
            "START_PRINT BED=60 EXTRUDER=200",
            "PRINT_START",
            "RESUME",
            "M23 test.gcode",
            "M24",
            "T0",
            "T1",
            "T2",
            "T3",
            "G01 E5",
            "G01 E5.0 F300",
            "G00 E1",
            "G2 X10 Y10 I2 J0 E1.2",
            "G3 X0 Y0 I-2 J0 E0.5",
            "G11",
            "N10 G1 E10",
            "N10 G01 E5*42",
            "G1 X10E5",
            "G01E5",
            "FORCE_MOVE STEPPER=extruder",
            "FORCE_MOVE STEPPER=extruder DISTANCE=5 VELOCITY=5",
            "start_print",
            "g01 e5",
        ]
        for cmd in blocked:
            initial = len(self.printer.gcodes)
            self.assertTrue(App.script_extrudes(cmd), cmd)
            self.assertFalse(self.app.gcode(cmd), cmd)
            self.assertEqual(len(self.printer.gcodes), initial, cmd)

        allowed = [
            "G1 E-10",
            "G01 E-5 F300",
            "G1 X10 Y20 F3000",
            "G10",
            "G28",
            "G92 E0",
            "M104 T0 S200",
            "M140 S60",
            "FORCE_MOVE STEPPER=stepper_z DISTANCE=5 VELOCITY=5",
            "SET_STEPPER_ENABLE STEPPER=extruder ENABLE=0",
            "ACE_FEED T=0 LENGTH=50",
            "ACE_RETRACT T=0 LENGTH=50",
        ]
        for cmd in allowed:
            self.assertFalse(App.script_extrudes(cmd), cmd)
            self.assertTrue(self.app.gcode(cmd), cmd)

    def test_rev03_prompt_buttons_t0_and_resume_locked(self):
        self.printer.respond("// action:prompt_begin Filament Runout")
        self.printer.respond("// action:prompt_button Retry T0|T0|primary")
        self.printer.respond("// action:prompt_button Retract 100mm|_RETRACT LENGTH=100 SPEED=300|secondary")
        self.printer.respond("// action:prompt_footer_button Resume|RESUME|primary")
        self.printer.respond("// action:prompt_show")
        self.app._sync_gcode_errors()

        placed = self.app.layout()
        retry_btn = next(i.widget for i in placed if isinstance(i.widget, Button) and i.widget.text == "Retry T0")
        resume_btn = next(i.widget for i in placed if isinstance(i.widget, Button) and i.widget.text == "Resume")
        retract_btn = next(i.widget for i in placed if isinstance(i.widget, Button) and i.widget.text == "Retract 100mm")
        self.assertTrue(retry_btn.disabled)
        self.assertEqual(retry_btn.icon, "lock")
        self.assertTrue(resume_btn.disabled)
        self.assertEqual(resume_btn.icon, "lock")
        self.assertFalse(retract_btn.disabled)
        self.assertFalse(self.app.handle_prompt_button("T0"))
        self.assertFalse(self.app.handle_prompt_button("RESUME"))

    def test_rev04_tmp_config_never_changes_extrusion_lock(self):
        from unittest import mock

        rh = tempfile.mkdtemp(dir=self.tmp.name)
        os.makedirs(os.path.join(rh, "apps"), exist_ok=True)
        tmp_dir = "/tmp/rinkhals/apps"
        os.makedirs(tmp_dir, exist_ok=True)
        app_name = "kobra-rev04-%d" % os.getpid()
        perm_path = os.path.join(rh, "apps", app_name + ".config")
        tmp_path = os.path.join(tmp_dir, app_name + ".config")
        self.addCleanup(lambda: os.path.exists(tmp_path) and os.remove(tmp_path))

        # /tmp has extrusion_lock: false, the user switched the lock on in the persistent config:
        # /tmp must not turn it off
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"extrusion_lock": False, "moonraker_port": 7125}, f)
        with open(perm_path, "w", encoding="utf-8") as f:
            json.dump({"language": "tr", "extrusion_lock": True}, f)

        loaded = limits.load_config(rinkhals_home=rh, app_name=app_name)
        self.assertTrue(loaded["extrusion_lock"])
        self.assertEqual(loaded["moonraker_port"], 7125)

        # Also verify App.__init__ + _load_persistent_settings when rinkhals_home=None (RINKHALS_HOME env)
        with mock.patch.dict(os.environ, {"RINKHALS_HOME": rh}):
            app = App(
                Moonraker("127.0.0.1", self.httpd.server_address[1]),
                cache_dir=self.tmp.name,
                wifi=FakeWifiManager(),
                app_name=app_name,
            )
        self.assertTrue(app.extrusion_lock)

        # Missing persistent file entirely -> /tmp is ignored either way, the default (off) applies
        os.remove(perm_path)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"extrusion_lock": True, "moonraker_port": 7125}, f)
        self.assertFalse(limits.load_config(rinkhals_home=rh, app_name=app_name)["extrusion_lock"])
        with mock.patch.dict(os.environ, {"RINKHALS_HOME": rh}):
            app2 = App(
                Moonraker("127.0.0.1", self.httpd.server_address[1]),
                cache_dir=self.tmp.name,
                wifi=FakeWifiManager(),
                app_name=app_name,
            )
        self.assertFalse(app2.extrusion_lock)

        # Persistent explicit false DOES take effect
        with open(perm_path, "w", encoding="utf-8") as f:
            json.dump({"extrusion_lock": False}, f)
        self.assertFalse(limits.load_config(rinkhals_home=rh, app_name=app_name)["extrusion_lock"])
        with mock.patch.dict(os.environ, {"RINKHALS_HOME": rh}):
            app3 = App(
                Moonraker("127.0.0.1", self.httpd.server_address[1]),
                cache_dir=self.tmp.name,
                wifi=FakeWifiManager(),
                app_name=app_name,
            )
        self.assertFalse(app3.extrusion_lock)


TestExtrusionLock = ExtrusionLockTests


if __name__ == "__main__":
    unittest.main()
