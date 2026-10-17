"""A board of tiles drawn in one widget: headings, section titles, hints and tiles in as
many columns as the width allows (layout.tile_columns). The Tk window drew each tile on a
canvas of its own; here the whole board is one paint, so 300 tiles cost one widget, and a
resize only moves rectangles.

What a tile looks like and does is the `TilePainter`'s: its height for a width, how to
paint it, the zones a click can hit (open, copy, the menu…) and their tips. The board
does the layout, the hover, the clicks, the tips and the keyboard: Tab and the arrows walk
the tiles, Enter acts ("enter"), Shift+Enter does the tile's second action, a double
click acts ("double"), the menu key or Shift+F10 opens its menu."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QHelpEvent, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from ...layout import TILE_MARGIN, TILE_MIN_WIDTH, tile_columns
from .. import icons, theme
from ..errors import guard_events

GAP = 12  # px between tiles


@dataclass
class Item:
    kind: str  # "heading", "section", "hint" or "tile"
    data: Any = None  # the tile's object; the text of the others
    extra: str = ""  # a heading's count


class TilePainter:
    """What the board asks of the tiles of one tab."""

    def height(self, data: Any, width: int) -> int:
        raise NotImplementedError

    def paint(self, p: QPainter, rect: QRect, data: Any, hot: bool, focused: bool) -> None:
        raise NotImplementedError

    def zones(self, data: Any, rect: QRect) -> list[tuple[str, QRect]]:
        """The places a click acts on, most specific first; the tile itself is "open"."""
        return []

    def tip(self, data: Any, zone: str) -> str:
        return ""


def rounded(p: QPainter, rect: QRectF, radius: float, fill: str, border: str | None = None, width: float = 1.0) -> None:
    p.setPen(QPen(QColor(border), width) if border else Qt.PenStyle.NoPen)
    p.setBrush(QColor(fill))
    p.drawRoundedRect(rect, radius, radius)


def elide(fm: QFontMetrics, text: str, width: int) -> str:
    return fm.elidedText(text, Qt.TextElideMode.ElideRight, max(0, width))


def glyph_px(fm: QFontMetrics) -> int:
    """The side of a drawn icon that sits in a line of this font."""
    return max(10, round(fm.height() * 0.8))


def glyph_text_width(fm: QFontMetrics, text: str) -> int:
    """The width of `text` as glyph_text draws it: a leading glyph is an icon and a gap."""
    name, rest = icons.split(text)
    return fm.horizontalAdvance(rest) + (glyph_px(fm) + 5 if name else 0)


def glyph_text(p: QPainter, rect: QRect, text: str, fm: QFontMetrics, colour: str, align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft) -> None:
    """Draw "▶ Run": the drawn icon of the leading glyph, then the text, in `colour`, in the
    painter's font, vertically centred in `rect`; `align` left or centre, elided to fit."""
    name, rest = icons.split(text)
    side = glyph_px(fm) if name else 0
    width = min(rect.width(), glyph_text_width(fm, text))
    x = rect.x() + (rect.width() - width) // 2 if align & Qt.AlignmentFlag.AlignHCenter else rect.x()
    if name:
        icons.paint(p, name, QRect(x, rect.y(), side, rect.height()), colour, size=side)
        x += side + 5
    p.setPen(QColor(colour))
    room = rect.right() + 1 - x
    p.drawText(QRect(x, rect.y(), room, rect.height()), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), elide(fm, rest, room))


