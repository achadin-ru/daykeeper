"""Bookmarks: tiles with links to resources, grouped, optionally tied to a vault entry.

Stored inside the encrypted vault (key `bookmarks`): addresses of internal services are
worth hiding too. A bookmark tied to an entry shows its login and copies its password,
so the password itself lives only in the entry. Pure (no tkinter)."""

from __future__ import annotations

import base64
import re
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .i18n import variants
from .settings import number

# Tile colours: (code, UI label key, colour of the avatar). "" — the colour of the site,
# blue until the site has told it.
DEFAULT_COLOUR = "#2563EB"
SITE_RECHECK_SEC = 30 * 86400  # a site that gave an icon is asked again a month later
SITE_RETRY_SEC = 86400  # a site that gave nothing — a day later
TILE_COLORS: list[tuple[str, str, str]] = [
    ("", "Site colour", DEFAULT_COLOUR),
    ("blue", "Blue", DEFAULT_COLOUR),
    ("green", "Green", "#16A34A"),
    ("teal", "Teal", "#0D9488"),
    ("orange", "Orange", "#EA580C"),
    ("red", "Red", "#DC2626"),
    ("purple", "Purple", "#7C3AED"),
    ("pink", "Pink", "#DB2777"),
    ("grey", "Grey", "#4B5563"),
]
TILE_COLOR = {code: colour for code, _label, colour in TILE_COLORS}

# Field names (in any UI language) that hold a login, a password or an address.
LOGIN_KEYS = ("login", "mail", "user", "username", "email")
SECRET_KEYS = ("password", "token", "PIN")
URL_KEYS = ("site", "service / URL", "url", "URL")


def _new_id() -> str:
    return secrets.token_hex(6)


def normalize_url(url: str) -> str:
    """Add https:// to a bare host (“gitlab.example.com/x”); keep any other scheme."""
    url = url.strip()
    if not url:
        return ""
    if "://" not in url and not url.startswith(("mailto:", "file:")):
        url = "https://" + url
    return url


WEB_SCHEMES = ("http", "https", "mailto")


def is_web(url: str) -> bool:
    """Does a click open this in the browser. Not file: or any other scheme: the system
    hands those to whatever program is registered for them — that can start a program."""
    return urlparse(url).scheme.lower() in WEB_SCHEMES


def host_of(url: str) -> str:
    """The host without “www.” — what a tile shows under its title."""
    host = urlparse(normalize_url(url)).hostname or ""
    return host[4:] if host.startswith("www.") else host


def _key_in(key: str, names: tuple[str, ...]) -> bool:
    k = key.strip().lower()
    return any(k in {v.lower() for v in variants(n)} for n in names)


def entry_login(entry) -> str:
    """The login of a vault entry: a non-secret field named like a login or mail."""
    for f in entry.fields:
        if not f.secret and f.value.strip() and _key_in(f.key, LOGIN_KEYS):
            return f.value.strip()
    return ""


def has_secret(entry) -> bool:
    """Does the entry hold a password or token — without opening it."""
    return any(f.secret and f.filled for f in entry.fields)


def entry_secret(entry) -> str:
    """The password of a vault entry: a secret field named like a password or token,
    otherwise the first secret field with a value."""
    secret_fields = [f for f in entry.fields if f.secret and f.filled]  # opened below, one
    for f in secret_fields:
        if _key_in(f.key, SECRET_KEYS):
            return f.value
    return secret_fields[0].value if secret_fields else ""


def entry_url(entry) -> str:
    """An address kept in a vault entry, if any."""
    for f in entry.fields:
        value = f.value.strip()
        if not f.secret and value and (_key_in(f.key, URL_KEYS) or value.startswith(("http://", "https://"))):
            return value
    return ""


