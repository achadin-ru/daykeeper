"""A small always-on-top card in the bottom-right corner of the screen's work area; several
stack upwards. A click opens the link or runs the action; after TOAST_MS it goes by itself.

A tool window without a frame that is shown without being activated: it does not take the
focus from the field being typed in, has no taskbar button, and on macOS does not show in
the Dock. It closes on lock (the window's register_child): an MR title or the result of
a run must not outlive the lock."""

from __future__ import annotations

import webbrowser
from collections.abc import Callable
from typing import ClassVar

import shiboken6
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from ..errors import guard_events
from .common import IconLabel

TOAST_MS = 8_000
WIDTH = 360  # px, as in the Tk window
EDGE = 24  # px from the right edge of the work area
GAP = 8  # px between stacked cards


@guard_events
class Toast(QWidget):
    """A card with a title and a text; `url` or `action` makes it clickable."""

    stack: ClassVar[list[Toast]] = []

    def __init__(self, window: QWidget | None, title: str, text: str, url: str = "", action: Callable[[], object] | None = None):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)  # the card's rounded corners show what is under them
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._url, self._action, self._window = url, action, window
        card = QFrame(self)
        card.setObjectName("Toast")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        body = QVBoxLayout(card)
        body.setContentsMargins(14, 10, 14, 10)
        body.setSpacing(2)
        head = IconLabel(title, "", card, size=14)  # "⚡ name": the drawn icon, then the name
        head.setObjectName("ToastTitle")
        self.text = QLabel(text, card)
        self.text.setWordWrap(True)
        body.addWidget(head)
        body.addWidget(self.text)
        if url or action is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(WIDTH)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.close)
        register = getattr(window, "register_child", None)
        if register is not None:
            register(self)
        Toast.stack.append(self)
        self.adjustSize()
        Toast.place_all(window)
        self.show()
        self._timer.start(TOAST_MS)

    @staticmethod
    def place_all(window: QWidget | None = None) -> None:
        """The newest at the bottom, each older one above it, in the work area of the
        screen the window is on (the taskbar is outside the work area)."""
        screen = window.screen() if window is not None and shiboken6.isValid(window) else QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        y = area.bottom() - EDGE
        for toast in reversed(Toast.stack):
            if not shiboken6.isValid(toast):
                continue
            h = toast.sizeHint().height()
            y -= h
            toast.setGeometry(area.right() - EDGE - WIDTH, y, WIDTH, h)
            y -= GAP

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        url, action = self._url, self._action
        self.close()
        if url.startswith(("https://", "http://")):  # a web page, never a program or a file
            webbrowser.open(url)
        if action is not None:
            action()

    def on_lock(self) -> None:
        """The window locks: go now, without the action; the timer dies with the card."""
        self._timer.stop()
        self._action = None

    def closeEvent(self, event) -> None:
        self._timer.stop()
        if self in Toast.stack:
            Toast.stack.remove(self)
        super().closeEvent(event)
        Toast.place_all(self._window)
