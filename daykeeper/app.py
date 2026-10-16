"""Daykeeper tkinter UI: the entry list on the left, a card with fields on the right."""

from __future__ import annotations

import math
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from . import appicon, autostart, fieldundo, palette, popup, theme, win32
from .bookmarks_view import BookmarksPage
from .claude_view import refresh_helper_in_background
from .clipboard import CLEAR_SEC, SecretClipboard
from .data_dialogs import export_dialog, import_all_dialog, import_env_dialog
from .dialogs import BULLET, ask_yes_no, show_error, show_info
from .errlog import log_path_for, write_error
from .i18n import LANGUAGES, MAC, MOD_KEY, _, hotkeys, language, set_language
from .journal import configure as configure_links, links as current_links
from .journal_layout import Layout, configure as configure_layout
from .journal_view import JournalPage
from .integrations_view import GitLabWatcher, IntegrationsPage
from .layout import DEFAULT_GEOMETRY, on_screen, parse_geometry
from .lock_view import SecurityController, strength_text
from .notes_view import NotesPage
from .palette import ACCENT, APP_TITLE, CARD, FONT, RAIL, RAIL_HOVER, pt
from .routines_view import RoutineRuns, RoutinesPage
from .session import Session, remove_preview_copy
from . import style
from .settings_view import SettingsWindow
from .tabpage import RESIZE_SETTLE_MS, ChunkBuilder, TabPage
from .tabs import tabs_hidden, visible_tabs
from .timers import Debounce, IdleOnce, Ticker
from .vault_view import VaultPage
from .widgets import Tooltip
from .undo import History
from .vault import UNDO_DOMAINS, BadFile, ChangedOnDisk, Entry, Vault, WrongPassword, default_vault_path

INBOX_POLL_MS = 60_000  # how often the journal inbox is picked up
AUTOSAVE_DELAY_MS = 800
DPI_CHECK_MS = 300  # a move across monitors is checked for a new scale once it pauses
ERROR_DIALOG_EVERY = 30  # s; an error in a timer repeats, its dialog must not
ROOT_TAG = "DaykeeperRoot"  # a bindtag only the window itself carries (bind_root)

# Shortcuts by key CODE (Windows virtual-key), not by letter: in a non-Latin layout
# Ctrl+V arrives as Ctrl+<local letter>, and ordinary Tk keysym bindings stay silent.
EDIT_SHORTCUTS = {86: "<<Paste>>", 67: "<<Copy>>", 88: "<<Cut>>", 65: "<<SelectAll>>", 90: "<<Undo>>", 89: "<<Redo>>"}
APP_SHORTCUTS = {78: "new", 83: "save", 76: "lock", 70: "find", 69: "export", 82: "report"}  # N S L F E R
UNDO_SETTLE_MS = 700  # changes this close together are one undo step
MIN_WIDTH = 860  # px; the bar of icon-only tabs fits in it
PAGE_TYPES = {"vault": VaultPage, "journal": JournalPage, "notes": NotesPage, "bookmarks": BookmarksPage, "routines": RoutinesPage, "integrations": IntegrationsPage}
TAB_TIPS = {  # what each tab is for, on hover over its button
    "vault": "Logins, tokens and other secrets, encrypted with the master password",
    "bookmarks": "Work sites as tiles; a tile can copy the password of its entry",
    "journal": "What was done each day, and the standup report built from it",
    "notes": "Notes and checklists by day",
    "routines": "Scripts, programs and links started with one click or by a schedule",
    "integrations": "GitLab: what changed in your merge requests",
}


