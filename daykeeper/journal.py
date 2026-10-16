"""Verification journal: per day — what was tested, what failed, which bugs were filed,
activity (reviews, MRs, test cases), plans.

Stored inside the encrypted vault (key `journal`). Sessions and scripts drop results into
the "inbox" — a plain JSONL file next to the vault; the app picks it up and files the
items by day (`inbox.py` in the project root is the writer)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import _
from .journal_layout import ALIASES, ROLE_MR, ROLE_NOTE, ROLE_PLAIN, ROLE_TESTS, current as current_layout, known_kind, resolve_known, role_of
from .settings import Links, private, readable

_links = Links()  # tracker and GitLab addresses; the app sets them from settings.json (`configure`)
ISSUE_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
MR_RE = re.compile(r"^!?(\d{1,6})$")
MR_URL_RE = re.compile(r"/merge_requests/(\d+)")
PR_URL_RE = re.compile(r"github\.com/[\w.-]+/([\w.-]+)/pull/(\d+)")
DEFAULT_OTHER = "None"  # UI key; what section 3 says when it is empty


def today() -> str:
    return time.strftime("%Y-%m-%d")


def configure(links: Links) -> None:
    """Where to expand issue keys and MR numbers (empty — they stay text)."""
    global _links
    _links = links


def links() -> Links:
    return _links


def activity_kind_for_label(label: str) -> str | None:
    """Editor line head (“MR reviews” in any UI language) → activity kind id."""
    return current_layout().kind_for_label(label)


def normalize_item(text: str) -> str:
    """Tracker link or key → key (APP-1234); other text as is, trimmed."""
    text = text.strip()
    if not text:
        return ""
    if "/browse/" in text:
        tail = text.rsplit("/browse/", 1)[-1].split("?", 1)[0].split("#", 1)[0].strip("/")
        return tail.upper() if ISSUE_KEY_RE.fullmatch(tail.upper()) else text
    m = ISSUE_KEY_RE.fullmatch(text.upper())
    return m.group(1) if m else text


def normalize_mr(text: str) -> str:
    """«42», «!42», link to an MR of the own repository → «!42»; foreign link or text as is."""
    text = text.strip()
    m = MR_RE.fullmatch(text)
    if m:
        return f"!{m.group(1)}"
    if _links.mr_browse and text.startswith(_links.mr_browse):
        m = MR_URL_RE.search(text)
        if m:
            return f"!{m.group(1)}"
    return text


def item_url(item: str) -> str:
    return _links.jira_browse + item if _links.jira_browse and ISSUE_KEY_RE.fullmatch(item) else item


def mr_url(item: str) -> str:
    return _links.mr_browse + item[1:] if _links.mr_browse and MR_RE.fullmatch(item) and item.startswith("!") else item


def mr_short(item: str) -> str:
    """For the report: «!42» as is; a link to an MR of another repository → «repo!17»,
    to a GitHub pull request → «repo#12»."""
    pr = PR_URL_RE.search(item)
    if pr:
        return f"{pr.group(1)}#{pr.group(2)}"
    m = MR_URL_RE.search(item)
    if not m or MR_RE.fullmatch(item):
        return item
    repo = item[: m.start()].rstrip("/").removesuffix("/-").rsplit("/", 1)[-1]
    return f"{repo}!{m.group(1)}"


def parse_lines(text: str) -> list[str]:
    """Field text → items without blank lines and duplicates (order kept)."""
    seen: set[str] = set()
    result: list[str] = []
    for line in text.splitlines():
        item = normalize_item(line)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def normalize_activity(kind: str, text: str) -> str:
    return normalize_mr(text) if role_of(kind) == ROLE_MR else text.strip()


def parse_activity_lines(text: str) -> list[dict]:
    """Lines «MR reviews: !42» → [{kind, text}]. A line without a known label → the note kind."""
    layout = current_layout()
    result: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        kind, body = layout.note_kind(), line
        if ":" in line:
            head, tail = line.split(":", 1)
            known = layout.kind_for_label(head)
            if known is not None:
                kind, body = known, tail
        for piece in body.split(",") if layout.role(kind) == ROLE_MR else [body]:
            item = normalize_activity(kind, piece)
            if item and (kind, item) not in seen:
                seen.add((kind, item))
                result.append({"kind": kind, "text": item})
    return result


