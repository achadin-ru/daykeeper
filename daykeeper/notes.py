"""Notes: cards by day — a title, free text, a checklist and attached files.

Stored inside the encrypted vault (key `notes`). The day of a note is the day it belongs
to (the creation day by default); unfinished items can be carried over to today as a new
card. Attached files are links to files on disk (the vault keeps the path, not the
content), so a Markdown file opened from a note is always its current version."""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import palette
from .journal import today

# Card colours: (code, UI label key, background, accent). The code is stored in the note;
# the accent colours the stripe on the left edge of the card and its progress bar.
NOTE_COLORS: list[tuple[str, str, str, str]] = [
    ("", "White", "#FFFFFF", palette.LINK),  # a plain card takes the app's accent (it is text too: "+", "to today")
    ("yellow", "Yellow", "#FFF7C2", "#CA8A04"),
    ("orange", "Orange", "#FFEDD5", "#EA580C"),
    ("green", "Green", "#E3F6E4", "#16A34A"),
    ("teal", "Teal", "#D5F5F0", "#0D9488"),
    ("blue", "Blue", "#DDEEFB", "#2563EB"),
    ("purple", "Purple", "#EDE7FB", "#7C3AED"),
    ("pink", "Pink", "#FCE3EC", "#DB2777"),
    ("grey", "Grey", "#EEF0F3", "#4B5563"),
]
if palette.theme == "dark":  # a light pastel would glare: a thin tint of the accent over the card
    NOTE_COLORS = [(code, label, palette.CARD if not code else palette.mix(accent, palette.CARD, 0.16), accent) for code, label, _bg, accent in NOTE_COLORS]
COLOR_BG = {code: bg for code, _label, bg, _accent in NOTE_COLORS}
COLOR_ACCENT = {code: accent for code, _label, _bg, accent in NOTE_COLORS}

# Files the built-in viewer opens; anything else opens in the system app.
TEXT_SUFFIXES = {".md", ".markdown", ".mdown", ".txt", ".log"}


def _new_id() -> str:
    return secrets.token_hex(6)


@dataclass
class NoteItem:
    text: str
    done: bool = False

    def to_dict(self) -> dict:
        return {"text": self.text, "done": self.done}

    @classmethod
    def from_dict(cls, d: dict) -> NoteItem:
        return cls(str(d.get("text", "")), bool(d.get("done", False)))


@dataclass
class Attachment:
    """A file attached to a note: its absolute path and when it was attached."""

    path: str
    added: float = field(default_factory=time.time)

    @property
    def name(self) -> str:
        return Path(self.path).name or self.path

    @property
    def suffix(self) -> str:
        return Path(self.path).suffix.lower()

    def exists(self) -> bool:
        return os.path.isfile(self.path)

    def is_text(self) -> bool:
        return self.suffix in TEXT_SUFFIXES

    def to_dict(self) -> dict:
        return {"path": self.path, "added": self.added}

    @classmethod
    def from_dict(cls, d: dict) -> Attachment:
        return cls(str(d.get("path", "")), float(d.get("added", time.time())))


MAX_SIZE = 4000  # the widest and tallest a card can be dragged, in pixels
MAX_SPAN = 4  # the most grid columns a card takes by the “Wide card” switch


