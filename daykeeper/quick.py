"""The quick window behind the global hotkey: what it searches and what a line typed into it
means for the journal. No tkinter here; the window is `quick_view.py`.

`index` gathers vault entries, bookmarks, recent notes, routines, text templates and a few
commands into `Item`s; `rank` orders them for a query. Secret values never get into the
index (as in `Vault.search`): nothing here opens a sealed field.

`parse_entry` reads a line such as “pass DEMO-12 DEMO-13”, “DEMO-12 verified”, “!7 opened
yesterday” or “note nightly run triage” as a journal entry, with the words of the journal
layout in use (the same words `daykeeper-cli` takes) and verbs in either UI language. A
line whose items look like a token or a key is not an entry (the rule of `daykeeper-cli`):
the journal is plain text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as Date, timedelta

from .bookmarks import URL_KEYS, _key_in, entry_login, has_secret
from .hookrules import looks_secret
from .i18n import _, fmt_day, variants
from .journal import ISSUE_KEY_RE, MR_URL_RE, normalize_item, normalize_mr, today as journal_today
from .journal_layout import ROLE_MR, ROLE_NOTE, ROLE_PLAIN, Layout
from .vault import MAIN_GROUP, main_group_label

KINDS = ("entry", "bookmark", "note", "routine", "template", "command")
NOTE_DAYS = 30  # notes older than this stay out of the index

# action id → UI key; the first action of an item is what Enter does
ACTION_LABELS: dict[str, str] = {
    "copy_password": "Copy password",
    "copy_login": "Copy login",
    "copy_code": "Copy 2FA code",
    "open_site": "Open site",
    "open": "Open",
    "run": "Run",
    "fill": "Fill in and copy",
    "show": "Show in the app",
}

# command id → (what the line says, UI key of the hint)
COMMANDS: dict[str, tuple[str, str]] = {
    "lock": ("/lock", "Lock the app"),
    "digest": ("/digest", "Morning digest"),
    "today": ("/today", "Today in the journal"),
    "clip": ("/clip", "Clipboard history"),
    "settings": ("/settings", "Open the settings"),
}

KIND_LABELS: dict[str, str] = {  # what the second line of a result says it is (UI keys)
    "bookmark": "Bookmark",
    "note": "Note",
    "routine": "Routine",
    "template": "Template",
    "command": "Command",
}

OTP_KEYS = ("2fa", "totp", "otp", "otpauth")  # short field names of a two-factor secret
OTP_PRESET = "2FA key"  # the field of the app's own templates (vault.KIND_TEMPLATES), in any UI language


def _is_otp_field(name: str) -> bool:
    folded = name.strip().casefold()
    return folded in OTP_KEYS or folded in {v.casefold() for v in variants(OTP_PRESET)}


@dataclass
class Item:
    kind: str  # one of KINDS
    id: str
    title: str
    subtitle: str
    keywords: list[str]  # what the match looks through, lower-cased
    actions: list[str]  # action ids in order; the first is the default

    @property
    def key(self) -> str:
        """Unique across kinds: what `quick.recent` in settings.json keeps."""
        return f"{self.kind}:{self.id}"


def _fold(text: str) -> str:
    """Lower case for matching in any language; ё reads as е, as people type it."""
    return text.casefold().replace("ё", "е")


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[\W_]+", text) if w]


# TODO: import bookmarks.entry_url instead once fix/entry-url-secret is merged (it stops opening secret fields)
def _entry_url(entry) -> str:
    """bookmarks.entry_url without reading a secret field: that one opens every value it
    looks at, and the index opens none."""
    for f in entry.fields:
        if f.secret:
            continue
        value = f.value.strip()
        if value and (_key_in(f.key, URL_KEYS) or value.startswith(("http://", "https://"))):
            return value
    return ""


def _entry_item(entry) -> Item:
    group = entry.group if entry.group != MAIN_GROUP else main_group_label()
    login = entry_login(entry)
    opened = [f.value for f in entry.fields if not f.secret and f.value.strip()]  # never a secret one: it stays sealed
    actions = []
    if has_secret(entry):
        actions.append("copy_password")
    if login:
        actions.append("copy_login")
    if any(f.secret and f.filled and _is_otp_field(f.key) for f in entry.fields):
        actions.append("copy_code")
    if _entry_url(entry):
        actions.append("open_site")
    actions.append("show")
    return Item(
        "entry", entry.id, entry.name or _("(no name)"),
        " · ".join(x for x in (entry.kind_label, group, login) if x),
        [_fold(x) for x in (entry.name, entry.kind_label, group, *[f.key for f in entry.fields], *opened) if x],
        actions,
    )


def _bookmark_item(bm, vault) -> Item:
    actions = ["open"] if bm.url else []
    entry = vault.entry_by_id(bm.entry_id) if bm.entry_id else None
    if entry is not None:
        if has_secret(entry):
            actions.append("copy_password")
        if entry_login(entry):
            actions.append("copy_login")
    actions.append("show")
    return Item(
        "bookmark", bm.id, bm.title or bm.host or bm.url,
        " · ".join(x for x in (_(KIND_LABELS["bookmark"]), bm.host, bm.group) if x),
        [_fold(x) for x in (bm.title, bm.host, bm.group) if x],
        actions,
    )


def _note_title(note) -> str:
    if note.title.strip():
        return note.title.strip()
    first = next((i.text for i in note.items if i.text.strip()), "") or next((ln for ln in note.text.splitlines() if ln.strip()), "")
    return first.strip()[:80] or _("(no title)")


def _note_item(note) -> Item:
    return Item(
        "note", note.id, _note_title(note),
        f"{_(KIND_LABELS['note'])} · {fmt_day(note.date)}",
        [_fold(x) for x in (note.title, *(i.text for i in note.items)) if x.strip()],
        ["show"],
    )


def _template_fields(tpl) -> tuple[str, str]:
    """A template is anything with an id and a name: an object or a dict (templates.py)."""
    if isinstance(tpl, dict):
        return str(tpl.get("id", "")), str(tpl.get("name", ""))
    return str(getattr(tpl, "id", "")), str(getattr(tpl, "name", ""))


def index(vault, templates=(), layout: Layout | None = None, today: str | None = None) -> list[Item]:
    """Everything the quick window can find, in the order of a tie: entries, bookmarks,
    notes of the last NOTE_DAYS days, routines, templates, commands. Built when the window
    opens, not kept. `layout` gives the /today command the journal words, so “review” or a
    column label finds it."""
    items = [_entry_item(e) for e in sorted(vault.entries, key=lambda e: e.order)]
    items += [_bookmark_item(b, vault) for b in vault.bookmarks.items]
    since = (Date.fromisoformat(today or journal_today()) - timedelta(days=NOTE_DAYS)).isoformat()
    items += [_note_item(n) for n in sorted(vault.notes.notes, key=lambda n: n.date, reverse=True) if n.date >= since and not n.is_empty()]
    items += [
        Item("routine", r.id, r.name or r.target, _(KIND_LABELS["routine"]), [_fold(x) for x in (r.name,) if x], ["run", "show"])
        for r in vault.routines.ordered()
    ]
    for tpl in templates:
        tid, name = _template_fields(tpl)
        if tid and name:
            items.append(Item("template", tid, name, _(KIND_LABELS["template"]), [_fold(name)], ["fill"]))
    journal_words: list[str] = []
    if layout is not None:
        for word, label in layout.statuses():
            journal_words += [word, *variants(label)]
    for cid, (text, hint) in COMMANDS.items():
        words = [text, cid, *variants(hint)] + (journal_words if cid == "today" else [])
        items.append(Item("command", cid, text, _(hint), [_fold(w) for w in words], ["run"]))
    return items


def _tier(item: Item, query: str) -> int:
    """3 — the title starts with the query; 2 — every word of the query starts a word;
    1 — every word of the query is somewhere; 0 — no match."""
    title = _fold(item.title)
    if title.startswith(query):
        return 3
    hay = [title, *item.keywords]
    words = [w for h in hay for w in _words(h)]
    tier = 2
    for term in query.split():
        bare = term.lstrip("/")
        if bare and any(w.startswith(bare) for w in words):
            continue
        if any(term in h for h in hay):
            tier = 1
            continue
        return 0
    return tier


def rank(items: list[Item], query: str, recent: list[str], limit: int = 8) -> list[Item]:
    """Prefix of the title > prefix of a word > substring; a recent item is lifted by one
    and a half steps (it beats the next step up, not two). `recent` holds `Item.key`s (or
    bare ids), the newest first. Without a query — the recent items, then the commands."""
    order = {k: n for n, k in enumerate(recent)}

    def recency(item: Item) -> int | None:
        return order.get(item.key, order.get(item.id))

    q = _fold(query.strip())
    if not q:
        lifted = sorted((i for i in items if recency(i) is not None), key=lambda i: recency(i) or 0)
        rest = [i for i in items if i.kind == "command" and i not in lifted]
        return (lifted + rest)[:limit]
    scored = []
    for n, item in enumerate(items):
        tier = _tier(item, q)
        if not tier:
            continue
        r = recency(item)
        lift = 0.0 if r is None else 1.5 - min(r, 99) / 1000  # the newer, the higher
        scored.append((-(tier + lift), n, item))
    scored.sort(key=lambda s: (s[0], s[1]))
    return [s[2] for s in scored[:limit]]


# ---------------------------------------------------------------- quick journal entry

# Verbs that say what happened to the items: UI key → the journal word it stands for (a
# word of the tester's layout, or an old alias; see journal_layout.ALIASES). The Russian
# forms come from locale_ru through variants(): there a translation lists every form,
# comma-separated (“проверил, проверила, …”).
VERBS: dict[str, str] = {
    "verified": "pass",
    "failed": "fail",
    "filed": "bug",
    "reviewed": "review",
    "opened": "mr-open",
    "merged": "mr-merged",
}
DAY_WORDS = {"today": 0, "yesterday": 1}  # UI key → days back


@dataclass
class QuickEntry:
    status: str  # a column or kind id of the layout
    items: list[str]
    date: str
    label: str  # what the preview says
    section: str = "column"  # "column" — Day.add; "activity" — Day.add_activity


def _forms(key: str) -> set[str]:
    out: set[str] = set()
    for text in variants(key):
        out |= {_fold(x.strip()) for x in text.split(",") if x.strip()}
    return out


def _is_reference(token: str) -> bool:
    """An issue key or tracker link, an MR (!7 or a link), or any web link."""
    t = token.strip()
    if ISSUE_KEY_RE.fullmatch(normalize_item(t)):
        return True
    return bool(re.fullmatch(r"!\d{1,6}", t) or MR_URL_RE.search(t) or re.match(r"https?://\S+$", t))


def _day_shift(token: str) -> int | None:
    word = _fold(token)
    for key, back in DAY_WORDS.items():
        if word in _forms(key):
            return back
    return None


def _target(word: str, layout: Layout) -> tuple[str, str] | None:
    """A journal word or a verb → ("column" | "activity", id) of `layout`."""
    found = layout.resolve(word)
    if found is not None:
        return found
    folded = _fold(word)
    for key, status in VERBS.items():
        if folded in _forms(key):
            return layout.resolve(status)
    return None


def parse_entry(text: str, layout: Layout, today: str) -> QuickEntry | None:
    """A typed line as a journal entry; None when it means nothing for the journal (the
    window keeps it as a search).

    The forms: a journal word first and items after it (“pass DEMO-12 DEMO-13”, “note any
    text”), or items with a journal word or a verb anywhere among them (“DEMO-12 verified”,
    “!7 opened”). A day word at either end (“yesterday”, “вчера”) moves it a day back.
    None too when an item looks like a token or a key (`hookrules.looks_secret`, as
    `daykeeper-cli` refuses it): the line stays a search and nothing reaches the journal."""
    tokens = text.split()
    shift = 0
    for end in (-1, 0):
        if tokens and (back := _day_shift(tokens[end])) is not None:
            shift = back
            tokens.pop(end)
            break
    if not tokens:
        return None
    first = _target(tokens[0], layout)
    if first is not None and len(tokens) > 1:
        where, ident = first
        rest = tokens[1:]
        if all(_is_reference(t) for t in rest):
            items = rest
        elif where == "activity" and layout.role(ident) in (ROLE_NOTE, ROLE_PLAIN):
            items = [" ".join(rest)]  # a phrase: one item, as `daykeeper-cli note "nightly run triage"`
        else:  # “pass manager”: a search for an entry, not a column item
            return None
    else:
        found = [(n, t) for n, tok in enumerate(tokens) if (t := _target(tok, layout)) is not None]
        if len(found) != 1:
            return None
        n, (where, ident) = found[0]
        items = tokens[:n] + tokens[n + 1:]
        if not items or not all(_is_reference(t) for t in items):
            return None
    if any(looks_secret(x) for x in items):
        return None
    clean: list[str] = []
    for raw in items:
        item = normalize_mr(raw) if where == "activity" and layout.role(ident) == ROLE_MR else normalize_item(raw)
        if item and item not in clean:
            clean.append(item)
    if not clean:
        return None
    day = (Date.fromisoformat(today) - timedelta(days=shift)).isoformat()
    target = layout.column(ident) if where == "column" else layout.kind(ident)
    name = _(target.label) if target is not None else ident
    when = _("today") if shift == 0 else _("yesterday") if shift == 1 else fmt_day(day)
    return QuickEntry(ident, clean, day, f"{name} ← {', '.join(clean)} · {when}", where)


def write_entry(entry: QuickEntry, journal) -> int:
    """File the entry into the journal (the open vault's); the number of new items. The
    caller marks the journal dirty, so Ctrl+Z takes it back. An item that looks like a
    token or a key is not filed (an entry built by hand skips the gate of `parse_entry`)."""
    day = journal.get(entry.date, create=True)
    added = 0
    for item in entry.items:
        if looks_secret(item):
            continue
        if day.add_activity(entry.status, item) if entry.section == "activity" else day.add(entry.status, item):
            added += 1
    return added
