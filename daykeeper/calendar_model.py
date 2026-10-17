"""Calendar: own events and reminders, the month grid that gathers the other tabs by day,
and the import of meetings from an .ics file. Pure (no tkinter).

Stored inside the encrypted vault (key `calendar`, a list of events). Named
calendar_model, not calendar: a module named calendar would shadow the standard one.

A repeating event is stored once, on the date of its first occurrence; `on(date)`
expands the repeats. A monthly event on the 31st falls on the last day of a shorter
month; a yearly one on 29 February falls on 28 February in a common year.

An .ics file is only read as text: nothing in it is fetched, opened or run. What
`read_ics` maps:

- DTSTART with a date — an all-day event; with a time — the wall time as written, with or
  without TZID (the zone is not converted); a time in UTC (…Z) — converted to local time.
  So a DTSTART with TZID is taken as local wall time: a meeting set in another time zone
  shows at the hour written in the file, which is the wrong local hour.
- DTEND — the end time of the same day; SUMMARY, DESCRIPTION, URL (http(s) only), UID; a
  VALARM with a TRIGGER before the start — the reminder minutes, at most a week
  (`MAX_REMIND_MIN`; a longer one is cut to a week).
- RRULE FREQ=WEEKLY (INTERVAL 1) with or without BYDAY — one weekly event per weekday:
  the model has no "several weekdays" repeat, so «MO,WE» becomes two events with the UIDs
  «<uid>#MO» and «<uid>#WE». FREQ=DAILY (INTERVAL 1) is the same with all seven days (or
  with its BYDAY). UNTIL becomes `until`; COUNT becomes the date of the last occurrence.
  FREQ=MONTHLY / YEARLY without other parts map to the monthly / yearly repeat while the
  day exists in every month (up to the 28th) or year (not 29 February); otherwise, and
  for any other rule (INTERVAL > 1, BYMONTHDAY, BYSETPOS…), the event is imported once, on
  its DTSTART. EXDATE is ignored. A VEVENT with RECURRENCE-ID (a moved occurrence) is
  imported as a single event with the UID «<uid>#<recurrence-id>».
- An event whose dates cannot be computed (a crafted file near the year 9999) is
  skipped; the other events of the file are read.
- Importing the same UID again updates the events of that UID (`upsert_ics`) instead of
  adding copies; the event keeps its id and the date its reminder was last shown.
"""

from __future__ import annotations

import calendar as stdcal
import re
import secrets
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Protocol

from .gitlab import web_url
from .journal_layout import current as current_layout
from .settings import number

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .integrations import Integrations
    from .journal import Journal
    from .notes import Notes
    from .routines import Routines

REPEATS = ("", "weekly", "monthly", "yearly")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
UID_PART = "#"  # «<uid>#MO»: one of the events an .ics event became
ICS_DAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")  # index = date.weekday()
MAX_ICS_EVENTS = 5000  # a file bigger than any real calendar export is cut, not read for minutes
MAX_COUNT = 1000  # RRULE COUNT beyond this is treated as no end
MAX_REMIND_MIN = 7 * 24 * 60  # a reminder at most a week ahead; a longer one is cut to this


Date = date  # for the methods of CalEvent, whose field `date` hides the type


def _new_id() -> str:
    return secrets.token_hex(6)


