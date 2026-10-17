"""FlowLayout: widgets left to right, a new line when the next one does not fit (the Tk
window's FlowRow). The example layout of the Qt documentation, with height-for-width so
a container grows a line when its width shrinks."""

from __future__ import annotations

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QSizePolicy, QWidget

from ..errors import guard_events


@guard_events
class FlowLayout(QLayout):
    def __init__(self, parent: QWidget | None = None, spacing: int = 8, line_spacing: int | None = None, margins: int = 0):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h = spacing
        self._v = spacing if line_spacing is None else line_spacing
        self.setContentsMargins(QMargins(margins, margins, margins, margins))

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._lay_out(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._lay_out(rect, apply=True)

    def sizeHint(self) -> QSize:
        """The widest item: a flow asks for no more than one item's width and wraps the rest
        (one line of everything made a scroll area as wide as all the items together)."""
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._visible():
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _visible(self) -> list[QLayoutItem]:
        out = []
        for item in self._items:
            widget = item.widget()
            if widget is None or not widget.isHidden():
                out.append(item)
        return out

    def _lay_out(self, rect: QRect, apply: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line = area.x(), area.y(), 0
        for item in self._visible():
            hint = item.sizeHint()
            if x > area.x() and x + hint.width() > area.right() + 1:
                x, y, line = area.x(), y + line + self._v, 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._h
            line = max(line, hint.height())
        return y + line - rect.y() + m.bottom()


def flow_host(parent: QWidget | None = None, spacing: int = 8) -> tuple[QWidget, FlowLayout]:
    """A plain widget holding a FlowLayout, sized by height-for-width."""
    host = QWidget(parent)
    layout = FlowLayout(host, spacing)
    policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
    policy.setHeightForWidth(True)
    host.setSizePolicy(policy)
    return host, layout
