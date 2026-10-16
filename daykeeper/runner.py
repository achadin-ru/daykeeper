"""Running a routine: a subprocess read by a thread, its lines and end through a queue;
or a separate console window that stays open. No tkinter: tested with a Python child."""

from __future__ import annotations

import os
import queue
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Any

from . import win32

MAC = sys.platform == "darwin"
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010


def _oem_encoding() -> str:
    return win32.oem_codepage() or "utf-8"


def decode_line(raw: bytes) -> str:
    """Script output: UTF-8 when it is, else the console code page (cmd and old tools)."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(_oem_encoding(), errors="replace")


class Run:
    """One run of a routine in a background thread; lines and the end come through a
    queue as ("line", text) and ("end", exit code). `auto` — started by the schedule."""

    def __init__(self, routine: Any, argv: list[str] | None, shell: str, cwd: str, auto: bool = False):
        self.routine = routine
        self.auto = auto
        self.started = time.time()
        self.ended: float | None = None
        self.code: int | None = None
        self.lines: list[str] = []
        self.queue: queue.Queue = queue.Queue()
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        flags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self.proc: subprocess.Popen[bytes] | None
        try:
            self.proc = subprocess.Popen(  # noqa: S603 — the user's own routine from the encrypted vault
                argv if argv else shell,
                shell=not argv,
                cwd=cwd or None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                creationflags=flags,
                start_new_session=sys.platform != "win32",  # its own group: Stop ends the children too
            )
        except OSError as exc:
            self.proc = None
            self.lines.append(str(exc))
            self.code = -1
            self.ended = time.time()
            return
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        if self.proc is None or self.proc.stdout is None:
            return
        for raw in iter(self.proc.stdout.readline, b""):
            self.queue.put(("line", decode_line(raw).rstrip("\r\n")))
        self.proc.wait()
        self.proc.stdout.close()
        self.queue.put(("end", self.proc.returncode))

    def stop(self) -> None:
        """End the run with everything it started: `cmd /c x.bat` or `powershell -File` run
        the script in a child, and ending the shell alone leaves the script running."""
        if self.proc is None or self.proc.poll() is not None:
            return
        if sys.platform == "win32":
            try:
                subprocess.run(  # noqa: S603 — taskkill, on our own child
                    ["taskkill", "/T", "/F", "/PID", str(self.proc.pid)],  # noqa: S607 — a system tool
                    capture_output=True, timeout=10, creationflags=CREATE_NO_WINDOW,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        else:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except OSError:
                pass
        if self.proc.poll() is None:
            self.proc.terminate()

    @property
    def running(self) -> bool:
        return self.ended is None

    def duration(self) -> float:
        return (self.ended or time.time()) - self.started

    def take(self) -> list[tuple[str, Any]]:
        """What the thread put since the last take: lines, and the end once. A line is
        kept in `lines`; the end sets `code` and `ended`."""
        out: list[tuple[str, Any]] = []
        while True:
            try:
                kind, value = self.queue.get_nowait()
            except queue.Empty:
                break
            if kind == "line":
                self.lines.append(value)
            else:
                self.code, self.ended = value, time.time()
            out.append((kind, value))
        return out


def open_in_console(plan: Any) -> bool:
    """Start the plan in a separate console window that stays open after the script ends.
    False — no terminal program was found (Linux without one); the caller runs it quietly."""
    cwd = plan.cwd or None
    if sys.platform == "win32":
        cmdline = plan.shell or subprocess.list2cmdline(plan.argv)
        subprocess.Popen(["cmd", "/k", cmdline], cwd=cwd, creationflags=CREATE_NEW_CONSOLE)  # noqa: S603,S607
        return True
    if MAC:
        line = (f"cd {shlex.quote(cwd)}; " if cwd else "") + (plan.shell or shlex.join(plan.argv))
        script = 'tell application "Terminal" to do script ' + '"' + line.replace("\\", "\\\\").replace('"', '\\"') + '"'
        subprocess.Popen(["osascript", "-e", script, "-e", 'tell application "Terminal" to activate'])  # noqa: S603,S607
        return True
    term = shutil.which("x-terminal-emulator") or shutil.which("gnome-terminal") or shutil.which("xterm")
    if not term:
        return False
    line = plan.shell or shlex.join(plan.argv)
    subprocess.Popen([term, "-e", "sh", "-c", line + "; exec sh"], cwd=cwd)  # noqa: S603
    return True
