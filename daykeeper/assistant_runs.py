"""Background runs of an assistant: the quiet channel (`claude -p`, one answer shown inside
Daykeeper) and the GeckIt channel (`geckit start`, which waits until the person answers in
GeckIt). Each run is a child process read by a thread; when it ends the thread fills the
run in and calls `notify(run)` — the window passes a callable that only queues the run
for its own thread, as JobPool's answers are drained there. No tkinter: tested with a
Python child standing in for the assistant."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .assistants import Assignment, GeckitInstall, geckit_env, plan_env
from .i18n import _

PATIENCE = 90.0  # seconds one quiet answer is waited on (GeckIt waits as long)
GECKIT_PATIENCE = 30 * 60.0  # `geckit start` waits for the person to answer
HISTORY = 20  # quiet runs kept in memory
CREATE_NO_WINDOW = 0x08000000
ERR_TAIL = 2000


def _flags() -> int:
    return CREATE_NO_WINDOW if sys.platform == "win32" else 0


def kill_tree(proc: subprocess.Popen) -> None:
    """End the child with everything it started (as runner.Run.stop): `taskkill /T` on
    Windows, the process group elsewhere."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(  # noqa: S603 — taskkill, on our own child
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],  # noqa: S607 — a system tool
                capture_output=True, timeout=10, creationflags=CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass
    if proc.poll() is None:
        proc.kill()


def _decode(raw: bytes | None) -> str:
    return (raw or b"").decode("utf-8", errors="replace")


@dataclass
class Answer:
    text: str
    cost: float | None = None  # total_cost_usd
    duration_ms: int | None = None
    is_error: bool = False


def parse_answer(out: str) -> Answer | None:
    """What `claude -p --output-format json` printed: one object with `result`,
    `total_cost_usd`, `duration_ms`, `is_error` (a list of messages ending in the result
    object is read too). None — it is not that."""
    out = out.strip()
    try:
        data: Any = json.loads(out)
    except (ValueError, RecursionError):  # RecursionError: nesting too deep to decode
        lines = [x for x in out.splitlines() if x.strip().startswith("{")]
        try:
            data = json.loads(lines[-1]) if lines else None
        except (ValueError, RecursionError):
            return None
    if isinstance(data, list):
        data = next((d for d in reversed(data) if isinstance(d, dict) and d.get("type") == "result"), None)
    if not isinstance(data, dict) or "result" not in data and "is_error" not in data:
        return None
    result = data.get("result")
    cost, ms = data.get("total_cost_usd"), data.get("duration_ms")
    return Answer(
        text=result.strip() if isinstance(result, str) else "",
        cost=float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
        duration_ms=int(ms) if isinstance(ms, (int, float)) and not isinstance(ms, bool) else None,
        is_error=data.get("is_error") is True,
    )


class _ChildRun:
    """A child given `stdin`, read to the end by a thread within `timeout` seconds; then
    `finish` fills the run in and `notify(self)` is called from that thread. `env` None —
    this process's environment without the variables of OFF_PLAN, never all of it."""

    def __init__(self, argv: list[str], stdin: str, cwd: str | None, env: Mapping[str, str] | None,
                 timeout: float, notify: Callable[[Any], None] | None):
        self.argv = argv
        self.started = time.time()
        self.ended: float | None = None
        self.code: int | None = None
        self.timed_out = False
        self.stopped = False
        self.error = ""  # why there is no answer ("" — there is one)
        self._notify = notify
        self._timeout = timeout
        self._stdin = stdin.encode("utf-8")
        self.proc: subprocess.Popen[bytes] | None
        try:
            self.proc = subprocess.Popen(  # noqa: S603 — the user's own assistant, the argv built by assistants.py
                argv,
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=dict(env) if env is not None else plan_env(os.environ),
                creationflags=_flags(),
                start_new_session=sys.platform != "win32",  # its own group: a stop ends its children too
            )
        except OSError as exc:
            self.proc = None
            self.code = -1
            self.error = _("Could not start {program}: {error}").format(program=argv[0] if argv else "", error=exc)
            self.failed()
            self._done()
            return
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        if self.proc is None:
            return
        try:
            out, err = self.proc.communicate(self._stdin, timeout=self._timeout)
        except subprocess.TimeoutExpired:
            self.timed_out = True
            kill_tree(self.proc)
            try:
                out, err = self.proc.communicate(timeout=10)
            except (subprocess.TimeoutExpired, ValueError):
                out, err = b"", b""
        except (OSError, ValueError):
            out, err = b"", b""
            self.proc.wait()
        self.code = self.proc.returncode
        try:
            self.finish(_decode(out), _decode(err)[-ERR_TAIL:])
        except Exception as exc:  # noqa: BLE001 — the run must still end, notify and clean up
            self.error = _("Daykeeper could not read the answer: {error}").format(error=exc)
            self.failed()
        finally:
            self._done()

    def finish(self, out: str, err: str) -> None:
        raise NotImplementedError

    def failed(self) -> None:
        """The child could not start; `error` says why."""

    def _done(self) -> None:
        self.cleanup()
        self.ended = time.time()
        if self._notify is not None:
            self._notify(self)

    def cleanup(self) -> None:
        pass

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.stopped = True
            kill_tree(self.proc)

    @property
    def running(self) -> bool:
        return self.ended is None

    def duration(self) -> float:
        return (self.ended or time.time()) - self.started


