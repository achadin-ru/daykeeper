"""Locks in the UI: idle lock of the app, the guard screen over the Passwords tab and the
PIN dialog (the security settings live in the settings window).

`SecurityController` is made once with the window. The Passwords tab and every place that shows a secret from the vault (the
bookmark tiles) ask `secrets_open()` first; when the tab is guarded and locked they ask
for the master password or the PIN, then carry on."""

from __future__ import annotations

import math
import time
import tkinter as tk
from tkinter import ttk

from . import palette
from .dialogs import BULLET, Dialog, ask_secret
from .i18n import _
from .security import FAIR, GOOD, GUARD_PIN, STRONG, WEAK, Attempts, IdleClock, password_strength, pin_problem
from .timers import Ticker

IDLE_CHECK_MS = 5_000


def strength_text(pw: str) -> str:
    """Under a new master password: how hard it is to guess, and what helps."""
    if not pw:
        return ""
    level = password_strength(pw)
    return {
        WEAK: _("Weak: easy to guess. A phrase of four unrelated words is long and easy to remember."),
        FAIR: _("Fair. Longer is better: a phrase of four unrelated words."),
        GOOD: _("Good."),
        STRONG: _("Strong."),
    }[level]


def minutes_label(m: int) -> str:
    return _("never") if m == 0 else _("{n} min").format(n=m)


