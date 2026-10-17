"""Import from another password manager's plain export: KeePass 2.x XML and CSV, Bitwarden
JSON (not encrypted) and CSV. `detect` names the format, `read` turns the file into
`Imported` items, `plan` matches them against the vault (same name in the same list), and
`apply` writes the plan. The export lies on disk in the open, so the window offers to delete
it afterwards; nothing here keeps a copy of a value outside the vault."""

from __future__ import annotations

import base64
import binascii
import csv
import io
import json
import os
import re
import xml.etree.ElementTree as ET  # noqa: S405 — parsed only after the DOCTYPE check below
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from .fieldicons import ICONS
from .i18n import _, variants
from .vault import MAIN_GROUP, Entry, Field, Vault, main_group_label

KEEPASS_XML = "keepass-xml"
KEEPASS_CSV = "keepass-csv"
BITWARDEN_JSON = "bitwarden-json"
BITWARDEN_CSV = "bitwarden-csv"
FORMATS = (KEEPASS_XML, KEEPASS_CSV, BITWARDEN_JSON, BITWARDEN_CSV)

MAX_BYTES = 50 * 1024 * 1024  # a vault export of tens of thousands of entries is a few MB
GROUP_SEP = " / "  # a nested group "Mail" inside "Work" becomes the list "Work / Mail"

# The vault's preset field names (catalog keys): stored translated, as a kind template does,
# so the field icons, the 2FA code and the expiry badge recognise them in any language.
LOGIN, PASSWORD, SITE, TOTP, VALID_UNTIL = "login", "password", "site", "2FA key", "valid until"
NUMBER, EXPIRES, CVV, HOLDER = "number", "expires", "CVV", "holder"
PRESET_KEYS = frozenset(ICONS)


class ImportFailed(ValueError):
    """The file cannot be imported: too large, unsafe, encrypted or not the format named.
    The message is for the user."""


@dataclass
class Imported:
    """One entry of the export. `fields` — (key, value, secret); a preset key is a catalog
    key (translated when written), any other key is the name from the export as it is."""

    name: str
    kind: str
    group: str
    fields: list[tuple[str, str, bool]]
    note: str = ""


# ---------- detection ----------


def _head(data: bytes, size: int = 4096) -> str:
    return data[:size].decode("utf-8-sig", "replace").lstrip()


def detect(data: bytes, suffix: str) -> str | None:
    """The format of an export by its content (the suffix only breaks a tie); None when it
    is none of the four. Sniffs the head: nothing is parsed here."""
    head = _head(data)
    suffix = suffix.lower().lstrip(".")
    if head.startswith("<"):
        return KEEPASS_XML if "<KeePassFile" in head else None
    if head.startswith("{"):
        return BITWARDEN_JSON if re.search(r'"(items|folders|encrypted)"\s*:', head) else None
    first = head.splitlines()[0] if head else ""
    cols = {c.strip().strip('"').casefold() for c in first.split(",")}
    if {"login_username", "login_password"} <= cols or {"type", "name", "login_uri"} <= cols:
        return BITWARDEN_CSV
    if {"title", "password"} <= cols or {"account", "password"} <= cols:
        return KEEPASS_CSV
    if suffix == "csv" and "password" in cols:
        return KEEPASS_CSV
    return None


