"""No exception leaves an event handler of the Qt window.

Qt for Python 6.12 hands an exception raised in a slot, a timer or an event that came from
the event loop to sys.excepthook and carries on. But an exception raised in an overridden
event handler (resizeEvent, paintEvent, …) while that event is delivered synchronously from
our own Python code (resize(), repaint(), sendEvent() inside a slot) ends the process with
code 1 — with whatever was not saved yet. Every class of the package that overrides an event
handler is decorated with `guard_events`: the handler's error goes to sys.excepthook (the
window's report: errors.log and a dialog) and the event counts as not handled.
tests/test_qt_package.py keeps every such class decorated."""

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


def _guarded(fn: Callable[..., Any], name: str) -> Callable[..., Any]:
    @functools.wraps(fn)
    def handler(self: Any, *args: Any) -> Any:
        try:
            return fn(self, *args)
        except Exception:  # noqa: BLE001 — reported, never let through to Qt (see the module text)
            sys.excepthook(*sys.exc_info())
            return False if name in _ANSWERS_BOOL else None

    handler.__guarded__ = True  # type: ignore[attr-defined]
    return handler


def guard_events[T](cls: T) -> T:
    """Wrap the event handlers `cls` defines itself (see the module text)."""
    for name in EVENT_METHODS:
        fn = vars(cls).get(name)
        if callable(fn) and not getattr(fn, "__guarded__", False):
            setattr(cls, name, _guarded(fn, name))
    return cls