def _parse_date(text: str) -> date | None:
    if not DATE_RE.fullmatch(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _clean_date(value) -> str:
    text = str(value or "")
    return text if _parse_date(text) else ""


def _clean_time(value) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d:[0-5]\d", text):  # «9:30» typed without the leading zero
        text = "0" + text
    return text if TIME_RE.fullmatch(text) else ""


def _clean_remind(value) -> int:
    """Reminder minutes in 0…MAX_REMIND_MIN: a stored or imported value cannot push the
    reminder start out of the range of dates."""
    return min(MAX_REMIND_MIN, max(0, number(int, value, 0)))


def _month_day(year: int, month: int, day: int) -> date:
    """The day of that month, or its last day when the month is shorter."""
    return date(year, month, min(day, stdcal.monthrange(year, month)[1]))


@dataclass
class CalEvent:
    id: str = field(default_factory=_new_id)
    title: str = ""
    date: str = ""  # "YYYY-MM-DD": the day, or the first occurrence of a repeat
    time: str = ""  # "HH:MM"; "" — all day
    repeat: str = ""  # "" | "weekly" | "monthly" | "yearly"
    until: str = ""  # "YYYY-MM-DD": the last day a repeat may fall on; "" — no end
    remind_min: int = 0  # minutes before the start to remind; 0 — no reminder (an all-day event has none)
    url: str = ""
    note: str = ""
    uid: str = ""  # from an .ics file
    notified: str = ""  # the date of the occurrence the reminder was last shown for
    end: str = ""  # "HH:MM" the event ends (DTEND of an .ics file); "" — not given

    def start_date(self) -> Date | None:
        return _parse_date(self.date)

    def occurs_on(self, day: Date) -> bool:
        first = self.start_date()
        if first is None or day < first:
            return False
        last = _parse_date(self.until)
        if self.repeat and last is not None and day > last:
            return False
        if self.repeat == "weekly":
            return (day - first).days % 7 == 0
        if self.repeat == "monthly":
            return day == _month_day(day.year, day.month, first.day)
        if self.repeat == "yearly":
            return day.month == first.month and day == _month_day(day.year, day.month, first.day)
        return day == first

    def starts_at(self, day: Date) -> datetime | None:
        """The start of the occurrence on `day` (None — all day)."""
        if not self.time:
            return None
        hh, mm = self.time.split(":")
        return datetime.combine(day, time(int(hh), int(mm)))

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "date": self.date, "time": self.time, "repeat": self.repeat,
            "until": self.until, "remind_min": self.remind_min, "url": self.url, "note": self.note,
            "uid": self.uid, "notified": self.notified, "end": self.end,
        }

    @classmethod
    def from_dict(cls, d) -> CalEvent | None:
        """Tolerant: a field of a wrong type gets its default; an event without a valid date
        is dropped (None)."""
        if not isinstance(d, dict):
            return None
        day = _clean_date(d.get("date"))
        if not day:
            return None
        repeat = str(d.get("repeat") or "")
        return cls(
            id=str(d.get("id") or _new_id()),
            title=str(d.get("title") or ""),
            date=day,
            time=_clean_time(d.get("time")),
            repeat=repeat if repeat in REPEATS else "",
            until=_clean_date(d.get("until")),
            remind_min=_clean_remind(d.get("remind_min")),
            url=str(d.get("url") or ""),
            note=str(d.get("note") or ""),
            uid=str(d.get("uid") or ""),
            notified=_clean_date(d.get("notified")),
            end=_clean_time(d.get("end")),
        )


def _base_uid(uid: str) -> str:
    return uid.split(UID_PART, 1)[0]


def _sort_key(e: CalEvent) -> tuple[str, str]:
    return (e.time, e.title.lower())  # all-day ("") first


class Calendar:
    """The own events of the vault."""

    def __init__(self, events: list[CalEvent] | None = None):
        self.events: list[CalEvent] = list(events or [])

    def add(self, title: str, date: str, **kw) -> CalEvent:
        event = CalEvent(title=title, date=date, **kw)
        self.events.append(event)
        return event

    def remove(self, event: CalEvent) -> None:
        if event in self.events:
            self.events.remove(event)

    def get(self, event_id: str) -> CalEvent | None:
        return next((e for e in self.events if e.id == event_id), None)

    def upsert_ics(self, events: Iterable[CalEvent]) -> tuple[int, int]:
        """Take in events read from an .ics file. The events of a UID already here are
        replaced by the new ones of that UID (a rule changed from «MO,WE» to «MO» drops the
        Wednesday). A new event takes over the old one with the same full UID, else a
        leftover old one of the same UID: it keeps its id, the date its reminder was last
        shown and, when the file gives none, its reminder minutes. Events without a UID are
        added. Returns (added, updated)."""
        incoming = list(events)
        groups = {_base_uid(e.uid) for e in incoming if e.uid}
        old = {e.uid: e for e in self.events if e.uid and _base_uid(e.uid) in groups}
        self.events = [e for e in self.events if not (e.uid and _base_uid(e.uid) in groups)]
        taken = {e.uid for e in incoming if e.uid in old}
        spare: dict[str, list[CalEvent]] = {}  # old events of a group no new one has the UID of
        for uid, kept in old.items():
            if uid not in taken:
                spare.setdefault(_base_uid(uid), []).append(kept)
        added = updated = 0
        for e in incoming:
            prev = old.get(e.uid) if e.uid else None
            if prev is None and e.uid and spare.get(_base_uid(e.uid)):
                prev = spare[_base_uid(e.uid)].pop(0)  # a rule that changed shape: the same meeting
            if prev is not None:
                e.id = prev.id
                e.notified = prev.notified
                e.remind_min = e.remind_min or prev.remind_min
                updated += 1
            else:
                added += 1
            self.events.append(e)
        return added, updated

    def on(self, day: str | date) -> list[CalEvent]:
        """The events that fall on a day, repeats expanded; all-day first, then by time. A
        repeat is returned as its stored event (its `date` is the first occurrence)."""
        d = _parse_date(day) if isinstance(day, str) else day
        if d is None:
            return []
        return sorted((e for e in self.events if e.occurs_on(d)), key=_sort_key)

    def due(self, now: datetime) -> list[CalEvent]:
        """The reminders to show now: an occurrence whose start is less than `remind_min`
        minutes away and has not begun. Each is marked (`notified` = the date of the
        occurrence), so it is shown once; the caller saves the vault. An event whose times
        cannot be computed is skipped: the others still fire."""
        found: list[CalEvent] = []
        for e in self.events:
            try:
                if self._is_due(e, now):
                    found.append(e)
            except (ValueError, OverflowError, TypeError):
                continue
        return sorted(found, key=_sort_key)

    @staticmethod
    def _is_due(e: CalEvent, now: datetime) -> bool:
        if not e.time or e.remind_min <= 0:
            return False
        minutes = min(e.remind_min, MAX_REMIND_MIN)  # an event added in code is not cleaned by from_dict
        ahead = minutes // (24 * 60) + 1  # a reminder may begin a day or more before
        for offset in range(ahead + 1):
            day = now.date() + timedelta(days=offset)
            start = e.starts_at(day)
            if start is None or not e.occurs_on(day) or e.notified == day.isoformat():
                continue
            if start - timedelta(minutes=minutes) <= now < start:
                e.notified = day.isoformat()
                return True
        return False

    def to_list(self) -> list[dict]:
        return [e.to_dict() for e in self.events]

    @classmethod
    def from_list(cls, rows) -> Calendar:
        if not isinstance(rows, list):
            return cls()
        return cls([e for e in (CalEvent.from_dict(r) for r in rows) if e is not None])


