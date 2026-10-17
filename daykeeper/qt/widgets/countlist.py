"""A sidebar list whose rows carry a count at their right edge (bookmark groups, days)."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from .. import icons, theme
from ..errors import guard_events

COUNT_ROLE = Qt.ItemDataRole.UserRole + 1
KEY_ROLE = Qt.ItemDataRole.UserRole + 2
ROW_ICON = theme.px(15)  # px, the drawn icon of a row whose text starts with a glyph


@guard_events
class CountDelegate(QStyledItemDelegate):
    """A sidebar row with a count at its right edge (groups, days)."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        t = theme.current()
        r = option.rect
        state = option.state
        painter.save()
        if state & QStyle.StateFlag.State_Selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.ACCENT_SOFT))
            painter.drawRoundedRect(QRectF(r).adjusted(0, 1, 0, -1), 6, 6)
        elif state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.HOVER))
            painter.drawRoundedRect(QRectF(r).adjusted(0, 1, 0, -1), 6, 6)
        fm = option.fontMetrics
        count = str(index.data(COUNT_ROLE) or "")
        cw = fm.horizontalAdvance(count) + 10 if count else 0
        painter.setPen(QColor(t.MUTED))
        painter.drawText(r.adjusted(0, 0, -8, 0), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight), count)
        glyph, text = icons.split(str(index.data() or ""))
        left = 8
        if glyph is not None:  # "  📎 With files": the drawn icon, then the text
            icons.paint(painter, glyph, QRect(r.x() + left + 4, r.y(), ROW_ICON, r.height()), t.MUTED, size=ROW_ICON)
            left += ROW_ICON + 12
        painter.setPen(QColor(t.TEXT))
        painter.drawText(r.adjusted(left, 0, -cw - 8, 0), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), fm.elidedText(text, Qt.TextElideMode.ElideRight, r.width() - cw - left - 8))
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        return QSize(120, theme.px(32))


def count_list(parent: QWidget) -> QListWidget:
    lst = QListWidget(parent)
    lst.setItemDelegate(CountDelegate(lst))
    lst.setMouseTracking(True)
    lst.setUniformItemSizes(True)
    return lst


def add_count_item(lst: QListWidget, text: str, key: str, count: int | str = "") -> QListWidgetItem:
    item = QListWidgetItem(text, lst)
    item.setData(KEY_ROLE, key)
    item.setData(COUNT_ROLE, str(count) if count else "")
    return item
