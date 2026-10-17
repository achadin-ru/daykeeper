"""The settings window of the Qt window (settings_view of the Tk one): language, theme, start
with the system, tabs, the journal layout, security, master password, report links, AI
assistants, data.

One window over the main one that leaves the main one usable (not modal). Sections on the
left, the open one on the right as rounded groups, each section built when first opened.
Language, theme and tabs apply at once — the theme without a restart now; the forms
(security, password, links) have their own button. Security asks for the master password
inside the window before it shows anything: whoever sits at an open app must not switch
the locks off."""

from __future__ import annotations

import shutil
import tempfile
import time
import webbrowser
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QRadioButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ... import RELEASES_URL, __version__, autostart
from ...i18n import LANGUAGES, _, language
from ...journal import configure as configure_links, inbox_path_for, links as current_links
from ...security import GUARD_MASTER, GUARD_OFF, GUARD_PIN, IDLE_CHOICES
from ...session import COPY_PREFIX, sweep_preview_copies
from ...settings import Links
from ...tabs import moved, tab_order, tabs_hidden
from ...vault import default_vault_path
from .. import icons, theme
from ..lock import strength_text
from ..widgets.common import divider, icon_button, scroll_area, with_icon
from ..widgets.secret_edit import SecretEdit
from .base import ask_yes_no, button, label, paint_caption, register, show_error, show_info
from ..errors import guard_events

SETTINGS_SIZE = (1200, 760)
COPIES_SHOWN = 12


def sections() -> list[tuple[str, str]]:
    return [
        ("general", _("⚙  General")),
        ("tabs", _("🗂  Tabs")),
        ("journal", _("📓  Journal")),
        ("security", _("🛡  Security")),
        ("password", _("🔑  Master password")),
        ("links", _("🔗  Report links")),
        ("claude", _("🤖  AI assistants")),
        ("data", _("🗄  Data")),
    ]


def minutes_label(m: int) -> str:
    return _("never") if m == 0 else _("{n} min").format(n=m)


def help_icon(text: str, parent: QWidget | None = None) -> QLabel:
    """ⓘ that explains the thing beside it on hover."""
    icon = QLabel("ⓘ", parent)
    icon.setProperty("role", "muted")
    icon.setToolTip(text)
    icon.setCursor(Qt.CursorShape.WhatsThisCursor)
    return icon


class Section(QWidget):
    """One section: a head, then groups; `col` is its column."""

    def __init__(self, win: SettingsWindow, scroll: bool = True):
        super().__init__()
        self.win = win
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.foot_box = QHBoxLayout()
        self.foot_box.setContentsMargins(30, 10, 30, 20)
        if scroll:
            self.area, inner, self.col = scroll_area(self, (30, 24, 30, 10), name="SettingsScroll")
            outer.addWidget(self.area, 1)
            self.inner = inner
        else:
            self.inner = QWidget(self)
            self.col = QVBoxLayout(self.inner)
            self.col.setContentsMargins(30, 24, 30, 10)
            outer.addWidget(self.inner, 1)
        outer.addLayout(self.foot_box)

    def head(self, title: str, hint: str = "") -> None:
        self.col.addWidget(label(title, "heading", self.inner))
        if hint:
            note = label(hint, "muted", self.inner)
            note.setWordWrap(True)
            self.col.addSpacing(4)
            self.col.addWidget(note)
        self.col.addSpacing(16)

    def group(self, title: str = "", help_text: str = "") -> QVBoxLayout:
        if title:
            row = QHBoxLayout()
            row.setContentsMargins(4, 0, 0, 0)
            row.addWidget(label(title.upper(), "section", self.inner))
            if help_text:
                row.addWidget(help_icon(help_text, self.inner))
            row.addStretch(1)
            self.col.addLayout(row)
            self.col.addSpacing(6)
        box = QFrame(self.inner)
        box.setObjectName("Group")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)
        self.col.addWidget(box)
        self.col.addSpacing(16)
        return lay

    def saved_label(self, text: str) -> Callable[[], None]:
        """A quiet “✓ Saved” beside a button for a moment; returns what shows it."""
        lab = label("", "ok", self)
        self.foot_box.addWidget(lab)

        def show() -> None:
            lab.setText("✓ " + text)
            QTimer.singleShot(2500, lab, lambda: lab.setText(""))

        return show

    def end(self) -> None:
        self.col.addStretch(1)
        self.foot_box.addStretch(1)


