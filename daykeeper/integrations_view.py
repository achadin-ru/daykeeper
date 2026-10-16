"""The “Integrations” tab: connections on the left, the selected one's settings and the feed
of notifications on the right.

GitLab is checked in a background thread every N minutes while the vault is open; the
token is read from a vault entry and never shown. New events go to the feed (kept in the
vault), a counter on the tab and a pop-up in the corner of the screen; on Windows the
taskbar button also flashes.

A page of the window (TabPage); the check is the window's `GitLabWatcher`."""

from __future__ import annotations

import time
from collections.abc import Callable
import tkinter as tk
import webbrowser
from tkinter import ttk

from . import palette, win32
from .bookmarks import entry_secret
from .gitlab import INTERVALS, RULES, Event, GitLabError, check as gitlab_check
from .i18n import _
from .jobs import JobPool
from .popup import Dropdown
from .theme import RoundCard
from .sidebar import SidebarKeeper
from .tabpage import Shell, TabPage, make_scrollable
from .timers import Debounce, Ticker
from .toast import Toast
from .widgets import make_fonts

POLL_TICK_MS = 15_000  # how often the app asks “is a check due?”
TOAST_MS = 8_000
FEED_MIN_WIDTH = 380  # px at 100 %; narrower than settings + this, the notifications go under the settings
FEED_PAGE = 20  # notifications shown at first and added by “Show more”: each is a few widgets
EVENT_ICONS = {
    "approved": "✅", "resolved": "💬", "pipeline": "❌", "merged": "🎉",
    "review_requested": "👀", "my_threads_resolved": "✔", "replies": "↩",
}


def event_text(ev: Event) -> str:
    try:
        return _(ev.text).format(**ev.args)
    except (KeyError, IndexError, ValueError):
        return ev.text


def flash_taskbar(window: tk.Tk) -> None:
    """Windows: flash the taskbar button until the window is focused."""
    win32.flash_window(win32.window_handle(window.winfo_id()))


class GitLabWatcher:
    """The GitLab check through the life of the window: the pool the check runs in, the
    tick that asks whether one is due, the text of the last outcome. Made once with the
    window and kept through a lock; the Integrations tab shows `status` when it is there."""

    def __init__(self, app: Shell):
        self.app = app
        self._gl_jobs = JobPool(workers=1, name="gitlab")
        self._gl_busy = False
        self.status = ""
        self._gl_ticker = Ticker(app, POLL_TICK_MS, self._gl_tick)
        self._gl_drainer = Ticker(app, 300, self._gl_drain)  # runs while a check is out
        self._gl_ticker.start()

    @property
    def page(self) -> IntegrationsPage | None:
        return self.app.pages.get("integrations")

    def close(self) -> None:
        """The window is closing: a check still out is not waited for."""
        self._gl_jobs.close()

    def _gl_token(self) -> str:
        entry = self.app.vault.entry_by_id(self.app.vault.integrations.gitlab.entry_id) if self.app.vault else None
        return entry_secret(entry) if entry is not None else ""

    def _gl_tick(self) -> None:
        self._gl_drain()
        if self.app.vault is None or self._gl_busy or not self.app.background_jobs or not self.app.tab_on("integrations"):
            return
        integ = self.app.vault.integrations
        gl = integ.gitlab
        if not gl.enabled or not gl.url or not self._gl_token():
            return
        if time.time() - integ.gitlab_checked >= gl.interval_min * 60:
            self.check_now(quiet=True)

    def check_now(self, quiet: bool = False) -> None:
        if self.app.vault is None or self._gl_busy:
            return
        gl = self.app.vault.integrations.gitlab
        token = self._gl_token()
        if not gl.url or not token:
            self.app.set_status(_("Fill in the address and pick the entry with the token"))
            return
        self._gl_busy = True
        self.app.set_status(_("Checking…"))
        settings = type(gl).from_dict(gl.to_dict())  # a copy for the thread
        integ = self.app.vault.integrations
        old = dict(integ.gitlab_state)
        self._gl_jobs.submit("check", gitlab_check, gl.url, token, settings, old, None, integ.since(time.time()), quiet=quiet)  # positional: keywords are the job's notes
        self._gl_drainer.start()

    def _gl_drain(self) -> bool:
        """Apply the answer of a check once it is there. True while one is still out."""
        results = self._gl_jobs.drain()
        if not results:
            return self._gl_busy
        self._gl_busy = False
        if self.app.vault is None:
            return False
        integ = self.app.vault.integrations
        result = results[-1]
        if not result.ok:
            exc = result.error
            integ.note_failed_check(time.time())
            # the known errors are in the catalog; any other answer must not kill the app
            self.app.set_status(_("Check failed: {error}").format(error=_(str(exc) if isinstance(exc, GitLabError) else repr(exc))))
            self.app.mark_dirty("integrations")
            return False
        found = result.value
        first = integ.apply_check(found, time.time())
        if found.events:
            self._notify(found.events)
        self.app.mark_dirty("integrations")
        stamp = time.strftime("%H:%M")
        if first:
            self.app.set_status(_("Connected as @{user}. Watching {n} MR(s); changes from now on will be reported.").format(user=integ.gitlab.username, n=len(found.snapshot)))
        else:
            self.app.set_status(_("Checked at {time}: {n} MR(s), new events: {events}").format(time=stamp, n=len(found.snapshot), events=len(found.events)))
        self.app.update_integrations_badge()  # the tab may not be made yet; its button is there
        page = self.page
        if page is not None and self.app.mode == "integrations":
            page.render_feed()
        return False

    def _notify(self, events: list[Event]) -> None:
        for ev in events[:4]:
            Toast(self.app, f"{EVENT_ICONS.get(ev.kind, '🔔')}  GitLab · {ev.title[:60]}", event_text(ev), ev.url)
        if len(events) > 4:
            Toast(self.app, "GitLab", _("And {n} more — see the Integrations tab").format(n=len(events) - 4))
        flash_taskbar(self.app)

    def set_status(self, text: str) -> None:
        self.status = text
        label = getattr(self.page, "_gl_status_label", None)
        if label is not None and label.winfo_exists():
            label.config(text=text)


