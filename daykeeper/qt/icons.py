"""Vector icons drawn with QPainter (decision 5 of the port: no emoji glyphs, which Windows
paints in colour and macOS in another shape). An icon is painted at whatever size and
scale the screen asks, in the current theme's colours, so it stays sharp at 125 / 150 %
and follows a theme switch without being made again."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPainterPath, QPen, QPixmap

from . import theme

Draw = Callable[[QPainter, QRectF], None]


class _Engine(QIconEngine):
    def __init__(self, draw: Draw, colour: Callable[[], str], active: Callable[[], str] | None = None):
        super().__init__()
        self._draw, self._colour, self._active = draw, colour, active

    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if mode == QIcon.Mode.Disabled:
            colour = QColor(theme.current().DISABLED_TEXT)
        elif mode == QIcon.Mode.Active and self._active is not None:  # under the pointer
            colour = QColor(self._active())
        else:
            colour = QColor(self._colour())
        pen = QPen(colour, max(1.4, rect.width() / 12))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        self._draw(painter, QRectF(rect).adjusted(1, 1, -1, -1))
        painter.restore()

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        pm = QPixmap(size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        self.paint(p, QRect(0, 0, size.width(), size.height()), mode, state)
        p.end()
        return pm

    def clone(self) -> QIconEngine:
        return _Engine(self._draw, self._colour, self._active)


def _eye(p: QPainter, r: QRectF) -> None:
    cx, cy, w, h = r.center().x(), r.center().y(), r.width(), r.height()
    path = QPainterPath(QPointF(r.left(), cy))
    path.quadTo(QPointF(cx, cy - h * 0.62), QPointF(r.right(), cy))
    path.quadTo(QPointF(cx, cy + h * 0.62), QPointF(r.left(), cy))
    p.drawPath(path)
    rad = min(w, h) * 0.16
    p.drawEllipse(QPointF(cx, cy), rad, rad)


def _eye_off(p: QPainter, r: QRectF) -> None:
    _eye(p, r)
    p.drawLine(QPointF(r.left() + r.width() * 0.15, r.bottom() - r.height() * 0.1), QPointF(r.right() - r.width() * 0.15, r.top() + r.height() * 0.1))


def _power(p: QPainter, r: QRectF) -> None:
    """The lock-the-app button: the power sign, as the Tk bar had in a glyph."""
    side = min(r.width(), r.height()) * 0.78
    box = QRectF(0, 0, side, side)
    box.moveCenter(r.center())
    p.drawArc(box, 60 * 16, -300 * 16)  # from 1 o'clock round to 11: open at the top
    p.drawLine(QPointF(box.center().x(), box.top() - side * 0.06), QPointF(box.center().x(), box.center().y()))


def icon(draw: Draw, colour: Callable[[], str], active: Callable[[], str] | None = None) -> QIcon:
    return QIcon(_Engine(draw, colour, active))


def eye(shown: bool = False) -> QIcon:
    """Inside a password field: the eye to show it, the crossed eye to hide it again."""
    return icon(_eye_off if shown else _eye, lambda: theme.current().MUTED)


def power() -> QIcon:
    return icon(_power, lambda: theme.current().RAIL_TEXT, lambda: theme.current().ON_RAIL)
