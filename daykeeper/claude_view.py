"""The "Claude Code" section of the settings window: connect the journal hook, choose what
counts as an event, own rules, folders, a check box, the recent triggers, and the button
that teaches Claude to keep the journal (a block in CLAUDE.md).

The logic lives in `claudecode` (files) and `hookrules` (matching); this module only lays
out the controls."""

from __future__ import annotations

import threading
from types import SimpleNamespace
import tkinter as tk
from tkinter import filedialog, ttk

from . import claudecode as cc
from . import palette, theme
from .cli import log_path, read_log
from .dialogs import show_error
from .hookrules import (
    PRESETS, TAKE_LABELS, TAKE_TEXT, TAKES, WHERE_LABELS, WHERES,
    HookConfig, Rule, match, test_payload,
)
from .i18n import _, language
from .journal import links as current_links
from .journal_layout import current as current_layout
from .popup import Dropdown
from .widgets import Tooltip, help_icon, divider, scroll_area

WRAP = 520


def refresh_helper_in_background() -> None:
    """At start: a connected built app puts its own helper build in place (after an update)."""
    def work() -> None:
        try:
            if any(cc.state(cc.settings_file(agent=a)) != cc.OFF for a in cc.AGENTS):
                cc.install_helper()
        except OSError:
            pass

    threading.Thread(target=work, daemon=True).start()


def _text_box(parent, height: int, value: str = "") -> tk.Text:
    box = tk.Text(parent, height=height, width=1, font=(palette.MONO, palette.pt(9)), bg=palette.BG, fg=palette.TEXT, relief="flat", bd=0,
                  wrap="word", insertbackground=palette.TEXT, padx=8, pady=6, highlightthickness=1,
                  highlightbackground=palette.BORDER, highlightcolor=palette.ACCENT)
    if value:
        box.insert("1.0", value)
    return box


def _lines(box: tk.Text) -> list[str]:
    return [line.strip() for line in box.get("1.0", "end").splitlines() if line.strip()]


def _titled(parent, text: str, help_text: str, style: str = "Card.TLabel") -> ttk.Frame:
    """A label with an ⓘ beside it that explains the field on hover."""
    row = ttk.Frame(parent, style="Card.TFrame")
    ttk.Label(row, text=text, style=style).pack(side="left")
    help_icon(row, help_text).pack(side="left", padx=(6, 0))
    return row


def status_words() -> list[str]:
    """The command words of the journal layout: what a rule can write."""
    return [w for w, _l in current_layout().statuses()]


def status_label(word: str) -> str:
    """A command word as the journal names it (“pass” → “Tested successfully”)."""
    layout = current_layout()
    resolved = layout.resolve(word)
    if resolved is None:
        return word
    item = layout.column(resolved[1]) if resolved[0] == "column" else layout.kind(resolved[1])
    return _(item.label)


def describe(rule: Rule) -> str:
    """The rule in one plain sentence: the tooltip in the list and the line under the form."""
    what = _("“{text}”").format(text=rule.text) if rule.take == TAKE_TEXT else _(TAKE_LABELS[rule.take])
    text = _("Looks for “{pattern}” {where} and writes to “{kind}”: {what}.").format(
        pattern=rule.pattern or "…", where=_(WHERE_LABELS[rule.where]), kind=status_label(rule.status), what=what)
    if rule.also:
        text += " " + _("Only when the answer has “{word}”.").format(word=rule.also)
    if rule.regex:
        text += " " + _("The pattern is a regular expression.")
    return text


def _event_text(status: str, item: str, rule: str) -> str:
    title = next((_(p.title) for p in PRESETS if p.id == rule), rule)
    return f"{status_label(status)}: {item}  ·  {title}"


