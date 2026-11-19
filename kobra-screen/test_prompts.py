"""Tests for Klipper/ace-driver action:prompt_* parser and modal dialogs (ERR-14)."""

import tempfile
import unittest
from unittest import mock

from PIL import Image, ImageDraw

from fake_moonraker import FakePrinter, FakeWifiManager, serve
from kobra_screen import prompts
from kobra_screen.app import App
from kobra_screen.moonraker import Moonraker
from kobra_screen.prompts import PromptDialog
from kobra_screen.render_pil import SCALE, font, render, text_line_height, text_width, wrap
from kobra_screen.widgets import HEIGHT, WIDTH, Button, Label, Panel, button_parts, button_text_size
from test_screen import StrictFakeLvgl


class PromptParserTests(unittest.TestCase):
    def test_parse_sequence_and_reset(self):
        dlg = PromptDialog()
        self.assertTrue(prompts.parse_prompt_line("// action:prompt_begin ACE Error", dlg))
        self.assertFalse(dlg.visible)
        self.assertEqual(dlg.title, "ACE Error")

        self.assertTrue(prompts.parse_prompt_line("// action:prompt_text Line 1", dlg))
        self.assertTrue(prompts.parse_prompt_line("// action:prompt_text Line 2", dlg))
        self.assertEqual(dlg.text, "Line 1\nLine 2")

        self.assertTrue(prompts.parse_prompt_line("// action:prompt_button Retry|ACE_RETRY|primary", dlg))
        self.assertTrue(prompts.parse_prompt_line("// action:prompt_button Simple|", dlg))
        self.assertTrue(prompts.parse_prompt_line("// action:prompt_footer_button Cancel|CANCEL_PRINT|danger", dlg))
        self.assertEqual(len(dlg.buttons), 2)
        self.assertEqual((dlg.buttons[0].label, dlg.buttons[0].gcode, dlg.buttons[0].color),
                         ("Retry", "ACE_RETRY", "primary"))
        self.assertEqual((dlg.buttons[1].label, dlg.buttons[1].gcode, dlg.buttons[1].color),
                         ("Simple", "", "normal"))
        self.assertEqual((dlg.footer_buttons[0].label, dlg.footer_buttons[0].gcode, dlg.footer_buttons[0].color),
                         ("Cancel", "CANCEL_PRINT", "danger"))
        self.assertFalse(dlg.visible)

        self.assertTrue(prompts.parse_prompt_line("// action:prompt_show", dlg))
        self.assertTrue(dlg.visible)
        self.assertFalse(dlg.complete)

        self.assertTrue(prompts.parse_prompt_line("// action:prompt_end", dlg))
        self.assertFalse(dlg.visible)
        self.assertTrue(dlg.complete)

        # A subsequent prompt_begin resets old state
        self.assertTrue(prompts.parse_prompt_line("// action:prompt_begin Second", dlg))
        self.assertEqual(dlg.title, "Second")
        self.assertEqual(dlg.text, "")
        self.assertEqual(dlg.buttons, [])
        self.assertEqual(dlg.footer_buttons, [])
        self.assertFalse(dlg.visible)
        self.assertFalse(dlg.complete)

    def test_ignores_command_entries_and_non_prompt_lines(self):
        dlg = PromptDialog()
        handled = prompts.try_parse_prompt_lines([
            {"type": "command", "message": "// action:prompt_begin Ignored"},
            {"type": "response", "message": "// ACE[0]: Ready"},
            {"type": "response", "message": "!! Some error"},
        ], dlg)
        self.assertFalse(handled)
        self.assertEqual(dlg.title, "")


