"""Tests: Wi-Fi password QWERTY keyboard letter rows.

Verifies that the lower-case page letter rows are, in order,
"qwertyuiop", "asdfghjkl", "zxcvbnm" and that the upper-case page
starts with "QWERTYUIOP". Two angles: the source table
`_KEYBOARD_LAYERS` in kobra_screen/screens.py and the rendered
keyboard rows as seen through the App layout.

Run: python3 -m unittest test_keyboard_qwerty
"""

import unittest

from fake_moonraker import FakePrinter, FakeWifiManager, serve
from kobra_screen.app import App
from kobra_screen.moonraker import Moonraker
from kobra_screen.screens import _KEYBOARD_LAYERS
from kobra_screen.widgets import Button


LOWER_LETTER_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
UPPER_LETTER_ROWS = ("QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM")


def _row_text(row):
    """Concatenated visible label text of a keyboard row (labels, not actions)."""
    return "".join(lbl for lbl, _act, _w in row)


class QwertyKeyboardLayerTests(unittest.TestCase):
    """Direct checks against the _KEYBOARD_LAYERS source table."""

    def test_lower_page_letter_rows(self):
        lower = _KEYBOARD_LAYERS["lower"]
        self.assertEqual([_row_text(r) for r in lower[:3]], list(LOWER_LETTER_ROWS))

    def test_upper_page_letter_rows(self):
        upper = _KEYBOARD_LAYERS["upper"]
        self.assertEqual([_row_text(r) for r in upper[:3]], list(UPPER_LETTER_ROWS))

    def test_upper_page_is_shift_of_lower(self):
        # upper must be the exact uppercase mirror of the lower letter rows
        lower = _KEYBOARD_LAYERS["lower"]
        upper = _KEYBOARD_LAYERS["upper"]
        for low_row, up_row in zip(lower[:3], upper[:3]):
            self.assertEqual(_row_text(up_row), _row_text(low_row).upper())


class QwertyKeyboardRenderedTests(unittest.TestCase):
    """Checks the keyboard as the App actually renders it in the Wi-Fi flow."""

    def setUp(self):
        self.printer = FakePrinter()
        self.httpd = serve(self.printer, simulate=False)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wifi = FakeWifiManager()
        self.app = App(Moonraker("127.0.0.1", self.httpd.server_address[1]),
                       cache_dir=self.tmp.name, wifi=self.wifi)
        self.app.refresh()

    def press(self, *texts):
        for text in texts:
            self.assertTrue(self.app.press(text, icon=None),
                            "pressable button not found: %r" % text)
        self.app.refresh()

    def _kbd_keys(self):
        return [i for i in self.app.layout()
                if isinstance(i.widget, Button) and i.widget.kind == "kbd"]

    def _kbd_row_texts(self):
        """Group kbd keys into rows by their y coordinate."""
        keys = self._kbd_keys()
        rows = {}
        for item in keys:
            rows.setdefault(item.y, []).append(item)
        return [(y, "".join(i.widget.text for i in sorted(items, key=lambda x: x.x)))
                for y, items in sorted(rows.items())]

    def test_lower_page_rendered_letter_rows(self):
        self.press("Ayar", "Wi-Fi", "Ev_5G")  # opens keyboard on the lower page
        row_texts = self._kbd_row_texts()
        self.assertEqual([t for _y, t in row_texts[:3]], list(LOWER_LETTER_ROWS))
        self.press("Vazgeç")

    def test_upper_page_rendered_letter_rows(self):
        self.press("Ayar", "Wi-Fi", "Ev_5G")
        self.press("Shift")  # switch to upper page
        row_texts = self._kbd_row_texts()
        self.assertEqual([t for _y, t in row_texts[:3]], list(UPPER_LETTER_ROWS))
        self.press("Vazgeç")


if __name__ == "__main__":
    unittest.main()