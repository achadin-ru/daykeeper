"""The “Notes” tab of the Qt window: days and search on the left, cards of the day on the
right. The same behaviour as notes_view of the Tk window (tests/test_notes_checklist.py is
its specification).

A card has a title, free text, a checklist and attached files. Checklist items are added
with the “+” row (Enter adds and keeps typing); done items move below under a “Done (n)”
header that folds. The “⋯” menu holds the card settings: colour, width, folding, files,
carrying over, deletion.

A card is one painted widget (NoteCard): everything on it is drawn, and a field — the
title, the text, an item, the “+” row — becomes a real editor over its spot only while it
is being edited (a click, Enter or a typed key open it; Esc, Enter or a click elsewhere
close it), so a busy day is a few widgets, not hundreds."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QIcon, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPaintEvent, QPen, QPixmap, QTextCursor
from PySide6.QtWidgets import QApplication, QFileDialog, QHBoxLayout, QLineEdit, QListWidgetItem, QMenu, QPlainTextEdit, QToolTip, QVBoxLayout, QWidget

from ... import palette
from ...i18n import _, fmt_day, hotkeys
from ...journal import today
from ...layout import CARD_GAP, CARD_MIN_WIDTH, column_width, flow_layout
from ...notes import MAX_SIZE, NOTE_COLORS, Attachment, Note, NoteItem
from .. import icons, theme
from ..dialogs.base import ask_yes_no, button, label, show_warning
from ..timers import IdleOnce
from ..widgets.common import scroll_area, search_field, section
from ..widgets.countlist import COUNT_ROLE, KEY_ROLE, add_count_item, count_list
from .base import TabPage
from ..errors import guard_events

CARD_SMALLEST = 200  # the narrowest a card can be dragged
UNFINISHED = "__unfinished__"  # a “day” on the left: everything unfinished across all days
WITH_FILES = "__files__"  # a “day” on the left: every card with attached files
FILE_ICONS = {".md": "📝", ".markdown": "📝", ".txt": "📄", ".log": "📄", ".pdf": "📕", ".png": "🖼", ".jpg": "🖼", ".jpeg": "🖼"}
RADIUS = 14
PAD = 14
PAD_Y = 10
CHECK = 18
TEXT_X = 26
RM_W = 18
PIN_ICON = 18  # px, the pin of a note
FOOT_ICON = 17  # px, ⋯, the palette and the clip under a note
FILE_ICON = 16  # px, before the name of an attached file
ROW_PAD = 3
PREVIEW_LINES = 8
PREVIEW_CHARS = 1200
MAX_EDIT_LINES = 12
EDGE = 10  # px of a card's right and bottom edge that drag its size
WRAP = int(Qt.TextFlag.TextWordWrap)


def note_colours(note: Note) -> tuple[str, str]:
    """(background, accent) of a card in the current theme (notes.NOTE_COLORS holds the
    light ones; the dark theme takes a thin tint of the accent over the card)."""
    t = theme.current()
    code, _label, bg, accent = next((c for c in NOTE_COLORS if c[0] == note.color), NOTE_COLORS[0])
    if not code:
        accent = t.LINK
    if t.name == "dark":
        bg = t.CARD if not code else palette.mix(accent, t.CARD, 0.16)
    return bg, accent


@guard_events
class ItemEdit(QPlainTextEdit):
    """One checklist item: wraps like the drawn text, but stays one line of text (Enter
    goes to the next field, a pasted line break becomes a space)."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.textChanged.connect(self._one_line)

    def _one_line(self) -> None:
        text = self.toPlainText()
        if "\n" in text:
            cursor = self.textCursor()
            pos = cursor.position()
            self.blockSignals(True)
            self.setPlainText(text.replace("\n", " "))
            self.blockSignals(False)
            cursor = self.textCursor()
            cursor.setPosition(min(pos, len(text)))
            self.setTextCursor(cursor)

    def value(self) -> str:
        return self.toPlainText()

    def event(self, event: QEvent) -> bool:
        """Alt+↑/↓ moves the item: the key is the editor's, not the window's shortcut."""
        if event.type() == QEvent.Type.ShortcutOverride and event.modifiers() & Qt.KeyboardModifier.AltModifier and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):  # type: ignore[attr-defined]
            event.accept()
            return True
        return super().event(event)


