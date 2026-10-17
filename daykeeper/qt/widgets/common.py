"""Small building blocks every page uses: the section caption, the divider line, an icon
button, a "⋯" button with its menu, a scroll area of the card colour, a menu shown under
a button, a label with a drawn icon, and the wheel guard that keeps a list or a number
from changing under a page being scrolled.

The texts still come with a glyph in front ("🔑  Password", the catalogue is shared with
the Tk window); these helpers draw the vector icon for it instead (icons.split)."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, QSize, Qt
from PySide6.QtGui import QAction, QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QAbstractButton, QAbstractSpinBox, QApplication, QComboBox, QFrame, QLabel, QLineEdit, QMenu, QScrollArea, QToolButton, QVBoxLayout, QWidget

from .. import icons, theme
from ..errors import guard_events

ICON_PX = theme.px(16)  # a drawn icon on a button, in a menu, beside a text

MenuItems = Iterable[tuple[str, Callable[[], object]] | None]  # None — a separator


def section(text: str, parent: QWidget | None = None) -> QLabel:
    """A small grey caption in capitals over a part of a card (FIELDS, NOTE)."""
    lab = QLabel(text, parent)
    lab.setProperty("role", "section")
    return lab


def divider(parent: QWidget | None = None) -> QFrame:
    line = QFrame(parent)
    line.setObjectName("Divider")
    line.setFixedHeight(1)
    return line


def icon_button(text: str, tip: str = "", on_click: Callable[[], object] | None = None, parent: QWidget | None = None, role: str = "icon") -> QToolButton:
    """A square button with a drawn icon for the glyph `text` (a text that is no known
    glyph stays text); `role` "icon", "danger" or "rail"."""
    b = QToolButton(parent)
    set_glyph(b, text, role)
    b.setProperty("role", role)
    b.setAutoRaise(True)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
    if tip:
        b.setToolTip(tip)
    if on_click is not None:
        b.clicked.connect(lambda _checked=False: on_click())  # `clicked` brings a flag the function does not take
    return b


def set_glyph(b: QToolButton, glyph: str, role: str = "icon") -> None:
    """Put the drawn icon of `glyph` on the button (an eye that turns into a crossed eye)."""
    name = icons.name_of(glyph)
    if name is None:
        b.setText(glyph)
        return
    b.setText("")
    b.setIcon(icons.rail(name) if role == "rail" else icons.button(name, role))
    b.setIconSize(QSize(ICON_PX, ICON_PX))


def with_icon(widget: QAbstractButton, text: str, on_accent: bool = False) -> QAbstractButton:
    """A check box or a button whose text starts with a glyph: the drawn icon and the rest
    (`on_accent` — white on a primary button)."""
    name, rest = icons.split(text)
    widget.setText(rest)
    if name is not None:
        widget.setIcon(icons.icon(name, lambda: theme.current().ON_ACCENT) if on_accent else icons.plain(name))
        widget.setIconSize(QSize(ICON_PX, ICON_PX))
    return widget


def add_action(menu: QMenu, text: str) -> QAction:
    """A menu item; a glyph in front of `text` becomes its drawn icon."""
    name, rest = icons.split(text)
    return menu.addAction(icons.plain(name), rest) if name is not None else menu.addAction(text)


def search_field(edit: QLineEdit, placeholder: str) -> QLineEdit:
    """A search box: the drawn magnifier in front instead of the 🔍 of the catalogue text."""
    _name, rest = icons.split(placeholder)
    edit.setPlaceholderText(rest)
    edit.addAction(icons.icon("search", lambda: theme.current().MUTED), QLineEdit.ActionPosition.LeadingPosition)
    return edit


def fill_menu(menu: QMenu, items: MenuItems) -> QMenu:
    for item in items:
        if item is None:
            menu.addSeparator()
            continue
        label, fn = item
        action = add_action(menu, label)
        action.triggered.connect(fn)
    return menu


def more_button(items: MenuItems, tip: str = "", parent: QWidget | None = None) -> QToolButton:
    """"⋯" (drawn) with a menu that opens on click."""
    b = icon_button("⋯", tip, None, parent)
    menu = fill_menu(QMenu(b), items)
    b.clicked.connect(lambda: popup_below(menu, b))
    return b


def transient(menu: QMenu) -> QMenu:
    """A menu made for one right click: deleted once it closes (else every click would
    leave one more hidden menu behind)."""
    menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    return menu


def popup_below(menu: QMenu, widget: QWidget) -> QAction | None:
    """Open `menu` under `widget` (non-blocking)."""
    menu.popup(widget.mapToGlobal(QPoint(0, widget.height())))
    return None


def scroll_area(parent: QWidget | None = None, margins: tuple[int, int, int, int] = (32, 24, 28, 24), name: str = "CardScroll") -> tuple[QScrollArea, QWidget, QVBoxLayout]:
    """A vertical scroll area whose inner widget takes the full width (the Tk window's
    make_scrollable and its Settler in one)."""
    area = QScrollArea(parent)
    area.setObjectName(name)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    inner = QWidget()
    inner.setObjectName(name + "Inner")
    layout = QVBoxLayout(inner)
    layout.setContentsMargins(*margins)
    layout.setSpacing(0)
    area.setWidget(inner)
    return area, inner, layout


@guard_events
class IconLabel(QLabel):
    """A label whose text starts with a glyph ("🦊 GitLab"): the drawn icon at the left of
    the first line, in the label's own text colour, then the rest of the text."""

    def __init__(self, text: str, role: str = "", parent: QWidget | None = None, size: int = ICON_PX):
        super().__init__(parent)
        self._size = size
        self._name: str | None = None
        if role:
            self.setProperty("role", role)
        self.set_text(text)

    def set_text(self, text: str) -> None:
        self._name, rest = icons.split(text)
        self.setContentsMargins(self._size + 8 if self._name else 0, 0, 0, 0)
        self.setText(rest)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if self._name is None:
            return
        p = QPainter(self)
        line = self.fontMetrics().height()
        top = (self.height() - line) / 2 if not self.wordWrap() else 0
        muted = self.property("role") in ("muted", "section")
        colour = QColor(theme.current().MUTED) if muted else self.palette().color(self.foregroundRole())
        icons.paint(p, self._name, QRectF(0, top, self._size, line), colour, size=self._size)
        p.end()


@guard_events
class GlyphView(QWidget):
    """One drawn icon on its own (the big padlock over locked passwords), in the text colour."""

    def __init__(self, name: str, size: int, parent: QWidget | None = None):
        super().__init__(parent)
        self._name = name
        self.setFixedSize(size, size)

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        icons.paint(p, self._name, QRectF(self.rect()), theme.current().TEXT)
        p.end()


@guard_events
class WheelGuard(QObject):
    """The wheel over a drop-down or a number box that does not have the focus scrolls
    the page instead of changing the value (Qt.StrongFocus does not stop it, see the
    port plan 5.7). Installed once on the application."""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel and isinstance(obj, (QComboBox, QAbstractSpinBox)) and not obj.hasFocus():
            event.ignore()
            parent = obj.parentWidget()
            while parent is not None and not isinstance(parent, QScrollArea):
                parent = parent.parentWidget()
            if parent is not None:
                QApplication.sendEvent(parent.viewport(), event)
            return True
        return False