@dataclass
class Bookmark:
    title: str = ""
    url: str = ""
    group: str = ""  # "" — no group
    icon: str = ""  # an emoji or one or two letters; empty — the first letter of the title
    color: str = ""
    entry_id: str = ""  # a tied vault entry; empty — none
    note: str = ""
    order: int = 0
    id: str = field(default_factory=_new_id)
    created: float = field(default_factory=time.time)
    opened: float = 0.0  # last time it was opened from the app
    favicon: str = ""  # the site icon, a base64 PNG (siteicon.ICON_SIZE square)
    site_colour: str = ""  # the colour of the site, "#RRGGBB"
    site_checked: float = 0.0  # when the site was last asked for them; 0 — never
    # the icon at the size a tile shows it, a base64 PNG, and that size in screen pixels:
    # Tk decodes a PNG itself in no time, shrinking one in Python takes 14 ms per tile.
    # Empty in older files — made in the background on the first visit
    favicon_small: str = ""
    favicon_small_px: int = 0

    @property
    def host(self) -> str:
        return host_of(self.url)

    def avatar(self) -> str:
        if self.icon.strip():
            return self.icon.strip()[:2]
        source = self.title.strip() or self.host
        return source[:1].upper() if source else "•"

    def colour(self) -> str:
        if self.color in TILE_COLOR and self.color:
            return TILE_COLOR[self.color]
        return self.site_colour or DEFAULT_COLOUR

    def needs_site_info(self, now: float | None = None) -> bool:
        """Time to ask the site for its icon and colour: never asked, or long enough ago."""
        if not self.url.startswith(("http://", "https://")):
            return False
        if not self.site_checked:
            return True
        age = (now or time.time()) - self.site_checked
        return age > (SITE_RECHECK_SEC if self.favicon else SITE_RETRY_SEC)

    def forget_site_info(self) -> None:
        """The address changed: the old site's look no longer applies."""
        self.favicon, self.site_colour, self.site_checked = "", "", 0.0
        self.favicon_small, self.favicon_small_px = "", 0

    def set_small_icon(self, png: bytes, size: int) -> None:
        """The stored icon shrunk to `size` (a PNG), kept so the tile never shrinks it again."""
        self.favicon_small = base64.b64encode(png).decode("ascii") if png else ""
        self.favicon_small_px = size if png else 0

    def small_icon_at(self, size: int) -> str:
        """The base64 PNG of the icon at `size`, "" when it is not there (yet)."""
        return self.favicon_small if self.favicon_small and self.favicon_small_px == size else ""

    def apply_site_info(self, url: str, icon: bytes | None, colour: str, small: bytes, size: int, now: float) -> bool:
        """What the site answered (asked in the background): its icon, already shrunk to
        `size` as `small`, and its colour. False — the bookmark was re-addressed meanwhile,
        the answer is for the old site and is dropped."""
        if self.url != url:
            return False
        self.site_checked = now
        if icon:
            self.favicon = base64.b64encode(icon).decode("ascii")
            self.set_small_icon(small, size)
        if colour:
            self.site_colour = colour
        return True

    def apply_small_icon(self, favicon: str, small: bytes, size: int) -> bool:
        """The stored icon shrunk in the background. False — the icon changed meanwhile,
        or it could not be shrunk."""
        if self.favicon != favicon or not small:
            return False
        self.set_small_icon(small, size)
        return True

    def matches(self, query: str) -> bool:
        q = query.strip().lower()
        return not q or any(q in h.lower() for h in (self.title, self.url, self.group, self.note))

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "url": self.url, "group": self.group, "icon": self.icon,
            "color": self.color, "entry_id": self.entry_id, "note": self.note, "order": self.order,
            "created": self.created, "opened": self.opened,
            "favicon": self.favicon, "site_colour": self.site_colour, "site_checked": self.site_checked,
            "favicon_small": self.favicon_small, "favicon_small_px": self.favicon_small_px,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Bookmark:
        color = str(d.get("color", ""))
        return cls(
            title=str(d.get("title", "")),
            url=str(d.get("url", "")),
            group=str(d.get("group", "")),
            icon=str(d.get("icon", "")),
            color=color if color in TILE_COLOR else "",
            entry_id=str(d.get("entry_id", "")),
            note=str(d.get("note", "")),
            order=number(int, d.get("order", 0), 0),
            id=str(d.get("id") or _new_id()),
            created=number(float, d.get("created"), None) or time.time(),
            opened=number(float, d.get("opened", 0.0), 0.0),
            favicon=str(d.get("favicon", "")),
            site_colour=str(d.get("site_colour", "")) if re.fullmatch(r"#[0-9A-Fa-f]{6}", str(d.get("site_colour", ""))) else "",
            site_checked=number(float, d.get("site_checked", 0.0), 0.0),
            favicon_small=str(d.get("favicon_small", "")),
            favicon_small_px=number(int, d.get("favicon_small_px", 0) or 0, 0),
        )