@dataclass
class Day:
    date: str
    summary: str = ""  # «1. …» — what the day was about, one line; empty → built from the day
    columns: dict[str, list[str]] = field(default_factory=dict)  # column id → items (see journal_layout)
    activities: list[dict] = field(default_factory=list)  # [{kind, text}]
    plans: str = ""  # «2. …»
    other: str = ""  # «3. …»; empty → _(DEFAULT_OTHER) in the report

    def section(self, name: str) -> list[str]:
        return self.columns.setdefault(name, [])

    def add(self, section: str, item: str) -> bool:
        """Add an item to a column; False — already there."""
        item = normalize_item(item)
        items = self.section(section)
        if not item or item in items:
            return False
        items.append(item)
        return True

    def add_activity(self, kind: str, text: str) -> bool:
        layout = current_layout()
        if not known_kind(kind, layout):  # a kind of another layout stays itself: hidden, not lost
            kind = layout.note_kind()
        text = normalize_activity(kind, text)
        if not text or any(a["kind"] == kind and a["text"] == text for a in self.activities):
            return False
        self.activities.append({"kind": kind, "text": text})
        return True

    def activity_lines(self) -> str:
        """For the editor: one line per visible kind, items comma-separated; notes one per line."""
        lines = []
        for kind in current_layout().visible_kinds():
            items = [a["text"] for a in self.activities if a["kind"] == kind.id]
            if kind.role == ROLE_NOTE:
                lines += [f"{_(kind.label)}: {x}" for x in items]
            elif items:
                lines.append(f"{_(kind.label)}: {', '.join(items)}")
        return "\n".join(lines)

    def hidden_activities(self) -> list[dict]:
        """Items of kinds the layout hides or no longer has: kept, not shown in the editor."""
        shown = {k.id for k in current_layout().visible_kinds()}
        return [a for a in self.activities if a["kind"] not in shown]

    def auto_summary(self) -> str:
        """Phrase for «1.» when there is no own one: «tested tasks, reviewed MRs, new manual/autotests,
        nightly run triage» — the column and activity phrases of the layout, then the notes of
        the day, each as it was written."""
        layout = current_layout()
        parts: list[str] = []
        for col in layout.visible_columns():
            if col.phrase and not col.phrase_alone and self.columns.get(col.id) and _(col.phrase) not in parts:
                parts.append(_(col.phrase))
        for kind in layout.visible_kinds():
            if kind.phrase and _(kind.phrase) not in parts and any(a["kind"] == kind.id for a in self.activities):
                parts.append(_(kind.phrase))
        notes = {k.id for k in layout.visible_kinds() if k.role == ROLE_NOTE}
        parts += [a["text"] for a in self.activities if a["kind"] in notes]
        if not parts:
            parts += [_(c.phrase) for c in layout.visible_columns() if c.phrase and c.phrase_alone and self.columns.get(c.id)]
        return ", ".join(parts)

    def counts(self) -> list[int]:
        return [len(self.columns.get(c.id, [])) for c in current_layout().visible_columns()]

    @property
    def total(self) -> int:
        return sum(self.counts())

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "summary": self.summary,
            "columns": {k: list(v) for k, v in self.columns.items() if v},
            "activities": [dict(a) for a in self.activities],
            "plans": self.plans,
            "other": self.other,
        }

    @staticmethod
    def from_dict(d: dict) -> Day:
        acts = [
            {"kind": str(a.get("kind", "note")), "text": str(a.get("text", ""))}
            for a in d.get("activities", [])
            if isinstance(a, dict) and str(a.get("text", "")).strip()
        ]
        raw = d.get("columns")
        if not isinstance(raw, dict):
            raw = {}
        columns = {str(k): [str(x) for x in v] for k, v in raw.items() if isinstance(v, list)}
        for legacy in LEGACY_COLUMNS:  # a day written before the layouts: three fixed lists
            if isinstance(d.get(legacy), list) and d[legacy] and legacy not in columns:
                columns[legacy] = [str(x) for x in d[legacy]]
        return Day(
            date=str(d.get("date", "")),
            summary=str(d.get("summary", "")),
            columns=columns,
            activities=acts,
            plans=str(d.get("plans", "")),
            other=str(d.get("other", "")),
        )