class ClaudeSection:
    """Builds the section into `body` of the settings window `win`."""

    def __init__(self, win, body: ttk.Frame):
        self.win, self.app = win, win.app
        self.parts = cc.helper_command(self.app.vault_path.parent)  # the helper, with --data for a vault elsewhere
        self.command = cc.hook_command(self.parts)
        self.inner = scroll_area(body)
        win.set_head(self.inner, _("Claude Code and Codex"), _(
            "After each command Claude Code or Codex runs, Daykeeper looks at it and writes what it recognises "
            "into the journal: MRs, reviews, new bugs, verdicts. Nothing else is sent anywhere."))
        self._steps()
        self._connection()
        self._presets()
        self._rules()
        self._folders()
        self._check()
        self._recent()
        self._teach()

    def config(self) -> HookConfig:
        return HookConfig.from_settings(self.app.session.settings())

    def save_config(self, config: HookConfig) -> bool:
        if not self.app.session.write_settings(config.to_settings()):
            show_error(self.win, _("Could not write settings.json"))
            return False
        return True

    def _muted(self, parent, text: str, **pack) -> ttk.Label:
        label = ttk.Label(parent, text=text, style="CardMuted.TLabel", wraplength=WRAP, justify="left")
        label.pack(anchor="w", **pack)
        return label

    def _steps(self) -> None:
        group = self.win.set_group(self.inner, _("How it works"))
        for n, text in enumerate((
            _("Press “Connect” next to your assistant — Claude Code, Codex or both: from now on it shows each command to Daykeeper."),
            _("Choose what counts as an event below; add your own rules if something is missing."),
            _("At the bottom, teach the assistant to keep the journal: it will also write what no command shows — verdicts, test cases, notes."),
            _("Open the Journal tab: the items arrive within a minute."),
        ), start=1):
            ttk.Label(group, text=f"{n}.  {text}", style="Card.TLabel", wraplength=WRAP, justify="left").pack(anchor="w", pady=1)

    # ---- connection

    def _connection(self) -> None:
        group = self.win.set_group(self.inner, _("Connection"), _(
            "Each assistant is connected on its own; the rules, folders and recent triggers below are shared."))
        if not cc.helper_available():
            ttk.Label(group, text=_("This build has no console helper, so the hook cannot be connected from here yet."), style="CardError.TLabel", wraplength=WRAP, justify="left").pack(anchor="w", pady=(0, 6))
        self.rows: dict[str, SimpleNamespace] = {}
        for i, agent in enumerate(cc.AGENTS):
            if i:
                divider(group).pack(fill="x", pady=12)
            self._agent_row(group, agent)

    def _agent_row(self, group, agent: cc.Agent) -> None:
        head = ttk.Frame(group, style="Card.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text=agent.name, style="CardTitle.TLabel").pack(side="left")
        state = ttk.Label(head, text="", style="Card.TLabel")
        state.pack(side="left", padx=(12, 0))
        Tooltip(state, _("Connected — the assistant's commands are checked. “Old path” — it calls an older copy of the helper: press “Connect again”."))
        note = _("{name} reads its hooks when a session starts: restart open sessions after connecting.").format(name=agent.name)
        if agent.trust_note:
            note += " " + _(agent.trust_note)
        self._muted(group, note, pady=(4, 0))
        row = ttk.Frame(group, style="Card.TFrame")
        row.pack(anchor="w", pady=(10, 0))
        connect = ttk.Button(row, text=_("Connect"), style="Primary.TButton", command=lambda: self._connect(agent))
        connect.pack(side="left")
        disconnect = ttk.Button(row, text=_("Disconnect"), style="Small.TButton", command=lambda: self._disconnect(agent))
        disconnect.pack(side="left", padx=(10, 0))
        Tooltip(connect, _("Puts the helper in place and adds Daykeeper to {name}'s hooks file. Nothing else in that file changes; a copy of it is kept.").format(name=agent.name))
        Tooltip(disconnect, _("Removes Daykeeper from {name}'s hooks file. The journal and your rules stay.").format(name=agent.name))
        error = ttk.Label(group, text="", style="CardError.TLabel", wraplength=WRAP, justify="left")
        error.pack(anchor="w", pady=(6, 0))
        path = ttk.Label(group, text=str(cc.settings_file(agent=agent)), style="CardMono.TLabel")
        path.pack(anchor="w")
        Tooltip(path, _("{name}'s hooks file. Daykeeper writes only its own entry there.").format(name=agent.name))
        self.rows[agent.id] = SimpleNamespace(state=state, connect=connect, disconnect=disconnect, error=error)
        self._paint_state(agent)

    def _paint_state(self, agent: cc.Agent) -> None:
        w = self.rows[agent.id]
        value = cc.state(cc.settings_file(agent=agent), self.command)
        w.state.config(text=("●  " if value == cc.ON else "○  ") + cc.state_text(value), style="CardGood.TLabel" if value == cc.ON else "Card.TLabel")
        w.connect.config(text=_("Connect again") if value == cc.STALE else _("Connect"))
        w.connect.state(["disabled"] if value == cc.ON or not cc.helper_available() else ["!disabled"])
        w.disconnect.state(["disabled"] if value == cc.OFF else ["!disabled"])

    def _broken_text(self, agent: cc.Agent) -> str:
        return _("{file} of {name} has an error, so it was left as it is. Fix the file and try again.").format(file=agent.hooks_name, name=agent.name)

    def _connect(self, agent: cc.Agent) -> None:
        w = self.rows[agent.id]
        w.error.config(text="")
        if not cc.install_helper():
            w.error.config(text=_("Could not put the helper into {path}").format(path=cc.helper_dir()))
            return
        try:
            cc.connect(cc.settings_file(agent=agent), self.command)
        except cc.SettingsBroken:
            w.error.config(text=self._broken_text(agent))
            return
        except OSError as exc:
            w.error.config(text=_("Could not write {path}: {error}").format(path=cc.settings_file(agent=agent), error=exc.strerror or exc))
            return
        self.app.set_status(_("{name} connected").format(name=agent.name))
        self._paint_state(agent)

    def _disconnect(self, agent: cc.Agent) -> None:
        w = self.rows[agent.id]
        w.error.config(text="")
        try:
            cc.disconnect(cc.settings_file(agent=agent))
        except (cc.SettingsBroken, OSError):
            w.error.config(text=self._broken_text(agent))
            return
        self.app.set_status(_("{name} disconnected").format(name=agent.name))
        self._paint_state(agent)

    # ---- ready-made sets

    def _presets(self) -> None:
        group = self.win.set_group(self.inner, _("What counts as an event"))
        config = self.config()
        for preset in PRESETS:
            var = tk.BooleanVar(value=config.presets.get(preset.id, True))
            row = ttk.Frame(group, style="Card.TFrame")
            row.pack(anchor="w", pady=(6, 0))
            box = ttk.Checkbutton(row, text=_(preset.title), variable=var, style="Card.TCheckbutton",
                                  command=lambda p=preset.id, v=var: self._toggle_preset(p, v.get()))
            box.pack(side="left")
            help_icon(row, _(preset.example)).pack(side="left", padx=(6, 0))
            Tooltip(box, _(preset.example))
            ttk.Label(group, text=_(preset.hint), style="CardMuted.TLabel", wraplength=WRAP - 26, justify="left").pack(anchor="w", padx=(26, 0))

    def _toggle_preset(self, preset: str, on: bool) -> None:
        config = self.config()
        config.presets[preset] = on
        self.save_config(config)

    # ---- own rules

    def _rules(self) -> None:
        group = self.win.set_group(self.inner, _("Own rules"))
        self._muted(group, _("For what the sets above do not know: “if the command has … — write …”."))
        self._muted(group, _("Example: when Claude runs the nightly script nightly.sh, write “nightly run” to Miscellaneous."), pady=(2, 0))
        self.rules_box = ttk.Frame(group, style="Card.TFrame")
        self.rules_box.pack(fill="x", pady=(8, 0))
        add = ttk.Button(group, text=_("+ Add rule"), style="Small.TButton", command=lambda: self._edit_rule(None))
        add.pack(anchor="w", pady=(8, 0))
        Tooltip(add, _("Opens a form; the “Fill with an example” button there shows what a rule looks like."))
        self._paint_rules()

    def _paint_rules(self) -> None:
        for child in self.rules_box.winfo_children():
            child.destroy()
        rules = self.config().rules
        if not rules:
            ttk.Label(self.rules_box, text=_("No own rules yet."), style="CardMuted.TLabel").pack(anchor="w")
        for i, rule in enumerate(rules):
            row = ttk.Frame(self.rules_box, style="Card.TFrame")
            row.pack(fill="x", pady=1)
            on = tk.BooleanVar(value=rule.enabled)
            switch = ttk.Checkbutton(row, variable=on, style="Card.TCheckbutton", command=lambda i=i, v=on: self._rule_on(i, v.get()))
            switch.pack(side="left")
            Tooltip(switch, _("On / off without deleting"))
            summary = f"{rule.name or rule.pattern}  →  {status_label(rule.status)}"
            label = ttk.Label(row, text=summary, style="Card.TLabel")
            label.pack(side="left", padx=(2, 0))
            Tooltip(label, describe(rule))
            rm = ttk.Button(row, text="✕", style="Icon.TButton", command=lambda i=i: self._delete_rule(i))
            rm.pack(side="right")
            Tooltip(rm, _("Delete"))
            edit = ttk.Button(row, text="✎", style="Icon.TButton", command=lambda i=i: self._edit_rule(i))
            edit.pack(side="right", padx=(0, 2))
            Tooltip(edit, _("Edit"))

    def _rule_on(self, index: int, on: bool) -> None:
        config = self.config()
        config.rules[index].enabled = on
        self.save_config(config)

    def _delete_rule(self, index: int) -> None:
        config = self.config()
        del config.rules[index]
        if self.save_config(config):
            self._paint_rules()

    def _edit_rule(self, index: int | None) -> None:
        config = self.config()
        RuleDialog(self.win, config.rules[index] if index is not None else Rule(), lambda rule: self._rule_saved(index, rule))

    def _rule_saved(self, index: int | None, rule: Rule) -> None:
        config = self.config()
        if index is None:
            config.rules.append(rule)
        else:
            config.rules[index] = rule
        if self.save_config(config):
            self._paint_rules()

    # ---- folders

    def _folders(self) -> None:
        group = self.win.set_group(self.inner, _("Where it works"))
        config = self.config()
        self.only_var = tk.BooleanVar(value=bool(config.only_folders))
        every = ttk.Radiobutton(group, text=_("In every project"), variable=self.only_var, value=False, style="Card.TRadiobutton", command=self._paint_only)
        every.pack(anchor="w", pady=2)
        Tooltip(every, _("The hook works wherever Claude is started."))
        only = ttk.Radiobutton(group, text=_("Only in these folders"), variable=self.only_var, value=True, style="Card.TRadiobutton", command=self._paint_only)
        only.pack(anchor="w", pady=2)
        Tooltip(only, _("The hook writes only when Claude works inside one of the folders listed here, e.g. E:\\work."))
        self.only_row = ttk.Frame(group, style="Card.TFrame")
        self.only_box = self._folder_box(self.only_row, config.only_folders)
        self.only_anchor = _titled(group, _("Never in these folders"), _("Personal projects and experiments: nothing Claude does inside these folders reaches the journal."))
        self.only_anchor.pack(anchor="w", pady=(10, 2))
        skip_row = ttk.Frame(group, style="Card.TFrame")
        skip_row.pack(fill="x")
        self.skip_box = self._folder_box(skip_row, config.skip_folders)
        self._muted(group, _("One folder per line; the folders inside count too. Items written in a repository whose server is not the tracker's or GitLab's from Report links are dropped anyway."), pady=(6, 0))
        foot = ttk.Frame(group, style="Card.TFrame")
        foot.pack(anchor="w", pady=(10, 0))
        saved = self.win.set_saved(foot, _("Saved"))
        save = ttk.Button(foot, text=_("Save folders"), style="Small.TButton", command=lambda: self._save_folders(saved))
        save.pack(side="left")
        Tooltip(save, _("The folder lists apply from the next command Claude runs."))
        saved.pack(side="left", padx=(12, 0))
        self._paint_only()

    def _folder_box(self, parent, folders: list[str]) -> tk.Text:
        box = _text_box(parent, 3, "\n".join(folders))
        box.pack(side="left", fill="x", expand=True)

        def add() -> None:
            folder = filedialog.askdirectory(parent=self.win)
            if folder:
                text = box.get("1.0", "end").rstrip("\n")
                box.delete("1.0", "end")
                box.insert("1.0", (text + "\n" if text else "") + folder)

        pick = ttk.Button(parent, text=_("Add folder…"), style="Small.TButton", command=add)
        pick.pack(side="left", anchor="n", padx=(8, 0))
        Tooltip(pick, _("Pick a folder: it is added as a new line. You can also type or paste paths."))
        return box

    def _paint_only(self) -> None:
        if self.only_var.get():
            self.only_row.pack(fill="x", pady=(2, 0), before=self.only_anchor)
        else:
            self.only_row.pack_forget()

    def _save_folders(self, saved) -> None:
        config = self.config()
        config.only_folders = _lines(self.only_box) if self.only_var.get() else []
        config.skip_folders = _lines(self.skip_box)
        if self.only_var.get() and not config.only_folders:
            show_error(self.win, _("Add at least one folder, or choose “In every project”."))
            return
        if self.save_config(config):
            saved.show()

    # ---- check

    def _check(self) -> None:
        group = self.win.set_group(self.inner, _("Test a command"))
        self._muted(group, _("Paste a command and what it answered: you will see what would go into the journal and which rule finds it."))
        _titled(group, _("Command"), _("What Claude ran, e.g. glab mr merge 44")).pack(anchor="w", pady=(8, 2))
        command = _text_box(group, 2)
        command.pack(fill="x")
        _titled(group, _("Answer"), _("What the command printed back, e.g. ✓ Merged. Some rules need it: an MR link appears only in the answer.")).pack(anchor="w", pady=(8, 2))
        answer = _text_box(group, 2)
        answer.pack(fill="x")
        result = ttk.Label(group, text="", style="Card.TLabel", wraplength=WRAP, justify="left")

        def run() -> None:
            hits = match(test_payload(command.get("1.0", "end-1c"), answer.get("1.0", "end-1c")), self.config(), current_links())
            result.config(text="\n".join("✓ " + _event_text(*h) for h in hits) if hits else _("Nothing would be written."))

        check = ttk.Button(group, text=_("Check it"), style="Small.TButton", command=run)
        check.pack(anchor="w", pady=(8, 0))
        Tooltip(check, _("Nothing is written: this only shows what would be."))
        result.pack(anchor="w", pady=(6, 0))

    # ---- recent

    def _recent(self) -> None:
        group = self.win.set_group(self.inner, _("Recent triggers"))
        self._muted(group, _("The last ten times the hook wrote something: the time, the journal line and the rule that found it."), pady=(0, 6))
        box = ttk.Frame(group, style="Card.TFrame")
        box.pack(fill="x")

        def paint() -> None:
            for child in box.winfo_children():
                child.destroy()
            rows = read_log(log_path(), 10)
            if not rows:
                ttk.Label(box, text=_("Nothing yet: the hook writes here when it recognises something."), style="CardMuted.TLabel", wraplength=WRAP).pack(anchor="w")
            for row in rows:
                if "error" in row:
                    text, style = f"{row.get('ts', '')}  {_('error')}: {row['error']}", "CardError.TLabel"
                elif row.get("slow_rules"):
                    text, style = f"{row.get('ts', '')[5:16]}  " + "; ".join(_("rule “{name}” ran too long on that answer and was skipped").format(name=n) for n in row["slow_rules"]), "CardError.TLabel"
                else:
                    events = "; ".join(_event_text(*e) for e in row.get("events", []) if len(e) == 3)
                    skipped = f"  ({_('folder skipped')})" if row.get("skipped") == "folder" else ""
                    text, style = f"{row.get('ts', '')[5:16]}  {events}{skipped}", "CardMuted.TLabel" if skipped else "Card.TLabel"
                ttk.Label(box, text=text, style=style, wraplength=WRAP, justify="left").pack(anchor="w", pady=1)

        ttk.Button(group, text=_("Refresh"), style="Small.TButton", command=paint).pack(anchor="w", pady=(8, 0))
        paint()

    # ---- teach

    def _teach(self) -> None:
        group = self.win.set_group(self.inner, _("Teach the assistant"), _(
            "Each assistant has its own file of personal instructions; it reads it at the start of every session, in every project."))
        self._muted(group, _("The hook sees only commands. Verdicts, test cases and other work the assistant writes itself when its instructions say so: this puts a block about the journal into its personal instructions."))
        self.teach_states: dict[str, ttk.Label] = {}
        for agent in cc.AGENTS:
            row = ttk.Frame(group, style="Card.TFrame")
            row.pack(fill="x", pady=(12, 0))
            ttk.Label(row, text=agent.name, style="CardTitle.TLabel").pack(side="left")
            state = ttk.Label(row, text="", style="Card.TLabel")
            state.pack(side="left", padx=(12, 0))
            self.teach_states[agent.id] = state
            path = ttk.Label(group, text=str(cc.instructions_file(agent=agent)), style="CardMono.TLabel")
            path.pack(anchor="w")
            Tooltip(path, _("{name}'s personal instructions: it reads them at the start of every session, in every project.").format(name=agent.name))
            teach = ttk.Button(group, text=_("Teach {name} to keep the journal…").format(name=agent.name), style="Small.TButton",
                               command=lambda a=agent: TeachDialog(self.win, self._paint_teach, self.parts, a))
            teach.pack(anchor="w", pady=(6, 0))
            Tooltip(teach, _("Opens the text first: you choose what the assistant writes and can edit it before anything is saved."))
        self._paint_teach()

    def _paint_teach(self) -> None:
        for agent in cc.AGENTS:
            label = self.teach_states.get(agent.id)
            if label is not None and label.winfo_exists():
                on = cc.current_block(cc.instructions_file(agent=agent)) is not None
                label.config(text=("●  " if on else "○  ") + (_("The instructions are in place") if on else _("No instructions yet")),
                             style="CardGood.TLabel" if on else "Card.TLabel")


class _Dialog(tk.Toplevel):
    closes_on_lock = True  # App.close_child_windows

    def __init__(self, parent, title: str, size: str):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=palette.BG)
        self.transient(parent)
        self.geometry(size)
        self.grab_set()
        self.after(10, lambda: self.winfo_exists() and theme.caption_colour(self, palette.SIDEBAR, palette.TEXT))
        self.body = ttk.Frame(self, style="Group.TFrame", padding=(22, 18))
        self.body.pack(fill="both", expand=True, padx=14, pady=14)
        self.bind("<Escape>", lambda _e: self.destroy())


