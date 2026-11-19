"""A reused LVGL button must show its new text (Files/History next page).

The LVGL renderer keeps the objects when the layout shape is unchanged and
only patches them. Buttons cut their text to fit when created; the patch used
to re-apply that old cut text, so every page of a list looked like page 1.
"""

import unittest

from test_screen import StrictFakeLvgl
from kobra_screen.widgets import Button, Placed, Row, place


def _page(names):
    rows = [Row(44, [Button(n, lambda: None, sub="1.2 MB · 19.11 21:00", align="left")]) for n in names]
    rows.append(Row(44, [Button("Sonraki", lambda: None)]))
    out = []
    place(rows, 8, 40, 256, out)
    return out


def _texts(fake_root):
    found = []

    def walk(obj):
        text = getattr(obj, "text", None)
        if isinstance(text, str) and text:
            found.append(text)
        for child in getattr(obj, "children", ()):
            walk(child)

    walk(fake_root)
    return found


class ReusedButtonTextTests(unittest.TestCase):
    def setUp(self):
        import kobra_screen.render_lvgl as render_lvgl
        self.render_lvgl = render_lvgl
        self.orig_lv = render_lvgl.lv
        render_lvgl.lv = StrictFakeLvgl()
        self.screen = render_lvgl.LvglScreen(manage_system=False)

    def tearDown(self):
        self.screen.close()
        self.render_lvgl.lv = self.orig_lv

    def _label_texts(self):
        out = []
        for obj in self.screen.objects:
            for attr in ("text_label", "sub_label"):
                lbl = getattr(obj, attr, None)
                if lbl is not None:
                    out.append(self._get_text(lbl))
        return out

    @staticmethod
    def _get_text(lbl):
        for name in ("get_text", "text"):
            val = getattr(lbl, name, None)
            if callable(val):
                return val()
            if isinstance(val, str):
                return val
        raise AssertionError("fake label has no text accessor")

    def test_next_page_shows_new_names(self):
        page1 = _page(["file01.gcode", "file02.gcode", "file03.gcode"])
        page2 = _page(["file04.gcode", "file05.gcode", "file06.gcode"])
        self.screen.sync(page1)
        objects_before = list(self.screen.objects)
        self.screen.sync(page2)
        # Same shape: objects are reused, not rebuilt
        self.assertEqual([id(o) for o in objects_before], [id(o) for o in self.screen.objects])
        texts = self._label_texts()
        for name in ("file04.gcode", "file05.gcode", "file06.gcode"):
            self.assertIn(name, texts)
        for name in ("file01.gcode", "file02.gcode", "file03.gcode"):
            self.assertNotIn(name, texts)

    def test_text_needing_other_font_size_rebuilds(self):
        self.screen.sync(_page(["a.gcode"]))
        long_name = "a_very_long_file_name_for_a_benchy_print_with_many_words_v2_final.gcode"
        self.screen.sync(_page([long_name]))
        texts = self._label_texts()
        self.assertFalse(any(t == "a.gcode" for t in texts))
        self.assertTrue(any(t and t[:6] == long_name[:6] for t in texts))


if __name__ == "__main__":
    unittest.main()
