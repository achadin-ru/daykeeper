"""Work on a thread, its answer on the window's thread: the bridge every slow call of the Qt
window goes through: `run_in_thread` for one call (the master password check), `Waker` for
the pools and processes that keep their answers in a queue (site icons, GitLab, routines).

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


class _Hub(QObject):
    """One for the life of the app, made on the GUI thread by the first Waker: a thread
    emits on an object that is never deleted, so no thread ever holds the last reference
    to a Qt object (see _Relay) nor emits on one that is gone."""

    poked = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.wakers: dict[int, Waker] = {}
        self.waiting: set[int] = set()
        self.lock = threading.Lock()
        self.poked.connect(self._deliver)

    def _deliver(self, key: int) -> None:
        with self.lock:
            self.waiting.discard(key)
        waker = self.wakers.get(key)
        if waker is not None:
            waker._wake()


_hub: _Hub | None = None
_keys = iter(range(1, 1 << 62))


class Waker:
    """A call from any thread that runs `fn()` on the GUI thread instead of a timer that
    asks a queue every few hundred ms: JobPool(notify=…) and Run(notify=…) call it after
    each answer they queue. Calls that come before `fn` has run are one run (a script
    printing a thousand lines wakes the window once per batch). With `owner`, nothing runs
    once that object has been deleted; `close` (GUI thread) ends it for good."""

    def __init__(self, fn: Callable[[], Any], owner: QObject | None = None):
        global _hub
        if _hub is None:
            _hub = _Hub()
        self._hub = _hub
        self.fn, self._owner = fn, owner
        self.key = next(_keys)
        self._hub.wakers[self.key] = self

    def __call__(self) -> None:
        hub = self._hub
        with hub.lock:
            if self.key in hub.waiting or self.key not in hub.wakers:
                return
            hub.waiting.add(self.key)
        hub.poked.emit(self.key)

    def _wake(self) -> None:
        if self._owner is not None and not shiboken6.isValid(self._owner):
            self.close()
            return
        self.fn()

    def close(self) -> None:
        self._hub.wakers.pop(self.key, None)
