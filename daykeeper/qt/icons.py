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
    def __init__(self, draw: Draw, colour: Callable[[], str], active: Callable[[], str] | None = None, disabled: Callable[[], str] | None = None):
        super().__init__()
        self._draw, self._colour, self._active, self._disabled = draw, colour, active, disabled

    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if mode == QIcon.Mode.Disabled:
            colour = QColor(self._disabled() if self._disabled is not None else theme.current().DISABLED_TEXT)
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
        return _Engine(self._draw, self._colour, self._active, self._disabled)


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


def icon(draw: Draw, colour: Callable[[], str], active: Callable[[], str] | None = None, disabled: Callable[[], str] | None = None) -> QIcon:
    return QIcon(_Engine(draw, colour, active, disabled))


def eye(shown: bool = False) -> QIcon:
    """Inside a password field: the eye to show it, the crossed eye to hide it again."""
    return icon(_eye_off if shown else _eye, lambda: theme.current().MUTED)


def power() -> QIcon:
    return _rail(_power)


def _gear(p: QPainter, r: QRectF) -> None:
    import math

    c = r.center()
    outer, inner = min(r.width(), r.height()) * 0.46, min(r.width(), r.height()) * 0.30
    path = QPainterPath()
    teeth = 8
    for i in range(teeth * 2):
        a0 = math.pi * 2 * i / (teeth * 2)
        a1 = math.pi * 2 * (i + 1) / (teeth * 2)
        rad = outer if i % 2 == 0 else inner * 1.18
        pts = [QPointF(c.x() + rad * math.cos(a), c.y() + rad * math.sin(a)) for a in (a0, a1)]
        if i == 0:
            path.moveTo(pts[0])
        else:
            path.lineTo(pts[0])
        path.lineTo(pts[1])
    path.closeSubpath()
    p.drawPath(path)
    hole = inner * 0.55
    p.drawEllipse(c, hole, hole)


def _padlock(p: QPainter, r: QRectF) -> None:
    w, h = r.width(), r.height()
    body = QRectF(r.left() + w * 0.2, r.top() + h * 0.45, w * 0.6, h * 0.45)
    p.drawRoundedRect(body, w * 0.08, w * 0.08)
    arc = QRectF(r.left() + w * 0.32, r.top() + h * 0.12, w * 0.36, h * 0.6)
    p.drawArc(arc, 0, 180 * 16)
    p.drawLine(QPointF(arc.left(), arc.center().y()), QPointF(arc.left(), body.top()))
    p.drawLine(QPointF(arc.right(), arc.center().y()), QPointF(arc.right(), body.top()))


def _turn(p: QPainter, r: QRectF, back: bool) -> None:
    """A curved arrow: back (undo) or forward (redo)."""
    w, h = r.width(), r.height()
    arc = QRectF(r.left() + w * 0.2, r.top() + h * 0.28, w * 0.6, h * 0.55)
    start, span = (180 * 16, -200 * 16) if back else (0, 200 * 16)
    p.drawArc(arc, start, span)
    tip = QPointF(arc.left(), arc.center().y()) if back else QPointF(arc.right(), arc.center().y())
    d = w * 0.16
    side = 1 if back else -1
    p.drawLine(tip, QPointF(tip.x() - side * d * 0.2, tip.y() - d))
    p.drawLine(tip, QPointF(tip.x() + side * d, tip.y() - d * 0.2))


def _rail(draw: Draw) -> QIcon:
    """On the dark rail: grey, white under the pointer, nearly the rail itself when off."""
    return icon(draw, lambda: theme.current().RAIL_TEXT, lambda: theme.current().ON_RAIL, lambda: theme.palette_mix(theme.current().RAIL_TEXT, theme.current().RAIL, 0.35))


def gear() -> QIcon:
    return _rail(_gear)


def padlock() -> QIcon:
    return _rail(_padlock)


def undo() -> QIcon:
    return _rail(lambda p, r: _turn(p, r, back=True))


def redo() -> QIcon:
    return _rail(lambda p, r: _turn(p, r, back=False))