class RuleDialog(_Dialog):
    """The form of one own rule."""

    def __init__(self, parent, rule: Rule, on_save):
        super().__init__(parent, _("Rule"), "620x700")
        self.on_save = on_save
        b = self.body
        b.columnconfigure(1, weight=1)
        words = status_words()
        note = current_layout().kind(current_layout().note_kind())
        self._note_word = note.word if note else (words[-1] if words else "note")
        if rule.status not in words:
            rule.status = self._note_word if self._note_word in words else (words[0] if words else rule.status)
            words = words or [rule.status]
        self._words = words
        status_names = [status_label(w) for w in words]
        take_names = [_(TAKE_LABELS[t]) for t in TAKES]
        where_names = [_(WHERE_LABELS[w]) for w in WHERES]
        self.name = tk.StringVar(value=rule.name)
        self.pattern = tk.StringVar(value=rule.pattern)
        self.regex = tk.BooleanVar(value=rule.regex)
        self.where = tk.StringVar(value=where_names[WHERES.index(rule.where)])
        self.also = tk.StringVar(value=rule.also)
        self.status = tk.StringVar(value=status_names[words.index(rule.status)])
        self.take = tk.StringVar(value=take_names[TAKES.index(rule.take)])
        self.text = tk.StringVar(value=rule.text)
        self.enabled = rule.enabled
        self._names = (status_names, take_names, where_names)

        def row(r: int, label: str, widget, hint: str = "", help_text: str = "") -> None:
            if label:
                head = _titled(b, label, help_text) if help_text else ttk.Label(b, text=label, style="Card.TLabel")
                head.grid(row=r * 2, column=0, sticky="w", padx=(0, 14), pady=(6, 0))
            widget.grid(row=r * 2, column=1, sticky="ew", pady=(6, 0))
            if hint:
                ttk.Label(b, text=hint, style="CardMuted.TLabel", wraplength=380, justify="left").grid(row=r * 2 + 1, column=1, sticky="w")

        row(0, _("Name"), ttk.Entry(b, textvariable=self.name), _("For you: shown in the list and in Recent triggers."),
            _("Any name that tells you what the rule is for, e.g. “Nightly run”."))
        row(1, _("Look for"), ttk.Entry(b, textvariable=self.pattern), _("A piece of text, any letter case. For example: nightly.sh"),
            _("The rule fires when this text is found. Usually a script or command name, e.g. nightly.sh or make-release."))
        regex = ttk.Checkbutton(b, text=_("It is a regular expression"), variable=self.regex, style="Card.TCheckbutton")
        row(2, "", regex)
        Tooltip(regex, _("For the experienced: the text above is a pattern, e.g. triage (\\d+). The part in brackets can be written as the item. Leave it off if unsure."))
        row(3, _("Where"), Dropdown(b, textvariable=self.where, values=where_names), "",
            _("“In the command” — what Claude ran. “In the answer” — what the command printed back. “In either” — both."))
        row(4, _("And the answer has"), ttk.Entry(b, textvariable=self.also), _("Optional: a word that means success, e.g. merged."),
            _("Write only when the answer also has this word, so failed attempts are skipped. Empty — any answer."))
        row(5, _("Write as"), Dropdown(b, textvariable=self.status, values=status_names), "",
            _("Which part of the journal it goes to: a verdict column (Tested successfully, Test failed, New bug), an MR list, test cases or Miscellaneous."))
        row(6, _("Item"), Dropdown(b, textvariable=self.take, values=take_names), _("Found text that looks like a token or a key is never written."),
            _("What exactly goes into the journal line: an issue key (APP-123) found in the command or the answer, an MR number, the found text, or your own fixed text."))
        self.text_entry = ttk.Entry(b, textvariable=self.text)
        row(7, _("Text"), self.text_entry, _("What to write when the item is the fixed text, e.g. “nightly run”."),
            _("Only used when “Item” is the fixed text."))
        self.summary = ttk.Label(b, text="", style="CardMuted.TLabel", wraplength=420, justify="left")
        self.summary.grid(row=16, column=1, sticky="w", pady=(12, 0))
        for var in (self.name, self.pattern, self.regex, self.where, self.also, self.status, self.take, self.text):
            var.trace_add("write", lambda *_a: self._paint())
        self._paint()
        self.error = ttk.Label(b, text="", style="CardError.TLabel")
        self.error.grid(row=17, column=1, sticky="w", pady=(6, 0))
        foot = ttk.Frame(b, style="Card.TFrame")
        foot.grid(row=18, column=1, sticky="w", pady=(10, 0))
        ttk.Button(foot, text=_("Save"), style="Primary.TButton", command=self._save).pack(side="left")
        ttk.Button(foot, text=_("Cancel"), style="Small.TButton", command=self.destroy).pack(side="left", padx=(10, 0))
        sample = ttk.Button(foot, text=_("Fill with an example"), style="Small.TButton", command=self._example)
        sample.pack(side="left", padx=(10, 0))
        Tooltip(sample, _("Fills the form with a sample rule: “when Claude runs nightly.sh, write ‘nightly run’ to Miscellaneous”. Change it to your own."))
        self.bind("<Return>", lambda _e: self._save())
        self.update_idletasks()  # as tall as the form: a fixed height clips the buttons on a scaled screen
        self.geometry(f"{max(self.winfo_reqwidth(), self.winfo_width())}x{self.winfo_reqheight()}")

    def _rule(self) -> Rule:
        status_names, take_names, where_names = self._names
        return Rule(
            name=self.name.get().strip(), status=self._words[status_names.index(self.status.get())],
            pattern=self.pattern.get().strip(), where=WHERES[where_names.index(self.where.get())],
            regex=self.regex.get(), take=TAKES[take_names.index(self.take.get())], text=self.text.get().strip(),
            also=self.also.get().strip(), enabled=self.enabled,
        )

    def _paint(self) -> None:
        rule = self._rule()
        self.text_entry.state(["!disabled"] if rule.take == TAKE_TEXT else ["disabled"])
        self.summary.config(text=_("This rule: ") + describe(rule))

    def _example(self) -> None:
        status_names, take_names, where_names = self._names
        self.name.set(_("Nightly run"))
        self.pattern.set("nightly.sh")
        self.regex.set(False)
        self.where.set(where_names[WHERES.index("command")])
        self.also.set("")
        self.status.set(status_names[self._words.index(self._note_word)] if self._note_word in self._words else status_names[0])
        self.take.set(take_names[TAKES.index(TAKE_TEXT)])
        self.text.set(_("nightly run"))

    def _save(self) -> None:
        rule = self._rule()
        problem = rule.problem()
        if problem:
            self.error.config(text=_(problem))
            return
        self.destroy()
        self.on_save(rule)