class PromptDialogAppTests(unittest.TestCase):
    def setUp(self):
        self.printer = FakePrinter()
        self.httpd = serve(self.printer, simulate=False)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = App(
            Moonraker("127.0.0.1", self.httpd.server_address[1]),
            cache_dir=self.tmp.name,
            wifi=FakeWifiManager(),
            rinkhals_home=self.tmp.name,
        )
        self.app.refresh()

    def test_prompt_show_gcode_extrude_lock_and_end(self):
        # 1. prompt_begin + prompt_button without prompt_show does not open modal yet
        self.printer.respond("// action:prompt_begin Toolchange Failed")
        self.printer.respond("// action:prompt_text Check filament path for T0.")
        self.printer.respond("// action:prompt_button Extrude 100mm|_EXTRUDE LENGTH=100 SPEED=300|primary")
        self.printer.respond("// action:prompt_button Resume|RESUME|primary")
        self.printer.respond("// action:prompt_footer_button Cancel|CANCEL_PRINT|danger")
        self.app._sync_gcode_errors()
        self.assertIsNone(self.app.modal)

        # 2. prompt_show opens the dialog
        self.printer.respond("// action:prompt_show")
        self.app._sync_gcode_errors()
        self.assertIsInstance(self.app.modal, PromptDialog)

        # 3. Extrusion buttons (_EXTRUDE, RESUME) are disabled/locked when extrusion_lock is True
        self.app.extrusion_lock = True
        placed = self.app.layout()
        ext_btn = next(
            i.widget for i in placed
            if isinstance(i.widget, Button) and i.widget.text == "Extrude 100mm"
        )
        self.assertIsNone(ext_btn.action)
        self.assertTrue(ext_btn.disabled)
        self.assertEqual(ext_btn.icon, "lock")
        self.assertFalse(self.app.press("Extrude 100mm"))
        self.assertFalse(self.app.handle_prompt_button("_EXTRUDE LENGTH=100 SPEED=300"))
        self.assertNotIn("_EXTRUDE LENGTH=100 SPEED=300", self.printer.gcodes)
        res_btn = next(
            i.widget for i in placed
            if isinstance(i.widget, Button) and i.widget.text == "Resume"
        )
        self.assertIsNone(res_btn.action)
        self.assertTrue(res_btn.disabled)
        self.assertEqual(res_btn.icon, "lock")
        self.assertFalse(self.app.press("Resume"))
        self.assertNotIn("RESUME", self.printer.gcodes)

        # 4. Non-extrusion button sends gcode via app.gcode()
        with mock.patch.object(self.app, "gcode", wraps=self.app.gcode) as gcode_spy:
            self.assertTrue(self.app.press("Cancel"))
            gcode_spy.assert_called_once_with("CANCEL_PRINT")
        self.assertIn("CANCEL_PRINT", self.printer.gcodes)

        # 5. Unlocking extrusion enables the _EXTRUDE and RESUME buttons and sends gcode
        self.app.extrusion_lock = False
        with mock.patch.object(self.app, "gcode", wraps=self.app.gcode) as gcode_spy:
            self.assertTrue(self.app.press("Extrude 100mm"))
            gcode_spy.assert_called_once_with("_EXTRUDE LENGTH=100 SPEED=300")
        self.assertIn("_EXTRUDE LENGTH=100 SPEED=300", self.printer.gcodes)
        with mock.patch.object(self.app, "gcode", wraps=self.app.gcode) as gcode_spy:
            self.assertTrue(self.app.press("Resume"))
            gcode_spy.assert_called_once_with("RESUME")
        self.assertIn("RESUME", self.printer.gcodes)

        # 6. prompt_end closes the modal
        self.printer.respond("// action:prompt_end")
        self.app._sync_gcode_errors()
        self.assertIsNone(self.app.modal)

    def test_close_button_does_not_reopen_on_next_poll_and_new_prompt_opens(self):
        self.printer.respond("// action:prompt_begin First Prompt")
        self.printer.respond("// action:prompt_button Continue|RESUME|primary")
        self.printer.respond("// action:prompt_show")
        self.app._sync_gcode_errors()
        self.assertIsInstance(self.app.modal, PromptDialog)

        # Dismiss locally with Kapat
        self.assertTrue(self.app.press("Kapat"))
        self.assertIsNone(self.app.modal)

        # Subsequent sync of the same gcode_store does not re-open it
        self.app._sync_gcode_errors()
        self.assertIsNone(self.app.modal)

        # A new prompt sequence opens cleanly
        self.printer.respond("// action:prompt_begin Second Prompt")
        self.printer.respond("// action:prompt_button Continue|RESUME|primary")
        self.printer.respond("// action:prompt_show")
        self.app._sync_gcode_errors()
        self.assertIsInstance(self.app.modal, PromptDialog)
        self.assertEqual(self.app.modal.title, "Second Prompt")

    def test_prompt_end_does_not_close_other_modals(self):
        self.app.confirm("Emin misiniz?", lambda: None)
        self.printer.respond("// action:prompt_end")
        self.app._sync_gcode_errors()
        self.assertIsNotNone(self.app.modal)

    def test_prompt_dialog_fits_pil_and_lvgl_in_both_languages(self):
        import kobra_screen.render_lvgl as render_lvgl

        draw = ImageDraw.Draw(Image.new("L", (1, 1)))
        self.printer.respond("// action:prompt_begin ACE Toolchange Error")
        self.printer.respond("// action:prompt_text Filament jam on slot T0. Clear path and retry.")
        self.printer.respond("// action:prompt_button Extrude 100mm|_EXTRUDE LENGTH=100 SPEED=300")
        self.printer.respond("// action:prompt_button Retry T0|ACE_CHANGE_TOOL TOOL=0|primary")
        self.printer.respond("// action:prompt_button Resume|RESUME")
        self.printer.respond("// action:prompt_footer_button Cancel|CANCEL_PRINT|danger")
        self.printer.respond("// action:prompt_show")
        self.app._sync_gcode_errors()

        fake_lv = StrictFakeLvgl()
        orig_lv = render_lvgl.lv
        render_lvgl.lv = fake_lv
        try:
            screen = render_lvgl.LvglScreen(manage_system=False)
            for lang, expected_close in (("tr", "Kapat"), ("en", "Close")):
                self.app.set_language(lang, persist=False)
                placed = self.app.layout()
                self.assertLessEqual(self.app.content_bottom, self.app.content_limit, lang)
                self.assertTrue(any(
                    isinstance(i.widget, Button) and i.widget.text == expected_close for i in placed
                ), lang)
                for item in placed:
                    self.assertGreaterEqual(item.x, 0, lang)
                    self.assertLessEqual(item.x + item.w, WIDTH, lang)
                    self.assertLessEqual(item.y + item.h, HEIGHT, lang)
                    widget = item.widget
                    if isinstance(widget, Button) and widget.text:
                        _, box = button_parts(item)
                        sz = button_text_size(item)
                        lh = text_line_height(sz)
                        self.assertLessEqual(lh, box[3] + 0.5)
                        self.assertLessEqual(text_width(widget.text, sz) * 1.15, box[2], widget.text)
                    elif isinstance(widget, Label) and widget.text:
                        lh = text_line_height(widget.size)
                        self.assertLessEqual(lh, item.h + 0.5)
                        if "\n" not in widget.text and item.h < 2 * lh:
                            self.assertLessEqual(text_width(widget.text, widget.size) * 1.15, item.w, widget.text)
                        else:
                            lines = wrap(draw, widget.text, font(widget.size), (item.w / 1.15) * SCALE)
                            self.assertLessEqual(len(lines) * lh, item.h + 1.5, widget.text)
                self.assertEqual(render(placed).size, (WIDTH, HEIGHT))
                screen.sync(placed)
                screen.sync(placed)
            screen.close()
        finally:
            render_lvgl.lv = orig_lv


if __name__ == "__main__":
    unittest.main()