class SecurityController:
    """Made once with the window. `vault_locked` — the Passwords tab is behind its guard;
    `secrets_open()` is what every place that shows a secret asks first."""

    def __init__(self, app):
        self.app = app
        self._idle = IdleClock(time.monotonic())
        self.attempts = Attempts()  # wrong codes in a row; only the master password at unlock clears it
        self.vault_locked = False
        self._guard_frame: tk.Frame | None = None
        for seq in ("<KeyPress>", "<ButtonPress>", "<MouseWheel>", "<Motion>"):
            app.bind_all(seq, self._touch_idle, add="+")
        app.bind_root("<Unmap>", self._on_unmap)  # the window's own unmap, not every widget's
        self.ticker = Ticker(app, IDLE_CHECK_MS, self._check_idle)
        self.ticker.start()

    @property
    def vault(self):
        return self.app.vault

    def reset(self) -> None:
        """The main screen is made anew (unlock, language switch): no guard is on it yet."""
        self.vault_locked = False
        self._guard_frame = None

    # ---- idle tracking

    def _touch_idle(self, _event=None) -> None:
        self._idle.touch(time.monotonic())

    def _check_idle(self) -> None:
        if self.vault is None:
            return
        prefs = self.vault.security
        now = time.monotonic()
        if self._idle.due(now, prefs.app_lock_min):
            self.app.lock()
            return
        if prefs.guarded and not self.vault_locked and self._idle.due(now, prefs.vault_lock_min):
            self.lock_secrets()

    def _on_unmap(self, event: tk.Event) -> None:
        if event.widget is not self.app or self.vault is None or not self.vault.security.lock_on_minimize:
            return
        self.app._after_jobs.append(self.app.after(50, lambda: self.vault is not None and self.app.state() == "iconic" and self.app.lock()))

    # ---- the Passwords tab guard

    def secrets_open(self) -> bool:
        return self.vault is not None and (not self.vault.security.guarded or not self.vault_locked)

    def lock_secrets(self) -> None:
        """Lock the Passwords tab (and the secrets on bookmark tiles) right now."""
        if self.vault is None or not self.vault.security.guarded:
            return
        self.app.save_now()
        self.vault_locked = True
        self.app.pages["vault"].guard(True)  # the list and the card go off the screen, the card forgets the entry
        self._show_guard()
        if "bookmarks" in self.app.pages:
            self.app.pages["bookmarks"].refresh(keep_scroll=True)
        self.app.set_status(_("Passwords are locked"))

    def restore_guard(self) -> None:
        """The Passwords tab was made anew while locked (an undo rebuilt it): the new one
        starts behind the guard too."""
        if self.vault_locked and "vault" in self.app.pages:
            self.app.pages["vault"].guard(True)
            self._guard_frame = None  # the old screen went with the old tab
            self._show_guard()

    def unlock_secrets(self) -> None:
        self.vault_locked = False
        self._idle.touch(time.monotonic())
        self._hide_guard()
        if "vault" in self.app.pages:
            self.app.pages["vault"].guard(False)
        if "bookmarks" in self.app.pages:
            self.app.pages["bookmarks"].refresh(keep_scroll=True)

    def _code_matches(self, code: str) -> bool:
        prefs = self.vault.security
        if prefs.vault_guard == GUARD_PIN:
            return prefs.check_pin(code)
        return self.vault.check_password(code)

    def limited(self, check, wrong: str):
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
                self.app.after_idle(self.app.lock)  # after the prompt has answered
                return _("Too many wrong tries: the app is locked")
            return wrong
        return accept

    def _code_label(self) -> str:
        return _("PIN code") if self.vault.security.vault_guard == GUARD_PIN else _("Master password")

    def _show_guard(self) -> None:
        """A screen over the Passwords tab with a code field."""
        if self._guard_frame is not None and self._guard_frame.winfo_exists():
            self._guard_frame.lift()
            return
        frame = tk.Frame(self.app.pages["vault"].frame, bg=palette.BG)
        frame.place(x=0, y=0, relwidth=1, relheight=1)
        frame.lift()
        self._guard_frame = frame
        box = ttk.Frame(frame)
        box.place(relx=0.5, rely=0.4, anchor="center")
        ttk.Label(box, text="🔒", font=(palette.FONT, palette.pt(32))).grid(row=0, column=0, pady=(0, 4))
        ttk.Label(box, text=_("Passwords are locked"), style="Title.TLabel", font=(palette.FONT, palette.pt(18), "bold")).grid(row=1, column=0)
        ttk.Label(box, text=(_("Enter the PIN code to see them") if self.vault.security.vault_guard == GUARD_PIN else _("Enter the master password to see them")), style="Subtitle.TLabel").grid(row=2, column=0, pady=(2, 14))
        code = ttk.Entry(box, show=BULLET, width=28, font=(palette.FONT, palette.pt(13)))
        code.grid(row=3, column=0, sticky="ew", ipady=2)
        err = ttk.Label(box, text="", style="Error.TLabel")
        err.grid(row=4, column=0, pady=(4, 0))

        accept = self.limited(self._code_matches, _("Wrong code"))

        def submit(_e=None):
            result = accept(code.get())
            if result is True:
                self.unlock_secrets()
            else:
                err.config(text=result)
                code.delete(0, "end")

        code.bind("<Return>", submit)
        ttk.Button(box, text=_("Unlock"), style="Primary.TButton", command=submit).grid(row=5, column=0, sticky="ew", pady=(10, 0))
        if self.app.mode == "vault":
            code.focus_set()
        frame.code_entry = code  # type: ignore[attr-defined]

    def _hide_guard(self) -> None:
        if self._guard_frame is not None:
            self._guard_frame.destroy()
            self._guard_frame = None

    def focus_guard(self) -> None:
        if self._guard_frame is not None and self._guard_frame.winfo_exists():
            self._guard_frame.lift()
            self._guard_frame.code_entry.focus_set()  # type: ignore[attr-defined]

    def require_secrets(self, then) -> None:
        """Run `then` once the secrets are open: right away, or after the code is entered."""
        if self.secrets_open():
            then()
            return
        ask_secret(self.app, _("Passwords are locked"), self._code_label(), _("Unlock"), self.limited(self._code_matches, _("Wrong code")), lambda: (self.unlock_secrets(), then()))

    # ---- settings

    def confirm_master(self, title: str, then) -> None:
        """Ask for the master password, then run `then` — for changes that weaken the locks."""
        ask_secret(self.app, title, _("Master password"), _("Continue"), self.limited(self.vault.check_password, _("Wrong master password")), then)


    def pin_dialog(self, after=None, parent: tk.Misc | None = None) -> None:
        """Set or change the PIN; changing it asks for the master password."""
        prefs = self.vault.security
        dlg = Dialog(self.app, _("PIN code"))
        box = dlg.box
        if parent is not None:
            dlg.transient(parent)
        ttk.Label(box, text=_("Master password")).grid(row=0, column=0, sticky="w", padx=(0, 12))
        master = ttk.Entry(box, show=BULLET, width=26)
        master.grid(row=0, column=1, pady=4)
        ttk.Label(box, text=_("New PIN")).grid(row=1, column=0, sticky="w", padx=(0, 12))
        p1 = ttk.Entry(box, show=BULLET, width=26)
        p1.grid(row=1, column=1, pady=4)
        ttk.Label(box, text=_("Once more")).grid(row=2, column=0, sticky="w", padx=(0, 12))
        p2 = ttk.Entry(box, show=BULLET, width=26)
        p2.grid(row=2, column=1, pady=4)
        ttk.Label(box, text=_("4–12 digits"), style="Muted.TLabel").grid(row=3, column=1, sticky="w")
        err = ttk.Label(box, text="", style="Error.TLabel")
        err.grid(row=4, column=0, columnspan=2)

        accept = self.limited(self.vault.check_password, _("Wrong master password"))

        def apply(_e=None):
            result = accept(master.get())
            if result is not True:
                err.config(text=result)
                master.delete(0, "end")
                return
            problem = pin_problem(p1.get())
            if problem:
                err.config(text=_("Digits only") if problem == "digits" else _("4–12 digits"))
                return
            if p1.get() != p2.get():
                err.config(text=_("PIN codes do not match"))
                return
            prefs.set_pin(p1.get())
            self.app.save_now()
            dlg.destroy()
            self.app.set_status(_("PIN code saved"))
            if after is not None:
                after()

        ttk.Button(box, text=_("Save"), style="Primary.TButton", command=apply).grid(row=5, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        dlg.keys(apply)
        master.focus_set()