LEGACY_COLUMNS = ("ok", "failed", "bugs")


class Journal:
    """Days by date. Serialised as a list inside the vault JSON."""

    def __init__(self, days: list[Day] | None = None):
        self.days: dict[str, Day] = {d.date: d for d in (days or []) if d.date}

    def get(self, date: str, create: bool = False) -> Day | None:
        day = self.days.get(date)
        if day is None and create:
            day = self.days[date] = Day(date)
        return day

    def remove(self, date: str) -> None:
        self.days.pop(date, None)

    def dates(self) -> list[str]:
        """Newest first."""
        return sorted(self.days, reverse=True)

    def to_list(self) -> list[dict]:
        return [self.days[d].to_dict() for d in sorted(self.days)]

    @staticmethod
    def from_list(items: list[dict]) -> Journal:
        return Journal([Day.from_dict(x) for x in items if isinstance(x, dict)])


def build_report(day: Day) -> str:
    """Report text in the shape of the daily standup, following the journal layout."""
    layout = current_layout()
    short = (lambda x: mr_short(x)) if layout.mr_short else (lambda x: mr_url(x))
    summary = day.summary.strip() or day.auto_summary()
    out = [f"1. {summary}".rstrip()]
    tests: list[str] = []
    for kind in layout.visible_kinds():
        items = [a["text"] for a in day.activities if a["kind"] == kind.id]
        if kind.role == ROLE_TESTS:
            tests += items
        elif kind.role == ROLE_MR and items:
            out.append(f"{_(kind.label)}: {', '.join(short(x) for x in items)}")
        elif kind.role == ROLE_PLAIN and items:
            out.append(f"{_(kind.label)}: {', '.join(items)}")
    tests = list(dict.fromkeys(tests))
    if tests:
        out.append(f"{_('New tests')}: {', '.join(tests)}")
    for col in layout.visible_columns():
        out.append(f"{_(col.label)}:")
        out += [item_url(x) for x in day.columns.get(col.id, [])]
        out.append("")
    number = 2
    if layout.show_plans:
        out += [f"{number}. {day.plans}".rstrip(), ""]
        number += 1
    if layout.show_other:
        out.append(f"{number}. {day.other.strip() or _(DEFAULT_OTHER)}")
    return "\n".join(out).rstrip("\n")


# ---------------------------------------------------------------- inbox

INBOX_NAME = "inbox.jsonl"


def inbox_path_for(vault_path: Path) -> Path:
    return Path(vault_path).parent / INBOX_NAME


@dataclass
class InboxItem:
    date: str
    section: str  # a column id of the journal layout, or activity
    item: str
    kind: str = ""  # activity category
    note: str = ""
    origin: str = ""  # git host of the folder the writer ran in; "" — unknown, counts as work


