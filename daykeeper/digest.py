"""The morning digest: what the day starts with, gathered from the vault — the plans of the
journal, open note items, MRs waiting for my review, keys that run out this week, what
happened at night (watched pipelines, failed routines) and the unread notifications of the
night. Sections come in a fixed order; an empty one is left out, and with nothing at all
the digest is not shown.

Section titles are UI keys (`SECTION_TITLES`, translated on display); line texts are built
in the UI language. Also the once-a-day rule of the settings key `digest`
({"on_unlock": true, "shown": "2026-10-17"}). Pure (no tkinter, no network)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from . import expiry, journal_layout
from .gitlab import Event
from .i18n import _

TAB_JOURNAL, TAB_NOTES, TAB_INTEGRATIONS, TAB_VAULT = "journal", "notes", "integrations", "vault"

PLANS = "Plans for today"
NOTES = "Open note items"
REVIEWS = "MRs waiting for my review"
EXPIRING = "Expiring this week"
NIGHT = "Night"
FEED = "Unread notifications of the night"
SECTION_TITLES = (PLANS, NOTES, REVIEWS, EXPIRING, NIGHT, FEED)  # the fixed order

PLANS_BACK_DAYS = 4  # how far back "yesterday" reaches for plans: a Monday finds Friday's
EVENING_HOUR = 18  # the night starts at this hour of the day before
ROUTINE_WINDOW = 86400.0  # a failed routine counts when it ran within this many seconds
PIPELINE_KINDS = ("watch_failed", "watch_fixed")  # feed events the night section already shows
BULLETS = "•-*–—·☐ \t"


@dataclass
class Line:
    text: str
    url: str = ""  # a link to open
    tab: str = ""  # a tab to show instead: "journal", "notes", "integrations", "vault"
    ok: bool | None = None  # True — went well (✓), False — went wrong (✗), None — neither
    ref: str = ""  # what the line is about inside its tab: an entry id (vault), an MR key (reviews)


@dataclass
class Section:
    title: str  # UI key, one of SECTION_TITLES
    lines: list[Line]
    count: int = 0  # how many there are in all; the lines show the first `max_lines`
    tab: str = ""  # the tab the section's button opens; "" — no button
    detail: str = ""  # a short note next to the title ("3 cards · 7 items")

    @property
    def more(self) -> int:
        """How many are not among the lines."""
        return max(0, self.count - len(self.lines))


def _section(title: str, lines: list[Line], max_lines: int, tab: str = "", detail: str = "", count: int | None = None) -> Section | None:
    if not lines:
        return None
    return Section(title, lines[:max_lines], len(lines) if count is None else count, tab, detail)


# ---------------------------------------------------------------- the sources


def _plan_lines(text: str) -> list[str]:
    """The plans field → one item a line, list bullets taken off."""
    return [s for s in (line.strip().lstrip(BULLETS).strip() for line in text.splitlines()) if s]


def plans_text(journal, today: str, yesterday_plans: bool = True) -> tuple[str, str]:
    """(date, plans) of the day whose plans are for today: today's when written, else the
    newest of the last PLANS_BACK_DAYS days before it ("" , "" — none). The layout may hide
    the plans altogether."""
    if not journal_layout.current().show_plans:
        return "", ""
    day = journal.get(today)
    if day is not None and day.plans.strip():
        return today, day.plans
    if not yesterday_plans:
        return "", ""
    start = date.fromisoformat(today)
    for back in range(1, PLANS_BACK_DAYS + 1):
        when = (start - timedelta(days=back)).isoformat()
        day = journal.get(when)
        if day is not None and day.plans.strip():
            return when, day.plans
    return "", ""


def _plans(vault, today: str, yesterday_plans: bool, max_lines: int) -> Section | None:
    _when, text = plans_text(vault.journal, today, yesterday_plans)
    return _section(PLANS, [Line(s, tab=TAB_JOURNAL) for s in _plan_lines(text)], max_lines, TAB_JOURNAL)


def _notes(vault, max_lines: int) -> Section | None:
    cards = vault.notes.unfinished()
    items = [(n, i) for n in cards for i in n.open_items()]
    lines = [Line(i.text.strip(), tab=TAB_NOTES) for _n, i in items if i.text.strip()]
    detail = _("{cards} cards · {items} items").format(cards=len(cards), items=len(items)) if cards else ""
    return _section(NOTES, lines, max_lines, TAB_NOTES, detail)


def mr_short(key: str) -> str:
    """“group/repo!12” → “repo!12”; any other text as is."""
    project, bang, iid = key.rpartition("!")
    return f"{project.rsplit('/', 1)[-1]}!{iid}" if bang and project else key


def _reviews(vault, max_lines: int) -> Section | None:
    """Open MRs of the last snapshot where I am a reviewer and not the author."""
    lines = []
    for key, facts in sorted(vault.integrations.gitlab_state.items()):
        if not isinstance(facts, dict) or not facts.get("reviewer") or facts.get("role") == "author":
            continue
        text = f"{mr_short(key)} {facts.get('title', '')}".strip()
        if facts.get("author"):
            text += f" — @{facts['author']}"
        url = str(facts.get("url") or "")
        lines.append(Line(text, url=url, tab="" if url else TAB_INTEGRATIONS, ref=key))
    return _section(REVIEWS, lines, max_lines, TAB_INTEGRATIONS)


def _when_left(days_left: int) -> str:
    if days_left < 0:
        return _("expired {n} d ago").format(n=-days_left)
    if days_left == 0:
        return _("runs out today")
    return _("in {n} d").format(n=days_left)


def _expiring(vault, today: str, days: int, seen: dict[str, str], max_lines: int) -> Section | None:
    found = expiry.expiring(vault, date.fromisoformat(today), days, seen)
    lines = [Line(f"{x.entry_name} · {_(x.field)} — {_when_left(x.days_left)}", tab=TAB_VAULT, ok=False if x.days_left < 0 else None, ref=x.entry_id) for x in found]
    return _section(EXPIRING, lines, max_lines, TAB_VAULT)


def night_start(today: str) -> float:
    """When the night of `today` began: EVENING_HOUR of the day before, local time."""
    day = date.fromisoformat(today) - timedelta(days=1)
    return datetime(day.year, day.month, day.day, EVENING_HOUR).timestamp()


def _iso_ts(text: str) -> float | None:
    """An ISO time from the API ("2026-10-17T03:12:00.000Z") as a timestamp; None — not one."""
    try:
        when = datetime.fromisoformat(text.strip())
    except (AttributeError, ValueError):
        return None
    return when.timestamp()  # a time without a zone counts as local


def _clock(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def _night(vault, since: float, now: float, max_lines: int) -> Section | None:
    """Watched pipelines that finished red or green since `since`, then routines that ended
    with an error within the last day; each group in time order."""
    pipes: list[tuple[float, Line]] = []
    for facts in vault.integrations.gitlab_pipelines.values():
        if not isinstance(facts, dict) or facts.get("status") not in ("failed", "success"):
            continue
        ts = _iso_ts(str(facts.get("finished_at") or ""))
        if ts is None or not since <= ts <= now:
            continue
        name = str(facts.get("name") or f"{facts.get('project', '')} · {facts.get('ref', '')}")
        failed = facts["status"] == "failed"
        text = (_("{name} — failed at {time}") if failed else _("{name} — green at {time}")).format(name=name, time=_clock(ts))
        pipes.append((ts, Line(text, url=str(facts.get("url") or ""), tab=TAB_INTEGRATIONS, ok=not failed)))
    runs: list[tuple[float, Line]] = []
    for r in vault.routines.ordered():
        if r.last_code in (0, None) or not 0 <= now - r.last_run <= ROUTINE_WINDOW:
            continue
        text = _("Routine “{name}” — code {code} at {time}").format(name=r.name, code=r.last_code, time=_clock(r.last_run))
        runs.append((r.last_run, Line(text, ok=False)))
    lines = [line for _ts, line in sorted(pipes, key=lambda p: p[0])] + [line for _ts, line in sorted(runs, key=lambda p: p[0])]
    return _section(NIGHT, lines, max_lines)


def event_text(ev: Event) -> str:
    try:
        return _(ev.text).format(**ev.args)
    except (KeyError, IndexError, ValueError):
        return ev.text


def _feed(vault, since: float, now: float, max_lines: int) -> Section | None:
    """Unread notifications raised since `since`, oldest first; pipeline events are left
    to the night section."""
    events = [e for e in vault.integrations.feed if not e.read and since <= e.ts <= now and e.kind not in PIPELINE_KINDS]
    events.sort(key=lambda e: e.ts)
    lines = [Line(f"{mr_short(e.mr)} — {event_text(e)}" if e.mr else event_text(e), url=e.url, tab="" if e.url else TAB_INTEGRATIONS) for e in events]
    return _section(FEED, lines, max_lines, TAB_INTEGRATIONS)


# ---------------------------------------------------------------- the digest


def build(vault, today: str, *, now: float | None = None, yesterday_plans: bool = True, max_lines: int = 6,
          expiry_days: int = 7, seen: dict[str, str] | None = None) -> list[Section]:
    """Sections in a fixed order (SECTION_TITLES); an empty one is left out, [] — nothing
    to show. `today` — an ISO day; `now` — the moment of the look (now by default), the
    night runs from the evening before `today` to it; `seen` — the `expiry.seen` map of the
    settings (what the user put off)."""
    now = time.time() if now is None else now
    since = night_start(today)
    found = (
        _plans(vault, today, yesterday_plans, max_lines),
        _notes(vault, max_lines),
        _reviews(vault, max_lines),
        _expiring(vault, today, expiry_days, seen or {}, max_lines),
        _night(vault, since, now, max_lines),
        _feed(vault, since, now, max_lines),
    )
    return [s for s in found if s is not None]


def _mark(line: Line) -> str:
    return "• " if line.ok is None else ("✓ " if line.ok else "✗ ")


def as_text(sections: list[Section]) -> str:
    """The digest as plain text for "Copy as text": a title with its count, the lines with
    their links, "…and n more" when some did not fit."""
    out: list[str] = []
    for s in sections:
        head = _(s.title)
        if s.count > 1 or s.more:
            head += f" ({s.count})"
        if s.detail:
            head += f" — {s.detail}"
        out.append(head)
        for line in s.lines:
            out.append(f"  {_mark(line)}{line.text}" + (f" {line.url}" if line.url else ""))
        if s.more:
            out.append("  " + _("…and {n} more").format(n=s.more))
        out.append("")
    return "\n".join(out).rstrip("\n")


# ---------------------------------------------------------------- once a day


def should_show(settings_digest: Any, today: str) -> bool:
    """Show the digest at this unlock: switched on (it is by default) and not shown today."""
    d = settings_digest if isinstance(settings_digest, dict) else {}
    return bool(d.get("on_unlock", True)) and str(d.get("shown", "")) != today


def mark_shown(settings_digest: Any, today: str) -> dict:
    """The settings value after the digest was shown today (a new dict; other keys kept)."""
    d: dict = dict(settings_digest) if isinstance(settings_digest, dict) else {}
    d.setdefault("on_unlock", True)
    d["shown"] = today
    return d


def switched(settings_digest: Any, on: bool) -> dict:
    """The settings value with "Show the digest at the first unlock" turned on or off
    (the "Don't show again" button turns it off)."""
    d: dict = dict(settings_digest) if isinstance(settings_digest, dict) else {}
    d["on_unlock"] = bool(on)
    return d

