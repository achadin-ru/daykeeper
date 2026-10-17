"""The Qt main window: one QMainWindow whose stack holds two screens, the unlock screen and
the main screen, made once and swapped; what the vault shows (the tabs) is made anew after
every unlock and dropped at every lock, so none of it outlives a lock.

The same session, rules and timings as App of the Tk window (app.py). Stage 1 of the port
has the frame only: unlock, an empty main screen with the bar and the status line, lock
(Ctrl+L, idle, minimize), autosave, the window's place, errors into errors.log. The tabs
come in the next stages."""

from __future__ import annotations

import sys
import time
import weakref
from pathlib import Path

import shiboken6
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QCloseEvent, QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QMainWindow, QStackedWidget, QToolButton, QVBoxLayout, QWidget

from .. import palette, win32
from ..errlog import log_path_for, write_error
from ..i18n import _, hotkeys, language, set_language
from ..journal import configure as configure_links
from ..journal_layout import Layout, configure as configure_layout
from ..layout import DEFAULT_GEOMETRY, on_screen, parse_geometry
from ..session import Session
from ..vault import ChangedOnDisk, Vault
from . import icons, shortcuts, theme
from .dialogs.base import ask_yes_no, label, show_error, show_info
from .lock import SecurityController
from .timers import Debounce
from .unlock import UnlockScreen

AUTOSAVE_DELAY_MS = 800
ERROR_DIALOG_EVERY = 30  # s; an error in a timer repeats, its dialog must not
MIN_WIDTH, MIN_HEIGHT = 860, 540  # px; the bar of icon-only tabs fits in the width
BAR_LOGO = 30  # px


class MainScreen(QWidget):
    """The bar along the top (the logo, lock the app), the area the tabs will fill, and the
    status line. Made once; `clear()` drops what an unlock filled in."""

    def __init__(self, window: MainWindow):
        super().__init__(window)
        self.shell = window  # not `window`: that is QWidget.window()
        self.setObjectName("MainScreen")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)

        bar = QFrame(self)
        bar.setObjectName("Rail")
        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 8, 10, 8)
        row.setSpacing(2)
        self.mark = label("", "", bar)
        row.addWidget(self.mark)
        row.addSpacing(8)
        self.lock_btn = QToolButton(bar)
        self.lock_btn.setProperty("role", "rail")
        self.lock_btn.setIcon(icons.power())
        self.lock_btn.setAutoRaise(True)
        self.lock_btn.clicked.connect(window.lock)
        row.addWidget(self.lock_btn)
        row.addStretch(1)
        self.bar = bar

        self.pages = QStackedWidget(self)  # the tabs, one page each (the next stages)
        self.pages.setObjectName("Pages")
        self.status = label("", "", self)
        self.status.setObjectName("Status")

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(bar)
        col.addWidget(self.pages, 1)
        col.addWidget(self.status)
        self.retranslate()

    def retranslate(self) -> None:
        self.lock_btn.setToolTip(_("Lock the app") + f"  ({hotkeys('Ctrl+L')})")
        pixmap = theme.logo(BAR_LOGO, self.devicePixelRatioF())
        if pixmap is not None:
            self.mark.setPixmap(pixmap)

    def clear(self) -> None:
        """The lock: every tab goes with what it showed."""
        while self.pages.count():
            page = self.pages.widget(0)
            if page is None:
                break
            self.pages.removeWidget(page)
            page.deleteLater()
        self.status.setText("")