class Bookmarks:
    """All bookmarks of the vault and the order of their groups."""

    def __init__(self, items: list[Bookmark] | None = None, groups: list[str] | None = None):
        self.items: list[Bookmark] = list(items or [])
        self.groups: list[str] = [g for g in (groups or []) if g]
        for b in self.items:  # a group a bookmark refers to always exists
            if b.group and b.group not in self.groups:
                self.groups.append(b.group)

    def get(self, bookmark_id: str) -> Bookmark | None:
        return next((b for b in self.items if b.id == bookmark_id), None)

    def add(self, title: str, url: str, group: str = "", **kw) -> Bookmark:
        group = group.strip()
        if group and group not in self.groups:
            self.groups.append(group)
        order = max((b.order for b in self.items if b.group == group), default=-1) + 1
        bm = Bookmark(title=title.strip(), url=normalize_url(url), group=group, order=order, **kw)
        self.items.append(bm)
        return bm

    def update(self, bm: Bookmark, **changes) -> None:
        """Change fields; a new group goes to the end of that group."""
        group = str(changes.pop("group", bm.group)).strip()
        if "url" in changes:
            changes["url"] = normalize_url(str(changes["url"]))
            if host_of(changes["url"]) != bm.host or changes["url"].split(":")[0] != bm.url.split(":")[0]:
                bm.forget_site_info()
        for key, value in changes.items():
            setattr(bm, key, value)
        if group != bm.group:
            if group and group not in self.groups:
                self.groups.append(group)
            bm.group = group
            bm.order = max((b.order for b in self.items if b.group == group and b is not bm), default=-1) + 1

    def remove(self, bm: Bookmark) -> None:
        if bm in self.items:
            self.items.remove(bm)

    def in_group(self, group: str | None) -> list[Bookmark]:
        """One group in manual order; None — all bookmarks, group by group."""
        if group is not None:
            return sorted((b for b in self.items if b.group == group), key=lambda b: b.order)
        out: list[Bookmark] = []
        for g in ["", *self.groups]:
            out.extend(self.in_group(g))
        return out

    def search(self, query: str) -> list[Bookmark]:
        return [b for b in self.in_group(None) if b.matches(query)]

    def move(self, bm: Bookmark, delta: int) -> bool:
        same = self.in_group(bm.group)
        idx = same.index(bm)
        target = idx + delta
        if not 0 <= target < len(same):
            return False
        other = same[target]
        bm.order, other.order = other.order, bm.order
        if bm.order == other.order:  # orders from an older file may coincide
            for i, b in enumerate(same):
                b.order = i
            bm.order, other.order = other.order, bm.order
        return True

    def rename_group(self, old: str, new: str) -> None:
        new = new.strip()
        if old not in self.groups or not new or (new != old and new in self.groups):
            raise ValueError(new)
        self.groups[self.groups.index(old)] = new
        for b in self.items:
            if b.group == old:
                b.group = new

    def remove_group(self, name: str) -> None:
        """Delete a group; its bookmarks stay, without a group."""
        if name in self.groups:
            self.groups.remove(name)
        for b in self.items:
            if b.group == name:
                b.group = ""
                b.order = max((x.order for x in self.items if x.group == ""), default=-1) + 1

    def forget_entry(self, entry_id: str) -> int:
        """A vault entry was deleted: untie the bookmarks that used it."""
        n = 0
        for b in self.items:
            if b.entry_id == entry_id:
                b.entry_id = ""
                n += 1
        return n

    def suggest_entry(self, url: str, entries) -> str:
        """The id of the vault entry whose address has the same host, if exactly one does."""
        host = host_of(url)
        if not host:
            return ""
        hits = [e.id for e in entries if (u := entry_url(e)) and host_of(u) == host]
        return hits[0] if len(hits) == 1 else ""

    def to_dict(self) -> dict:
        return {"groups": list(self.groups), "items": [b.to_dict() for b in self.items]}

    @classmethod
    def from_dict(cls, d) -> Bookmarks:
        if not isinstance(d, dict):
            return cls()
        items = [Bookmark.from_dict(x) for x in d.get("items", []) if isinstance(x, dict)]
        return cls(items, [str(g) for g in d.get("groups", []) if str(g)])
