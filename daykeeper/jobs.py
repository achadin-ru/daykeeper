"""Background work for a window: a thread pool whose answers are read on the window's
thread. A job is `submit`ted with a kind and whatever the caller wants back with the
answer; `drain` hands the finished ones over as JobResult, an exception as an error
result. The threads never touch the models — the drain on the window's thread does.
`notify`, when given, is called on the job's thread after each answer is queued (the Qt
window drains on that call; the Tk one polls). Pure (no tkinter)."""

from __future__ import annotations

import queue
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from collections.abc import Callable

if TYPE_CHECKING:
    from concurrent.futures import ThreadPoolExecutor


@dataclass
class JobResult:
    kind: str
    meta: dict = field(default_factory=dict)  # what was given with the job, back unchanged
    value: Any = None
    error: BaseException | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class JobPool:
    """Threads made on the first job; `drain()` on the window's thread takes the answers."""

    def __init__(self, workers: int = 3, name: str = "jobs", notify: Callable[[], None] | None = None):
        self.workers, self.name, self.notify = workers, name, notify
        self._pool: ThreadPoolExecutor | None = None
        self._results: queue.Queue = queue.Queue()
        self._out = 0  # jobs submitted and not yet drained

    @property
    def pending(self) -> int:
        return self._out

    def submit(self, kind: str, fn: Callable[..., Any], *args: Any, **meta: Any) -> None:
        if self._pool is None:
            from concurrent.futures import ThreadPoolExecutor  # 20 ms at start otherwise, for a pool made on the first job

            self._pool = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix=self.name)
        self._out += 1

        def work() -> None:
            try:
                self._results.put(JobResult(kind, meta, fn(*args)))
            except BaseException as exc:  # noqa: BLE001 — the answer carries the error; a thread must not die silently
                self._results.put(JobResult(kind, meta, None, exc))
            if self.notify is not None:
                self.notify()

        self._pool.submit(work)

    def drain(self) -> list[JobResult]:
        """Every answer that is there, oldest first; never blocks."""
        out: list[JobResult] = []
        while True:
            try:
                out.append(self._results.get_nowait())
            except queue.Empty:
                break
        self._out -= len(out)
        return out

    def close(self) -> None:
        """Let the threads finish what they run; nothing waits for them."""
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
