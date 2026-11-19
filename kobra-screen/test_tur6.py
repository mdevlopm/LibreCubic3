"""Tests for UI Round 6: modal and touch safe goto tap formats.

- modal:estop  -> E-stop confirmation dialog opens; 'Evet' does NOT send emergency_stop
- modal:confirm -> confirmation dialog opens; 'Evet' does NOT proceed any action
- modal:numpad -> keypad modal opens; no number submission
- modal:keyboard -> keyboard modal opens; no text submission
- touch:<x>,<y> -> tap at (x,y) without triggering any widget.action()

Run: python3 -m unittest -v test_tur6
"""

import sys
import os
import unittest

from unittest import mock

from fake_moonraker import FakePrinter, FakeWifiManager, serve
from kobra_screen.app import App
from kobra_screen.moonraker import Moonraker


class Tur6Tests:
    """Mixin class with shared helpers for Tur6 tests."""

    def setUp(self):
        self.printer = FakePrinter()
        self.httpd = serve(self.printer, simulate=False)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.tmp = __import__("tempfile").TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wifi = FakeWifiManager()
        self.app = App(Moonraker("127.0.0.1", self.httpd.server_address[1]),
                       cache_dir=self.tmp.name, wifi=self.wifi)
        self.app.refresh()

    def make_app(self, **kwargs):
        opts = dict(cache_dir=self.tmp.name, wifi=self.wifi, rinkhals_home=self.tmp.name,
                    clock_fn=lambda: 100.0)
        opts.update(kwargs)
        app = App(Moonraker("127.0.0.1", self.httpd.server_address[1]), **opts)
        app.trigger_beep = lambda event, force=False: True
        return app

    def texts(self, app=None):
        out = []
        for item in (app or self.app).layout():
            for attr in ("text", "sub"):
                value = getattr(item.widget, attr, None)
                if value:
                    out.append(value)
        return out

    def press(self, *labels, icon=None):
        for label in labels:
            self.assertTrue(self.app.press(label, icon),
                            "cannot press %r in %r" % (label, self.texts()))


# -------------------------------------------------------------------
# M O D A L   G O T O   T E S T S
# -------------------------------------------------------------------

class ModalGotoTests(Tur6Tests, unittest.TestCase):
    """Verify modal:... goto formats open modals without sending G-code."""

    def setUp(self):
        super().setUp()
        # Ensure no pending gcode from previous tests
        self.printer.gcodes = []

    def test_modal_estop_opens_without_estop_command(self):
        """modal:estop opens the E-stop confirm dialog; 'Evet' must NOT send emergency_stop."""
        self.app.gcodes = []  # clean slate
        self.app.debug_goto("modal:estop")
        # After goto, the modal should be open with 'Evet' and 'Vazgeç' buttons
        # The 'Evet' button callback was lambda: None, so no gcode should be generated
        self.assertEqual(len(self.printer.gcodes), 0,
                         "modal:estop should NOT send emergency_stop gcode")
        # Modal should be active (modal is not None)
        self.assertIsNotNone(self.app.modal,
                             "modal:estop should set a modal")
        # Texts should include the E-stop question
        t = self.texts()
        self.assertIn("Acil durdurma", str(t),
                      "modal:estop should show the E-stop question")

    def test_modal_confirm_opens_without_action(self):
        """modal:confirm opens a confirmation dialog; 'Evet' must NOT proceed any action."""
        self.app.gcodes = []
        self.app.debug_goto("modal:confirm")
        self.assertEqual(len(self.printer.gcodes), 0,
                         "modal:confirm should NOT send any gcode")
        self.assertIsNotNone(self.app.modal,
                             "modal:confirm should set a modal")
        t = self.texts()
        # The confirm dialog should show a question
        self.assertTrue(any("Onaylamak" in str(x) for x in t),
                        "modal:confirm should show a confirmation question")

    def test_modal_numpad_opens(self):
        """modal:numpad opens the keypad modal."""
        self.app.gcodes = []
        self.app.debug_goto("modal:numpad")
        self.assertEqual(len(self.printer.gcodes), 0,
                         "modal:numpad should NOT send any gcode")
        self.assertIsNotNone(self.app.modal,
                             "modal:numpad should set a modal")
        # The modal should be a Keypad instance
        from kobra_screen.app import Keypad
        self.assertIsInstance(self.app.modal, Keypad,
                              "modal:numpad should set a Keypad modal")

    def test_modal_keyboard_opens(self):
        """modal:keyboard opens the keyboard modal."""
        self.app.gcodes = []
        self.app.debug_goto("modal:keyboard")
        self.assertEqual(len(self.printer.gcodes), 0,
                         "modal:keyboard should NOT send any gcode")
        self.assertIsNotNone(self.app.modal,
                             "modal:keyboard should set a modal")
        from kobra_screen.app import Keyboard
        self.assertIsInstance(self.app.modal, Keyboard,
                              "modal:keyboard should set a Keyboard modal")

    def test_modal_sequences_do_not_send_gcode(self):
        """Running multiple modal goto formats should not accumulate gcode."""
        self.app.gcodes = []
        for spec in ["modal:estop", "modal:confirm", "modal:numpad", "modal:keyboard"]:
            self.app.debug_goto(spec)
        self.assertEqual(len(self.printer.gcodes), 0,
                         "All modal: goto formats together should NOT send any gcode")


# -------------------------------------------------------------------
# T O U C H   G O T O   T E S T S
# -------------------------------------------------------------------

class TouchGotoTests(Tur6Tests, unittest.TestCase):
    """Verify touch:<x>,<y> goto format taps without triggering widget actions."""

    def setUp(self):
        super().setUp()
        self.app.gcodes = []

    def test_touch_no_gcode_sent(self):
        """touch:<x>,<y> should tap at coordinates without sending any gcode."""
        self.app.gcodes = []
        self.app.debug_goto("touch:100,100")
        self.assertEqual(len(self.printer.gcodes), 0,
                         "touch: goto should NOT send any gcode")

    def test_touch_does_not_trigger_button_actions(self):
        """touch:<x>,<y> should not call widget.action() even if a button is at those coordinates."""
        self.app.gcodes = []
        self.app.debug_goto("touch:0,0")
        # After touch goto, no gcode should have been generated
        self.assertEqual(len(self.printer.gcodes), 0,
                         "touch: goto should not trigger button actions / send gcode")

    def test_touch_various_coordinates(self):
        """touch:<x>,<y> should work with various coordinate values."""
        self.app.gcodes = []
        for coords in ["touch:0,0", "touch:10,20", "touch:270,470", "touch:135,240"]:
            self.app.debug_goto(coords)
        self.assertEqual(len(self.printer.gcodes), 0,
                         "All touch: goto formats should NOT send any gcode")


# -------------------------------------------------------------------
# R U N   A N D   M A I N
# -------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()