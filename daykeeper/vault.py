"""Encrypted vault: one file, one master password, AES-256-GCM + Argon2id.

File layout (all integers big-endian):
    4  bytes  magic  b"PKV1"  (kept from the first release for compatibility)
    16 bytes  Argon2id salt
    4  bytes  iterations
    4  bytes  memory_cost (KiB)
    4  bytes  lanes
    12 bytes  AES-GCM nonce
    N  bytes  ciphertext (UTF-8 JSON inside); the header is the AAD
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from .i18n import _
from .bookmarks import Bookmarks
from .integrations import Integrations
from .journal import Journal
from .notes import Notes
from .routines import Routine, Routines
from .security import SecurityPrefs
from .settings import app_dir, private

UNDO_DOMAINS = ("vault", "journal", "notes", "bookmarks", "routines")  # what Ctrl+Z puts back
RUN_FIELDS = ("last_run", "last_code", "target_hash")  # a routine's run state and the script it accepted: not the user's edit
SITE_FIELDS = ("opened", "favicon", "site_colour", "site_checked", "favicon_small", "favicon_small_px")  # a bookmark's look, fetched

MAGIC = b"PKV2"  # the secret fields are sealed inside the content (Sealer); written by 1.1+
OLD_MAGICS = (b"PKV1",)  # read and converted on the next save; 1.0 cannot read PKV2 — it refuses, not wipes
MAGICS = (MAGIC, *OLD_MAGICS)
SALT_LEN = 16
NONCE_LEN = 12
KEY_LEN = 32
HEADER_FMT = ">4s16sIII12s"
HEADER_LEN = struct.calcsize(HEADER_FMT)

# Default Argon2id parameters: about 0.1–0.2 s on an ordinary PC (t=3 took 55 ms on a
# 2026 desktop, which is on the low side for the cost of a guess; the derivation runs on
# a thread, so the window does not feel the doubling). Files written with t=3 open as before.
DEFAULT_ITERATIONS = 6
DEFAULT_MEMORY_KIB = 64 * 1024
DEFAULT_LANES = 4

# Bounds for Argon2id parameters read from a file header. The header is checked only
# after the key is derived, so a forged header must not be able to make the app allocate
# gigabytes or spin for minutes before the password is even tested. At the old bounds
# (1 GiB, 64 passes) a forged header froze the window for 13 s per try; at these a forged
# header costs at most ~2.3 s per try (measured). Every file the app writes uses the defaults.
MAX_ITERATIONS = 2 * DEFAULT_ITERATIONS
MAX_MEMORY_KIB = 4 * DEFAULT_MEMORY_KIB  # 256 MiB
MAX_LANES = 2 * DEFAULT_LANES

# Python keeps an unpaired surrogate in a str (a Windows file name may carry one, and it
# comes in through a file dialog or the clipboard); strict UTF-8 refuses it, and one such
# character made every autosave raise until the user found it. Inside the vault the text
# goes through as it is and comes back the same; a readable export replaces it (session.py).
TEXT_ERRORS = "surrogatepass"

BACKUP_KEEP = 20  # the newest copies
BACKUP_EVERY = 10 * 60  # s; autosaves (a GitLab check, a routine run) must not wash the copies out
BACKUP_DAYS = 14  # beyond the newest copies, the last copy of each of these days stays

# Entry kinds: code → UI label key. The code is stored in the file, the label is display only.
KINDS: dict[str, str] = {
    "login": "Login and password",
    "token": "API token",
    "key": "SSH / certificate",
    "card": "Bank card",
    "note": "Note",
    "other": "Other",
}
DEFAULT_KIND = "login"

# Fields a new entry of each kind starts with: (field name key, secret?).
KIND_TEMPLATES: dict[str, list[tuple[str, bool]]] = {
    "login": [("login", False), ("password", True), ("site", False)],
    "token": [("token", True), ("service / URL", False), ("valid until", False)],
    "key": [("public key", False), ("private key", True), ("passphrase", True)],
    "card": [("number", True), ("expires", False), ("CVV", True), ("holder", False)],
    "note": [],
    "other": [("value", True)],
}

MAIN_GROUP = ""  # the main list of entries; other lists are created by the user
MAIN_GROUP_LABEL = "Main"  # UI key

KIND_DEFAULT_NAMES: dict[str, str] = {
    "login": "New service",
    "token": "New token",
    "key": "New key",
    "card": "New card",
    "note": "New note",
    "other": "New entry",
}


def main_group_label() -> str:
    return _(MAIN_GROUP_LABEL)


EXPORT_FORMAT = "daykeeper-export"


class WrongPassword(Exception):
    """The master password did not fit or the file is damaged."""


class BadFile(Exception):
    """The file is not a vault."""


class ChangedOnDisk(OSError):
    """The vault file is not the one this window read or wrote last: another window or
    program saved it. Writing now would silently throw that save away."""


def check_params(iterations: int, memory_kib: int, lanes: int) -> None:
    """Argon2id parameters from a header must be sane before a key is derived with them."""
    if not 1 <= lanes <= MAX_LANES:
        raise BadFile(_("Damaged vault header: lanes = {n}").format(n=lanes))
    if not 1 <= iterations <= MAX_ITERATIONS:
        raise BadFile(_("Damaged vault header: iterations = {n}").format(n=iterations))
    if not 8 * lanes <= memory_kib <= MAX_MEMORY_KIB:
        raise BadFile(_("Damaged vault header: memory = {n} KiB").format(n=memory_kib))


def write_private(path: Path, data: bytes) -> None:
    """Write bytes atomically (temp file + rename) with owner-only access."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    private(path)


