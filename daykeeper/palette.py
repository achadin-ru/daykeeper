"""Colours, fonts and the shown name of the app — the one place they are defined.

Pure (no tkinter): the style installer, the widgets, the menus and the viewers all read
from here, so a colour changed here changes everywhere at once.

Two themes. The light one is what the names hold; `apply("dark")` swaps them for DARK. It
runs at start, before any module of the window is imported (boot.py): many of them take
a colour into a name or a default argument when they are imported, so a switch of theme
takes effect at the next start."""

from __future__ import annotations

import sys

APP_TITLE = "Daykeeper Multitool"  # shown name; the exe, the bundle and the data folder stay “Daykeeper”

_MAC = sys.platform == "darwin"
FONT = "Segoe UI" if sys.platform == "win32" else ("Helvetica Neue" if _MAC else "DejaVu Sans")
MONO = "Consolas" if sys.platform == "win32" else ("Menlo" if _MAC else "DejaVu Sans Mono")
# Tk on macOS takes a font size in screen points and ignores `tk scaling`: a size of 11 is
# 11 pixels there, a quarter smaller than on Windows (11 pt = 14.7 px at 96 dpi), while the
# boxes and gaps round the text are the same pixels on both. Every font size goes through pt().
# The full 4/3 made the text larger than in other Mac apps: the body size 11 comes out as
# 13, the size macOS uses for its own text.
SIZE_SCALE = 13 / 11 if _MAC else 1.0


def pt(size: float) -> int:
    """A font size as the layout was drawn for it (Windows at 100 %)."""
    return round(size * SIZE_SCALE)

WHITE = "#FFFFFF"  # the light surface: fields, menus, tool tips' text; not white in the dark theme
ON_ACCENT = "#FFFFFF"  # text and ticks on an accent fill, in both themes
BG = "#F3F4F8"  # window and unlock screen background
SIDEBAR = "#F7F8FB"  # left column with the list
CARD = "#FFFFFF"  # entry card
FIELD = "#FFFFFF"
BORDER = "#E3E6ED"
TEXT = "#111827"
MUTED = "#667085"  # 4.7:1 on the window background (#6B7280 was 4.4:1)
DISABLED_TEXT = "#6E7684"  # a greyed-out menu item (4.5:1 on white; #B8BDC7 was 1.9:1)
ACCENT = "#4F46E5"
LINK = "#4F46E5"  # link-like text on a card; lighter than the accent in the dark theme, where the accent fill is too dim for text
ACCENT_HOVER = "#4338CA"
ACCENT_PRESSED = "#3730A3"
ACCENT_DISABLED = "#A5B4FC"
ACCENT_SOFT = "#EEF0FF"
ACCENT_INK = "#3730A3"  # text on a soft accent background
DANGER = "#DC2626"
DANGER_SOFT = "#FEE2E2"  # a danger button under the pointer
DANGER_PRESSED = "#FECACA"
SUCCESS = "#15803D"  # 4.5:1 on white (#16A34A was 3.3:1)
HOVER = "#F1F3F8"
PRESSED = "#E6E9F0"
CHECK_BORDER = "#C3C8D3"
SCROLL = "#D3D8E1"
SCROLL_HOVER = "#A3ABB9"
RAIL = "#111827"  # the navigation rail on the left
RAIL_HOVER = "#1F2937"
RAIL_ACTIVE = "#4F46E5"
RAIL_TEXT = "#9CA3AF"
CRED_BG = "#F6F7F9"  # the login and password rows on a bookmark tile
CONSOLE_BG = "#0F172A"  # the output window of a routine
CONSOLE_TEXT = "#E2E8F0"
TILE = "#FFFFFF"  # a bookmark or routine tile, a read event
RAISED = "#FFFFFF"  # the face of a raised button
RAISED_HOVER = "#F7F8FB"  # ... under the pointer
DANGER_HOVER = "#FEF2F2"
CODE_BG = "#F1F3F6"  # `code` in a Markdown file
CODE_TEXT = "#B42318"
CODEBLOCK_BG = "#F6F8FA"
SHADE_INK = "#8A93A6"  # the soft shadow over a pinned bar, at its darkest

THEMES = ("light", "dark")
theme = "light"

DARK = {
    "WHITE": "#262B33",
    "BG": "#16181D",
    "SIDEBAR": "#1B1E24",
    "CARD": "#1F232A",
    "FIELD": "#262B33",
    "BORDER": "#343A46",
    "TEXT": "#E7E9EE",
    "MUTED": "#9AA3B2",
    "DISABLED_TEXT": "#8E97A7",
    "ACCENT": "#6366F1",
    "LINK": "#818CF8",
    "ACCENT_HOVER": "#7477F4",
    "ACCENT_PRESSED": "#4F46E5",
    "ACCENT_DISABLED": "#3E4180",
    "ACCENT_SOFT": "#2B2E55",
    "ACCENT_INK": "#C7CAFE",
    "DANGER": "#F87171",
    "DANGER_SOFT": "#3D2226",
    "DANGER_PRESSED": "#4C262B",
    "SUCCESS": "#4ADE80",
    "HOVER": "#2A2F38",
    "PRESSED": "#323844",
    "CHECK_BORDER": "#4B5362",
    "SCROLL": "#3A404C",
    "SCROLL_HOVER": "#596273",
    "RAIL": "#0E1014",
    "RAIL_HOVER": "#20242B",
    "RAIL_ACTIVE": "#5457E5",
    "RAIL_TEXT": "#9CA3AF",
    "CRED_BG": "#2A2F38",
    "CONSOLE_BG": "#0B0E14",
    "TILE": "#252A32",
    "RAISED": "#2C313A",
    "RAISED_HOVER": "#333944",
    "DANGER_HOVER": "#3D2226",
    "CODE_BG": "#2C313A",
    "CODE_TEXT": "#FCA5A5",
    "CODEBLOCK_BG": "#232830",
    "SHADE_INK": "#000000",
}


def apply(name: str) -> None:
    """Take the colours of theme `name` ("light" — as they are). Call it before the window
    is built: see the module text."""
    global theme
    if name == "dark" and theme != "dark":
        globals().update(DARK)
        theme = "dark"


def mix(a: str, b: str, t: float) -> str:
    """`a` laid over `b` at strength `t` (0 — b, 1 — a)."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x * t + y * (1 - t)):02X}" for x, y in zip(ca, cb, strict=True))