class TeachDialog(_Dialog):
    """What goes into CLAUDE.md: which events, the note language, and the text itself."""

    def __init__(self, parent, after, parts: list[str], agent: cc.Agent = cc.CLAUDE):
        super().__init__(parent, _("Teach {name} to keep the journal").format(name=agent.name), "760x640")
        self.after_change, self.parts, self.agent = after, parts, agent
        self.file = cc.instructions_file(agent=agent)
        b = self.body
        _titled(b, _("What Claude should write"), _("Untick what you do not want in the journal: Claude will not be told about it.")).pack(anchor="w")
        boxes = ttk.Frame(b, style="Card.TFrame")
        boxes.pack(anchor="w", pady=(4, 0))
        self.picked: dict[str, tk.BooleanVar] = {}
        for i, status in enumerate(status_words()):
            var = tk.BooleanVar(value=True)
            self.picked[status] = var
            ttk.Checkbutton(boxes, text=status_label(status), variable=var, style="Card.TCheckbutton", command=self._regenerate).grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 18))
        lang_row = ttk.Frame(b, style="Card.TFrame")
        lang_row.pack(anchor="w", pady=(10, 0))
        _titled(lang_row, _("Language of notes"), _("Claude writes the “Miscellaneous” notes in this language, e.g. Russian or English.")).pack(side="left")
        self.lang = tk.StringVar(value="Russian" if language() == "ru" else "English")
        entry = ttk.Entry(lang_row, textvariable=self.lang, width=16)
        entry.pack(side="left", padx=(10, 0))
        entry.bind("<KeyRelease>", lambda _e: self._regenerate())
        _titled(b, _("The text (you can edit it before inserting)"), _("This is what Claude will read. Ticks and the language rebuild it, so edit the text last.")).pack(anchor="w", pady=(12, 2))
        self.preview = _text_box(b, 16)
        self.preview.config(wrap="none")  # a command and its comment stay on one line, as Claude will read them
        self.preview.pack(fill="both", expand=True)
        if cc.mentions_journal_elsewhere(self.file):
            ttk.Label(b, text=_("Your instructions file already speaks of the journal outside this block. Remove the older section by hand, or Claude gets two sets of rules."),
                      style="CardError.TLabel", wraplength=680, justify="left").pack(anchor="w", pady=(8, 0))
        foot = ttk.Frame(b, style="Card.TFrame")
        foot.pack(anchor="w", pady=(12, 0))
        has_block = cc.current_block(self.file) is not None
        insert = ttk.Button(foot, text=(_("Replace in {file}") if has_block else _("Insert into {file}")).format(file=self.file.name), style="Primary.TButton", command=self._insert)
        insert.pack(side="left")
        Tooltip(insert, _("Saves the text into Claude's instructions between Daykeeper's marks; the rest of the file stays. Pressed again, it replaces its own block."))
        copy = ttk.Button(foot, text=_("Copy"), style="Small.TButton", command=self._copy)
        copy.pack(side="left", padx=(10, 0))
        Tooltip(copy, _("To paste it somewhere yourself, e.g. into one project's instructions."))
        if has_block:
            remove = ttk.Button(foot, text=_("Remove from {file}").format(file=self.file.name), style="Small.TButton", command=self._remove)
            remove.pack(side="left", padx=(10, 0))
            Tooltip(remove, _("Takes Daykeeper's block out; Claude stops writing the journal itself."))
        ttk.Button(foot, text=_("Cancel"), style="Small.TButton", command=self.destroy).pack(side="left", padx=(10, 0))
        self._regenerate()

    def _regenerate(self) -> None:
        options = cc.TeachOptions(tuple(s for s, v in self.picked.items() if v.get()), self.lang.get().strip() or "English")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", cc.instructions(options, self.parts))

    def _insert(self) -> None:
        try:
            cc.insert_block(self.preview.get("1.0", "end-1c"), self.file)
        except OSError as exc:
            show_error(self, _("Could not write {path}: {error}").format(path=self.file, error=exc.strerror or exc))
            return
        self.master.app.set_status(_("{name}'s instructions updated").format(name=self.agent.name))
        self.destroy()
        self.after_change()

    def _copy(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.preview.get("1.0", "end-1c"))
        self.master.app.set_status(_("Copied"))

    def _remove(self) -> None:
        try:
            cc.remove_block(self.file)
        except OSError as exc:
            show_error(self, _("Could not write {path}: {error}").format(path=self.file, error=exc.strerror or exc))
            return
        self.destroy()
        self.after_change()