@guard_events
class TileBoard(QWidget):
    """See the module text. `activate(data, zone)` and `menu(data, global_pos)` are the page's."""

    def __init__(self, painter: TilePainter, activate: Callable[[Any, str], object], menu: Callable[[Any, QPoint], object], parent: QWidget | None = None, second: str = ""):
        super().__init__(parent)
        self.painter, self.activate, self.menu, self.second = painter, activate, menu, second
        self.items: list[Item] = []
        self._rects: list[QRect] = []
        self._layout_width = -1
        self._height = 0
        self.hot = -1  # the tile under the pointer
        self.focused = -1  # the tile with the keyboard
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.fonts = {"heading": theme.font(17, bold=True), "count": theme.font(13), "section": theme.font(10, bold=True), "hint": theme.font(10)}

    # ---- content and layout

    def set_items(self, items: list[Item]) -> None:
        self.items = items
        self.hot = -1
        self.focused = min(self.focused, len(items) - 1)
        if self.focused >= 0 and self.items[self.focused].kind != "tile":
            self.focused = -1
        self._layout_width = -1
        self._relayout()

    def tiles(self) -> list[int]:
        return [i for i, it in enumerate(self.items) if it.kind == "tile"]

    def index_of(self, data: Any) -> int:
        return next((i for i, it in enumerate(self.items) if it.kind == "tile" and it.data is data), -1)

    def rect_of(self, index: int) -> QRect:
        self._relayout()
        return self._rects[index] if 0 <= index < len(self._rects) else QRect()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._compute(width)[1]

    def sizeHint(self) -> QSize:
        return QSize(TILE_MIN_WIDTH + TILE_MARGIN, max(self._height, 100))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        width = self.width()
        if width == self._layout_width:
            return
        self._layout_width = width
        rects, height = self._compute(width)
        self._rects = rects
        if height != self._height:
            self._height = height
            self.updateGeometry()
        self.update()

    def _text_height(self, item: Item, width: int) -> int:
        if item.kind == "heading":
            return QFontMetrics(self.fonts["heading"]).height()
        if item.kind == "section":
            return QFontMetrics(self.fonts["section"]).height()
        fm = QFontMetrics(self.fonts["hint"])
        return fm.boundingRect(QRect(0, 0, min(width, 560), 10_000), int(Qt.TextFlag.TextWordWrap), str(item.data)).height()

    def _compute(self, width: int) -> tuple[list[QRect], int]:
        cols = tile_columns(width + TILE_MARGIN)
        tile_w = max(1, (width - GAP * (cols - 1)) // cols)
        rects: list[QRect] = []
        y = 0
        run: list[int] = []

        def close_run() -> None:
            nonlocal y
            if not run:
                return
            for start in range(0, len(run), cols):
                line = run[start:start + cols]
                h = max(self.painter.height(self.items[i].data, tile_w) for i in line)
                for c, i in enumerate(line):
                    rects[i] = QRect(c * (tile_w + GAP), y, tile_w, h)
                y += h + GAP
            run.clear()

        for i, item in enumerate(self.items):
            rects.append(QRect())
            if item.kind == "tile":
                run.append(i)
                continue
            close_run()
            if item.kind == "section":
                y += 4
            h = self._text_height(item, width)
            rects[i] = QRect(0, y, width, h)
            y += h + (14 if item.kind == "heading" else 8)
        close_run()
        return rects, y

    # ---- painting

    def paintEvent(self, event: QPaintEvent) -> None:
        self._relayout()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        t = theme.current()
        p.fillRect(event.rect(), QColor(t.CARD))
        area = event.rect()
        for i, item in enumerate(self.items):
            r = self._rects[i]
            if not r.intersects(area):
                continue
            if item.kind == "tile":
                self.painter.paint(p, r, item.data, i == self.hot, i == self.focused and self.hasFocus())
            elif item.kind == "heading":
                p.setFont(self.fonts["heading"])
                p.setPen(QColor(t.TEXT))
                p.drawText(r, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), str(item.data))
                if item.extra:
                    w = QFontMetrics(self.fonts["heading"]).horizontalAdvance(str(item.data))
                    p.setFont(self.fonts["count"])
                    p.setPen(QColor(t.MUTED))
                    p.drawText(r.adjusted(w + 10, 2, 0, 0), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), item.extra)
            elif item.kind == "section":
                p.setFont(self.fonts["section"])
                p.setPen(QColor(t.MUTED))
                p.drawText(r, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), str(item.data).upper())
            else:
                p.setFont(self.fonts["hint"])
                p.setPen(QColor(t.MUTED))
                p.drawText(QRect(r.x(), r.y(), min(r.width(), 560), r.height()), int(Qt.TextFlag.TextWordWrap), str(item.data))
        p.end()

    # ---- the pointer

    def tile_at(self, pos: QPoint) -> int:
        for i in self.tiles():
            if self._rects[i].contains(pos):
                return i
        return -1

    def zone_at(self, index: int, pos: QPoint) -> str:
        for name, rect in self.painter.zones(self.items[index].data, self._rects[index]):
            if rect.contains(pos):
                return name
        return "open"

    def _set_hot(self, index: int) -> None:
        if index == self.hot:
            return
        old, self.hot = self.hot, index
        for i in (old, index):
            if 0 <= i < len(self._rects):
                self.update(self._rects[i].adjusted(-2, -2, 2, 2))
        self.setCursor(Qt.CursorShape.PointingHandCursor if index >= 0 else Qt.CursorShape.ArrowCursor)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._set_hot(self.tile_at(event.position().toPoint()))

    def leaveEvent(self, event: QEvent) -> None:
        self._set_hot(-1)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        index = self.tile_at(pos)
        if index < 0:
            return
        self.focused = index
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        data = self.items[index].data
        if event.button() == Qt.MouseButton.RightButton:
            self.menu(data, self.mapToGlobal(pos))
            return
        if event.button() == Qt.MouseButton.LeftButton:
            zone = self.zone_at(index, pos)
            if zone == "more":
                self.menu(data, self.mapToGlobal(pos))
            else:
                self.activate(data, zone)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        index = self.tile_at(pos)
        if index >= 0 and event.button() == Qt.MouseButton.LeftButton and self.zone_at(index, pos) == "open":
            self.activate(self.items[index].data, "double")

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            help_event: QHelpEvent = event  # type: ignore[assignment]
            pos = help_event.pos()
            index = self.tile_at(pos)
            text = self.painter.tip(self.items[index].data, self.zone_at(index, pos)) if index >= 0 else ""
            if text.strip():
                QToolTip.showText(help_event.globalPos(), text, self)
            else:
                QToolTip.hideText()
                event.ignore()
            return True
        return super().event(event)

    # ---- the keyboard

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        tiles = self.tiles()
        if self.focused < 0 and tiles:
            reason = event.reason()
            self.focused = tiles[-1] if reason == Qt.FocusReason.BacktabFocusReason else tiles[0]
        self._update_focused()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._update_focused()

    def _update_focused(self) -> None:
        if 0 <= self.focused < len(self._rects):
            self.update(self._rects[self.focused].adjusted(-2, -2, 2, 2))

    def _move_focus(self, index: int) -> None:
        old = self.focused
        self.focused = index
        if 0 <= old < len(self._rects):
            self.update(self._rects[old].adjusted(-2, -2, 2, 2))
        self._update_focused()
        parent = self.parentWidget()
        while parent is not None and not hasattr(parent, "ensureVisible"):
            parent = parent.parentWidget()
        if parent is not None:
            r = self._rects[index]
            parent.ensureVisible(r.center().x(), r.center().y(), r.width() // 2 + 8, r.height() // 2 + 8)

    def focusNextPrevChild(self, forward: bool) -> bool:
        """Tab walks the tiles; past the last (or before the first) it leaves the board."""
        tiles = self.tiles()
        if self.hasFocus() and self.focused in tiles:
            k = tiles.index(self.focused) + (1 if forward else -1)
            if 0 <= k < len(tiles):
                self._move_focus(tiles[k])
                return True
        self.focused = -1
        return super().focusNextPrevChild(forward)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        tiles = self.tiles()
        if self.focused not in tiles:
            super().keyPressEvent(event)
            return
        key, mods = event.key(), event.modifiers()
        data = self.items[self.focused].data
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.activate(data, self.second if mods & Qt.KeyboardModifier.ShiftModifier and self.second else "enter")
            return
        if key == Qt.Key.Key_Menu or (key == Qt.Key.Key_F10 and mods & Qt.KeyboardModifier.ShiftModifier):
            r = self._rects[self.focused]
            self.menu(data, self.mapToGlobal(QPoint(r.center().x(), r.top() + 52)))
            return
        k = tiles.index(self.focused)
        row = self._rects[self.focused]
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Left):
            k += 1 if key == Qt.Key.Key_Right else -1
        elif key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            below = key == Qt.Key.Key_Down
            same_col = [j for j in tiles if self._rects[j].x() == row.x() and (self._rects[j].y() > row.y() if below else self._rects[j].y() < row.y())]
            if same_col:
                k = tiles.index(same_col[0] if below else same_col[-1])
        else:
            super().keyPressEvent(event)
            return
        if 0 <= k < len(tiles):
            self._move_focus(tiles[k])

