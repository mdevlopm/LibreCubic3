"""Automatic file-tool -> ACE slot matching (kobra_screen.tool_map)."""

import unittest

from kobra_screen import tool_map


def slot(tool, material="", color=(0, 0, 0), status="ready"):
    return {"index": tool, "tool": tool, "status": status, "material": material, "color": list(color)}


# The 20 Nov test print: slots 3 (PETG) and 4 (PLA) loaded, file sliced for T0 PLA
TEST_SLOTS = [slot(0, status="empty"), slot(1, status="empty"),
              slot(2, "PETG", (40, 170, 80)), slot(3, "PLA", (245, 245, 245))]
TEST_META = {"referenced_tools": [0], "filament_type": "PLA;PLA;PLA",
             "filament_colors": ["#00C1AE", "#F4E2C1", "#ED1C24"]}


def slots_of(entries):
    return {e["tool"]: e["slot"] for e in entries}


class SingleColourTests(unittest.TestCase):
    def test_test_print_goes_to_the_pla_slot(self):
        entries = tool_map.plan(TEST_META, TEST_SLOTS)
        self.assertEqual(slots_of(entries), {0: 3})
        self.assertEqual(entries[0]["match"], "exact")
        self.assertEqual(tool_map.map_command(entries), "ACE_SET_TOOL_MAP MAP=0:3")

    def test_material_is_never_swapped_automatically(self):
        meta = dict(TEST_META, filament_type="ABS")
        entries = tool_map.plan(meta, TEST_SLOTS)
        self.assertEqual(slots_of(entries), {0: None})
        self.assertEqual(len(tool_map.unresolved(entries)), 1)

    def test_family_match_when_no_exact(self):
        slots = [slot(0, "PLA+"), slot(1, "PETG")]
        entries = tool_map.plan({"referenced_tools": [0], "filament_type": "PLA"}, slots)
        self.assertEqual(slots_of(entries), {0: 0})
        self.assertEqual(entries[0]["match"], "family")

    def test_exact_beats_family(self):
        slots = [slot(0, "PLA+"), slot(1, "PLA")]
        entries = tool_map.plan({"referenced_tools": [0], "filament_type": "PLA"}, slots)
        self.assertEqual(slots_of(entries), {0: 1})

    def test_no_referenced_tools_means_t0(self):
        entries = tool_map.plan({"filament_type": "PLA"}, TEST_SLOTS)
        self.assertEqual(slots_of(entries), {0: 3})

    def test_unknown_file_material_takes_any_loaded_slot(self):
        entries = tool_map.plan({}, TEST_SLOTS)
        self.assertEqual(slots_of(entries), {0: 2})

    def test_empty_slots_are_ignored(self):
        slots = [slot(0, "PLA", status="empty"), slot(1, "PLA")]
        entries = tool_map.plan({"filament_type": "PLA"}, slots)
        self.assertEqual(slots_of(entries), {0: 1})


class TwoSameMaterialTests(unittest.TestCase):
    """Two PLA slots: colour first, then the loaded slot, then the lower slot number."""

    def test_closest_colour_wins(self):
        slots = [slot(0, "PLA", (250, 250, 250)), slot(1, "PLA", (0, 190, 170))]
        entries = tool_map.plan(TEST_META, slots)
        self.assertEqual(slots_of(entries), {0: 1})

    def test_loaded_slot_wins_when_colours_are_unknown(self):
        slots = [slot(0, "PLA"), slot(1, "PLA")]
        entries = tool_map.plan(TEST_META, slots, current_tool=1)
        self.assertEqual(slots_of(entries), {0: 1})

    def test_lowest_slot_wins_full_tie(self):
        slots = [slot(2, "PLA"), slot(1, "PLA")]
        entries = tool_map.plan(TEST_META, slots)
        self.assertEqual(slots_of(entries), {0: 1})


