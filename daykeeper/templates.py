"""Text templates: a verification comment, a defect report, an MR description — any text with
placeholders filled from where it is inserted (the journal day, a note, the search window).

    {key} {key_url}       the issue key of the selected line and its tracker link
    {mr} {mr_url}         the MR and its link
    {today}               the date, YYYY-MM-DD
    {date:%d.%m.%Y}       the date in any strftime format
    {me}                  the GitLab login of the integration
    {clipboard}           the clipboard text
    {note_title} {note_items}   the note the template is inserted from
    {?Environment}        asked in a form before the text is made; several questions — one form

The values come from the caller (`context`); only the dates are made here. A brace this
module does not know stays as typed. Stored in settings.json (key `templates`), as the
journal layout is. No tkinter here."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from .i18n import _

PLACES = ("journal", "notes", "quick")
PLACE_LABELS = {"journal": "In the journal", "notes": "In notes", "quick": "In the search window"}
DEFAULT_PLACES = ("journal", "quick")
PLACEHOLDERS = ("key", "key_url", "mr", "mr_url", "today", "date", "me", "clipboard", "note_title", "note_items")
PLACEHOLDER_RE = re.compile(r"\{(\?[^}]+|[a-z_]+(?::[^}]+)?)\}")


@dataclass
class Template:
    id: str
    name: str
    text: str
    places: list[str] = field(default_factory=lambda: list(DEFAULT_PLACES))
    open_link: bool = False  # after the text is copied, open {key_url}

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "text": self.text, "places": list(self.places), "open_link": self.open_link}


def questions(t: Template) -> list[str]:
    """The {?…} questions of the template, each once, in the order they appear."""
    seen: list[str] = []
    for m in PLACEHOLDER_RE.finditer(t.text):
        body = m.group(1)
        if body.startswith("?"):
            label = body[1:].strip()
            if label and label not in seen:
                seen.append(label)
    return seen


def render(t: Template, context: dict[str, str], answers: dict[str, str], now: datetime) -> str:
    """The text with the placeholders filled. A question without an answer becomes empty;
    a name neither `context` nor this module knows stays as typed; `{date:…}` is strftime
    (a format the system rejects stays as typed too)."""

    def fill(m: re.Match[str]) -> str:
        body = m.group(1)
        if body.startswith("?"):
            return answers.get(body[1:].strip(), "")
        name, _sep, spec = body.partition(":")
        if spec:
            if name != "date":
                return m.group(0)
            try:
                return now.strftime(spec)
            except ValueError:
                return m.group(0)
        if name in context:
            return context[name]
        if name in ("today", "date"):
            return now.strftime("%Y-%m-%d")
        return m.group(0)

    return PLACEHOLDER_RE.sub(fill, t.text)


def load(settings: dict) -> list[Template]:
    """The templates from settings.json; an entry without a name or a text, or that is not an
    object, is dropped (the file may be edited by hand). No key — an empty list: the caller
    seeds `presets()` when `"templates" not in settings`."""
    raw = settings.get("templates")
    if not isinstance(raw, list):
        return []
    out: list[Template] = []
    seen: set[str] = set()
    for row in raw:
        if not isinstance(row, dict):
            continue
        name, text = row.get("name"), row.get("text")
        if not isinstance(name, str) or not isinstance(text, str) or not name.strip() or not text.strip():
            continue
        ident = str(row.get("id") or "").strip()
        if not ident or ident in seen:
            ident = uuid.uuid4().hex
        seen.add(ident)
        places = row.get("places")
        if isinstance(places, list):
            chosen = [p for p in PLACES if p in places]  # known places only, in their own order
        else:
            chosen = list(DEFAULT_PLACES)
        out.append(Template(ident, name.strip(), text, chosen, bool(row.get("open_link", False))))
    return out


def save(settings: dict, items: list[Template]) -> dict:
    """A copy of `settings` with the templates in it; the other keys are kept as they are."""
    return {**settings, "templates": [t.to_dict() for t in items]}


def _ask(text: str, labels: dict[str, str]) -> str:
    """Put the translated question labels into a translated preset: the catalogue keeps
    `{?env}` as it is, so both languages share the placeholder names."""
    return re.sub(r"\{\?(\w+)\}", lambda m: "{?" + labels.get(m.group(1), m.group(1)) + "}", text)


def presets() -> list[Template]:
    """The ready-made templates in the UI language; copied into the settings the first time
    the section is opened, and rewritten there as the user likes."""
    labels = {"env": _("Environment"), "result": _("Result"), "summary": _("What changed")}
    return [
        Template(
            "verification",
            _("Verification comment"),
            _ask(_("Tested on {?env}, {date:%d.%m.%Y}.\nResult: {?result}\n{key_url}"), labels),
            ["journal", "quick"],
            open_link=True,
        ),
        Template(
            "defect",
            _("Defect report"),
            _ask(_("Found while checking {key_url}\n\nSteps to reproduce:\n1. \n\nExpected result:\n\nActual result:\n\nEnvironment: {?env}"), labels),
            ["journal", "notes", "quick"],
        ),
        Template(
            "mr",
            _("MR description"),
            _ask(_("## What\n{?summary}\n\n## Why\n{key_url}\n\n## How to check\n"), labels),
            ["journal", "notes", "quick"],
        ),
    ]
