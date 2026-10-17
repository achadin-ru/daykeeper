"""The unlock screen: the master password of an existing vault, or a new one twice with a
hint of its strength. The same screen and rules as App.show_unlock / _do_unlock of the
Tk window.

One screen for the life of the window (a page of its stack): `refresh()` puts it back to
an empty form for the vault as it is now (new or existing, the language), and wipes every
field so nothing typed can be undone back into it. The key is derived on a thread, the
window keeps painting meanwhile."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QVBoxLayout, QWidget

from .. import palette
from ..i18n import LANGUAGES, _, language
from ..vault import BadFile, WrongPassword
from . import jobs, theme
from .dialogs.base import button, label
from .lock import strength_text
from .widgets.secret_edit import SecretEdit

FIELD_CHARS = 34  # the width of the password field in average characters, as in the Tk window
LOGO = 64  # px


class UnlockScreen(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.shell = window  # the main window (not `window`: that is QWidget.window())
        self.setObjectName("Unlock")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.opening = False  # the master password is being checked on a thread
        self.creating = False  # no vault file yet: the password is asked twice

        panel = QFrame(self)
        panel.setObjectName("Panel")
        box = self._box = QVBoxLayout(panel)
        box.setContentsMargins(28, 22, 28, 22)
        box.setSpacing(0)

        self.logo = label("", "", panel)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo.setFixedHeight(LOGO)
        self.title = label(palette.APP_TITLE, "title", panel)
        self.tagline = label("", "muted", panel)
        self.subtitle = label("", "subtitle", panel)
        for lab in (self.title, self.tagline, self.subtitle):
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for lab in (self.tagline, self.subtitle):  # at a large system text size they are wider than the field
            lab.setWordWrap(True)

        self.pw1 = SecretEdit(panel)
        self.pw2 = SecretEdit(panel)
        for field in (self.pw1, self.pw2):
            field.setProperty("role", "big")
            field.returnPressed.connect(self.submit)
        self.eye = self.pw1.add_eye(_("Show the password"))
        self.eye.triggered.connect(self.toggle_shown)
        self.pw1.textChanged.connect(self._on_typed)

        # a long hint or error wraps at the field's width: the panel must not widen as one types
        self.strength = label("", "muted", panel)
        self.repeat_hint = label("", "muted", panel)
        self.error = label("", "error", panel)
        for lab in (self.strength, self.repeat_hint, self.error):
            lab.setWordWrap(True)
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.open_btn = button("", "primary", panel)
        self.open_btn.clicked.connect(self.submit)
        self.lang_btn = button("", "link", panel)
        self.lang_btn.clicked.connect(self._switch_language)

        box.addWidget(self.logo)
        box.addSpacing(12)
        box.addWidget(self.title)
        box.addSpacing(4)
        box.addWidget(self.tagline)
        box.addSpacing(16)
        box.addWidget(self.subtitle)
        box.addSpacing(14)
        box.addWidget(self.pw1)
        box.addSpacing(8)
        box.addWidget(self.pw2)
        box.addSpacing(2)
        box.addWidget(self.strength)
        box.addWidget(self.repeat_hint)
        box.addSpacing(4)
        box.addWidget(self.error)
        box.addSpacing(10)
        box.addWidget(self.open_btn)
        box.addSpacing(16)
        box.addWidget(self.lang_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        self.panel = panel
        self._fit_width()

        # the panel a little above the middle, as the Tk one (rely 0.46)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(panel)
        row.addStretch(1)
        outer = QVBoxLayout(self)
        outer.addStretch(46)
        outer.addLayout(row)
        outer.addStretch(54)

    def _fit_width(self) -> None:
        """The panel is as wide as the password field wants and no wider: the labels under
        it wrap instead of stretching it."""
        self.pw1.ensurePolished()  # the style sheet's font of a "big" field, not the default one
        field = self.pw1.fontMetrics().averageCharWidth() * FIELD_CHARS + 60  # padding and the eye
        margins = self._box.contentsMargins()
        self.panel.setFixedWidth(field + margins.left() + margins.right())

    # ---- the form

    def refresh(self) -> None:
        """An empty form for the vault as it is now; called every time the screen is shown."""
        exists = self.shell.session.exists
        self.creating = not exists
        self.pw1.wipe()
        self.pw2.wipe()
        self.pw1.set_shown(False)
        self.pw2.set_shown(False)
        self.pw2.setVisible(not exists)
        self.strength.setVisible(not exists)
        self.repeat_hint.setVisible(not exists)
        self.strength.setText("")
        self.error.setText("")
        self.tagline.setText(_("passwords · bookmarks · journal · notes · routine · integrations"))
        self.subtitle.setText(_("Enter the master password") if exists else _("Create a master password for the new vault"))
        self.repeat_hint.setText(_("Repeat the password. If you forget it, the data cannot be recovered."))
        self.open_btn.setText(_("Open") if exists else _("Create vault"))
        self.eye.setToolTip(_("Show the password"))
        other_name = next(n for c, n in LANGUAGES if c != language())
        self.lang_btn.setText(_(other_name))
        self._set_logo()
        self._fit_width()
        self.pw1.setFocus()

    def _set_logo(self) -> None:
        pixmap = theme.logo(LOGO, self.devicePixelRatioF())
        if pixmap is not None:
            self.logo.setPixmap(pixmap)
        else:  # a stripped-down build without the pictures: the letter on the accent
            self.logo.setText("D")

    def toggle_shown(self) -> None:
        on = not self.pw1.shown()
        self.pw1.set_shown(on)
        self.pw2.set_shown(on)

    def _on_typed(self, text: str) -> None:
        if self.creating:
            self.strength.setText(strength_text(text))

    def _switch_language(self) -> None:
        other = next(c for c, _n in LANGUAGES if c != language())
        self.shell.set_ui_language(other)

    def leave(self) -> None:
        """The screen goes off (unlocked): nothing typed stays in the fields."""
        self.pw1.wipe()
        self.pw2.wipe()

    # ---- unlocking

    def submit(self) -> None:
        pw = self.pw1.text()
        if not pw:
            self.error.setText(_("Password is empty"))
            return
        if self.creating:
            if pw != self.pw2.text():
                self.error.setText(_("Passwords do not match"))
                return
            if len(pw) < 8:
                self.error.setText(_("At least 8 characters"))
                return
        if self.opening:
            return
        wait = self.shell.security.attempts.wait(time.monotonic())  # the same 1-2-4 s pause as the PIN prompt
        if wait:
            self.error.setText(_("Too many wrong tries. Wait {n} s").format(n=math.ceil(wait)))
            return
        # Argon2 takes a tenth of a second here and more on a slow laptop
        self.opening = True
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.error.setText(_("Opening…"))
        session = self.shell.session
        jobs.run_in_thread(lambda: session.open_vault(pw), self._opened, owner=self, name="unlock")

    def _opened(self, vault, error: BaseException | None) -> None:
        self.opening = False
        QApplication.restoreOverrideCursor()
        if error is None:
            if self.shell.vault is None:
                self.error.setText("")
                self.shell.unlock(vault=vault)
            return
        if isinstance(error, WrongPassword):
            self.shell.security.attempts.fail(time.monotonic())
            self.error.setText(_("Wrong password"))
            self.pw1.wipe()
        elif isinstance(error, BadFile):
            self.error.setText(str(error))
        else:
            self.error.setText("")
            self.shell.report_error(type(error), error, error.__traceback__)
