"""The "AI assistants" section of the Qt settings window (claude_view of the Tk one): connect
the journal hook to Claude Code or Codex, choose what counts as an event, own rules,
folders, a check box, the recent triggers, and the button that teaches the assistant to
keep the journal (a block in its personal instructions).

The logic lives in `claudecode` (files) and `hookrules` (matching); this module only lays
out the controls."""

from __future__ import annotations

import threading
from functools import partial
from types import SimpleNamespace
from typing import Any

import shiboken6
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QButtonGroup, QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QRadioButton, QVBoxLayout, QWidget

from ... import claudecode as cc
from ...cli import log_path, read_log
from ...hookrules import PRESETS, TAKE_LABELS, TAKE_TEXT, TAKES, WHERE_LABELS, WHERES, HookConfig, Rule, match, test_payload
from ...i18n import _, language
from ...journal import links as current_links
from ...journal_layout import current as current_layout
from .. import theme
from ..widgets.common import divider, icon_button
from .base import Dialog, button, label, show_error


def refresh_helper_in_background() -> None:
    """At start: a connected built app puts its own helper build in place (after an update)."""
    def work() -> None:
        try:
            if any(cc.state(cc.settings_file(agent=a)) != cc.OFF for a in cc.AGENTS):
                cc.install_helper()
        except OSError:
            pass

    threading.Thread(target=work, daemon=True).start()


def status_words() -> list[str]:
    return [w for w, _l in current_layout().statuses()]


def status_label(word: str) -> str:
    layout = current_layout()
    resolved = layout.resolve(word)
    if resolved is None:
        return word
    item = layout.column(resolved[1]) if resolved[0] == "column" else layout.kind(resolved[1])
    return _(item.label) if item is not None else word


def describe(rule: Rule) -> str:
    what = _("“{text}”").format(text=rule.text) if rule.take == TAKE_TEXT else _(TAKE_LABELS[rule.take])
    text = _("Looks for “{pattern}” {where} and writes to “{kind}”: {what}.").format(pattern=rule.pattern or "…", where=_(WHERE_LABELS[rule.where]), kind=status_label(rule.status), what=what)
    if rule.also:
        text += " " + _("Only when the answer has “{word}”.").format(word=rule.also)
    if rule.regex:
        text += " " + _("The pattern is a regular expression.")
    return text


def event_text(status: str, item: str, rule: str) -> str:
    title = next((_(p.title) for p in PRESETS if p.id == rule), rule)
    return f"{status_label(status)}: {item}  ·  {title}"


def text_box(parent: QWidget, lines: int, value: str = "") -> QPlainTextEdit:
    box = QPlainTextEdit(parent)
    box.setFont(theme.font(9, mono=True))
    box.setPlainText(value)
    box.setFixedHeight(box.fontMetrics().lineSpacing() * lines + 16)
    box.setTabChangesFocus(True)
    return box


def lines_of(box: QPlainTextEdit) -> list[str]:
    return [line.strip() for line in box.toPlainText().splitlines() if line.strip()]


def titled(text: str, help_text: str, parent: QWidget) -> QHBoxLayout:
    from .settings import help_icon

    row = QHBoxLayout()
    row.addWidget(QLabel(text, parent))
    row.addWidget(help_icon(help_text, parent))
    row.addStretch(1)
    return row