class MainWindow(QMainWindow):
    def __init__(self, vault_path: Path | None = None, preview: bool = False):
        super().__init__()
        # a preview copy shows everything but runs nothing on its own (no scheduled
        # routines, no GitLab checks): the main copy already does that
        self.background_jobs = not preview
        self.preview = preview
        self.setWindowTitle(palette.APP_TITLE + (" — preview" if preview else ""))
        self.setWindowIcon(theme.window_icon())
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)
        self.session = Session(vault_path)  # the vault, its settings, the change counters, the inbox

        configure_links(self.session.links())
        configure_layout(Layout.from_settings(self.session.settings()))
        set_language(self.session.language())

        self._save = Debounce(self, AUTOSAVE_DELAY_MS, self.save_now)
        self._children: weakref.WeakSet[QWidget] = weakref.WeakSet()  # windows that close on lock
        self._conflict_told = False  # the "changed elsewhere" dialog was shown for this unlock
        self._error_told = 0.0  # when the last "something went wrong" dialog was shown
        self._captioned = False

        self.screens = QStackedWidget(self)
        self.screens.setObjectName("Screens")
        self.unlock_screen = UnlockScreen(self)
        self.main_screen = MainScreen(self)
        self.screens.addWidget(self.unlock_screen)
        self.screens.addWidget(self.main_screen)
        self.setCentralWidget(self.screens)

        self.security = SecurityController(self)  # the idle lock and the attempt limit
        self.actions_by_name = shortcuts.install(self, {"lock": self.lock_key, "save": self.save_key})
        self._restore_window()
        self.show_unlock()

    # ---- the session

    @property
    def vault(self) -> Vault | None:
        """The open vault; None while locked. Lives in the session, read by every tab."""
        return self.session.vault

    @property
    def vault_path(self) -> Path:
        return self.session.vault_path

    # ---- the two screens

    def show_unlock(self) -> None:
        self.unlock_screen.refresh()
        self.screens.setCurrentWidget(self.unlock_screen)
        self.unlock_screen.pw1.setFocus()

    def unlock(self, password: str | None = None, vault: Vault | None = None) -> None:
        """Open the vault (create it when there is none yet) and show the main screen.
        `vault` — opened already (on the unlock thread); else opened here with `password`.
        WrongPassword and BadFile come through from the vault."""
        self.session.unlock(password or "", vault)
        self._conflict_told = False
        self.security.attempts.ok()  # the master password itself was given
        self.security.reset()
        self.security.touch()
        self.unlock_screen.leave()
        self.show_main()

    def show_main(self) -> None:
        self.main_screen.clear()
        self.screens.setCurrentWidget(self.main_screen)
        self.main_screen.setFocus()

    def lock(self) -> None:
        """Save, close every window that shows the vault, forget the vault and show the
        unlock screen (Ctrl+L, idle, minimize, too many wrong codes)."""
        if self.vault is None:
            return
        self._save_or_keep_copy()
        self.close_child_windows()
        self.session.lock()
        self.security.reset()
        self.main_screen.clear()
        self.show_unlock()

    def lock_key(self) -> None:
        self.lock()

    def save_key(self) -> None:
        if self.vault is not None:
            self.save_now()

    # ---- windows that must not outlive a lock

    def register_child(self, window: QWidget) -> None:
        """A dialog, a toast, an opened file: closed on lock (`on_lock` first, if it has one)."""
        self._children.add(window)

    def close_child_windows(self) -> None:
        for window in list(self._children):
            if not shiboken6.isValid(window):
                continue
            on_lock = getattr(window, "on_lock", None)
            if on_lock is not None:  # a window with work of its own keeps it first
                on_lock()
            window.close()
        self._children = weakref.WeakSet()

    # ---- saving

    def mark_dirty(self, domain: str | None = None) -> None:
        """Data changed: save soon. `domain` — whose data (revisions.DOMAINS), so the tabs
        drawing from it are rebuilt on their next visit."""
        if domain is not None:
            self.session.touch(domain)
        self._save.touch()

    def save_now(self) -> str:
        """Write the vault at once (Ctrl+S, before a lock); the autosave calls this too.
        "ok", "conflict" (another window saved the file) or "error"."""
        self._save.cancel()
        if self.vault is None:
            return "ok"
        try:
            self.session.save()
            self.set_status(_("Saved {time} · entries: {n}").format(time=time.strftime("%H:%M:%S"), n=len(self.vault.entries)))
        except ChangedOnDisk as exc:
            self.set_status(_("Not saved: the vault file was changed elsewhere"))
            if not self._conflict_told:  # the autosave keeps trying; one dialog is enough
                self._conflict_told = True
                show_error(self, _("Could not save:\n{error}").format(error=exc))
            return "conflict"
        except (OSError, ValueError) as exc:  # ValueError: the content could not be serialised
            show_error(self, _("Could not save:\n{error}").format(error=exc))
            return "error"
        return "ok"

    def _save_or_keep_copy(self) -> None:
        """Save before the vault goes (close, lock). When another window saved the file in the
        meantime, what was changed here would be lost: offer to keep it in a copy instead."""
        if self.save_now() != "conflict":
            return
        if not ask_yes_no(self, _("Your changes could not be saved: the vault file was changed by another window or program.\n\nKeep your changes in a separate copy next to the vault?")):
            return
        try:
            path = self.session.save_conflict_copy()
        except OSError as exc:
            show_error(self, _("Could not write the copy:\n{error}").format(error=exc))
            return
        show_info(self, _("Your changes are kept in:\n{path}\n\nIt opens with the same master password: Settings → Data → Copies.").format(path=path))

    def set_status(self, text: str) -> None:
        self.main_screen.status.setText(text)

    # ---- language

    def set_ui_language(self, code: str) -> None:
        """Switch the UI language now; the choice is kept in settings.json."""
        if code == language():
            return
        self.save_now()
        self.session.write_settings({"language": code})
        set_language(code)
        self.main_screen.retranslate()
        if self.vault is None:
            self.unlock_screen.refresh()

    # ---- the window itself

    def _restore_window(self) -> None:
        """Size and position as at the last close; the position only if it is on screen.
        The same "WxH+X+Y" in settings.json as the Tk window writes (the hook reads it too)."""
        settings = self.session.settings()
        geometry = str(settings.get("geometry") or "")
        size, pos = parse_geometry(geometry, DEFAULT_GEOMETRY)
        w, h = (int(n) for n in size.split("x"))
        self.resize(max(w, MIN_WIDTH), max(h, MIN_HEIGHT))
        if pos and on_screen(geometry, virtual_screen()):
            x, _sep, y = pos.partition("+")
            try:
                self.move(int(x), int(y))
            except ValueError:
                pass
        self._zoomed_at_start = bool(settings.get("zoomed"))

    def show_as_saved(self, minimized: bool = False) -> None:
        """Show the window as it was left: maximized, or minimized for a start with the system."""
        if minimized:
            self.showMinimized()  # the unlock screen waits in the taskbar
        elif self._zoomed_at_start:
            self.showMaximized()
        else:
            self.show()

    def _save_window(self) -> None:
        update: dict[str, object] = {"zoomed": self.isMaximized()}
        if not self.isMaximized() and not self.isMinimized():
            update["geometry"] = f"{self.width()}x{self.height()}+{self.x()}+{self.y()}"
        self.session.write_settings(update)  # window settings are not worth an error on exit

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._captioned:  # once it is on screen; set before that, Windows paints it its own way
            self._captioned = True
            win32.caption_colour(int(self.winId()), theme.current().RAIL, "#FFFFFF")

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            self.security.on_minimized()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_or_keep_copy()
        self._save_window()
        self.close_child_windows()
        self._save.cancel()
        self.security.ticker.stop()
        event.accept()

    # ---- errors

    def report_error(self, exc_type, exc, tb) -> None:
        """An error in a click or a timer: into errors.log next to the vault, and a short
        dialog — a built app has no console, and without this nothing would show it."""
        path = log_path_for(self.vault_path)
        written = write_error(path, exc_type, exc, tb)
        self.set_status(_("Something went wrong: {error}").format(error=exc_type.__name__))
        now = time.monotonic()
        if now - self._error_told < ERROR_DIALOG_EVERY:
            return
        self._error_told = now
        text = _("Something went wrong: {error}").format(error=f"{exc_type.__name__}: {exc}"[:300])
        if written:
            text += "\n\n" + _("The details are in:\n{path}").format(path=path) + "\n" + _("If it keeps happening, send that file with a bug report; it holds no passwords.")
        show_error(self, text)


def virtual_screen() -> tuple[int, int, int, int] | None:
    """(left, top, width, height) of all monitors together, as layout.on_screen wants it."""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return None
    g = screen.virtualGeometry()
    return g.x(), g.y(), g.width(), g.height()


def install_excepthook(window: MainWindow) -> None:
    """Errors raised in slots, timers and event handlers go to the window's report (Qt for
    Python hands them to sys.excepthook and carries on; the default hook only prints)."""
    previous = sys.excepthook
    ref = weakref.ref(window)

    def hook(exc_type, exc, tb) -> None:
        win = ref()
        if win is None or not shiboken6.isValid(win) or issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, exc, tb)
            return
        win.report_error(exc_type, exc, tb)

    sys.excepthook = hook