class QuietRun(_ChildRun):
    """One quiet answer: the assignment's text on stdin, in a scratch folder (so no project's
    hooks start), no window; the answer, its cost and its time from the JSON."""

    def __init__(self, assignment: Assignment, argv: list[str], env: Mapping[str, str] | None = None,
                 timeout: float = PATIENCE, notify: Callable[[Any], None] | None = None, text: str | None = None):
        self.assignment = assignment
        self.text = ""
        self.cost: float | None = None
        self.duration_ms: int | None = None
        self.is_error = False
        self.scratch = tempfile.mkdtemp(prefix="daykeeper-ai-")
        super().__init__(argv, assignment.text if text is None else text, self.scratch, env, timeout, notify)

    def finish(self, out: str, err: str) -> None:
        if self.stopped:
            self.error = _("Stopped")
            return
        if self.timed_out:
            self.error = _("The assistant did not answer in {n} s").format(n=round(self._timeout))
            return
        answer = parse_answer(out)
        if answer is None:
            self.error = err.strip() or out.strip()[-ERR_TAIL:] or _("The assistant answered with something Daykeeper cannot read")
            return
        self.cost, self.duration_ms, self.is_error = answer.cost, answer.duration_ms, answer.is_error
        if answer.is_error:
            self.error = answer.text or err.strip() or _("The assistant reported an error")
        elif not answer.text:
            self.error = _("The assistant answered with nothing")
        else:
            self.text = answer.text

    def cleanup(self) -> None:
        shutil.rmtree(self.scratch, ignore_errors=True)

    @property
    def ok(self) -> bool:
        return not self.running and not self.error


class QuietRuns:
    """The quiet runs of a session: started here, the last HISTORY kept in memory, newest
    last. `notify` is called from the reading thread with the finished run."""

    def __init__(self, notify: Callable[[QuietRun], None] | None = None, keep: int = HISTORY, timeout: float = PATIENCE):
        self.notify = notify
        self.timeout = timeout
        self.history: deque[QuietRun] = deque(maxlen=keep)

    def start(self, assignment: Assignment, argv: list[str], env: Mapping[str, str] | None = None, text: str | None = None) -> QuietRun:
        run = QuietRun(assignment, argv, env, self.timeout, self.notify, text)
        self.history.append(run)
        return run

    def running(self) -> list[QuietRun]:
        return [r for r in self.history if r.running]

    def stop_all(self) -> None:
        for run in self.running():
            run.stop()


# ---- GeckIt


@dataclass
class GeckitAnswer:
    state: str  # "started" | "queued" | "refused" | "error"
    id: str = ""
    note: str = ""  # the person's note, or what went wrong
    reply: str = ""


def parse_geckit_answer(text: str) -> GeckitAnswer:
    """What `geckit start` printed for one task: JSON (`--json`) or its plain lines —
    "1 Started <id>: note", "1 Queued <id>", "1 Refused", "Reply: …". Anything else (GeckIt
    not running, a refusal of the request itself) is an error with the text as the note."""
    text = text.strip()
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        data = None
    if isinstance(data, dict) and isinstance(data.get("tasks"), list) and data["tasks"] and isinstance(data["tasks"][0], dict):
        task = data["tasks"][0]
        state = task.get("answer")
        said = data.get("reply")
        reply = said if isinstance(said, str) else ""
        if state in ("started", "queued", "refused"):
            return GeckitAnswer(str(state), str(task.get("id") or ""), str(task.get("note") or ""), reply)
        return GeckitAnswer("error", note=text)
    lines = text.splitlines()
    reply = next((x[len("Reply:"):].strip() for x in lines if x.startswith("Reply:")), "")
    for line in lines:
        words = line.split(" ", 2)
        if len(words) >= 2 and words[0].isdigit():
            state = words[1].lower()
            if state == "refused":
                return GeckitAnswer("refused", reply=reply)
            if state in ("started", "queued"):
                rest = words[2] if len(words) > 2 else ""
                tid, _sep, note = rest.partition(": ")
                return GeckitAnswer(state, tid.strip(), note.strip(), reply)
    return GeckitAnswer("error", note=text)


class GeckitSend(_ChildRun):
    """`geckit start` in the background: GeckIt shows the request to the person and the
    command waits for the answer, which `answer` holds once the run has ended. `env` None —
    `geckit_env(install)`: without ELECTRON_RUN_AS_NODE GeckIt's program would open its
    window instead of running the command."""

    def __init__(self, assignment: Assignment, argv: list[str], install: GeckitInstall, env: Mapping[str, str] | None = None,
                 timeout: float = GECKIT_PATIENCE, notify: Callable[[Any], None] | None = None):
        self.assignment = assignment
        self.answer: GeckitAnswer | None = None
        super().__init__(argv, "", None, geckit_env(install) if env is None else env, timeout, notify)

    def failed(self) -> None:
        self.answer = GeckitAnswer("error", note=self.error)

    def finish(self, out: str, err: str) -> None:
        if self.timed_out or self.stopped:
            self.error = _("Stopped") if self.stopped else _("GeckIt did not answer in {n} min").format(n=round(self._timeout / 60))
            self.answer = GeckitAnswer("error", note=self.error)
            return
        self.answer = parse_geckit_answer(out if out.strip() else err)
        if self.answer.state == "error":
            self.error = self.answer.note or _("GeckIt did not start it")