# ---------------------------------------------------------------- the month grid


class Expiry(Protocol):
    """What the grid needs of an expiring key (the expiry module gives such objects)."""

    @property
    def when(self) -> date: ...

    @property
    def entry_name(self) -> str: ...


class VaultParts(Protocol):
    journal: Journal
    notes: Notes
    routines: Routines
    integrations: Integrations


@dataclass
class DayCell:
    date: str
    journal: int = 0  # items of the journal day: visible columns and activities of visible kinds
    notes_open: int = 0  # undone checklist items of the day's notes
    notes_total: int = 0
    routines: list[str] = field(default_factory=list)  # «09:30 Name» of routines that run at a time that weekday
    expiries: list[str] = field(default_factory=list)  # names of entries whose key expires that day
    events: list[CalEvent] = field(default_factory=list)
    feed: int = 0  # integration events of the day
    in_month: bool = True  # False — a day of the previous or next month that fills the grid
    today: bool = False
    weekend: bool = False


def _expiry_day(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "")[:10]


def month_cells(year: int, month: int, vault: VaultParts, expiries: Iterable[Expiry], today: str, calendar: Calendar | None = None) -> list[list[DayCell]]:
    """Six rows of seven, Monday first. `calendar` — the own events; by default the
    vault's (`vault.calendar`), none when it has no such part."""
    cal = calendar if calendar is not None else getattr(vault, "calendar", None)
    first = date(year, month, 1)
    start = first - timedelta(days=first.weekday())
    by_day: dict[str, list[str]] = {}
    for x in expiries:
        by_day.setdefault(_expiry_day(getattr(x, "when", None)), []).append(str(getattr(x, "entry_name", "")))
    shown = {k.id for k in current_layout().visible_kinds()}  # as the daily report counts: hidden kinds are not
    feed = Counter(datetime.fromtimestamp(e.ts).date().isoformat() for e in vault.integrations.feed)
    timed = []
    for r in vault.routines.ordered():
        hm = r.schedule.valid_time()
        if hm is not None:
            timed.append((f"{hm[0]:02d}:{hm[1]:02d}", r))
    timed.sort(key=lambda t: t[0])
    rows: list[list[DayCell]] = []
    for week in range(6):
        row: list[DayCell] = []
        for wd in range(7):
            d = start + timedelta(days=week * 7 + wd)
            key = d.isoformat()
            jday = vault.journal.get(key)
            done, total = vault.notes.day_progress(key)
            row.append(DayCell(
                date=key,
                journal=(jday.total + sum(1 for a in jday.activities if a.get("kind") in shown)) if jday is not None else 0,
                notes_open=total - done,
                notes_total=total,
                routines=[f"{at} {r.name}".rstrip() for at, r in timed if wd in r.schedule.days],
                expiries=list(by_day.get(key, [])),
                events=cal.on(d) if isinstance(cal, Calendar) else [],
                feed=feed.get(key, 0),
                in_month=d.month == month,
                today=key == today,
                weekend=wd >= 5,
            ))
        rows.append(row)
    return rows


