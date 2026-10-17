"""Work on a thread, its answer on the window's thread: the bridge every slow call of the Qt
window goes through (the master password check now; GitLab, routines and site icons later).

A widget may be touched only by the GUI thread. `run_in_thread` runs `fn` on a plain
thread and hands `(result, error)` to `done` through a queued Qt signal, so `done` always
runs on the GUI thread, between events, as a timer would. The Tk window polled a thread
with `after`; a signal needs no polling."""

from __future__ import annotations

import threading
from collections.abc import Callable
from functools import partial
from typing import Any

import shiboken6
from PySide6.QtCore import QObject, QTimer, Signal


class _Relay(QObject):
    """Made on the GUI thread, so its slot runs there; kept alive in `_live` until it has
    delivered, so the thread never holds the last reference to a Qt object."""

    finished = Signal(object, object)

    def __init__(self, done: Callable[[Any, BaseException | None], None], owner: QObject | None):
        super().__init__()
        self._done, self._owner = done, owner
        self.finished.connect(self._deliver)

    def _deliver(self, result: Any, error: BaseException | None) -> None:
        # dropped from `_live` by a timer, not here: freeing the relay inside its own
        # slot would delete a Qt object while Qt is still delivering to it
        QTimer.singleShot(0, partial(_live.discard, self))
        if self._owner is not None and not shiboken6.isValid(self._owner):
            return  # the screen that asked is gone: nobody to answer
        self._done(result, error)


_live: set[_Relay] = set()


def run_in_thread(fn: Callable[[], Any], done: Callable[[Any, BaseException | None], None], owner: QObject | None = None, name: str = "job") -> threading.Thread:
    """`fn()` on a daemon thread; then `done(result, None)` or `done(None, error)` on the
    GUI thread. With `owner`, nothing is delivered once that object has been deleted."""
    relay = _Relay(done, owner)
    _live.add(relay)

    def work() -> None:
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 — handed to the GUI thread, which decides
            relay.finished.emit(None, exc)
            return
        relay.finished.emit(result, None)

    thread = threading.Thread(target=work, name=name, daemon=True)
    thread.start()
    return thread


def pending() -> int:
    """Answers not yet delivered (tests wait on it)."""
    return len(_live)