@dataclass
class Note:
    id: str = field(default_factory=_new_id)
    date: str = field(default_factory=today)
    title: str = ""
    text: str = ""
    items: list[NoteItem] = field(default_factory=list)
    pinned: bool = False
    color: str = ""
    span: int = 1  # how many columns of the grid the card takes (the “Wide card” switch)
    width: int = 0  # the width dragged by the right edge, in 96-dpi pixels; 0 — by the columns
    height: int = 0  # the height dragged by the bottom edge, in 96-dpi pixels; 0 — by the content
    hide_done: bool = False  # done items collapsed under “Done (n)”
    attachments: list[Attachment] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    @property
    def wide(self) -> bool:
        return self.span > 1

    @wide.setter
    def wide(self, value: bool) -> None:
        self.span = max(self.span, 2) if value else 1

    # ---- checklist

    def add_item(self, text: str, done: bool = False) -> NoteItem | None:
        text = text.strip()
        if not text:
            return None
        item = NoteItem(text, done)
        self.items.append(item)
        self.touch()
        return item

    def remove_item(self, item: NoteItem) -> None:
        if item in self.items:
            self.items.remove(item)
            self.touch()

    def toggle(self, item: NoteItem, done: bool | None = None) -> None:
        item.done = (not item.done) if done is None else done
        self.touch()

    def move_item(self, item: NoteItem, delta: int) -> bool:
        """Shift an item among the items of the same state (open or done). False — at the edge."""
        same = [i for i in self.items if i.done == item.done]
        if item not in same:
            return False
        target = same.index(item) + delta
        if not 0 <= target < len(same):
            return False
        a, b = self.items.index(item), self.items.index(same[target])
        self.items[a], self.items[b] = self.items[b], self.items[a]
        self.touch()
        return True

    def clear_done(self) -> int:
        """Remove done items; returns how many were removed."""
        before = len(self.items)
        self.items = [i for i in self.items if not i.done]
        if len(self.items) != before:
            self.touch()
        return before - len(self.items)

    def open_items(self) -> list[NoteItem]:
        """Items still to do, in their order; done items are shown after them."""
        return [i for i in self.items if not i.done]

    def done_items(self) -> list[NoteItem]:
        return [i for i in self.items if i.done]

    # ---- files

    def attach(self, path: str | os.PathLike) -> Attachment | None:
        """Attach a file by its absolute path; the same file twice — None."""
        full = os.path.abspath(os.fspath(path))
        if any(os.path.normcase(a.path) == os.path.normcase(full) for a in self.attachments):
            return None
        att = Attachment(full)
        self.attachments.append(att)
        self.touch()
        return att

    def detach(self, att: Attachment) -> None:
        if att in self.attachments:
            self.attachments.remove(att)
            self.touch()

    def touch(self) -> None:
        self.updated = time.time()

    # ---- state

    def progress(self) -> tuple[int, int]:
        """(done, total) over checklist items."""
        return sum(1 for i in self.items if i.done), len(self.items)

    def is_done(self) -> bool:
        return bool(self.items) and all(i.done for i in self.items)

    def is_empty(self) -> bool:
        return not (self.title.strip() or self.text.strip() or self.items or self.attachments)

    def undone_items(self) -> list[NoteItem]:
        return self.open_items()

    def matches(self, query: str) -> bool:
        q = query.strip().lower()
        if not q:
            return True
        hay = [self.title, self.text, *(i.text for i in self.items), *(a.name for a in self.attachments)]
        return any(q in h.lower() for h in hay)

    def bg(self) -> str:
        return COLOR_BG.get(self.color, COLOR_BG[""])

    def accent(self) -> str:
        return COLOR_ACCENT.get(self.color, COLOR_ACCENT[""])

    # ---- serialisation

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "date": self.date,
            "title": self.title,
            "text": self.text,
            "items": [i.to_dict() for i in self.items],
            "pinned": self.pinned,
            "color": self.color,
            "span": self.span,
            "wide": self.wide,  # older versions read only this
            "width": self.width,
            "height": self.height,
            "hide_done": self.hide_done,
            "attachments": [a.to_dict() for a in self.attachments],
            "created": self.created,
            "updated": self.updated,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Note:
        color = str(d.get("color", ""))
        return cls(
            id=str(d.get("id") or _new_id()),
            date=str(d.get("date") or today()),
            title=str(d.get("title", "")),
            text=str(d.get("text", "")),
            items=[NoteItem.from_dict(i) for i in d.get("items", []) if isinstance(i, dict)],
            pinned=bool(d.get("pinned", False)),
            color=color if color in COLOR_BG else "",
            span=max(1, min(MAX_SPAN, int(d.get("span") or (2 if d.get("wide") else 1)))),
            width=max(0, min(MAX_SIZE, int(d.get("width", 0) or 0))),
            height=max(0, min(MAX_SIZE, int(d.get("height", 0) or 0))),
            hide_done=bool(d.get("hide_done", False)),
            attachments=[Attachment.from_dict(a) for a in d.get("attachments", []) if isinstance(a, dict) and a.get("path")],
            created=float(d.get("created", time.time())),
            updated=float(d.get("updated", time.time())),
        )


class Notes:
    """All notes of the vault."""

    def __init__(self, notes: list[Note] | None = None):
        self.notes: list[Note] = list(notes or [])

    # ---- access

    def get(self, note_id: str) -> Note | None:
        return next((n for n in self.notes if n.id == note_id), None)

    def add(self, date: str | None = None, **kw) -> Note:
        note = Note(date=date or today(), **kw)
        self.notes.append(note)
        return note

    def remove(self, note: Note) -> None:
        if note in self.notes:
            self.notes.remove(note)

    def dates(self) -> list[str]:
        """Days that have notes, newest first."""
        return sorted({n.date for n in self.notes}, reverse=True)

    def for_day(self, date: str) -> list[Note]:
        return self.sort([n for n in self.notes if n.date == date])

    def search(self, query: str) -> list[Note]:
        """Across all days: title, text, items, file names. Newest days first."""
        return self._sort_across_days([n for n in self.notes if n.matches(query)])

    def _sort_across_days(self, notes: list[Note]) -> list[Note]:
        out: list[Note] = []
        for date in sorted({n.date for n in notes}, reverse=True):
            out.extend(self.sort([n for n in notes if n.date == date]))
        return out

    def unfinished(self) -> list[Note]:
        """Notes with at least one undone item, across all days."""
        return self._sort_across_days([n for n in self.notes if n.undone_items()])

    def with_files(self) -> list[Note]:
        """Notes with attached files, across all days."""
        return self._sort_across_days([n for n in self.notes if n.attachments])

    @staticmethod
    def sort(notes: list[Note]) -> list[Note]:
        """Within a day: pinned first, then unfinished, done last; older before newer."""
        return sorted(notes, key=lambda n: (not n.pinned, n.is_done(), n.created))

    def day_progress(self, date: str) -> tuple[int, int]:
        """(done, total) over all items of the day."""
        done = total = 0
        for n in self.for_day(date):
            d, t = n.progress()
            done += d
            total += t
        return done, total

    # ---- actions

    def carry_over(self, note: Note, date: str | None = None) -> Note | None:
        """Undone items of a note → a new card on the given day (today by default), with the
        same colour, width and files. The source note is left untouched. Nothing to carry → None."""
        undone = note.undone_items()
        if not undone:
            return None
        target = date or today()
        copy = self.add(target, title=note.title, color=note.color, span=note.span, width=note.width, height=note.height)
        copy.attachments = [Attachment(a.path) for a in note.attachments]
        for item in undone:
            copy.add_item(item.text)
        return copy

    def drop_empty(self) -> int:
        """Remove empty cards (no title, text, items or files)."""
        before = len(self.notes)
        self.notes = [n for n in self.notes if not n.is_empty()]
        return before - len(self.notes)

    # ---- serialisation

    def to_list(self) -> list[dict]:
        return [n.to_dict() for n in self.notes]

    @classmethod
    def from_list(cls, rows: list) -> Notes:
        return cls([Note.from_dict(r) for r in rows if isinstance(r, dict)])