# ---------------------------------------------------------------- .ics import


def _unfold(text: str) -> list[str]:
    """Content lines: a line that begins with a space or a tab continues the previous one."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _split_property(line: str) -> tuple[str, dict[str, str], str] | None:
    """«DTSTART;TZID="Europe/X":20261020T110000» → ("DTSTART", {"TZID": "Europe/X"}, value).
    A colon inside a quoted parameter value does not end the name part."""
    quoted = False
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ":" and not quoted:
            head, value = line[:i], line[i + 1:]
            break
    else:
        return None
    name, *params = head.split(";")
    found: dict[str, str] = {}
    for p in params:
        if "=" in p:
            k, v = p.split("=", 1)
            found[k.strip().upper()] = v.strip().strip('"')
    return name.strip().upper(), found, value


def _unescape(value: str) -> str:
    return re.sub(r"\\([\\,;nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value)


def _ics_moment(value: str, params: dict[str, str]) -> tuple[date, time | None, date] | None:
    """(local date, local time or None for a date, the date as written in the file)."""
    value = value.strip()
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})?(Z?))?", value)
    if not m:
        return None
    try:
        written = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if m.group(4) is None or params.get("VALUE", "").upper() == "DATE":
            return written, None, written
        moment = datetime.combine(written, time(int(m.group(4)), int(m.group(5)), int(m.group(6) or 0)))
    except ValueError:
        return None
    if m.group(7):  # UTC: the local wall time of that moment
        moment = moment.replace(tzinfo=UTC).astimezone().replace(tzinfo=None)
    return moment.date(), moment.time(), written


def _trigger_minutes(value: str) -> int:
    """«-PT15M», «-PT1H», «-P1D», «-P1DT2H» before the start → minutes, at most
    MAX_REMIND_MIN; anything else — 0."""
    m = re.fullmatch(r"-P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value.strip().upper())
    if not m:
        return 0
    w, d, h, mi = (int(g or 0) for g in m.groups()[:4])
    return min(MAX_REMIND_MIN, ((w * 7 + d) * 24 + h) * 60 + mi)


def _rule(value: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for piece in value.split(";"):
        if "=" in piece:
            k, v = piece.split("=", 1)
            parts[k.strip().upper()] = v.strip().upper()
    return parts


def _count_end(first: date, days: list[int], count: int) -> date:
    """The date of the `count`-th occurrence, from `first`, on the weekdays `days`."""
    day, seen = first, 0
    while True:
        if day.weekday() in days:
            seen += 1
            if seen >= count:
                return day
        day += timedelta(days=1)


def _expand_rule(base: CalEvent, rule: dict[str, str], first: date, shift: int, until: str) -> list[CalEvent]:
    """The events a rule maps to (see the module docstring); [base] — kept as a single event."""
    freq = rule.get("FREQ", "")
    interval = number(int, rule.get("INTERVAL") or 1, 0)
    other = set(rule) - {"FREQ", "INTERVAL", "UNTIL", "COUNT", "BYDAY", "WKST"}
    if interval != 1 or other:
        return [base]
    count = number(int, rule.get("COUNT"), 0)
    if not 0 < count <= MAX_COUNT:
        count = 0  # none, broken or too far: no end from COUNT
    if freq in ("WEEKLY", "DAILY"):
        names = [x for x in rule.get("BYDAY", "").split(",") if x]
        if any(x not in ICS_DAYS for x in names):  # «1MO», «-1FR»: not a plain weekly rule
            return [base]
        if names:
            days = sorted({(ICS_DAYS.index(n) + shift) % 7 for n in names})  # a UTC time moved to another local day moves the weekdays too
        elif freq == "DAILY":
            days = list(range(7))
        else:
            days = [first.weekday()]
        last = _count_end(first, days, count).isoformat() if count else until
        out = []
        for wd in days:
            day = first + timedelta(days=(wd - first.weekday()) % 7)
            if last and day.isoformat() > last:
                continue
            uid = base.uid if len(days) == 1 or not base.uid else f"{base.uid}{UID_PART}{ICS_DAYS[wd]}"
            out.append(CalEvent(
                title=base.title, date=day.isoformat(), time=base.time, repeat="weekly", until=last,
                remind_min=base.remind_min, url=base.url, note=base.note, uid=uid, end=base.end,
            ))
        return out or [base]
    if freq == "MONTHLY" and "BYDAY" not in rule and first.day <= 28:
        last = _month_day(*_add_months(first, count - 1), first.day).isoformat() if count else until
        return [_with(base, repeat="monthly", until=last)]
    if freq == "YEARLY" and "BYDAY" not in rule and (first.month, first.day) != (2, 29):
        last = first.replace(year=first.year + count - 1).isoformat() if count else until
        return [_with(base, repeat="yearly", until=last)]
    return [base]


def _add_months(d: date, months: int) -> tuple[int, int]:
    total = d.year * 12 + d.month - 1 + months
    return total // 12, total % 12 + 1


def _with(e: CalEvent, **kw) -> CalEvent:
    data = e.to_dict()
    data.update(kw)
    made = CalEvent.from_dict(data)
    return made if made is not None else e


def _event_from(props: list[tuple[str, dict[str, str], str]], alarm: int) -> list[CalEvent]:
    seen: dict[str, tuple[dict[str, str], str]] = {}
    for name, params, value in props:
        seen.setdefault(name, (params, value))  # the first of a repeated property
    if "DTSTART" not in seen:
        return []
    start = _ics_moment(seen["DTSTART"][1], seen["DTSTART"][0])
    if start is None:
        return []
    first, at, written = start
    end = ""
    if at is not None and "DTEND" in seen:
        stop = _ics_moment(seen["DTEND"][1], seen["DTEND"][0])
        if stop is not None and stop[1] is not None and stop[0] == first:
            end = stop[1].strftime("%H:%M")
    uid = _unescape(seen.get("UID", ({}, ""))[1]).strip()
    if uid and "RECURRENCE-ID" in seen:
        uid = f"{uid}{UID_PART}{seen['RECURRENCE-ID'][1].strip()}"
    base = CalEvent(
        title=_unescape(seen.get("SUMMARY", ({}, ""))[1]).strip(),
        date=first.isoformat(),
        time=at.strftime("%H:%M") if at is not None else "",
        remind_min=alarm if at is not None else 0,
        url=web_url(_unescape(seen.get("URL", ({}, ""))[1]).strip()),
        note=_unescape(seen.get("DESCRIPTION", ({}, ""))[1]).strip(),
        uid=uid,
        end=end,
    )
    if "RRULE" not in seen or "RECURRENCE-ID" in seen:
        return [base]
    rule = _rule(seen["RRULE"][1])
    until = ""
    if "UNTIL" in rule:
        moment = _ics_moment(rule["UNTIL"], {})
        until = moment[0].isoformat() if moment is not None else ""
    return _expand_rule(base, rule, first, (first - written).days, until)


def read_ics(text: str) -> list[CalEvent]:
    """The events of an .ics file (see the module docstring for what is mapped). Broken
    events are skipped (also one whose dates fall out of range: a crafted file near the
    year 9999); nothing in the file is fetched or run."""
    events: list[CalEvent] = []
    props: list[tuple[str, dict[str, str], str]] | None = None
    depth = 0  # components nested inside the VEVENT (VALARM)
    alarm = 0
    alarm_trigger = 0
    for line in _unfold(text):
        parsed = _split_property(line)
        if parsed is None:
            continue
        name, params, value = parsed
        kind = value.strip().upper()
        if name == "BEGIN" and kind == "VEVENT" and props is None:
            props, depth, alarm = [], 0, 0
        elif props is None:
            continue
        elif name == "BEGIN":
            depth += 1
            alarm_trigger = 0
        elif name == "END" and depth:
            depth -= 1
            if kind == "VALARM" and alarm_trigger and not alarm:
                alarm = alarm_trigger
        elif name == "END" and kind == "VEVENT":
            try:
                events += _event_from(props, alarm)
            except (ValueError, OverflowError):
                pass  # a date pushed past the year 9999: this event is skipped, the rest read
            props = None
            if len(events) >= MAX_ICS_EVENTS:
                break
        elif depth:
            if name == "TRIGGER" and params.get("RELATED", "START").upper() == "START":
                alarm_trigger = _trigger_minutes(value)
        else:
            props.append((name, params, value))
    return events[:MAX_ICS_EVENTS]
