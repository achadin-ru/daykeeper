"""Small building blocks every page uses: the section caption, the divider line, an icon
button, a "⋯" button with its menu, a scroll area of the card colour, a menu shown under
a button, and the wheel guard that keeps a list or a number from changing under a page
being scrolled."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QComboBox, QFrame, QLabel, QMenu, QScrollArea, QToolButton, QVBoxLayout, QWidget

from ..errors import guard_events

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
    """A square button with a glyph; `role` "icon", "danger" or "rail"."""
    b = QToolButton(parent)
    b.setText(text)
    b.setProperty("role", role)
    b.setAutoRaise(True)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
    if tip:
        b.setToolTip(tip)
    if on_click is not None:
        b.clicked.connect(lambda _checked=False: on_click())  # `clicked` brings a flag the function does not take
    return b


def fill_menu(menu: QMenu, items: MenuItems) -> QMenu:
    for item in items:
        if item is None:
            menu.addSeparator()
            continue
        label, fn = item
        action = menu.addAction(label)
        action.triggered.connect(fn)
    return menu


def more_button(items: MenuItems, tip: str = "", parent: QWidget | None = None) -> QToolButton:
    """"⋯" with a menu that opens on click."""
    b = icon_button("⋯", tip, None, parent)
    menu = fill_menu(QMenu(b), items)
    b.clicked.connect(lambda: popup_below(menu, b))
    return b


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
