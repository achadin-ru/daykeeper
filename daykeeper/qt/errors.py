"""No exception leaves an event handler of the Qt window.

Qt for Python 6.12 hands an exception raised in a slot, a timer or an event that came from
the event loop to sys.excepthook and carries on. But an exception raised in an overridden
event handler (resizeEvent, paintEvent, …) while that event is delivered synchronously from
our own Python code (resize(), repaint(), sendEvent() inside a slot) ends the process with
code 1 — with whatever was not saved yet. Every class of the package that overrides an event
handler is decorated with `guard_events`: the handler's error goes to sys.excepthook (the
window's report: errors.log and a dialog) and the event counts as not handled.

The same holds for every other virtual Qt calls back into while our code waits on it — a
size hint or height-for-width asked by a layout during show(), a delegate's or icon
engine's paint asked by grab(), a model's data asked by setCurrentIndex(). Those are
wrapped too (SYNC_METHODS) and answer a harmless default: an empty size, no row, no
picture. tests/test_qt_package.py keeps every Qt class that overrides one decorated."""

from __future__ import annotations

import functools
import sys
from collections.abc import Callable
from typing import Any

EVENT_METHODS = frozenset({
    "event", "eventFilter", "paintEvent", "resizeEvent", "moveEvent", "showEvent", "hideEvent", "closeEvent", "changeEvent",
    "mousePressEvent", "mouseReleaseEvent", "mouseMoveEvent", "mouseDoubleClickEvent", "wheelEvent", "enterEvent", "leaveEvent",
    "keyPressEvent", "keyReleaseEvent", "focusInEvent", "focusOutEvent", "focusNextPrevChild", "contextMenuEvent",
})
_ANSWERS_BOOL = {"event", "eventFilter", "focusNextPrevChild"}


def _size() -> Any:
    from PySide6.QtCore import QSize

    return QSize()


def _pixmap() -> Any:
    from PySide6.QtGui import QPixmap

    return QPixmap()


# virtual → what it answers when it failed (see the module text)
SYNC_METHODS: dict[str, Callable[[], Any]] = {
    "sizeHint": _size, "minimumSizeHint": _size, "minimumSize": _size,
    "heightForWidth": lambda: -1, "hasHeightForWidth": lambda: False,
    "paint": lambda: None, "drawPrimitive": lambda: None, "setGeometry": lambda: None,
    "data": lambda: None, "rowCount": lambda: 0, "pixelMetric": lambda: 0,
    "pixmap": _pixmap, "scaledPixmap": _pixmap,
}


def _guarded(fn: Callable[..., Any], name: str) -> Callable[..., Any]:
    @functools.wraps(fn)
    def handler(self: Any, *args: Any) -> Any:
        try:
            return fn(self, *args)
        except Exception:  # noqa: BLE001 — reported, never let through to Qt (see the module text)
            sys.excepthook(*sys.exc_info())
            if name in SYNC_METHODS:
                return SYNC_METHODS[name]()
            return False if name in _ANSWERS_BOOL else None

    handler.__guarded__ = True  # type: ignore[attr-defined]
    return handler


def guard_events[T](cls: T) -> T:
    """Wrap the event handlers and the other Qt virtuals `cls` defines itself (see the
    module text)."""
    for name in EVENT_METHODS | SYNC_METHODS.keys():
        fn = vars(cls).get(name)
        if callable(fn) and not getattr(fn, "__guarded__", False):
            setattr(cls, name, _guarded(fn, name))
    return cls
