"""The hotkey that opens the quick window from anywhere: `RegisterHotKey` (ctypes) on
Windows. No tkinter here: a press puts a tick into a queue the window drains with a Ticker,
or calls a function from the hotkey thread.

On another system nothing is registered and `start` says why; macOS binds a system shortcut
to `Daykeeper --quick` instead (not done here)."""

from __future__ import annotations

import ctypes
import queue as queue_mod
import sys
import threading
from collections.abc import Callable

from .i18n import _

IS_WINDOWS = sys.platform == "win32"

MODS = {"ctrl": 0x0002, "alt": 0x0001, "shift": 0x0004, "win": 0x0008}
MOD_ALIASES = {"control": "ctrl", "ctl": "ctrl", "option": "alt", "super": "win", "meta": "win", "cmd": "win"}
_MOD_NOREPEAT = 0x4000  # holding the keys down gives one press, not a stream
_WM_HOTKEY, _WM_QUIT, _WM_USER = 0x0312, 0x0012, 0x0400
_PM_NOREMOVE = 0
_ERROR_HOTKEY_ALREADY_REGISTERED = 1409
_HOTKEY_ID = 0x5DC1  # any number in 0x0000..0xBFFF: one hotkey per thread
_START_WAIT = 3.0  # s for the thread to say whether Windows took the combination

# key name → virtual key code (winuser.h)
KEYS: dict[str, int] = {
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "backspace": 0x08, "insert": 0x2D, "ins": 0x2D, "delete": 0x2E, "del": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "pause": 0x13,
    ";": 0xBA, "=": 0xBB, "plus": 0xBB, ",": 0xBC, "-": 0xBD, "minus": 0xBD, ".": 0xBE,
    "/": 0xBF, "`": 0xC0, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
}
KEYS |= {chr(c): c for c in range(ord("A"), ord("Z") + 1)} | {c.lower(): ord(c) for c in map(chr, range(ord("A"), ord("Z") + 1))}
KEYS |= {str(d): 0x30 + d for d in range(10)}
KEYS |= {f"f{n}": 0x6F + n for n in range(1, 25)}  # F1 = 0x70 … F24 = 0x87
_F_KEYS = {0x6F + n for n in range(1, 25)}


def parse_combo(text: str) -> tuple[int, int] | None:
    """"ctrl+shift+space" → (modifiers, virtual key); None when it cannot be read: no key,
    two keys, an unknown name, or a plain key without a modifier (it would take that key
    from every program; F1–F24 alone are allowed)."""
    mods, key = 0, None
    for part in (p.strip().lower() for p in str(text).split("+")):
        if not part:
            return None
        part = MOD_ALIASES.get(part, part)
        if part in MODS:
            mods |= MODS[part]
            continue
        if key is not None or part not in KEYS:
            return None
        key = KEYS[part]
    if key is None or (not mods and key not in _F_KEYS):
        return None
    return mods, key


class WindowsHotkey:
    """RegisterHotKey lives in its own thread with a message loop (GetMessageW): the
    WM_HOTKEY message is posted to the thread that registered it. A press puts a tick into
    `queue` and calls `notify` — in the hotkey thread, so it must not touch Tk; the window
    drains the queue with a Ticker (100 ms). `stop` posts WM_QUIT; the thread unregisters
    the combination on its way out."""

    def __init__(self, combo: str, queue: queue_mod.Queue | None = None, notify: Callable[[], None] | None = None):
        self.combo = combo
        self.queue = queue
        self.notify = notify
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._problem = ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> str:
        """"" — registered; otherwise the problem in words (taken by another program, a
        combination that cannot be read, not Windows). Never raises."""
        if self.running:
            return ""
        parsed = parse_combo(self.combo)
        if parsed is None:
            return _("This key combination cannot be read: {combo}").format(combo=self.combo)
        if not IS_WINDOWS:
            return _("A hotkey for the whole computer works on Windows only")
        self._ready.clear()
        self._problem = ""
        self._thread = threading.Thread(target=self._run, args=parsed, name="daykeeper-hotkey", daemon=True)
        try:
            self._thread.start()
        except RuntimeError as exc:
            self._thread = None
            return _("The hotkey could not be set up: {error}").format(error=exc)
        if not self._ready.wait(_START_WAIT):
            self.stop()
            return _("The hotkey could not be set up: {error}").format(error=_("Windows did not answer"))
        if self._problem:
            self._thread.join(_START_WAIT)
            self._thread = None
        return self._problem

    def stop(self) -> None:
        """Ask the thread to leave its loop and wait for it (it unregisters on the way)."""
        thread = self._thread
        if thread is None:
            return
        if IS_WINDOWS and self._thread_id:
            try:
                _user32().PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
            except (OSError, AttributeError):
                pass
        thread.join(_START_WAIT)
        self._thread = None
        self._thread_id = 0

    def _press(self) -> None:
        if self.queue is not None:
            self.queue.put_nowait(True)
        if self.notify is not None:
            try:
                self.notify()
            except Exception:  # noqa: BLE001, S110 — a broken callback must not end the loop
                pass

    def _run(self, mods: int, vk: int) -> None:
        from ctypes import wintypes

        try:
            user32 = _user32()
            msg = wintypes.MSG()
            self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
            user32.PeekMessageW(ctypes.byref(msg), None, _WM_USER, _WM_USER, _PM_NOREMOVE)  # the thread's queue exists before stop can post to it
            if not user32.RegisterHotKey(None, _HOTKEY_ID, mods | _MOD_NOREPEAT, vk):
                code = ctypes.get_last_error()
                if code == _ERROR_HOTKEY_ALREADY_REGISTERED:
                    self._problem = _("{combo} is already taken by another program").format(combo=self.combo)
                else:
                    self._problem = _("Windows did not register {combo} (error {code})").format(combo=self.combo, code=code)
                return
        except (OSError, AttributeError) as exc:
            self._problem = _("The hotkey could not be set up: {error}").format(error=exc)
            return
        finally:
            self._ready.set()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:  # 0 — WM_QUIT, -1 — an error
                if msg.message == _WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                    self._press()
        finally:
            user32.UnregisterHotKey(None, _HOTKEY_ID)


_dll = None


def _user32():
    """user32 with its own argument types (the shared `windll.user32` of win32.py is left
    alone) and the last error kept for RegisterHotKey."""
    global _dll
    if _dll is None:
        from ctypes import wintypes

        dll = ctypes.WinDLL("user32", use_last_error=True)
        dll.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        dll.RegisterHotKey.restype = wintypes.BOOL
        dll.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        dll.UnregisterHotKey.restype = wintypes.BOOL
        dll.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        dll.GetMessageW.restype = wintypes.BOOL
        dll.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
        dll.PeekMessageW.restype = wintypes.BOOL
        dll.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        dll.PostThreadMessageW.restype = wintypes.BOOL
        _dll = dll
    return _dll
