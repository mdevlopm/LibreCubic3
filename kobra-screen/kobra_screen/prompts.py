"""Parse Klipper/ace-driver action:prompt commands from gcode_store responses.

Supported commands (each line starts with "// action:prompt_..."):
  // action:prompt_begin <title>
  // action:prompt_text <text>
  // action:prompt_button <label>|<gcode>|<color>
  // action:prompt_footer_button <label>|<gcode>|<color>
  // action:prompt_show
  // action:prompt_end
"""

from dataclasses import dataclass, field


@dataclass
class PromptButton:
    label: str
    gcode: str
    color: str = "normal"  # normal | primary | danger | outline_danger | active | card | ghost


@dataclass
class PromptDialog:
    title: str = ""
    text: str = ""
    buttons: list = field(default_factory=list)  # list of PromptButton
    footer_buttons: list = field(default_factory=list)  # list of PromptButton
    visible: bool = False  # set True when prompt_show is seen
    complete: bool = False  # set True when prompt_end is seen
    begin_ts: float = 0.0
    begin_key: object = None
    dismissed: bool = False

    def reset(self):
        self.title = ""
        self.text = ""
        self.buttons.clear()
        self.footer_buttons.clear()
        self.visible = False
        self.complete = False
        self.begin_ts = 0.0
        self.begin_key = None
        self.dismissed = False


def _extract_prompt_cmd(line: str):
    if not isinstance(line, str):
        return None
    msg = line.strip()
    if msg.startswith("//"):
        msg = msg[2:].strip()
    if not msg.startswith("action:prompt_"):
        return None
    return msg[len("action:prompt_"):].strip()


def is_prompt_line(line: str) -> bool:
    """Return True if `line` is a Klipper action:prompt_* response line."""
    return _extract_prompt_cmd(line) is not None


def parse_prompt_begin(line: str):
    """Return the dialog title if `line` is an action:prompt_begin command, else None."""
    cmd_part = _extract_prompt_cmd(line)
    if cmd_part is not None and (
        cmd_part == "begin" or cmd_part.startswith("begin ") or cmd_part.startswith("begin\t")
    ):
        return cmd_part[len("begin"):].strip()
    return None


def _split_button_payload(payload: str) -> tuple[str, str, str]:
    """Split 'label|gcode|color' into components. Color defaults to 'normal'."""
    parts = payload.split("|", 2)
    label = parts[0].strip() if parts else ""
    gcode = parts[1].strip() if len(parts) > 1 else ""
    color = (parts[2].strip() if len(parts) > 2 else "") or "normal"
    return label, gcode, color


def parse_prompt_line(line: str, dialog: PromptDialog, ts: float = 0.0) -> bool:
    """Parse a single gcode_store response line for action:prompt commands.

    Returns True if the line was a prompt command and was handled.
    """
    cmd_part = _extract_prompt_cmd(line)
    if cmd_part is None:
        return False

    if cmd_part == "end":
        dialog.visible = False
        dialog.complete = True
        return True

    if cmd_part == "begin" or cmd_part.startswith("begin ") or cmd_part.startswith("begin\t"):
        title = cmd_part[len("begin"):].strip()
        dialog.reset()
        dialog.title = title
        dialog.begin_ts = float(ts or 0.0)
        dialog.begin_key = (round(dialog.begin_ts, 4), title)
        return True

    if dialog.dismissed:
        return cmd_part == "show" or cmd_part in ("button_group_start", "button_group_end") or any(
            cmd_part == k or cmd_part.startswith(k + " ") or cmd_part.startswith(k + "\t")
            for k in ("text", "button", "footer_button")
        )

    if cmd_part == "show":
        dialog.visible = True
        dialog.complete = False
        return True

    if cmd_part == "text" or cmd_part.startswith("text ") or cmd_part.startswith("text\t"):
        txt = cmd_part[len("text"):].strip()
        if txt:
            dialog.text = (dialog.text + "\n" + txt) if dialog.text else txt
        return True

    if cmd_part.startswith("button ") or cmd_part.startswith("button\t"):
        payload = cmd_part[len("button"):].strip()
        label, gcode, color = _split_button_payload(payload)
        if label:
            dialog.buttons.append(PromptButton(label=label, gcode=gcode, color=color))
        return True

    if cmd_part.startswith("footer_button ") or cmd_part.startswith("footer_button\t"):
        payload = cmd_part[len("footer_button"):].strip()
        label, gcode, color = _split_button_payload(payload)
        if label:
            dialog.footer_buttons.append(PromptButton(label=label, gcode=gcode, color=color))
        return True

    if cmd_part in ("button_group_start", "button_group_end"):
        return True

    return False


def _is_dismissed_begin(ts: float, title: str, dismissed_begins, min_begin_ts) -> bool:
    if min_begin_ts is not None and min_begin_ts > 0.0 and ts > 0.0 and ts < min_begin_ts:
        return True
    if dismissed_begins and (round(ts, 4), title) in dismissed_begins:
        return True
    return False


def try_parse_prompt_lines(
    lines: list, dialog: PromptDialog, dismissed_begins=None, min_begin_ts=None
) -> bool:
    """Feed a list of gcode_store lines to the parser.

    Returns True if any prompt command was found.
    """
    found = False
    for item in lines:
        if isinstance(item, dict):
            item_type = item.get("type")
            if item_type is not None and item_type != "response":
                continue
            msg = str(item.get("message") or "").strip()
            item_ts = float(item.get("time") or 0.0)
        elif isinstance(item, str):
            msg = item.strip()
            item_ts = 0.0
        else:
            continue
        begin_title = parse_prompt_begin(msg)
        if begin_title is not None and _is_dismissed_begin(item_ts, begin_title, dismissed_begins, min_begin_ts):
            dialog.reset()
            dialog.dismissed = True
            found = True
            continue
        if parse_prompt_line(msg, dialog, ts=item_ts):
            found = True
    return found


def slice_new_prompt_items(prev_keys: list, curr_keys: list, curr_items: list) -> list:
    """Return only prompt items in `curr_items` not already seen in `prev_keys`."""
    if not curr_keys:
        return []
    if prev_keys == curr_keys:
        return []
    max_k = min(len(prev_keys), len(curr_keys))
    for k in range(max_k, 0, -1):
        if prev_keys[-k:] == curr_keys[:k]:
            return curr_items[k:]
    return list(curr_items)
