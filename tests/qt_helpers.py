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
        app.setStyle("Fusion")
    theme.apply(app, theme.LIGHT)
    return app


def pump(ms: int = 0) -> None:
    """Run the event loop for about `ms` (at least once)."""
    from PySide6.QtWidgets import QApplication

    end = time.monotonic() + ms / 1000
    while True:
        QApplication.processEvents()
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