class IntegrationsPage(TabPage):
    """The Integrations tab: the connections on the left, the settings of the picked one and
    the feed of notifications on the right. The check itself is the window's `gitlab`."""

    name = "integrations"

    def __init__(self, app: Shell, parent: tk.Misc):
        self._feed_box: tk.Frame | None = None  # the feed, made with the GitLab section
        self._feed_aside: bool | None = None  # the notifications stand right of the settings
        self._settings_width = 760  # what the settings column needs; measured on each render
        self._shown_sel = ""  # the connection on screen: a select of the same one draws nothing
        self._feed_parent: tk.Misc | None = None
        self._feed_head: tk.Frame | None = None
        self._feed_count = FEED_PAGE
        # a settings column narrower than they ask goes to one column: hints under their
        # fields, the ticks one under another, the texts wrapped at the column's width
        self._fits: list[Callable[[bool], None]] = []
        self._wraps: list[tuple[tk.Label, int]] = []  # a text and its widest wrap
        self._narrow: bool | None = None
        super().__init__(app, parent)

    # ---- what the window asks for

    def view_key(self) -> tuple:
        return super().view_key() + (self.app.gitlab.status,)

    def refresh(self, keep_scroll: bool = False) -> None:
        self._refresh_integrations()

    def update_badge(self) -> None:
        self.app.update_integrations_badge()


    # ---- the tab

    def build(self, frame: ttk.Frame) -> None:
        paned = ttk.PanedWindow(frame, orient="horizontal")
        paned.pack(fill="both", expand=True)
        left = ttk.Frame(paned, style="Sidebar.TFrame", padding=(16, 16, 12, 12))
        paned.add(left, weight=0)
        ttk.Label(left, text=_("CONNECTIONS"), style="SidebarMuted.TLabel", font=(palette.FONT, palette.pt(10), "bold")).pack(anchor="w", pady=(0, 6))
        self.integ_tree = ttk.Treeview(left, columns=("state",), show="tree", selectmode="browse")
        self.integ_tree.column("#0", width=160, stretch=True)
        self.integ_tree.column("state", width=60, anchor="e", stretch=False)
        self.integ_tree.pack(fill="both", expand=True)
        self.integ_tree.insert("", "end", iid="gitlab", text="  🦊 GitLab")
        self.integ_tree.insert("", "end", iid="jira", text="  🧭 Jira")
        self.integ_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())
        host = ttk.Frame(paned, style="Card.TFrame")
        paned.add(host, weight=1)
        SidebarKeeper(paned, minimum=self.app.painter.px(220), width=self.app.painter.px(260))
        # the settings and the notifications, each scrolling by itself: side by side when the
        # width allows, else the notifications under the settings in the one column
        self._ihost = host
        host.rowconfigure(0, weight=1)
        self._isettings = ttk.Frame(host, style="Card.TFrame")
        self._ifeed = ttk.Frame(host, style="Card.TFrame")
        self._iline = tk.Frame(host, bg=palette.BORDER, width=1)
        self.icanvas, self._ivsb, self.ibody = make_scrollable(self.app, self._isettings, (28, 20, 28, 20))
        self.icanvas.bind("<Configure>", lambda _e: self._fit_settings(), add="+")
        self.fcanvas, _fvsb, self.fbody = make_scrollable(self.app, self._ifeed, (20, 20, 28, 20))
        self._relayout = Debounce(host, 120, self._move_feed)
        host.bind("<Configure>", lambda _e: self._relayout.touch() if self._wants_aside() != self._feed_aside else None)
        self._ifonts = make_fonts({"head": (17, "bold"), "section": (10, "bold"), "row": (11,), "small": (9,)})
        self.integ_tree.selection_set("gitlab")

    def _refresh_integrations(self) -> None:
        if self.vault is None:
            return
        gl = self.vault.integrations.gitlab
        self.integ_tree.set("gitlab", "state", "●" if gl.enabled else "")
        self._render_integration()

    def _wants_aside(self) -> bool:
        sel = self.integ_tree.selection()
        gitlab = not sel or sel[0] == "gitlab"
        return gitlab and self._ihost.winfo_width() >= self._settings_width + self.app.painter.px(FEED_MIN_WIDTH)

    def _place_columns(self) -> None:
        aside = self._wants_aside()
        if aside == self._feed_aside:
            if aside:  # the settings were measured again: their column takes the new width
                self._ihost.columnconfigure(0, minsize=self._settings_width)
            return
        self._feed_aside = aside
        for w in (self._isettings, self._iline, self._ifeed):
            w.grid_forget()
        if aside:
            self._ihost.columnconfigure(0, weight=0, minsize=self._settings_width)
            self._ihost.columnconfigure(2, weight=1)
            self._isettings.grid(row=0, column=0, sticky="nsew")
            self._iline.grid(row=0, column=1, sticky="ns", pady=20)
            self._ifeed.grid(row=0, column=2, sticky="nsew")
        else:
            self._ihost.columnconfigure(0, weight=1, minsize=0)
            self._ihost.columnconfigure(2, weight=0)
            self._isettings.grid(row=0, column=0, columnspan=3, sticky="nsew")

    def _selected(self) -> str:
        sel = self.integ_tree.selection()
        return sel[0] if sel else "gitlab"

    def _on_select(self) -> None:
        # the tab selects GitLab when it is made and draws it in `refresh`: that select must
        # not draw the whole section a second time
        if self._selected() != self._shown_sel:
            self._render_integration()

    def _move_feed(self) -> None:
        """The width crossed the line between side by side and one column: only the
        notifications change their place, the settings stay as they are."""
        self._place_columns()
        if self._selected() == "gitlab" and self._feed_parent is not None and self._feed_parent is not self._feed_target():
            self._build_feed_block()

    def _feed_target(self) -> tk.Misc:
        return self.fbody if self._feed_aside else self.ibody

    def _render_integration(self) -> None:
        if self.vault is None:
            return
        self._place_columns()
        for child in (*self.ibody.winfo_children(), *self.fbody.winfo_children()):
            child.destroy()
        self._feed_parent = self._feed_head = self._feed_box = None
        self._fits, self._wraps, self._narrow = [], [], None
        self._shown_sel = self._selected()
        if self._shown_sel == "jira":
            self._render_jira_stub()
        else:
            self._render_gitlab()

    def _render_jira_stub(self) -> None:
        tk.Label(self.ibody, text="Jira", bg=palette.CARD, fg=palette.TEXT, font=self._ifonts["head"]).pack(anchor="w")
        tk.Label(
            self.ibody,
            text=_("Not connected yet. The tracker address (Journal tab → Links…) already turns issue keys into links; notifications from Jira can follow the same scheme as GitLab."),
            bg=palette.CARD, fg=palette.MUTED, font=self._ifonts["row"], justify="left", wraplength=620,
        ).pack(anchor="w", pady=(6, 0))

    def _render_gitlab(self) -> None:
        integ = self.vault.integrations
        gl = integ.gitlab
        body = self.ibody
        head = tk.Frame(body, bg=palette.CARD)
        head.pack(fill="x")
        tk.Label(head, text="🦊 GitLab", bg=palette.CARD, fg=palette.TEXT, font=self._ifonts["head"]).pack(side="left")
        enabled = tk.BooleanVar(value=gl.enabled)

        def toggle_enabled():
            gl.enabled = enabled.get()
            self.app.mark_dirty("integrations")
            self.integ_tree.set("gitlab", "state", "●" if gl.enabled else "")
            if gl.enabled and not integ.gitlab_state:
                self.app.gitlab.check_now()

        ttk.Checkbutton(head, text=_("Watch"), variable=enabled, command=toggle_enabled, style="Card.TCheckbutton").pack(side="right")
        about = tk.Label(
            body, text=_("Checks your open merge requests every few minutes and tells you what changed. The token needs the read_api scope and is taken from a password entry."),
            bg=palette.CARD, fg=palette.MUTED, font=self._ifonts["row"], justify="left", wraplength=640,
        )
        about.pack(anchor="w", pady=(4, 14))
        self._wraps.append((about, 640))

        form = tk.Frame(body, bg=palette.CARD)
        form.pack(fill="x")
        url_var = tk.StringVar(value=gl.url or self.app.default_gitlab_url())
        entries = sorted(self.vault.entries, key=lambda e: e.name.lower())
        none_label = _("— pick an entry —")
        labels = [none_label] + [e.name for e in entries]
        current = self.vault.entry_by_id(gl.entry_id)
        entry_var = tk.StringVar(value=current.name if current in entries else none_label)
        interval_var = tk.StringVar(value=_("every {n} min").format(n=gl.interval_min))
        interval_labels = [_("every {n} min").format(n=n) for n in INTERVALS]

        def row(r, label, widget, hint=""):
            tk.Label(form, text=label, bg=palette.CARD, fg=palette.TEXT, font=self._ifonts["row"]).grid(row=2 * r, column=0, sticky="w", padx=(0, 14), pady=4)
            widget.grid(row=2 * r, column=1, sticky="w", pady=4)
            if hint:
                note = tk.Label(form, text=hint, bg=palette.CARD, fg=palette.MUTED, font=self._ifonts["small"])
                self._fits.append(lambda narrow, n=note: n.grid(
                    row=2 * r + 1 if narrow else 2 * r, column=1 if narrow else 2, sticky="w", padx=(0 if narrow else 10, 0), pady=(0, 4) if narrow else 0))

        url_entry = ttk.Entry(form, textvariable=url_var, width=42)
        row(0, _("Address"), url_entry, "https://gitlab.example.com")
        row(1, _("Token from entry"), Dropdown(form, textvariable=entry_var, values=labels, width=40), _("its secret field holds the token"))
        row(2, _("Check"), Dropdown(form, textvariable=interval_var, values=interval_labels, width=16))

        def save_form(*_):
            gl.url = url_var.get().strip().rstrip("/")
            label = entry_var.get()
            gl.entry_id = entries[labels.index(label) - 1].id if label in labels[1:] else ""
            if interval_var.get() in interval_labels:
                gl.interval_min = INTERVALS[interval_labels.index(interval_var.get())]
            self.app.mark_dirty("integrations")

        for var in (url_var, entry_var, interval_var):
            var.trace_add("write", save_form)

        tk.Label(body, text=_("NOTIFY ME WHEN"), bg=palette.CARD, fg=palette.MUTED, font=self._ifonts["section"]).pack(anchor="w", pady=(18, 6))
        rules = tk.Frame(body, bg=palette.CARD)
        rules.pack(fill="x")
        self._rule_vars = {}
        ticks: list[ttk.Checkbutton] = []
        for code, (label, _on) in RULES.items():
            var = tk.BooleanVar(value=gl.rule(code))
            self._rule_vars[code] = var

            def flip(c=code, v=var):
                gl.rules[c] = v.get()
                integ.gitlab_full = 0.0  # a rule just on has no facts yet: the next check asks about all
                self.app.mark_dirty("integrations")

            ticks.append(ttk.Checkbutton(rules, text=f"{EVENT_ICONS[code]}  {_(label)}", variable=var, command=flip, style="Card.TCheckbutton"))
        self._fits.append(lambda narrow: self._grid_ticks(ticks, narrow))

        self._render_projects(body)

        actions = tk.Frame(body, bg=palette.CARD)
        actions.pack(fill="x", pady=(16, 0))
        ttk.Button(actions, text=_("Check now"), style="Primary.TButton", command=self.app.gitlab.check_now).pack(side="left")
        who = _("Signed in as @{user}").format(user=gl.username) if gl.username else ""
        self._gl_status_label = tk.Label(actions, text=self.app.gitlab.status or who, bg=palette.CARD, fg=palette.MUTED, font=self._ifonts["row"], justify="left", wraplength=520)
        self._gl_status_label.pack(side="left", padx=(12, 0))
        for fit in self._fits:
            fit(False)  # the wide form is measured: it says how wide the settings column wants to be

        body.update_idletasks()  # the settings column is as wide as the settings ask
        self._settings_width = body.winfo_reqwidth() + self._ivsb.winfo_reqwidth()  # the scrollbar shares the column
        self._place_columns()  # the width is known now: the feed is made once, in its place
        self._build_feed_block()
        self._narrow = False
        self._fit_settings()

    def _render_projects(self, body: tk.Misc) -> None:
        """Which projects are watched: a tick per project with my open MRs; none ticked — all."""
        integ = self.vault.integrations
        gl = integ.gitlab
        tk.Label(body, text=_("PROJECTS"), bg=palette.CARD, fg=palette.MUTED, font=self._ifonts["section"]).pack(anchor="w", pady=(18, 2))
        known = sorted(set(integ.gitlab_projects) | set(gl.projects))

        def hint() -> str:
            if not known:
                return _("The projects with your open merge requests appear here after the first check.")
            return _("Only the ticked projects are watched.") if gl.projects else _("None ticked — all projects are watched.")

        note = tk.Label(body, text=hint(), bg=palette.CARD, fg=palette.MUTED, font=self._ifonts["small"], justify="left", wraplength=640)
        note.pack(anchor="w", pady=(0, 6))
        self._wraps.append((note, 640))
        box = tk.Frame(body, bg=palette.CARD)
        box.pack(fill="x")
        ticks: dict[str, tk.BooleanVar] = {}

        def changed() -> None:
            gl.projects = [p for p in known if ticks[p].get()]
            integ.gitlab_full = 0.0  # MRs of a project just added have no facts yet
            note.configure(text=hint())
            self.app.mark_dirty("integrations")

        boxes = []
        for path in known:
            ticks[path] = tk.BooleanVar(value=path in gl.projects)
            boxes.append(ttk.Checkbutton(box, text=path, variable=ticks[path], command=changed, style="Card.TCheckbutton"))
        self._fits.append(lambda narrow: self._grid_ticks(boxes, narrow))

    @staticmethod
    def _grid_ticks(ticks: list[ttk.Checkbutton], narrow: bool) -> None:
        """Two columns of ticks, or one when the settings column is narrow."""
        for i, tick in enumerate(ticks):
            tick.grid(row=i if narrow else i // 2, column=0 if narrow else i % 2, sticky="w", padx=(0, 24), pady=2)

    def _fit_settings(self) -> None:
        """The settings to the width of their column: one column of everything when the
        wide form does not fit, the texts wrapped at the width."""
        if self._narrow is None:
            return  # not drawn yet, or a section without the form
        room = self.icanvas.winfo_width()
        if room <= 1:
            return
        for label, widest in self._wraps:
            if label.winfo_exists():
                label.configure(wraplength=max(200, min(widest, room - self.app.painter.px(64))))
        narrow = room + self._ivsb.winfo_reqwidth() < self._settings_width
        if narrow != self._narrow:
            self._narrow = narrow
            for fit in self._fits:
                fit(narrow)

    def _build_feed_block(self) -> None:
        """The head of the notifications and their list, right of the settings or under them."""
        integ = self.vault.integrations
        for w in (self._feed_head, self._feed_box):
            if w is not None and w.winfo_exists():
                w.destroy()
        feed_parent = self._feed_parent = self._feed_target()
        feed_head = self._feed_head = tk.Frame(feed_parent, bg=palette.CARD)
        feed_head.pack(fill="x", pady=(0 if self._feed_aside else 26, 6))
        tk.Label(feed_head, text=_("NOTIFICATIONS"), bg=palette.CARD, fg=palette.MUTED, font=self._ifonts["section"]).pack(side="left")
        clear = tk.Label(feed_head, text=_("Clear"), bg=palette.CARD, fg=palette.MUTED, font=self._ifonts["small"], cursor="hand2")
        clear.pack(side="right")
        clear.bind("<Button-1>", lambda _e: (integ.clear_feed(), self.app.mark_dirty("integrations"), self.render_feed(), self.update_badge()))
        read = tk.Label(feed_head, text=_("Mark all read"), bg=palette.CARD, fg=palette.LINK, font=self._ifonts["small"], cursor="hand2")
        read.pack(side="right", padx=(0, 14))
        read.bind("<Button-1>", lambda _e: (integ.mark_all_read(), self.app.mark_dirty("integrations"), self.render_feed(), self.update_badge()))
        self._feed_box = tk.Frame(feed_parent, bg=palette.CARD)
        self._feed_box.pack(fill="x")
        self.render_feed()

    def render_feed(self) -> None:
        box = self._feed_box
        if box is None or not box.winfo_exists() or self.vault is None:
            return
        for child in box.winfo_children():
            child.destroy()
        feed = self.vault.integrations.feed
        if not feed:
            empty = tk.Label(box, text=_("Nothing yet. Events appear here and as pop-ups in the corner of the screen."),
                             bg=palette.CARD, fg=palette.MUTED, font=self._ifonts["row"], anchor="w", justify="left", width=1)
            empty.pack(fill="x")  # width=1: it wraps in a narrow column instead of being cut
            empty.bind("<Configure>", lambda e: empty.configure(wraplength=max(80, e.width - 2)))
            return
        # the newest only: a hundred cards took two seconds to lay out on each visit
        for ev in feed[:self._feed_count]:
            bg = palette.TILE if ev.read else palette.ACCENT_SOFT
            frame = RoundCard(box, self.app, bg, palette.BORDER, radius=10, outer=palette.CARD, padx=6, pady=2, cursor="hand2")
            frame.pack(fill="x", pady=3)
            row = frame.body
            row.config(cursor="hand2")
            tk.Label(row, text=EVENT_ICONS.get(ev.kind, "🔔"), bg=bg, font=self._ifonts["row"]).pack(side="left")
            texts = tk.Frame(row, bg=bg)
            texts.pack(side="left", fill="x", expand=True, padx=(8, 0))
            for text, fg, font in ((event_text(ev), palette.TEXT, self._ifonts["row"]), (ev.title, palette.MUTED, self._ifonts["small"])):
                # width=1: a long title wraps in a narrow column instead of widening it
                line = tk.Label(texts, text=text, bg=bg, fg=fg, font=font, anchor="w", justify="left", width=1)
                line.pack(fill="x")
                line.bind("<Configure>", lambda e, w=line: w.configure(wraplength=max(80, e.width - 2)))
            tk.Label(row, text=time.strftime("%d.%m %H:%M", time.localtime(ev.ts)), bg=bg, fg=palette.MUTED, font=self._ifonts["small"]).pack(side="right")

            def open_event(_e=None, e=ev):
                e.read = True
                if e.url:
                    webbrowser.open(e.url)
                self.app.mark_dirty("integrations")
                self.render_feed()
                self.update_badge()

            for w in (frame, row, texts, *texts.winfo_children(), *row.winfo_children()):
                w.bind("<Button-1>", open_event)
        rest = len(feed) - self._feed_count
        if rest > 0:
            more = tk.Label(box, text=_("Show more ({n})").format(n=rest), bg=palette.CARD, fg=palette.LINK, font=self._ifonts["small"], cursor="hand2")
            more.pack(anchor="w", pady=(8, 0))
            more.bind("<Button-1>", lambda _e: self._show_more())

    def _show_more(self) -> None:
        self._feed_count += FEED_PAGE
        self.render_feed()
