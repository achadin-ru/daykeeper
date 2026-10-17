"""When a key runs out: the dates in "valid until" / "expires" fields and in certificates,
and the list of entries that expire soon.

Only open fields are read: a secret field is never unsealed for this, so a date or a
certificate kept in a secret field is not seen. Pure (no tkinter)."""

from __future__ import annotations

import calendar
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from .i18n import variants
from .vault import Entry

CERT_HEAD = "-----BEGIN CERTIFICATE-----"
CERT_FIELD = "certificate"  # the field name of an expiry found in a certificate of an unnamed field
DATE_FIELDS = ("valid until", "expires")  # preset field name keys, matched in every language


@dataclass(frozen=True)
class Expiry:
    entry_id: str
    entry_name: str
    field: str  # field name, or "certificate"
    when: date
    days_left: int  # negative — already expired

    @property
    def key(self) -> str:
        """"<entry_id>:<field>" — the key of the item in the `seen` map of the settings."""
        return f"{self.entry_id}:{self.field}"


class _HasEntries(Protocol):
    entries: list[Entry]


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _year(two: str, today: date) -> int:
    """A two-digit year in the century that puts it closest to today: "26" → 2026."""
    year = today.year // 100 * 100 + int(two)
    if year > today.year + 50:
        year -= 100
    elif year < today.year - 50:
        year += 100
    return year


# (pattern, year/month/day groups); a month alone means the last day of it, as on a card.
# Tried in this order at each place of the text, so a full date wins over a part of it.
_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})", "ymd"),  # 2026-12-31
    (r"(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{4})", "dmy"),  # 31.12.2026
    (r"(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>\d{4})", "dmy"),  # 31/12/2026
    (r"(?P<y>\d{4})-(?P<m>\d{1,2})", "ym"),  # 2026-12
    (r"(?P<m>\d{1,2})/(?P<y>\d{4})", "my"),  # 12/2026
    (r"(?P<m>\d{1,2})/(?P<yy>\d{2})", "my"),  # 12/26, a card
)
# all the patterns as one, unnamed, for finding a token in free text; a token is not a
# piece of a longer number ("12/26" inside "12/2026", "1/2026/7")
_DATE_RE = re.compile("|".join(r"(?<![\d./-])(?:" + re.sub(r"\?P<\w+>", "?:", p) + r")(?![\d./-]*\d)" for p, _kind in _PATTERNS))


def _build(text: str, today: date) -> date | None:
    """One token of the text as a date, or None when it is not a real day."""
    for pattern, _kind in _PATTERNS:
        m = re.fullmatch(pattern, text)
        if m is None:
            continue
        g = m.groupdict()
        try:
            year = _year(g["yy"], today) if g.get("yy") else int(g["y"])
            month = int(g["m"])
            if g.get("d") is None:
                return _month_end(year, month)
            return date(year, month, int(g["d"]))
        except ValueError:  # month 13, February 30, year 0
            return None
    return None


def parse_date(text: str, today: date) -> date | None:
    """The first date in `text`: "2026-12-31", "31.12.2026", "31/12/2026", "2026-12",
    "12/2026", "12/26" (a month alone — its last day), also inside words ("until
    31.12.2026"). `today` places a two-digit year in its century. None — no date."""
    for m in _DATE_RE.finditer(text or ""):
        when = _build(m.group(0), today)
        if when is not None:
            return when
    return None


def cert_expiry(pem: str) -> date | None:
    """The last day of a PEM certificate in LOCAL time (it is compared with a local
    `today`; the UTC date can be a day off near midnight), or None when the text is not
    one."""
    text = (pem or "").strip()
    if not text.startswith(CERT_HEAD):
        return None
    from cryptography import x509  # only when there is a certificate: keeps the import of this module light

    try:
        cert = x509.load_pem_x509_certificate(text.encode("ascii", "replace"))
    except ValueError:
        return None
    return cert.not_valid_after_utc.astimezone().date()


_DATE_NAMES: set[str] = set()


def is_date_field(name: str) -> bool:
    """A "valid until" / "expires" field in any language."""
    if not _DATE_NAMES:
        for key in DATE_FIELDS:
            _DATE_NAMES.update(v.casefold() for v in variants(key))
    return name.strip().casefold() in _DATE_NAMES


def _found(entries: Iterable[Entry], today: date) -> Iterable[Expiry]:
    for e in entries:
        for f in e.fields:
            if f.secret or not f.filled:
                continue  # a secret value is never unsealed for a date
            value = f.value
            name = f.key.strip()
            when = parse_date(value, today) if is_date_field(name) else None
            if when is None and value.lstrip().startswith(CERT_HEAD):
                when = cert_expiry(value)
                name = name or CERT_FIELD
            if when is not None:
                yield Expiry(e.id, e.name, name, when, (when - today).days)


def expiring(vault: _HasEntries, today: date, days: int, seen: dict[str, str]) -> list[Expiry]:
    """What runs out within `days` days from today, the expired included; sorted soonest
    first. `seen` maps "<entry_id>:<field>" to an ISO day: the item is left out up to and
    including that day (today — dismissed for the day; a later day — "remind me then")."""
    stamp = today.isoformat()
    out = [x for x in _found(vault.entries, today) if x.days_left <= days and str(seen.get(x.key, "")) < stamp]
    out.sort(key=lambda x: (x.when, x.entry_name.casefold(), x.field))
    return out
