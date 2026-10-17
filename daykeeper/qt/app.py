"""The Qt main window: one QMainWindow whose stack holds two screens, the unlock screen and
the main screen, made once and swapped. What the vault shows (the tabs) is made anew
after every unlock and dropped at every lock, so none of it outlives a lock.

The same session, rules and timings as App of the Tk window (app.py); the window is the
`shell` every page talks to (tabpage.Shell): vault, mark_dirty, set_status, show_page,
copy_to_clipboard, security, page, tab_on, apply_tabs, update_lock_button,
update_integrations_badge, default_gitlab_url."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import weakref
from pathlib import Path
from typing import Any

import shiboken6
from PySide6.QtCore import QEvent, QFileSystemWatcher, QSize, Qt
from PySide6.QtGui import QAction, QCloseEvent, QGuiApplication, QKeySequence
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLineEdit, QMainWindow, QPlainTextEdit, QStackedWidget, QToolButton, QVBoxLayout, QWidget

from .. import palette, win32
from ..clipboard import CLEAR_SEC, SecretClipboard
from ..errlog import log_path_for, write_error
from ..i18n import MAC, _, hotkeys, language, set_language
from ..journal import configure as configure_links, links as current_links
from ..journal_layout import Layout, configure as configure_layout
from ..layout import DEFAULT_GEOMETRY, on_screen, parse_geometry
from ..session import Session
from ..tabs import tabs_hidden, visible_tabs
from ..undo import History
from ..vault import UNDO_DOMAINS, ChangedOnDisk, Entry, Vault
from . import icons, theme
from .dialogs.base import ask_yes_no, label, show_error, show_info
from .lock import SecurityController
from .timers import Debounce, Ticker
from .unlock import UnlockScreen
from .widgets.common import WheelGuard, icon_button
from .errors import guard_events

AUTOSAVE_DELAY_MS = 800
UNDO_SETTLE_MS = 700  # changes this close together are one undo step
INBOX_POLL_MS = 300_000  # a safety net under the watcher (a folder it cannot watch)
INBOX_SETTLE_MS = 700  # the inbox is read this long after its last change: a writer finishes first
ERROR_DIALOG_EVERY = 30  # s; an error in a timer repeats, its dialog must not
MIN_WIDTH, MIN_HEIGHT = 860, 540  # px; the bar of icon-only tabs fits in the width
BAR_LOGO = 30  # px
TAB_ICON = theme.px(18)  # px, the drawn icon of a tab button
TAB_TIPS = {  # what each tab is for, on hover over its button (the same keys as app.TAB_TIPS)
    "vault": "Logins, tokens and other secrets, encrypted with the master password",
    "bookmarks": "Work sites as tiles; a tile can copy the password of its entry",
    "journal": "What was done each day, and the standup report built from it",
    "notes": "Notes and checklists by day",
    "routines": "Scripts, programs and links started with one click or by a schedule",
    "integrations": "GitLab: what changed in your merge requests",
}


def tab_label(mode: str) -> str:
    return {
        "vault": _("🔐  Passwords"),
        "bookmarks": _("🔖  Bookmarks"),
        "journal": _("📝  Journal"),
        "notes": _("🗒  Notes"),
        "routines": _("⚡  Routine"),
        "integrations": _("🔌  Integrations"),
    }[mode]


def tab_name(mode: str) -> str:
    """The name of a tab without its glyph (the icon is drawn beside it)."""
    return icons.split(tab_label(mode))[1]


def tab_icon(mode: str) -> str:
    return icons.split(tab_label(mode))[0] or "plain"


def page_types() -> dict[str, Any]:
    """The page class of every tab; imported when first needed (a locked window needs none)."""
    from .pages.bookmarks import BookmarksPage
    from .pages.integrations import IntegrationsPage
    from .pages.journal import JournalPage
    from .pages.notes import NotesPage
    from .pages.routines import RoutinesPage
    from .pages.vault import VaultPage

    return {"vault": VaultPage, "journal": JournalPage, "notes": NotesPage, "bookmarks": BookmarksPage, "routines": RoutinesPage, "integrations": IntegrationsPage}


@guard_events
class MainScreen(QWidget):
    """The bar along the top (the logo, settings, lock the app, lock the passwords, undo,
    redo, the tab buttons), the page stack, the status line. Made once."""

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
        self.gear = icon_button("", "", lambda: window.settings_dialog(), bar, role="rail")
        self.lock_btn = QToolButton(bar)
        self.lock_btn.setProperty("role", "rail")
        self.lock_btn.setIcon(icons.power())
        self.lock_btn.setAutoRaise(True)
        self.lock_btn.clicked.connect(window.lock)
        self.lock_tab_btn = icon_button("", "", lambda: window.security.lock_secrets(), bar, role="rail")
        self.undo_btn = icon_button("", "", window.undo, bar, role="rail")
        self.redo_btn = icon_button("", "", window.redo, bar, role="rail")
        for w, picture in ((self.gear, icons.gear()), (self.lock_tab_btn, icons.padlock()), (self.undo_btn, icons.undo()), (self.redo_btn, icons.redo())):
            w.setIcon(picture)
            w.setIconSize(QSize(20, 20))
        self.lock_btn.setIconSize(QSize(20, 20))
        for w in (self.gear, self.lock_btn, self.lock_tab_btn, self.undo_btn, self.redo_btn):
            row.addWidget(w)
        line = QFrame(bar)
        line.setObjectName("RailDivider")
        line.setFixedSize(1, 24)
        row.addSpacing(10)
        row.addWidget(line)
        row.addSpacing(12)
        self.tabs_host = QWidget(bar)
        self.tabs_row = QHBoxLayout(self.tabs_host)
        self.tabs_row.setContentsMargins(0, 0, 0, 0)
        self.tabs_row.setSpacing(2)
        row.addWidget(self.tabs_host)
        row.addStretch(1)
        self.bar = bar
        self.tab_buttons: dict[str, QToolButton] = {}
        self.compact: bool | None = None
        self.full_width = 0

        self.pages = QStackedWidget(self)
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
        self.gear.setToolTip(_("Settings: language, start with the system, tabs, journal layout, security, links, AI assistants, data"))
        self.lock_btn.setToolTip(_("Lock the app") + f"  ({hotkeys('Ctrl+L')})")
        self.lock_tab_btn.setToolTip(_("Lock passwords") + " — " + _("Hide the passwords until the code is entered"))
        self.undo_btn.setToolTip(_("Undo the last change") + f"  ({hotkeys('Ctrl+Z')})" + "\n" + _("Passwords, journal, notes, bookmarks, routines. Inside a text field Ctrl+Z undoes the typing."))
        self.redo_btn.setToolTip(_("Redo what was undone") + f"  ({hotkeys('Ctrl+Y')})")
        pixmap = theme.logo(BAR_LOGO, self.devicePixelRatioF())
        if pixmap is not None:
            self.mark.setPixmap(pixmap)

    def build_tabs(self, modes: list[str]) -> None:
        for btn in self.tab_buttons.values():
            self.tabs_row.removeWidget(btn)
            btn.hide()  # until the deferred delete runs it would still paint where it was
            btn.deleteLater()
        self.tab_buttons = {}
        for n, mode in enumerate(modes, 1):
            btn = QToolButton(self.tabs_host)
            btn.setProperty("role", "tab")
            btn.setCheckable(True)
            btn.setAutoRaise(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            btn.clicked.connect(lambda _c=False, m=mode: self.shell.show_page(m))
            btn.setIcon(icons.rail(tab_icon(mode)))
            btn.setIconSize(QSize(TAB_ICON, TAB_ICON))
            name = tab_name(mode)
            keys = f"  ({hotkeys(f'Ctrl+{n}')})" if n <= 9 else ""
            btn.setToolTip((f"{name} — {_(TAB_TIPS[mode])}" if mode in TAB_TIPS else name) + keys)
            self.tabs_row.addWidget(btn)
            self.tab_buttons[mode] = btn
        self.compact = None
        self.set_compact(False)
        self.full_width = self.bar.sizeHint().width()
        self.fit_tabs()

    def tab_text(self, mode: str, extra: str = "") -> str:
        """The text beside the tab's icon: none in the compact bar but the unread count."""
        return extra.strip() if self.compact else tab_name(mode) + extra

    def set_tab_text(self, mode: str, extra: str = "") -> None:
        btn = self.tab_buttons.get(mode)
        if btn is None:
            return
        text = self.tab_text(mode, extra)
        btn.setText(text)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if text else Qt.ToolButtonStyle.ToolButtonIconOnly)

    def set_compact(self, compact: bool) -> None:
        if compact == self.compact:
            return
        self.compact = compact
        for mode in self.tab_buttons:
            self.set_tab_text(mode)
        self.shell.update_integrations_badge()

    def fit_tabs(self) -> None:
        if self.full_width:
            self.set_compact(self.width() < self.full_width)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fit_tabs()

    def mark_active(self, mode: str) -> None:
        for m, btn in self.tab_buttons.items():
            btn.setChecked(m == mode)

    def clear(self) -> None:
        """The lock: every tab goes with what it showed."""
        while self.pages.count():
            page = self.pages.widget(0)
            if page is None:
                break
            self.pages.removeWidget(page)
            page.hide()
            page.deleteLater()
        self.status.setText("")