class ClaudeSection(QWidget):
    def __init__(self, win: Any):
        from .settings import Section

        super().__init__()
        self.win, self.shell = win, win.shell
        self.parts = cc.helper_command(self.shell.vault_path.parent)
        self.command = cc.hook_command(self.parts)
        self.s = Section(win)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.s)
        self.s.head(_("Claude Code and Codex"), _("After each command Claude Code or Codex runs, Daykeeper looks at it and writes what it recognises into the journal: MRs, reviews, new bugs, verdicts. Nothing else is sent anywhere."))
        self._steps()
        self._connection()
        self._presets()
        self._rules()
        self._folders()
        self._check()
        self._recent()
        self._teach()
        self.s.end()

    def config(self) -> HookConfig:
        return HookConfig.from_settings(self.shell.session.settings())

    def save_config(self, config: HookConfig) -> bool:
        if not self.shell.session.write_settings(config.to_settings()):
            show_error(self.win, _("Could not write settings.json"))
            return False
        return True

    def _muted(self, g: QVBoxLayout, text: str) -> QLabel:
        lab = label(text, "muted", self.s.inner)
        lab.setWordWrap(True)
        g.addWidget(lab)
        return lab

    def _steps(self) -> None:
        g = self.s.group(_("How it works"))
        for n, text in enumerate((
            _("Press “Connect” next to your assistant — Claude Code, Codex or both: from now on it shows each command to Daykeeper."),
            _("Choose what counts as an event below; add your own rules if something is missing."),
            _("At the bottom, teach the assistant to keep the journal: it will also write what no command shows — verdicts, test cases, notes."),
            _("Open the Journal tab: the items arrive within a minute."),
        ), start=1):
            lab = QLabel(f"{n}.  {text}", self.s.inner)
            lab.setWordWrap(True)
            g.addWidget(lab)

    # ---- connection

    def _connection(self) -> None:
        g = self.s.group(_("Connection"), _("Each assistant is connected on its own; the rules, folders and recent triggers below are shared."))
        if not cc.helper_available():
            warn = label(_("This build has no console helper, so the hook cannot be connected from here yet."), "error", self.s.inner)
            warn.setWordWrap(True)
            g.addWidget(warn)
        self.rows: dict[str, SimpleNamespace] = {}
        for i, agent in enumerate(cc.AGENTS):
            if i:
                g.addWidget(divider(self.s.inner))
            self._agent_row(g, agent)

    def _agent_row(self, g: QVBoxLayout, agent: cc.Agent) -> None:
        inner = self.s.inner
        head = QHBoxLayout()
        name = QLabel(agent.name, inner)
        name.setFont(theme.font(12, bold=True))
        head.addWidget(name)
        state = QLabel("", inner)
        state.setToolTip(_("Connected — the assistant's commands are checked. “Old path” — it calls an older copy of the helper: press “Connect again”."))
        head.addWidget(state)
        head.addStretch(1)
        g.addLayout(head)
        note = _("{name} reads its hooks when a session starts: restart open sessions after connecting.").format(name=agent.name)
        if agent.trust_note:
            note += " " + _(agent.trust_note)
        self._muted(g, note)
        row = QHBoxLayout()
        connect = button(_("Connect"), "primary", inner)
        connect.setToolTip(_("Puts the helper in place and adds Daykeeper to {name}'s hooks file. Nothing else in that file changes; a copy of it is kept.").format(name=agent.name))
        connect.clicked.connect(lambda: self._connect(agent))
        disconnect = button(_("Disconnect"), "small", inner)
        disconnect.setToolTip(_("Removes Daykeeper from {name}'s hooks file. The journal and your rules stay.").format(name=agent.name))
        disconnect.clicked.connect(lambda: self._disconnect(agent))
        row.addWidget(connect)
        row.addWidget(disconnect)
        row.addStretch(1)
        g.addLayout(row)
        error = label("", "error", inner)
        error.setWordWrap(True)
        g.addWidget(error)
        path = QLabel(str(cc.settings_file(agent=agent)), inner)
        path.setFont(theme.font(9, mono=True))
        path.setToolTip(_("{name}'s hooks file. Daykeeper writes only its own entry there.").format(name=agent.name))
        g.addWidget(path)
        self.rows[agent.id] = SimpleNamespace(state=state, connect=connect, disconnect=disconnect, error=error)
        self._paint_state(agent)

    def _paint_state(self, agent: cc.Agent) -> None:
        w = self.rows[agent.id]
        value = cc.state(cc.settings_file(agent=agent), self.command)
        w.state.setText(("●  " if value == cc.ON else "○  ") + cc.state_text(value))
        w.state.setProperty("role", "ok" if value == cc.ON else "")
        w.state.style().polish(w.state)
        w.connect.setText(_("Connect again") if value == cc.STALE else _("Connect"))
        w.connect.setEnabled(value != cc.ON and cc.helper_available())
        w.disconnect.setEnabled(value != cc.OFF)

    def _broken(self, agent: cc.Agent) -> str:
        return _("{file} of {name} has an error, so it was left as it is. Fix the file and try again.").format(file=agent.hooks_name, name=agent.name)

    def _connect(self, agent: cc.Agent) -> None:
        w = self.rows[agent.id]
        w.error.setText("")
        if not cc.install_helper():
            w.error.setText(_("Could not put the helper into {path}").format(path=cc.helper_dir()))
            return
        try:
            cc.connect(cc.settings_file(agent=agent), self.command)
        except cc.SettingsBroken:
            w.error.setText(self._broken(agent))
            return
        except OSError as exc:
            w.error.setText(_("Could not write {path}: {error}").format(path=cc.settings_file(agent=agent), error=exc.strerror or exc))
            return
        self.shell.set_status(_("{name} connected").format(name=agent.name))
        self._paint_state(agent)

    def _disconnect(self, agent: cc.Agent) -> None:
        w = self.rows[agent.id]
        w.error.setText("")
        try:
            cc.disconnect(cc.settings_file(agent=agent))
        except (cc.SettingsBroken, OSError):
            w.error.setText(self._broken(agent))
            return
        self.shell.set_status(_("{name} disconnected").format(name=agent.name))
        self._paint_state(agent)

    # ---- ready-made sets

    def _presets(self) -> None:
        from .settings import help_icon

        g = self.s.group(_("What counts as an event"))
        config = self.config()
        for preset in PRESETS:
            row = QHBoxLayout()
            box = QCheckBox(_(preset.title), self.s.inner)
            box.setChecked(config.presets.get(preset.id, True))
            box.setToolTip(_(preset.example))
            box.toggled.connect(lambda on, p=preset.id: self._toggle_preset(p, on))
            row.addWidget(box)
            row.addWidget(help_icon(_(preset.example), self.s.inner))
            row.addStretch(1)
            g.addLayout(row)
            hint = label(_(preset.hint), "muted", self.s.inner)
            hint.setWordWrap(True)
            hint.setContentsMargins(26, 0, 0, 0)
            g.addWidget(hint)

    def _toggle_preset(self, preset: str, on: bool) -> None:
        config = self.config()
        config.presets[preset] = on
        self.save_config(config)

    # ---- own rules

    def _rules(self) -> None:
        g = self.s.group(_("Own rules"))
        self._muted(g, _("For what the sets above do not know: “if the command has … — write …”."))
        self._muted(g, _("Example: when Claude runs the nightly script nightly.sh, write “nightly run” to Miscellaneous."))
        self.rules_host = QWidget(self.s.inner)
        self.rules_col = QVBoxLayout(self.rules_host)
        self.rules_col.setContentsMargins(0, 6, 0, 0)
        g.addWidget(self.rules_host)
        add = button(_("+ Add rule"), "small", self.s.inner)
        add.setToolTip(_("Opens a form; the “Fill with an example” button there shows what a rule looks like."))
        add.clicked.connect(lambda: self.edit_rule(None))
        g.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)
        self.paint_rules()

    def paint_rules(self) -> None:
        while self.rules_col.count():
            item = self.rules_col.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.hide()
                w.deleteLater()
        rules = self.config().rules
        if not rules:
            self.rules_col.addWidget(label(_("No own rules yet."), "muted", self.rules_host))
        for i, rule in enumerate(rules):
            row = QWidget(self.rules_host)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            on = QCheckBox(row)
            on.setChecked(rule.enabled)
            on.setToolTip(_("On / off without deleting"))
            on.toggled.connect(lambda v, k=i: self._rule_on(k, v))
            rl.addWidget(on)
            text = QLabel(f"{rule.name or rule.pattern}  →  {status_label(rule.status)}", row)
            text.setToolTip(describe(rule))
            rl.addWidget(text, 1)
            rl.addWidget(icon_button("✎", _("Edit"), partial(self.edit_rule, i), row))
            rl.addWidget(icon_button("✕", _("Delete"), partial(self._delete_rule, i), row))
            self.rules_col.addWidget(row)

    def _rule_on(self, index: int, on: bool) -> None:
        config = self.config()
        config.rules[index].enabled = on
        self.save_config(config)

    def _delete_rule(self, index: int) -> None:
        config = self.config()
        del config.rules[index]
        if self.save_config(config):
            self.paint_rules()

    def edit_rule(self, index: int | None) -> RuleDialog:
        config = self.config()
        return RuleDialog(self.win, config.rules[index] if index is not None else Rule(), lambda rule: self._rule_saved(index, rule))

    def _rule_saved(self, index: int | None, rule: Rule) -> None:
        config = self.config()
        if index is None:
            config.rules.append(rule)
        else:
            config.rules[index] = rule
        if self.save_config(config):
            self.paint_rules()

    # ---- folders

    def _folders(self) -> None:
        g = self.s.group(_("Where it works"))
        inner = self.s.inner
        config = self.config()
        choice = QButtonGroup(self)
        every = QRadioButton(_("In every project"), inner)
        every.setToolTip(_("The hook works wherever Claude is started."))
        only = QRadioButton(_("Only in these folders"), inner)
        only.setToolTip(_("The hook writes only when Claude works inside one of the folders listed here, e.g. E:\\work."))
        choice.addButton(every)
        choice.addButton(only)
        (only if config.only_folders else every).setChecked(True)
        g.addWidget(every)
        g.addWidget(only)
        self.only_row, self.only_box = self._folder_box(config.only_folders)
        g.addWidget(self.only_row)
        g.addLayout(titled(_("Never in these folders"), _("Personal projects and experiments: nothing Claude does inside these folders reaches the journal."), inner))
        skip_row, self.skip_box = self._folder_box(config.skip_folders)
        g.addWidget(skip_row)
        self._muted(g, _("One folder per line; the folders inside count too. Items written in a repository whose server is not the tracker's or GitLab's from Report links are dropped anyway."))
        foot = QHBoxLayout()
        save = button(_("Save folders"), "small", inner)
        save.setToolTip(_("The folder lists apply from the next command Claude runs."))
        saved = label("", "ok", inner)
        foot.addWidget(save)
        foot.addWidget(saved)
        foot.addStretch(1)
        g.addLayout(foot)
        self.only_radio = only
        only.toggled.connect(self.only_row.setVisible)
        self.only_row.setVisible(only.isChecked())
        save.clicked.connect(lambda: self.save_folders(saved))

    def _folder_box(self, folders: list[str]) -> tuple[QWidget, QPlainTextEdit]:
        row = QWidget(self.s.inner)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        box = text_box(row, 3, "\n".join(folders))
        rl.addWidget(box, 1)
        pick = button(_("Add folder…"), "small", row)
        pick.setToolTip(_("Pick a folder: it is added as a new line. You can also type or paste paths."))

        def add() -> None:
            folder = QFileDialog.getExistingDirectory(self.win)
            if folder and shiboken6.isValid(box):  # the idle lock does not wait for a file dialog: the window may have locked under it
                text = box.toPlainText().rstrip("\n")
                box.setPlainText((text + "\n" if text else "") + folder)

        pick.clicked.connect(add)
        rl.addWidget(pick, 0, Qt.AlignmentFlag.AlignTop)
        return row, box

    def save_folders(self, saved: QLabel | None = None) -> bool:
        config = self.config()
        config.only_folders = lines_of(self.only_box) if self.only_radio.isChecked() else []
        config.skip_folders = lines_of(self.skip_box)
        if self.only_radio.isChecked() and not config.only_folders:
            show_error(self.win, _("Add at least one folder, or choose “In every project”."))
            return False
        if self.save_config(config):
            if saved is not None:
                saved.setText("✓ " + _("Saved"))
            return True
        return False

    # ---- check

    def _check(self) -> None:
        g = self.s.group(_("Test a command"))
        inner = self.s.inner
        self._muted(g, _("Paste a command and what it answered: you will see what would go into the journal and which rule finds it."))
        g.addLayout(titled(_("Command"), _("What Claude ran, e.g. glab mr merge 44"), inner))
        self.check_command = text_box(inner, 2)
        g.addWidget(self.check_command)
        g.addLayout(titled(_("Answer"), _("What the command printed back, e.g. ✓ Merged. Some rules need it: an MR link appears only in the answer."), inner))
        self.check_answer = text_box(inner, 2)
        g.addWidget(self.check_answer)
        run = button(_("Check it"), "small", inner)
        run.setToolTip(_("Nothing is written: this only shows what would be."))
        run.clicked.connect(self.run_check)
        g.addWidget(run, 0, Qt.AlignmentFlag.AlignLeft)
        self.check_result = QLabel("", inner)
        self.check_result.setWordWrap(True)
        g.addWidget(self.check_result)

    def run_check(self) -> None:
        hits = match(test_payload(self.check_command.toPlainText(), self.check_answer.toPlainText()), self.config(), current_links())
        self.check_result.setText("\n".join("✓ " + event_text(*h) for h in hits) if hits else _("Nothing would be written."))

    # ---- recent

    def _recent(self) -> None:
        g = self.s.group(_("Recent triggers"))
        self._muted(g, _("The last ten times the hook wrote something: the time, the journal line and the rule that found it."))
        host = QWidget(self.s.inner)
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        g.addWidget(host)

        def paint() -> None:
            while col.count():
                item = col.takeAt(0)
                w = item.widget() if item is not None else None
                if w is not None:
                    w.hide()
                    w.deleteLater()
            rows = read_log(log_path(), 10)
            if not rows:
                col.addWidget(label(_("Nothing yet: the hook writes here when it recognises something."), "muted", host))
            for row in rows:
                role = ""
                if "error" in row:
                    text, role = f"{row.get('ts', '')}  {_('error')}: {row['error']}", "error"
                elif row.get("slow_rules"):
                    text, role = f"{row.get('ts', '')[5:16]}  " + "; ".join(_("rule “{name}” ran too long on that answer and was skipped").format(name=n) for n in row["slow_rules"]), "error"
                else:
                    events = "; ".join(event_text(*e) for e in row.get("events", []) if len(e) == 3)
                    skipped = f"  ({_('folder skipped')})" if row.get("skipped") == "folder" else ""
                    text, role = f"{row.get('ts', '')[5:16]}  {events}{skipped}", "muted" if skipped else ""
                lab = label(text, role, host)
                lab.setWordWrap(True)
                col.addWidget(lab)

        refresh = button(_("Refresh"), "small", self.s.inner)
        refresh.clicked.connect(paint)
        g.addWidget(refresh, 0, Qt.AlignmentFlag.AlignLeft)
        paint()

    # ---- teach

    def _teach(self) -> None:
        g = self.s.group(_("Teach the assistant"), _("Each assistant has its own file of personal instructions; it reads it at the start of every session, in every project."))
        inner = self.s.inner
        self._muted(g, _("The hook sees only commands. Verdicts, test cases and other work the assistant writes itself when its instructions say so: this puts a block about the journal into its personal instructions."))
        self.teach_states: dict[str, QLabel] = {}
        for agent in cc.AGENTS:
            row = QHBoxLayout()
            name = QLabel(agent.name, inner)
            name.setFont(theme.font(12, bold=True))
            row.addWidget(name)
            state = QLabel("", inner)
            row.addWidget(state)
            row.addStretch(1)
            g.addLayout(row)
            self.teach_states[agent.id] = state
            path = QLabel(str(cc.instructions_file(agent=agent)), inner)
            path.setFont(theme.font(9, mono=True))
            path.setToolTip(_("{name}'s personal instructions: it reads them at the start of every session, in every project.").format(name=agent.name))
            g.addWidget(path)
            teach = button(_("Teach {name} to keep the journal…").format(name=agent.name), "small", inner)
            teach.setToolTip(_("Opens the text first: you choose what the assistant writes and can edit it before anything is saved."))
            teach.clicked.connect(lambda _c=False, a=agent: TeachDialog(self.win, self.paint_teach, self.parts, a))
            g.addWidget(teach, 0, Qt.AlignmentFlag.AlignLeft)
        self.paint_teach()

    def paint_teach(self) -> None:
        for agent in cc.AGENTS:
            lab = self.teach_states.get(agent.id)
            if lab is not None:
                on = cc.current_block(cc.instructions_file(agent=agent)) is not None
                lab.setText(("●  " if on else "○  ") + (_("The instructions are in place") if on else _("No instructions yet")))