def repo_host(folder: str | Path | None = None) -> str:
    """The host of the `origin` remote of the git repository at `folder` (the current one by
    default): https://host/…, ssh://git@host:2222/…, git@host:… → «host». "" — not a repository."""
    try:
        url = subprocess.run(  # noqa: S603 — git, asked for the remote of a folder
            ["git", "-C", str(folder or Path.cwd()), "remote", "get-url", "origin"],  # noqa: S607 — git from PATH, as the user runs it
            capture_output=True, text=True, timeout=3, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.match(r"^(?:[a-z+]+://)?(?:[^@/]+@)?([^:/]+)", url)
    return m.group(1).lower() if m else ""


def is_work(origin: str, work_hosts: set[str]) -> bool:
    """An item belongs to the work journal unless it was written in a repository on a host
    that is not one of `work_hosts` (no hosts set — everything is work)."""
    return not origin or not work_hosts or origin in work_hosts


def append_inbox(path: Path, status: str, items: list[str], date: str | None = None, note: str = "", origin: str | None = None) -> int:
    """Writer for scripts and sessions: one JSON line per item. Returns the number written.
    `status` — a column or activity word of the journal layout (`pass`, `review`, `note`…);
    `origin` — where the work was done (see repo_host); None — the current folder's repository."""
    layout = current_layout()
    resolved = resolve_known(status, layout)
    if resolved is None:
        words = " | ".join(w for w, _l in layout.statuses())
        raise ValueError(f"Status must be one of {words}, not {status!r}")
    where, ident = resolved
    if origin is None:
        origin = repo_host()
    rows = []
    for x in items:
        text = normalize_activity(ident, x) if where == "activity" else normalize_item(x)
        if text:
            row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "date": date or today(), "note": note, "origin": origin, "item": text}
            row.update({"status": "activity", "kind": ident} if where == "activity" else {"status": ident, "kind": ""})
            rows.append(row)
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    # a lone surrogate (an odd file name pasted into a note) becomes U+FFFD: the line is
    # written, not a traceback in the hook or the console
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(readable(json.dumps(row, ensure_ascii=False)) + "\n")
    private(path)
    return len(rows)


def claim_inbox(path: Path) -> list[Path]:
    """Move the inbox aside under a name no writer uses, so what a writer appends while
    the app reads and saves goes to a fresh inbox instead of being deleted unread with
    it. The rename is atomic (NTFS and POSIX); when a writer holds the file open at that
    moment (Windows refuses the rename), nothing is claimed and the next pull tries again.
    Returns the files to read, a claim left by a crash between claim and save first."""
    folder = path.parent
    claimed = sorted(folder.glob(path.name + ".*")) if folder.is_dir() else []
    if path.exists():
        target = path.with_name(f"{path.name}.{os.getpid()}-{time.time_ns()}")
        try:
            os.replace(path, target)
        except OSError:
            return claimed
        claimed.append(target)
    return claimed


def read_inbox(path: Path) -> list[InboxItem]:
    """Read the inbox; broken lines are skipped."""
    if not path.exists():
        return []
    items: list[InboxItem] = []
    # a broken byte or a line that is not an object is skipped like any broken line: one
    # bad write must not keep the inbox from being picked up for good
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        status = str(row.get("status", "")).lower()
        kind = str(row.get("kind", ""))
        date = str(row.get("date") or today())
        origin = str(row.get("origin", "")).lower()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            continue
        if status == "activity":
            item = normalize_activity(kind, str(row.get("item", "")))
            if item and kind:
                items.append(InboxItem(date, "activity", item, kind, str(row.get("note", "")), origin))
            continue
        resolved = current_layout().resolve(status)
        section: str | None
        if resolved and resolved[0] == "column":
            section = resolved[1]
        else:  # a column this layout does not show: kept under its id, back when shown again
            section = next((cid for cid in LEGACY_COLUMNS if status in ALIASES[cid]), None) or (status if re.fullmatch(r"[a-z0-9_-]{1,32}", status) else None)
        item = normalize_item(str(row.get("item", "")))
        if section and item:
            items.append(InboxItem(date, section, item, "", str(row.get("note", "")), origin))
    return items


def merge_inbox(journal: Journal, items: list[InboxItem], work_hosts: set[str] | None = None) -> int:
    """File inbox items by day. Returns the number of new items (duplicates not counted).
    Items written in a repository outside `work_hosts` (personal projects) are dropped."""
    added = 0
    for it in items:
        if not is_work(it.origin, work_hosts or set()):
            continue
        day = journal.get(it.date, create=True)
        if day is None:  # create=True always gives one
            continue
        ok = day.add_activity(it.kind, it.item) if it.section == "activity" else day.add(it.section, it.item)
        if ok:
            added += 1
    return added


def clear_inbox(path: Path) -> None:
    if path.exists():
        path.unlink()
