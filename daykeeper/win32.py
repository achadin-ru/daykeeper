"""Calls into Windows (ctypes), the only place they live apart from the clipboard.

Every function does nothing and answers None / False / 0 on another system or when the
call is not there (an older Windows, no DWM), so callers need no platform checks of
their own. A window is addressed by its handle (`window_handle` turns a Tk widget id
into the handle of its top-level window); nothing here imports tkinter."""

from __future__ import annotations

import ctypes
import sys

IS_WINDOWS = sys.platform == "win32"

_GA_ROOT = 2
_WM_SETREDRAW = 0x000B
_RDW_INVALIDATE, _RDW_ALLCHILDREN, _RDW_UPDATENOW = 0x0001, 0x0080, 0x0100
_DWMWA_BORDER_COLOR, _DWMWA_CAPTION_COLOR, _DWMWA_TEXT_COLOR = 34, 35, 36
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_ROUNDSMALL = 3  # the radius of system menus
_FLASHW_ALL, _FLASHW_TIMERNOFG = 0x3, 0xC
_MONITOR_DEFAULTTONEAREST = 2
_SM_XVIRTUALSCREEN, _SM_YVIRTUALSCREEN, _SM_CXVIRTUALSCREEN, _SM_CYVIRTUALSCREEN = 76, 77, 78, 79
_LANG_RUSSIAN = 0x19

_FAILURES = (OSError, AttributeError, ValueError, TypeError)


def _colorref(colour: str) -> int:
    """"#RRGGBB" as the COLORREF Windows wants (0x00BBGGRR)."""
    c = colour.lstrip("#")
    return int(c[0:2], 16) | (int(c[2:4], 16) << 8) | (int(c[4:6], 16) << 16)


def enable_dpi_awareness() -> bool:
    """Per-monitor scaling before the first window is made: Windows then leaves the drawing
    to the app on every monitor instead of stretching it as a picture (blurred text on a
    monitor of another scale); the app redraws itself at the monitor's scale (App._check_dpi).
    Falls back to system scaling on older Windows. False when not on Windows."""
    if not IS_WINDOWS:
        return False
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # per monitor v2
            return True
    except _FAILURES:  # before Windows 10 1703
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return True
    except _FAILURES:  # an old system without shcore
        return False


def dpi_of_window(hwnd: int) -> int:
    """The DPI of the monitor a window is on (96 = 100 %); 0 when unknown."""
    if not IS_WINDOWS or not hwnd:
        return 0
    try:
        return int(ctypes.windll.user32.GetDpiForWindow(hwnd))
    except _FAILURES:
        return 0


def dpi_at(x: int, y: int) -> int:
    """The DPI of the monitor at a screen point (the nearest one); 0 when unknown."""
    if not IS_WINDOWS:
        return 0
    try:
        monitor = ctypes.windll.user32.MonitorFromPoint(_Point(x, y), _MONITOR_DEFAULTTONEAREST)
        dx, dy = ctypes.c_uint(), ctypes.c_uint()
        if ctypes.windll.shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(dx), ctypes.byref(dy)) != 0:
            return 0
        return int(dx.value)
    except _FAILURES:
        return 0


def window_handle(widget_id: int) -> int:
    """The handle of the top-level window a Tk widget (its `winfo_id()`) is in; 0 elsewhere."""
    if not IS_WINDOWS:
        return 0
    try:
        return int(ctypes.windll.user32.GetAncestor(widget_id, _GA_ROOT) or 0)
    except _FAILURES:
        return 0


def virtual_screen() -> tuple[int, int, int, int] | None:
    """(left, top, width, height) of all monitors together; None elsewhere."""
    if not IS_WINDOWS:
        return None
    try:
        user32 = ctypes.windll.user32
        return (
            user32.GetSystemMetrics(_SM_XVIRTUALSCREEN),
            user32.GetSystemMetrics(_SM_YVIRTUALSCREEN),
            user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN),
            user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN),
        )
    except _FAILURES:
        return None


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _Rect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _Rect), ("rcWork", _Rect), ("dwFlags", ctypes.c_ulong)]


def work_area_at(x: int, y: int) -> tuple[int, int, int, int] | None:
    """Left, top, right and bottom of the screen area (taskbar excluded) of the monitor
    the point is on; None elsewhere or when Windows does not say."""
    if not IS_WINDOWS:
        return None
    try:
        user32 = ctypes.windll.user32
        monitor = user32.MonitorFromPoint(_Point(x, y), _MONITOR_DEFAULTTONEAREST)
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            r = info.rcWork
            return r.left, r.top, r.right, r.bottom
    except _FAILURES:
        pass
    return None


def oem_codepage() -> str | None:
    """The console code page, as a Python codec name ("cp866"); None elsewhere."""
    if not IS_WINDOWS:
        return None
    try:
        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    except _FAILURES:
        return None