def _too_large() -> ImportFailed:
    return ImportFailed(_("The file is larger than {n} MB").format(n=MAX_BYTES // (1024 * 1024)))


def check_size(path: str | os.PathLike[str]) -> None:
    """Refuse a file above MAX_BYTES by its size on disk, before it is read into memory
    (the window calls this first; `read` checks the bytes again). An unreadable file is
    left to the reading itself."""
    try:
        size = os.stat(path).st_size
    except OSError:
        return
    if size > MAX_BYTES:
        raise _too_large()


def read(data: bytes, fmt: str) -> list[Imported]:
    """Items of an export in the format `detect` named. ImportFailed with a message for the
    user when the file is too large, unsafe, encrypted or damaged. Check the size on disk
    with `check_size` before reading the file."""
    if len(data) > MAX_BYTES:
        raise _too_large()
    readers = {KEEPASS_XML: read_keepass_xml, KEEPASS_CSV: read_keepass_csv, BITWARDEN_JSON: read_bitwarden_json, BITWARDEN_CSV: read_bitwarden_csv}
    if fmt not in readers:
        raise ImportFailed(_("Unknown export format"))
    try:
        return readers[fmt](data)
    except (RecursionError, MemoryError) as exc:  # nesting too deep for the parser, a file that swells when parsed
        damaged = {KEEPASS_XML: _("Damaged XML file"), BITWARDEN_JSON: _("Damaged JSON file")}.get(fmt) or _("Damaged CSV file")
        raise ImportFailed(damaged) from exc


def _text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportFailed(_("The file is not UTF-8 text")) from exc


def _csv_rows(data: bytes) -> list[dict[str, str]]:
    """Rows keyed by the header in lower case; a BOM and newlines inside quotes are fine."""
    reader = csv.DictReader(io.StringIO(_text(data), newline=""))
    try:
        rows = list(reader)
    except csv.Error as exc:
        raise ImportFailed(_("Damaged CSV file")) from exc
    return [{(k or "").strip().casefold(): (v or "") for k, v in row.items() if isinstance(v, str)} for row in rows]


def _add(fields: list[tuple[str, str, bool]], key: str, value: object, secret: bool) -> None:
    text = str(value).strip() if value is not None else ""
    if text:
        fields.append((key, text, secret))


# ---------- KeePass ----------

DOCTYPE_RE = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
ENCODING_RE = re.compile(r"""^\s*<\?xml[^>]*\bencoding\s*=\s*["']([^"']+)["']""")


def parse_xml(data: bytes) -> ET.Element:
    """Parse a document from somebody else's file. A DOCTYPE (entities, external DTD) is
    refused before the parser sees a byte — the "billion laughs" and external entities
    need one; only UTF-8 is accepted, so the check reads what the parser will read."""
    if len(data) > MAX_BYTES:
        raise _too_large()
    text = _text(data)
    m = ENCODING_RE.match(text)
    if m and m.group(1).strip().lower().replace("_", "-") not in ("utf-8", "utf8"):
        raise ImportFailed(_("The file is not UTF-8 text"))
    if DOCTYPE_RE.search(text):
        raise ImportFailed(_("The file declares a DOCTYPE: refused for safety"))
    try:
        return ET.fromstring(data.removeprefix(b"\xef\xbb\xbf"))  # noqa: S314 — no DOCTYPE, so no entities
    except ET.ParseError as exc:
        raise ImportFailed(_("Damaged XML file")) from exc


def _kp_time(text: str) -> str:
    """A KeePass time as YYYY-MM-DD: ISO ("2026-12-31T00:00:00Z") or, from a KDBX 4
    database, base64 of seconds since 0001-01-01 (little-endian int64). "" when neither."""
    text = text.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", text):
        return text[:10]
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return ""
    if len(raw) != 8:
        return ""
    try:
        return (datetime(1, 1, 1, tzinfo=UTC) + timedelta(seconds=int.from_bytes(raw, "little", signed=True))).date().isoformat()
    except OverflowError:
        return ""


OTP_ALGORITHMS = {"HMAC-SHA-1": "SHA1", "HMAC-SHA-256": "SHA256", "HMAC-SHA-512": "SHA512"}


def _keepass_otp(name: str, strings: dict[str, tuple[str, bool]]) -> str:
    """The 2FA key of a KeePass entry: KeePassXC's `otp` (an otpauth:// URI) as it is;
    KeePass's own TimeOtp-* strings as a base32 key, or an otpauth:// URI when the length,
    period or algorithm is not the default. The TimeOtp-* strings are taken out of `strings`."""
    otp = strings.pop("otp", ("", False))[0].strip()
    settings = {k: strings.pop(k)[0].strip() for k in list(strings) if k.startswith("TimeOtp-")}
    if otp:
        return otp
    secret = settings.get("TimeOtp-Secret-Base32", "").replace(" ", "").upper()
    try:
        if not secret and settings.get("TimeOtp-Secret-Hex"):
            secret = base64.b32encode(bytes.fromhex(settings["TimeOtp-Secret-Hex"].replace(" ", ""))).decode()
        elif not secret and settings.get("TimeOtp-Secret-Base64"):
            secret = base64.b32encode(base64.b64decode(settings["TimeOtp-Secret-Base64"])).decode()
        elif not secret and settings.get("TimeOtp-Secret"):
            secret = base64.b32encode(settings["TimeOtp-Secret"].encode("utf-8")).decode()
    except (ValueError, binascii.Error):
        secret = ""
    secret = secret.rstrip("=")
    if not secret:
        return ""
    digits = settings.get("TimeOtp-Length", "") or "6"
    period = settings.get("TimeOtp-Period", "") or "30"
    algorithm = OTP_ALGORITHMS.get(settings.get("TimeOtp-Algorithm", ""), "SHA1")
    if (digits, period, algorithm) == ("6", "30", "SHA1"):
        return secret
    return f"otpauth://totp/{quote(name)}?secret={secret}&digits={digits}&period={period}&algorithm={algorithm}"


def _keepass_entry(e: ET.Element, group: str) -> Imported:
    strings: dict[str, tuple[str, bool]] = {}
    for s in e.findall("String"):
        key = (s.findtext("Key") or "").strip()
        value = s.find("Value")
        if not key or value is None:
            continue
        # a plain export marks a protected string ProtectInMemory, a database's own XML Protected
        protected = any(value.get(a, "").lower() == "true" for a in ("Protected", "ProtectInMemory"))
        strings[key] = (value.text or "", protected)
    name = strings.pop("Title", ("", False))[0].strip()
    fields: list[tuple[str, str, bool]] = []
    _add(fields, LOGIN, strings.pop("UserName", ("", False))[0], False)
    _add(fields, PASSWORD, strings.pop("Password", ("", False))[0], True)
    _add(fields, SITE, strings.pop("URL", ("", False))[0], False)
    _add(fields, TOTP, _keepass_otp(name, strings), True)
    note = strings.pop("Notes", ("", False))[0].strip()
    times = e.find("Times")
    if times is not None and (times.findtext("Expires") or "").strip().lower() == "true":
        _add(fields, VALID_UNTIL, _kp_time(times.findtext("ExpiryTime") or ""), False)
    for key, (text, protected) in strings.items():
        _add(fields, key, text, protected)
    return Imported(name, "login", group, fields, note)


def read_keepass_xml(data: bytes) -> list[Imported]:
    """KeePass 2.x XML (File → Export → KeePass XML). The groups under the database's root
    group become lists, nested ones joined with " / "; entries of the root group go to the
    main list. The recycle bin and the history of an entry are left out."""
    doc = parse_xml(data)
    if doc.tag != "KeePassFile":
        raise ImportFailed(_("Not a KeePass XML export"))
    meta = doc.find("Meta")
    bin_uuid = ""
    if meta is not None and (meta.findtext("RecycleBinEnabled") or "True").strip().lower() == "true":
        bin_uuid = (meta.findtext("RecycleBinUUID") or "").strip()
    root = doc.find("Root/Group")
    if root is None:
        return []
    items: list[Imported] = []
    # depth first with a stack of its own: a file with thousands of nested groups must not
    # run into the recursion limit; children go on reversed to keep the order of the file
    stack: list[tuple[ET.Element, str]] = [(root, "")]
    while stack:
        g, group = stack.pop()
        for e in g.findall("Entry"):  # direct children only: Entry/History holds old versions
            items.append(_keepass_entry(e, group))
        children = [c for c in g.findall("Group") if not (bin_uuid and (c.findtext("UUID") or "").strip() == bin_uuid)]
        for child in reversed(children):
            name = (child.findtext("Name") or "").strip() or "…"
            stack.append((child, f"{group}{GROUP_SEP}{name}" if group else name))
    return items


KEEPASS_CSV_COLUMNS = {  # KeePass 2.x, then KeePassXC
    "account": "title", "login name": "username", "web site": "url", "comments": "notes",
    "title": "title", "username": "username", "url": "url", "notes": "notes",
}


def read_keepass_csv(data: bytes) -> list[Imported]:
    """KeePass 2.x CSV (Account, Login Name, Password, Web Site, Comments) or KeePassXC CSV
    (Group, Title, Username, Password, URL, Notes, TOTP, …). No custom fields or expiry: the
    format does not carry them. KeePassXC's group path starts with the root group's name,
    which is dropped."""
    items = []
    for raw in _csv_rows(data):
        row = {KEEPASS_CSV_COLUMNS.get(k, k): v for k, v in raw.items()}
        parts = [p.strip() for p in row.get("group", "").split("/") if p.strip()]
        fields: list[tuple[str, str, bool]] = []
        _add(fields, LOGIN, row.get("username"), False)
        _add(fields, PASSWORD, row.get("password"), True)
        _add(fields, SITE, row.get("url"), False)
        _add(fields, TOTP, row.get("totp"), True)
        items.append(Imported(row.get("title", "").strip(), "login", GROUP_SEP.join(parts[1:]), fields, row.get("notes", "").strip()))
    return items


# ---------- Bitwarden ----------

BW_LOGIN, BW_NOTE, BW_CARD, BW_IDENTITY, BW_SSH = 1, 2, 3, 4, 5
BW_KINDS = {BW_LOGIN: "login", BW_NOTE: "note", BW_CARD: "card", BW_IDENTITY: "other", BW_SSH: "key"}
BW_FIELD_TEXT, BW_FIELD_HIDDEN, BW_FIELD_BOOLEAN = 0, 1, 2  # 3 — linked to a login field: no value of its own

# Bitwarden identity → field name; "mail" and "phone" are presets
BW_IDENTITY_FIELDS = [
    ("title", "title"), ("firstName", "first name"), ("middleName", "middle name"), ("lastName", "last name"),
    ("username", LOGIN), ("company", "company"), ("email", "mail"), ("phone", "phone"),
    ("address1", "address"), ("address2", "address 2"), ("address3", "address 3"), ("city", "city"),
    ("state", "state"), ("postalCode", "postal code"), ("country", "country"),
]
BW_IDENTITY_SECRET = frozenset({"ssn", "passportNumber", "licenseNumber"})


def _expires(month: object, year: object) -> str:
    """A card's expiry as MM/YYYY — the form the expiry badge reads; one part alone as it is."""
    m, y = str(month or "").strip(), str(year or "").strip()
    if m.isdigit():
        m = f"{int(m):02d}"
    if len(y) == 2 and y.isdigit():
        y = "20" + y
    return f"{m}/{y}" if m and y else m or y


def _bw_folder(name: str) -> str:
    """A Bitwarden folder "Work/Mail" is nested in the app: the same " / " as KeePass."""
    return GROUP_SEP.join(p.strip() for p in name.split("/") if p.strip())


def _obj(item: dict, key: str) -> dict:
    value = item.get(key)
    return value if isinstance(value, dict) else {}


def _bw_code(value: object) -> int | None:
    """A Bitwarden type code: an int, never a bool (True == 1 would pass for a login)."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _bw_item(item: dict, folders: dict[str, str]) -> Imported | None:
    kind_code = _bw_code(item.get("type"))
    if kind_code not in BW_KINDS:
        return None
    group = folders.get(str(item.get("folderId") or ""), "")
    if not group:
        ids = item.get("collectionIds")
        if isinstance(ids, list) and ids:
            group = folders.get(str(ids[0]), "")
    fields: list[tuple[str, str, bool]] = []
    if kind_code == BW_LOGIN:
        login = _obj(item, "login")
        _add(fields, LOGIN, login.get("username"), False)
        _add(fields, PASSWORD, login.get("password"), True)
        uris = [u.get("uri") for u in login.get("uris") or [] if isinstance(u, dict) and u.get("uri")]
        for i, uri in enumerate(uris):
            _add(fields, SITE if i == 0 else f"{_(SITE)} {i + 1}", uri, False)
        _add(fields, TOTP, login.get("totp"), True)
    elif kind_code == BW_CARD:
        card = _obj(item, "card")
        _add(fields, NUMBER, card.get("number"), True)
        _add(fields, EXPIRES, _expires(card.get("expMonth"), card.get("expYear")), False)
        _add(fields, CVV, card.get("code"), True)
        _add(fields, HOLDER, card.get("cardholderName"), False)
        _add(fields, "brand", card.get("brand"), False)
    elif kind_code == BW_IDENTITY:
        ident = _obj(item, "identity")
        for src, key in BW_IDENTITY_FIELDS:
            _add(fields, key, ident.get(src), False)
        for src in sorted(BW_IDENTITY_SECRET):
            _add(fields, src, ident.get(src), True)
    elif kind_code == BW_SSH:
        ssh = _obj(item, "sshKey")
        _add(fields, "public key", ssh.get("publicKey"), False)
        _add(fields, "private key", ssh.get("privateKey"), True)
        _add(fields, "fingerprint", ssh.get("keyFingerprint"), False)
    for f in item.get("fields") or []:
        if isinstance(f, dict) and _bw_code(f.get("type")) in (BW_FIELD_TEXT, BW_FIELD_HIDDEN, BW_FIELD_BOOLEAN):
            _add(fields, str(f.get("name") or "").strip() or _("value"), f.get("value"), f.get("type") == BW_FIELD_HIDDEN)
    return Imported(str(item.get("name") or "").strip(), BW_KINDS[kind_code], group, fields, str(item.get("notes") or "").strip())


def read_bitwarden_json(data: bytes) -> list[Imported]:
    """Bitwarden JSON (Tools → Export vault → .json, not encrypted). Folders (or, in an
    organisation export, collections) become lists; types 1–5: login, secure note, card,
    identity, SSH key. A hidden custom field is a secret one."""
    try:
        doc = json.loads(_text(data))
    except json.JSONDecodeError as exc:
        raise ImportFailed(_("Damaged JSON file")) from exc
    if not isinstance(doc, dict):
        raise ImportFailed(_("Not a Bitwarden export"))
    if doc.get("encrypted"):
        raise ImportFailed(_("The export is encrypted: export the vault again as plain .json"))
    folders: dict[str, str] = {}
    for key in ("folders", "collections"):
        for f in doc.get(key) or []:
            if isinstance(f, dict) and f.get("id"):
                folders[str(f["id"])] = _bw_folder(str(f.get("name") or ""))
    items = []
    for raw in doc.get("items") or []:
        item = _bw_item(raw, folders) if isinstance(raw, dict) else None
        if item is not None:
            items.append(item)
    return items


def read_bitwarden_csv(data: bytes) -> list[Imported]:
    """Bitwarden CSV: logins and notes only (the format has no cards or identities). The
    custom fields come as "name: value" lines and do not say which were hidden, so they are
    kept secret."""
    items = []
    for row in _csv_rows(data):
        kind = "note" if row.get("type", "").strip().lower() == "note" else "login"
        fields: list[tuple[str, str, bool]] = []
        if kind == "login":
            _add(fields, LOGIN, row.get("login_username"), False)
            _add(fields, PASSWORD, row.get("login_password"), True)
            uris = [u.strip() for u in re.split(r"[,\n]", row.get("login_uri", "")) if u.strip()]
            for i, uri in enumerate(uris):
                _add(fields, SITE if i == 0 else f"{_(SITE)} {i + 1}", uri, False)
            _add(fields, TOTP, row.get("login_totp"), True)
        for line in row.get("fields", "").splitlines():
            key, sep, value = line.partition(": ")
            if sep:
                _add(fields, key.strip() or _("value"), value, True)
        items.append(Imported(row.get("name", "").strip(), kind, _bw_folder(row.get("folder", "")), fields, row.get("notes", "").strip()))
    return items


# ---------- plan and apply ----------


def _list_name(group: str) -> str:
    """The vault list of an imported group: a group named as the main list goes to it."""
    return MAIN_GROUP if not group.strip() or group.strip() == main_group_label() else group.strip()


def _field_key(key: str) -> str:
    return _(key) if key in PRESET_KEYS else key


def _same_key(existing: str, key: str) -> bool:
    names = {v.casefold() for v in variants(key)} if key in PRESET_KEYS else {key.casefold()}
    return existing.strip().casefold() in names


def _missing(entry: Entry, item: Imported) -> list[tuple[str, str, bool]]:
    """Fields of `item` the entry does not have yet: a key it lacks, or has with no value.
    A value already there is never replaced."""
    return [f for f in item.fields if not any(_same_key(x.key, f[0]) and x.filled for x in entry.fields)]


@dataclass
class Match:
    entry_id: str
    item: Imported
    missing: list[tuple[str, str, bool]]


@dataclass
class ImportPlan:
    """What an import would do. An item with the same name in the same list as an entry is
    a match: skipped, or — `merge` — the entry gets the fields it lacks."""

    merge: bool
    lists: list[str] = field(default_factory=list)  # lists to create
    new: list[Imported] = field(default_factory=list)
    matches: list[Match] = field(default_factory=list)

    @property
    def entries_added(self) -> int:
        return len(self.new)

    @property
    def fields_added(self) -> int:
        added = sum(len(i.fields) for i in self.new)
        return added + (sum(len(m.missing) for m in self.matches) if self.merge else 0)

    @property
    def entries_extended(self) -> int:
        return sum(1 for m in self.matches if m.missing) if self.merge else 0

    @property
    def skipped(self) -> int:
        return len(self.matches) - self.entries_extended

    def summary(self) -> str:
        """The preview: counts only, no names of entries and no values."""
        parts = [_("lists: +{n}").format(n=len(self.lists)), _("entries: +{n}").format(n=self.entries_added)]
        if self.entries_extended:
            parts.append(_("extended {n}").format(n=self.entries_extended))
        parts.append(_("fields: +{n}").format(n=self.fields_added))
        if self.skipped:
            parts.append(_("skipped {n}").format(n=self.skipped))
        return " · ".join(parts)


def plan(items: list[Imported], vault: Vault, merge: bool) -> ImportPlan:
    """Match the items against the vault by name and list. Two items of the export with
    the same name are both kept (the export had both)."""
    result = ImportPlan(merge)
    for item in items:
        group = _list_name(item.group)
        if group != MAIN_GROUP and group not in vault.groups and group not in result.lists:
            result.lists.append(group)
        existing = next((e for e in vault.entries if e.group == group and e.name == item.name), None)
        if existing is None:
            result.new.append(item)
        else:
            result.matches.append(Match(existing.id, item, _missing(existing, item)))
    return result


def apply(plan: ImportPlan, vault: Vault) -> tuple[int, int]:
    """Write the plan: (entries created, fields added). A match is looked up again and its
    missing fields counted again, so a vault changed since the plan loses nothing."""
    for name in plan.lists:
        if name not in vault.groups:
            vault.add_group(name)
    entries = fields = 0
    for item in plan.new:
        group = _list_name(item.group)
        entry = vault.add_entry(item.name or None, kind=item.kind, group=group)
        entry.fields = [Field(_field_key(k), v, s) for k, v, s in item.fields]
        entry.note = item.note
        entries += 1
        fields += len(item.fields)
    if plan.merge:
        for m in plan.matches:
            found = vault.entry_by_id(m.entry_id)
            if found is None:
                continue
            for key, value, secret in _missing(found, m.item):
                empty = next((x for x in found.fields if _same_key(x.key, key) and not x.filled), None)
                if empty is not None:
                    empty.value = value
                else:
                    found.fields.append(Field(_field_key(key), value, secret))
                fields += 1
            if m.item.note and not found.note.strip():
                found.note = m.item.note
    return entries, fields