def read_header(blob: bytes) -> tuple[bytes, bytes, tuple[int, int, int], bytes]:
    """(header, salt, (iterations, memory_kib, lanes), nonce) of a vault blob; BadFile if
    it is not a vault or its parameters are out of bounds."""
    if len(blob) < HEADER_LEN or not blob.startswith(MAGICS):
        raise BadFile(_("Not a vault file"))
    header = blob[:HEADER_LEN]
    _magic, salt, iterations, memory_kib, lanes, nonce = struct.unpack(HEADER_FMT, header)
    check_params(iterations, memory_kib, lanes)
    return header, salt, (iterations, memory_kib, lanes), nonce


class Sealer:
    """Seals the value of a secret field with the vault's field key, which lives inside the
    encrypted content. So a secret is plain text only for the moment it is shown or copied:
    not in the content decrypted at unlock, not in every autosave, not in the undo history.
    `forget()` zeroes the key at a lock (a bytearray: bytes could not be wiped)."""

    AAD = b"daykeeper field v5"

    def __init__(self, key: bytes | bytearray | None = None):
        self.key = bytearray(key if key is not None else secrets.token_bytes(KEY_LEN))

    def seal(self, text: str) -> bytes:
        nonce = secrets.token_bytes(NONCE_LEN)
        return nonce + AESGCM(self.key).encrypt(nonce, text.encode("utf-8", TEXT_ERRORS), self.AAD)

    def open(self, blob: bytes) -> str:
        return AESGCM(self.key).decrypt(blob[:NONCE_LEN], blob[NONCE_LEN:], self.AAD).decode("utf-8", TEXT_ERRORS)

    def forget(self) -> None:
        for i in range(len(self.key)):
            self.key[i] = 0


