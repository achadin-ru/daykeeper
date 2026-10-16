"""Clipboard for secrets.

On Windows a copied password is put with the formats Windows reads before it records the
clipboard: “ExcludeClipboardContentFromMonitorProcessing” (clipboard monitors skip it),
“CanIncludeInClipboardHistory” = 0 (Win+V history does not keep it) and
“CanUploadToCloudClipboard” = 0 (no sync to other devices). Elsewhere the text goes
through Tk; the app still clears it after a delay.
"""

from __future__ import annotations

import ctypes
import hashlib
import sys

CF_UNICODETEXT = 13
CLEAR_SEC = 30  # a copied secret is wiped from the clipboard after this long
GMEM_MOVEABLE = 0x0002

_EXCLUDE = "ExcludeClipboardContentFromMonitorProcessing"
_HISTORY = "CanIncludeInClipboardHistory"
_CLOUD = "CanUploadToCloudClipboard"


def _win():
    """ctypes handles with 64-bit-safe signatures; None outside Windows."""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    return user32, kernel32


_API = None


def _api():
    global _API
    if _API is None:
        _API = _win() or False
    return _API or None


def _alloc(kernel32, data: bytes):
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise OSError("GlobalAlloc failed")
    ptr = kernel32.GlobalLock(handle)
    ctypes.memmove(ptr, data, len(data))
    kernel32.GlobalUnlock(handle)
    return handle


def _open(user32) -> bool:
    for _attempt in range(10):  # another app may hold the clipboard for a moment
        if user32.OpenClipboard(None):
            return True
        ctypes.windll.kernel32.Sleep(20)
    return False


def put_secret(text: str) -> bool:
    """Windows: put `text` hidden from history, monitors and cloud sync. False — not done
    (not Windows or the clipboard is busy); the caller falls back to Tk."""
    api = _api()
    if api is None:
        return False
    user32, kernel32 = api
    if not _open(user32):
        return False
    try:
        user32.EmptyClipboard()
        handle = _alloc(kernel32, (text + "\0").encode("utf-16-le"))
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        zero = (0).to_bytes(4, "little")
        for name, payload in ((_EXCLUDE, b"\0\0\0\0"), (_HISTORY, zero), (_CLOUD, zero)):
            fmt = user32.RegisterClipboardFormatW(name)
            h = _alloc(kernel32, payload)
            if not user32.SetClipboardData(fmt, h):
                kernel32.GlobalFree(h)
        return True
    finally:
        user32.CloseClipboard()


def read_text() -> str | None:
    """Windows: the current clipboard text, None if there is none or it is busy."""
    api = _api()
    if api is None:
        return None
    user32, kernel32 = api
    if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT) or not _open(user32):
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def clear_if(expected: str) -> bool | None:
    """Windows: empty the clipboard if it still holds `expected`. None — not Windows."""
    api = _api()
    if api is None:
        return None
    if read_text() != expected:
        return False
    user32, _kernel32 = api
    if not _open(user32):
        return False
    try:
        user32.EmptyClipboard()
        return True
    finally:
        user32.CloseClipboard()


def has_history_opt_out() -> bool:
    """Windows: does the clipboard carry the “not in history” mark (for tests)."""
    api = _api()
    if api is None:
        return False
    user32, _kernel32 = api
    return bool(user32.IsClipboardFormatAvailable(user32.RegisterClipboardFormatW(_HISTORY)))


class SecretClipboard:
    """Puts a secret on the clipboard hidden from history and wipes it later if it is
    still there. The Windows way first; where that is not available, the window's own
    clipboard (`fallback_put`, `fallback_get`, `fallback_clear` — Tk) does it, and the
    text is still wiped after the delay. The delay itself is the caller's timer."""

    def __init__(self, fallback_put, fallback_get, fallback_clear, put=put_secret, clear_if=clear_if, read=read_text):
        self._fallback_put, self._fallback_get, self._fallback_clear = fallback_put, fallback_get, fallback_clear
        self._put, self._clear_if, self._read = put, clear_if, read

    @staticmethod
    def digest(text: str) -> str:
        """What the wipe timer keeps instead of the secret itself: a hash of the copied text."""
        return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()

    def wipe_if_digest(self, digest: str) -> bool:
        """Empty the clipboard if what it holds hashes to `digest`; True when it was wiped."""
        text = self._read()
        if text is None:
            text = self._fallback_get()
        if text is None or self.digest(text) != digest:
            return False
        return self.wipe_if(text)

    def put(self, text: str) -> bool:
        """Copy `text`. True — hidden from the clipboard history; False — put plainly."""
        if self._put(text):
            return True
        self._fallback_put(text)
        return False

    def wipe_if(self, expected: str) -> bool:
        """Empty the clipboard if it still holds `expected`; True when it was wiped."""
        done = self._clear_if(expected)
        if done is not None:
            return done
        if self._fallback_get() == expected:
            self._fallback_clear()
            return True
        return False
