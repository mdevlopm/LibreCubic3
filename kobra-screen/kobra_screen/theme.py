"""Palette, text sizes and icons shared by both renderers."""

COLORS = {
    "bg": (11, 13, 12),             # #0B0D0C base
    "sunken": (8, 10, 9),           # #080A09 sunken / keypad display
    "nav_bg": (15, 18, 17),         # #0F1211 bottom tab bar
    "card": (21, 24, 23),           # #151817 surface/1
    "surface2": (28, 32, 30),       # #1C201E surface/2
    "surface3": (36, 41, 39),       # #242927 surface/3
    "surface4": (46, 52, 49),       # #2E3431 surface/4
    "button": (28, 32, 30),         # #1C201E secondary button fill
    "border_subtle": (38, 43, 41),  # #262B29 card border
    "border_default": (51, 57, 55), # #333937 button border
    "border_strong": (69, 76, 72),  # #454C48 selected segment / key border
    "divider": (31, 36, 34),        # #1F2422 hairlines
    "text": (243, 246, 244),        # #F3F6F4 primary text
    "muted": (167, 175, 171),       # #A7AFAB secondary text
    "tertiary": (118, 126, 122),    # #767E7A metadata / inactive tab
    "accent": (0, 174, 66),         # #00AE42 Bambu green
    "primary": (0, 174, 66),        # #00AE42 primary CTA
    "on_accent": (6, 19, 11),       # #06130B text on green CTA
    "on_danger": (26, 5, 6),        # #1A0506 text on red danger
    "active": (12, 42, 24),         # #0C2A18 green tint surface
    "active_text": (150, 229, 175), # #96E5AF text on green tint
    "active_icon": (94, 214, 132),  # #5ED684 icon on green tint
    "ok": (43, 197, 95),            # #2BC55F ready / success
    "warn": (242, 169, 59),         # #F2A93B heating / paused / warning
    "danger": (229, 72, 77),        # #E5484D danger / stop
    "danger_bg": (53, 17, 20),      # #351114 error card / outline danger bg
    "danger_border": (110, 33, 38), # #6E2126 error card border
    "danger_text": (255, 138, 142), # #FF8A8E text on danger_bg
    "info": (76, 154, 255),         # #4C9AFF info
    "ghost": (11, 13, 12),          # #0B0D0C transparent fallback
    "disabled": (28, 32, 30),       # #1C201E disabled button fill
    "disabled_text": (75, 82, 78),  # #4B524E disabled text
    "track": (36, 41, 39),          # #242927 bar / arc track
    "spool_rim": (58, 64, 61),      # #3A403D spool outer ring
}
FONT_SIZES = {
    "tab": 11,
    "small": 12,
    "med": 14,
    "normal": 16,
    "h2": 18,
    "big": 22,
    "num": 28,
    "huge": 34,
    "hero": 44,
}
ICON_SIZES = {"small": 16, "normal": 20, "big": 24, "xl": 32}

# Material Icons codepoints (MaterialIcons-Regular.ttf shipped with Rinkhals)
ICONS = {
    "home": 0xE88A, "tune": 0xE429, "settings": 0xE8B8, "build": 0xE869, "folder": 0xE2C7, "play": 0xE037,
    "pause": 0xE034, "stop": 0xE047, "nozzle": 0xE80E, "bed": 0xE3EC, "fan": 0xE332, "layers": 0xE53B,
    "time": 0xE8B5, "speed": 0xE9E4, "drop": 0xE91C, "back": 0xE5C4, "next": 0xE5CC, "prev": 0xE5CB,
    "up": 0xE5D8, "down": 0xE5DB, "left": 0xE5C4, "right": 0xE5C8, "add": 0xE145, "remove": 0xE15B,
    "check": 0xE5CA, "close": 0xE5CD, "warning": 0xE002, "power": 0xE8AC, "refresh": 0xE5D5, "light": 0xE90F,
    "level": 0xE41C, "heal": 0xE3F3, "file": 0xE24D, "load": 0xE258, "unload": 0xE25A, "thermo": 0xF076,
    "air": 0xEFD8, "clean": 0xF0FF, "info": 0xE88E, "error": 0xE000, "move": 0xE89F, "zoffset": 0xE8D5,
    "vibration": 0xE62D, "chart": 0xE922, "palette": 0xE40A, "flash": 0xE3E7, "edit": 0xE3C9, "spool": 0xE86A,
    "print": 0xE8AD, "lock": 0xE897, "extension": 0xE87B, "wifi": 0xE63E, "wifi_off": 0xE648,
    "backspace": 0xE14A,
}