class Field:
    """A "name — value" line of an entry. A secret value is kept sealed once the field
    belongs to an open vault (`bind`); `value` opens it for the moment it is read. A field
    made by hand holds its value plain until the vault binds it (at the next save)."""

    __slots__ = ("key", "_secret", "_plain", "_sealed", "_sealer", "_damaged")

    def __init__(self, key: str = "", value: str = "", secret: bool = False, sealer: Sealer | None = None):
        self.key = key
        self._secret = bool(secret)
        self._plain = ""
        self._sealed: bytes | None = None
        self._sealer = sealer
        self._damaged = ""  # a sealed value from the file that could not be decoded: kept as it was, so a save does not lose it
        self.value = value

    @property
    def value(self) -> str:
        if self._sealed is not None:
            return self._sealer.open(self._sealed) if self._sealer is not None else ""
        return self._plain

    @value.setter
    def value(self, text: str) -> None:
        text = str(text)
        if (self._sealed is not None or self._plain) and text == self.value:
            return  # the same value: a new seal would only look like a change
        if text:
            self._damaged = ""  # typed over: the unreadable value is given up on purpose
        if self._secret and self._sealer is not None and text:
            self._sealed, self._plain = self._sealer.seal(text), ""
        else:
            self._sealed, self._plain = None, text

    @property
    def secret(self) -> bool:
        return self._secret

    @secret.setter
    def secret(self, on: bool) -> None:
        on = bool(on)
        if on == self._secret:
            return
        text = self.value
        self._secret, self._sealed, self._plain = on, None, ""
        self.value = text

    @property
    def filled(self) -> bool:
        """Has a value — without opening a sealed one."""
        return self._sealed is not None or bool(self._plain.strip()) or bool(self._damaged)

    @property
    def unreadable(self) -> bool:
        """The file held a sealed value that could not be decoded: the card says so instead
        of showing an empty field, and a save writes it back as it was."""
        return bool(self._damaged) and self._sealed is None and not self._plain

    def bind(self, sealer: Sealer) -> None:
        """Belong to the vault of `sealer`: a secret value is sealed with its key."""
        if sealer is self._sealer:
            return
        text = self.value
        self._sealer, self._sealed, self._plain = sealer, None, ""
        self.value = text

    def to_dict(self, plain: bool = False) -> dict:
        """For the file and the undo history the sealed value; `plain` — the value itself
        (an export, which the user asked for with the master password)."""
        if self._sealed is not None and not plain:
            return {"key": self.key, "sealed": base64.b64encode(self._sealed).decode("ascii"), "secret": True}
        if self.unreadable and not plain:
            return {"key": self.key, "sealed": self._damaged, "secret": True}
        return {"key": self.key, "value": self.value, "secret": self._secret}

    @staticmethod
    def from_dict(d: dict, sealer: Sealer | None = None) -> Field:
        secret = bool(d.get("secret", False))
        f = Field(str(d.get("key", "")), "", secret, sealer)
        sealed = d.get("sealed")
        if secret and isinstance(sealed, str) and sealed:
            try:
                f._sealed = base64.b64decode(sealed, validate=True)
            except ValueError:
                f._sealed, f._damaged = None, sealed
        else:
            f.value = str(d.get("value", ""))  # a file of 1.0, an export: sealed by `sealer` if given
        return f

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Field) and (self.key, self.value, self._secret) == (other.key, other.value, other._secret)

    __hash__ = None  # type: ignore[assignment]  # mutable, as the dataclass it replaces

    def __repr__(self) -> str:
        return f"Field({self.key!r}, {'•••' if self._secret else repr(self._plain)}, secret={self._secret})"


@dataclass
class Entry:
    name: str = ""
    fields: list[Field] = field(default_factory=list)
    note: str = ""
    kind: str = DEFAULT_KIND
    group: str = MAIN_GROUP  # list name; "" — the main list
    order: int = -1  # position in the list; -1 — not assigned yet (file of an older version)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    updated: float = field(default_factory=time.time)

    @property
    def kind_label(self) -> str:
        return _(KINDS.get(self.kind, KINDS[DEFAULT_KIND]))

    def to_dict(self, plain: bool = False) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "group": self.group,
            "order": self.order,
            "fields": [f.to_dict(plain) for f in self.fields],
            "note": self.note,
            "updated": self.updated,
        }

    @staticmethod
    def from_dict(d: dict, sealer: Sealer | None = None) -> Entry:
        """Tolerant, as the other parts of the file: a value of an unexpected type (a newer
        version may change one) gives the default, not a vault that does not open."""
        kind = str(d.get("kind") or DEFAULT_KIND)
        rows = d.get("fields")
        return Entry(
            name=str(d.get("name", "")),
            fields=[Field.from_dict(f, sealer) for f in rows if isinstance(f, dict)] if isinstance(rows, list) else [],
            note=str(d.get("note", "")),
            kind=kind if kind in KINDS else DEFAULT_KIND,  # file of an older version — no kind
            group=str(d.get("group") or MAIN_GROUP),
            order=_number(int, d.get("order"), -1),
            id=str(d.get("id") or uuid.uuid4().hex),
            updated=_number(float, d.get("updated"), None) or time.time(),
        )