class RuleDialog(Dialog):
    """The form of one own rule."""

    def __init__(self, parent: QWidget, rule: Rule, on_save):
        super().__init__(parent, _("Rule"))
        self.on_save = on_save
        words = status_words()
        note = current_layout().kind(current_layout().note_kind())
        self._note_word = note.word if note else (words[-1] if words else "note")
        if rule.status not in words:
            rule.status = self._note_word if self._note_word in words else (words[0] if words else rule.status)
            words = words or [rule.status]
        self._words = words
        self.enabled = rule.enabled
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        width = self.fontMetrics().averageCharWidth() * 44
        self.name = QLineEdit(rule.name, self)
        self.pattern = QLineEdit(rule.pattern, self)
        self.regex = QCheckBox(_("It is a regular expression"), self)
        self.regex.setChecked(rule.regex)
        self.regex.setToolTip(_("For the experienced: the text above is a pattern, e.g. triage (\\d+). The part in brackets can be written as the item. Leave it off if unsure."))
        self.where = QComboBox(self)
        for w in WHERES:
            self.where.addItem(_(WHERE_LABELS[w]), w)
        self.where.setCurrentIndex(WHERES.index(rule.where))
        self.also = QLineEdit(rule.also, self)
        self.status = QComboBox(self)
        for w in words:
            self.status.addItem(status_label(w), w)
        self.status.setCurrentIndex(words.index(rule.status))
        self.take = QComboBox(self)
        for t in TAKES:
            self.take.addItem(_(TAKE_LABELS[t]), t)
        self.take.setCurrentIndex(TAKES.index(rule.take))
        self.text = QLineEdit(rule.text, self)
        rows = [
            (_("Name"), self.name, _("For you: shown in the list and in Recent triggers.")),
            (_("Look for"), self.pattern, _("A piece of text, any letter case. For example: nightly.sh")),
            ("", self.regex, ""),
            (_("Where"), self.where, ""),
            (_("And the answer has"), self.also, _("Optional: a word that means success, e.g. merged.")),
            (_("Write as"), self.status, ""),
            (_("Item"), self.take, _("Found text that looks like a token or a key is never written.")),
            (_("Text"), self.text, _("What to write when the item is the fixed text, e.g. “nightly run”.")),
        ]
        r = 0
        for text, widget, hint in rows:
            if text:
                grid.addWidget(QLabel(text, self), r, 0)
            widget.setMinimumWidth(width)
            grid.addWidget(widget, r, 1)
            r += 1
            if hint:
                note_label = label(hint, "muted", self)
                note_label.setWordWrap(True)
                grid.addWidget(note_label, r, 1)
                r += 1
        self.summary = label("", "muted", self)
        self.summary.setWordWrap(True)
        grid.addWidget(self.summary, r, 1)
        self.error = label("", "error", self)
        grid.addWidget(self.error, r + 1, 1)
        self.box.addLayout(grid, 0, 0)
        foot = QHBoxLayout()
        save = button(_("Save"), "primary", self)
        save.clicked.connect(self.save)
        cancel = button(_("Cancel"), "small", self)
        cancel.clicked.connect(self.reject)
        sample = button(_("Fill with an example"), "small", self)
        sample.setToolTip(_("Fills the form with a sample rule: “when Claude runs nightly.sh, write ‘nightly run’ to Miscellaneous”. Change it to your own."))
        sample.clicked.connect(self.example)
        foot.addWidget(save)
        foot.addWidget(cancel)
        foot.addWidget(sample)
        foot.addStretch(1)
        self.box.addLayout(foot, 1, 0)
        for edit in (self.name, self.pattern, self.also, self.text):
            edit.textChanged.connect(self.paint)
        for combo in (self.where, self.status, self.take):
            combo.currentIndexChanged.connect(self.paint)
        self.regex.toggled.connect(self.paint)
        self.keys(self.save)
        self.paint()
        self.open()

    def rule(self) -> Rule:
        return Rule(name=self.name.text().strip(), status=self.status.currentData(), pattern=self.pattern.text().strip(), where=self.where.currentData(),
                    regex=self.regex.isChecked(), take=self.take.currentData(), text=self.text.text().strip(), also=self.also.text().strip(), enabled=self.enabled)

    def paint(self, *_a: object) -> None:
        rule = self.rule()
        self.text.setEnabled(rule.take == TAKE_TEXT)
        self.summary.setText(_("This rule: ") + describe(rule))

    def example(self) -> None:
        self.name.setText(_("Nightly run"))
        self.pattern.setText("nightly.sh")
        self.regex.setChecked(False)
        self.where.setCurrentIndex(WHERES.index("command"))
        self.also.setText("")
        if self._note_word in self._words:
            self.status.setCurrentIndex(self._words.index(self._note_word))
        self.take.setCurrentIndex(TAKES.index(TAKE_TEXT))
        self.text.setText(_("nightly run"))

    def save(self) -> None:
        rule = self.rule()
        problem = rule.problem()
        if problem:
            self.error.setText(_(problem))
            return
        self.accept()
        self.on_save(rule)


