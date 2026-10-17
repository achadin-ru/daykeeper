"""Clipboard history kept sealed: every text is sealed with the vault's field key (`Sealer`)
the moment it is taken, so the history holds ciphertext only — in memory, in every
autosave and in the vault file. Pure: no tkinter, no clipboard access; the window reads the
clipboard and hands the text here.

A locked vault has nothing to seal with, and then nothing is recorded: honester than
piling the texts up in the open until the next unlock.

Vault key `clipboard`: [{"ts": 1760000000.0, "sealed": "<base64>", "pinned": false}, …],
newest first."""

from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass, field
from typing import Protocol

from cryptography.exceptions import InvalidTag

from . import hookrules

MAX_CHARS = 20_000  # a longer text is not recorded (a dump, a log, a file pasted whole)
LIMITS = (20, 50, 100)  # the lengths offered in the settings
DEFAULT_LIMIT = 50


class SealerLike(Protocol):
    """What the history needs of `vault.Sealer`."""

    def seal(self, text: str) -> bytes: ...

    def open(self, blob: bytes) -> str: ...


def usable(sealer: SealerLike | None) -> bool:
    """Can `sealer` seal right now: not missing and its key not zeroed by a lock."""
    if sealer is None:
        return False
    key = getattr(sealer, "key", None)
    return key is None or any(key)


@dataclass
class ClipItem:
    """One recorded text: when it was taken, its sealed bytes, whether it is pinned."""

    ts: float
    sealed: bytes
    pinned: bool = False

    def text(self, sealer: SealerLike | None) -> str:
        """The plain text for the moment it is shown or copied back; "" when the vault is
        locked or the bytes do not open with this key."""
        if sealer is None or not usable(sealer):
            return ""
        try:
            return sealer.open(self.sealed)
        except (InvalidTag, ValueError, RuntimeError):
            return ""

    def to_dict(self) -> dict:
        return {"ts": self.ts, "sealed": base64.b64encode(self.sealed).decode("ascii"), "pinned": self.pinned}

    @staticmethod
    def from_dict(d: dict) -> ClipItem | None:
        """None for a row that is not a sealed item (a damaged file, a hand edit)."""
        sealed = d.get("sealed")
        if not isinstance(sealed, str) or not sealed:
            return None
        try:
            blob = base64.b64decode(sealed, validate=True)
        except (binascii.Error, ValueError):
            return None
        try:
            ts = float(d.get("ts") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        return ClipItem(ts, blob, bool(d.get("pinned", False)))


@dataclass
class ClipHistory:
    """The recorded texts, newest first."""

    items: list[ClipItem] = field(default_factory=list)
    # (sealed bytes of the newest item, SHA-256 of its text): lets `add` spot a repeat
    # without opening the newest item. Memory only, never stored; it counts only while
    # that item is still the newest, so a removal or a fresh load falls back to opening.
    _last: tuple[bytes, bytes] | None = field(default=None, repr=False, compare=False)

    def add(self, text: str, sealer: SealerLike | None, now: float, limit: int = DEFAULT_LIMIT) -> bool:
        """Record `text` at the top. False — nothing recorded: empty, longer than
        MAX_CHARS, the same as the newest item, or the vault is locked (the sealer is
        missing, zeroed or refuses). Over `limit` the oldest unpinned items go; pinned
        ones, and the text just taken, stay even past it.

        Token-looking text is NOT filtered here: skipping it (`looks_secret`) is the
        caller's rule, applied before the call."""
        if sealer is None or not usable(sealer) or not text or not text.strip() or len(text) > MAX_CHARS:
            return False
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        if self.items and self._is_newest(text, digest, sealer):
            return False
        try:
            sealed = sealer.seal(text)
        except (ValueError, RuntimeError):
            return False
        self.items.insert(0, ClipItem(float(now), sealed))
        self._last = (sealed, digest)
        self._trim(limit)
        return True

    def _is_newest(self, text: str, digest: bytes, sealer: SealerLike) -> bool:
        """Is `text` the newest item: by the remembered digest when it still belongs to
        the newest item, else (just loaded, the newest removed) by opening it once."""
        newest = self.items[0]
        if self._last is not None and self._last[0] is newest.sealed:
            return self._last[1] == digest
        shown = newest.text(sealer)
        if not shown:
            return False  # does not open with this key: compare again next time
        self._last = (newest.sealed, hashlib.sha256(shown.encode("utf-8")).digest())
        return self._last[1] == digest

    def _trim(self, limit: int) -> None:
        limit = max(1, int(limit))
        excess = len(self.items) - limit
        if excess <= 0:
            return
        for i in range(len(self.items) - 1, 0, -1):  # the oldest first; the text just taken stays
            if excess <= 0:
                break
            if not self.items[i].pinned:
                del self.items[i]
                excess -= 1

    def remove(self, index: int) -> bool:
        """Delete the item at `index`; False when there is none."""
        if not 0 <= index < len(self.items):
            return False
        del self.items[index]
        return True

    def pin(self, index: int, on: bool = True) -> bool:
        """Pin (or unpin) the item at `index`; False when there is none."""
        if not 0 <= index < len(self.items):
            return False
        self.items[index].pinned = bool(on)
        return True

    def clear(self, keep_pinned: bool = True) -> None:
        """Forget the history; the pinned items stay unless `keep_pinned` is False."""
        self.items = [item for item in self.items if item.pinned] if keep_pinned else []

    def to_list(self) -> list[dict]:
        """The value of the vault key `clipboard`: sealed bytes only, base64."""
        return [item.to_dict() for item in self.items]

    @staticmethod
    def from_list(rows: object) -> ClipHistory:
        """Read the vault key `clipboard`; rows that are not sealed items are skipped."""
        if not isinstance(rows, list):
            return ClipHistory()
        items = [ClipItem.from_dict(row) for row in rows if isinstance(row, dict)]
        return ClipHistory([item for item in items if item is not None])


def looks_secret(text: str) -> bool:
    """Reuses the token shape the hook refuses to write into the journal (hookrules)."""
    return hookrules.looks_secret(text)