def _number(kind, value, default):
    try:
        return kind(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _stamp_of(path: Path) -> tuple[int, int, bytes] | None:
    """What identifies the file on disk: its time, its size and the nonce of its header.
    A sync tool (OneDrive, iCloud, `copy2`) puts another version in place with the time
    and the size kept; the nonce is fresh on every write, so it tells them apart."""
    try:
        st = path.stat()
        with open(path, "rb") as fh:
            header = fh.read(HEADER_LEN)
    except FileNotFoundError:
        return None
    except OSError:
        header = b""
    return st.st_mtime_ns, st.st_size, header[HEADER_LEN - NONCE_LEN :]


def backups_to_drop(copies: list[Path], now: float) -> list[Path]:
    """Copies past the newest BACKUP_KEEP, except the last copy of each of the last
    BACKUP_DAYS days. `copies` are sorted oldest first; the name ends with the time stamp."""
    keep = set(copies[-BACKUP_KEEP:])
    days: dict[str, Path] = {}
    for c in copies:
        day = c.stem.rsplit("-", 2)[-2] if c.stem.count("-") >= 2 else ""
        if day.isdigit():
            days[day] = c  # sorted: the last one of the day wins
    recent = {time.strftime("%Y%m%d", time.localtime(now - d * 86400)) for d in range(BACKUP_DAYS)}
    keep.update(c for day, c in days.items() if day in recent)
    return [c for c in copies if c not in keep]


def _derive_key(password: str, salt: bytes, iterations: int, memory_kib: int, lanes: int) -> bytes:
    return Argon2id(
        salt=salt,
        length=KEY_LEN,
        iterations=iterations,
        lanes=lanes,
        memory_cost=memory_kib,
    ).derive(password.encode("utf-8"))


class Vault:
    """An open vault. The key is derived once on open; saving is AES only."""

    def __init__(
        self,
        path: Path,
        key: bytes,
        salt: bytes,
        params: tuple[int, int, int],
        entries: list[Entry],
        groups: list[str] | None = None,
        journal: Journal | None = None,
        notes: Notes | None = None,
        bookmarks: Bookmarks | None = None,
        security: SecurityPrefs | None = None,
        integrations: Integrations | None = None,
        routines: Routines | None = None,
    ):
        self.path = Path(path)
        self._key = bytearray(key)  # wiped by forget()
        self._sealer = Sealer()  # the field key; a file brings its own (_load)
        self.upgraded = False  # opened from a file of 1.0: the next save writes the new form
        self._salt = salt
        self._params = params
        self.entries = entries
        self.groups: list[str] = list(groups or [])
        self.journal: Journal = journal or Journal()
        self.notes: Notes = notes or Notes()
        self.bookmarks: Bookmarks = bookmarks or Bookmarks()
        self.security: SecurityPrefs = security or SecurityPrefs()
        self.integrations: Integrations = integrations or Integrations()
        self.routines: Routines = routines or Routines()
        self._normalize()
        self._saved_digest = b""  # what is on disk, as this window last read or wrote it
        self._disk_stamp: tuple[int, int, bytes] | None = None
        self._copy_due = False  # the next save keeps a copy whatever the cadence (set after an open: the undo history died with the lock)

    def _normalize(self) -> None:
        """Older file: hand out positions in order, register lists that entries refer to.
        Every field belongs to this vault: a secret one is sealed with its key."""
        for e in self.entries:
            for f in e.fields:
                f.bind(self._sealer)
        next_order = max((e.order for e in self.entries), default=-1) + 1
        for e in self.entries:
            if e.order < 0:
                e.order = next_order
                next_order += 1
            if e.group != MAIN_GROUP and e.group not in self.groups:
                self.groups.append(e.group)

    # ---------- create / open ----------

    @classmethod
    def create(cls, path: Path, password: str) -> Vault:
        salt = secrets.token_bytes(SALT_LEN)
        params = (DEFAULT_ITERATIONS, DEFAULT_MEMORY_KIB, DEFAULT_LANES)
        key = _derive_key(password, salt, *params)
        vault = cls(path, key, salt, params, [])
        vault.save()
        return vault

    @classmethod
    def open(cls, path: Path, password: str) -> Vault:
        blob = Path(path).read_bytes()
        if len(blob) < HEADER_LEN or not blob.startswith(MAGICS):
            raise BadFile(_("{path} is not a vault file").format(path=path))
        header, salt, params, nonce = read_header(blob)
        key = _derive_key(password, salt, *params)
        try:
            plain = AESGCM(key).decrypt(nonce, blob[HEADER_LEN:], header)
        except InvalidTag as exc:
            raise WrongPassword(_("Wrong master password")) from exc
        data = json.loads(plain.decode("utf-8", TEXT_ERRORS))
        vault = cls(path, key, salt, params, [])
        vault._load(data if isinstance(data, dict) else {})
        vault.upgraded = blob.startswith(OLD_MAGICS)
        vault._saved_digest = hashlib.sha256(vault._payload()).digest()
        vault._disk_stamp = _stamp_of(Path(path))
        vault._copy_due = True
        return vault

    def _load(self, data: dict, parts: tuple[str, ...] = ("vault", "field_key", "journal", "notes", "bookmarks", "security", "integrations", "routines")) -> None:
        """Take the parts of the content from `data` (the decrypted file, or an export)."""
        if "vault" in parts:
            if "field_key" in parts:
                try:
                    key = base64.b64decode(str(data.get("field_key") or ""), validate=True)
                except ValueError:
                    key = b""
                if len(key) == KEY_LEN:  # a file of 1.0 has none: its plain secrets are sealed with a new one
                    self._sealer = Sealer(key)
            self.entries = [Entry.from_dict(e, self._sealer) for e in data.get("entries") or [] if isinstance(e, dict)]
            self.groups = [str(g) for g in data.get("groups") or [] if str(g)]
        if "journal" in parts:
            self.journal = Journal.from_list(data.get("journal") or [])
        if "notes" in parts:
            self.notes = Notes.from_list(data.get("notes") or [])
        if "bookmarks" in parts:
            self.bookmarks = Bookmarks.from_dict(data.get("bookmarks"))
        if "security" in parts:
            self.security = SecurityPrefs.from_dict(data.get("security"))
        if "integrations" in parts:
            self.integrations = Integrations.from_dict(data.get("integrations"))
        if "routines" in parts:
            self.routines = Routines.from_list(data.get("routines"))
        self._normalize()

    # ---------- the whole content as a readable file ----------

    def export_data(self) -> dict:
        """Everything the tabs hold, as plain JSON: to keep, to move, to read with any tool.
        Not the lock settings: a PIN hash means nothing outside, and a file must not be
        able to switch the locks off when it is imported back."""
        data = json.loads(self._payload(plain=True))
        data.pop("security", None)
        data.pop("field_key", None)
        return {"format": EXPORT_FORMAT, "version": data.pop("version", 4), "exported": time.strftime("%Y-%m-%d %H:%M:%S"), "data": data}

    def import_data(self, doc: dict) -> None:
        """Replace everything the tabs hold with an export (export_data). ValueError — the
        file is not such an export. The lock settings stay as they are."""
        if not isinstance(doc, dict) or doc.get("format") != EXPORT_FORMAT or not isinstance(doc.get("data"), dict):
            raise ValueError(_("This is not a Daykeeper export"))
        self._load(doc["data"], ("vault", "journal", "notes", "bookmarks", "integrations", "routines"))

    # ---------- save ----------

    def _seal(self, payload: bytes) -> bytes:
        """Encrypt a payload with the current key under a fresh nonce."""
        nonce = secrets.token_bytes(NONCE_LEN)
        header = struct.pack(HEADER_FMT, MAGIC, self._salt, *self._params, nonce)
        return header + AESGCM(self._key).encrypt(nonce, payload, header)

    def _payload(self, plain: bool = False) -> bytes:
        """The content as JSON. Secret values stay sealed, with the field key beside them;
        `plain` opens them (the readable export only)."""
        self._normalize()  # a field added by hand since the last save is sealed now
        return json.dumps(
            {
                "version": 5,
                **({} if plain else {"field_key": base64.b64encode(bytes(self._sealer.key)).decode("ascii")}),
                "groups": self.groups,
                "entries": [e.to_dict(plain) for e in self.entries],
                "journal": self.journal.to_list(),
                "notes": self.notes.to_list(),
                "bookmarks": self.bookmarks.to_dict(),
                "security": self.security.to_dict(),
                "integrations": self.integrations.to_dict(),
                "routines": self.routines.to_list(),
            },
            ensure_ascii=False,
        ).encode("utf-8", TEXT_ERRORS)

    def _encrypt(self) -> bytes:
        return self._seal(self._payload())

    def save(self, force: bool = False) -> bool:
        """Write the vault when its content changed; False — nothing to write. Raises
        ChangedOnDisk when someone else saved the file since this window read it."""
        payload = self._payload()
        digest = hashlib.sha256(payload).digest()
        if not force and digest == self._saved_digest and self.path.exists():
            return False
        if self._disk_stamp is not None and _stamp_of(self.path) != self._disk_stamp:
            raise ChangedOnDisk(_("The vault file was changed by another window or program. Nothing was written, so that change is kept. What you change here stays in this window: when you close or lock it, Daykeeper offers to keep it in a separate copy next to the vault."))
        if not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            private(self.path.parent, folder=True)
        self._backup(force=self._copy_due)
        self._copy_due = False
        write_private(self.path, self._seal(payload))
        self._saved_digest, self._disk_stamp = digest, _stamp_of(self.path)
        return True

    def backup_now(self) -> None:
        """A copy of the file as it is, whatever the cadence: before an action undo cannot
        take back once the window is locked (a list or a day deleted)."""
        self._backup(force=True)

    def save_copy(self, path: Path) -> None:
        """Write what this window holds to another file, under the same password — the way
        out of a conflict (ChangedOnDisk) without losing either side."""
        write_private(Path(path), self._seal(self._payload()))

    def _backup(self, force: bool = False) -> None:
        if not self.path.exists():
            return
        backup_dir = self.path.parent / "backup"
        if not backup_dir.exists():
            backup_dir.mkdir()
            private(backup_dir, folder=True)
        pattern = f"{self.path.stem}-*{self.path.suffix}"
        copies = sorted(backup_dir.glob(pattern))
        now = time.time()
        if copies and now - copies[-1].stat().st_mtime < BACKUP_EVERY and not force:
            return  # a fresh copy is there already
        target = backup_dir / f"{self.path.stem}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}{self.path.suffix}"
        n = 1
        while target.exists():  # a forced copy within the same second must not write over the one before it
            target = target.with_name(f"{target.stem.rsplit('-', 1)[0] if n > 1 else target.stem}-{n}{target.suffix}")
            n += 1
        target.write_bytes(self.path.read_bytes())
        private(target)
        for extra in backups_to_drop(sorted(backup_dir.glob(pattern)), now):
            extra.unlink(missing_ok=True)

    def change_password(self, new_password: str) -> tuple[int, int]:
        """Re-encrypt the vault under a new password, then its backups: copies made under the
        old password are re-encrypted, copies from even older passwords are deleted (they
        could not be opened with the new one anyway). Returns (re-encrypted, deleted)."""
        old_key, old_salt, old_params = self._key, self._salt, self._params
        self._salt = secrets.token_bytes(SALT_LEN)
        self._params = (DEFAULT_ITERATIONS, DEFAULT_MEMORY_KIB, DEFAULT_LANES)
        self._key = bytearray(_derive_key(new_password, self._salt, *self._params))
        old_sealer, self._sealer = self._sealer, Sealer()  # a new field key too: whoever held the old file and password keeps nothing of the new one
        for e in self.entries:
            for f in e.fields:
                f.bind(self._sealer)
        old_sealer.forget()
        self.save(force=True)
        return self._rekey_backups(old_key, old_salt, old_params)

    def _rekey_backups(self, old_key: bytes | bytearray, old_salt: bytes, old_params: tuple[int, int, int]) -> tuple[int, int]:
        rekeyed = deleted = 0
        backup_dir = self.path.parent / "backup"
        managed = sorted(backup_dir.glob("*.pkv")) if backup_dir.is_dir() else []
        # Other copies next to the vault (e.g. one kept before a merge) are re-encrypted
        # when they can be, and never deleted: they are the user's files.
        loose = [f for f in self.path.parent.glob("*.pkv") if f.resolve() != self.path.resolve()]
        for f in managed + loose:
            plain = None
            try:
                blob = f.read_bytes()
                header, salt, params, nonce = read_header(blob)
                if salt == old_salt and params == old_params:
                    plain = AESGCM(old_key).decrypt(nonce, blob[HEADER_LEN:], header)
            except (OSError, BadFile, InvalidTag):
                plain = None
            if plain is not None:
                try:
                    write_private(f, self._seal(plain))
                    rekeyed += 1
                except OSError:
                    pass
            elif f in managed:
                try:
                    f.unlink()
                    deleted += 1
                except OSError:
                    pass
        return rekeyed, deleted

    # ---------- undo ----------

    def domain_state(self, domain: str) -> str:
        """One part of the data as text, for undo (see undo.History): the entries and lists,
        the journal, notes, bookmarks, routines. A routine's last run is not part of it — an
        undo puts back what the user did, not what a run did; nor a bookmark's fetched icon."""
        if domain == "vault":
            data: object = {"groups": self.groups, "entries": [e.to_dict() for e in self.entries]}
        elif domain == "journal":  # an empty day is the one the Journal tab opens by itself
            data = [d for d in self.journal.to_list() if d.get("summary") or d.get("columns") or d.get("activities") or d.get("plans") or d.get("other")]
        elif domain == "notes":
            data = self.notes.to_list()
        elif domain == "bookmarks":
            data = self.bookmarks.to_dict()
            data["items"] = [{k: v for k, v in b.items() if k not in SITE_FIELDS} for b in data["items"]]
        elif domain == "routines":
            data = [{k: v for k, v in r.items() if k not in RUN_FIELDS} for r in self.routines.to_list()]
        else:
            raise KeyError(domain)
        return json.dumps(data, ensure_ascii=False, sort_keys=True)

    GROUPS_KEY = "\x00groups"  # the lists of entries / the groups of bookmarks, beside the objects by id

    def object_states(self, domain: str) -> dict[str, str]:
        """One part of the data as `key → JSON of one object` for undo (undo.History): an
        entry, a day, a note, a bookmark, a routine each on its own, so a step keeps only
        the objects that changed. The same leaves as domain_state: no run state, no fetched
        icon, no empty day; `GROUPS_KEY` holds the lists / groups."""
        dumps = lambda data: json.dumps(data, ensure_ascii=False, sort_keys=True)  # noqa: E731
        if domain == "vault":
            states = {e.id: dumps(e.to_dict()) for e in self.entries}
            states[self.GROUPS_KEY] = dumps(self.groups)
        elif domain == "journal":
            states = {d["date"]: dumps(d) for d in self.journal.to_list() if d.get("summary") or d.get("columns") or d.get("activities") or d.get("plans") or d.get("other")}
        elif domain == "notes":
            states = {n["id"]: dumps(n) for n in self.notes.to_list()}
        elif domain == "bookmarks":
            data = self.bookmarks.to_dict()
            states = {b["id"]: dumps({k: v for k, v in b.items() if k not in SITE_FIELDS}) for b in data["items"]}
            states[self.GROUPS_KEY] = dumps(data["groups"])
        elif domain == "routines":
            states = {r["id"]: dumps({k: v for k, v in r.items() if k not in RUN_FIELDS}) for r in self.routines.to_list()}
        else:
            raise KeyError(domain)
        return states

    def set_object_state(self, domain: str, key: str, state: str | None) -> None:
        """Put one object back from `object_states` text; `state` None removes it. A routine
        is updated in place by id and keeps its run; a bookmark keeps its fetched icon."""
        data = None if state is None else json.loads(state)
        if domain == "vault":
            if key == self.GROUPS_KEY:
                self.groups = [str(g) for g in (data or []) if str(g)]
                return
            self.entries = [e for e in self.entries if e.id != key]
            if data is not None:
                self.entries.append(Entry.from_dict(data, self._sealer))
                self.entries.sort(key=lambda e: e.order)
        elif domain == "journal":
            if data is None:
                self.journal.remove(key)
            else:
                self.journal.days[key] = Journal.from_list([data]).days[key]
        elif domain == "notes":
            self.notes.notes = [n for n in self.notes.notes if n.id != key]
            if data is not None:
                self.notes.notes.extend(Notes.from_list([data]).notes)
        elif domain == "bookmarks":
            if key == self.GROUPS_KEY:
                self.bookmarks.groups = [str(g) for g in (data or []) if str(g)]
                return
            old = self.bookmarks.get(key)
            self.bookmarks.items = [b for b in self.bookmarks.items if b.id != key]
            if data is not None:
                fresh = Bookmarks.from_dict({"groups": [], "items": [data]}).items[0]
                if old is not None:  # the site's icon and the last opening stay as they are now
                    for name in SITE_FIELDS:
                        setattr(fresh, name, getattr(old, name))
                self.bookmarks.items.append(fresh)
                if fresh.group and fresh.group not in self.bookmarks.groups:
                    self.bookmarks.groups.append(fresh.group)
        elif domain == "routines":
            running = self.routines.get(key)
            if data is None:
                self.routines.items = [r for r in self.routines.items if r.id != key]
                return
            restored = Routine.from_dict(data)
            if running is not None:
                for name in RUN_FIELDS:
                    setattr(restored, name, getattr(running, name))
                running.__dict__.update(restored.__dict__)
            else:
                self.routines.items.append(restored)
        else:
            raise KeyError(domain)

    def set_domain_state(self, domain: str, state: str) -> None:
        """Put a part back from `domain_state` text. Routines are updated in place by id:
        a run under way keeps its routine and its last run."""
        data = json.loads(state)
        if domain == "vault":
            self.groups = [str(g) for g in data.get("groups", []) if str(g)]
            self.entries = [Entry.from_dict(e, self._sealer) for e in data.get("entries", [])]
        elif domain == "journal":
            self.journal = Journal.from_list(data)
        elif domain == "notes":
            self.notes = Notes.from_list(data)
        elif domain == "bookmarks":
            looks = {b.id: b for b in self.bookmarks.items}
            self.bookmarks = Bookmarks.from_dict(data)
            for b in self.bookmarks.items:  # the site's icon and the last opening stay as they are now
                old = looks.get(b.id)
                if old is not None:
                    for name in SITE_FIELDS:
                        setattr(b, name, getattr(old, name))
        elif domain == "routines":
            live = {r.id: r for r in self.routines.items}
            items = []
            for row in data:
                fresh = Routine.from_dict(row)
                running = live.get(fresh.id)
                if running is not None:
                    for name in RUN_FIELDS:
                        setattr(fresh, name, getattr(running, name))
                    running.__dict__.update(fresh.__dict__)
                    fresh = running
                items.append(fresh)
            self.routines.items = items
        else:
            raise KeyError(domain)

    def forget(self) -> None:
        """A lock: the master key and the field key are zeroed. What was read from them is
        dropped with the vault; the keys themselves are the one thing that is wiped."""
        self._sealer.forget()
        for i in range(len(self._key)):
            self._key[i] = 0

    def check_password(self, password: str) -> bool:
        """Is this the master password the vault was opened with (re-entry before export)."""
        candidate = _derive_key(password, self._salt, *self._params)
        return hmac.compare_digest(candidate, self._key)

    # ---------- entries ----------

    def _next_order(self) -> int:
        return max((e.order for e in self.entries), default=-1) + 1

    def add_entry(self, name: str | None = None, kind: str = DEFAULT_KIND, group: str = MAIN_GROUP) -> Entry:
        if kind not in KINDS:
            kind = DEFAULT_KIND
        fields = [Field(_(key), secret=secret) for key, secret in KIND_TEMPLATES[kind]]
        entry = Entry(
            name=name if name is not None else _(KIND_DEFAULT_NAMES[kind]),
            fields=fields,
            kind=kind,
            group=group if group in self.groups else MAIN_GROUP,
            order=self._next_order(),
        )
        self.entries.append(entry)
        return entry

    def remove_entry(self, entry: Entry) -> None:
        self.entries = [e for e in self.entries if e.id != entry.id]
        self.bookmarks.forget_entry(entry.id)

    def entry_by_id(self, entry_id: str) -> Entry | None:
        return next((e for e in self.entries if e.id == entry_id), None) if entry_id else None

    def move_entry(self, entry: Entry, delta: int) -> bool:
        """Shift the entry by delta positions inside its list (-1 up, +1 down). False — at the edge."""
        siblings = sorted((e for e in self.entries if e.group == entry.group), key=lambda e: e.order)
        idx = next((i for i, e in enumerate(siblings) if e.id == entry.id), -1)
        target = idx + delta
        if idx < 0 or not 0 <= target < len(siblings):
            return False
        other = siblings[target]
        entry.order, other.order = other.order, entry.order
        return True

    def set_group(self, entry: Entry, group: str) -> None:
        """Move the entry to another list — to its end."""
        if group != MAIN_GROUP and group not in self.groups:
            raise ValueError(_("No list named “{name}”").format(name=group))
        if entry.group == group:
            return
        entry.group = group
        entry.order = self._next_order()

    # ---------- lists ----------

    def add_group(self, name: str) -> str:
        name = name.strip()
        if not name or name == main_group_label():
            raise ValueError(_("List name is empty or taken"))
        if name in self.groups:
            raise ValueError(_("List “{name}” already exists").format(name=name))
        self.groups.append(name)
        return name

    def rename_group(self, old: str, new: str) -> str:
        new = new.strip()
        if old not in self.groups:
            raise ValueError(_("No list named “{name}”").format(name=old))
        if new == old:
            return old
        if not new or new == main_group_label() or new in self.groups:
            raise ValueError(_("List name is empty or taken"))
        self.groups[self.groups.index(old)] = new
        for e in self.entries:
            if e.group == old:
                e.group = new
        return new

    def remove_group(self, name: str) -> None:
        """Delete a list; its entries are not lost, they go to the main list."""
        if name not in self.groups:
            raise ValueError(_("No list named “{name}”").format(name=name))
        self.groups.remove(name)
        moved = sorted((e for e in self.entries if e.group == name), key=lambda e: e.order)
        for e in moved:  # in the same order they had in the list
            e.group = MAIN_GROUP
            e.order = self._next_order()

    def search(self, query: str, group: str | None = MAIN_GROUP) -> list[Entry]:
        """With a query — searches all lists; without — returns list `group` (None — all) in manual order."""
        q = query.strip().lower()
        result = []
        for e in self.entries:
            if not q:
                if group is None or e.group == group:
                    result.append(e)
                continue
            haystack = (
                [e.name, e.note, e.kind_label]
                + [f.key for f in e.fields]
                + [f.value for f in e.fields if not f.secret]
            )
            if any(q in h.lower() for h in haystack):
                result.append(e)
        return sorted(result, key=lambda e: e.order)


def default_vault_path() -> Path:
    return app_dir() / "vault.pkv"


def generate_password(length: int = 20, symbols: bool = True) -> str:
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    if symbols:
        alphabet += "!@#$%^&*-_=+?"
    return "".join(secrets.choice(alphabet) for _i in range(length))
