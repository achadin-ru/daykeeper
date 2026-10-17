"""The look of the Qt window: the two colour themes as objects, the style sheet built from
them, the fonts and the app's pictures.

The Tk window takes its colours from `palette` module names swapped once at start, which
is why a theme switch needs a restart there. Here a theme is a `Theme` value: `apply`
puts its style sheet, palette and font on the application at any time, and widgets that
paint themselves read `current()` on every paint. The colours are the ones `palette`
defines; nothing here invents a colour of its own."""

from __future__ import annotations

import sys
from dataclasses import dataclass, fields, replace
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import QApplication

from .. import palette

BODY_PT = 10  # Qt's default on Windows is Segoe UI 9 pt, small next to the Tk window's text
# Tk drew the layout for an 11 pt body; Qt measures points by the screen's DPI, so the
# same numbers come out larger. Sizes below are the Tk ones times this, rounded.
TK_TO_QT = BODY_PT / 11


@dataclass(frozen=True)
class Theme:
    """One colour theme: the names and meanings are those of `palette` (see there)."""

    name: str
    WHITE: str
    ON_ACCENT: str
    ON_RAIL: str
    BG: str
    SIDEBAR: str
    CARD: str
    FIELD: str
    BORDER: str
    TEXT: str
    MUTED: str
    DISABLED_TEXT: str
    ACCENT: str
    LINK: str
    ACCENT_HOVER: str
    ACCENT_PRESSED: str
    ACCENT_DISABLED: str
    ACCENT_SOFT: str
    ACCENT_INK: str
    DANGER: str
    DANGER_SOFT: str
    DANGER_PRESSED: str
    SUCCESS: str
    HOVER: str
    PRESSED: str
    CHECK_BORDER: str
    SCROLL: str
    SCROLL_HOVER: str
    RAIL: str
    RAIL_HOVER: str
    RAIL_ACTIVE: str
    RAIL_TEXT: str
    CRED_BG: str
    CONSOLE_BG: str
    CONSOLE_TEXT: str
    TILE: str
    RAISED: str
    RAISED_HOVER: str
    DANGER_HOVER: str
    CODE_BG: str
    CODE_TEXT: str
    CODEBLOCK_BG: str
    SHADE_INK: str


def colour_names() -> list[str]:
    return [f.name for f in fields(Theme) if f.name != "name"]


def _light() -> Theme:
    """The light theme as `palette` holds it. The Qt window never calls `palette.apply`;
    were the names already swapped for the dark ones, the light colours would be gone."""
    if palette.theme != "light":
        raise RuntimeError("palette.apply() was called before the Qt theme was read")
    return Theme(name="light", **{n: getattr(palette, n) for n in colour_names()})


LIGHT = _light()
DARK = replace(LIGHT, name="dark", **palette.DARK)
THEMES = {"light": LIGHT, "dark": DARK}

_current = LIGHT


def current() -> Theme:
    return _current


def font_pt(tk_size: float) -> int:
    """A Tk font size of the old layout as Qt points (see TK_TO_QT)."""
    return max(1, round(tk_size * TK_TO_QT))


def build_qss(t: Theme) -> str:
    """The style sheet of the whole app. Widgets choose a look by `objectName` (one-off
    surfaces: Panel, Rail, Status, Toast) or by the `role` property (kinds that repeat:
    primary / link / danger buttons, muted / error / title labels)."""
    title, big, button, small, muted = font_pt(20), font_pt(13), font_pt(12), font_pt(9), font_pt(10)
    return f"""
QMainWindow, QDialog, QStackedWidget#Screens, QWidget#Unlock, QWidget#MainScreen {{ background: {t.BG}; color: {t.TEXT}; }}
QLabel {{ color: {t.TEXT}; background: transparent; }}
QLabel[role="title"] {{ font-size: {title}pt; font-weight: bold; }}
QLabel[role="subtitle"] {{ color: {t.MUTED}; }}
QLabel[role="muted"] {{ color: {t.MUTED}; font-size: {muted}pt; }}
QLabel[role="error"] {{ color: {t.DANGER}; }}
QFrame#Panel {{ background: {t.CARD}; border: 1px solid {t.BORDER}; border-radius: 16px; }}
QLineEdit {{
    background: {t.FIELD}; color: {t.TEXT}; border: 1px solid {t.BORDER}; border-radius: 8px;
    padding: 7px 10px; selection-background-color: {t.ACCENT_SOFT}; selection-color: {t.TEXT};
}}
QLineEdit:focus {{ border: 1px solid {t.ACCENT}; }}
QLineEdit[role="big"] {{ font-size: {big}pt; }}
QPushButton {{
    background: {t.RAISED}; color: {t.TEXT}; border: 1px solid {t.BORDER}; border-radius: 8px;
    padding: 7px 14px; font-size: {button}pt;
}}
QPushButton:hover {{ background: {t.RAISED_HOVER}; }}
QPushButton:pressed {{ background: {t.PRESSED}; }}
QPushButton:disabled {{ color: {t.DISABLED_TEXT}; }}
QPushButton[role="primary"] {{ background: {t.ACCENT}; color: {t.ON_ACCENT}; border: none; font-weight: bold; padding: 8px 16px; }}
QPushButton[role="primary"]:hover {{ background: {t.ACCENT_HOVER}; }}
QPushButton[role="primary"]:pressed {{ background: {t.ACCENT_PRESSED}; }}
QPushButton[role="primary"]:disabled {{ background: {t.ACCENT_DISABLED}; color: {t.ON_ACCENT}; }}
QPushButton[role="link"] {{ background: transparent; color: {t.LINK}; border: none; padding: 5px 6px; }}
QPushButton[role="link"]:hover {{ background: {t.ACCENT_SOFT}; }}
QPushButton[role="danger"] {{ color: {t.DANGER}; }}
QPushButton[role="danger"]:hover {{ background: {t.DANGER_HOVER}; }}
QPushButton[role="danger"]:pressed {{ background: {t.DANGER_PRESSED}; }}
QFrame#Rail {{ background: {t.RAIL}; }}
QToolButton[role="rail"] {{
    color: {t.RAIL_TEXT}; background: transparent; border: none; border-radius: 8px;
    padding: 5px 8px; font-size: {font_pt(13)}pt;
}}
QToolButton[role="rail"]:hover {{ background: {t.RAIL_HOVER}; color: {t.ON_RAIL}; }}
QToolButton[role="rail"]:disabled {{ color: {t.RAIL_HOVER}; }}
QLabel#Status {{ color: {t.MUTED}; font-size: {small}pt; padding: 5px 16px; }}
QFrame#Toast {{ background: {t.WHITE}; border: 1px solid {t.BORDER}; border-radius: 10px; }}
QLabel#ToastTitle {{ color: {t.MUTED}; font-size: {small}pt; font-weight: bold; }}
QToolTip {{ background: {t.CARD}; color: {t.TEXT}; border: 1px solid {t.BORDER}; padding: 4px 6px; }}
"""