class TeachDialog(Dialog):
    """What goes into the assistant's instructions: which events, the note language, the text."""

    def __init__(self, parent: QWidget, after, parts: list[str], agent: cc.Agent = cc.CLAUDE):
        super().__init__(parent, _("Teach {name} to keep the journal").format(name=agent.name))
        self.after_change, self.parts, self.agent = after, parts, agent
        self.file = cc.instructions_file(agent=agent)
        self.box.addLayout(titled(_("What Claude should write"), _("Untick what you do not want in the journal: Claude will not be told about it."), self), 0, 0)
        ticks = QGridLayout()
        self.picked: dict[str, QCheckBox] = {}
        for i, status in enumerate(status_words()):
            box = QCheckBox(status_label(status), self)
            box.setChecked(True)
            box.toggled.connect(self.regenerate)
            ticks.addWidget(box, i // 3, i % 3)
            self.picked[status] = box
        self.box.addLayout(ticks, 1, 0)
        lang_row = titled(_("Language of notes"), _("Claude writes the “Miscellaneous” notes in this language, e.g. Russian or English."), self)
        self.lang = QLineEdit("Russian" if language() == "ru" else "English", self)
        self.lang.setFixedWidth(self.lang.fontMetrics().averageCharWidth() * 16 + 20)
        self.lang.textEdited.connect(self.regenerate)
        lang_row.insertWidget(2, self.lang)
        self.box.addLayout(lang_row, 2, 0)
        self.box.addLayout(titled(_("The text (you can edit it before inserting)"), _("This is what Claude will read. Ticks and the language rebuild it, so edit the text last."), self), 3, 0)
        self.preview = text_box(self, 16)
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.preview.setMinimumWidth(self.preview.fontMetrics().averageCharWidth() * 100)
        self.box.addWidget(self.preview, 4, 0)
        if cc.mentions_journal_elsewhere(self.file):
            warn = label(_("Your instructions file already speaks of the journal outside this block. Remove the older section by hand, or Claude gets two sets of rules."), "error", self)
            warn.setWordWrap(True)
            self.box.addWidget(warn, 5, 0)
        foot = QHBoxLayout()
        has_block = cc.current_block(self.file) is not None
        insert = button((_("Replace in {file}") if has_block else _("Insert into {file}")).format(file=self.file.name), "primary", self)
        insert.setToolTip(_("Saves the text into Claude's instructions between Daykeeper's marks; the rest of the file stays. Pressed again, it replaces its own block."))
        insert.clicked.connect(self.insert)
        copy = button(_("Copy"), "small", self)
        copy.setToolTip(_("To paste it somewhere yourself, e.g. into one project's instructions."))
        copy.clicked.connect(self.copy)
        foot.addWidget(insert)
        foot.addWidget(copy)
        if has_block:
            remove = button(_("Remove from {file}").format(file=self.file.name), "small", self)
            remove.setToolTip(_("Takes Daykeeper's block out; Claude stops writing the journal itself."))
            remove.clicked.connect(self.remove)
            foot.addWidget(remove)
        cancel = button(_("Cancel"), "small", self)
        cancel.clicked.connect(self.reject)
        foot.addWidget(cancel)
        foot.addStretch(1)
        self.box.addLayout(foot, 6, 0)
        self.regenerate()
        self.open()

    def _shell(self):
        win = self.parentWidget()
        return getattr(win, "shell", None)

    def regenerate(self, *_a: object) -> None:
        options = cc.TeachOptions(tuple(s for s, b in self.picked.items() if b.isChecked()), self.lang.text().strip() or "English")
        self.preview.setPlainText(cc.instructions(options, self.parts))

    def insert(self) -> None:
        try:
            cc.insert_block(self.preview.toPlainText(), self.file)
        except OSError as exc:
            show_error(self, _("Could not write {path}: {error}").format(path=self.file, error=exc.strerror or exc))
            return
        shell = self._shell()
        if shell is not None:
            shell.set_status(_("{name}'s instructions updated").format(name=self.agent.name))
        self.accept()
        self.after_change()

    def copy(self) -> None:
        shell = self._shell()
        if shell is not None:
            shell.copy_to_clipboard(self.preview.toPlainText(), secret=False)

    def remove(self) -> None:
        try:
            cc.remove_block(self.file)
        except OSError as exc:
            show_error(self, _("Could not write {path}: {error}").format(path=self.file, error=exc.strerror or exc))
            return
        self.accept()
        self.after_change()
