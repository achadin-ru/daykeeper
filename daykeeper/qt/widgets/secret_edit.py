"""A one-line field for a password, a PIN or a secret value.

QLineEdit keeps an undo history even in password mode and has no switch to turn it off;
`clear()` is an undoable edit: once the field is shown (the eye), Ctrl+Z brings the
password back. `setText` resets the history. This class makes the safe way the only way: `clear`
and `wipe` both go through `setText("")`. It also keeps the secret away from the input
method's prediction and memory, and has no context menu (Paste / Delete stay enabled on a
password field and `cut()` erases the text without copying it)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLineEdit, QWidget

from .. import icons

SECRET_HINTS = Qt.InputMethodHint.ImhSensitiveData | Qt.InputMethodHint.ImhNoPredictiveText | Qt.InputMethodHint.ImhNoAutoUppercase | Qt.InputMethodHint.ImhHiddenText


class SecretEdit(QLineEdit):
    """Hidden by default; `set_shown(True)` shows the text (the eye), `wipe()` empties
    the field so that nothing can be undone back into it."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setEchoMode(QLineEdit.EchoMode.Password)
        self.setInputMethodHints(SECRET_HINTS)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setDragEnabled(False)
        self._eye: QAction | None = None

    def clear(self) -> None:  # the slot of QLineEdit, made safe
        self.wipe()

    def wipe(self) -> None:
        """Empty the field and its undo history (see the module text)."""
        self.setText("")

    def shown(self) -> bool:
        return self.echoMode() == QLineEdit.EchoMode.Normal

    def set_shown(self, on: bool) -> None:
        self.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)
        if self._eye is not None:
            self._eye.setIcon(icons.eye(shown=on))

    def add_eye(self, tooltip: str) -> QAction:
        """The eye inside the field at its right edge; a click shows or hides the text.
        The returned action is triggered by the click (several fields can share one state)."""
        self._eye = self.addAction(icons.eye(), QLineEdit.ActionPosition.TrailingPosition)
        self._eye.setToolTip(tooltip)
        return self._eye