def ui_language_is_russian() -> bool | None:
    """Whether the Windows UI language is Russian; None elsewhere or when unknown."""
    if not IS_WINDOWS:
        return None
    try:
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
    except _FAILURES:
        return None
    return (lang_id & 0x3FF) == _LANG_RUSSIAN


def caption_colour(hwnd: int, background: str, text: str) -> bool:
    """Paint the Windows 11 title bar of a window; False where it is not supported."""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        for attr, colour in ((_DWMWA_CAPTION_COLOR, background), (_DWMWA_TEXT_COLOR, text)):
            value = ctypes.c_int(_colorref(colour))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
        return True
    except _FAILURES:  # not Windows 11 or no DWM
        return False


def round_corners(hwnd: int, border: str) -> bool:
    """Windows 11 rounds a borderless window and draws its outline in `border`; False elsewhere."""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        corner = ctypes.c_int(_DWMWCP_ROUNDSMALL)
        if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(corner), ctypes.sizeof(corner)) != 0:
            return False
        colour = ctypes.c_int(_colorref(border))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, _DWMWA_BORDER_COLOR, ctypes.byref(colour), ctypes.sizeof(colour))
        return True
    except _FAILURES:  # older Windows: a square card with a drawn outline
        return False


def flash_window(hwnd: int) -> bool:
    """Flash the taskbar button until the window is focused; False elsewhere."""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        from ctypes import wintypes

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND), ("dwFlags", wintypes.DWORD), ("uCount", wintypes.UINT), ("dwTimeout", wintypes.DWORD)]

        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, _FLASHW_ALL | _FLASHW_TIMERNOFG, 0, 0)
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
        return True
    except _FAILURES:  # cosmetic
        return False


def set_redraw(hwnd: int, on: bool) -> bool:
    """Stop (or resume) the repainting of a window and everything in it; False elsewhere."""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        ctypes.windll.user32.SendMessageW(hwnd, _WM_SETREDRAW, 1 if on else 0, 0)
        return True
    except _FAILURES:
        return False


def redraw_now(hwnd: int) -> bool:
    """Repaint a window and all its children at once; False elsewhere."""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        ctypes.windll.user32.RedrawWindow(hwnd, None, None, _RDW_INVALIDATE | _RDW_ALLCHILDREN | _RDW_UPDATENOW)
        return True
    except _FAILURES:
        return False


def mouse_held() -> bool:
    """The left mouse button is down right now — a window border being dragged; False elsewhere."""
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
    except (AttributeError, OSError):
        return False


def _declare() -> None:
    """Argument and result types for every call above, once at import. Without them ctypes
    passes a window handle above 2^31 as a negative 32-bit int on x64, and the call
    silently does nothing for such a window (caption colours, DPI, flashing). A function
    an older Windows does not have is skipped; its caller already falls back."""
    if not IS_WINDOWS:
        return
    from ctypes import wintypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    signatures: list[tuple] = [
        (user32, "GetAncestor", [wintypes.HWND, wintypes.UINT], wintypes.HWND),
        (user32, "GetDpiForWindow", [wintypes.HWND], wintypes.UINT),
        (user32, "SetProcessDpiAwarenessContext", [ctypes.c_void_p], wintypes.BOOL),
        (user32, "MonitorFromPoint", [_Point, wintypes.DWORD], ctypes.c_void_p),
        (user32, "GetSystemMetrics", [ctypes.c_int], ctypes.c_int),
        (user32, "GetMonitorInfoW", [ctypes.c_void_p, ctypes.POINTER(_MonitorInfo)], wintypes.BOOL),
        (user32, "FlashWindowEx", [ctypes.c_void_p], wintypes.BOOL),
        (user32, "SendMessageW", [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], ctypes.c_ssize_t),
        (user32, "RedrawWindow", [wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT], wintypes.BOOL),
        (user32, "GetAsyncKeyState", [ctypes.c_int], ctypes.c_short),
        (kernel32, "GetOEMCP", [], wintypes.UINT),
        (kernel32, "GetUserDefaultUILanguage", [], ctypes.c_ushort),
    ]
    for dll_name, name, args, result in (("shcore", "SetProcessDpiAwareness", [ctypes.c_int], ctypes.c_long),
                                         ("shcore", "GetDpiForMonitor", [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)], ctypes.c_long),
                                         ("dwmapi", "DwmSetWindowAttribute", [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD], ctypes.c_long)):
        try:
            signatures.append((getattr(ctypes.windll, dll_name), name, args, result))
        except _FAILURES:
            pass
    for dll, name, args, result in signatures:
        try:
            fn = getattr(dll, name)
            fn.argtypes, fn.restype = args, result
        except _FAILURES:
            pass


_declare()
