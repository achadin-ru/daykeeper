"""Helpers for the tests of the Qt window: one QApplication per process, always without a
screen (the offscreen platform draws into memory), and the two traps of QTest.

A module of Qt tests starts with `require_qt()`: without PySide6 installed (the Tk build's
environment) the whole module is skipped, not failed."""
from __future__ import annotations

import os
import sys
import time
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"  # never a window on the desktop of the person running the tests
for _name in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
    os.environ.pop(_name, None)
if sys.platform == "win32" and "QT_QPA_FONTDIR" not in os.environ:
    # offscreen looks for fonts in PySide6's own folder, which has none: text came out
    # as boxes and every width measured from it was wrong
    os.environ["QT_QPA_FONTDIR"] = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")

from daykeeper import win32  # noqa: E402

# the same sizes on every machine, whatever its Windows text size (read when the Qt theme
# is imported, so before any test imports it)
REAL_TEXT_SCALE = win32.text_scale
win32.text_scale = lambda: 1.0

try:
    import PySide6  # noqa: F401
except ImportError:
    HAS_QT = False
else:
    HAS_QT = True


def require_qt() -> None:
    if not HAS_QT:
        raise unittest.SkipTest("PySide6 is not installed")


def qt_app():
    """The one QApplication of the process, in the light theme and the Fusion style."""
    from PySide6.QtWidgets import QApplication

    from daykeeper.qt import theme

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
        app.setStyle(theme.make_style())
    theme.TEXT_SCALE = 1.0  # see win32.text_scale above; a test that changed it is undone
    theme.apply(app, theme.LIGHT)
    return app


def pump(ms: int = 0) -> None:
    """Run the event loop for about `ms` (at least once), the deferred deletes too: the app's
    own loop runs them, processEvents() alone does not, and widgets dropped with
    deleteLater() would pile up (and slow everything that walks the widgets)."""
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    end = time.monotonic() + ms / 1000
    while True:
        QApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        if time.monotonic() >= end:
            return
        time.sleep(0.002)


def wait_until(condition, timeout_ms: int = 5000) -> bool:
    """Run the event loop until `condition()` holds; False when the time ran out."""
    from PySide6.QtWidgets import QApplication

    end = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.002)
    QApplication.processEvents()
    return bool(condition())


def type_text(widget, text: str) -> None:
    """Type `text` into `widget` key by key. QTest.keyClicks with Cyrillic crashes the
    process without a traceback (Qt 6.12); a key event per character does not."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    for ch in text:
        QTest.sendKeyEvent(QTest.KeyAction.Click, widget, Qt.Key.Key_unknown, ch, Qt.KeyboardModifier.NoModifier)


def activate(window) -> None:
    """A QAction's shortcut fires only in the active window."""
    from PySide6.QtTest import QTest

    window.show()
    window.activateWindow()
    QTest.qWaitForWindowActive(window, 1000)


class FakeClipboard:
    """Stands in for the system clipboard: a test must never write to the real one."""

    def __init__(self):
        self.text: str | None = None

    def put(self, text: str) -> bool:
        self.text = text
        return True

    def get(self) -> str | None:
        return self.text

    def clear(self) -> None:
        self.text = None


def open_window(folder, password: str = "test-master-1", unlock: bool = True, language: str = "en", errors: list | None = None):
    """A MainWindow on a vault in `folder` (a temp dir), shown offscreen and active, with a
    fake clipboard and errors collected in `errors`; unlocked (and created) by default."""
    from pathlib import Path

    from daykeeper.clipboard import SecretClipboard
    from daykeeper.qt.app import MainWindow
    from daykeeper.settings import write_settings

    path = Path(folder) / "test.pkv"
    write_settings(path.parent / "settings.json", {"language": language, "geometry": "1200x760+20+20"})
    win = MainWindow(path, preview=True)
    clip = FakeClipboard()
    win.fake_clip = clip
    win._clip = SecretClipboard(clip.put, clip.get, clip.clear, put=clip.put, clear_if=lambda e: (clip.clear() or True) if clip.text == e else False, read=clip.get)
    win._qt_clip_put = clip.put
    win._qt_clip_get = clip.get
    win._qt_clip_clear = clip.clear
    sink = errors if errors is not None else []
    win.report_error = lambda t, e, tb: sink.append(repr(e))
    activate(win)
    if unlock:
        win.unlock(password)
        pump()
    return win


def close_window(win) -> None:
    win.close()
    win.deleteLater()
    pump()