@guard_events
class SettingsWindow(QDialog):
    """The settings window. `show_section(name)` opens a section."""

    def __init__(self, shell: Any, section: str = "general"):
        super().__init__(shell)
        self.shell = shell
        self.setWindowTitle(_("Settings"))
        self.setModal(False)
        self.setObjectName("Settings")
        self.unlocked = False  # the master password was given for the security section
        self.section = ""
        self.return_action: Callable[[], object] | None = None
        register(shell, self)
        geo = shell.screen().availableGeometry() if shell.screen() is not None else None
        w, h = SETTINGS_SIZE
        if geo is not None:
            w, h = min(w, int(geo.width() * 0.9)), min(h, int(geo.height() * 0.9))
        self.resize(w, h)
        self.setMinimumSize(880, 560)
        p = shell.frameGeometry()
        self.move(p.x() + (p.width() - w) // 2, p.y() + max(0, (p.height() - h) // 3))
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        nav = QFrame(self)
        nav.setObjectName("Sidebar")
        nav.setFixedWidth(230)
        nl = QVBoxLayout(nav)
        nl.setContentsMargins(12, 18, 12, 12)
        title = label(_("Settings"), "heading", nav)
        title.setContentsMargins(10, 0, 0, 0)
        nl.addWidget(title)
        nl.addSpacing(14)
        self.nav = QListWidget(nav)
        self.nav.setObjectName("SettingsNav")
        self.nav.setIconSize(QSize(18, 18))
        t = theme.current
        for name, text in sections():
            glyph, rest = icons.split(text)
            item = QListWidgetItem(rest, self.nav)
            if glyph is not None:
                item.setIcon(icons.icon(glyph, lambda: t().MUTED, lambda: t().ACCENT_INK))
            item.setData(Qt.ItemDataRole.UserRole, name)
        self.nav.currentRowChanged.connect(self._nav_changed)
        nl.addWidget(self.nav, 1)
        row.addWidget(nav)
        self.stack = QStackedWidget(self)
        self.stack.setObjectName("SettingsBody")
        row.addWidget(self.stack, 1)
        self.pages: dict[str, QWidget] = {}
        QShortcut(QKeySequence("Escape"), self, self.close)
        self.show_section(section if section in dict(sections()) else "general")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        paint_caption(self)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.return_action is not None:
            self.return_action()
            return
        super().keyPressEvent(event)

    def _nav_changed(self, row: int) -> None:
        item = self.nav.item(row)
        if item is not None and item.data(Qt.ItemDataRole.UserRole) != self.section:
            self.show_section(item.data(Qt.ItemDataRole.UserRole))

    def show_section(self, name: str, rebuild: bool = False) -> None:
        if rebuild and name in self.pages:
            old = self.pages.pop(name)
            self.stack.removeWidget(old)
            old.hide()
            old.deleteLater()
        page = self.pages.get(name)
        self.return_action = None
        if page is None:
            page = self.pages[name] = getattr(self, f"_section_{name}")()
            self.stack.addWidget(page)
        else:
            self.return_action = getattr(page, "return_action", None)
        self.section = name
        self.stack.setCurrentWidget(page)
        names = [n for n, _t in sections()]
        self.nav.blockSignals(True)
        self.nav.setCurrentRow(names.index(name))
        self.nav.blockSignals(False)

    def _set_return(self, page: QWidget, action: Callable[[], object]) -> None:
        page.return_action = action  # type: ignore[attr-defined]
        self.return_action = action

    def closeEvent(self, event: QCloseEvent) -> None:
        event.accept()

    # ---- general

    def _section_general(self) -> QWidget:
        s = Section(self)
        s.head(_("General"))
        g = s.group(_("Language"))
        group = QButtonGroup(s)
        for code, name in LANGUAGES:
            radio = QRadioButton(_(name), s.inner)
            radio.setChecked(code == language())
            radio.toggled.connect(partial(self._language_picked, code))
            group.addButton(radio)
            g.addWidget(radio)
        g.addWidget(label(_("The whole window switches at once."), "muted", s.inner))

        g = s.group(_("Appearance"))
        chosen = str(self.shell.session.settings().get("theme") or "light")
        themes = QButtonGroup(s)
        for code, text in (("light", _("Light")), ("dark", _("Dark"))):
            radio = QRadioButton(text, s.inner)
            radio.setChecked(code == (chosen if chosen in theme.THEMES else "light"))
            radio.toggled.connect(partial(self._theme_picked, code))
            themes.addButton(radio)
            g.addWidget(radio)

        g = s.group(_("Start"), _("Daykeeper can open by itself when you sign in to the computer, so the journal inbox and routines keep working without a reminder."))
        start = QCheckBox(_("Start Daykeeper when I sign in"), s.inner)
        start.setToolTip(_("Per user, no administrator rights. The app still asks for the master password: nothing opens without it."))
        minimized = QCheckBox(_("Start minimized to the taskbar"), s.inner)
        minimized.setToolTip(_("The window does not pop up over your work: it waits in the taskbar until you open it."))
        minimized.setContentsMargins(26, 0, 0, 0)
        try:
            start.setChecked(autostart.is_on())
            minimized.setChecked(autostart.is_minimized())
        except OSError:
            pass
        minimized.setEnabled(start.isChecked())

        def apply_start(_on: bool = False) -> None:
            try:
                if start.isChecked():
                    autostart.turn_on(autostart.launch_args(minimized.isChecked(), self.shell.vault_path, default_vault_path()))
                else:
                    autostart.turn_off()
            except OSError as exc:
                show_error(self, _("Could not change the start with the system: {error}").format(error=exc.strerror or exc))
                start.blockSignals(True)
                start.setChecked(autostart.is_on())
                start.blockSignals(False)
            minimized.setEnabled(start.isChecked())

        start.toggled.connect(apply_start)
        minimized.toggled.connect(apply_start)
        g.addWidget(start)
        g.addWidget(minimized)

        g = s.group(_("Bookmarks"))
        icons = QCheckBox(_("Load site icons and colours"), s.inner)
        icons.setChecked(bool(self.shell.session.settings().get("site_icons", True)))

        def toggle_icons(on: bool) -> None:
            self.shell.session.write_settings({"site_icons": on})
            if "bookmarks" in self.shell.pages:
                self.shell.pages["bookmarks"].refresh(keep_scroll=True)

        icons.toggled.connect(toggle_icons)
        g.addWidget(icons)
        note = label(_("The app asks each bookmarked site for its icon once a month. Nothing goes to other services."), "muted", s.inner)
        note.setWordWrap(True)
        g.addWidget(note)

        g = s.group(_("About"))
        about = QLabel(f"Daykeeper {__version__}", s.inner)
        about.setFont(theme.font(12, bold=True))
        g.addWidget(about)
        g.addWidget(label(_("Passwords, bookmarks, the journal, notes, routines and GitLab news in one encrypted file."), "muted", s.inner))
        releases = button(_("Releases and changes ↗"), "small", s.inner)
        releases.setToolTip(_("Opens the releases page: what is new, the latest build and its SHA256."))
        releases.clicked.connect(lambda: webbrowser.open(RELEASES_URL))
        g.addWidget(releases, 0, Qt.AlignmentFlag.AlignLeft)
        s.end()
        self.theme_buttons = themes
        return s

    def _language_picked(self, code: str, on: bool) -> None:
        if on:
            self._switch_language(code)

    def _theme_picked(self, code: str, on: bool) -> None:
        if on:
            self._switch_theme(code)

    def _switch_language(self, code: str) -> None:
        if code != language():
            QTimer.singleShot(0, self.shell, lambda: self.shell.set_ui_language(code))  # it closes this window and opens it again

    def _switch_theme(self, code: str) -> None:
        """At once: the style sheet and the palette are put on the application again and
        every painted widget reads the theme on its next paint."""
        from PySide6.QtWidgets import QApplication

        from ... import win32

        self.shell.session.write_settings({"theme": code})
        app = QApplication.instance()
        if isinstance(app, QApplication):
            theme.apply(app, theme.theme_named(code))
        win32.caption_colour(int(self.shell.winId()), theme.current().RAIL, "#FFFFFF")
        paint_caption(self)
        for w in QApplication.topLevelWidgets():
            w.update()

    # ---- tabs

    def _section_tabs(self) -> QWidget:
        s = Section(self)
        s.head(_("Tabs"), _("Order and visibility of the tabs in the top bar. A hidden tab also stops its background work: routines do not start on their own, GitLab is not checked."))
        g = s.group()
        host = QWidget(s.inner)
        rows = QVBoxLayout(host)
        rows.setContentsMargins(0, 0, 0, 0)
        g.addWidget(host)
        from ..app import tab_label

        def render() -> None:
            while rows.count():
                item = rows.takeAt(0)
                w = item.widget() if item is not None else None
                if w is not None:
                    w.hide()
                    w.deleteLater()
            settings = self.shell.session.settings()
            order, hidden = tab_order(settings), tabs_hidden(settings)
            for i, tab in enumerate(order):
                row = QWidget(host)
                rl = QHBoxLayout(row)
                rl.setContentsMargins(0, 4, 0, 4)
                on = QCheckBox(row)
                with_icon(on, tab_label(tab))
                on.setChecked(tab not in hidden)
                on.toggled.connect(lambda v, t=tab: toggle(t, v))
                rl.addWidget(on)
                rl.addStretch(1)
                up = icon_button("▲", "", partial(move, tab, -1), row)
                down = icon_button("▼", "", partial(move, tab, +1), row)
                up.setEnabled(i > 0)
                down.setEnabled(i < len(order) - 1)
                rl.addWidget(up)
                rl.addWidget(down)
                rows.addWidget(row)
                if i < len(order) - 1:
                    rows.addWidget(divider(host))

        def save(update: dict) -> None:
            if not self.shell.session.write_settings(update):
                show_error(self, _("Could not write settings.json"))
            self.shell.apply_tabs()
            QTimer.singleShot(0, s, render)

        def toggle(tab: str, on: bool) -> None:
            hidden = tabs_hidden(self.shell.session.settings())
            if on:
                hidden.discard(tab)
            elif len(hidden) + 1 >= len(tab_order(self.shell.session.settings())):
                QTimer.singleShot(0, s, render)  # the last visible tab stays
                return
            else:
                hidden.add(tab)
            save({"tabs_hidden": sorted(hidden)})

        def move(tab: str, step: int) -> None:
            save({"tab_order": moved(tab_order(self.shell.session.settings()), tab, step)})

        render()
        s.end()
        s.rerender = render  # type: ignore[attr-defined]
        return s

    # ---- security

    def _section_security(self) -> QWidget:
        s = Section(self)
        s.head(_("Security"), _("Idle lock of the whole app and an extra code for the Passwords tab."))
        if not self.unlocked:
            self._gate(s)
            s.end()
            return s
        prefs = self.shell.vault.security
        g = s.group(_("Whole app"))
        row = QHBoxLayout()
        row.addWidget(QLabel(_("Lock after idle"), s.inner))
        row.addStretch(1)
        app_box = QComboBox(s.inner)
        for m in IDLE_CHOICES:
            app_box.addItem(minutes_label(m), m)
        app_box.setCurrentIndex(IDLE_CHOICES.index(prefs.app_lock_min) if prefs.app_lock_min in IDLE_CHOICES else IDLE_CHOICES.index(0))
        row.addWidget(app_box)
        g.addLayout(row)
        minimize = QCheckBox(_("Lock when the window is minimized"), s.inner)
        minimize.setChecked(prefs.lock_on_minimize)
        g.addWidget(minimize)

        g = s.group(_("Passwords tab"))
        guards = QButtonGroup(s)
        radios = {}
        for value, text in ((GUARD_OFF, _("No extra code — the master password at start is enough")), (GUARD_MASTER, _("Ask for the master password")), (GUARD_PIN, _("Ask for a PIN code"))):
            radio = QRadioButton(text, s.inner)
            radio.setChecked(prefs.vault_guard == value)
            guards.addButton(radio)
            radios[value] = radio
            if value != GUARD_PIN:
                g.addWidget(radio)
        pin_row = QHBoxLayout()
        pin_row.addWidget(radios[GUARD_PIN])
        pin_state = label("", "muted", s.inner)
        pin_row.addWidget(pin_state)

        def show_pin_state() -> None:
            pin_state.setText(_("(set)") if prefs.has_pin else _("(not set yet)"))

        show_pin_state()
        set_pin = button(_("Set PIN…"), "small", s.inner)
        set_pin.clicked.connect(lambda: self.shell.security.pin_dialog(after=show_pin_state, parent=self))
        pin_row.addWidget(set_pin)
        pin_row.addStretch(1)
        g.addLayout(pin_row)
        relock = QHBoxLayout()
        relock.addWidget(QLabel(_("Lock it again after idle"), s.inner))
        relock.addStretch(1)
        vault_box = QComboBox(s.inner)
        for m in IDLE_CHOICES:
            vault_box.addItem(minutes_label(m), m)
        vault_box.setCurrentIndex(IDLE_CHOICES.index(prefs.vault_lock_min) if prefs.vault_lock_min in IDLE_CHOICES else IDLE_CHOICES.index(5))
        relock.addWidget(vault_box)
        g.addLayout(relock)
        note = label(_("The guard also hides passwords on bookmark tiles.\n“never” — the tab locks only by hand (🔒 on the tab or Ctrl+L locks the whole app)."), "muted", s.inner)
        note.setWordWrap(True)
        g.addWidget(note)

        save_btn = button(_("Save"), "primary", s)
        s.foot_box.addWidget(save_btn)
        shown = s.saved_label(_("Saved"))
        err = label("", "error", s)
        s.foot_box.addWidget(err)

        def save() -> None:
            guard = next(v for v, r in radios.items() if r.isChecked())
            if guard == GUARD_PIN and not prefs.has_pin:
                err.setText(_("Set the PIN first"))
                return
            err.setText("")
            prefs.app_lock_min = int(app_box.currentData())
            prefs.vault_lock_min = int(vault_box.currentData())
            prefs.lock_on_minimize = minimize.isChecked()
            prefs.vault_guard = guard
            self.shell.save_now()
            if not prefs.guarded:
                self.shell.security.unlock_secrets()
            self.shell.update_lock_button()
            self.shell.set_status(_("Security settings saved"))
            shown()

        save_btn.clicked.connect(save)
        self._set_return(s, save)
        s.end()
        s.parts = {"app": app_box, "vault": vault_box, "minimize": minimize, "radios": radios, "save": save, "error": err}  # type: ignore[attr-defined]
        return s

    def _gate(self, s: Section) -> None:
        g = s.group()
        text = label(_("These settings can weaken the protection, so enter the master password first."), "", s.inner)
        text.setWordWrap(True)
        g.addWidget(text)
        row = QHBoxLayout()
        pw = SecretEdit(s.inner)
        pw.setFixedWidth(pw.fontMetrics().averageCharWidth() * 30 + 24)
        row.addWidget(pw)
        go = button(_("Continue"), "primary", s.inner)
        row.addWidget(go)
        row.addStretch(1)
        g.addLayout(row)
        err = label("", "error", s.inner)
        g.addWidget(err)
        accept = self.shell.security.limited(self.shell.vault.check_password, _("Wrong master password"))

        def submit() -> None:
            result = accept(pw.text())
            pw.wipe()
            if result is not True:
                err.setText(str(result))
                return
            self.unlocked = True
            self.show_section("security", rebuild=True)

        go.clicked.connect(submit)
        pw.returnPressed.connect(submit)
        self._set_return(s, submit)
        QTimer.singleShot(0, pw, pw.setFocus)
        s.gate = {"password": pw, "error": err, "submit": submit}  # type: ignore[attr-defined]

    # ---- master password

    def _section_password(self) -> QWidget:
        s = Section(self)
        s.head(_("Master password"), _("Backups are re-encrypted with the new password; copies under older passwords are removed."))
        g = s.group()
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        fields = []
        for i, text in enumerate((_("Current password"), _("New password"), _("Once more"))):
            grid.addWidget(QLabel(text, s.inner), i, 0)
            e = SecretEdit(s.inner)
            e.setFixedWidth(e.fontMetrics().averageCharWidth() * 32 + 24)
            grid.addWidget(e, i, 1)
            fields.append(e)
        p0, p1, p2 = fields
        strength = label("", "muted", s.inner)
        strength.setWordWrap(True)
        grid.addWidget(strength, 1, 2)
        p1.textChanged.connect(lambda t: strength.setText(strength_text(t)))
        err = label("", "error", s.inner)
        grid.addWidget(err, 3, 1, 1, 2)
        grid.setColumnStretch(2, 1)
        g.addLayout(grid)
        change = button(_("Change"), "primary", s)
        s.foot_box.addWidget(change)
        shown = s.saved_label(_("Password changed"))
        accept = self.shell.security.limited(self.shell.vault.check_password, _("Wrong current password"))

        def apply() -> None:
            from PySide6.QtWidgets import QApplication

            result = accept(p0.text())
            if result is not True:
                err.setText(str(result))
                p0.wipe()
                return
            if len(p1.text()) < 8:
                err.setText(_("At least 8 characters"))
                return
            if p1.text() != p2.text():
                err.setText(_("Passwords do not match"))
                return
            err.setText("")
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                rekeyed, deleted = self.shell.vault.change_password(p1.text())
            finally:
                QApplication.restoreOverrideCursor()
            for f in fields:
                f.wipe()
            self.shell.set_status(_("Master password changed. Backups re-encrypted: {rekeyed}, removed (older passwords): {deleted}").format(rekeyed=rekeyed, deleted=deleted))
            shown()

        change.clicked.connect(apply)
        self._set_return(s, apply)
        s.end()
        s.parts = {"fields": fields, "error": err, "apply": apply}  # type: ignore[attr-defined]
        return s

    # ---- report links

    def _section_links(self) -> QWidget:
        s = Section(self)
        s.head(_("Report links"), _("Empty — keys and numbers stay plain text. The safety hook reads the same settings."))
        g = s.group()
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        cur = current_links()
        rows = [
            (_("Issue tracker"), "jira_url", cur.jira_url, _("https://company.atlassian.net — key APP-1 becomes a /browse/APP-1 link"),
             _("Your tracker's address. Issue keys like APP-1234 in the journal and the report become clickable links. It also marks what is work: journal items from repositories on other servers are dropped. Leave empty if you have no tracker.")),
            ("GitLab", "gitlab_url", cur.gitlab_url, "https://gitlab.example.com",
             _("Your GitLab address. MR numbers become links, the Integrations tab and the Claude Code hook ask this server, and it counts as a work server too.")),
            (_("Own repository"), "gitlab_project", cur.gitlab_project, _("group/repo — its MRs are written as !42, others as a full link"),
             _("The repository you mostly work in, as group/repo from its address (https://gitlab.example.com/group/repo). Its MRs are written short — !42; MRs of other repositories keep the full link.")),
        ]
        edits: dict[str, QLineEdit] = {}
        for i, (text, key, value, hint, help_text) in enumerate(rows):
            head = QHBoxLayout()
            head.addWidget(QLabel(text, s.inner))
            head.addWidget(help_icon(help_text, s.inner))
            head.addStretch(1)
            grid.addLayout(head, i * 2, 0)
            edit = QLineEdit(value, s.inner)
            grid.addWidget(edit, i * 2, 1)
            note = label(hint, "muted", s.inner)
            note.setWordWrap(True)
            grid.addWidget(note, i * 2 + 1, 1)
            edits[key] = edit
        grid.setColumnStretch(1, 1)
        g.addLayout(grid)
        save_btn = button(_("Save"), "primary", s)
        save_btn.setToolTip(_("Links in the journal and the report change at once; the hook reads them from its next command."))
        s.foot_box.addWidget(save_btn)
        shown = s.saved_label(_("Links saved"))

        def save() -> None:
            new = Links.from_settings({k: v.text() for k, v in edits.items()})
            if not self.shell.session.write_settings(new.to_settings()):
                show_error(self, _("Could not write settings.json"))
                return
            configure_links(new)
            self.shell.set_status(_("Links saved"))
            shown()

        save_btn.clicked.connect(save)
        self._set_return(s, save)
        s.end()
        s.parts = {"edits": edits, "save": save}  # type: ignore[attr-defined]
        return s

    # ---- journal and AI assistants: their own modules

    def _section_journal(self) -> QWidget:
        from .settings_journal import JournalSection

        return JournalSection(self)

    def _section_claude(self) -> QWidget:
        from .settings_claude import ClaudeSection

        return ClaudeSection(self)

    # ---- data

    def _section_data(self) -> QWidget:
        s = Section(self)
        s.head(_("Data"))
        shell = self.shell

        def path_group(title: str, help_text: str, path, buttons) -> None:
            g = s.group(title, help_text)
            where = QLabel(str(path), s.inner)
            where.setFont(theme.font(10, mono=True))
            where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            g.addWidget(where)
            row = QHBoxLayout()
            for text, command, tip in buttons:
                b = button(text, "small", s.inner)
                b.setToolTip(tip)
                b.clicked.connect(command)
                row.addWidget(b)
            row.addStretch(1)
            g.addLayout(row)

        def copy(path) -> None:
            shell.copy_to_clipboard(str(path), secret=False)
            shell.set_status(_("Path copied"))

        def leave(then: Callable[[], object]) -> None:
            """The export and import dialogs are modal: this window steps aside and comes
            back on this section when the dialog closes."""
            self.close()
            dlg: Any = then()
            if dlg is None:
                QTimer.singleShot(0, shell, lambda: shell.settings_dialog("data"))
            else:
                dlg.finished.connect(lambda _r: QTimer.singleShot(0, shell, lambda: shell.vault is not None and shell.settings_dialog("data")))

        inbox = inbox_path_for(shell.vault_path)
        copy_tip = _("To the clipboard, e.g. to paste into a backup tool or a script.")
        path_group(_("Vault file"), _("The encrypted file with everything: passwords, bookmarks, the journal, notes, routines. Without the master password it cannot be read. Copy it somewhere safe from time to time; automatic copies are in the backup folder beside it."), shell.vault_path,
                   [(_("Open vault folder"), shell.open_folder, _("Opens the folder in the file manager: the vault, settings.json and the backup folder with automatic copies.")),
                    (_("Copy path"), lambda: copy(shell.vault_path), copy_tip)])
        path_group(_("Journal inbox"), _("A plain file where scripts and Claude drop journal items without opening the app. The app picks it up when the vault opens and once a minute, files the items by day and empties it."), inbox,
                   [(_("Copy path"), lambda: copy(inbox), copy_tip)])
        g = s.group(_("Import and export"), _("Moving data in and out of the vault."))
        row = QHBoxLayout()
        export = button(_("Export to a readable file…"), "small", s.inner)
        export.setToolTip(_("Saves the entries as a readable file — passwords in clear text — or everything as .json. Keep it safe and delete it when done."))
        export.clicked.connect(lambda: leave(shell.export_dialog))
        row.addWidget(export)
        row.addStretch(1)
        g.addLayout(row)
        row = QHBoxLayout()
        env = button(_("Import from a .env file…"), "small", s.inner)
        env.setToolTip(_("Adds entries to the Passwords tab from a .env file: lines like KEY=value."))
        env.clicked.connect(lambda: leave(shell.import_env_dialog))
        everything = button(_("Import everything from an export…"), "small", s.inner)
        everything.setToolTip(_("Replaces every tab with what a .json export holds. The vault as it was is kept as a copy."))
        everything.clicked.connect(lambda: leave(shell.import_all_dialog))
        row.addWidget(env)
        row.addWidget(everything)
        row.addStretch(1)
        g.addLayout(row)
        self._copies_group(s)
        s.end()
        return s

    def _copies_group(self, s: Section) -> None:
        g = s.group(_("Copies"), _("Before a save the previous vault goes to the backup folder (the latest 20 and one a day for two weeks). A conflict, an import and a restore keep a copy beside the vault; those are never deleted."))
        copies = self.shell.session.copies()
        if not copies:
            g.addWidget(label(_("No copies yet: the first one is made at the first save."), "muted", s.inner))
            return
        kinds = {"backup": _("automatic"), "conflict": _("conflict"), "import": _("before an import"), "restore": _("before a restore"), "other": _("a file beside the vault")}
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        for i, (path, kind) in enumerate(copies[:COPIES_SHOWN]):
            try:
                st = path.stat()
            except OSError:
                continue
            grid.addWidget(QLabel(time.strftime("%d.%m.%Y %H:%M", time.localtime(st.st_mtime)), s.inner), i, 0)
            grid.addWidget(label(f"{kinds[kind]} · " + _("{n} KB").format(n=max(1, st.st_size // 1024)), "muted", s.inner), i, 1)
            look = button(_("Open"), "link", s.inner)
            look.setToolTip(_("Opens the copy in a separate window, with its own password. You look at a duplicate: nothing you do there changes the copy or the vault."))
            look.clicked.connect(lambda _c=False, p=path: self._open_copy(p))
            back = button(_("Restore…"), "link", s.inner)
            back.setToolTip(_("Makes this copy the vault. The vault as it is now is kept beside it first."))
            back.clicked.connect(lambda _c=False, p=path: self._restore_copy(p))
            grid.addWidget(look, i, 2)
            grid.addWidget(back, i, 3)
        grid.setColumnStretch(1, 1)
        g.addLayout(grid)
        if len(copies) > COPIES_SHOWN:
            g.addWidget(label(_("…and {n} older in the vault folder.").format(n=len(copies) - COPIES_SHOWN), "muted", s.inner))

    def _open_copy(self, path: Path) -> None:
        """A second window of the app on a duplicate of the copy (a temporary folder)."""
        sweep_preview_copies()
        try:
            folder = Path(tempfile.mkdtemp(prefix=COPY_PREFIX))
            target = folder / path.name
            shutil.copy2(path, target)
        except OSError as exc:
            show_error(self, _("Could not open the copy:\n{error}").format(error=exc))
            return
        self.shell.open_window(target, preview=True)

    def _restore_copy(self, path: Path) -> None:
        try:
            when = time.strftime("%d.%m.%Y %H:%M", time.localtime(path.stat().st_mtime))
        except OSError as exc:
            show_error(self, _("Could not restore:\n{error}").format(error=exc))
            return
        if not ask_yes_no(self, _("Make the copy of {when} the vault?\n\nThe vault as it is now is kept beside it. The app then locks: open it with the master password of that copy.").format(when=when)):
            return
        shell = self.shell
        shell.save_now()
        try:
            keep = shell.session.restore_copy(path)
        except (OSError, ValueError) as exc:
            show_error(self, _("Could not restore:\n{error}").format(error=exc))
            return
        shell.lock()
        show_info(shell, _("Restored. The vault as it was is kept beside it:\n{path}").format(path=keep))

