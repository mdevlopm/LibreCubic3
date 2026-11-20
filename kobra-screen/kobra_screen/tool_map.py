"""Match a print file's tools to the ACE slots that hold the right filament.

The slicer writes which tools the file uses (referenced_tools) and the material and colour of each
(filament_type, filament_colors). Before a print the screen picks a loaded slot for every used tool and
sends ACE_SET_TOOL_MAP, so a single-colour file sliced for T0 prints from the slot that holds its PLA
instead of waiting on an empty slot 1.

Choice per tool, most important first:
  1. only slots the ACE reports as loaded ("ready")
  2. same material (PLA = PLA); same family next (PLA+ for PLA); never a different material
  3. closest colour to the slicer's colour (only tells slots apart when their colour was set or read by RFID)
  4. the slot already loaded in the toolhead (saves a filament change)
  5. the lowest slot number
Different tools get different slots; tools with the same material and colour may share one. The user can
change any choice on the screen (overrides).
"""

import re

NO_SLOT_COST = 100.0  # a tool left without a slot; any real match is cheaper
FAMILY_COST = 10.0  # PLA+ for PLA: allowed, but any exact material match wins
UNKNOWN_COLOUR = 0.5  # colour distance when the file or the slot has no colour
LOADED_BONUS = 0.02  # prefer the slot in the toolhead over a slightly closer colour
MAX_SEARCH_SLOTS = 8  # exhaustive search up to this many loaded slots, greedy beyond


def used_tools(meta):
    tools = sorted({int(t) for t in (meta.get("referenced_tools") or ()) if str(t).lstrip("-").isdigit()})
    tools = [t for t in tools if t >= 0]
    return tools or [0]  # a file without T commands prints with the start macro's T0


def _split(value):
    if isinstance(value, (list, tuple)):
        return [str(v).strip().strip('"') for v in value]
    return [v.strip().strip('"') for v in str(value or "").split(";")]


def hex_to_rgb(text):
    text = str(text or "").strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", text):
        return None
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def file_filament(meta, tool):
    """(material, rgb or None) the slicer set for a tool."""
    types = _split(meta.get("filament_type"))
    colours = meta.get("filament_colors") or meta.get("extruder_colors") or ()
    material = types[tool] if tool < len(types) else ""
    rgb = hex_to_rgb(colours[tool]) if tool < len(colours) else None
    return material, rgb


def _norm(material):
    return re.sub(r"\s+", "", str(material or "").upper())


def _family(material):
    m = re.match(r"[A-Z]+", _norm(material))
    return m.group(0) if m else ""


def material_cost(wanted, loaded):
    """0 same material, FAMILY_COST same family, None different (never chosen automatically)."""
    if not _norm(wanted):
        return 0.0  # the file does not say: any loaded material will do
    if _norm(wanted) == _norm(loaded):
        return 0.0
    if _family(wanted) and _family(wanted) == _family(loaded):
        return FAMILY_COST
    return None


def colour_distance(a, b):
    if a is None or b is None:
        return UNKNOWN_COLOUR
    return (sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5) / (3 ** 0.5 * 255.0)


def _slot_tool(slot):
    return int(slot.get("tool", slot.get("index", 0)))


def _slot_rgb(slot):
    colour = slot.get("color")
    if isinstance(colour, (list, tuple)) and len(colour) == 3:
        return tuple(int(c) for c in colour)
    return None


def loaded_slots(slots):
    return [s for s in (slots or ()) if isinstance(s, dict) and str(s.get("status") or "").lower() == "ready"]


def _pair_cost(want, slot, current_tool):
    material, rgb = want
    mat = material_cost(material, slot.get("material"))
    if mat is None:
        return None
    cost = mat + colour_distance(rgb, _slot_rgb(slot))
    if _slot_tool(slot) != current_tool:
        cost += LOADED_BONUS  # kept non-negative so the search can prune on partial totals
    return cost


def _assign(demands, slots, current_tool):
    """Best distinct slot (or None) for each demand; lowest slot numbers win ties."""
    slots = sorted(slots, key=_slot_tool)
    costs = [[_pair_cost(d, s, current_tool) for s in slots] for d in demands]
    if len(slots) > MAX_SEARCH_SLOTS:
        chosen, used = [], set()
        for row in costs:
            options = [(c, i) for i, c in enumerate(row) if c is not None and i not in used]
            pick = min(options)[1] if options else None
            if pick is not None:
                used.add(pick)
            chosen.append(pick)
        return [slots[i] if i is not None else None for i in chosen]

    best = [None, None]  # cost, choice

    def search(k, used, total, choice):
        if best[0] is not None and total >= best[0] - 1e-9:
            return
        if k == len(demands):
            best[0], best[1] = total, list(choice)
            return
        for i, c in enumerate(costs[k]):
            if c is not None and i not in used:
                search(k + 1, used | {i}, total + c, choice + [i])
        search(k + 1, used, total + NO_SLOT_COST, choice + [None])

    search(0, frozenset(), 0.0, [])
    return [slots[i] if i is not None else None for i in best[1]]


def plan(meta, slots, current_tool=-1, overrides=None):
    """One entry per used tool: tool, material, rgb, slot (ACE tool index or None), slot_material,
    slot_rgb, match ("exact" | "family" | "other" | "none") and manual (chosen by the user)."""
    meta = meta or {}
    loaded = loaded_slots(slots)
    by_tool = {_slot_tool(s): s for s in loaded}
    tools = used_tools(meta)
    wants = {t: file_filament(meta, t) for t in tools}
    overrides = {int(k): int(v) for k, v in (overrides or {}).items() if int(v) in by_tool}

    chosen = {t: by_tool[overrides[t]] for t in tools if t in overrides}
    taken = {_slot_tool(s) for s in chosen.values()}
    demands = []
    for t in tools:
        if t in chosen:
            continue
        key = (_norm(wants[t][0]), wants[t][1])
        if key not in demands:
            demands.append(key)
    free = [s for s in loaded if _slot_tool(s) not in taken]
    picks = _assign([(m, rgb) for m, rgb in demands], free, current_tool)
    by_demand = dict(zip(demands, picks))
    for t in tools:
        if t not in chosen:
            chosen[t] = by_demand.get((_norm(wants[t][0]), wants[t][1]))

    entries = []
    for t in tools:
        material, rgb = wants[t]
        slot = chosen.get(t)
        if slot is None:
            match = "none"
        else:
            mat = material_cost(material, slot.get("material"))
            match = "exact" if mat == 0.0 else "family" if mat is not None else "other"
        entries.append({
            "tool": t, "material": material, "rgb": rgb,
            "slot": _slot_tool(slot) if slot else None,
            "slot_material": str(slot.get("material") or "") if slot else "",
            "slot_rgb": _slot_rgb(slot) if slot else None,
            "match": match, "manual": t in overrides,
        })
    return entries


def unresolved(entries):
    return [e for e in entries or () if e["slot"] is None]


def map_command(entries):
    pairs = ",".join("%d:%d" % (e["tool"], e["slot"]) for e in entries if e["slot"] is not None)
    return "ACE_SET_TOOL_MAP MAP=%s" % pairs if pairs else "ACE_SET_TOOL_MAP CLEAR=1"


def next_slot(entries, tool, slots):
    """The loaded slot after the tool's current one (wraps), for the tap-to-change button."""
    choices = sorted(_slot_tool(s) for s in loaded_slots(slots))
    if not choices:
        return None
    current = next((e["slot"] for e in entries if e["tool"] == tool), None)
    if current not in choices:
        return choices[0]
    return choices[(choices.index(current) + 1) % len(choices)]
