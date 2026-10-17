"""The window's keyboard shortcuts as QActions.

The Tk window matched Ctrl+letter by the key's code so that it worked in a Russian layout;
Qt already adds the Latin Ctrl+letter to a key pressed in such a layout (checked by hand
on Windows 11 with PySide6 6.12.0, see the port plan 5.4), so a plain QKeySequence does.
"Ctrl" in a sequence is Cmd on macOS. A shortcut whose action needs an open vault does
nothing while locked."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QWidget

# what is wired up so far; app.APP_SHORTCUTS of the Tk window is the full list to reach
SHORTCUTS = {
    "lock": "Ctrl+L",
    "save": "Ctrl+S",
}


def install(window: QWidget, handlers: dict[str, Callable[[], object]]) -> dict[str, QAction]:
    """One window-wide action per entry of SHORTCUTS that has a handler."""
    actions: dict[str, QAction] = {}
    for name, keys in SHORTCUTS.items():
        handler = handlers.get(name)
        if handler is None:
            continue
        action = QAction(name, window)
        action.setShortcut(QKeySequence(keys))
        action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        action.triggered.connect(handler)
        window.addAction(action)
        actions[name] = action
    return actions
