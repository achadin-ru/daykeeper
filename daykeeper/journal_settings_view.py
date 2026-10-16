"""The "Journal" section of the settings window: a ready-made layout for the user's work
(tester, developer, analyst or manager, plain diary), the columns of a day, the kinds of
activity and the report lines. Edited on a copy; "Save" keeps it in settings.json and
rebuilds the Journal tab. The model is `journal_layout`; this module only lays out controls."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace

from . import journal_layout as jl
from .dialogs import ask_yes_no, show_error
from .i18n import _
from .popup import Dropdown
from .timers import Debounce
from .widgets import Tooltip, help_icon, divider, scroll_area

WRAP = 520


class JournalSection:
    def __init__(self, win, body: ttk.Frame):
        self.win, self.app = win, win.app
        self.layout = jl.copy(jl.current())
        self.fresh: set[int] = set()  # id() of columns and kinds added here: their word follows the label
        self._foot(body)  # first, so it keeps the bottom while the rest scrolls
        self.inner = scroll_area(body)
        win.set_head(self.inner, _("Journal"), _(
            "What a day of your journal is made of. Start from a ready-made layout, then rename, "
            "hide or add columns and kinds of activity. Items already written are never deleted."))
        self._presets()
        self.columns_box = self._list_group(_("Columns of a day"), _(
            "Lists of the day, side by side on the Journal tab: issue keys or any short items, one per line. "
            "The report prints each as a heading with its items."), _("+ Add column"), self._add_column)
        self.kinds_box = self._list_group(_("Kinds of activity"), _(
            "Lines of the Activity field, like “MR reviews: !42, !43”. The role says how a kind goes into the report."),
            _("+ Add kind"), self._add_kind)
        self._report()
        self._paint()

    # ---- ready-made

    def _presets(self) -> None:
        group = self.win.set_group(self.inner, _("Ready-made layouts"), _(
            "Pick the one closest to your work and press “Use it”: the columns and kinds below are replaced. "
            "Nothing is saved until “Save”."))
        self.preset_var = tk.StringVar(value=self.layout.preset if self.layout.preset in jl.PRESETS else "")
        for pid, title in jl.PRESETS.items():
            row = ttk.Frame(group, style="Card.TFrame")
            row.pack(anchor="w", fill="x", pady=(2, 0))
            ttk.Radiobutton(row, text=_(title), value=pid, variable=self.preset_var, style="Card.TRadiobutton").pack(side="left")
            ttk.Label(group, text=_(jl.PRESET_HINTS[pid]), style="CardMuted.TLabel", wraplength=WRAP - 26, justify="left").pack(anchor="w", padx=(26, 0))
        use = ttk.Button(group, text=_("Use it"), style="Small.TButton", command=self._use_preset)
        use.pack(anchor="w", pady=(10, 0))
        Tooltip(use, _("Replaces the columns and kinds below with the chosen layout. Your own changes to them are lost; written items stay."))

    def _use_preset(self) -> None:
        pid = self.preset_var.get()
        if not pid:
            return
        if not ask_yes_no(self.win, _("Replace the columns and kinds with the “{name}” layout?").format(name=_(jl.PRESETS[pid]))):
            return
        keep = (self.layout.mr_short,)
        self.layout = jl.preset(pid)
        self.layout.mr_short = keep[0]
        self.fresh.clear()
        self._paint()

    # ---- lists

    def _list_group(self, title: str, help_text: str, add_text: str, add) -> ttk.Frame:
        group = self.win.set_group(self.inner, title, help_text)
        box = ttk.Frame(group, style="Card.TFrame")
        box.pack(fill="x")
        ttk.Button(group, text=add_text, style="Small.TButton", command=add).pack(anchor="w", pady=(10, 0))
        return box

    PHRASE_TIP = "A word for the first line of the report when there are items, e.g. tested tasks or code review. Empty — nothing."
    ROLE_TIP = "MR numbers — !42 and links on one line; test cases — one shared “New tests” line; a list — any items on one line; notes — each goes into line 1 as it is."

    def _paint(self) -> None:
        for box, items, is_kind in ((self.columns_box, self.layout.columns, False), (self.kinds_box, self.layout.kinds, True)):
            for child in box.winfo_children():
                child.destroy()
            box.columnconfigure(2, weight=1)  # the line-1 word takes the free width; the buttons stay at the right edge
            # the name fits the longest name of the list, so a default name is never cut
            box.name_chars = max(12, min(32, max((len(_(x.label)) for x in items), default=0) + 2))
            box.phrase_chars = 10  # the least it shrinks to; it stretches over the rest
            box.heads = [self._caption(box, _("Name"), _("How the column or kind is called on the Journal tab and in the report.")),
                         self._caption(box, _("In line 1"), _(self.PHRASE_TIP))]
            if is_kind:
                box.heads.append(self._caption(box, _("Role"), _(self.ROLE_TIP)))
            box.rows, box.mode = [], None
            for i, item in enumerate(items):
                if i:
                    divider(box).grid(row=i * 3 + 1, column=0, columnspan=6, sticky="ew", pady=6)
                box.rows.append(self._row(box, items, i, item, is_kind))
            if not items:
                ttk.Label(box, text=_("None: press the button below to add one."), style="CardMuted.TLabel").grid(row=0, column=0, columnspan=6, sticky="w")
            if not hasattr(box, "relayout"):
                box.relayout = Debounce(box, 60, lambda b=box: self._fit(b))
                box.bind("<Configure>", lambda _e, b=box: b.relayout.touch(), add="+")
            box.needs = {}  # the width each layout takes, measured once per paint
            if box.rows:
                self._place(box, self.WIDE)
        # one layout pass measures the table of both lists; the narrower layouts are measured
        # only when a list does not fit as a table (each pass lays out the whole window)
        self.columns_box.update_idletasks()
        for box in (self.columns_box, self.kinds_box):
            if box.rows:
                box.needs[self.WIDE] = box.winfo_reqwidth()
                self._fit(box)
        self.mr_var.set(self.layout.mr_short)
        self.plans_var.set(self.layout.show_plans)
        self.other_var.set(self.layout.show_other)

    @staticmethod
    def _caption(parent, text: str, tip: str) -> ttk.Frame:
        head = ttk.Frame(parent, style="Card.TFrame")
        ttk.Label(head, text=text, style="CardMuted.TLabel").pack(side="left")
        help_icon(head, tip).pack(side="left", padx=(4, 0))
        return head

    WIDE, TWO, STACK = "wide", "two", "stack"

    @staticmethod
    def _place(box, mode: str) -> None:
        """Wide: a table — the captions once on top, a row per line. Two: the line-1 word
        and the role side by side under the name, each with its caption. Stack: the same
        one under the other, for a window too narrow even for that."""
        wide = mode == JournalSection.WIDE
        for k, head in enumerate(box.heads):
            if wide and box.rows:
                head.grid(row=0, column=k + 1, sticky="w", padx=(0, 10), pady=(0, 6))
            else:
                head.grid_remove()
        for row in box.rows:
            if wide:
                row.more.grid_remove()
                for cap in row.caps:
                    cap.grid_remove()
                for k, w in enumerate(row.fields):
                    w.grid(in_=box, row=row.r, column=k + 2, sticky="ew" if k == 0 else "w", padx=(0, 10), pady=0)
                continue
            row.more.grid(row=row.r + 1, column=1, columnspan=5, sticky="ew", pady=(8, 0))
            row.more.columnconfigure(1, weight=1)  # the line-1 word stretches here too
            for k, (cap, w) in enumerate(zip(row.caps, row.fields, strict=True)):
                if mode == JournalSection.TWO:
                    cap.grid(in_=row.more, row=0, column=k * 2, sticky="w", padx=(0 if k == 0 else 24, 8))
                    w.grid(in_=row.more, row=0, column=k * 2 + 1, sticky="ew" if k == 0 else "w")
                else:
                    cap.grid(in_=row.more, row=k, column=0, sticky="w", padx=(0, 8), pady=2)
                    w.grid(in_=row.more, row=k, column=1, sticky="ew" if k == 0 else "w", pady=2)
                cap.lift(row.more)
                w.lift(row.more)
        box.mode = mode

    def _fit(self, box) -> None:
        if not box.winfo_exists() or not box.rows:
            return
        width = box.winfo_width()
        mode = self.STACK
        for m in (self.WIDE, self.TWO):
            if m not in box.needs:
                self._place(box, m)
                box.update_idletasks()
                box.needs[m] = box.winfo_reqwidth()
            if width <= 1 or width >= box.needs[m]:
                mode = m
                break
        if mode != box.mode:
            self._place(box, mode)

    def _row(self, box, items: list, index: int, item, is_kind: bool):
        r = index * 3 + 2
        shown = tk.BooleanVar(value=not item.hidden)
        check = ttk.Checkbutton(box, variable=shown, style="Card.TCheckbutton", command=lambda: setattr(item, "hidden", not shown.get()))
        check.grid(row=r, column=0, sticky="w", padx=(0, 4))
        Tooltip(check, _("Shown / hidden. A hidden one keeps its items: show it again and they are back."))

        label = tk.StringVar(value=_(item.label))
        entry = ttk.Entry(box, textvariable=label, width=box.name_chars)
        entry.grid(row=r, column=1, sticky="w", padx=(0, 10))

        def on_label(*_a) -> None:
            text = label.get().strip()
            if text and text != _(item.label):
                item.label = text
                if id(item) in self.fresh:
                    self._rename(item)

        label.trace_add("write", on_label)
        Tooltip(entry, _("How it is called on the Journal tab and in the report. Command word: {word}").format(word=item.word))

        buttons = ttk.Frame(box, style="Card.TFrame")
        buttons.grid(row=r, column=5, sticky="e")
        up = ttk.Button(buttons, text="▲", style="Icon.TButton", command=lambda: self._move(items, index, -1))
        up.pack(side="left")
        down = ttk.Button(buttons, text="▼", style="Icon.TButton", command=lambda: self._move(items, index, +1))
        down.pack(side="left")
        rm = ttk.Button(buttons, text="✕", style="Icon.TButton", command=lambda: self._remove(items, index))
        rm.pack(side="left", padx=(2, 0))
        Tooltip(rm, _("Remove. Items already written stay in the vault."))
        if index == 0:
            up.state(["disabled"])
        if index == len(items) - 1:
            down.state(["disabled"])

        more = ttk.Frame(box, style="Card.TFrame")  # the narrow layout's second line
        caps = [self._caption(box, _("In line 1"), _(self.PHRASE_TIP))]
        phrase = tk.StringVar(value=_(item.phrase) if item.phrase else "")
        fields = [ttk.Entry(box, textvariable=phrase, width=box.phrase_chars)]
        phrase.trace_add("write", lambda *_a: setattr(item, "phrase", phrase.get().strip()))
        if is_kind:
            caps.append(self._caption(box, _("Role"), _(self.ROLE_TIP)))
            names = [_(jl.ROLE_LABELS[x]) for x in jl.ROLES]
            role = tk.StringVar(value=_(jl.ROLE_LABELS[item.role]))
            fields.append(Dropdown(box, textvariable=role, values=names, width=14))
            role.trace_add("write", lambda *_a: setattr(item, "role", jl.ROLES[names.index(role.get())]))
        return SimpleNamespace(r=r, more=more, caps=caps, fields=fields)

    def _rename(self, item) -> None:
        """A column or kind added here takes its word from the label until it is saved."""
        taken = jl.taken_words(self.layout) - {item.id, item.word}
        item.id = item.command = jl.new_id(item.label, taken)

    def _move(self, items: list, index: int, step: int) -> None:
        j = index + step
        if 0 <= j < len(items):
            items[index], items[j] = items[j], items[index]
            self._paint()

    def _remove(self, items: list, index: int) -> None:
        if not ask_yes_no(self.win, _("Remove “{name}”? Items already written stay in the vault and come back if you add it again with the same command word.").format(name=_(items[index].label))):
            return
        self.fresh.discard(id(items[index]))
        del items[index]
        self._paint()

    def _add_column(self) -> None:
        label = _("New column")
        word = jl.new_id(label, jl.taken_words(self.layout))
        col = jl.Column(word, label, command=word)
        self.layout.columns.append(col)
        self.fresh.add(id(col))
        self._paint()

    def _add_kind(self) -> None:
        label = _("New kind")
        word = jl.new_id(label, jl.taken_words(self.layout))
        kind = jl.Kind(word, label, command=word)
        self.layout.kinds.append(kind)
        self.fresh.add(id(kind))
        self._paint()

    # ---- report

    def _report(self) -> None:
        group = self.win.set_group(self.inner, _("Report"), _("What “Build report” puts together besides the columns and the activity."))
        self.mr_var = tk.BooleanVar(value=self.layout.mr_short)
        self.plans_var = tk.BooleanVar(value=self.layout.show_plans)
        self.other_var = tk.BooleanVar(value=self.layout.show_other)
        for text, var, attr, tip in (
            (_("MR numbers short: !42, repo!17"), self.mr_var, "mr_short", _("Off — every MR as a full link.")),
            (_("The “Plans” line"), self.plans_var, "show_plans", _("“2. Plans” in the day editor and the report.")),
            (_("The “Out of office / other” line"), self.other_var, "show_other", _("The last numbered line: time off, and anything else worth a line.")),
        ):
            box = ttk.Checkbutton(group, text=text, variable=var, style="Card.TCheckbutton", command=lambda a=attr, v=var: setattr(self.layout, a, v.get()))
            box.pack(anchor="w", pady=2)
            Tooltip(box, tip)

    # ---- save

    def _foot(self, body: ttk.Frame) -> None:
        foot = ttk.Frame(body)
        foot.pack(side="bottom", fill="x", pady=(10, 0))
        saved = self.win.set_saved(foot, _("Saved"))
        save = ttk.Button(foot, text=_("Save"), style="Primary.TButton", command=lambda: self._save(saved))
        save.pack(side="left")
        Tooltip(save, _("The Journal tab is rebuilt at once; the console commands and the Claude Code hook use the new words from their next run."))
        saved.pack(side="left", padx=(12, 0))

    def _save(self, saved) -> None:
        if not self.layout.visible_columns() and not self.layout.visible_kinds():
            show_error(self.win, _("Keep at least one column or kind of activity shown."))
            return
        if self.layout.preset in jl.PRESETS and self.layout.to_settings() != jl.preset(self.layout.preset).to_settings():
            self.layout.preset = "custom"
        if not self.app.apply_journal_layout(jl.copy(self.layout)):
            show_error(self.win, _("Could not write settings.json"))
            return
        self.fresh.clear()
        self.app.set_status(_("Journal layout saved"))
        saved.show()