class MultiColourTests(unittest.TestCase):
    def test_each_tool_gets_its_own_slot_by_colour(self):
        slots = [slot(0, "PLA", (255, 255, 255)), slot(1, "PLA", (230, 30, 30)),
                 slot(2, "PLA", (0, 200, 170)), slot(3, "PETG", (0, 0, 0))]
        meta = {"referenced_tools": [0, 1, 2], "filament_type": "PLA;PLA;PLA",
                "filament_colors": ["#00C1AE", "#F4E2C1", "#ED1C24"]}
        self.assertEqual(slots_of(tool_map.plan(meta, slots)), {0: 2, 1: 0, 2: 1})

    def test_mixed_materials(self):
        meta = {"referenced_tools": [0, 1], "filament_type": "PETG;PLA"}
        self.assertEqual(slots_of(tool_map.plan(meta, TEST_SLOTS)), {0: 2, 1: 3})

    def test_global_best_not_greedy(self):
        # Greedy would give T0 the white slot (slightly closer) and leave T1 (white) with grey
        slots = [slot(0, "PLA", (250, 250, 250)), slot(1, "PLA", (200, 200, 200))]
        meta = {"referenced_tools": [0, 1], "filament_type": "PLA;PLA",
                "filament_colors": ["#E6E6E6", "#FFFFFF"]}
        self.assertEqual(slots_of(tool_map.plan(meta, slots)), {0: 1, 1: 0})

    def test_more_colours_than_slots_leaves_one_unresolved(self):
        meta = {"referenced_tools": [0, 1, 2], "filament_type": "PLA;PLA;PLA",
                "filament_colors": ["#FF0000", "#00FF00", "#0000FF"]}
        entries = tool_map.plan(meta, [slot(0, "PLA"), slot(1, "PLA")])
        self.assertEqual(len(tool_map.unresolved(entries)), 1)

    def test_identical_filaments_share_a_slot(self):
        meta = {"referenced_tools": [0, 1], "filament_type": "PLA;PLA",
                "filament_colors": ["#FFFFFF", "#FFFFFF"]}
        entries = tool_map.plan(meta, [slot(3, "PLA")])
        self.assertEqual(slots_of(entries), {0: 3, 1: 3})
        self.assertEqual(tool_map.map_command(entries), "ACE_SET_TOOL_MAP MAP=0:3,1:3")


class OverrideTests(unittest.TestCase):
    def test_override_wins_and_others_avoid_its_slot(self):
        slots = [slot(0, "PLA"), slot(1, "PLA")]
        meta = {"referenced_tools": [0, 1], "filament_type": "PLA;PLA",
                "filament_colors": ["#FF0000", "#00FF00"]}
        entries = tool_map.plan(meta, slots, overrides={1: 0})
        self.assertEqual(slots_of(entries), {0: 1, 1: 0})
        self.assertTrue(entries[1]["manual"])

    def test_override_may_pick_another_material(self):
        entries = tool_map.plan(TEST_META, TEST_SLOTS, overrides={0: 2})
        self.assertEqual(slots_of(entries), {0: 2})
        self.assertEqual(entries[0]["match"], "other")

    def test_override_on_unloaded_slot_is_dropped(self):
        entries = tool_map.plan(TEST_META, TEST_SLOTS, overrides={0: 0})
        self.assertEqual(slots_of(entries), {0: 3})

    def test_next_slot_cycles_loaded_slots(self):
        entries = tool_map.plan(TEST_META, TEST_SLOTS)
        self.assertEqual(tool_map.next_slot(entries, 0, TEST_SLOTS), 2)
        entries = tool_map.plan(TEST_META, TEST_SLOTS, overrides={0: 2})
        self.assertEqual(tool_map.next_slot(entries, 0, TEST_SLOTS), 3)


# ---- Screen integration: print screen row, start_print sends the map ----

import tempfile  # noqa: E402

from kobra_screen import screens  # noqa: E402
from kobra_screen.app import App  # noqa: E402
from kobra_screen.moonraker import MoonrakerError  # noqa: E402


class FakeClient:
    def __init__(self, meta=None, gcode_error=None):
        self.calls = []
        self.meta = meta or {}
        self.gcode_error = gcode_error

    def query(self, objects):
        return {}

    def metadata(self, path):
        self.calls.append(("metadata", path))
        return self.meta

    def gcode(self, script, timeout=300):
        self.calls.append(("gcode", script))
        if self.gcode_error and script.startswith("ACE_SET_TOOL_MAP"):
            raise MoonrakerError(self.gcode_error)

    def print_start(self, path):
        self.calls.append(("print_start", path))

    def __getattr__(self, name):
        return lambda *a, **k: {}


