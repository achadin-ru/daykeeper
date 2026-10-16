"""One settings window: language, tabs, the journal layout, security, master password,
report links, Claude Code, data.

`SettingsWindow` is a window over the main one. Sections sit on the left, the open one on the right as white rounded
groups. Language and tabs apply at once; the forms (security, password, links) have their
own button. Security asks for the master password inside the window before it shows
anything: whoever sits at an open app must not switch the locks off."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk

from . import RELEASES_URL, __version__, autostart, palette, theme, win32
from .claude_view import ClaudeSection
from .dialogs import BULLET, ask_yes_no, show_error, show_info
from .i18n import LANGUAGES, _, language
from .session import COPY_PREFIX, sweep_preview_copies
from .journal_settings_view import JournalSection
from .popup import Dropdown
from .journal import configure as configure_links, inbox_path_for, links as current_links
from .lock_view import minutes_label, strength_text
from .security import GUARD_MASTER, GUARD_OFF, GUARD_PIN, IDLE_CHOICES
from .settings import Links
from .tabs import moved, tab_order, tabs_hidden
from .vault import default_vault_path
from .widgets import Tooltip, help_icon, divider, scroll_area

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


SETTINGS_SIZE = (1200, 760)  # px at 100 %
COPIES_SHOWN = 12  # the newest copies listed in Settings → Data


class SettingsWindow(tk.Toplevel):
    """The settings window: it stays over the main one, but the main one stays usable while
    it is open (no grab). `show(name)` opens a section."""

    closes_on_lock = True  # App.close_child_windows

    def __init__(self, app, section: str = "general"):
        super().__init__(app)
        self.app = app
        self.unlocked = False  # the master password was given for the security section
        self.section = ""
        self.title(_("Settings"))
        self.configure(bg=palette.BG)
        self.transient(app)
        self.minsize(app.painter.px(880), app.painter.px(560))  # narrower, the Journal lists overflow their card even one field under another
        # wide enough for the widest section (the Journal table) in one line a row; at most
        # nine tenths of the screen on a small one
        scale = app.painter.scale
        w = min(round(SETTINGS_SIZE[0] * scale), int(app.winfo_screenwidth() * 0.9))
        h = min(round(SETTINGS_SIZE[1] * scale), int(app.winfo_screenheight() * 0.9))
        x = app.winfo_rootx() + (app.winfo_width() - w) // 2
        y = app.winfo_rooty() + (app.winfo_height() - h) // 3
        self.geometry(f"{w}x{h}+{max(x, app.winfo_rootx())}+{max(y, app.winfo_rooty())}")  # over the main window, on its monitor (a left one has x < 0)
        app.after(10, lambda: self.winfo_exists() and theme.caption_colour(self, palette.SIDEBAR, palette.TEXT))

        nav = ttk.Frame(self, style="Sidebar.TFrame", padding=(12, 18, 12, 12), width=app.painter.px(230))
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        ttk.Label(nav, text=_("Settings"), style="SetTitle.TLabel").pack(anchor="w", padx=(10, 0), pady=(0, 14))
        divider(self, vertical=True).pack(side="left", fill="y")
        self.host = ttk.Frame(self)
        self.host.pack(side="left", fill="both", expand=True)
        self.body = ttk.Frame(self.host, padding=(30, 24, 30, 20))  # placed: a drag can hold its size
        self.body.place(x=0, y=0, relwidth=1, relheight=1)
        self.buttons: dict[str, ttk.Button] = {}
        for name, text in sections():
            b = ttk.Button(nav, text=text, style="SetNav.TButton", command=lambda n=name: self.show(n))
            b.pack(fill="x", pady=1)
            self.buttons[name] = b
        self.bind("<Escape>", lambda _e: self.destroy())
        self.show(section if section in dict(sections()) else "general")
        # a drag to a new size paints the section once it pauses, not on every step
        self._quiet = theme.QuietResize(self, lambda: self.host, 160, self._pin, self._unpin)

    def _pin(self) -> None:
        """A drag began: the section keeps its size until the drag pauses, so nothing in it
        is laid out again on every step."""
        self.body.place_configure(relwidth=0, relheight=0, width=self.body.winfo_width(), height=self.body.winfo_height())

    def _unpin(self) -> None:
        self.body.place_configure(relwidth=1, relheight=1, width=0, height=0)

    def show(self, name: str) -> None:
        for n, b in self.buttons.items():
            b.configure(style="SetNavActive.TButton" if n == name else "SetNav.TButton")
        # the old section goes and the new one is laid out with painting held: the switch
        # shows the finished section instead of building it piece by piece on screen
        hwnd = self.body.winfo_id() if self.section and win32.set_redraw(self.body.winfo_id(), False) else 0
        try:
            for child in self.body.winfo_children():
                child.destroy()
            self.unbind("<Return>")
            getattr(self, f"_section_{name}")(self.body)
            self.section = name
            if hwnd:
                self.body.update_idletasks()
        finally:
            if hwnd:
                win32.set_redraw(hwnd, True)
                win32.redraw_now(hwnd)

    # ---- building blocks

    def set_head(self, body: ttk.Frame, title: str, hint: str = "") -> None:
        ttk.Label(body, text=title, style="SetHead.TLabel").pack(anchor="w")
        if hint:
            ttk.Label(body, text=hint, style="Muted.TLabel", wraplength=560, justify="left").pack(anchor="w", pady=(4, 0))
        ttk.Frame(body, height=16).pack()

    def set_group(self, body: ttk.Frame, title: str = "", help_text: str = "") -> ttk.Frame:
        if title:
            head = ttk.Frame(body)
            head.pack(anchor="w", pady=(0, 6), padx=(4, 0))
            ttk.Label(head, text=title.upper(), style="SetGroup.TLabel").pack(side="left")
            if help_text:
                help_icon(head, help_text, bg=palette.BG).pack(side="left", padx=(6, 0))
        group = ttk.Frame(body, style="Group.TFrame", padding=(20, 16))
        group.pack(fill="x", pady=(0, 16))
        return group

    def set_saved(self, parent: ttk.Frame, text: str) -> ttk.Label:
        """A quiet “Saved” next to a button: the status bar is behind the window."""
        label = ttk.Label(parent, text="", style="CardOk.TLabel")
        label.show = lambda: (label.config(text="✓ " + text), label.after(2500, lambda: label.winfo_exists() and label.config(text="")))
        return label

    # ---- sections

    def _section_general(self, body: ttk.Frame) -> None:
        self.set_head(body, _("General"))
        body = scroll_area(body)  # taller than a small window
        group = self.set_group(body, _("Language"))
        var = tk.StringVar(value=language())

        def switch() -> None:
            code = var.get()
            if code == language():
                return
            self.destroy()
            self.app.set_ui_language(code)
            self.app.settings_dialog("general")

        for code, name in LANGUAGES:
            ttk.Radiobutton(group, text=_(name), value=code, variable=var, style="Card.TRadiobutton", command=switch).pack(anchor="w", pady=2)
        ttk.Label(group, text=_("The whole window switches at once."), style="CardMuted.TLabel").pack(anchor="w", pady=(6, 0))

        self._theme_group(body)
        self._autostart_group(body)

        sites = self.set_group(body, _("Bookmarks"))
        icons_var = tk.BooleanVar(value=bool(self.app.session.settings().get("site_icons", True)))

        def toggle_icons() -> None:
            self.app.session.write_settings({"site_icons": icons_var.get()})
            if "bookmarks" in self.app.pages:  # a tab not made yet reads the setting when it is
                self.app.pages["bookmarks"].refresh(keep_scroll=True)

        ttk.Checkbutton(sites, text=_("Load site icons and colours"), variable=icons_var, style="Card.TCheckbutton", command=toggle_icons).pack(anchor="w", pady=2)
        ttk.Label(
            sites, text=_("The app asks each bookmarked site for its icon once a month. Nothing goes to other services."),
            style="CardMuted.TLabel", wraplength=480, justify="left",
        ).pack(anchor="w", pady=(6, 0))

        about = self.set_group(body, _("About"))
        ttk.Label(about, text=f"Daykeeper {__version__}", style="Card.TLabel", font=(palette.FONT, palette.pt(12), "bold")).pack(anchor="w")
        ttk.Label(about, text=_("Passwords, bookmarks, the journal, notes, routines and GitLab news in one encrypted file."), style="CardMuted.TLabel").pack(anchor="w", pady=(2, 10))
        releases = ttk.Button(about, text=_("Releases and changes ↗"), style="Small.TButton", command=lambda: webbrowser.open(RELEASES_URL))
        releases.pack(anchor="w")
        Tooltip(releases, _("Opens the releases page: what is new, the latest build and its SHA256."))

    def _theme_group(self, body: ttk.Frame) -> None:
        """Light or dark. The colours are taken when the app starts (palette.py), so a new
        choice asks for a restart; the vault is saved and locked by it like by a close."""
        group = self.set_group(body, _("Appearance"))
        chosen = str(self.app.session.settings().get("theme") or "light")
        var = tk.StringVar(value=chosen if chosen in palette.THEMES else "light")
        note = ttk.Label(group, text="", style="CardMuted.TLabel")
        restart = ttk.Button(group, text=_("Restart now"), style="Small.TButton", command=self.app.restart)

        def show_note() -> None:
            pending = var.get() != palette.theme
            note.config(text=_("Takes effect after a restart: the app asks for the master password again.") if pending else "")
            if pending:
                restart.pack(anchor="w", pady=(8, 0))
            else:
                restart.pack_forget()

        def pick() -> None:
            self.app.session.write_settings({"theme": var.get()})
            show_note()

        for code, label in (("light", _("Light")), ("dark", _("Dark"))):
            ttk.Radiobutton(group, text=label, value=code, variable=var, style="Card.TRadiobutton", command=pick).pack(anchor="w", pady=2)
        note.pack(anchor="w", pady=(6, 0))
        show_note()

    def _autostart_group(self, body: ttk.Frame) -> None:
        group = self.set_group(body, _("Start"), _("Daykeeper can open by itself when you sign in to the computer, so the journal inbox and routines keep working without a reminder."))
        on_var = tk.BooleanVar(value=autostart.is_on())
        min_var = tk.BooleanVar(value=autostart.is_minimized())
        args = lambda: autostart.launch_args(min_var.get(), self.app.vault_path, default_vault_path())  # noqa: E731

        def apply() -> None:
            try:
                if on_var.get():
                    autostart.turn_on(args())
                else:
                    autostart.turn_off()
            except OSError as exc:
                show_error(self, _("Could not change the start with the system: {error}").format(error=exc.strerror or exc))
                on_var.set(autostart.is_on())
            minimized.state(["!disabled"] if on_var.get() else ["disabled"])

        start = ttk.Checkbutton(group, text=_("Start Daykeeper when I sign in"), variable=on_var, style="Card.TCheckbutton", command=apply)
        start.pack(anchor="w", pady=2)
        Tooltip(start, _("Per user, no administrator rights. The app still asks for the master password: nothing opens without it."))
        minimized = ttk.Checkbutton(group, text=_("Start minimized to the taskbar"), variable=min_var, style="Card.TCheckbutton", command=apply)
        minimized.pack(anchor="w", padx=(26, 0), pady=2)
        Tooltip(minimized, _("The window does not pop up over your work: it waits in the taskbar until you open it."))
        if not on_var.get():
            minimized.state(["disabled"])

    def _section_tabs(self, body: ttk.Frame) -> None:
        self.set_head(body, _("Tabs"), _("Order and visibility of the tabs in the top bar. A hidden tab also stops its background work: routines do not start on their own, GitLab is not checked."))
        group = self.set_group(body)
        rows = ttk.Frame(group, style="Card.TFrame")
        rows.pack(fill="x")

        def render() -> None:
            for child in rows.winfo_children():
                child.destroy()
            settings = self.app.session.settings()
            order, hidden = tab_order(settings), tabs_hidden(settings)
            for i, tab in enumerate(order):
                row = ttk.Frame(rows, style="Card.TFrame")
                row.pack(fill="x", pady=1)
                on = tk.BooleanVar(value=tab not in hidden)
                ttk.Checkbutton(row, text=self.app.tab_label(tab), variable=on, style="Card.TCheckbutton", command=lambda t=tab, v=on: toggle(t, v.get())).pack(side="left", pady=4)
                down = ttk.Button(row, text="▼", style="Icon.TButton", command=lambda t=tab: move(t, +1))
                down.pack(side="right")
                up = ttk.Button(row, text="▲", style="Icon.TButton", command=lambda t=tab: move(t, -1))
                up.pack(side="right", padx=(0, 2))
                if i == 0:
                    up.state(["disabled"])
                if i == len(order) - 1:
                    down.state(["disabled"])
                if i < len(order) - 1:
                    divider(rows).pack(fill="x")

        def save(update: dict) -> None:
            if not self.app.session.write_settings(update):
                show_error(self, _("Could not write settings.json"))
            self.app.apply_tabs()
            render()

        def toggle(tab: str, on: bool) -> None:
            hidden = tabs_hidden(self.app.session.settings())
            if on:
                hidden.discard(tab)
            elif len(hidden) + 1 >= len(tab_order(self.app.session.settings())):
                render()  # the last visible tab stays
                return
            else:
                hidden.add(tab)
            save({"tabs_hidden": sorted(hidden)})

        def move(tab: str, step: int) -> None:
            save({"tab_order": moved(tab_order(self.app.session.settings()), tab, step)})

        render()

    def _section_security(self, body: ttk.Frame) -> None:
        self.set_head(body, _("Security"), _("Idle lock of the whole app and an extra code for the Passwords tab."))
        if not self.unlocked:
            self._section_gate(body)
            return
        prefs = self.app.vault.security
        labels = [minutes_label(m) for m in IDLE_CHOICES]

        app_group = self.set_group(body, _("Whole app"))
        row = ttk.Frame(app_group, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text=_("Lock after idle"), style="Card.TLabel").pack(side="left")
        app_var = tk.StringVar(value=minutes_label(prefs.app_lock_min if prefs.app_lock_min in IDLE_CHOICES else 0))
        Dropdown(row, textvariable=app_var, values=labels, width=14).pack(side="right")
        minimize_var = tk.BooleanVar(value=prefs.lock_on_minimize)
        ttk.Checkbutton(app_group, text=_("Lock when the window is minimized"), variable=minimize_var, style="Card.TCheckbutton").pack(anchor="w", pady=(10, 0))

        tab_group = self.set_group(body, _("Passwords tab"))
        guard_var = tk.StringVar(value=prefs.vault_guard)
        ttk.Radiobutton(tab_group, text=_("No extra code — the master password at start is enough"), variable=guard_var, value=GUARD_OFF, style="Card.TRadiobutton").pack(anchor="w", pady=2)
        ttk.Radiobutton(tab_group, text=_("Ask for the master password"), variable=guard_var, value=GUARD_MASTER, style="Card.TRadiobutton").pack(anchor="w", pady=2)
        pin_row = ttk.Frame(tab_group, style="Card.TFrame")
        pin_row.pack(anchor="w", fill="x", pady=2)
        ttk.Radiobutton(pin_row, text=_("Ask for a PIN code"), variable=guard_var, value=GUARD_PIN, style="Card.TRadiobutton").pack(side="left")
        pin_state = ttk.Label(pin_row, text="", style="CardMuted.TLabel")
        pin_state.pack(side="left", padx=(8, 0))

        def show_pin_state() -> None:
            if pin_state.winfo_exists():
                pin_state.config(text=_("(set)") if prefs.has_pin else _("(not set yet)"))

        show_pin_state()
        ttk.Button(pin_row, text=_("Set PIN…"), style="Small.TButton", command=lambda: self.app.security.pin_dialog(after=show_pin_state, parent=self)).pack(side="left", padx=(10, 0))
        relock = ttk.Frame(tab_group, style="Card.TFrame")
        relock.pack(fill="x", pady=(10, 0))
        ttk.Label(relock, text=_("Lock it again after idle"), style="Card.TLabel").pack(side="left")
        vault_var = tk.StringVar(value=minutes_label(prefs.vault_lock_min if prefs.vault_lock_min in IDLE_CHOICES else 5))
        Dropdown(relock, textvariable=vault_var, values=labels, width=14).pack(side="right")
        ttk.Label(
            tab_group,
            text=_("The guard also hides passwords on bookmark tiles.\n“never” — the tab locks only by hand (🔒 on the tab or Ctrl+L locks the whole app)."),
            style="CardMuted.TLabel",
            justify="left",
            wraplength=540,
        ).pack(anchor="w", pady=(10, 0))

        foot = ttk.Frame(body)
        foot.pack(fill="x")
        err = ttk.Label(foot, text="", style="Error.TLabel")
        saved = self.set_saved(foot, _("Saved"))

        def save(_e=None) -> None:
            guard = guard_var.get()
            if guard == GUARD_PIN and not prefs.has_pin:
                err.config(text=_("Set the PIN first"))
                return
            err.config(text="")
            prefs.app_lock_min = IDLE_CHOICES[labels.index(app_var.get())]
            prefs.vault_lock_min = IDLE_CHOICES[labels.index(vault_var.get())]
            prefs.lock_on_minimize = minimize_var.get()
            prefs.vault_guard = guard
            self.app.save_now()
            if not prefs.guarded:
                self.app.security.unlock_secrets()
            self.app.update_lock_button()
            self.app.set_status(_("Security settings saved"))
            saved.show()

        ttk.Button(foot, text=_("Save"), style="Primary.TButton", command=save).pack(side="left")
        saved.pack(side="left", padx=(12, 0))
        err.pack(side="left", padx=(12, 0))
        self.bind("<Return>", save)

    def _section_gate(self, body: ttk.Frame) -> None:
        group = self.set_group(body)
        ttk.Label(group, text=_("These settings can weaken the protection, so enter the master password first."), style="Card.TLabel", wraplength=520, justify="left").pack(anchor="w")
        row = ttk.Frame(group, style="Card.TFrame")
        row.pack(fill="x", pady=(12, 0))
        pw = ttk.Entry(row, show=BULLET, width=30)
        pw.pack(side="left")
        err = ttk.Label(group, text="", style="CardError.TLabel")
        err.pack(anchor="w", pady=(6, 0))

        accept = self.app.security.limited(self.app.vault.check_password, _("Wrong master password"))

        def submit(_e=None) -> None:
            result = accept(pw.get())
            if result is not True:
                err.config(text=result)
                pw.delete(0, "end")
                return
            self.unlocked = True
            self.show("security")

        ttk.Button(row, text=_("Continue"), style="Primary.TButton", command=submit).pack(side="left", padx=(10, 0))
        self.bind("<Return>", submit)
        pw.focus_set()

    def _section_password(self, body: ttk.Frame) -> None:
        self.set_head(body, _("Master password"), _("Backups are re-encrypted with the new password; copies under older passwords are removed."))
        group = self.set_group(body)
        group.columnconfigure(1, weight=1)
        entries = []
        for i, label in enumerate((_("Current password"), _("New password"), _("Once more"))):
            ttk.Label(group, text=label, style="Card.TLabel").grid(row=i, column=0, sticky="w", padx=(0, 16), pady=4)
            e = ttk.Entry(group, show=BULLET, width=32)
            e.grid(row=i, column=1, sticky="w", pady=4)
            entries.append(e)
        p0, p1, p2 = entries
        strength = ttk.Label(group, text="", style="CardMuted.TLabel", wraplength=300, justify="left")
        strength.grid(row=1, column=2, sticky="w", padx=(12, 0))
        p1.bind("<KeyRelease>", lambda _e: strength.config(text=strength_text(p1.get())), add="+")
        err = ttk.Label(group, text="", style="CardError.TLabel")
        err.grid(row=3, column=1, sticky="w")
        foot = ttk.Frame(body)
        foot.pack(fill="x")
        saved = self.set_saved(foot, _("Password changed"))

        accept = self.app.security.limited(self.app.vault.check_password, _("Wrong current password"))

        def apply(_e=None) -> None:
            result = accept(p0.get())
            if result is not True:
                err.config(text=result)
                p0.delete(0, "end")
                return
            if len(p1.get()) < 8:
                err.config(text=_("At least 8 characters"))
                return
            if p1.get() != p2.get():
                err.config(text=_("Passwords do not match"))
                return
            err.config(text="")
            self.app.config(cursor="watch")
            self.app.update_idletasks()
            try:
                rekeyed, deleted = self.app.vault.change_password(p1.get())
            finally:
                self.app.config(cursor="")
            for e in entries:
                e.delete(0, "end")
            self.app.set_status(
                _("Master password changed. Backups re-encrypted: {rekeyed}, removed (older passwords): {deleted}").format(rekeyed=rekeyed, deleted=deleted)
            )
            saved.show()

        ttk.Button(foot, text=_("Change"), style="Primary.TButton", command=apply).pack(side="left")
        saved.pack(side="left", padx=(12, 0))
        self.bind("<Return>", apply)
        p0.focus_set()

    def _section_links(self, body: ttk.Frame) -> None:
        self.set_head(body, _("Report links"), _("Empty — keys and numbers stay plain text. The safety hook reads the same settings."))
        group = self.set_group(body)
        group.columnconfigure(1, weight=1)
        cur = current_links()
        rows = [
            (_("Issue tracker"), "jira_url", cur.jira_url, _("https://company.atlassian.net — key APP-1 becomes a /browse/APP-1 link"),
             _("Your tracker's address. Issue keys like APP-1234 in the journal and the report become clickable links. It also marks what is work: journal items from repositories on other servers are dropped. Leave empty if you have no tracker.")),
            ("GitLab", "gitlab_url", cur.gitlab_url, "https://gitlab.example.com",
             _("Your GitLab address. MR numbers become links, the Integrations tab and the Claude Code hook ask this server, and it counts as a work server too.")),
            (_("Own repository"), "gitlab_project", cur.gitlab_project, _("group/repo — its MRs are written as !42, others as a full link"),
             _("The repository you mostly work in, as group/repo from its address (https://gitlab.example.com/group/repo). Its MRs are written short — !42; MRs of other repositories keep the full link.")),
        ]
        vars_: dict[str, tk.StringVar] = {}
        for i, (label, key, value, hint, help_text) in enumerate(rows):
            head = ttk.Frame(group, style="Card.TFrame")
            head.grid(row=i * 2, column=0, sticky="w", padx=(0, 16))
            ttk.Label(head, text=label, style="Card.TLabel").pack(side="left")
            help_icon(head, help_text).pack(side="left", padx=(6, 0))
            vars_[key] = tk.StringVar(value=value)
            ttk.Entry(group, textvariable=vars_[key]).grid(row=i * 2, column=1, sticky="ew", pady=(4, 0))
            ttk.Label(group, text=hint, style="CardMuted.TLabel", wraplength=420, justify="left").grid(row=i * 2 + 1, column=1, sticky="w", pady=(2, 8))
        foot = ttk.Frame(body)
        foot.pack(fill="x")
        saved = self.set_saved(foot, _("Links saved"))

        def save(_e=None) -> None:
            new = Links.from_settings({k: v.get() for k, v in vars_.items()})
            if not self.app.session.write_settings(new.to_settings()):
                show_error(self, _("Could not write settings.json"))
                return
            configure_links(new)
            self.app.set_status(_("Links saved"))
            saved.show()

        save_btn = ttk.Button(foot, text=_("Save"), style="Primary.TButton", command=save)
        save_btn.pack(side="left")
        Tooltip(save_btn, _("Links in the journal and the report change at once; the hook reads them from its next command."))
        saved.pack(side="left", padx=(12, 0))
        self.bind("<Return>", save)

    def _section_journal(self, body: ttk.Frame) -> None:
        JournalSection(self, body)

    def _section_claude(self, body: ttk.Frame) -> None:
        ClaudeSection(self, body)

    def _section_data(self, body: ttk.Frame) -> None:
        self.set_head(body, _("Data"))
        body = scroll_area(body)  # the copies make it taller than the window

        def path_group(title: str, help_text: str, path, buttons) -> None:
            group = self.set_group(body, title, help_text)
            ttk.Label(group, text=str(path), style="CardMono.TLabel").pack(anchor="w")
            row = ttk.Frame(group, style="Card.TFrame")
            row.pack(anchor="w", pady=(10, 0))
            for text, command, tip in buttons:
                b = ttk.Button(row, text=text, style="Small.TButton", command=command)
                b.pack(side="left", padx=(0, 8))
                Tooltip(b, tip)

        def copy(path) -> None:
            self.app.clipboard_clear()
            self.app.clipboard_append(str(path))
            self.app.set_status(_("Path copied"))

        def leave(then) -> None:
            """The export and import dialogs are modal on their own: this window steps
            aside and comes back on this section when the dialog closes."""
            app = self.app
            self.destroy()
            dlg = then()
            back = lambda: app.after_idle(lambda: app.settings_dialog("data"))  # noqa: E731
            if dlg is None:
                back()
            else:
                dlg.bind("<Destroy>", lambda e: e.widget is dlg and back(), add="+")

        inbox = inbox_path_for(self.app.vault_path)
        copy_tip = _("To the clipboard, e.g. to paste into a backup tool or a script.")
        path_group(
            _("Vault file"),
            _("The encrypted file with everything: passwords, bookmarks, the journal, notes, routines. Without the master password it cannot be read. Copy it somewhere safe from time to time; automatic copies are in the backup folder beside it."),
            self.app.vault_path,
            [(_("Open vault folder"), self.app.open_folder, _("Opens the folder in the file manager: the vault, settings.json and the backup folder with automatic copies.")),
             (_("Copy path"), lambda: copy(self.app.vault_path), copy_tip)],
        )
        path_group(
            _("Journal inbox"),
            _("A plain file where scripts and Claude drop journal items without opening the app. The app picks it up when the vault opens and once a minute, files the items by day and empties it."),
            inbox,
            [(_("Copy path"), lambda: copy(inbox), copy_tip)],
        )
        group = self.set_group(body, _("Import and export"), _("Moving data in and out of the vault."))
        row = ttk.Frame(group, style="Card.TFrame")
        row.pack(anchor="w")
        export = ttk.Button(row, text=_("Export to a readable file…"), style="Small.TButton", command=lambda: leave(self.app.export_dialog))
        export.pack(side="left", padx=(0, 8))
        Tooltip(export, _("Saves the entries as a readable file — passwords in clear text — or everything as .json. Keep it safe and delete it when done."))
        row = ttk.Frame(group, style="Card.TFrame")  # the imports on a line of their own: three buttons overflow a narrow window
        row.pack(anchor="w", pady=(8, 0))
        env = ttk.Button(row, text=_("Import from a .env file…"), style="Small.TButton", command=lambda: leave(self.app.import_env_dialog))
        env.pack(side="left", padx=(0, 8))
        Tooltip(env, _("Adds entries to the Passwords tab from a .env file: lines like KEY=value."))
        everything = ttk.Button(row, text=_("Import everything from an export…"), style="Small.TButton", command=lambda: leave(self.app.import_all_dialog))
        everything.pack(side="left")
        Tooltip(everything, _("Replaces every tab with what a .json export holds. The vault as it was is kept as a copy."))
        self._copies_group(body)

    def _copies_group(self, body: ttk.Frame) -> None:
        """The copies the vault can go back to: the automatic backups and the copies kept
        beside it. Each opens in a separate window (on a copy of it, so looking changes
        nothing) or becomes the vault."""
        group = self.set_group(body, _("Copies"), _("Before a save the previous vault goes to the backup folder (the latest 20 and one a day for two weeks). A conflict, an import and a restore keep a copy beside the vault; those are never deleted."))
        copies = self.app.session.copies()
        if not copies:
            ttk.Label(group, text=_("No copies yet: the first one is made at the first save."), style="CardMuted.TLabel").pack(anchor="w")
            return
        kinds = {"backup": _("automatic"), "conflict": _("conflict"), "import": _("before an import"), "restore": _("before a restore"), "other": _("a file beside the vault")}
        table = ttk.Frame(group, style="Card.TFrame")
        table.pack(fill="x")
        table.columnconfigure(1, weight=1)
        for i, (path, kind) in enumerate(copies[:COPIES_SHOWN]):
            try:
                st = path.stat()
            except OSError:
                continue
            when = time.strftime("%d.%m.%Y %H:%M", time.localtime(st.st_mtime))
            ttk.Label(table, text=when, style="Card.TLabel").grid(row=i, column=0, sticky="w", padx=(0, 16), pady=2)
            ttk.Label(table, text=f"{kinds[kind]} · " + _("{n} KB").format(n=max(1, st.st_size // 1024)), style="CardMuted.TLabel").grid(row=i, column=1, sticky="w", pady=2)
            look = ttk.Button(table, text=_("Open"), style="Link.TButton", command=lambda p=path: self._open_copy(p))
            look.grid(row=i, column=2, padx=(8, 0), pady=0)
            Tooltip(look, _("Opens the copy in a separate window, with its own password. You look at a duplicate: nothing you do there changes the copy or the vault."))
            back = ttk.Button(table, text=_("Restore…"), style="Link.TButton", command=lambda p=path: self._restore_copy(p))
            back.grid(row=i, column=3, padx=(4, 0), pady=0)
            Tooltip(back, _("Makes this copy the vault. The vault as it is now is kept beside it first."))
        if len(copies) > COPIES_SHOWN:
            ttk.Label(group, text=_("…and {n} older in the vault folder.").format(n=len(copies) - COPIES_SHOWN), style="CardMuted.TLabel").pack(anchor="w", pady=(6, 0))

    def _open_copy(self, path: Path) -> None:
        """A second window of the app on a duplicate of the copy (a temporary folder): the
        copy itself stays as it was, whatever is done there."""
        sweep_preview_copies()  # folders a crashed preview left behind
        try:
            folder = Path(tempfile.mkdtemp(prefix=COPY_PREFIX))
            target = folder / path.name
            shutil.copy2(path, target)
            args = autostart.launch_args(False, target, default_vault_path()) + ["--preview"]
            subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # noqa: S603 — this very app
        except OSError as exc:
            show_error(self, _("Could not open the copy:\n{error}").format(error=exc))

    def _restore_copy(self, path: Path) -> None:
        try:
            when = time.strftime("%d.%m.%Y %H:%M", time.localtime(path.stat().st_mtime))
        except OSError as exc:  # deleted meanwhile
            show_error(self, _("Could not restore:\n{error}").format(error=exc))
            return
        if not ask_yes_no(self, _("Make the copy of {when} the vault?\n\nThe vault as it is now is kept beside it. The app then locks: open it with the master password of that copy.").format(when=when)):
            return
        app = self.app
        app.save_now()
        try:
            keep = app.session.restore_copy(path)
        except (OSError, ValueError) as exc:
            show_error(self, _("Could not restore:\n{error}").format(error=exc))
            return
        app.lock()
        show_info(app, _("Restored. The vault as it was is kept beside it:\n{path}").format(path=keep))