@guard_events
class MainWindow(QMainWindow):
    def __init__(self, vault_path: Path | None = None, preview: bool = False):
        super().__init__()
        self.background_jobs = not preview  # a preview copy runs nothing on its own (no schedule, no GitLab)
        self.preview = preview
        self.setWindowTitle(palette.APP_TITLE + (" — preview" if preview else ""))
        self.setWindowIcon(theme.window_icon())
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)
        self.session = Session(vault_path)

        configure_links(self.session.links())
        configure_layout(Layout.from_settings(self.session.settings()))
        set_language(self.session.language())
        app = QApplication.instance()
        if isinstance(app, QApplication):
            theme.translate_qt(app, language())

        self.mode = "vault"
        self.pages: dict[str, Any] = {}  # the tabs, made on their first visit
        self._shown_keys: dict[str, tuple] = {}
        self.history: History | None = None  # undo / redo of the open vault (undo.py)
        self._save = Debounce(self, AUTOSAVE_DELAY_MS, self.save_now)
        self._capture = Debounce(self, UNDO_SETTLE_MS, self._capture_now)  # a burst of typing is one step
        self._clip = SecretClipboard(self._qt_clip_put, self._qt_clip_get, self._qt_clip_clear)
        self._clip_wipe = Debounce(self, CLEAR_SEC * 1000, self._clear_clipboard)
        self._inbox_ticker = Ticker(self, INBOX_POLL_MS, self.pull_inbox)
        self._inbox_settle = Debounce(self, INBOX_SETTLE_MS, self.pull_inbox)
        self._inbox_watch = QFileSystemWatcher(self)
        self._children: weakref.WeakSet[QWidget] = weakref.WeakSet()  # windows that close on lock
        self._conflict_told = False
        self._error_told = 0.0
        self._captioned = False
        self._settings_win: Any = None
        self._wheel_guard = WheelGuard(self)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self._wheel_guard)

        self.screens = QStackedWidget(self)
        self.screens.setObjectName("Screens")
        self.unlock_screen = UnlockScreen(self)
        self.main_screen = MainScreen(self)
        self.screens.addWidget(self.unlock_screen)
        self.screens.addWidget(self.main_screen)
        self.setCentralWidget(self.screens)

        self.security = SecurityController(self)
        self.gitlab: Any = None  # the GitLab check and the runs of routines, kept through a lock
        self.runs: Any = None
        self._install_shortcuts()
        if MAC:
            self._mac_menu()
        self._init_background()
        if self.background_jobs:  # a preview copy changes nothing outside its window
            from .dialogs.settings_claude import refresh_helper_in_background

            refresh_helper_in_background()  # a connected Claude Code hook gets this build's helper
        self._watch_inbox()
        self._restore_window()
        self.show_unlock()

    def _init_background(self) -> None:
        from .pages.integrations import GitLabWatcher
        from .pages.routines import RoutineRuns

        self.gitlab = GitLabWatcher(self)
        self.runs = RoutineRuns(self)

    # ---- the session

    @property
    def vault(self) -> Vault | None:
        return self.session.vault

    @property
    def vault_path(self) -> Path:
        return self.session.vault_path

    # ---- shortcuts (QAction: Qt matches a Russian layout's Ctrl+letter itself, plan 5.4)

    def _install_shortcuts(self) -> None:
        table: list[tuple[str, Any]] = [
            ("Ctrl+L", self.lock_key),
            ("Ctrl+S", self.save_key),
            ("Ctrl+N", self._new_for_mode),
            ("Ctrl+F", self._find_for_mode),
            ("Ctrl+E", lambda: self.vault is not None and self.export_dialog()),
            ("Ctrl+R", lambda: self.vault is not None and self.page("journal").report_dialog()),
            ("Ctrl+Z", lambda: self._undo_key(False)),
            ("Ctrl+Y", lambda: self._undo_key(True)),
            ("Ctrl+Shift+Z", lambda: self._undo_key(True)),
            ("Ctrl+B", lambda: self._copy_key("login")),
            ("Ctrl+Shift+C", lambda: self._copy_key("secret")),
            ("Ctrl+Tab", lambda: self._next_tab(+1)),
            ("Ctrl+Shift+Tab", lambda: self._next_tab(-1)),
            ("Alt+Up", lambda: self._move_key(-1)),
            ("Alt+Down", lambda: self._move_key(+1)),
        ]
        table += [(f"Ctrl+{n}", lambda n=n: self.show_tab_number(n)) for n in range(1, 10)]
        self.actions_by_keys: dict[str, QAction] = {}
        for keys, fn in table:
            action = QAction(keys, self)
            action.setShortcut(QKeySequence(keys))
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            action.triggered.connect(fn)
            self.addAction(action)
            self.actions_by_keys[keys] = action

    def _mac_menu(self) -> None:
        """macOS: the menu bar every app there has (decision 6 of the port): the app menu
        with About and Settings (Qt moves them there by their role), File, Edit, Window."""
        from PySide6.QtWidgets import QMenuBar

        bar = QMenuBar()  # without a parent: the one menu bar of the app on macOS
        app_menu = bar.addMenu("Daykeeper")
        about = app_menu.addAction(_("About"))
        about.setMenuRole(QAction.MenuRole.AboutRole)
        about.triggered.connect(lambda: self.settings_dialog("general"))
        prefs = app_menu.addAction(_("Settings"))
        prefs.setMenuRole(QAction.MenuRole.PreferencesRole)
        prefs.triggered.connect(lambda: self.settings_dialog("general"))
        file_menu = bar.addMenu(_("File"))
        file_menu.addAction(_("Lock the app")).triggered.connect(self.lock_key)
        file_menu.addAction(_("Export to a readable file…")).triggered.connect(lambda: self.vault is not None and self.export_dialog())
        quit_action = file_menu.addAction(_("Quit"))
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        quit_action.triggered.connect(self.close)
        edit_menu = bar.addMenu(_("Edit"))
        edit_menu.addAction(_("Undo the last change")).triggered.connect(self.undo)
        edit_menu.addAction(_("Redo what was undone")).triggered.connect(self.redo)
        window_menu = bar.addMenu(_("Window"))
        window_menu.addAction(_("Minimize")).triggered.connect(self.showMinimized)
        self.mac_menu_bar = bar

    def lock_key(self) -> None:
        self.lock()

    def save_key(self) -> None:
        if self.vault is not None:
            self.save_now()

    def _new_for_mode(self) -> None:
        if self.vault is not None and self.mode in self.pages:
            self.pages[self.mode].new_item()

    def _find_for_mode(self) -> None:
        if self.vault is None:
            return
        mode = self.mode if self.mode in ("notes", "bookmarks") else "vault"
        self.page(mode).focus_search()

    def _copy_key(self, kind: str) -> None:
        if self.vault is not None and self.mode == "vault":
            self.page("vault").copy_field(kind)

    def _move_key(self, delta: int) -> None:
        if self.vault is not None and self.mode == "vault":
            self.page("vault").move_entry(delta)

    def show_tab_number(self, n: int) -> None:
        modes = list(self.main_screen.tab_buttons)
        if self.vault is not None and 1 <= n <= len(modes):
            self.show_page(modes[n - 1])

    def _next_tab(self, step: int) -> None:
        modes = list(self.main_screen.tab_buttons)
        if self.vault is not None and modes:
            i = modes.index(self.mode) if self.mode in modes else 0
            self.show_page(modes[(i + step) % len(modes)])

    # ---- the two screens

    def show_unlock(self) -> None:
        self.unlock_screen.refresh()
        self.screens.setCurrentWidget(self.unlock_screen)
        self.unlock_screen.pw1.setFocus()

    def unlock(self, password: str | None = None, vault: Vault | None = None) -> None:
        """Open the vault (create it when there is none yet) and show the main screen."""
        self.session.unlock(password or "", vault)
        self._conflict_told = False
        self.security.attempts.ok()
        self.security.touch()
        self.unlock_screen.leave()
        self.show_main()
        if self.runs is not None:
            self.runs.on_unlock_later()

    def show_main(self) -> None:
        self._drop_pages()
        self.screens.setCurrentWidget(self.main_screen)
        self.security.reset()
        self.main_screen.build_tabs(visible_tabs(self.session.settings()))
        self.update_lock_button()
        if self.mode not in self.main_screen.tab_buttons:
            self.mode = next(iter(self.main_screen.tab_buttons))
        self.page("vault")
        self.show_page(self.mode)
        self.page("vault").open_first()
        self.pull_inbox()
        self.history = History(self.vault.object_states, UNDO_DOMAINS)  # type: ignore[union-attr]
        self.history.reset()
        self._update_undo_buttons()

    def _drop_pages(self) -> None:
        for page in self.pages.values():
            page.cancel_jobs()
        self.pages, self._shown_keys = {}, {}
        self.main_screen.clear()

    def lock(self) -> None:
        """Save, close every window that shows the vault, forget the vault and the undo steps,
        show the unlock screen (Ctrl+L, idle, minimize, too many wrong codes)."""
        if self.vault is None:
            return
        self._clip_wipe.flush()
        self._save_or_keep_copy()
        self.close_settings()
        self.close_child_windows()
        self._capture.cancel()
        self.history = None  # the snapshots hold the open data: none of it outlives a lock
        self.session.lock()
        self.security.reset()
        self._drop_pages()
        self.show_unlock()

    # ---- the tabs

    def page(self, mode: str) -> Any:
        """The tab, made now if it was never shown."""
        page = self.pages.get(mode)
        if page is None:
            page = self.pages[mode] = page_types()[mode](self)
            self.main_screen.pages.addWidget(page)
        return page

    def show_page(self, mode: str, select_entry: Entry | None = None) -> None:
        """Bring a tab up: rebuilt first when what it shows changed (`view_key`)."""
        if self.vault is None:
            return
        self.mode = mode
        self.main_screen.mark_active(mode)
        page = self.page(mode)
        key = page.view_key()
        if self._shown_keys.get(mode) != key:
            self._shown_keys[mode] = key
            page.refresh(keep_scroll=True)
        page.on_shown()
        self.main_screen.pages.setCurrentWidget(page)
        if mode == "vault" and not self.security.secrets_open():
            page.focus_guard()
        if select_entry is not None and mode == "vault" and self.security.secrets_open():
            page.select_entry(select_entry)

    def tab_on(self, mode: str) -> bool:
        return mode not in tabs_hidden(self.session.settings())

    def apply_tabs(self) -> None:
        """Settings changed the tabs: rebuild the bar; a hidden open tab gives way to the first one."""
        self.main_screen.build_tabs(visible_tabs(self.session.settings()))
        modes = list(self.main_screen.tab_buttons)
        self.show_page(self.mode if self.mode in modes else modes[0])

    def update_integrations_badge(self) -> None:
        btn = self.main_screen.tab_buttons.get("integrations")
        if btn is None or self.vault is None:
            return
        n = self.vault.integrations.unread()
        self.main_screen.set_tab_text("integrations", f"  ({n})" if n else "")

    def update_lock_button(self) -> None:
        """“Lock passwords” is there only when the Passwords tab has a guard."""
        self.main_screen.lock_tab_btn.setVisible(self.vault is not None and self.vault.security.guarded)

    def default_gitlab_url(self) -> str:
        return current_links().gitlab_url

    def apply_journal_layout(self, layout: Layout) -> bool:
        if not self.session.write_settings(layout.to_settings()):
            return False
        configure_layout(layout)
        self._rebuild_page("journal")
        return True

    def _rebuild_page(self, name: str) -> None:
        """A fresh tab in place of the old one (after an undo, a new layout)."""
        old = self.pages.get(name)
        if old is None:
            return
        keep = getattr(old, "current", None)
        old.cancel_jobs()
        page = type(old)(self)
        self.pages[name] = page
        stack = self.main_screen.pages
        stack.insertWidget(stack.indexOf(old), page)
        stack.removeWidget(old)
        old.hide()
        old.deleteLater()
        self._shown_keys.pop(name, None)
        if name == "vault":
            again = next((e for e in self.vault.entries if keep is not None and e.id == keep.id), None) if self.vault is not None else None
            page.select_entry(again) if again is not None else page.open_first()
            self.security.restore_guard()
        if self.mode == name:
            self.show_page(name)

    def set_ui_language(self, code: str) -> None:
        """Switch the UI language now; the choice is kept in settings.json."""
        if code == language():
            return
        self.save_now()
        self.session.write_settings({"language": code})
        set_language(code)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            theme.translate_qt(app, code)
        self.main_screen.retranslate()
        section = getattr(self._settings_win, "section", None) if self._settings_win is not None and shiboken6.isValid(self._settings_win) else None
        self.close_settings()
        if self.vault is None:
            self.unlock_screen.refresh()
        else:
            history, locked = self.history, self.security.vault_locked
            self.show_main()
            self.history = history
            self._update_undo_buttons()
            if locked:
                self.security.lock_secrets()
            if section:
                self.settings_dialog(section)

    # ---- windows that must not outlive a lock

    def register_child(self, window: QWidget) -> None:
        self._children.add(window)

    def close_child_windows(self) -> None:
        for window in list(self._children):
            if not shiboken6.isValid(window):
                continue
            on_lock = getattr(window, "on_lock", None)
            if on_lock is not None:
                on_lock()
            window.close()
        self._children = weakref.WeakSet()

    def settings_dialog(self, section: str = "general") -> None:
        """The settings window; one at a time: an open one comes to the front on `section`."""
        if self.vault is None:
            return
        from .dialogs.settings import SettingsWindow

        win = self._settings_win
        if win is not None and shiboken6.isValid(win) and win.isVisible():
            if win.section != section:
                win.show_section(section)
            win.raise_()
            win.activateWindow()
            return
        self._settings_win = SettingsWindow(self, section)
        self._settings_win.show()

    def close_settings(self) -> None:
        win, self._settings_win = self._settings_win, None
        if win is not None and shiboken6.isValid(win):
            win.close()

    # ---- saving and the inbox

    def mark_dirty(self, domain: str | None = None) -> None:
        if domain is not None:
            self.session.touch(domain)
        self._save.touch()
        if self.history is not None:
            self._capture.touch()

    def save_now(self) -> str:
        """"ok", "conflict" (another window saved the file) or "error"."""
        self._save.cancel()
        if self.vault is None:
            return "ok"
        try:
            self.session.save()
            self.set_status(_("Saved {time} · entries: {n}").format(time=time.strftime("%H:%M:%S"), n=len(self.vault.entries)))
        except ChangedOnDisk as exc:
            self.set_status(_("Not saved: the vault file was changed elsewhere"))
            if not self._conflict_told:
                self._conflict_told = True
                show_error(self, _("Could not save:\n{error}").format(error=exc))
            return "conflict"
        except (OSError, ValueError) as exc:
            show_error(self, _("Could not save:\n{error}").format(error=exc))
            return "error"
        return "ok"

    def _save_or_keep_copy(self) -> None:
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

    def _watch_inbox(self) -> None:
        """The inbox is picked up as a session writes it: the folder is watched for the file
        to appear, the file for lines added to it. The slow ticker stays for a folder the
        system does not report changes of (a network drive)."""
        folder = str(self.session.inbox_path.parent)
        if self.session.inbox_path.parent.is_dir() and folder not in self._inbox_watch.directories():
            self._inbox_watch.addPath(folder)
        self._inbox_watch.directoryChanged.connect(self._on_inbox_change)
        self._inbox_watch.fileChanged.connect(self._on_inbox_change)
        self._inbox_ticker.start()
        self._on_inbox_change()

    def _on_inbox_change(self, _path: str = "") -> None:
        inbox = self.session.inbox_path
        if not inbox.exists():
            return  # the vault saved, or the inbox was just taken: nothing to read
        if str(inbox) not in self._inbox_watch.files():
            self._inbox_watch.addPath(str(inbox))  # dropped by the watcher when the file went
        if self.vault is not None:
            self._inbox_settle.touch()

    def pull_inbox(self, loud: bool = False) -> None:
        """Pick up what the sessions wrote into the inbox: on a change of the file (the
        watcher), at unlock, by hand, and every five minutes as a safety net."""
        if self.vault is None:
            return
        if self._capture.pending:  # the user's own change waiting to be recorded goes first
            self._capture.cancel()
            self._capture_now()
        try:
            pulled = self.session.pull_inbox()
        except ChangedOnDisk as exc:
            self.set_status(_("The inbox waits: the vault file was changed elsewhere"))
            if not self._conflict_told:
                self._conflict_told = True
                show_error(self, _("Could not save:\n{error}").format(error=exc))
            return
        except OSError as exc:
            self.set_status(_("Could not save the inbox: {error}").format(error=exc))
            return
        except Exception as exc:  # noqa: BLE001 — a broken inbox must not stop the minute tick
            self.set_status(_("Could not read the inbox: {error}").format(error=exc))
            return
        if pulled is None:
            if loud:
                self.set_status(_("Inbox is empty"))
            return
        added, total = pulled
        if self.history is not None:
            self.history.rebase("journal")  # items from the inbox are not the user's to undo
        journal = self.pages.get("journal")
        if journal is not None:
            journal.refresh()
        self.set_status(_("Added from sessions: {added} (the inbox had {total})").format(added=added, total=total))

    def set_status(self, text: str) -> None:
        self.main_screen.status.setText(text)

    # ---- undo / redo

    def _capture_now(self) -> None:
        if self.history is not None and self.vault is not None:
            self.history.capture()
            self._update_undo_buttons()

    def _update_undo_buttons(self) -> None:
        h = self.history
        self.main_screen.undo_btn.setEnabled(h is not None and h.can_undo)
        self.main_screen.redo_btn.setEnabled(h is not None and h.can_redo)

    def _undo_key(self, redo: bool) -> None:
        """Ctrl+Z / Ctrl+Y: the field in focus undoes its own typing first, like any editor;
        when it has nothing to undo, the last change of the app."""
        focus = QApplication.focusWidget()
        if isinstance(focus, QLineEdit) and (focus.isRedoAvailable() if redo else focus.isUndoAvailable()):
            focus.redo() if redo else focus.undo()
            return
        if isinstance(focus, QPlainTextEdit) and not focus.isReadOnly():
            doc = focus.document()
            if doc.isRedoAvailable() if redo else doc.isUndoAvailable():
                focus.redo() if redo else focus.undo()
                return
        self._undo_or_redo(redo)

    def undo(self) -> None:
        self._undo_or_redo(redo=False)

    def redo(self) -> None:
        self._undo_or_redo(redo=True)

    def _undo_or_redo(self, redo: bool) -> None:
        if self.history is None or self.vault is None:
            return
        self._capture.cancel()
        focus = QApplication.focusWidget()
        if focus is not None:
            focus.clearFocus()  # a field being typed into would write its old text back over the undo
        touched = (self.history.redo if redo else self.history.undo)(self.vault.set_object_state)
        self._update_undo_buttons()
        if not touched:
            self.set_status(_("Nothing to redo") if redo else _("Nothing to undo"))
            return
        for domain in touched:
            self.session.touch(domain)
        names_of = set(touched) | ({"bookmarks"} if "vault" in touched else set())  # tiles show entries
        for name in names_of:
            self._rebuild_page(name)
        self._save.touch()
        names = ", ".join(tab_name(d) for d in UNDO_DOMAINS if d in touched)
        self.set_status((_("Redone: {what}") if redo else _("Undone: {what}")).format(what=names))

    # ---- clipboard

    def copy_to_clipboard(self, value: str, secret: bool = True) -> None:
        """A secret stays out of the Windows clipboard history and cloud sync and is wiped
        after CLEAR_SEC; other text is copied as is."""
        if not value:
            return
        if not secret:
            self._qt_clip_put(value)
            self.set_status(_("Copied"))
            return
        if not self.security.secrets_open():  # the last line of the guard
            self.set_status(_("Passwords are locked"))
            return
        self._clip.put(value)
        self._clip_wipe.touch(self._clip.digest(value))  # the timer holds a hash, not the password
        self.set_status(_("Copied. In {n} s the password is wiped from the clipboard").format(n=CLEAR_SEC))

    def _clear_clipboard(self, digest: str) -> None:
        if self._clip.wipe_if_digest(digest):
            self.set_status(_("The copied password was wiped from the clipboard"))

    def _qt_clip_put(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)

    def _qt_clip_get(self) -> str | None:
        return QGuiApplication.clipboard().text()

    def _qt_clip_clear(self) -> None:
        QGuiApplication.clipboard().clear()
        if MAC:  # the system pasteboard too
            try:
                subprocess.run(["pbcopy"], input=b"", timeout=2, check=False)  # noqa: S603, S607
            except (OSError, subprocess.TimeoutExpired):
                pass

    # ---- dialogs reached from the shortcuts and the pages

    def export_dialog(self) -> Any:
        from .dialogs.data import export_dialog

        return export_dialog(self)

    def import_env_dialog(self) -> Any:
        from .dialogs.data import import_env_dialog

        return import_env_dialog(self)

    def import_all_dialog(self) -> Any:
        from .dialogs.data import import_all_dialog

        return import_all_dialog(self)

    def open_folder(self) -> None:
        folder = self.vault_path.parent
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606
        else:
            subprocess.Popen(["open" if MAC else "xdg-open", str(folder)])  # noqa: S603, S607

    def open_window(self, vault: Path, preview: bool = True) -> None:
        """Another copy of the app on `vault` (a backup copy looked at on its own)."""
        if getattr(sys, "frozen", False):
            args = [sys.executable]
        else:
            args = [sys.executable, str(Path(__file__).resolve().parents[2] / "main_qt.py")]
        args += [str(vault)] + (["--preview"] if preview else [])
        try:
            subprocess.Popen(args, env=restart_env(os.environ))  # noqa: S603 — this very app
        except OSError as exc:
            show_error(self, str(exc))

    # ---- the window itself

    def _restore_window(self) -> None:
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
        if minimized:
            self.showMinimized()
        elif self._zoomed_at_start:
            self.showMaximized()
        else:
            self.show()

    def _save_window(self) -> None:
        update: dict[str, object] = {"zoomed": self.isMaximized()}
        if not self.isMaximized() and not self.isMinimized():
            update["geometry"] = f"{self.width()}x{self.height()}+{self.x()}+{self.y()}"
        self.session.write_settings(update)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._captioned:
            self._captioned = True
            win32.caption_colour(int(self.winId()), theme.current().RAIL, "#FFFFFF")

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            self.security.on_minimized()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._clip_wipe.flush()  # the wipe timer dies with the window: a copied password would stay
        self._save_or_keep_copy()
        self._save_window()
        self.close_settings()
        self.close_child_windows()
        for page in self.pages.values():
            page.cancel_jobs()
        if self.gitlab is not None:
            self.gitlab.close()
        for job in (self._save, self._capture, self._inbox_settle):
            job.cancel()
        self._inbox_ticker.stop()
        self._inbox_watch.removePaths(self._inbox_watch.files() + self._inbox_watch.directories())
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
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return None
    g = screen.virtualGeometry()
    return g.x(), g.y(), g.width(), g.height()


def restart_env(env) -> dict[str, str]:
    """The environment of a new copy: without a one-file build's `_PYI_*` variables (see app.restart_env)."""
    fresh = {k: v for k, v in env.items() if not k.startswith("_PYI_")}
    fresh["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return fresh


def install_excepthook(window: MainWindow) -> None:
    """Errors raised in slots, timers and event handlers go to the window's report."""
    previous = sys.excepthook
    ref = weakref.ref(window)

    def hook(exc_type, exc, tb) -> None:
        win = ref()
        if win is None or not shiboken6.isValid(win) or issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, exc, tb)
            return
        win.report_error(exc_type, exc, tb)

    sys.excepthook = hook
