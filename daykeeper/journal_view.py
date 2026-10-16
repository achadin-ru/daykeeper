"""The “Journal” tab: the day list on the left, the day editor on the right — what was
verified, failed and filed, the activity lines, the plans — and the standup report built
from a day. The sessions' inbox is pulled by the window; the page just redraws."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme
from .dialogs import Dialog, ask_yes_no
from .i18n import _, fmt_day
from .journal import DEFAULT_OTHER, Day, build_report, inbox_path_for, parse_activity_lines, parse_lines, today
from .journal_layout import ROLE_MR, current as current_layout
from .palette import BG, FONT, MONO, TEXT, WHITE, pt
from .popup import PopupMenu
from .sidebar import SidebarKeeper
from .tabpage import Shell, TabPage, make_scrollable
from .widgets import FlowRow, Placeholder, TextField, Tooltip, more_button, soft_edge


class JournalPage(TabPage):
    """The Journal tab. `jday` is the day on the editor, None while no day is picked."""

    name = "journal"

    def __init__(self, app: Shell, parent: tk.Misc):
        self.jday: Day | None = None
        self._jloading = False
        super().__init__(app, parent)

    def refresh(self, keep_scroll: bool = False) -> None:
        self._refresh_days(select=self.jday.date if self.jday else today())

    def build(self, frame: ttk.Frame) -> None:
        layout = current_layout()
        paned = ttk.PanedWindow(frame, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned, style="Sidebar.TFrame", padding=(16, 16, 12, 12))
        paned.add(left, weight=0)
        ttk.Label(left, text=_("DAYS"), style="SidebarMuted.TLabel", font=(FONT, pt(10), "bold")).pack(anchor="w", pady=(0, 6))
        self.days_tree = ttk.Treeview(left, columns=("counts",), show="tree", selectmode="browse")
        self.days_tree.column("#0", width=170, stretch=True)
        self.days_tree.column("counts", width=90, anchor="e", stretch=False)
        self.days_tree.pack(fill="both", expand=True)
        self.days_tree.bind("<<TreeviewSelect>>", self._on_day_select)
        Tooltip(self.days_tree, _("The numbers on the right: {columns}").format(columns=" / ".join(_(c.label) for c in layout.visible_columns()) or _("items")))
        dbtns = ttk.Frame(left, style="Sidebar.TFrame")
        dbtns.pack(side="bottom", fill="x", pady=(12, 0), before=self.days_tree)  # packed first from the bottom: a short window shrinks the list, not the buttons
        ttk.Button(dbtns, text=_("+ Today"), style="Primary.TButton", command=self._add_today).pack(side="left")
        ttk.Button(dbtns, text=_("Delete day"), style="Danger.TButton", command=self._delete_day).pack(side="right")

        host = ttk.Frame(paned, style="Card.TFrame")
        paned.add(host, weight=1)
        SidebarKeeper(paned, minimum=self.app.painter.px(240))
        # the buttons stay at the bottom of the pane; only the day above them scrolls
        actions = FlowRow(host, style="Card.TFrame", padding=(28, 12, 28, 16))  # a narrow pane wraps the buttons
        actions.pack(side="bottom", fill="x")
        soft_edge(host).pack(side="bottom", fill="x")
        _canvas, _vsb, right = make_scrollable(self.app, host, (28, 20, 28, 16))
        self.jhost = host
        columns = layout.visible_columns()
        span = max(1, len(columns))  # the day's columns side by side; everything else spans them
        for col in range(span):
            right.columnconfigure(col, weight=1, uniform="sec")

        self.jtitle = ttk.Label(right, text="", style="Card.TLabel", font=(FONT, pt(17), "bold"))
        self.jtitle.grid(row=0, column=0, columnspan=span, sticky="w")

        ttk.Label(right, text=_("1. DONE — what the day was about, one line"), style="Section.TLabel").grid(
            row=1, column=0, columnspan=span, sticky="w", pady=(14, 4)
        )
        self.jsummary_var = tk.StringVar()
        self.jsummary_var.trace_add("write", lambda *_: self._jsync())
        self.jsummary_entry = ttk.Entry(right, textvariable=self.jsummary_var, font=(FONT, pt(11)))
        self.jsummary_entry.grid(row=2, column=0, columnspan=span, sticky="ew")
        self.jsummary_hint = Placeholder(self.jsummary_entry, "", WHITE, padx=13)  # the text follows the day (_update_summary_hint)

        kinds = layout.visible_kinds()
        example = next((k for k in kinds if k.role == ROLE_MR), kinds[0] if kinds else None)
        sample = f"{_(example.label)}: {'!42, !43' if example.role == ROLE_MR else '…'}" if example else ""
        activity_title = ttk.Label(
            right,
            text=_("ACTIVITY — {kinds}; a line like “{sample}”").format(kinds=", ".join(_(k.label) for k in kinds), sample=sample),
            style="Section.TLabel", wraplength=900, justify="left",
        )
        activity_title.grid(row=10, column=0, columnspan=span, sticky="w", pady=(14, 4))
        side_pad = 28 + 28  # the card's padding left and right (make_scrollable above)
        right.bind("<Configure>", lambda e: activity_title.configure(wraplength=max(200, e.width - side_pad)), add="+")  # wraps inside the card
        self.jactivity = TextField(right, height=4)
        self.jactivity.grid(row=11, column=0, columnspan=span, sticky="ew")
        self.jactivity.bind("<<Modified>>", lambda _e: self._jtext_modified("activities"))

        # the verdict columns come right after the summary — what passed and failed is the
        # point of the tab, and at 760 px of height they sat below the fold under the activity box
        quick = FlowRow(right, style="Card.TFrame")
        quick.grid(row=3, column=0, columnspan=span, sticky="ew", pady=(14, 4))
        add = ttk.Frame(quick, style="Card.TFrame")  # the label stays with its field
        ttk.Label(add, text=_("Add"), style="CardMuted.TLabel").pack(side="left")
        self.jquick_var = tk.StringVar()
        quick_entry = ttk.Entry(add, textvariable=self.jquick_var, width=16, font=(FONT, pt(11)))
        quick_entry.pack(side="left", padx=(8, 0))
        quick.add(add, padx=(0, 8))
        if columns:
            quick_entry.bind("<Return>", lambda _e: self._quick_add(columns[0].id))
        self._jquick_entry = quick_entry
        for col in columns:
            quick.add(ttk.Button(quick, text=_(col.button or col.label), style="Small.TButton", command=lambda c=col.id: self._quick_add(c)), padx=(0, 6))
        if kinds:
            act_btn = ttk.Button(quick, text=_("Activity") + "  ▾", style="Quiet.TButton")
            act_menu = PopupMenu(act_btn)
            for kind in kinds:
                act_menu.add_command(label=_(kind.label), command=lambda k=kind.id: self._quick_add_activity(k))
            act_btn.config(command=lambda: act_menu.post_below(act_btn))
            quick.add(act_btn, padx=(8, 0))
            Tooltip(act_btn, _("Adds the text from the field on the left as activity of the chosen kind; for MRs — !42 or a link"))

        self.jsections: dict[str, TextField] = {}
        for i, col in enumerate(columns):
            box = ttk.Frame(right, style="Card.TFrame")
            box.grid(row=4, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            ttk.Label(box, text=_(col.label).upper(), style="Section.TLabel").pack(anchor="w", pady=(6, 4))
            txt = TextField(box, height=8)
            txt.pack(fill="both", expand=True)
            txt.bind("<<Modified>>", lambda _e, a=col.id: self._jtext_modified(a))
            self.jsections[col.id] = txt

        self.jplans = TextField(right, height=3)
        self.jother = TextField(right, height=2)
        if layout.show_plans:
            ttk.Label(right, text=_("2. PLANS"), style="Section.TLabel").grid(row=14, column=0, columnspan=span, sticky="w", pady=(14, 4))
            self.jplans.grid(row=15, column=0, columnspan=span, sticky="ew")
        self.jplans.bind("<<Modified>>", lambda _e: self._jtext_modified("plans"))
        if layout.show_other:
            other_title = _("3. OUT OF OFFICE / OTHER") if layout.show_plans else _("2. OUT OF OFFICE / OTHER")
            ttk.Label(right, text=other_title, style="Section.TLabel").grid(row=16, column=0, columnspan=span, sticky="w", pady=(14, 4))
            self.jother.grid(row=17, column=0, columnspan=span, sticky="ew")
        self.jother.bind("<<Modified>>", lambda _e: self._jtext_modified("other"))

        actions.add(ttk.Button(actions, text=_("📋  Build report"), style="Primary.TButton", command=self.report_dialog))
        inbox_btn = ttk.Button(actions, text=_("Read the inbox now"), style="Small.TButton", command=lambda: self.app.pull_inbox(loud=True))
        actions.add(inbox_btn, padx=(8, 0))
        Tooltip(inbox_btn, _("The sessions' inbox is read every minute by itself; this reads it right away"))
        # the settings of the journal are a menu, not two more buttons: the bar is one line
        # even in a narrow window, and the columns above it get the height
        self.jmore = more_button(actions, [(_("Links…"), lambda: self.app.settings_dialog("links")), (_("Set up the journal…"), lambda: self.app.settings_dialog("journal"))])
        actions.add(self.jmore, padx=(6, 0))
        Tooltip(self.jmore, _("Links to the tracker and GitLab; the columns, kinds of activity and the report"))
        self.jinbox_label = ttk.Label(actions, text="", style="CardMuted.TLabel")
        actions.add(self.jinbox_label, padx=(10, 0), side="right")
        self._jinbox_tip = Tooltip(self.jinbox_label, "")

        self.jplaceholder = ttk.Label(
            host, text=_("Pick a day on the left\nor press “+ Today”"), style="CardMuted.TLabel", font=(FONT, pt(13)), justify="center"
        )

    # ---- day list

    def _refresh_days(self, select: str | None = None) -> None:
        if self.vault is None:
            return
        journal = self.vault.journal
        journal.get(today(), create=True)  # today is always at hand
        self.days_tree.delete(*self.days_tree.get_children())
        for date in journal.dates():
            d = journal.days[date]
            self.days_tree.insert("", "end", iid=date, text=f"  {fmt_day(date)}", values=(self._counts(d),))
        target = select if select in journal.days else (journal.dates()[0] if journal.dates() else None)
        if target is not None:
            self._jloading = True
            try:
                self.days_tree.selection_set(target)
                self.days_tree.see(target)
            finally:
                self._jloading = False
            if self.jday is None or self.jday.date != target:
                self._load_day(journal.days[target])
            else:
                self._load_day(self.jday)
        else:
            self._load_day(None)

    def _on_day_select(self, _event=None) -> None:
        if self._jloading or self.vault is None:
            return
        sel = self.days_tree.selection()
        # the <<TreeviewSelect>> Tk delivers after _refresh_days picked the day itself: it
        # is already loaded, and loading it again would reset the cursor and the undo history
        if not sel or sel[0] not in self.vault.journal.days or (self.jday is not None and self.jday.date == sel[0]):
            return
        self._load_day(self.vault.journal.days[sel[0]])

    def _add_today(self) -> None:
        if self.vault is None:
            return
        self.vault.journal.get(today(), create=True)
        self._refresh_days(select=today())
        self._jquick_entry.focus_set()

    def _delete_day(self) -> None:
        if self.vault is None or self.jday is None:
            return
        if not ask_yes_no(self.app, _("Delete day {day} from the journal?").format(day=fmt_day(self.jday.date))):
            return
        self.vault.backup_now()  # a deleted day cannot be undone after a lock: a copy first
        self.vault.journal.remove(self.jday.date)
        self.jday = None
        self._refresh_days()
        self.app.mark_dirty("journal")

    # ---- day editor

    def _load_day(self, day: Day | None) -> None:
        self._jloading = True
        try:
            self.jday = day
            if day is None:
                self.jplaceholder.place(relx=0.5, rely=0.45, anchor="center")
                self.jplaceholder.lift()
                return
            self.jplaceholder.place_forget()
            self.jtitle.config(text=fmt_day(day.date) + (_("  · today") if day.date == today() else ""))
            if self.jsummary_var.get() != day.summary:
                self.jsummary_var.set(day.summary)
            self._set_text(self.jactivity, day.activity_lines(), parse_activity_lines)
            self._update_summary_hint()
            for cid, field in self.jsections.items():
                self._set_text(field, "\n".join(day.columns.get(cid, [])), parse_lines)
            self._set_text(self.jplans, day.plans)
            self._set_text(self.jother, day.other or _(DEFAULT_OTHER))
            inbox = inbox_path_for(self.app.vault_path)
            self.jinbox_label.config(text=_("inbox: {name}").format(name=inbox.name))
            self._jinbox_tip.text = _("Sessions write the day's events here:") + chr(10) + str(inbox)
        finally:
            self._jloading = False

    @staticmethod
    def _set_text(widget: tk.Text, value: str, parse=None) -> None:
        """Put `value` into the field unless what is there already means the same (`parse`
        reads both the way the day does): reloading a text the user is typing in moved the
        cursor and put the reload into the undo history, so Ctrl+Z wiped the field. A real
        change starts the history afresh and keeps the cursor where it was."""
        current = widget.get("1.0", "end-1c")
        if current == value or (parse is not None and parse(current) == parse(value)):
            widget.edit_modified(False)
            return
        insert = widget.index("insert")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.edit_reset()
        widget.edit_modified(False)
        widget.mark_set("insert", insert)  # Tk clamps an index past the new end

    def _jsync(self) -> None:
        self._update_summary_hint()
        if self._jloading or self.jday is None:
            return
        self.jday.summary = self.jsummary_var.get()
        self.app.mark_dirty("journal")

    def _update_summary_hint(self) -> None:
        """An empty “Done” line shows in grey what will be built from verdicts and activity."""
        if self.jday is None or self.jsummary_var.get():
            self.jsummary_hint.hide()
            return
        auto = self.jday.auto_summary()
        self.jsummary_hint.show(_("will go into the report by itself: {auto}").format(auto=auto))

    def _jtext_modified(self, what: str) -> None:
        widget = {"plans": self.jplans, "other": self.jother, "activities": self.jactivity}.get(what) or self.jsections[what]
        if not widget.edit_modified():
            return
        widget.edit_modified(False)
        if self._jloading or self.jday is None:
            return
        text = widget.get("1.0", "end-1c")
        if what == "plans":
            self.jday.plans = text
        elif what == "other":
            self.jday.other = text
        elif what == "activities":
            self.jday.activities = parse_activity_lines(text) + self.jday.hidden_activities()  # hidden kinds stay
        else:
            self.jday.columns[what] = parse_lines(text)
            self._update_day_counts(self.jday)
        self._update_summary_hint()
        self.app.mark_dirty("journal")

    def _quick_add_activity(self, kind: str) -> None:
        if self.jday is None:
            self._add_today()
        if self.jday is None:
            return
        raw = self.jquick_var.get()
        pieces = [x for x in raw.replace(",", " ").split() if x] if current_layout().role(kind) == ROLE_MR else [raw]
        added = [x for x in pieces if self.jday.add_activity(kind, x)]
        if not added:
            self.app.set_status(_("Nothing to add: empty or already there"))
            return
        self.jquick_var.set("")
        self._load_day(self.jday)
        self.app.mark_dirty("journal")
        self.app.set_status(_("Added: {items}").format(items=", ".join(added)))

    def _update_day_counts(self, day: Day) -> None:
        if self.days_tree.exists(day.date):
            self.days_tree.item(day.date, values=(self._counts(day),))

    @staticmethod
    def _counts(day: Day) -> str:
        return " / ".join(str(n) for n in day.counts())

    def _quick_add(self, section: str) -> None:
        if self.jday is None:
            self._add_today()
        if self.jday is None:
            return
        raw = self.jquick_var.get()
        added = [x for x in parse_lines(raw.replace(",", "\n").replace(" ", "\n")) if self.jday.add(section, x)]
        if not added:
            self.app.set_status(_("Nothing to add: empty or already in this section"))
            return
        self.jquick_var.set("")
        self._load_day(self.jday)
        self._update_day_counts(self.jday)
        self.app.mark_dirty("journal")
        self.app.set_status(_("Added: {items}").format(items=", ".join(added)))

    # ---- report

    def report_dialog(self) -> None:
        if self.vault is None:
            return
        if self.jday is None:
            self.app.show_page("journal")
        if self.jday is None:
            return
        self.app.save_now()
        text = build_report(self.jday)
        self.app.clipboard_clear()
        self.app.clipboard_append(text)
        dlg = Dialog(self.app, _("Report for {day}").format(day=fmt_day(self.jday.date)))
        box = dlg.box
        ttk.Label(box, text=_("Report copied to the clipboard"), font=(FONT, pt(13), "bold")).grid(row=0, column=0, sticky="w")
        # a long line wraps by word on screen; the copied text keeps its lines as they are
        view = theme.RoundText(box, self.app, outer=BG, width=78, height=min(32, text.count("\n") + 3), font=(MONO, pt(10)), wrap="word", fg=TEXT, padx=8, pady=6)
        view.insert("1.0", text)
        view.edit_reset()  # editable: a word fixed here goes out with "Copy", the day stays as it was
        view.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        try:  # tall enough for the wrapped lines too, counted at the width it got
            view.update_idletasks()
            shown = int(view.tk.call(view._w, "count", "-update", "-displaylines", "1.0", "end-1c")) + 1
            view.configure(height=min(32, shown + 1))
        except (tk.TclError, ValueError):
            pass
        row = ttk.Frame(box)
        row.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(row, text=_("Close"), command=dlg.destroy).pack(side="right")

        def copy_again():
            self.app.clipboard_clear()
            self.app.clipboard_append(view.get("1.0", "end-1c"))
            self.app.set_status(_("Report copied to the clipboard"))

        ttk.Button(row, text=_("Copy"), style="Primary.TButton", command=copy_again).pack(side="right", padx=(0, 8))
        dlg.keys()
        self.app.set_status(_("Report copied to the clipboard"))