class App(tk.Tk):
    def __init__(self, vault_path: Path | None = None, preview: bool = False):
        win32.enable_dpi_awareness()
        super().__init__()
        self.bindtags((str(self), ROOT_TAG, "Tk", "all"))
        # a preview copy shows everything but runs nothing on its own: no scheduled
        # routines, no GitLab checks — the main copy already does that
        self.background_jobs = not preview
        self.title(APP_TITLE + (" — preview" if preview else ""))
        appicon.apply(self)
        self.minsize(MIN_WIDTH, 540)
        self.session = Session(vault_path)  # the vault, its settings, the change counters, the inbox
        self._dpi = self._start_dpi()
        self.tk.call("tk", "scaling", self._dpi / 72)  # points and images at the monitor's scale
        self._old_painters: list = []  # images of an earlier scale stay alive while anything shows them
        self._tabs_compact: bool | None = None
        self._tabs_full_width = 0
        self._tabs_fit = Debounce(self, 60, self._fit_tabs)
        self.bind_root("<Configure>", lambda _e: (self._tabs_fit.touch(), self._dpi_check.touch()))
        self._dpi_check = Debounce(self, DPI_CHECK_MS, self._check_dpi)
        self._setup_style()

        configure_links(self.session.links())
        configure_layout(Layout.from_settings(self.session.settings()))
        set_language(self.session.language())
        self._restore_window()
        self._save = Debounce(self, AUTOSAVE_DELAY_MS, self.save_now)
        self.history: History | None = None  # undo / redo of the open vault (see undo.py)
        self._capture = Debounce(self, UNDO_SETTLE_MS, self._capture_now)  # a burst of typing is one step
        self._clip = SecretClipboard(self._tk_clip_put, self._tk_clip_get, self._tk_clip_clear)
        self._clip_wipe = Debounce(self, CLEAR_SEC * 1000, self._clear_clipboard)
        self._root_frame: ttk.Frame | None = None
        self.mode = "vault"  # "vault" — passwords, "journal" — journal, "notes" — notes
        self._hide_views = IdleOnce(self, self._hide_other_views)  # takes the previous tab off screen once the new one is painted
        self._inbox_ticker = Ticker(self, INBOX_POLL_MS, self.pull_inbox)
        self.pages: dict[str, TabPage] = {}  # the tabs, made in show_main
        self._conflict_told = False  # the "changed elsewhere" dialog was shown for this unlock
        self._error_told = 0.0  # when the last "something went wrong" dialog was shown
        self._opening = False  # the master password is being checked on a thread
        self._views: dict[str, ttk.Frame] = {}  # their frames
        self._settlers: list[tuple[tk.Canvas, object]] = []  # the scrollable areas of the screen (tabpage.make_scrollable)
        self._content: ttk.Frame | None = None  # the area the tabs are placed over
        self.status: ttk.Label | None = None
        self._chunks = ChunkBuilder(self)  # long tabs are built in slices
        Tooltip.install(self)
        fieldundo.install(self)  # Ctrl+Z inside a one-line field undoes the typing first

        self.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self.bind_all("<ButtonPress-1>", self._click_away, add="+")
        if sys.platform == "win32":
            self.bind_all("<Control-KeyPress>", self._on_control_key, add="+")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.security = SecurityController(self)  # the idle lock and the Passwords tab guard
        self._settings_win: SettingsWindow | None = None
        self.gitlab = GitLabWatcher(self)  # the GitLab check, kept through a lock
        self.runs = RoutineRuns(self)  # the runs of routines, kept through a lock
        self._inbox_ticker.start()
        refresh_helper_in_background()  # a connected Claude Code hook gets this build's helper
        self.show_unlock()
        self._after_jobs: list[str] = [self.after(10, lambda: theme.caption_colour(self, RAIL, "#FFFFFF"))]  # raw timers, cancelled by destroy
        # every other window (settings, dialogs, an opened file): the title bar in the colours
        # of the theme, once it is on screen — set before that, Windows paints it its own way
        self.bind_class("Toplevel", "<Map>", self._paint_caption, add="+")
        # painted after the cards took their new width (RESIZE_SETTLE_MS), so once
        self._quiet_resize = theme.QuietResize(self, lambda: self._content, RESIZE_SETTLE_MS + 80, self._pin_view, self._unpin_view)

    def destroy(self) -> None:
        """Every timer first: a job firing after the window was destroyed printed
        `invalid command name "…_tick"` to the console (and hid real errors in the tests)."""
        for job in (self._save, self._capture, self._clip_wipe, self._tabs_fit, self._dpi_check, self._hide_views):
            job.cancel()
        for raw in getattr(self, "_after_jobs", ()):
            try:
                self.after_cancel(raw)
            except tk.TclError:
                pass
        self._inbox_ticker.stop()
        tickers = [getattr(getattr(self, "security", None), "ticker", None), getattr(getattr(self, "gitlab", None), "_gl_ticker", None), getattr(getattr(self, "gitlab", None), "_gl_drainer", None)]
        tickers += list(getattr(getattr(self, "runs", None), "tickers", ()))
        for ticker in tickers:
            if ticker is not None:
                ticker.stop()
        quiet = getattr(self, "_quiet_resize", None)
        if quiet is not None:
            quiet.thaw.cancel()
        if self._root_frame is not None:
            self._cancel_view_jobs()
        super().destroy()

    def report_callback_exception(self, exc_type, exc, tb) -> None:
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

    def _paint_caption(self, event: tk.Event) -> None:
        window = event.widget
        if isinstance(window, tk.Toplevel) and not window.overrideredirect():
            theme.caption_colour(window, palette.SIDEBAR, palette.TEXT)

    # ------------------------------------------------------------------ the session

    @property
    def vault(self) -> Vault | None:
        """The open vault; None while locked. Lives in the session, read by every tab."""
        return self.session.vault

    @property
    def vault_path(self) -> Path:
        return self.session.vault_path

    # ------------------------------------------------------------------ style

    def _start_dpi(self) -> int:
        """The DPI of the monitor the window opens on: where it was left, else the main one."""
        geometry = str(self.session.settings().get("geometry") or "")
        m = re.search(r"x\d+\+?(-?\d+)\+?(-?\d+)$", geometry)  # 1200x760+-2600+200
        point = (int(m.group(1)) + 100, int(m.group(2)) + 100) if m else (0, 0)
        return win32.dpi_at(*point) or round(self.winfo_fpixels("1i"))

    def _check_dpi(self) -> None:
        """The window moved to a monitor of another scale: draw everything again at that scale
        (sharp, not stretched by Windows) and keep the window's size on screen in proportion."""
        if self._quiet_resize.hwnd:  # mid-drag: once it pauses
            self._dpi_check.touch()
            return
        dpi = win32.dpi_of_window(win32.window_handle(self.winfo_id()))
        if not dpi or dpi == self._dpi:
            return
        ratio, self._dpi = dpi / self._dpi, dpi
        width, height = self.winfo_width(), self.winfo_height()
        self.tk.call("tk", "scaling", dpi / 72)
        self._old_painters.append(self.painter)
        self.painter = style.install(self)
        section = self._settings_win.section if self._settings_win is not None and self._settings_win.winfo_exists() else None
        self.close_settings()
        history = self.history
        if self.vault is None:
            self.show_unlock()
        else:
            self.show_main()
            self.history = history  # the undo steps survive the move
            self._update_undo_buttons()
        self.geometry(f"{round(width * ratio)}x{round(height * ratio)}")
        if section:
            self.settings_dialog(section)

    def _setup_style(self) -> None:
        self.painter = style.install(self)
        style.fit_button_surface(self)  # raised buttons blend into the sidebar, the card, the window
        popup.install(self)

    # ------------------------------------------------------------------ common

    def _clear(self) -> None:
        self._cancel_view_jobs()
        if self._root_frame is not None:
            self._root_frame.destroy()
        self.pages, self._views = {}, {}  # a page keeps the open entry and shown secrets: none of that outlives a lock
        self._root_frame = ttk.Frame(self)
        self._root_frame.pack(fill="both", expand=True)
        self.config(menu="")

    def _cancel_view_jobs(self) -> None:
        """The screen is going away (lock, language switch): a job still waiting to lay out
        or repaint a tab would hit destroyed widgets."""
        for _canvas, settler in self._settlers:
            settler.cancel()
        self._hide_views.cancel()
        self._chunks.cancel_all()
        for page in self.pages.values():
            page.cancel_jobs()

    # ---- building a long tab in slices (tabpage.ChunkBuilder)

    def build_in_chunks(self, name: str, items: list, build, after_chunk, done) -> None:
        self._chunks.build(name, items, build, after_chunk, done)

    def finish_chunks(self, name: str) -> None:
        self._chunks.finish(name)

    def cancel_chunks(self, name: str) -> None:
        self._chunks.cancel(name)

    def bind_root(self, sequence: str, func, add: str | None = "+") -> str:
        """A binding on the window itself and nothing else. `bind` on the root also fires
        for every widget in it (the toplevel is in every widget's bindtags), so a
        <Configure> or <Unmap> bound there reached Python once per widget on every
        resize, map and tab switch — a thousand times for a tab of tiles."""
        return self.bind_class(ROOT_TAG, sequence, func, add)

    def _on_close(self) -> None:
        self._clip_wipe.flush()  # the wipe timer dies with the window: a copied password would stay
        self._save_or_keep_copy()
        self._save_window()
        self.gitlab.close()
        for page in self.pages.values():
            page.cancel_jobs()
        self.destroy()

    def restart(self) -> None:
        """Close and start again with the same arguments (a new colour theme is taken at
        start). Everything is saved as on a close; the new window asks for the password."""
        args = autostart.launch_args(False)[:1 if getattr(sys, "frozen", False) else 2] + sys.argv[1:]
        self._on_close()
        try:
            subprocess.Popen(args)  # noqa: S603 — this very app
        except OSError:
            pass

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

    def close_child_windows(self) -> None:
        """Lock: every window that shows what the vault holds goes (settings, dialogs, an
        opened file, the output of a run) — a window left open would show it over the
        unlock screen. Such windows set `closes_on_lock`."""
        def walk(widget: tk.Misc) -> None:
            for child in widget.winfo_children():
                if getattr(child, "closes_on_lock", False):
                    if hasattr(child, "on_lock"):  # a window with work of its own keeps it first
                        child.on_lock()
                    child.destroy()
                else:
                    walk(child)
        walk(self)

    # ------------------------------------------------------------- window

    def _restore_window(self) -> None:
        """Window size and position as at the last close; the position only if it is on screen."""
        settings = self.session.settings()
        geometry = str(settings.get("geometry") or "")
        size, pos = parse_geometry(geometry, DEFAULT_GEOMETRY)
        if pos and on_screen(geometry, win32.virtual_screen()):
            self.geometry(f"{size}+{pos}")
        else:
            self.geometry(size)
        if settings.get("zoomed"):
            try:
                self.state("zoomed")
            except tk.TclError:
                pass

    def _save_window(self) -> None:
        try:
            zoomed = self.state() == "zoomed"
        except tk.TclError:
            zoomed = False
        update = {"zoomed": zoomed}
        if not zoomed:
            update["geometry"] = self.geometry()
        self.session.write_settings(update)  # window settings are not worth an error on exit

    def _on_control_key(self, event: tk.Event) -> str | None:
        """Ctrl+letter in any keyboard layout: app shortcuts and text editing (paste, copy…)."""
        if 49 <= event.keycode <= 57 and self.vault is not None:  # Ctrl+1…9: the tabs in their order
            self.show_tab_number(event.keycode - 48)
            return "break"
        if event.keycode in (66, 67) and self.vault is not None and self.mode == "vault" and (event.keycode == 66 or event.state & 0x1):
            # Ctrl+B / Ctrl+Shift+C on the Passwords tab: the login / the password of the entry on the card
            self.pages["vault"].copy_field("login" if event.keycode == 66 else "secret")
            return "break"
        if event.keycode in (90, 89) and self.vault is not None and not isinstance(self.focus_get(), tk.Text):
            # Z / Y in any layout (a text box undoes its typing itself)
            self.undo_key(redo=event.keycode == 89 or bool(event.state & 0x1))
            return "break"
        action = APP_SHORTCUTS.get(event.keycode)
        if action is not None:
            if self.vault is None:
                return None
            notes = self.mode == "notes"
            marks = self.mode == "bookmarks"
            {
                "new": self._new_for_mode,
                "save": self.save_now,
                "lock": self.lock,
                "find": lambda: self.page(self.mode if notes or marks else "vault").focus_search(),
                "export": self.export_dialog,
                "report": lambda: self.page("journal").report_dialog(),
            }[action]()
            return "break"
        keysym = event.keysym
        if len(keysym) == 1 and keysym.isascii():
            return None  # Latin layout — the standard Tk binding has already fired
        virtual = EDIT_SHORTCUTS.get(event.keycode)
        widget = getattr(event, "widget", None) or self.focus_get()
        if virtual is None or widget is None:
            return None
        try:
            widget.event_generate(virtual)
        except tk.TclError:
            return None
        return "break"

    def show_tab_number(self, n: int) -> None:
        """Ctrl+1…6: the n-th tab of the bar, as the user ordered them."""
        modes = list(getattr(self, "tab_buttons", {}))
        if 1 <= n <= len(modes):
            self.show_page(modes[n - 1])

    def _next_tab(self, step: int) -> str:
        """Ctrl+Tab / Ctrl+Shift+Tab: the next or the previous tab, round the bar."""
        modes = list(getattr(self, "tab_buttons", {}))
        if self.vault is not None and modes:
            i = modes.index(self.mode) if self.mode in modes else 0
            self.show_page(modes[(i + step) % len(modes)])
        return "break"

    def _click_away(self, event: tk.Event) -> None:
        """A click outside the field being typed in ends the typing: the field loses the
        cursor and its selection, as anywhere else in Windows."""
        widget = event.widget
        if not isinstance(widget, tk.Misc) or widget.winfo_toplevel() is not self:
            return
        try:
            focus = self.focus_get()
        except (KeyError, tk.TclError):  # the focus is in a menu or a closed window
            return
        if not isinstance(focus, (tk.Entry, tk.Text)) or focus is widget:
            return
        # only an empty place counts: a frame, a caption, a background. A control (the hand
        # cursor) does its own thing, and may itself put the cursor into a field.
        if not isinstance(widget, (tk.Frame, ttk.Frame, tk.Label, ttk.Label, tk.Canvas, tk.Tk)):
            return
        try:
            if str(widget.cget("cursor")) in ("hand2", "xterm", "size_nw_se"):
                return
        except tk.TclError:
            return
        if isinstance(widget.master, (tk.Entry, tk.Text)):  # the grey hint inside a field
            return
        if isinstance(focus, tk.Text):
            focus.tag_remove("sel", "1.0", "end")
        else:
            focus.selection_clear()
        self.focus_set()

    def _on_mousewheel(self, event: tk.Event) -> None:
        """The wheel over the card scrolls the card; the list scrolls itself."""
        widget = self.winfo_containing(event.x_root, event.y_root)
        if isinstance(widget, tk.Text) and widget.yview() != (0.0, 1.0):
            return  # a text with more than fits scrolls itself; the page must not move under it
        while widget is not None and not getattr(widget, "scrollable_canvas", False):
            widget = widget.master
        if widget is None:
            return
        canvas = widget
        if canvas.bbox("all") and canvas.bbox("all")[3] > canvas.winfo_height():
            # Windows reports the wheel in multiples of 120, macOS in small step counts
            steps = int(event.delta) if MAC else int(event.delta / 120)
            canvas.yview_scroll(-steps, "units")

    # ------------------------------------------------------------- unlock screen

    def show_unlock(self) -> None:
        self._clear()
        frame = self._root_frame
        panel = ttk.Frame(frame, style="Panel.TFrame", padding=self.painter.px(30))
        panel.place(relx=0.5, rely=0.46, anchor="center")
        box = ttk.Frame(panel, style="Card.TFrame", padding=(28, 22))
        box.pack()

        exists = self.vault_path.exists()
        px = self.painter.px
        logo = tk.Canvas(box, width=px(64), height=px(64), bg=CARD, highlightthickness=0)
        logo.create_image(px(32), px(32), image=self.painter.image(64, 64, 18, fill=ACCENT))
        logo.create_text(px(32), px(33), text="🗝", font=(FONT, pt(24)), fill=palette.ON_ACCENT)
        logo.grid(row=0, column=0, pady=(0, 12))
        head = ttk.Frame(box, style="Card.TFrame")
        head.grid(row=1, column=0)
        ttk.Label(head, text=APP_TITLE, style="PanelTitle.TLabel").pack()
        ttk.Label(head, text=_("passwords · bookmarks · journal · notes · routine · integrations"), style="PanelMuted.TLabel").pack(pady=(4, 6))
        subtitle = _("Enter the master password") if exists else _("Create a master password for the new vault")
        ttk.Label(box, text=subtitle, style="PanelSubtitle.TLabel").grid(row=2, column=0, pady=(10, 14))

        fields = ttk.Frame(box, style="Card.TFrame")
        fields.grid(row=3, column=0, sticky="ew")
        fields.columnconfigure(0, weight=1)
        self._pw1 = ttk.Entry(fields, show=BULLET, width=34, font=(FONT, pt(13)), style="Eye.TEntry")
        self._pw1.grid(row=0, column=0, pady=4, ipady=2, sticky="ew")
        self._pw1.focus_set()
        # a long phrase is typed blind otherwise; the eye sits inside the field at its right
        # edge, so the field lines up with the button under it
        eye = tk.Label(fields, text="👁", bg=palette.FIELD, fg=palette.MUTED, font=(FONT, pt(12)), cursor="hand2")
        eye.place(in_=self._pw1, relx=1.0, x=-self.painter.px(10), rely=0.5, anchor="e")
        Tooltip(eye, _("Show the password"))

        strength: ttk.Label | None = None
        if not exists:
            self._pw2 = ttk.Entry(fields, show=BULLET, width=34, font=(FONT, pt(13)), style="Eye.TEntry")  # as wide inside as the first
            self._pw2.grid(row=1, column=0, pady=4, ipady=2, sticky="ew")
            strength = ttk.Label(box, text="", style="PanelMuted.TLabel", justify="center")
            strength.grid(row=4, column=0, pady=(2, 0))
            self._pw1.bind("<KeyRelease>", lambda _e: strength.config(text=strength_text(self._pw1.get())), add="+")
            ttk.Label(
                box,
                text=_("Repeat the password. If you forget it, the data cannot be recovered."),
                style="PanelMuted.TLabel",
            ).grid(row=5, column=0, pady=(2, 8))
        else:
            self._pw2 = None

        def show(on: bool) -> None:
            for entry in (self._pw1, self._pw2):
                if entry is not None:
                    entry.config(show="" if on else BULLET)
            eye.config(text="🙈" if on else "👁")

        eye.bind("<Button-1>", lambda _e: show(self._pw1.cget("show") != ""))
        eye.bind("<Enter>", lambda _e: eye.config(fg=palette.TEXT))
        eye.bind("<Leave>", lambda _e: eye.config(fg=palette.MUTED))

        self._unlock_err = ttk.Label(box, text="", style="PanelError.TLabel", justify="center")
        self._unlock_err.grid(row=6, column=0, pady=(4, 0))
        # a long hint or error wraps at the field's width: the panel must not widen as one types
        fields.update_idletasks()
        for label in (strength, self._unlock_err):
            if label is not None:
                label.config(wraplength=self._pw1.winfo_reqwidth())

        btn_text = _("Open") if exists else _("Create vault")
        ttk.Button(box, text=btn_text, style="Primary.TButton", command=self._do_unlock).grid(
            row=7, column=0, pady=(10, 16), sticky="ew"
        )
        other_code, other_name = next((c, n) for c, n in LANGUAGES if c != language())
        ttk.Button(box, text=_(other_name), style="Link.TButton", command=lambda: self.set_ui_language(other_code)).grid(row=9, column=0, pady=(10, 0))
        self.bind("<Return>", lambda _e: self._do_unlock())

    def _do_unlock(self) -> None:
        pw = self._pw1.get()
        if not pw:
            self._unlock_err.config(text=_("Password is empty"))
            return
        if self._pw2 is not None:
            if pw != self._pw2.get():
                self._unlock_err.config(text=_("Passwords do not match"))
                return
            if len(pw) < 8:
                self._unlock_err.config(text=_("At least 8 characters"))
                return
        if self._opening:
            return
        wait = self.security.attempts.wait(time.monotonic())  # the same 1-2-4 s pause as the PIN prompt
        if wait:
            self._unlock_err.config(text=_("Too many wrong tries. Wait {n} s").format(n=math.ceil(wait)))
            return
        # the key is derived on a thread: Argon2 takes a tenth of a second here and more on
        # a slow laptop, and the window must keep painting meanwhile
        self._opening = True
        self.config(cursor="watch")
        self._unlock_err.config(text=_("Opening…"))
        got: dict = {}

        def work() -> None:
            try:
                got["vault"] = self.session.open_vault(pw)
            except Exception as exc:  # noqa: BLE001 — handed to the window's thread below
                got["error"] = exc

        thread = threading.Thread(target=work, name="unlock", daemon=True)
        thread.start()

        def wait() -> None:
            if thread.is_alive():
                self.after(20, wait)
                return
            self._opening = False
            self.config(cursor="")
            error = got.get("error")
            if error is None:
                if self.vault is None:
                    self.unlock(pw, got["vault"])
                return
            if not self._unlock_err.winfo_exists():  # the screen was made again meanwhile (language)
                return
            if isinstance(error, WrongPassword):
                self.security.attempts.fail(time.monotonic())
                self._unlock_err.config(text=_("Wrong password"))
                self._pw1.delete(0, "end")
            elif isinstance(error, BadFile):
                self._unlock_err.config(text=str(error))
            else:
                self._unlock_err.config(text="")
                raise error  # to report_callback_exception: errors.log and a dialog

        self.after(20, wait)

    def unlock(self, password: str, vault: Vault | None = None) -> None:
        """Open the vault (create it when there is none yet) and show the main screen.
        WrongPassword and BadFile come through from the vault. `vault` — opened already."""
        self.session.unlock(password, vault)
        self._conflict_told = False
        self.security.attempts.ok()  # the master password itself was given
        self.unbind("<Return>")
        self.show_main()
        self._after_jobs.append(self.after(800, lambda: self.vault is not None and self.runs.on_unlock()))

    def lock(self) -> None:
        """Save, forget the vault and show the unlock screen (Ctrl+L, idle, minimize)."""
        self._clip_wipe.flush()
        self._save_or_keep_copy()
        self.close_settings()
        self.close_child_windows()
        self._capture.cancel()
        self.history = None  # the snapshots hold the open data: none of it outlives a lock
        self.session.lock()
        self.security.reset()
        self.show_unlock()

    # ----------------------------------------------------------- main screen

    def show_main(self) -> None:
        self._clear()
        self._settlers = []
        root = self._root_frame

        bar = ttk.Frame(root, style="Rail.TFrame", padding=(14, 8, 10, 8))
        bar.pack(side="top", fill="x")
        px = self.painter.px
        mark = tk.Canvas(bar, width=px(30), height=px(30), bg=RAIL, highlightthickness=0)
        mark.create_image(px(15), px(15), image=self.painter.image(30, 30, 9, fill=ACCENT))
        mark.create_text(px(15), px(16), text="🗝", font=(FONT, pt(12)), fill=palette.ON_ACCENT)
        mark.pack(side="left", padx=(0, 8))
        # next to the logo, where the hand starts: settings, lock the app, lock the passwords.
        # Everything the old app menu held has its place in a tab or in Settings
        gear = ttk.Button(bar, text="⚙", style="NavIcon.TButton", command=self.settings_dialog)
        gear.pack(side="left")
        Tooltip(gear, _("Settings: language, start with the system, tabs, journal layout, security, links, AI assistants, data"))
        self._lock_anchor = ttk.Button(bar, text="⏻", style="NavIcon.TButton", command=self.lock)
        self._lock_anchor.pack(side="left", padx=(2, 0))
        Tooltip(self._lock_anchor, _("Lock the app") + f"  ({hotkeys('Ctrl+L')})")
        self._lock_tab_btn = ttk.Button(bar, text="🔒", style="NavIcon.TButton", command=self.security.lock_secrets)
        Tooltip(self._lock_tab_btn, _("Lock passwords") + " — " + _("Hide the passwords until the code is entered"))
        self._undo_btn = ttk.Button(bar, text="↶", style="NavIcon.TButton", command=self.undo)
        self._undo_btn.pack(side="left", padx=(2, 0))
        Tooltip(self._undo_btn, _("Undo the last change") + f"  ({hotkeys('Ctrl+Z')})" + "\n" + _("Passwords, journal, notes, bookmarks, routines. Inside a text field Ctrl+Z undoes the typing."))
        self._redo_btn = ttk.Button(bar, text="↷", style="NavIcon.TButton", command=self.redo)
        self._redo_btn.pack(side="left")
        Tooltip(self._redo_btn, _("Redo what was undone") + f"  ({hotkeys('Ctrl+Y')})")
        self._bar_divider = tk.Frame(bar, bg=RAIL_HOVER, width=1, height=24)
        self._bar_divider.pack(side="left", padx=(10, 12))
        self._tab_bar = bar
        self._tabs_frame = ttk.Frame(bar, style="Rail.TFrame")
        self._tabs_frame.pack(side="left")
        self.tab_buttons: dict[str, ttk.Button] = {}

        main_area = ttk.Frame(root)
        main_area.pack(side="top", fill="both", expand=True)
        self.status = ttk.Label(main_area, text="", style="Status.TLabel", padding=(16, 5))
        self.status.pack(side="bottom", fill="x")

        content = self._content = ttk.Frame(main_area)
        content.pack(fill="both", expand=True)
        self.pages, self._views = {}, {}  # a tab is made on its first visit (page); the Passwords tab now
        self.page("vault")
        self.security.reset()

        self._build_tab_buttons()
        self.update_lock_button()
        self._shown_keys: dict[str, tuple] = {}
        if self.mode not in self.tab_buttons:
            self.mode = next(iter(self.tab_buttons))
        self.show_page(self.mode)

        if sys.platform != "win32":  # on Windows _on_control_key does the same by key code
            self.bind(f"<{MOD_KEY}-n>", lambda _e: self._new_for_mode())
            self.bind(f"<{MOD_KEY}-s>", lambda _e: self.save_now())
            self.bind(f"<{MOD_KEY}-l>", lambda _e: self.lock())
            self.bind(f"<{MOD_KEY}-f>", lambda _e: self.pages["vault"].focus_search())
            self.bind(f"<{MOD_KEY}-e>", lambda _e: self.export_dialog())
            self.bind(f"<{MOD_KEY}-r>", lambda _e: self.page("journal").report_dialog())
            self.bind(f"<{MOD_KEY}-b>", lambda _e: self.mode == "vault" and (self.pages["vault"].copy_field("login"), "break")[1])
            self.bind(f"<{MOD_KEY}-Shift-C>", lambda _e: self.mode == "vault" and (self.pages["vault"].copy_field("secret"), "break")[1])
            for seq, again in ((f"<{MOD_KEY}-z>", False), (f"<{MOD_KEY}-Z>", True), (f"<{MOD_KEY}-y>", True)):
                self.bind(seq, lambda e, r=again: None if isinstance(self.focus_get(), tk.Text) else (self.undo_key(r), "break")[1])
        if sys.platform != "win32":
            for n in range(1, 10):
                self.bind(f"<{MOD_KEY}-Key-{n}>", lambda _e, n=n: (self.show_tab_number(n), "break")[1])
        self.bind_all("<Control-Tab>", lambda _e: self._next_tab(+1))
        for seq in ("<Control-Shift-Tab>", "<Control-ISO_Left_Tab>"):
            try:
                self.bind_all(seq, lambda _e: self._next_tab(-1))
            except tk.TclError:  # ISO_Left_Tab is an X11 keysym
                pass
        self.bind("<Alt-Up>", lambda _e: self.pages["vault"].move_entry(-1))
        self.bind("<Alt-Down>", lambda _e: self.pages["vault"].move_entry(+1))
        self.pages["vault"].open_first()
        self.pull_inbox()
        self.history = History(self.vault.object_states, UNDO_DOMAINS)
        self.history.reset()
        self._update_undo_buttons()

    def page(self, mode: str) -> TabPage:
        """The tab, made now if it was never shown: making all six at unlock kept the window
        from answering for a third of a second, for tabs most visits never open."""
        page = self.pages.get(mode)
        if page is None:
            page = self.pages[mode] = PAGE_TYPES[mode](self, self._content)
            self._views[mode] = page.frame
        return page

    def apply_journal_layout(self, layout: Layout) -> bool:
        """Keep a new journal layout and rebuild the Journal tab with it. False — settings.json
        could not be written (nothing changed)."""
        if not self.session.write_settings(layout.to_settings()):
            return False
        configure_layout(layout)
        self._rebuild_page("journal")
        return True

    def _pin_view(self) -> None:
        """A drag of the window began: the tab on screen keeps its size until it pauses."""
        view = self._views.get(self.mode) if self._root_frame is not None else None
        if view is not None and view.winfo_exists() and view.place_info():
            view.place_configure(relwidth=0, relheight=0, width=view.winfo_width(), height=view.winfo_height())

    def _unpin_view(self) -> None:
        self._fit_tabs()
        for view in self._views.values():
            if view.winfo_exists() and view.place_info() and float(view.place_info().get("relwidth") or 0) == 0:
                view.place_configure(relwidth=1, relheight=1, width=0, height=0)

    def show_page(self, mode: str, select_entry: Entry | None = None) -> None:
        """Bring a tab up; `select_entry` — show this entry on the Passwords tab (a bookmark tile's link)."""
        self.mode = mode
        for name in self._chunks.building():
            if name != mode:  # a tab left half built stops building under the new one: it slowed it down
                self._chunks.cancel(name)
                self._shown_keys.pop(name, None)  # and is built again on its next visit
        for m, btn in self.tab_buttons.items():
            btn.configure(style="NavActive.TButton" if m == mode else "Nav.TButton")
        view = self.page(mode).frame
        if not view.place_info():
            # under the tab on screen, at the size it will have: its cards are then built at
            # their real width, not at a guess that is laid out (and painted) again at once
            view.lower()
            view.place(x=0, y=0, relwidth=1, relheight=1)
            view.update_idletasks()
            for canvas, settle in self._settlers:
                if canvas.winfo_exists() and self.view_of(canvas) is view:
                    settle()
        # rebuild the tab's cards before it is shown and only when something changed:
        # a rebuild on screen made the cards jump on every switch
        key = self._view_key(mode)
        if self._shown_keys.get(mode) != key:
            self._shown_keys[mode] = key
            self._refresh_view(mode)
        elif mode in self.pages:
            self.pages[mode].on_shown()
        # the tab coming up is laid out under the one on screen and raised when ready, then
        # the old one is taken off: the switch shows up at once and nothing moves after it.
        # Only the tab on screen is mapped, so resizing the window lays out that one alone
        for canvas, settle in self._settlers:
            if canvas.winfo_exists() and self.view_of(canvas) is view:
                settle()
        view.update_idletasks()
        view.tkraise()
        # the previous tab is taken off screen at idle, after the new one is painted: unmapping
        # its hundreds of windows took a tenth of a second before the click was answered
        self._hide_views.touch()
        self._focus_into(view)
        if mode == "vault" and not self.security.secrets_open():
            self.security.focus_guard()
        if select_entry is not None and mode == "vault" and self.security.secrets_open():
            self.pages["vault"].select_entry(select_entry)

    def _hide_other_views(self) -> None:
        view = self._views.get(self.mode)
        for other in self._views.values():
            if other is not view and other.place_info():
                other.place_forget()

    def current_view(self) -> tk.Misc | None:
        """The frame of the tab on screen."""
        return self._views.get(self.mode)

    def add_settler(self, canvas: tk.Canvas, settler) -> None:
        """A scrollable area of a tab (tabpage.make_scrollable): show_page settles it."""
        self._settlers.append((canvas, settler))

    def view_of(self, widget: tk.Misc) -> tk.Misc | None:
        """The tab view a widget is on; None outside the views."""
        views = list(self._views.values())
        while widget is not None and widget not in views:
            widget = widget.master
        return widget

    def _focus_into(self, view: tk.Widget) -> None:
        """Keys must not go to a field of a tab underneath: take the focus off it."""
        try:
            focus = self.focus_get()
        except (KeyError, tk.TclError):
            return
        if focus is None or focus is self:
            return
        owner = focus
        while owner is not None and owner not in self._views.values():
            owner = owner.master
        if owner is not None and owner is not view:
            view.focus_set()

    def _view_key(self, mode: str) -> tuple:
        """What a tab shows depends on: the changes to the domains it draws from
        (VIEW_DOMAINS) and the day. The clock on the routine tiles is refreshed in place."""
        return self.pages[mode].view_key()

    def _refresh_view(self, mode: str) -> None:
        """Rebuild a tab for a visit: what was scrolled to stays scrolled to."""
        self.pages[mode].refresh(keep_scroll=True)

    # ---- tabs: order and visibility come from settings.json

    def tab_label(self, mode: str) -> str:
        return {
            "vault": _("🔐  Passwords"),
            "bookmarks": _("🔖  Bookmarks"),
            "journal": _("📝  Journal"),
            "notes": _("🗒  Notes"),
            "routines": _("⚡  Routine"),
            "integrations": _("🔌  Integrations"),
        }[mode]

    def tab_on(self, mode: str) -> bool:
        return mode not in tabs_hidden(self.session.settings())

    def _build_tab_buttons(self) -> None:
        for child in self._tabs_frame.winfo_children():
            child.destroy()
        self.tab_buttons = {}
        for n, mode in enumerate(visible_tabs(self.session.settings()), 1):
            btn = ttk.Button(self._tabs_frame, text=self.tab_label(mode), style="Nav.TButton", command=lambda m=mode: self.show_page(m))
            btn.pack(side="left", padx=(0, 2))
            name = self.tab_label(mode).split("  ", 1)[-1]
            keys = f"  ({hotkeys(f'Ctrl+{n}')})" if n <= 9 else ""
            Tooltip(btn, (f"{name} — {_(TAB_TIPS[mode])}" if mode in TAB_TIPS else name) + keys)  # the name too: a narrow bar shows icons only
            self.tab_buttons[mode] = btn
        # a narrow window shows the tabs as icons; the window is never narrower than that bar
        extra = self._lock_tab_btn.winfo_reqwidth()
        self._tabs_compact = None
        self._set_tabs_compact(False)
        self._tab_bar.update_idletasks()
        self._tabs_full_width = self._tab_bar.winfo_reqwidth() + extra
        self._set_tabs_compact(True)
        self._tab_bar.update_idletasks()
        self.minsize(max(MIN_WIDTH, self._tab_bar.winfo_reqwidth() + extra), 540)
        self._fit_tabs()

    def tab_text(self, mode: str, extra: str = "") -> str:
        """A tab button's text: the icon and the name, or the icon alone on a narrow bar."""
        label = self.tab_label(mode)
        return (label.split("  ", 1)[0] if self._tabs_compact else label) + extra

    def _set_tabs_compact(self, compact: bool) -> None:
        if compact == self._tabs_compact:
            return
        self._tabs_compact = compact
        for mode, btn in self.tab_buttons.items():
            if btn.winfo_exists():  # a button keeps a minimum width of its own: an icon needs little
                btn.config(text=self.tab_text(mode), width=3 if compact else 0)
        self.update_integrations_badge()

    def update_integrations_badge(self) -> None:
        """The unread GitLab news as a count on the Integrations tab button."""
        btn = getattr(self, "tab_buttons", {}).get("integrations")
        if btn is None or self.vault is None or not btn.winfo_exists():
            return
        n = self.vault.integrations.unread()
        btn.config(text=self.tab_text("integrations", f"  ({n})" if n else ""))

    def _fit_tabs(self) -> None:
        if not self._tabs_full_width or not getattr(self, "tab_buttons", None):
            return
        if self._quiet_resize.hwnd:  # mid-drag: the bar changes once the drag pauses (_unpin_view)
            return
        width = self.winfo_width()
        if width > 1:
            self._set_tabs_compact(width < self._tabs_full_width)

    def apply_tabs(self) -> None:
        """Settings changed the tabs: rebuild the bar; a hidden open tab gives way to the first one."""
        self._build_tab_buttons()
        self.show_page(self.mode if self.mode in self.tab_buttons else next(iter(self.tab_buttons)))

    def _new_for_mode(self) -> None:
        """Ctrl/Cmd+N: a new thing of the open tab (entry, note, bookmark, routine)."""
        self.pages[self.mode].new_item()

    def settings_dialog(self, section: str = "general") -> None:
        """The settings window; one at a time: an open one comes to the front on `section`."""
        if self.vault is None:
            return
        win = self._settings_win
        if win is not None and win.winfo_exists():
            if win.section != section:
                win.show(section)
            win.deiconify()
            win.lift()
            win.focus_force()
            return
        self._settings_win = SettingsWindow(self, section)

    def close_settings(self) -> None:
        """Lock and language switch: the settings window works on the open vault, so it goes."""
        win, self._settings_win = self._settings_win, None
        if win is not None and win.winfo_exists():
            win.destroy()

    def default_gitlab_url(self) -> str:
        """The GitLab address from File → Links, offered when the integration is empty."""
        return current_links().gitlab_url

    def update_lock_button(self) -> None:
        """The “Lock passwords” button is there only when the Passwords tab has a guard."""
        if self.vault is not None and self.vault.security.guarded:
            self._lock_tab_btn.pack(side="left", padx=(2, 0), after=self._lock_anchor)
        else:
            self._lock_tab_btn.pack_forget()

    def round_style(self, fill: str, border: str | None, radius: float = 12, outer: str = CARD, width: float = 1.0) -> str:
        """A ttk frame style for a rounded card of this look (see theme.RoundCard)."""
        return theme.round_style(ttk.Style(self), self.painter, fill, border, radius, outer, width)

    def set_ui_language(self, code: str) -> None:
        """Switch the UI language and rebuild the current screen; the choice is kept in settings.json."""
        if code == language():
            return
        self.save_now()
        self.session.write_settings({"language": code})
        set_language(code)
        if self.vault is None:
            self.show_unlock()
        else:
            self.show_main()

    # ------------------------------------------------------------- saving

    def pull_inbox(self, loud: bool = False) -> None:
        """Pick up what the sessions wrote into the inbox; the ticker calls this every minute."""
        if self.vault is None:
            return
        if self._capture.pending:  # the user's own change waiting to be recorded goes first
            self._capture.cancel()
            self._capture_now()
        # every minute: a failure goes to the status line, a dialog would come back each time
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
        if journal is not None and journal.days_tree.winfo_exists():
            journal.refresh()
        self.set_status(_("Added from sessions: {added} (the inbox had {total})").format(added=added, total=total))

    def mark_dirty(self, domain: str | None = None) -> None:
        """Data changed: save soon. `domain` — whose data (see revisions.DOMAINS), so the
        tabs drawing from it are rebuilt on their next visit; None — the change is already
        on screen, no tab needs a rebuild for it."""
        if domain is not None:
            self.session.touch(domain)
        self._save.touch()
        if self.history is not None:
            self._capture.touch()

    # ---- undo / redo

    def _capture_now(self) -> None:
        if self.history is not None and self.vault is not None:
            self.history.capture()
            self._update_undo_buttons()

    def _update_undo_buttons(self) -> None:
        h = self.history
        for btn, on in ((getattr(self, "_undo_btn", None), h is not None and h.can_undo), (getattr(self, "_redo_btn", None), h is not None and h.can_redo)):
            if btn is not None and btn.winfo_exists():
                btn.state(["!disabled"] if on else ["disabled"])

    def undo_key(self, redo: bool) -> None:
        """Ctrl+Z / Ctrl+Y: the typing of the field in focus first, like any editor; when the
        field has nothing to undo, the last change of the app."""
        widget = self.focus_get()
        if fieldundo.is_field(widget) and (fieldundo.redo(widget) if redo else fieldundo.undo(widget)):
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
        self.focus_set()  # a field being typed into would write its old text back over the undo
        self.update_idletasks()
        touched = (self.history.redo if redo else self.history.undo)(self.vault.set_object_state)
        self._update_undo_buttons()
        if not touched:
            self.set_status(_("Nothing to redo") if redo else _("Nothing to undo"))
            return
        for domain in touched:
            self.session.touch(domain)
        pages = set(touched) | ({"bookmarks"} if "vault" in touched else set())  # tiles show entries
        for name in pages:
            self._rebuild_page(name)
        self._save.touch()
        names = ", ".join(self.tab_label(d).split("  ", 1)[-1] for d in UNDO_DOMAINS if d in touched)  # without the icon
        self.set_status((_("Redone: {what}") if redo else _("Undone: {what}")).format(what=names))

    def _rebuild_page(self, name: str) -> None:
        """A fresh tab in place of the old one: the old one may hold objects the vault no
        longer has (after an undo) or a layout no longer in use."""
        old = self.pages.get(name)
        if old is None:
            return
        keep = getattr(old, "current", None)
        old.cancel_jobs()
        page = type(old)(self, self._content)
        self.pages[name], self._views[name] = page, page.frame
        old.frame.destroy()
        self._shown_keys.pop(name, None)
        if name == "vault":
            again = next((e for e in self.vault.entries if keep is not None and e.id == keep.id), None)
            page.select_entry(again) if again is not None else page.open_first()
            self.security.restore_guard()  # locked passwords stay locked on the new tab
        if self.mode == name:
            self.show_page(name)

    def save_now(self) -> str:
        """Write the vault at once (Ctrl+S, before a lock, before an export); the autosave
        calls this too. "ok", "conflict" (another window saved the file) or "error"."""
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

    def set_status(self, text: str) -> None:
        if self.status is not None and self.status.winfo_exists():
            self.status.config(text=text)

    # ------------------------------------------------------------- clipboard

    def copy_to_clipboard(self, value: str, secret: bool = True) -> None:
        """Copy to the clipboard. A secret stays out of the Windows clipboard history and
        cloud sync and is cleared after CLEAR_SEC; other text is copied as is."""
        if not value:
            return
        if not secret:
            self._tk_clip_put(value)
            self.set_status(_("Copied"))
            return
        if not self.security.secrets_open():  # the last line of the guard: no secret leaves while the tab is locked
            self.set_status(_("Passwords are locked"))
            return
        self._clip.put(value)
        self._clip_wipe.touch(self._clip.digest(value))  # the timer holds a hash, not the password, for the 30 s
        self.set_status(_("Copied. In {n} s the password is wiped from the clipboard").format(n=CLEAR_SEC))

    def _clear_clipboard(self, digest: str) -> None:
        if self._clip.wipe_if_digest(digest):
            self.set_status(_("The copied password was wiped from the clipboard"))

    # the window's own clipboard: what SecretClipboard falls back on outside Windows

    def _tk_clip_put(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()

    def _tk_clip_get(self) -> str | None:
        try:
            return self.clipboard_get()
        except tk.TclError:
            return None  # the clipboard no longer holds text

    def _tk_clip_clear(self) -> None:
        self.clipboard_clear()
        if MAC:
            # Tk on macOS forgets only its own copy: the system pasteboard kept the password
            try:
                subprocess.run(["pbcopy"], input=b"", timeout=2, check=False)  # noqa: S603, S607
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.update()

    # ------------------------------------------------------------- dialogs

    def open_folder(self) -> None:
        folder = self.vault_path.parent
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606
        else:
            subprocess.Popen(["open" if MAC else "xdg-open", str(folder)])  # noqa: S603, S607

    def import_env_dialog(self):
        return import_env_dialog(self)

    def export_dialog(self):
        return export_dialog(self)

    def import_all_dialog(self):
        return import_all_dialog(self)


def main() -> None:
    flags = {"--preview", autostart.MINIMIZED}
    args = [a for a in sys.argv[1:] if a not in flags]
    path = Path(args[0]) if args else None
    app = App(path, preview="--preview" in sys.argv[1:])
    if autostart.MINIMIZED in sys.argv[1:]:
        app.iconify()  # started with the system: the unlock screen waits in the taskbar
    if getattr(sys, "frozen", False):
        _refresh_autostart(app)
    app.mainloop()
    if "--preview" in sys.argv[1:]:
        remove_preview_copy(app.vault_path)  # the duplicate made for the look goes with the window
    # a site icon or a GitLab check may still be waiting on the network; the window is gone
    # and everything is saved, so the process does not wait for them
    if any(t.is_alive() and not t.daemon for t in threading.enumerate() if t is not threading.main_thread()):
        os._exit(0)


def _refresh_autostart(app: App) -> None:
    """An autostart entry left by a copy of the app elsewhere now starts this one."""
    try:
        autostart.refresh(autostart.launch_args(autostart.is_minimized(), app.vault_path, default_vault_path()))
    except OSError:
        pass
