"""What the journal is made of: the columns of a day (what was verified, failed, filed — or
whatever the user's work is), the kinds of activity, and the shape of the report. Ready-made
layouts for a tester, a developer, an analyst or manager, and a plain diary; any of them can
be edited in the settings.

Stored in settings.json next to the vault (key `journal`), so the app, the console helper
and the hook read the same layout. Labels of the ready-made layouts are UI keys (translated
on display); a label typed by the user is shown as it is. No tkinter here."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields, replace

from .i18n import _, variants

ROLE_MR, ROLE_TESTS, ROLE_NOTE, ROLE_PLAIN = "mr", "tests", "note", "plain"
ROLES = (ROLE_MR, ROLE_TESTS, ROLE_PLAIN, ROLE_NOTE)
ROLE_LABELS = {
    ROLE_MR: "MR numbers",
    ROLE_TESTS: "Test cases",
    ROLE_PLAIN: "A list",
    ROLE_NOTE: "Notes into line 1",
}
ROLE_HINTS = {
    ROLE_MR: "Items are MRs: !42 or a link; the report lists them on one line.",
    ROLE_TESTS: "All kinds of this role share one report line, “New tests: …”.",
    ROLE_PLAIN: "Any items; the report lists them on one line.",
    ROLE_NOTE: "Each item is a short phrase that goes into line 1 of the report.",
}


@dataclass
class Column:
    """A list of the day: issue keys or any items, one per line."""

    id: str
    label: str
    command: str = ""  # the word for `daykeeper-cli <command> …`; the id when empty
    phrase: str = ""  # what line 1 of the report says when the column has items
    phrase_alone: bool = False  # say `phrase` only when nothing else is said
    button: str = ""  # the quick-add button text; the label when empty
    hidden: bool = False

    @property
    def word(self) -> str:
        return self.command or self.id


@dataclass
class Kind:
    """A kind of activity: MR reviews, test cases, meetings, notes…"""

    id: str
    label: str
    command: str = ""
    phrase: str = ""
    role: str = ROLE_PLAIN
    hidden: bool = False

    @property
    def word(self) -> str:
        return self.command or self.id


def _as_type(kind, name: str, value):
    """A value from settings.json as the field `name` of `kind` wants it: a flag or a text."""
    default = next(f.default for f in fields(kind) if f.name == name)
    if isinstance(default, bool):
        return bool(value)
    return "" if value is None else str(value).strip()


@dataclass
class Layout:
    preset: str = "qa"
    columns: list[Column] = field(default_factory=list)
    kinds: list[Kind] = field(default_factory=list)
    mr_short: bool = True  # in the report: !42 / repo!17, or the full link
    show_plans: bool = True  # "2." in the editor and the report
    show_other: bool = True  # "3." in the editor and the report

    # ---- lookups

    def visible_columns(self) -> list[Column]:
        return [c for c in self.columns if not c.hidden]

    def visible_kinds(self) -> list[Kind]:
        return [k for k in self.kinds if not k.hidden]

    def column(self, cid: str) -> Column | None:
        return next((c for c in self.columns if c.id == cid), None)

    def kind(self, kid: str) -> Kind | None:
        return next((k for k in self.kinds if k.id == kid), None)

    def role(self, kid: str) -> str:
        k = self.kind(kid)
        return k.role if k else ROLE_PLAIN

    def note_kind(self) -> str:
        """Where a line without a known label goes: the first note kind, else the last kind."""
        notes = [k.id for k in self.kinds if k.role == ROLE_NOTE]
        return notes[0] if notes else (self.kinds[-1].id if self.kinds else "note")

    def statuses(self, visible_only: bool = True) -> list[tuple[str, str]]:
        """(command word, label) of every column and kind: what `daykeeper-cli` accepts."""
        cols = self.visible_columns() if visible_only else self.columns
        kinds = self.visible_kinds() if visible_only else self.kinds
        return [(c.word, c.label) for c in cols] + [(k.word, k.label) for k in kinds]

    def resolve(self, status: str) -> tuple[str, str] | None:
        """A command word (or an id, or an old alias) → ("column", id) or ("activity", id)."""
        st = status.strip().lower()
        for c in self.columns:
            if st in (c.word.lower(), c.id.lower()) or st in ALIASES.get(c.id, ()):
                return "column", c.id
        for k in self.kinds:
            if st in (k.word.lower(), k.id.lower(), k.id.replace("_", "-")) or st in ALIASES.get(k.id, ()):
                return "activity", k.id
        return None

    def kind_for_label(self, label: str) -> str | None:
        """An editor line head (“MR reviews”, in any UI language, or a typed label) → kind id."""
        wanted = label.strip().lower()
        for k in self.kinds:
            names = {v.lower() for v in variants(k.label)} | {k.label.lower(), _(k.label).lower()}
            if wanted in names:
                return k.id
        return None

    # ---- storage

    def to_settings(self) -> dict:
        return {"journal": {
            "preset": self.preset,
            "columns": [asdict(c) for c in self.columns],
            "kinds": [asdict(k) for k in self.kinds],
            "mr_short": self.mr_short,
            "show_plans": self.show_plans,
            "show_other": self.show_other,
        }}

    @classmethod
    def from_settings(cls, settings: dict) -> Layout:
        raw = settings.get("journal") if isinstance(settings.get("journal"), dict) else None
        if not raw:
            return preset("qa")

        def build(kind, rows) -> list:
            known = {f.name for f in fields(kind)}
            out, seen = [], set()
            for row in rows or []:
                if not isinstance(row, dict) or not str(row.get("id", "")).strip() or not str(row.get("label", "")).strip():
                    continue
                # settings.json may be edited by hand: a number where a word belongs would
                # break the Journal tab on unlock, so every field is brought to its type
                item = kind(**{k: _as_type(kind, k, v) for k, v in row.items() if k in known})
                if item.id in seen:
                    continue
                seen.add(item.id)
                out.append(item)
            return out

        kinds = build(Kind, raw.get("kinds"))
        for k in kinds:
            k.role = k.role if k.role in ROLES else ROLE_PLAIN
        layout = cls(
            preset=str(raw.get("preset") or "custom"),
            columns=build(Column, raw.get("columns")),
            kinds=kinds,
            mr_short=bool(raw.get("mr_short", True)),
            show_plans=bool(raw.get("show_plans", True)),
            show_other=bool(raw.get("show_other", True)),
        )
        return layout if layout.columns or layout.kinds else preset("qa")


# The tester's words of the first version: the inbox and scripts keep working with them.
ALIASES: dict[str, tuple[str, ...]] = {
    "ok": ("pass", "ok"),
    "failed": ("fail", "failed"),
    "bugs": ("bug", "bugs", "defect"),
    "review": ("review",),
    "mr_open": ("mr-open", "mr_open"),
    "mr_merged": ("mr-merged", "mr_merged"),
}


# ---- ready-made layouts

PRESETS: dict[str, str] = {  # id → title (UI key)
    "qa": "Tester",
    "dev": "Developer",
    "pm": "Analyst or manager",
    "simple": "Plain diary",
}
PRESET_HINTS = {
    "qa": "Verified, failed and filed bugs; MR reviews, opened and merged MRs, test cases.",
    "dev": "Tasks done and in progress, bugs fixed; MRs opened and merged, code review.",
    "pm": "Tasks, documents, decisions; meetings and notes.",
    "simple": "What was done and what is in the way; notes.",
}


def _note() -> Kind:
    return Kind("note", "Miscellaneous", command="note", role=ROLE_NOTE)


def preset(pid: str) -> Layout:
    if pid == "dev":
        return Layout("dev", [
            Column("done", "Tasks done", command="done", phrase="development"),
            Column("progress", "In progress", command="progress", phrase="development"),
            Column("fixed", "Bugs fixed", command="fixed", phrase="bug fixes"),
        ], [
            Kind("mr_open", "MRs opened", command="mr-open", role=ROLE_MR),
            Kind("mr_merged", "MRs merged", command="mr-merged", role=ROLE_MR),
            Kind("review", "MR reviews", command="review", phrase="code review", role=ROLE_MR),
            _note(),
        ])
    if pid == "pm":
        return Layout("pm", [
            Column("tasks", "Tasks", command="task", phrase="tasks"),
            Column("docs", "Documents", command="doc", phrase="documents"),
            Column("decisions", "Decisions", command="decision", phrase="decisions"),
        ], [
            Kind("meetings", "Meetings", command="meeting", phrase="meetings"),
            _note(),
        ], mr_short=True)
    if pid == "simple":
        return Layout("simple", [
            Column("done", "Done", command="done"),
            Column("blockers", "What is in the way", command="blocker"),
        ], [_note()], show_other=False)
    return Layout("qa", [
        Column("ok", "Tested successfully", command="pass", phrase="tested tasks", button="✓ Passed"),
        Column("failed", "Test failed", command="fail", phrase="tested tasks", button="✗ Failed"),
        Column("bugs", "New bug", command="bug", phrase="bugs filed", phrase_alone=True, button="🐞 Bug"),
    ], [
        Kind("review", "MR reviews", command="review", phrase="reviewed MRs", role=ROLE_MR),
        Kind("mr_open", "MRs opened", command="mr-open", role=ROLE_MR),
        Kind("mr_merged", "MRs merged", command="mr-merged", role=ROLE_MR),
        Kind("manual", "Manual cases", command="manual", phrase="new manual/autotests", role=ROLE_TESTS),
        Kind("auto", "Autotests", command="auto", phrase="new manual/autotests", role=ROLE_TESTS),
        _note(),
    ])


def resolve_known(status: str, layout: Layout | None = None) -> tuple[str, str] | None:
    """Like Layout.resolve, but a word of any ready-made layout counts too: with another
    layout on for a while, `pass` or `manual` from a script or the hook is still kept (under
    its own id, hidden) and shows again when its layout is back."""
    found = (layout or current()).resolve(status)
    if found is not None:
        return found
    for pid in PRESETS:
        found = preset(pid).resolve(status)
        if found is not None:
            return found
    return None


def role_of(kid: str) -> str:
    """The role of a kind in the layout in use, else in the ready-made layout that has it."""
    for layout in (current(), *(preset(pid) for pid in PRESETS)):
        kind = layout.kind(kid)
        if kind is not None:
            return kind.role
    return ROLE_PLAIN


def known_kind(kid: str, layout: Layout | None = None) -> bool:
    """A kind of the layout in use or of any ready-made one."""
    return (layout or current()).kind(kid) is not None or any(preset(pid).kind(kid) is not None for pid in PRESETS)


# ---- names for new columns and kinds

_TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"],
    strict=True,
))


def slug(label: str) -> str:
    """A command word from a label: «Созвоны» → sozvony, “Code review” → code-review."""
    text = "".join(_TRANSLIT.get(ch, ch) for ch in label.strip().lower())
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:24]


def new_id(label: str, taken: set[str]) -> str:
    base = slug(label) or "item"
    word, n = base, 2
    while word in taken:
        word, n = f"{base}-{n}", n + 1
    return word


def taken_words(layout: Layout) -> set[str]:
    words = {c.id for c in layout.columns} | {k.id for k in layout.kinds}
    words |= {c.word for c in layout.columns} | {k.word for k in layout.kinds}
    for aliases in ALIASES.values():
        words |= set(aliases)
    return words | {"hook", "--data", "activity"}  # the helper's own words and the inbox's activity marker


# ---- the layout in use

_layout = preset("qa")


def configure(layout: Layout) -> None:
    global _layout
    _layout = layout


def current() -> Layout:
    return _layout


def copy(layout: Layout) -> Layout:
    return replace(layout, columns=[replace(c) for c in layout.columns], kinds=[replace(k) for k in layout.kinds])