def make_app(client, ace=True, cache_meta=True):
    tmp = tempfile.TemporaryDirectory()
    app = App(client, cache_dir=tmp.name, run_async=lambda fn: fn(), run_urgent=lambda fn: fn())
    app._tmp = tmp
    app.extrusion_lock = app.heating_lock = False
    app.refresh = lambda: None
    if ace:
        app.state.status["ace_instance_0"] = {"connection_state": "connected", "status": "ready",
                                             "slots": [dict(s) for s in TEST_SLOTS]}
        app.state.status["ace_state"] = {"current_index": -1}
    if cache_meta:
        app.meta["box.gcode"] = dict(TEST_META)
    return app


def texts(rows):
    out = []

    def walk(node):
        for attr in ("rows", "widgets"):
            for child in getattr(node, attr, None) or ():
                walk(child)
        if getattr(node, "text", None):
            out.append(node.text)
    for r in rows:
        walk(r)
    return out


class PrintStartToolMapTests(unittest.TestCase):
    def test_map_sent_before_print_start(self):
        client = FakeClient()
        app = make_app(client)
        self.assertTrue(app.start_print("box.gcode"))
        gcodes = [c for c in client.calls if c[0] in ("gcode", "print_start")]
        self.assertIn(("gcode", "ACE_SET_TOOL_MAP MAP=0:3"), gcodes)
        self.assertLess(gcodes.index(("gcode", "ACE_SET_TOOL_MAP MAP=0:3")), gcodes.index(("print_start", "box.gcode")))

    def test_override_is_sent_then_forgotten(self):
        client = FakeClient()
        app = make_app(client)
        app.set_print_tool_override("box.gcode", 0, 2)
        app.start_print("box.gcode")
        self.assertIn(("gcode", "ACE_SET_TOOL_MAP MAP=0:2"), client.calls)
        self.assertEqual(app.print_tool_overrides, {})

    def test_no_loaded_slot_refuses_to_start(self):
        client = FakeClient()
        app = make_app(client)
        app.meta["box.gcode"]["filament_type"] = "ABS"
        self.assertFalse(app.start_print("box.gcode"))
        self.assertNotIn("print_start", [c[0] for c in client.calls])

    def test_without_ace_no_map_is_sent(self):
        client = FakeClient()
        app = make_app(client, ace=False)
        self.assertTrue(app.start_print("box.gcode"))
        self.assertFalse(any(c[0] == "gcode" and "ACE_SET_TOOL_MAP" in c[1] for c in client.calls))
        self.assertIn(("print_start", "box.gcode"), client.calls)

    def test_metadata_fetched_when_not_cached(self):
        client = FakeClient(meta=dict(TEST_META))
        app = make_app(client, cache_meta=False)
        app.start_print("box.gcode")
        self.assertIn(("gcode", "ACE_SET_TOOL_MAP MAP=0:3"), client.calls)
        self.assertIn(("print_start", "box.gcode"), client.calls)

    def test_map_failure_does_not_start_print(self):
        client = FakeClient(gcode_error="Unknown command")
        app = make_app(client)
        app.start_print("box.gcode")
        self.assertNotIn("print_start", [c[0] for c in client.calls])


class PrintScreenToolMapTests(unittest.TestCase):
    def test_file_page_shows_chosen_slot(self):
        app = make_app(FakeClient())
        shown = texts(screens.file(app, "box.gcode"))
        self.assertIn("T0 PLA > Yuva 4 · PLA", shown)

    def test_file_page_warns_and_blocks_print_without_slot(self):
        app = make_app(FakeClient())
        app.meta["box.gcode"]["filament_type"] = "ABS"
        rows = screens.file(app, "box.gcode")
        self.assertIn("T0 ABS: dolu yuva yok", texts(rows))
        start = rows[-1].widgets[0]
        self.assertIsNone(start.action)

    def test_no_row_without_ace(self):
        app = make_app(FakeClient(), ace=False)
        self.assertFalse(any("Yuva" in t for t in texts(screens.file(app, "box.gcode"))))

    def test_tool_map_page_cycles_and_resets(self):
        app = make_app(FakeClient())
        rows = screens.tool_map_page(app, "box.gcode")
        slot_btn = rows[0].rows[0].widgets[1]
        self.assertEqual(slot_btn.text, "Yuva 4 · PLA")
        slot_btn.action()
        self.assertEqual(app.print_tool_overrides, {0: 2})
        self.assertIn("Yuva 3 · PETG", texts(screens.tool_map_page(app, "box.gcode")))
        app.clear_print_tool_overrides()
        self.assertEqual(app.print_tool_plan("box.gcode")[0]["slot"], 3)


if __name__ == "__main__":
    unittest.main()
