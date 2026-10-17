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

from .. import palette, win32

BODY_PT = 10  # Qt's default on Windows is Segoe UI 9 pt, small next to the Tk window's text
# Tk drew the layout for an 11 pt body; Qt measures points by the screen's DPI, so the
# same numbers come out larger. Sizes below are the Tk ones times this, rounded.
TK_TO_QT = BODY_PT / 11
# Windows' "Text size" (Accessibility): Qt does not apply it to fonts of a given size, so
# every size of the window is multiplied by it; read once, at start, as the system menus do
TEXT_SCALE = win32.text_scale()


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


def px(n: float) -> int:
    """A size that goes with the text (an icon beside a word, a list row), in px at the
    system's text size."""
    return round(n * TEXT_SCALE)


def font_pt(tk_size: float) -> float:
    """A Tk font size of the old layout as Qt points (see TK_TO_QT), times the system's
    text size (TEXT_SCALE), to half a point."""
    pt = max(1, round(tk_size * TK_TO_QT))
    return pt if TEXT_SCALE == 1.0 else round(pt * TEXT_SCALE * 2) / 2


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
QWidget#Page, QWidget#Guard {{ background: {t.BG}; }}
QFrame#Sidebar {{ background: {t.SIDEBAR}; border: none; border-right: 1px solid {t.BORDER}; }}
QFrame#Card, QScrollArea#CardScroll, QWidget#CardScrollInner {{ background: {t.CARD}; border: none; }}
QSplitter::handle {{ background: {t.BORDER}; }}
QFrame#Divider, QFrame#RailDivider {{ background: {t.BORDER}; border: none; }}
QFrame#RailDivider {{ background: {t.RAIL_HOVER}; }}
QLabel[role="section"] {{ color: {t.MUTED}; font-size: {small}pt; font-weight: bold; }}
QLabel[role="placeholder"] {{ color: {t.MUTED}; font-size: {big}pt; }}
QLabel[role="heading"] {{ font-size: {font_pt(15)}pt; font-weight: bold; }}
QLineEdit[role="name"] {{ font-size: {font_pt(17)}pt; font-weight: bold; }}
QLineEdit[role="key"] {{ color: {t.MUTED}; font-size: {muted}pt; }}
QLineEdit:disabled, QPlainTextEdit:disabled {{ color: {t.DISABLED_TEXT}; }}
QPlainTextEdit, QTextEdit, QTextBrowser {{
    background: {t.FIELD}; color: {t.TEXT}; border: 1px solid {t.BORDER}; border-radius: 8px;
    padding: 4px 6px; selection-background-color: {t.ACCENT_SOFT}; selection-color: {t.TEXT};
}}
QPlainTextEdit:focus, QTextEdit:focus {{ border: 1px solid {t.ACCENT}; }}
QPlainTextEdit[role="console"] {{ background: {t.CONSOLE_BG}; color: {t.CONSOLE_TEXT}; border: none; font-family: "{palette.MONO}"; }}
QPushButton[role="small"] {{ padding: 4px 10px; font-size: {font_pt(11)}pt; }}
QToolButton {{ color: {t.TEXT}; background: transparent; border: none; border-radius: 6px; padding: 4px 6px; }}
QToolButton:hover {{ background: {t.HOVER}; }}
QToolButton:pressed, QToolButton:checked {{ background: {t.PRESSED}; }}
QToolButton:disabled {{ color: {t.DISABLED_TEXT}; }}
QToolButton[role="danger"] {{ color: {t.DANGER}; }}
QToolButton[role="danger"]:hover {{ background: {t.DANGER_HOVER}; }}
QToolButton[role="tab"] {{
    color: {t.RAIL_TEXT}; background: transparent; border: none; border-radius: 8px;
    padding: 6px 10px; font-size: {font_pt(11)}pt;
}}
QToolButton[role="tab"]:hover {{ background: {t.RAIL_HOVER}; color: {t.ON_RAIL}; }}
QToolButton[role="tab"]:checked {{ background: {t.RAIL_ACTIVE}; color: {t.ON_ACCENT}; font-weight: bold; }}
QComboBox {{
    background: {t.FIELD}; color: {t.TEXT}; border: 1px solid {t.BORDER}; border-radius: 8px;
    padding: 5px 10px;
}}
QComboBox:focus {{ border: 1px solid {t.ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {t.FIELD}; color: {t.TEXT}; border: 1px solid {t.BORDER}; selection-background-color: {t.ACCENT_SOFT}; selection-color: {t.TEXT}; outline: none; }}
QListView, QTreeView, QListWidget {{
    background: {t.SIDEBAR}; color: {t.TEXT}; border: none; outline: none;
    selection-background-color: {t.ACCENT_SOFT}; selection-color: {t.TEXT};
}}
QFrame#Card QListView, QFrame#Card QListWidget {{ background: {t.CARD}; }}
QListWidget::item, QTreeView::item {{ padding: 6px 8px; border-radius: 6px; }}
QListWidget::item:hover, QTreeView::item:hover {{ background: {t.HOVER}; }}
QListWidget::item:selected, QTreeView::item:selected {{ background: {t.ACCENT_SOFT}; color: {t.TEXT}; }}
QMenu {{ background: {t.CARD}; color: {t.TEXT}; border: 1px solid {t.BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 4px; }}
QMenu::item:selected {{ background: {t.ACCENT_SOFT}; color: {t.TEXT}; }}
QMenu::item:disabled {{ color: {t.DISABLED_TEXT}; }}
QMenu::separator {{ height: 1px; background: {t.BORDER}; margin: 4px 6px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.SCROLL}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {t.SCROLL_HOVER}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t.SCROLL}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}
QCheckBox, QRadioButton {{ color: {t.TEXT}; spacing: 8px; background: transparent; }}
QSpinBox {{ background: {t.FIELD}; color: {t.TEXT}; border: 1px solid {t.BORDER}; border-radius: 8px; padding: 4px 8px; }}
QDialog#Settings, QStackedWidget#SettingsBody, QScrollArea#SettingsScroll, QWidget#SettingsScrollInner {{ background: {t.BG}; border: none; }}
QFrame#Group {{ background: {t.CARD}; border: 1px solid {t.BORDER}; border-radius: 12px; }}
QFrame#Group QLabel, QFrame#Group QCheckBox, QFrame#Group QRadioButton {{ background: transparent; }}
QListWidget#SettingsNav {{ background: transparent; font-size: {font_pt(11)}pt; }}
QListWidget#SettingsNav::item {{ padding: 8px 10px; border-radius: 8px; }}
QListWidget#SettingsNav::item:selected {{ background: {t.ACCENT_SOFT}; color: {t.ACCENT_INK}; font-weight: bold; }}
QLabel[role="ok"] {{ color: {t.SUCCESS}; }}
QWidget#FileViewer {{ background: {t.CARD}; }}
QProgressBar {{ background: {t.HOVER}; border: none; border-radius: 3px; max-height: 6px; text-align: center; }}
QProgressBar::chunk {{ background: {t.SUCCESS}; border-radius: 3px; }}
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
        font.setPointSizeF(BODY_PT * TEXT_SCALE)
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


def font(tk_size: float, bold: bool = False, mono: bool = False) -> QFont:
    """A font of the old layout's size (`tk_size` in Tk points, see TK_TO_QT)."""
    f = QFont(palette.MONO) if mono else body_font()
    f.setPointSizeF(font_pt(tk_size))
    f.setBold(bold)
    return f


def blend(a: str, b: str, t: float) -> str:
    """Colour `a` mixed into `b` by `t` (0 — all b, 1 — all a)."""
    return palette.mix(a, b, t)


_translator = None


def translate_qt(app: QApplication, language: str) -> None:
    """The texts Qt itself draws (the menu of a text field, the buttons of the system dialogs,
    the file dialog) in the UI language: Qt's own Russian catalogue for "ru", none for English.
    Read from PySide6's own folder (inside the built app too), never from a user path."""
    from PySide6.QtCore import QLibraryInfo, QTranslator

    global _translator
    if _translator is not None:
        app.removeTranslator(_translator)
        _translator = None
    if language != "ru":
        return
    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    translator = QTranslator(app)
    if translator.load("qtbase_ru", folder):
        app.installTranslator(translator)
        _translator = translator


def make_style():
    """Fusion with the check boxes and radio buttons drawn in the theme: an accent box with
    an ink tick, a ring with a dot (the style sheet alone can only fill the box)."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QPainterPath, QPen
    from PySide6.QtWidgets import QProxyStyle, QStyle, QStyleFactory

    from .errors import guard_events

    @guard_events
    class Style(QProxyStyle):
        def pixelMetric(self, metric, option=None, widget=None):  # noqa: N802 — Qt's name
            if metric in (QStyle.PixelMetric.PM_IndicatorWidth, QStyle.PixelMetric.PM_IndicatorHeight, QStyle.PixelMetric.PM_ExclusiveIndicatorWidth, QStyle.PixelMetric.PM_ExclusiveIndicatorHeight):
                return 17
            return super().pixelMetric(metric, option, widget)

        def drawPrimitive(self, element, option, painter, widget=None):  # noqa: N802 — Qt's name
            if element not in (QStyle.PrimitiveElement.PE_IndicatorCheckBox, QStyle.PrimitiveElement.PE_IndicatorRadioButton):
                super().drawPrimitive(element, option, painter, widget)
                return
            t = current()
            state = option.state
            on = bool(state & QStyle.StateFlag.State_On)
            enabled = bool(state & QStyle.StateFlag.State_Enabled)
            hot = bool(state & QStyle.StateFlag.State_MouseOver)
            r = QRectF(option.rect).adjusted(1.5, 1.5, -1.5, -1.5)
            side = min(r.width(), r.height())
            r = QRectF(r.center().x() - side / 2, r.center().y() - side / 2, side, side)
            painter.save()
            painter.setRenderHint(painter.RenderHint.Antialiasing)
            fill = t.ACCENT if on else t.FIELD
            if not enabled:
                fill = t.ACCENT_DISABLED if on else t.HOVER
            border = t.ACCENT if on or hot else t.CHECK_BORDER
            painter.setPen(QPen(QColor(border), 1.2))
            painter.setBrush(QColor(fill))
            if element == QStyle.PrimitiveElement.PE_IndicatorRadioButton:
                painter.drawEllipse(r)
                if on:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(t.ON_ACCENT))
                    painter.drawEllipse(r.center(), side * 0.2, side * 0.2)
            else:
                painter.drawRoundedRect(r, side * 0.25, side * 0.25)
                if on:
                    pen = QPen(QColor(t.ON_ACCENT), max(1.6, side * 0.13))
                    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                    painter.setPen(pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    path = QPainterPath(QPointF(r.left() + side * 0.24, r.top() + side * 0.52))
                    path.lineTo(QPointF(r.left() + side * 0.43, r.top() + side * 0.70))
                    path.lineTo(QPointF(r.left() + side * 0.77, r.top() + side * 0.32))
                    painter.drawPath(path)
            painter.restore()

    return Style(QStyleFactory.create("Fusion"))


def palette_mix(a: str, b: str, t: float) -> str:
    """`a` laid over `b` at strength `t` (palette.mix)."""
    return palette.mix(a, b, t)
