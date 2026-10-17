"""The window's timers over QTimer: `Debounce`, `IdleOnce` and `Ticker` with the interface
and behaviour of daykeeper.timers (the Tk ones), one QTimer each.

The QTimer is a child of the owner, so it dies with the owner; after that every call is
nothing. A timer object holds its QTimer and its function, never the owner: a Python
reference to the owner kept alive by a timer's own slot made the owner's wrapper go at
the moment its last child was deleted, and Qt deleted the owner a second time (found by
the tests: the process aborted)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import shiboken6
from PySide6.QtCore import QObject, QTimer


def _timer(owner: QObject, ms: int, single: bool) -> QTimer:
    timer = QTimer(owner)
    timer.setSingleShot(single)
    timer.setInterval(max(0, int(ms)))
    return timer


class Debounce:
    """Calls `fn(*args)` `delay_ms` after the last `touch(*args)`; a touch before that
    restarts the clock and replaces the arguments. `flush` runs it now when it is due."""

    def __init__(self, owner: QObject, delay_ms: int, fn: Callable[..., Any]):
        self.delay_ms, self.fn = delay_ms, fn
        self._args: tuple = ()
        self._timer = _timer(owner, delay_ms, single=True)
        self._timer.timeout.connect(self._fire)

    @property
    def pending(self) -> bool:
        return shiboken6.isValid(self._timer) and self._timer.isActive()

    def touch(self, *args: Any) -> None:
        if shiboken6.isValid(self._timer):
            self._args = args
            self._timer.start(max(0, int(self.delay_ms)))

    def cancel(self) -> None:
        if shiboken6.isValid(self._timer):
            self._timer.stop()

    def flush(self) -> None:
        """Run the waiting call at once (nothing when none is waiting)."""
        if self.pending:
            self._timer.stop()
            self.fn(*self._args)

    def _fire(self) -> None:
        self.fn(*self._args)


class IdleOnce:
    """Calls `fn()` once at idle (a zero timer: once the events waiting now are handled),
    however many times `touch` is called before that."""

    def __init__(self, owner: QObject, fn: Callable[[], Any]):
        self.fn = fn
        self._timer = _timer(owner, 0, single=True)
        self._timer.timeout.connect(self._fire)

    @property
    def pending(self) -> bool:
        return shiboken6.isValid(self._timer) and self._timer.isActive()

    def touch(self) -> None:
        if shiboken6.isValid(self._timer) and not self._timer.isActive():
            self._timer.start()

    def cancel(self) -> None:
        if shiboken6.isValid(self._timer):
            self._timer.stop()

    def _fire(self) -> None:
        self.fn()


class Ticker:
    """Calls `fn()` every `ms` from `start` until `stop`. A repeating timer, so the next
    tick is already booked when `fn` runs: an error inside it does not end the ticks, and
    `fn` answering False does. `start` while running is nothing."""

    def __init__(self, owner: QObject, ms: int, fn: Callable[[], Any]):
        self.ms, self.fn = ms, fn
        self._timer = _timer(owner, ms, single=False)
        self._timer.timeout.connect(self._tick)

    @property
    def running(self) -> bool:
        return shiboken6.isValid(self._timer) and self._timer.isActive()

    def start(self) -> None:
        if shiboken6.isValid(self._timer) and not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        if shiboken6.isValid(self._timer):
            self._timer.stop()

    def _tick(self) -> None:
        if self.fn() is False:
            self.stop()
