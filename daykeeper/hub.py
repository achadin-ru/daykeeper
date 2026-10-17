"""The event hub: one place the integrations publish their events to, and everyone who
cares (the feed, the routines started by an event, later the digest and the calendar)
subscribes to. Pure (no tkinter): the window publishes from its own thread."""

from __future__ import annotations

import sys
import traceback
from collections.abc import Callable
from types import TracebackType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .gitlab import Event

Subscriber = Callable[[list["Event"]], None]
ErrorSink = Callable[[type[BaseException], BaseException, TracebackType | None], object]


def _to_stderr(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
    """The default sink: the console, when there is one (a built app has none)."""
    if sys.stderr is not None:
        traceback.print_exception(exc_type, exc, tb, file=sys.stderr)


class Hub:
    """`publish` hands every subscriber the whole batch. An error in one subscriber goes
    to `on_error` (errlog.write_error bound to the log path, in the app) and does not stop
    the others."""

    def __init__(self, on_error: ErrorSink | None = None):
        self._subscribers: list[Subscriber] = []
        self.on_error: ErrorSink = on_error or _to_stderr

    def subscribe(self, fn: Subscriber) -> None:
        if fn not in self._subscribers:
            self._subscribers.append(fn)

    def unsubscribe(self, fn: Subscriber) -> None:
        if fn in self._subscribers:
            self._subscribers.remove(fn)

    def publish(self, events: list[Event]) -> None:
        if not events:
            return
        for fn in list(self._subscribers):  # a subscriber may unsubscribe while called
            try:
                fn(list(events))  # its own copy: one subscriber cannot eat the batch of the next
            except Exception as exc:
                try:
                    self.on_error(type(exc), exc, exc.__traceback__)
                except Exception:  # noqa: S110 — a broken error sink must not stop the others either
                    pass