@guard_events
class NoteCard(QWidget):
    """One note as one painted widget. `relayout()` measures it from the note; `zones` is
    what is where (for the pointer and the keyboard); `edit(kind, payload)` puts a real
    editor over a spot."""

    HOVER_REPAINT = {"item", "check", "rm", "file", "frm", "more", "colour", "attach"}

    def __init__(self, page: NotesPage, parent: QWidget, note: Note, width: int):
        super().__init__(parent)
        self.page, self.note = page, note
        self.f = page.fonts
        self.fm = page.metrics
        self._width = width
        self.natural_h = 0
        self.min_h = 0
        self.zones: list[tuple[QRect, str, Any]] = []
        self.boxes: dict[Any, QRect] = {}
        self.text_box = QRect()
        self.hot: tuple[str, Any] | None = None
        self.sel = -1
        self.editor: QWidget | None = None
        self.editing: tuple[str, Any] | None = None
        self.add_text = ""  # what is typed in the “+” row
        self._drag: dict | None = None
        self._edge = ""
        self._text_lines = 0
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.relayout()

    # ---- measuring: where everything goes (the painting follows the same plan)

    def _wrap_h(self, font_key: str, text: str, width: int) -> int:
        return self.fm[font_key].boundingRect(QRect(0, 0, max(1, width), 100_000), WRAP, text).height()

    def relayout(self) -> None:
        """Measure the card from the note: zones, boxes and the heights the page lays it out by."""
        note, fm = self.note, self.fm
        self.zones, self.boxes = [], {}
        w = self._width
        y = PAD_Y
        pin_w = PIN_ICON + 4
        title_h = max(self._wrap_h("title", note.title or _("Title"), w - 2 * PAD - 34), fm["title"].height())
        self._title_rect = QRect(PAD, y, w - 2 * PAD - 34, title_h)
        self._pin_rect = QRect(w - PAD - pin_w, y, pin_w, fm["icon"].height())
        self._zone(QRect(PAD - 2, y - 2, self._pin_rect.x() - 4 - PAD + 2, title_h + 4), "title", None)
        self._zone(QRect(self._pin_rect.x() - 4, y - 2, w - self._pin_rect.x() + 4, self._pin_rect.height() + 4), "pin", None)
        self.boxes["title"] = QRect(PAD - 2, y - 2, self._pin_rect.x() - 6 - PAD + 2, max(title_h, self.fm["title"].height()) + 6)
        y += max(title_h, self._pin_rect.height()) + 6

        line = fm["text"].lineSpacing()
        preview = self.preview()
        natural_text_h = max(2 * line + 8, self._wrap_h("text", preview or _("Note text…"), w - 2 * PAD - 4) + 8)
        text_h = natural_text_h
        if self.editing == ("text", None) and self.editor is not None:
            text_h = max(text_h, self.editor.height() + 2)
        y_text = y
        y += text_h

        done_items, open_items = note.done_items(), note.open_items()
        y += 14
        self._progress_y = -1
        if note.items:
            self._progress_y = y
            y += fm["small_bold"].height() + 6
        self._rows: list[tuple[NoteItem, QRect, bool]] = []
        y = self._measure_items(open_items, y, done=False)
        plus_y = y + 8
        add_w = w - 2 * PAD - TEXT_X
        row_h = max(self._wrap_h("item", self.add_text or _("Add item"), add_w), fm["item"].lineSpacing())
        self._plus_rect = QRect(PAD, plus_y - 3, TEXT_X - 4, row_h)
        self._add_rect = QRect(PAD + TEXT_X, plus_y, add_w, row_h)
        self._zone(QRect(PAD, plus_y - 2, TEXT_X - 4, row_h + 4), "plus", None)
        self._zone(QRect(PAD + TEXT_X - 4, plus_y - 2, w - 2 * PAD - TEXT_X + 4, row_h + 4), "add", None)
        self.boxes["add"] = QRect(PAD + TEXT_X - 3, plus_y - 3, add_w + 3, row_h + 6)
        y = plus_y + row_h + 4
        self._fold_rect = self._clear_rect = QRect()
        if done_items:
            y += 12
            sh = fm["small_bold"].height()
            fold_text = ("▸ " if note.hide_done else "▾ ") + _("Done ({n})").format(n=len(done_items))
            self._fold_text = fold_text
            self._fold_rect = QRect(PAD, y, fm["small_bold"].horizontalAdvance(fold_text) + 4, sh)
            cw = fm["small"].horizontalAdvance(_("Clear")) + 4
            self._clear_rect = QRect(w - PAD - cw, y, cw, sh)
            self._zone(self._fold_rect.adjusted(-2, -2, 4, 2), "fold", None)
            self._zone(self._clear_rect.adjusted(-4, -2, PAD, 2), "clear", None)
            y += sh + 4
            if not note.hide_done:
                y = self._measure_items(done_items, y, done=True)

        self._files: list[tuple[Attachment, QRect]] = []
        if note.attachments:
            y += 12
            for att in note.attachments:
                text_h = self._wrap_h("item", att.name, w - 2 * PAD - 40 - FILE_ICON - 8)
                row = QRect(PAD, y, w - 2 * PAD, text_h + 8)
                self._files.append((att, row))
                self._zone(QRect(w - PAD - RM_W - 4, y, RM_W + 4, row.height()), "frm", att)
                self._zone(QRect(PAD, y, w - 2 * PAD - RM_W - 4, row.height()), "file", att)
                y += row.height() + 4

        y += 14
        self._foot_y = y
        self._foot: list[tuple[str, QRect, str]] = []
        x = PAD
        stamp = time.strftime("%H:%M", time.localtime(note.updated))
        self._foot.append(("stamp", QRect(x, y, fm["small"].horizontalAdvance(stamp), fm["small"].height()), stamp))
        x += self._foot[-1][1].width() + 10
        if self.page.across_days():
            day = fmt_day(note.date)
            self._foot.append(("day", QRect(x, y, fm["small"].horizontalAdvance(day), fm["small"].height()), day))
            x += self._foot[-1][1].width() + 10
        if note.undone_items() and note.date != today():
            carry = _("⤴ to today")
            r = QRect(x, y, fm["small_bold"].horizontalAdvance(carry), fm["small_bold"].height())
            self._foot.append(("carry", r, carry))
            self._zone(r.adjusted(-2, -2, 2, 2), "carry", None)
        right = w - PAD
        foot_bottom = y + fm["small"].height()
        for kind, glyph, gap in (("more", "⋯", 0), ("colour", "🎨", 9), ("attach", "📎", 9)):
            right -= gap
            gw = FOOT_ICON + 4
            r = QRect(right - gw, y - 3, gw, fm["icon"].height())
            self._foot.append((kind, r, glyph))
            self._zone(r.adjusted(-3, -2, 3, 2), kind, None)
            right = r.x()
            foot_bottom = max(foot_bottom, r.bottom() + 1)
        y = foot_bottom + PAD_Y

        self.natural_h = y
        self.min_h = y - natural_text_h + 2 * line + 8
        height = self.height() if self.height() > 0 else y
        stretch = height - y if note.height and height > y else 0
        self.text_box = QRect(PAD, y_text, w - 2 * PAD, text_h + stretch)
        self._stretch = stretch
        if stretch:  # dragged taller: the room goes to the text, the rest moves down
            below = y_text + text_h
            self.zones = [(r.translated(0, stretch) if r.y() >= below else r, k, p) for r, k, p in self.zones]
            self.boxes = {k: (r.translated(0, stretch) if r.y() >= below else r) for k, r in self.boxes.items()}
        if self.editor is not None:
            self._place_editor()

    def _measure_items(self, items: list[NoteItem], y: int, done: bool) -> int:
        w = self._width
        wrap = w - 2 * PAD - TEXT_X - RM_W
        x_text = PAD + TEXT_X
        for item in items:
            y += ROW_PAD
            h = max(self._wrap_h("item", item.text or " ", wrap), CHECK)
            self._rows.append((item, QRect(x_text, y, wrap, h), done))
            self._zone(QRect(PAD - 2, y - ROW_PAD, x_text - 4 - PAD + 2, h + 2 * ROW_PAD), "check", item)
            self._zone(QRect(x_text - 4, y - ROW_PAD, wrap + 4, h + 2 * ROW_PAD), "item", item)
            self._zone(QRect(w - PAD - RM_W, y - ROW_PAD, RM_W + 2, h + 2 * ROW_PAD), "rm", item)
            self.boxes[id(item)] = QRect(x_text - 3, y - 3, wrap + RM_W - 1, h + 6)
            y += h + ROW_PAD
        return y

    def preview(self) -> str:
        text = self.note.text
        lines = text.split("\n")
        preview = "\n".join(lines[:PREVIEW_LINES])
        cut = len(lines) > PREVIEW_LINES or len(preview) > PREVIEW_CHARS
        preview = preview[:PREVIEW_CHARS]
        return preview.rstrip() + " …" if cut else preview

    def _zone(self, rect: QRect, kind: str, payload: Any) -> None:
        self.zones.append((rect, kind, payload))

    def zone_at(self, pos: QPoint) -> tuple[str, Any] | None:
        for rect, kind, payload in self.zones:
            if rect.contains(pos):
                return kind, payload
        if self.text_box.y() <= pos.y() < self.text_box.y() + self.text_box.height():
            return "text", None
        return None

    def set_width(self, width: int) -> None:
        if width != self._width:
            self._width = width
            self.relayout()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if event.size().width() != self._width:
            self._width = event.size().width()
            self.relayout()
        elif event.size().height() != event.oldSize().height() and (self.note.height or self._stretch):
            self.relayout()

    # ---- painting

    def paintEvent(self, event: QPaintEvent) -> None:
        t = theme.current()
        note = self.note
        bg, accent = note_colours(note)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(t.CARD))
        outline = t.BORDER if not note.color else theme.blend(accent, bg, 0.35)
        p.setPen(QPen(QColor(outline), 1))
        p.setBrush(QColor(bg))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)
        hidden = self.editing
        s = self._stretch

        def moved(r: QRect, below: int) -> QRect:
            return r.translated(0, s) if s and r.y() >= below else r

        below = self.text_box.y() + self.text_box.height() - s
        if hidden != ("title", None):
            p.setFont(self.f["title"])
            p.setPen(QColor(t.TEXT if note.title else t.MUTED))
            p.drawText(self._title_rect, WRAP, note.title or _("Title"))
        pin = QRect(self._pin_rect.right() + 1 - PIN_ICON, self._pin_rect.y(), PIN_ICON, self._pin_rect.height())
        icons.paint(p, "pin" if note.pinned else "pin_off", pin, t.TEXT if note.pinned else t.MUTED, size=PIN_ICON)
        if hidden != ("text", None):
            p.setFont(self.f["text"])
            p.setPen(QColor(t.TEXT if note.text else t.MUTED))
            p.drawText(QRect(PAD + 2, self.text_box.y() + 4, self._width - 2 * PAD - 4, self.text_box.height()), WRAP, self.preview() or _("Note text…"))
        if self._progress_y >= 0:
            n_done, total = len(note.done_items()), len(note.items)
            text = _("✓ {done} / {total}").format(done=n_done, total=total) if n_done == total else f"{n_done} / {total}"
            colour = t.SUCCESS if n_done == total else t.MUTED
            p.setFont(self.f["small_bold"])
            tw = self.fm["small_bold"].horizontalAdvance(text)
            y = self._progress_y + s if s and self._progress_y >= below else self._progress_y
            p.setPen(QColor(colour))
            p.drawText(QRect(self._width - PAD - tw, y, tw, self.fm["small_bold"].height()), int(Qt.AlignmentFlag.AlignRight), text)
            mid = y + self.fm["small_bold"].height() // 2
            span = max(0, self._width - PAD - tw - 8 - PAD)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.BORDER))
            p.drawRoundedRect(QRectF(PAD, mid - 2, span, 4), 2, 2)
            filled = span * n_done / total
            if filled:
                p.setBrush(QColor(colour if n_done == total else accent))
                p.drawRoundedRect(QRectF(PAD, mid - 2, filled, 4), 2, 2)
        for item, rect, done in self._rows:
            r = moved(rect, below)
            hot = self.hot is not None and self.hot[0] in ("item", "check", "rm") and self.hot[1] is item
            self._paint_check(p, QRect(PAD, r.y() + 2, CHECK, CHECK), done, hot and not done, accent)
            if hidden != ("item", item):
                p.setFont(self.f["item_done" if done else "item"])
                p.setPen(QColor(t.MUTED if done else t.TEXT))
                p.drawText(r, WRAP, item.text)
            if hot:
                p.setFont(self.f["small"])
                p.setPen(QColor(t.MUTED))
                p.drawText(QRect(self._width - PAD - RM_W, r.y() + 2, RM_W, CHECK), int(Qt.AlignmentFlag.AlignRight), "✕")
        p.setFont(self.f["plus"])
        p.setPen(QColor(accent))
        p.drawText(moved(self._plus_rect, below).adjusted(1, 0, 0, 4), int(Qt.AlignmentFlag.AlignLeft), "+")
        if hidden != ("add", None):
            p.setFont(self.f["item"])
            p.setPen(QColor(t.TEXT if self.add_text else t.MUTED))
            p.drawText(moved(self._add_rect, below), WRAP, self.add_text or _("Add item"))
        if not self._fold_rect.isNull():
            p.setFont(self.f["small_bold"])
            p.setPen(QColor(t.MUTED))
            p.drawText(moved(self._fold_rect, below), int(Qt.AlignmentFlag.AlignLeft), self._fold_text)
            p.setFont(self.f["small"])
            p.drawText(moved(self._clear_rect, below), int(Qt.AlignmentFlag.AlignRight), _("Clear"))
        for att, rect in self._files:
            r = moved(rect, below)
            exists = att.exists()
            hot = self.hot is not None and self.hot[0] in ("file", "frm") and self.hot[1] is att
            p.setPen(QPen(QColor(accent if hot else t.BORDER), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
            p.setFont(self.f["item"])
            ink = t.TEXT if exists else t.MUTED
            icon = FILE_ICONS.get(att.suffix, "📄") if exists else "⚠"
            icons.paint(p, icon, QRect(r.x() + 8, r.y() + 4, FILE_ICON, self.fm["item"].height()), t.MUTED if exists else t.DANGER, size=FILE_ICON)
            p.setPen(QColor(ink))
            p.drawText(r.adjusted(8 + FILE_ICON + 8, 4, -40, -4), WRAP, att.name)
            p.setFont(self.f["small"])
            p.setPen(QColor(t.MUTED))
            p.drawText(r.adjusted(0, 5, -8, 0), int(Qt.AlignmentFlag.AlignRight), "✕")
        for kind, rect, text in self._foot:
            r = rect.translated(0, s)
            if kind in ("more", "colour", "attach"):
                icons.paint(p, text, r, t.TEXT if self.hot is not None and self.hot[0] == kind else t.MUTED, size=FOOT_ICON)
                continue
            if kind == "carry":
                p.setFont(self.f["small_bold"])
                p.setPen(QColor(accent))
            else:
                p.setFont(self.f["small"])
                p.setPen(QColor(t.MUTED))
            p.drawText(r, int(Qt.AlignmentFlag.AlignLeft), text)
        if self.sel >= 0 and self.hasFocus():
            box = self._box_of(*self.spots()[min(self.sel, len(self.spots()) - 1)])
            if box is not None:
                p.setPen(QPen(QColor(t.ACCENT), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(QRectF(box).adjusted(-1, -1, 1, 1), 4, 4)
        p.end()

    @staticmethod
    def _paint_check(p: QPainter, r: QRect, done: bool, hover: bool, accent: str) -> None:
        t = theme.current()
        box = QRectF(r).adjusted(1, 1, -1, -1)
        if done:
            p.setPen(QPen(QColor(accent), 1))
            p.setBrush(QColor(accent))
            p.drawRoundedRect(box, 5, 5)
            pen = QPen(QColor(t.ON_ACCENT), 2.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            path = QPainterPath()
            x0, y0 = box.x(), box.y()
            path.moveTo(x0 + 3.5, y0 + 8)
            path.lineTo(x0 + 6.5, y0 + 11)
            path.lineTo(x0 + 12, y0 + 4.5)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        else:
            p.setPen(QPen(QColor(accent if hover else t.MUTED), 1.6))
            p.setBrush(QColor(t.FIELD))
            p.drawRoundedRect(box, 5, 5)

    # ---- the pointer

    def tips(self, kind: str, payload: Any) -> str:
        if kind == "pin":
            return _("Unpin") if self.note.pinned else _("Pin to the top of the day")
        if kind == "file" and isinstance(payload, Attachment):
            return payload.path if payload.exists() else _("File not found: {path}").format(path=payload.path)
        return {
            "plus": _("Add a checklist item (Enter adds and keeps typing)"),
            "clear": _("Remove the done items"),
            "carry": _("Undone items as a new card for today"),
            "more": _("Card settings"),
            "colour": _("Card colour"),
            "attach": _("Attach files"),
            "frm": _("Detach the file (the file itself stays on disk)"),
        }.get(kind, "")

    def _edge_at(self, pos: QPoint) -> str:
        right = self.width() - EDGE <= pos.x() <= self.width() + 2
        bottom = self.height() - EDGE <= pos.y() <= self.height() + 2
        return "xy" if right and bottom else "x" if right else "y" if bottom else ""

    def _set_hot(self, zone: tuple[str, Any] | None) -> None:
        if zone == self.hot:
            return
        old, self.hot = self.hot, zone
        if any(z is not None and z[0] in self.HOVER_REPAINT for z in (old, zone)):
            self.update()
        if zone is None:
            self.unsetCursor()
            return
        self.setCursor(Qt.CursorShape.IBeamCursor if zone[0] in ("title", "text", "item", "add") else Qt.CursorShape.PointingHandCursor)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            zone = self.zone_at(event.pos())  # type: ignore[attr-defined]
            text = self.tips(*zone) if zone is not None else ""
            if text:
                QToolTip.showText(event.globalPos(), text, self)  # type: ignore[attr-defined]
            else:
                QToolTip.hideText()
            return True
        return super().event(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        if self._drag is not None:
            self._drag_to(event.globalPosition().toPoint())
            return
        edge = self._edge_at(pos)
        if edge:
            self._edge = edge
            self.setCursor({"x": Qt.CursorShape.SizeHorCursor, "y": Qt.CursorShape.SizeVerCursor, "xy": Qt.CursorShape.SizeFDiagCursor}[edge])
            return
        if self._edge:
            self._edge = ""
            self.hot = ("", None)  # the cursor is set again below
        self._set_hot(self.zone_at(pos))

    def leaveEvent(self, event: QEvent) -> None:
        if self._drag is None:
            self._edge = ""
            self._set_hot(None)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        if event.button() == Qt.MouseButton.RightButton:
            self._right_click(pos, event.globalPosition().toPoint())
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        edge = self._edge_at(pos)
        if edge:
            g = event.globalPosition().toPoint()
            self._drag = {"zone": edge, "x": g.x(), "y": g.y(), "w": self.width(), "h": self.height(), "before": (self.note.width, self.note.height)}
            return
        self.click(pos, event.globalPosition().toPoint())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag is None:
            return
        before = self._drag["before"]
        self._drag = None
        if (self.note.width, self.note.height) != before:
            self.page.note_changed(self.note)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        edge = self._edge_at(event.position().toPoint())
        if not edge:
            self.mousePressEvent(event)
            return
        if "x" in edge:
            self.note.width = 0
        if "y" in edge:
            self.note.height = 0
        self.page.note_changed(self.note)
        self.page.place_cards()

    def _drag_to(self, g: QPoint) -> None:
        d = self._drag
        if d is None:
            return
        scale = 1.0  # sizes in the note are logical pixels, as in the Tk window at 100 %
        if "x" in d["zone"]:
            width = max(CARD_SMALLEST, d["w"] + g.x() - d["x"])
            self.note.width = min(MAX_SIZE, round(width / scale))
        if "y" in d["zone"]:
            height = max(self.min_h, d["h"] + g.y() - d["y"])
            self.note.height = min(MAX_SIZE, round(height / scale))
        self.page.place_cards()

    def click(self, pos: QPoint, global_pos: QPoint | None = None) -> None:
        zone = self.zone_at(pos)
        if zone is None:
            self.setFocus()
            return
        kind, payload = zone
        page, note = self.page, self.note
        g = global_pos or self.mapToGlobal(pos)
        if kind in ("title", "text", "add"):
            self.edit(kind, None, at=pos)
        elif kind == "item":
            self.edit("item", payload, at=pos)
        elif kind == "pin":
            page.note_pin(note)
        elif kind == "check" and isinstance(payload, NoteItem):
            page.note_toggle(self, note, payload)
        elif kind == "rm" and isinstance(payload, NoteItem):
            page.note_remove_item(self, note, payload)
        elif kind == "plus":
            if self.add_text.strip():
                page.note_add_item(self, note)
            else:
                self.edit("add")
        elif kind == "fold":
            page.note_fold_done(self, note)
        elif kind == "clear":
            page.note_clear_done(self, note)
        elif kind == "file" and isinstance(payload, Attachment):
            page.open_attachment(payload)
        elif kind == "frm" and isinstance(payload, Attachment):
            page.note_detach(note, payload)
        elif kind == "carry":
            page.note_carry_over(note)
        elif kind == "more":
            page.note_menu(note, g)
        elif kind == "colour":
            page.note_color_menu(note, g)
        elif kind == "attach":
            page.note_attach(note)

    def _right_click(self, pos: QPoint, global_pos: QPoint) -> None:
        zone = self.zone_at(pos)
        kind, payload = zone if zone is not None else ("", None)
        if kind in ("item", "check", "rm") and isinstance(payload, NoteItem):
            self.page.note_item_menu(self, self.note, payload, global_pos)
        elif kind in ("file", "frm") and isinstance(payload, Attachment):
            self.page.file_menu(self.note, payload, global_pos)
        else:
            self.page.note_menu(self.note, global_pos)

    # ---- the keyboard: Tab reaches the card, ↑/↓ pick a field, Enter or a key opens it

    def spots(self) -> list[tuple[str, Any]]:
        out: list[tuple[str, Any]] = [("title", None), ("text", None)]
        out += [("item", i) for i in self.note.open_items()]
        out.append(("add", None))
        if not self.note.hide_done:
            out += [("item", i) for i in self.note.done_items()]
        return out

    def select(self, i: int) -> None:
        spots = self.spots()
        i = -1 if i < 0 else min(i, len(spots) - 1)
        if i != self.sel:
            self.sel = i
            self.update()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.select(0 if self.sel < 0 else self.sel)
        self.update()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.update()

    def edit_selected(self) -> None:
        spots = self.spots()
        if 0 <= self.sel < len(spots):
            self.edit(*spots[self.sel])

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Up:
            self.select(self.sel - 1)
        elif key == Qt.Key.Key_Down:
            self.select(self.sel + 1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.edit_selected()
        elif event.text() and event.text().isprintable() and not event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier) and self.sel >= 0:
            self.edit_selected()  # a printable key opens the field with that key typed
            editor = self.editor
            if isinstance(editor, QLineEdit):
                editor.insert(event.text())
            elif isinstance(editor, QPlainTextEdit):
                editor.insertPlainText(event.text())
        else:
            super().keyPressEvent(event)

    # ---- the editors: a real field over the spot, one at a time

    def _box_of(self, kind: str, payload: Any) -> QRect | None:
        if kind == "text":
            return self.text_box
        if kind == "item":
            return self.boxes.get(id(payload))
        return self.boxes.get(kind)

    def _style_editor(self, editor: QWidget) -> None:
        bg, _accent = note_colours(self.note)
        t = theme.current()
        cls = type(editor).__name__ if isinstance(editor, QLineEdit) else "QPlainTextEdit"
        if isinstance(editor, QLineEdit):
            cls = "QLineEdit"
        editor.setStyleSheet(f"{cls} {{ background: {bg}; color: {t.TEXT}; border: 1px solid {bg}; border-radius: 4px; padding: 0px 1px; }} {cls}:focus {{ border: 1px solid {t.ACCENT}; }}")

    def edit(self, kind: str, payload: Any = None, at: QPoint | None = None) -> None:
        """Open the editor of a field; `at` — the click, where the cursor goes (the end without it)."""
        if self.editor is not None:
            if self.editing == (kind, payload):
                self.editor.setFocus()
                return
            self.close_editor()
        box = self._box_of(kind, payload)
        if box is None:
            return
        page, note = self.page, self.note
        self.editing = (kind, payload)
        editor: QWidget
        if kind == "title":
            line = QLineEdit(note.title, self)
            line.setFont(self.f["title"])
            line.textEdited.connect(lambda text: page.note_edit(note, "title", text))
            editor = line
        elif kind == "text":
            text = QPlainTextEdit(self)
            text.setFont(self.f["text"])
            text.setPlainText(note.text)
            text.setTabChangesFocus(False)
            text.textChanged.connect(lambda w=text: self._text_changed(w))
            self._text_lines = 0
            editor = text
        elif kind == "add":
            line = QLineEdit(self.add_text, self)
            line.setFont(self.f["item"])
            line.textEdited.connect(self._add_typed)
            editor = line
        else:
            item = payload
            if not isinstance(item, NoteItem):
                self.editing = None
                return
            wrap = ItemEdit(self)
            wrap.setFont(self.f["item_done" if item.done else "item"])
            wrap.setPlainText(item.text)
            wrap.textChanged.connect(lambda w=wrap, it=item: page.note_item_edit(note, it, w.toPlainText()))
            editor = wrap
        self._style_editor(editor)
        editor.installEventFilter(self)
        self.editor = editor
        if kind == "text":
            self._grow_text(editor)  # type: ignore[arg-type]
        self.relayout()
        self.page.place_cards()
        self._place_editor()
        editor.show()
        editor.setFocus()
        self._put_cursor(editor, box, at)
        self.update()

    def _put_cursor(self, editor: QWidget, box: QRect, at: QPoint | None) -> None:
        if isinstance(editor, QLineEdit):
            editor.setCursorPosition(editor.cursorPositionAt(at - box.topLeft()) if at is not None else len(editor.text()))
        elif isinstance(editor, QPlainTextEdit):
            if at is not None:
                editor.setTextCursor(editor.cursorForPosition(editor.viewport().mapFrom(self, at)))
            else:
                cursor = editor.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.End)
                editor.setTextCursor(cursor)

    def _place_editor(self) -> None:
        if self.editor is None or self.editing is None:
            return
        box = self._box_of(*self.editing)
        if box is None:
            return
        if self.editing[0] == "text":
            self.editor.setGeometry(box.x(), box.y(), box.width(), max(box.height(), self.editor.height()))
        elif isinstance(self.editor, QLineEdit):
            self.editor.setGeometry(box.x(), box.y(), box.width(), max(box.height(), self.editor.sizeHint().height()))
        else:
            self.editor.setGeometry(box)

    def _text_changed(self, editor: QPlainTextEdit) -> None:
        self.page.note_edit(self.note, "text", editor.toPlainText())
        self._grow_text(editor)

    def _grow_text(self, editor: QPlainTextEdit) -> None:
        """The text editor grows with its lines (to a limit) and the rest of the card moves
        down — the height is set only when it really changes (the lesson of the Tk window)."""
        if self.note.height:
            if self._text_lines == 0:
                self._text_lines = -1
                editor.setFixedHeight(self.text_box.height())
            return
        lines = max(2, min(MAX_EDIT_LINES, editor.document().blockCount() + 1))
        if lines != self._text_lines:
            self._text_lines = lines
            editor.setFixedHeight(editor.fontMetrics().lineSpacing() * lines + 10)
            if self.editing == ("text", None):
                self.relayout()
                self.page.place_cards()

    def _add_typed(self, text: str) -> None:
        self.add_text = text

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is not self.editor or self.editing is None:
            return False
        kind, payload = self.editing
        if event.type() == QEvent.Type.KeyPress:
            key, mods = event.key(), event.modifiers()  # type: ignore[attr-defined]
            if key == Qt.Key.Key_Tab and not mods & Qt.KeyboardModifier.ShiftModifier:
                self.neighbour(+1)
                return True
            if key == Qt.Key.Key_Backtab or (key == Qt.Key.Key_Tab and mods & Qt.KeyboardModifier.ShiftModifier):
                self.neighbour(-1)
                return True
            if key == Qt.Key.Key_Escape:
                self.close_editor()
                self.setFocus()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and kind != "text":
                if kind == "title":
                    self.edit("text")
                elif kind == "add":
                    self.page.note_add_item(self, self.note)
                else:
                    self.edit("add")
                return True
            if kind == "item" and isinstance(payload, NoteItem):
                if mods & Qt.KeyboardModifier.AltModifier and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                    self.page.note_move_item(self, self.note, payload, -1 if key == Qt.Key.Key_Up else +1)
                    return True
                if key == Qt.Key.Key_Backspace and not self.editor.toPlainText():  # type: ignore[attr-defined]
                    self.page.note_backspace_item(self, self.note, payload)
                    return True
        elif event.type() == QEvent.Type.FocusOut:
            reason = event.reason()  # type: ignore[attr-defined]
            if reason in (Qt.FocusReason.ActiveWindowFocusReason, Qt.FocusReason.PopupFocusReason):
                return False  # the app only lost the foreground (or a menu opened): typing goes on on return
            if kind == "add" and self.add_text.strip():
                QTimer.singleShot(0, self, lambda: self.page.commit_left_item(self))
            QTimer.singleShot(0, self, self._left)
        elif event.type() == QEvent.Type.ContextMenu:
            g = event.globalPos()  # type: ignore[attr-defined]
            if kind == "item" and isinstance(payload, NoteItem):
                self.page.note_item_menu(self, self.note, payload, g)
            else:
                self.page.note_menu(self.note, g)
            return True
        return False

    def neighbour(self, step: int) -> None:
        """Tab / Shift+Tab: the next or the previous field; past the last one — the next card."""
        spots = self.spots()
        try:
            i = spots.index(self.editing) + step  # type: ignore[arg-type]
        except ValueError:
            i = 0
        self.close_editor()
        if 0 <= i < len(spots):
            self.edit(*spots[i])
        else:
            self.page.focus_card_after(self, step)

    def _left(self) -> None:
        editor = self.editor
        if editor is None:
            return
        if QApplication.focusWidget() is not editor:
            self.close_editor()

    def close_editor(self) -> None:
        """The editor goes; the card shows the field as it is now."""
        editor, self.editor, self.editing = self.editor, None, None
        if editor is None:
            return
        editor.removeEventFilter(self)
        editor.hide()
        editor.deleteLater()
        self.relayout()
        self.page.place_cards()
        self.update()

    @property
    def text_widget(self) -> QPlainTextEdit | None:
        return self.editor if isinstance(self.editor, QPlainTextEdit) and self.editing == ("text", None) else None


@guard_events
class NotesPage(TabPage):
    """The Notes tab. `nday` is the day (or the list) on the left, None before the first visit."""

    name = "notes"

    def build(self) -> None:
        self.nday: str | None = None
        self._loading = False
        self.shown: list[Note] = []
        self.cards: dict[str, NoteCard] = {}
        self.fonts = {
            "title": theme.font(13, bold=True), "text": theme.font(11), "item": theme.font(11), "small": theme.font(9),
            "small_bold": theme.font(9, bold=True), "icon": theme.font(12), "plus": theme.font(14, bold=True),
        }
        done = QFont(self.fonts["item"])
        done.setStrikeOut(True)
        self.fonts["item_done"] = done
        self.metrics = {k: QFontMetrics(v) for k, v in self.fonts.items()}
        _splitter, left, col, right = self.split(300, 240)
        self.search = QLineEdit(left)
        search_field(self.search, _("🔍 Search notes"))
        self.search.setToolTip(_("Search across all days: title, text, items, file names"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self.render_notes())
        col.addWidget(self.search)
        col.addWidget(section(_("DAYS"), left))
        self.days = count_list(left)
        self.days.setToolTip(_("done / total items"))
        self.days.currentItemChanged.connect(self._on_day_select)
        self.days.itemClicked.connect(self._on_day_click)
        col.addWidget(self.days, 1)
        row = QHBoxLayout()
        note_btn = button(_("+ Note"), "primary", left)
        note_btn.clicked.connect(lambda: self.new_note())
        check_btn = button(_("+ Checklist"), "small", left)
        check_btn.clicked.connect(lambda: self.new_note(checklist=True))
        row.addWidget(note_btn)
        row.addWidget(check_btn)
        row.addStretch(1)
        col.addLayout(row)

        self.area, inner, icol = scroll_area(right, (24, 20, 24, 20))
        self.title = label("", "heading", inner)
        icol.addWidget(self.title)
        icol.addSpacing(12)
        self.empty = label("", "muted", inner)
        self.empty.setWordWrap(True)
        icol.addWidget(self.empty)
        self.flow = QWidget(inner)
        self.flow.setObjectName("NotesFlow")
        icol.addWidget(self.flow)
        icol.addStretch(1)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.area)
        self._relayout = IdleOnce(self, self.place_cards)
        self._flow_width = 0
        self.flow.installEventFilter(self)

    # ---- what the window asks for

    def refresh(self, keep_scroll: bool = False) -> None:
        self.refresh_days(keep_scroll=keep_scroll)

    def new_item(self) -> None:
        self.new_note()

    def focus_search(self) -> None:
        self.search.setFocus()

    def cancel_jobs(self) -> None:
        self._relayout.cancel()
        for card in self.cards.values():
            card.close_editor()

    # ---- days

    def refresh_days(self, select: str | None = None, keep_scroll: bool = False) -> None:
        if self.vault is None:
            return
        notes = self.vault.notes
        self._loading = True
        try:
            self.days.clear()
            unfinished = notes.unfinished()
            add_count_item(self.days, _("  ⏳ Unfinished"), UNFINISHED, len(unfinished))
            add_count_item(self.days, _("  📎 With files"), WITH_FILES, len(notes.with_files()))
            dates = notes.dates()
            if today() not in dates:
                dates.insert(0, today())
            for date in dates:
                done, total = notes.day_progress(date)
                item = add_count_item(self.days, f"  {fmt_day(date)}", date, f"{done} / {total}" if total else "")
                if date == today():
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
            target = select or self.nday or today()
            keys = [self.days.item(i).data(KEY_ROLE) for i in range(self.days.count())]
            if target not in keys:
                target = today()
            self.nday = target
            self.days.setCurrentRow(keys.index(target))
        finally:
            self._loading = False
        self.render_notes(keep_scroll=keep_scroll)

    def _on_day_select(self, current: QListWidgetItem | None, _previous=None) -> None:
        if self._loading or current is None or self.vault is None:
            return
        key = current.data(KEY_ROLE)
        if key == self.nday:
            return
        self.nday = key
        if self.search.text():
            self.search.setText("")
        else:
            self.render_notes()

    def _on_day_click(self, item: QListWidgetItem) -> None:
        if self.search.text() and item.data(KEY_ROLE) == self.nday:
            self.search.setText("")

    def refresh_counts(self) -> None:
        """The numbers on the left without rebuilding the cards."""
        notes = self.vault.notes
        for i in range(self.days.count()):
            item = self.days.item(i)
            key = item.data(KEY_ROLE)
            if key == UNFINISHED:
                n = len(notes.unfinished())
                item.setData(COUNT_ROLE, str(n) if n else "")
            elif key == WITH_FILES:
                n = len(notes.with_files())
                item.setData(COUNT_ROLE, str(n) if n else "")
            else:
                done, total = notes.day_progress(key)
                item.setData(COUNT_ROLE, f"{done} / {total}" if total else "")

    # ---- the cards

    def visible_notes(self) -> tuple[str, list[Note]]:
        notes = self.vault.notes
        query = self.search.text().strip()
        if query:
            hits = notes.search(query)
            return _("Search “{query}”: {n}").format(query=query, n=len(hits)), hits
        if self.nday == UNFINISHED:
            hits = notes.unfinished()
            return _("Unfinished: {n}").format(n=len(hits)), hits
        if self.nday == WITH_FILES:
            hits = notes.with_files()
            return _("With files: {n}").format(n=len(hits)), hits
        day = self.nday or today()
        return fmt_day(day) + (_("  · today") if day == today() else ""), notes.for_day(day)

    def across_days(self) -> bool:
        return self.nday in (UNFINISHED, WITH_FILES) or bool(self.search.text().strip())

    def render_notes(self, keep_scroll: bool = False) -> None:
        """Rebuild the cards (day change, search, deletion, order change)."""
        if self.vault is None:
            return
        top = self.area.verticalScrollBar().value()
        for card in self.cards.values():
            card.close_editor()
            card.hide()
            card.deleteLater()
        self.cards = {}
        title, shown = self.visible_notes()
        self.title.setText(title)
        self.shown = shown
        avail = self.avail_width()
        for note in shown:
            card = NoteCard(self, self.flow, note, self.card_width(note, avail))
            self.cards[note.id] = card
            card.show()
        self.place_cards()
        self.area.verticalScrollBar().setValue(top if keep_scroll else 0)

    def avail_width(self) -> int:
        w = self.flow.width()
        if w <= 1:
            w = max(CARD_MIN_WIDTH, self.area.viewport().width() - 48)
        return w

    def card_width(self, note: Note, avail: int) -> int:
        cols, col = column_width(avail, CARD_GAP, CARD_MIN_WIDTH)
        if note.width:
            w = note.width
        else:
            span = min(note.span, cols)
            w = round(col * span + CARD_GAP * (span - 1))
        return max(min(CARD_SMALLEST, avail), min(w, avail))

    def place_cards(self) -> None:
        shown = self.shown
        if not shown:
            self.empty.setText(hotkeys(_("Nothing here yet. “+ Note” or “+ Checklist” on the left; Ctrl+N also adds a note.")) if not self.search.text().strip() else _("Nothing found."))
            self.empty.show()
            self.flow.setFixedHeight(1)
            return
        self.empty.hide()
        avail = self.avail_width()
        cards = [(n, self.cards[n.id]) for n in shown if n.id in self.cards]
        sizes = []
        for note, card in cards:
            w = self.card_width(note, avail)
            card.set_width(w)
            h = max(note.height, card.min_h) if note.height else card.natural_h
            sizes.append((w, h))
        spots, total = flow_layout(sizes, avail, CARD_GAP)
        for (_n, card), (w, h), (x, y) in zip(cards, sizes, spots, strict=True):
            if card.geometry() != QRect(x, y, w, h):
                card.setGeometry(x, y, w, h)  # a new height of a dragged card relayouts in resizeEvent
        if self.flow.height() != total + CARD_GAP:
            self.flow.setFixedHeight(total + CARD_GAP)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.flow and event.type() == QEvent.Type.Resize and self.flow.width() != self._flow_width:
            self._flow_width = self.flow.width()
            self.place_cards()
        return False

    def focus_card_after(self, card: NoteCard, step: int) -> None:
        order = [self.cards[n.id] for n in self.shown if n.id in self.cards]
        try:
            i = order.index(card) + step
        except ValueError:
            return
        if 0 <= i < len(order):
            order[i].edit("title" if step > 0 else "add")
        else:
            card.setFocus()

    def refresh_card(self, note: Note) -> None:
        card = self.cards.get(note.id)
        if card is not None:
            card.relayout()
            card.update()
        self.place_cards()

    # ---- changes

    def note_changed(self, note: Note, refresh_days: bool = False) -> None:
        """An edit made in a card: the card shows it already, so the tab is not rebuilt on
        its next visit — unless the list is picked by content (Unfinished, With files, a search)."""
        note.touch()
        self.shell.mark_dirty("notes" if self.across_days() else None)
        if refresh_days:
            self.refresh_counts()

    def note_edit(self, note: Note, attr: str, value: str) -> None:
        if getattr(note, attr) == value:
            return
        setattr(note, attr, value)
        self.note_changed(note)

    def note_item_edit(self, note: Note, item: NoteItem, value: str) -> None:
        if item.text == value:
            return
        item.text = value
        self.note_changed(note)

    def _checklist_changed(self, card: NoteCard) -> None:
        card.relayout()
        card.update()
        self.place_cards()

    def note_add_item(self, card: NoteCard, note: Note, keep_focus: bool = True) -> None:
        item = note.add_item(card.add_text)
        if item is None:
            return
        card.add_text = ""
        if card.editing == ("add", None) and isinstance(card.editor, QLineEdit):
            card.editor.setText("")
        self._checklist_changed(card)
        self.note_changed(note, refresh_days=True)
        if keep_focus:
            card.edit("add")

    def commit_left_item(self, card: NoteCard) -> None:
        """The text left in “Add item” becomes an item once the user is elsewhere in the app."""
        if card.add_text.strip():
            self.note_add_item(card, card.note, keep_focus=False)

    def note_toggle(self, card: NoteCard, note: Note, item: NoteItem) -> None:
        note.toggle(item)
        if card.editing == ("item", item):
            card.close_editor()
        self._checklist_changed(card)
        self.note_changed(note, refresh_days=True)

    def note_remove_item(self, card: NoteCard, note: Note, item: NoteItem) -> None:
        note.remove_item(item)
        if card.editing == ("item", item):
            card.close_editor()
        self._checklist_changed(card)
        self.note_changed(note, refresh_days=True)

    def note_backspace_item(self, card: NoteCard, note: Note, item: NoteItem) -> None:
        """Backspace in an empty item removes it and goes to the end of the one above."""
        same = note.done_items() if item.done else note.open_items()
        idx = same.index(item) if item in same else 0
        card.close_editor()
        self.note_remove_item(card, note, item)
        same = note.done_items() if item.done else note.open_items()
        if idx > 0 and same:
            card.edit("item", same[idx - 1])
        else:
            card.edit("add")

    def note_move_item(self, card: NoteCard, note: Note, item: NoteItem, delta: int) -> None:
        if note.move_item(item, delta):
            card.close_editor()
            self._checklist_changed(card)
            self.note_changed(note)
            card.edit("item", item)

    def note_fold_done(self, card: NoteCard, note: Note) -> None:
        note.hide_done = not note.hide_done
        self._checklist_changed(card)
        self.note_changed(note)

    def note_clear_done(self, card: NoteCard, note: Note) -> None:
        removed = note.clear_done()
        if removed:
            self._checklist_changed(card)
            self.note_changed(note, refresh_days=True)
            self.shell.set_status(_("Done items removed: {n}").format(n=removed))

    def note_pin(self, note: Note) -> None:
        note.pinned = not note.pinned
        self.note_changed(note)
        self.render_notes(keep_scroll=True)

    # ---- menus

    def note_item_menu(self, card: NoteCard, note: Note, item: NoteItem, pos: QPoint) -> None:
        menu = QMenu(self)
        menu.addAction(_("Mark as open") if item.done else _("Mark as done")).triggered.connect(lambda: self.note_toggle(card, note, item))
        menu.addAction(_("Move up") + "\tAlt+↑").triggered.connect(lambda: self.note_move_item(card, note, item, -1))
        menu.addAction(_("Move down") + "\tAlt+↓").triggered.connect(lambda: self.note_move_item(card, note, item, +1))
        menu.addSeparator()
        menu.addAction(_("Delete item")).triggered.connect(lambda: self.note_remove_item(card, note, item))
        menu.popup(pos)
        self.last_menu = menu

    def note_menu(self, note: Note, pos: QPoint) -> None:
        """Card settings: width, folding, colour, files, carrying over, deletion."""
        card = self.cards.get(note.id)
        menu = QMenu(self)
        wide = QAction(_("Wide card"), menu)
        wide.setCheckable(True)
        wide.setChecked(note.span > 1)
        wide.triggered.connect(lambda on: self.note_set_wide(note, on))
        menu.addAction(wide)
        fold = QAction(_("Fold done items"), menu)
        fold.setCheckable(True)
        fold.setChecked(note.hide_done)
        fold.triggered.connect(lambda _on: self._fold(note))
        menu.addAction(fold)
        self._fill_color_menu(menu.addMenu(_("Colour")), note)
        menu.addSeparator()
        menu.addAction(_("Attach files…")).triggered.connect(lambda: self.note_attach(note))
        if note.done_items() and card is not None:
            menu.addAction(_("Clear done items")).triggered.connect(lambda: self.note_clear_done(card, note))
        if note.undone_items():
            menu.addAction(_("Copy undone items to today")).triggered.connect(lambda: self.note_carry_over(note))
        menu.addSeparator()
        menu.addAction(_("Delete note")).triggered.connect(lambda: self.note_delete(note))
        menu.popup(pos)
        self.last_menu = menu

    def _fold(self, note: Note) -> None:
        card = self.cards.get(note.id)
        if card is not None:
            self.note_fold_done(card, note)

    def _fill_color_menu(self, menu: QMenu, note: Note) -> None:
        for code, text, _bg, accent in NOTE_COLORS:
            swatch = QPixmap(QSize(14, 14))
            swatch.fill(QColor(accent if code else theme.current().LINK))
            act = menu.addAction(QIcon(swatch), _(text) + ("   ✓" if code == note.color else ""))
            act.triggered.connect(lambda _c=False, c=code: self.note_set_color(note, c))

    def note_color_menu(self, note: Note, pos: QPoint) -> None:
        menu = QMenu(self)
        self._fill_color_menu(menu, note)
        menu.popup(pos)
        self.last_menu = menu

    def note_set_color(self, note: Note, code: str) -> None:
        if note.color == code:
            return
        note.color = code
        self.note_changed(note)
        card = self.cards.get(note.id)
        if card is not None:
            card.close_editor()
        self.refresh_card(note)

    def note_set_wide(self, note: Note, wide: bool) -> None:
        if (note.span > 1) == wide and not note.width:
            return
        note.span = 2 if wide else 1
        note.width = 0
        self.note_changed(note)
        self.place_cards()

    def note_carry_over(self, note: Note) -> None:
        copy = self.vault.notes.carry_over(note)
        if copy is None:
            return
        self.shell.mark_dirty("notes")
        self.refresh_days(select=today())
        self.shell.set_status(_("Carried over to today: {n} item(s)").format(n=len(copy.items)))

    def note_delete(self, note: Note) -> None:
        if not note.is_empty() and not ask_yes_no(self.shell, _("Delete note “{title}”?").format(title=note.title or _("untitled"))):
            return
        self.vault.notes.remove(note)
        self.shell.mark_dirty("notes")
        self.refresh_days()

    def new_note(self, checklist: bool = False) -> NoteCard | None:
        if self.vault is None:
            return None
        date = self.nday if self.nday not in (None, UNFINISHED, WITH_FILES) else today()
        if self.search.text():
            self.search.blockSignals(True)
            self.search.setText("")
            self.search.blockSignals(False)
        note = self.vault.notes.add(date)
        self.shell.mark_dirty("notes")
        self.refresh_days(select=date)
        card = self.cards.get(note.id)
        if card is not None:
            card.edit("add" if checklist else "title")
        return card

    # ---- files

    def open_attachment(self, att: Attachment) -> None:
        from ..dialogs.fileview import FileViewer, open_with_system

        if not att.exists():
            show_warning(self.shell, _("File not found: {path}").format(path=att.path))
            return
        if att.is_text():
            FileViewer(self.shell, att.path).show()
        else:
            open_with_system(att.path)

    def file_menu(self, note: Note, att: Attachment, pos: QPoint) -> None:
        from ..dialogs.fileview import open_with_system, show_in_folder

        menu = QMenu(self)
        menu.addAction(_("Open")).triggered.connect(lambda: self.open_attachment(att))
        def system() -> None:
            if att.exists():
                open_with_system(att.path)

        def folder() -> None:
            if att.exists():
                show_in_folder(att.path)

        menu.addAction(_("Open in the system app")).triggered.connect(system)
        menu.addAction(_("Show in folder")).triggered.connect(folder)
        menu.addAction(_("Copy path")).triggered.connect(lambda: self.shell.copy_to_clipboard(att.path, secret=False))
        menu.addSeparator()
        menu.addAction(_("Detach")).triggered.connect(lambda: self.note_detach(note, att))
        menu.popup(pos)
        self.last_menu = menu

    def note_attach(self, note: Note, paths: list[str] | None = None) -> None:
        if paths is None:
            paths, _filter = QFileDialog.getOpenFileNames(self.shell, _("Attach files to the note"), "", _("Markdown and text") + " (*.md *.markdown *.txt);;" + _("All files") + " (*.*)")
        added = sum(1 for p in paths if note.attach(p) is not None)
        if not added:
            return
        self.note_changed(note)
        self.refresh_card(note)
        self.refresh_counts()
        self.shell.set_status(_("Files attached: {n}").format(n=added))

    def note_detach(self, note: Note, att: Attachment) -> None:
        note.detach(att)
        self.note_changed(note)
        self.refresh_card(note)
        self.refresh_counts()

