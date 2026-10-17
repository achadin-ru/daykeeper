"""Speed of the Qt window's frame, without a screen: start to the first paint, the unlock
screen, unlock and lock, a sweep of window widths, a theme switch. Not part of the unit
run (the name does not start with test_); run by hand after a change to the window:

    python -X utf8 tests/bench_qt_shell.py

On a temporary vault only. Paints are forced with repaint(), so a number includes the
layout and the drawing of what is on screen, as a real frame would."""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.qt_helpers import pump, qt_app  # noqa: E402  (sets the offscreen platform first)

PASSWORD = "bench-master-1"

CHILD = r"""
import os, sys, time
sys.path.insert(0, {root!r})
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication
app = QApplication([]); app.setStyle("Fusion")
from daykeeper.qt import theme
from daykeeper.qt.app import MainWindow
theme.apply(app, theme.LIGHT)
class First(QObject):
    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Paint:
            print(time.time_ns()); sys.stdout.flush(); os._exit(0)
        return False
win = MainWindow({vault!r}, preview=True)
f = First(); win.unlock_screen.installEventFilter(f)
win.show()
app.exec()
"""


def ms(seconds: float) -> float:
    return round(seconds * 1000, 1)


def start_to_first_paint(vault: Path, runs: int = 5) -> list[float]:
    """From the process start to the first paint of the unlock screen (Python, imports, Qt)."""
    out = []
    code = CHILD.format(root=str(ROOT), vault=str(vault))
    for _ in range(runs):
        t0 = time.time_ns()
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, env=dict(os.environ))  # noqa: S603
        out.append(round((int(done.stdout.strip().splitlines()[-1]) - t0) / 1e6, 1))
    return out


def main() -> None:
    from PySide6.QtWidgets import QApplication

    from daykeeper.qt import theme
    from daykeeper.qt.app import MainWindow
    from daykeeper.settings import write_settings
    from daykeeper.vault import Vault

    app = qt_app()
    tmp = Path(tempfile.mkdtemp())
    vault = tmp / "bench.pkv"
    write_settings(tmp / "settings.json", {"language": "en", "geometry": "1180x720+20+20"})
    Vault.create(vault, PASSWORD)
    results: dict[str, object] = {}

    results["start_to_first_paint_ms"] = start_to_first_paint(vault)

    t = time.perf_counter()
    win = MainWindow(vault, preview=True)
    results["window_built_ms"] = ms(time.perf_counter() - t)
    t = time.perf_counter()
    win.show()
    win.repaint()
    QApplication.processEvents()
    results["first_show_ms"] = ms(time.perf_counter() - t)

    opened = Vault.open(vault, PASSWORD)  # the key is derived on a thread in the app; not the window's time
    switches_in, switches_out = [], []
    for _ in range(10):
        t = time.perf_counter()
        win.unlock(vault=opened)
        win.repaint()
        QApplication.processEvents()
        switches_in.append(ms(time.perf_counter() - t))
        t = time.perf_counter()
        win.lock()  # saves the vault first, as a real lock does
        win.repaint()
        pump()
        switches_out.append(ms(time.perf_counter() - t))
        opened = Vault.open(vault, PASSWORD)
    results["unlock_to_main_ms"] = {"median": round(statistics.median(switches_in), 1), "max": max(switches_in)}
    results["lock_to_unlock_screen_ms"] = {"median": round(statistics.median(switches_out), 1), "max": max(switches_out)}

    for screen in ("unlock", "main"):
        if screen == "main":
            win.unlock(vault=opened)
        steps = []
        for w in list(range(860, 1700, 40)) + list(range(1700, 860, -40)):
            t = time.perf_counter()
            win.resize(w, 720)
            QApplication.processEvents()
            win.repaint()
            steps.append(time.perf_counter() - t)
        results[f"resize_step_{screen}_ms"] = {"median": ms(statistics.median(steps)), "max": ms(max(steps)), "steps": len(steps)}

    win.lock()
    flips = []
    for name in ("dark", "light") * 3:
        t = time.perf_counter()
        theme.apply(app, theme.THEMES[name])
        win.repaint()
        QApplication.processEvents()
        flips.append(time.perf_counter() - t)
    results["theme_switch_ms"] = {"median": ms(statistics.median(flips)), "max": ms(max(flips))}
    win.close()
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