def qpalette(t: Theme) -> QPalette:
    """The system palette in the theme's colours: what the style sheet does not reach
    (message boxes, the text cursor, the placeholder text, disabled text) takes these."""
    p = QPalette()
    c = QColor
    roles = {
        QPalette.ColorRole.Window: t.BG,
        QPalette.ColorRole.WindowText: t.TEXT,
        QPalette.ColorRole.Base: t.FIELD,
        QPalette.ColorRole.AlternateBase: t.SIDEBAR,
        QPalette.ColorRole.Text: t.TEXT,
        QPalette.ColorRole.Button: t.RAISED,
        QPalette.ColorRole.ButtonText: t.TEXT,
        QPalette.ColorRole.Highlight: t.ACCENT_SOFT,
        QPalette.ColorRole.HighlightedText: t.TEXT,
        QPalette.ColorRole.ToolTipBase: t.CARD,
        QPalette.ColorRole.ToolTipText: t.TEXT,
        QPalette.ColorRole.PlaceholderText: t.MUTED,
        QPalette.ColorRole.Link: t.LINK,
        QPalette.ColorRole.BrightText: t.ON_ACCENT,
    }
    for role, colour in roles.items():
        p.setColor(role, c(colour))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, c(t.DISABLED_TEXT))
    return p


def body_font() -> QFont:
    """Windows: Segoe UI at BODY_PT. macOS keeps the system font at its own size (13 pt)."""
    font = QFont(QGuiApplication.font())
    if sys.platform == "win32":
        font.setFamily(palette.FONT)
        font.setPointSize(BODY_PT)
    return font


def apply(app: QApplication, t: Theme) -> None:
    """Make `t` the theme of the whole app now: no restart, no rebuilt widgets."""
    global _current
    _current = t
    app.setPalette(qpalette(t))
    app.setFont(body_font())
    app.setStyleSheet(build_qss(t))


def theme_named(name: str) -> Theme:
    return THEMES.get(name, LIGHT)


# ---- pictures from assets/ (the source tree, or next to the built app)

def asset(name: str) -> Path | None:
    """The asset file, or None when it is not there. As appicon.asset, which imports Tk."""
    base = getattr(sys, "_MEIPASS", None)
    root = Path(base) if base else Path(__file__).resolve().parents[2]
    path = root / "assets" / name
    return path if path.exists() else None


def window_icon() -> QIcon:
    path = asset("icon.png")
    return QIcon(str(path)) if path is not None else QIcon()


def logo(size: int, ratio: float = 1.0) -> QPixmap | None:
    """The app icon as a logo `size` logical pixels wide: the file nearest to the size the
    screen needs (64 at 150 % wants 96), marked with the ratio so it is drawn sharp."""
    folder = asset("logo")
    if folder is None:
        return None
    sizes = sorted(int(p.stem) for p in folder.glob("*.png") if p.stem.isdigit())
    if not sizes:
        return None
    want = size * ratio
    best = min(sizes, key=lambda s: (abs(s - want), s))
    pixmap = QPixmap(str(folder / f"{best}.png"))
    if pixmap.isNull():
        return None
    pixmap.setDevicePixelRatio(best / size)
    return pixmap
