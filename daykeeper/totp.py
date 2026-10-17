"""Two-factor codes: TOTP (RFC 6238) from the key of a "2FA key" field.

The value of the field is an otpauth://totp/... URI, as a QR code of a service holds it,
or the bare base32 key (spaces and case do not matter). HOTP (counter-based) and Steam
codes are not supported: `parse` gives None for them. Standard library only; pure."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import struct
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

from .i18n import variants

ALGORITHMS = ("sha1", "sha256", "sha512")
MIN_KEY_BYTES = 10  # 80 bits, the shortest key services hand out; shorter text is not a key
FIELD = "2FA key"  # the preset field name key


@dataclass(frozen=True)
class Totp:
    secret: bytes
    digits: int = 6
    period: int = 30
    algorithm: str = "sha1"  # sha1 | sha256 | sha512
    label: str = ""

    def __repr__(self) -> str:
        return f"Totp(•••, digits={self.digits}, period={self.period}, algorithm={self.algorithm!r}, label={self.label!r})"


def _key(text: str) -> bytes | None:
    """A base32 key, spaces, dashes, case and missing padding forgiven."""
    clean = re.sub(r"[\s-]+", "", text).upper().rstrip("=")
    if not clean or not re.fullmatch(r"[A-Z2-7]+", clean):
        return None
    try:
        key = base64.b32decode(clean + "=" * (-len(clean) % 8))
    except (binascii.Error, ValueError):
        return None
    return key if len(key) >= MIN_KEY_BYTES else None


def _number(params: dict[str, list[str]], name: str, default: int, low: int, high: int) -> int | None:
    raw = params.get(name, [""])[0].strip()
    if not raw:
        return default
    try:
        n = int(raw)
    except ValueError:
        return None
    return n if low <= n <= high else None


def parse(text: str) -> Totp | None:
    """An otpauth:// URI or a bare base32 key; None when it is neither."""
    text = (text or "").strip()
    if "://" not in text:
        secret = _key(text)
        return Totp(secret) if secret is not None else None
    parts = urlsplit(text)
    if parts.scheme.lower() != "otpauth" or parts.netloc.lower() != "totp":
        return None  # hotp, steam://, anything else
    params = {k.lower(): v for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
    if params.get("encoder", [""])[0].lower() == "steam":
        return None
    secret = _key(params.get("secret", [""])[0])
    digits = _number(params, "digits", 6, 6, 10)
    period = _number(params, "period", 30, 1, 3600)
    algorithm = (params.get("algorithm", [""])[0].strip() or "sha1").lower()
    if secret is None or digits is None or period is None or algorithm not in ALGORITHMS:
        return None
    return Totp(secret, digits, period, algorithm, unquote(parts.path.lstrip("/")))


def code(t: Totp, now: float) -> str:
    """The code for the moment `now` (a Unix time), zero-padded to `digits`."""
    counter = int(now // t.period)
    mac = hmac.new(t.secret, struct.pack(">Q", counter), getattr(hashlib, t.algorithm)).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**t.digits).zfill(t.digits)


def seconds_left(t: Totp, now: float) -> int:
    """Whole seconds until the code changes: from `period` down to 1."""
    return t.period - int(now) % t.period


_NAMES: set[str] = set()


def is_totp_field(name: str) -> bool:
    """The "2FA key" preset in any language (i18n.variants)."""
    if not _NAMES:
        _NAMES.update(v.casefold() for v in variants(FIELD))
    return name.strip().casefold() in _NAMES
