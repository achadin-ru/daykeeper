"""Locks of the Qt window: the idle lock of the app, the lock on minimize, the limit on wrong
codes, and the prompts that ask for the master password before a secret or a weaker
setting. The same rules as lock_view.SecurityController of the Tk window.

Not here yet (stage 3 of the port): the guard screen over the Passwords tab and the PIN
dialog. `secrets_open()` already answers as the Tk one does, so the pages can ask it."""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QApplication

from ..i18n import _
from ..security import FAIR, GOOD, GUARD_PIN, STRONG, WEAK, Attempts, IdleClock, password_strength
from .dialogs.base import ask_secret
from .timers import Ticker

IDLE_CHECK_MS = 5_000
MINIMIZE_LOCK_MS = 50  # the window must still be minimized after this
# what counts as the user doing something (Tk: <KeyPress>, <ButtonPress>, <MouseWheel>, <Motion>)
ACTIVITY = frozenset({QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress, QEvent.Type.Wheel, QEvent.Type.MouseMove})


def strength_text(pw: str) -> str:
    """Under a new master password: how hard it is to guess, and what helps. The same
    words as lock_view.strength_text, which cannot be imported here: that module loads Tk."""
    if not pw:
        return ""
    return {
        WEAK: _("Weak: easy to guess. A phrase of four unrelated words is long and easy to remember."),
        FAIR: _("Fair. Longer is better: a phrase of four unrelated words."),
        GOOD: _("Good."),
        STRONG: _("Strong."),
    }[password_strength(pw)]


class SecurityController(QObject):
    """Made once with the window, a child of it. `vault_locked` — the Passwords tab is
    behind its guard; `secrets_open()` is what every place that shows a secret asks first."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._idle = IdleClock(time.monotonic())
        self.attempts = Attempts()  # wrong codes in a row; only the master password at unlock clears it
        self.vault_locked = False
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)  # removed by Qt when this object goes with the window
        self.ticker = Ticker(self, IDLE_CHECK_MS, self.check_idle)
        self.ticker.start()

    @property
    def vault(self):
        return self.window.vault

    def reset(self) -> None:
        """The main screen is made anew (unlock): no guard is on it yet."""
        self.vault_locked = False

    # ---- idle tracking

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() in ACTIVITY:
            self._idle.touch(time.monotonic())
        return False

    def touch(self) -> None:
        self._idle.touch(time.monotonic())

    def check_idle(self, now: float | None = None) -> None:
        if self.vault is None:
            return
        prefs = self.vault.security
        now = time.monotonic() if now is None else now
        if self._idle.due(now, prefs.app_lock_min):
            self.window.lock()
            return
        if prefs.guarded and not self.vault_locked and self._idle.due(now, prefs.vault_lock_min):
            self.lock_secrets()

    def on_minimized(self) -> None:
        """The window went to the taskbar: lock it there when the settings ask for it."""
        if self.vault is None or not self.vault.security.lock_on_minimize:
            return
        QTimer.singleShot(MINIMIZE_LOCK_MS, self, self._lock_if_still_minimized)

    def _lock_if_still_minimized(self) -> None:
        if self.vault is not None and self.window.isMinimized():
            self.window.lock()

    # ---- the Passwords tab guard (the screen itself comes with the Passwords tab)

    def secrets_open(self) -> bool:
        return self.vault is not None and (not self.vault.security.guarded or not self.vault_locked)

    def lock_secrets(self) -> None:
        """Lock the secrets right now. Without the Passwords tab there is no screen to put
        over it yet: only the flag, which `secrets_open` and the clipboard obey."""
        if self.vault is None or not self.vault.security.guarded:
            return
        self.window.save_now()
        self.vault_locked = True
        self.window.set_status(_("Passwords are locked"))

    def unlock_secrets(self) -> None:
        self.vault_locked = False
        self.touch()

    def _code_matches(self, code: str) -> bool:
        prefs = self.vault.security
        if prefs.vault_guard == GUARD_PIN:
            return prefs.check_pin(code)
        return self.vault.check_password(code)

    def limited(self, check: Callable[[str], bool], wrong: str) -> Callable[[str], bool | str]:
        """`check(code)` behind the attempt limit (security.Attempts): the result is True
        for a right code, else the text to show under the field."""
        def accept(code: str) -> bool | str:
            now = time.monotonic()
            wait = self.attempts.wait(now)
            if wait:
                return _("Too many wrong tries. Wait {n} s").format(n=math.ceil(wait))
            if check(code):
                self.attempts.ok()
                return True
            if self.attempts.fail(now):
                QTimer.singleShot(0, self.window, self.window.lock)  # after the prompt has answered
                return _("Too many wrong tries: the app is locked")
            return wrong
        return accept

    def _code_label(self) -> str:
        return _("PIN code") if self.vault.security.vault_guard == GUARD_PIN else _("Master password")

    def require_secrets(self, then: Callable[[], object]) -> None:
        """Run `then` once the secrets are open: right away, or after the code is entered."""
        if self.secrets_open():
            then()
            return

        def opened() -> None:
            self.unlock_secrets()
            then()

        ask_secret(self.window, _("Passwords are locked"), self._code_label(), _("Unlock"), self.limited(self._code_matches, _("Wrong code")), opened)

    def confirm_master(self, title: str, then: Callable[[], object]) -> None:
        """Ask for the master password, then run `then` — for changes that weaken the locks."""
        ask_secret(self.window, title, _("Master password"), _("Continue"), self.limited(self.vault.check_password, _("Wrong master password")), then)
