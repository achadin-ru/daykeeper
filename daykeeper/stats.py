"""The journal over a period: a week, a month, a quarter or any dates. Counts per column and
per activity kind of the journal layout in use, a total per day for the heat map, a row
per week, the most frequent notes, and the same as a Markdown table for a letter.

Nothing here knows the names of the columns: everything goes through the layout, so a
renamed column or one the user added is counted as it is. No tkinter here."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from .i18n import _
from .journal import Journal
from .journal_layout import ROLE_NOTE, Layout

KINDS = ("week", "last_week", "month", "last_month", "quarter")
KIND_LABELS = {  # kind → UI key, in the order of the period menu
    "week": "This week",
    "last_week": "Last week",
    "month": "This month",
    "last_month": "Last month",
    "quarter": "This quarter",
}


@dataclass(frozen=True)
class Period:
    start: str  # YYYY-MM-DD, inclusive
    end: str  # YYYY-MM-DD, inclusive

    def dates(self) -> list[str]:
        """Every date of the period, oldest first."""
        first, last = date.fromisoformat(self.start), date.fromisoformat(self.end)
        return [(first + timedelta(days=n)).isoformat() for n in range((last - first).days + 1)]

    def __contains__(self, day: object) -> bool:
        return isinstance(day, str) and self.start <= day <= self.end


def _month_end(day: date) -> date:
    following = day.replace(day=28) + timedelta(days=4)
    return following - timedelta(days=following.day)


def period(kind: str, today: date) -> Period:
    """A named period around `today`: weeks run Monday to Sunday, months and quarters are
    calendar ones."""
    if kind in ("week", "last_week"):
        monday = today - timedelta(days=today.weekday())
        if kind == "last_week":
            monday -= timedelta(days=7)
        return Period(monday.isoformat(), (monday + timedelta(days=6)).isoformat())
    if kind in ("month", "last_month"):
        first = today.replace(day=1)
        if kind == "last_month":
            first = (first - timedelta(days=1)).replace(day=1)
        return Period(first.isoformat(), _month_end(first).isoformat())
    if kind == "quarter":
        first = today.replace(month=(today.month - 1) // 3 * 3 + 1, day=1)
        last = _month_end(first.replace(month=first.month + 2))
        return Period(first.isoformat(), last.isoformat())
    raise ValueError(f"Period must be one of {' | '.join(KINDS)}, not {kind!r}")


def custom(start: date | str, end: date | str) -> Period:
    """Own dates; given in the wrong order they are swapped. A bad date raises ValueError."""
    first = start if isinstance(start, date) else date.fromisoformat(str(start).strip())
    last = end if isinstance(end, date) else date.fromisoformat(str(end).strip())
    if last < first:
        first, last = last, first
    return Period(first.isoformat(), last.isoformat())


@dataclass
class Week:
    """One row of the table by weeks; a week cut by the period counts only its own days."""

    start: str  # the Monday, even when the period starts later
    columns: dict[str, int] = field(default_factory=dict)  # column id → items
    kinds: dict[str, int] = field(default_factory=dict)  # kind id → items

    @property
    def total(self) -> int:
        return sum(self.columns.values()) + sum(self.kinds.values())


@dataclass
class Summary:
    period: Period
    columns: dict[str, int]  # visible column id → items, in the layout's order
    kinds: dict[str, int]  # visible kind id → items, in the layout's order
    per_day: dict[str, int]  # every date of the period → items of the day (0 included)
    per_week: list[Week]
    notes: list[tuple[str, int]]  # texts of the note kinds, most frequent first
    days_with_entries: int

    @property
    def total(self) -> int:
        return sum(self.per_day.values())


def _monday(iso: str) -> str:
    day = date.fromisoformat(iso)
    return (day - timedelta(days=day.weekday())).isoformat()


def summarize(journal: Journal, span: Period, layout: Layout) -> Summary:
    """Count what the visible columns and kinds of `layout` hold on the days of `span`.
    Items of hidden kinds, or of kinds the layout does not have, are not counted (the same
    as the daily report shows them)."""
    columns = {c.id: 0 for c in layout.visible_columns()}
    kinds = {k.id: 0 for k in layout.visible_kinds()}
    note_kinds = {k.id for k in layout.visible_kinds() if k.role == ROLE_NOTE}
    per_day = dict.fromkeys(span.dates(), 0)
    weeks: dict[str, Week] = {}
    for iso in per_day:
        weeks.setdefault(_monday(iso), Week(_monday(iso), dict.fromkeys(columns, 0), dict.fromkeys(kinds, 0)))
    notes: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    days_with_entries = 0
    for day_date in sorted(d for d in journal.days if d in span):
        day = journal.days[day_date]
        week = weeks[_monday(day_date)]
        count = 0
        for cid in columns:
            n = len(day.columns.get(cid, []))
            columns[cid] += n
            week.columns[cid] += n
            count += n
        for act in day.activities:
            kid = act.get("kind", "")
            if kid not in kinds:
                continue
            kinds[kid] += 1
            week.kinds[kid] += 1
            count += 1
            if kid in note_kinds:
                text = str(act.get("text", "")).strip()
                notes[text] += 1
                first_seen.setdefault(text, len(first_seen))
        per_day[day_date] = count
        days_with_entries += count > 0
    ranked = sorted(notes.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))
    return Summary(span, columns, kinds, per_day, [weeks[k] for k in sorted(weeks)], ranked, days_with_entries)


def heat_levels(per_day: dict[str, int]) -> dict[str, int]:
    """0..4 per date, as the cells of a contribution graph: 0 — nothing; otherwise the share
    of the busy days whose total is not above this one, in quarters. Equal totals get the
    same level, and the busiest day is always 4."""
    busy = sorted(v for v in per_day.values() if v > 0)
    levels: dict[str, int] = {}
    for day, value in per_day.items():
        if value <= 0:
            levels[day] = 0
            continue
        not_above = sum(1 for v in busy if v <= value)
        levels[day] = max(1, math.ceil(4 * not_above / len(busy)))
    return levels


def _dmy(day: str) -> str:
    return date.fromisoformat(day).strftime("%d.%m.%Y")


def _cell(text: str) -> str:
    """A table cell: a pipe would split it, a line break would end the row."""
    return " ".join(text.replace("|", "\\|").split())


def as_markdown(summary: Summary, layout: Layout, top_notes: int = 10) -> str:
    """The summary as Markdown: totals, a table by weeks when there is more than one, and
    the most frequent notes. Labels are the layout's own, translated when they are UI keys."""
    labels = [(c.id, "column", _(c.label)) for c in layout.visible_columns() if c.id in summary.columns]
    labels += [(k.id, "kind", _(k.label)) for k in layout.visible_kinds() if k.id in summary.kinds]
    out = [f"## {_('Report for {start} – {end}').format(start=_dmy(summary.period.start), end=_dmy(summary.period.end))}", ""]
    out += [f"| {_('What was done')} | {_('Number')} |", "|---|---:|"]
    for ident, where, label in labels:
        value = summary.columns[ident] if where == "column" else summary.kinds[ident]
        out.append(f"| {_cell(label)} | {value} |")
    out += ["", f"{_('Days with entries')}: {summary.days_with_entries}"]
    if len(summary.per_week) > 1:
        out += ["", f"| {_('Week')} | " + " | ".join(_cell(label) for _i, _w, label in labels) + f" | {_('Total')} |"]
        out.append("|---|" + "---:|" * (len(labels) + 1))
        for week in summary.per_week:
            values = [week.columns.get(i, 0) if w == "column" else week.kinds.get(i, 0) for i, w, _l in labels]
            out.append(f"| {_dmy(week.start)} | " + " | ".join(str(v) for v in values) + f" | {week.total} |")
    if summary.notes and top_notes > 0:
        out += ["", f"### {_('Frequent notes')}", ""]
        out += [f"- {text} — {n}" if n > 1 else f"- {text}" for text, n in summary.notes[:top_notes]]
    return "\n".join(out)
