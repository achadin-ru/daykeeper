"""The "Journal" section of the Qt settings window (journal_settings_view of the Tk one): a
ready-made layout for the user's work, the columns of a day, the kinds of activity and the
report lines. Edited on a copy; "Save" keeps it in settings.json and rebuilds the Journal
tab. The model is `journal_layout`; this module only lays out controls."""

from __future__ import annotations

from functools import partial
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QButtonGroup, QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLineEdit, QRadioButton, QVBoxLayout, QWidget

from ... import journal_layout as jl
from ...i18n import _
from ..widgets.common import divider, icon_button
from .base import ask_yes_no, button, label, show_error
from ..errors import guard_events

PHRASE_TIP = "A word for the first line of the report when there are items, e.g. tested tasks or code review. Empty — nothing."
ROLE_TIP = "MR numbers — !42 and links on one line; test cases — one shared “New tests” line; a list — any items on one line; notes — each goes into line 1 as it is."


class ItemRow(QWidget):
    """One column or kind: shown, name, the word in line 1, the role (kinds), the order and
    the removal. One line when it fits, the word and the role under the name when not."""

    def __init__(self, section: JournalSection, items: list, index: int, item: Any, is_kind: bool, parent: QWidget):
        super().__init__(parent)
        self.section, self.item = section, item
        self.shown = QCheckBox(self)
        self.shown.setChecked(not item.hidden)
        self.shown.setToolTip(_("Shown / hidden. A hidden one keeps its items: show it again and they are back."))
        self.shown.toggled.connect(lambda on: setattr(item, "hidden", not on))
        self.name = QLineEdit(_(item.label), self)
        self.name.setToolTip(_("How it is called on the Journal tab and in the report. Command word: {word}").format(word=item.word))
        self.name.textEdited.connect(self._renamed)
        self.phrase = QLineEdit(_(item.phrase) if item.phrase else "", self)
        self.phrase.setPlaceholderText(_("In line 1"))
        self.phrase.setToolTip(_(PHRASE_TIP))
        self.phrase.textEdited.connect(lambda t: setattr(item, "phrase", t.strip()))
        self.role: QComboBox | None = None
        if is_kind:
            self.role = QComboBox(self)
            for r in jl.ROLES:
                self.role.addItem(_(jl.ROLE_LABELS[r]), r)
            self.role.setCurrentIndex(jl.ROLES.index(item.role) if item.role in jl.ROLES else 0)
            self.role.setToolTip(_(ROLE_TIP))
            self.role.activated.connect(lambda _i: setattr(item, "role", self.role.currentData() if self.role else item.role))
        self.up = icon_button("▲", "", partial(section.move_item, items, index, -1), self)
        self.down = icon_button("▼", "", partial(section.move_item, items, index, +1), self)
        self.rm = icon_button("✕", _("Remove. Items already written stay in the vault."), partial(section.remove_item, items, index), self)
        self.up.setEnabled(index > 0)
        self.down.setEnabled(index < len(items) - 1)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 2, 0, 2)
        self.grid.setHorizontalSpacing(8)
        self.compact: bool | None = None
        self.set_compact(False)

    def set_compact(self, compact: bool) -> None:
        if compact == self.compact:
            return
        self.compact = compact
        fields = [self.phrase] + ([self.role] if self.role is not None else [])
        for w in (self.shown, self.name, *fields, self.up, self.down, self.rm):
            self.grid.removeWidget(w)
        self.grid.addWidget(self.shown, 0, 0)
        self.grid.addWidget(self.name, 0, 1)
        if compact:
            for k, w in enumerate(fields):
                self.grid.addWidget(w, 1, 1 + k)
            base = 2
        else:
            for k, w in enumerate(fields):
                self.grid.addWidget(w, 0, 2 + k)
            base = 2 + len(fields)
        for k, b in enumerate((self.up, self.down, self.rm)):
            self.grid.addWidget(b, 0, base + k)
        self.grid.setColumnStretch(2 if not compact else 1, 1)

    def wide_width(self) -> int:
        fm = self.name.fontMetrics()
        return fm.averageCharWidth() * (28 + 14 + (16 if self.role else 0)) + 160

    def _renamed(self, text: str) -> None:
        text = text.strip()
        if text and text != _(self.item.label):
            self.item.label = text
            if id(self.item) in self.section.fresh:
                taken = jl.taken_words(self.section.layout_) - {self.item.id, self.item.word}
                self.item.id = self.item.command = jl.new_id(self.item.label, taken)


@guard_events
class ListBox(QWidget):
    """The rows of one list, two lines each when the section is narrow."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.col = QVBoxLayout(self)
        self.col.setContentsMargins(0, 0, 0, 0)
        self.col.setSpacing(4)
        self.rows: list[ItemRow] = []
        self._compact = False

    def clear(self) -> None:
        while self.col.count():
            item = self.col.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.hide()
                w.deleteLater()
        self.rows = []

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fit()

    def fit(self) -> None:
        if not self.rows:
            return
        compact = self.width() < max(r.wide_width() for r in self.rows)
        if compact != self._compact or any(r.compact != compact for r in self.rows):
            self._compact = compact
            for r in self.rows:
                r.set_compact(compact)


class JournalSection(QWidget):
    def __init__(self, win: Any):
        from .settings import Section

        super().__init__()
        self.win, self.shell = win, win.shell
        self.layout_ = jl.copy(jl.current())
        self.fresh: set[int] = set()  # id() of columns and kinds added here: their word follows the label
        self.section = Section(win)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.section)
        s = self.section
        s.head(_("Journal"), _("What a day of your journal is made of. Start from a ready-made layout, then rename, hide or add columns and kinds of activity. Items already written are never deleted."))
        self._presets()
        self.columns_box = self._list_group(_("Columns of a day"), _("Lists of the day, side by side on the Journal tab: issue keys or any short items, one per line. The report prints each as a heading with its items."), _("+ Add column"), self.add_column)
        self.kinds_box = self._list_group(_("Kinds of activity"), _("Lines of the Activity field, like “MR reviews: !42, !43”. The role says how a kind goes into the report."), _("+ Add kind"), self.add_kind)
        self._report()
        save = button(_("Save"), "primary", s)
        save.setToolTip(_("The Journal tab is rebuilt at once; the console commands and the Claude Code hook use the new words from their next run."))
        s.foot_box.addWidget(save)
        self.shown_saved = s.saved_label(_("Saved"))
        save.clicked.connect(self.save)
        s.end()
        self.paint()

    # ---- ready-made

    def _presets(self) -> None:
        s = self.section
        g = s.group(_("Ready-made layouts"), _("Pick the one closest to your work and press “Use it”: the columns and kinds below are replaced. Nothing is saved until “Save”."))
        self.preset_group = QButtonGroup(self)
        for pid, title in jl.PRESETS.items():
            radio = QRadioButton(_(title), s.inner)
            radio.setProperty("pid", pid)
            radio.setChecked(pid == self.layout_.preset)
            self.preset_group.addButton(radio)
            g.addWidget(radio)
            hint = label(_(jl.PRESET_HINTS[pid]), "muted", s.inner)
            hint.setWordWrap(True)
            hint.setContentsMargins(26, 0, 0, 0)
            g.addWidget(hint)
        use = button(_("Use it"), "small", s.inner)
        use.setToolTip(_("Replaces the columns and kinds below with the chosen layout. Your own changes to them are lost; written items stay."))
        use.clicked.connect(self.use_preset)
        g.addWidget(use, 0, Qt.AlignmentFlag.AlignLeft)

    def use_preset(self, confirm: bool = True) -> None:
        checked = self.preset_group.checkedButton()
        pid = str(checked.property("pid")) if checked is not None else ""
        if not pid:
            return
        if confirm and not ask_yes_no(self.win, _("Replace the columns and kinds with the “{name}” layout?").format(name=_(jl.PRESETS[pid]))):
            return
        mr_short = self.layout_.mr_short
        self.layout_ = jl.preset(pid)
        self.layout_.mr_short = mr_short
        self.fresh.clear()
        self.paint()

    # ---- lists

    def _list_group(self, title: str, help_text: str, add_text: str, add) -> ListBox:
        s = self.section
        g = s.group(title, help_text)
        heads = QHBoxLayout()
        heads.setContentsMargins(26, 0, 0, 0)
        heads.addWidget(label(_("Name"), "muted", s.inner))
        heads.addStretch(1)
        g.addLayout(heads)
        box = ListBox(s.inner)
        g.addWidget(box)
        add_btn = button(add_text, "small", s.inner)
        add_btn.clicked.connect(add)
        g.addWidget(add_btn, 0, Qt.AlignmentFlag.AlignLeft)
        return box

    def paint(self) -> None:
        for box, items, is_kind in ((self.columns_box, self.layout_.columns, False), (self.kinds_box, self.layout_.kinds, True)):
            box.clear()
            if not items:
                box.col.addWidget(label(_("None: press the button below to add one."), "muted", box))
            for i, item in enumerate(items):
                if i:
                    box.col.addWidget(divider(box))
                row = ItemRow(self, items, i, item, is_kind, box)
                box.rows.append(row)
                box.col.addWidget(row)
            box.fit()
        self.mr.setChecked(self.layout_.mr_short)
        self.plans.setChecked(self.layout_.show_plans)
        self.other.setChecked(self.layout_.show_other)

    def move_item(self, items: list, index: int, step: int) -> None:
        j = index + step
        if 0 <= j < len(items):
            items[index], items[j] = items[j], items[index]
            self.paint()

    def remove_item(self, items: list, index: int) -> None:
        if not ask_yes_no(self.win, _("Remove “{name}”? Items already written stay in the vault and come back if you add it again with the same command word.").format(name=_(items[index].label))):
            return
        self.fresh.discard(id(items[index]))
        del items[index]
        self.paint()

    def add_column(self) -> None:
        text = _("New column")
        word = jl.new_id(text, jl.taken_words(self.layout_))
        col = jl.Column(word, text, command=word)
        self.layout_.columns.append(col)
        self.fresh.add(id(col))
        self.paint()

    def add_kind(self) -> None:
        text = _("New kind")
        word = jl.new_id(text, jl.taken_words(self.layout_))
        kind = jl.Kind(word, text, command=word)
        self.layout_.kinds.append(kind)
        self.fresh.add(id(kind))
        self.paint()

    # ---- report

    def _report(self) -> None:
        s = self.section
        g = s.group(_("Report"), _("What “Build report” puts together besides the columns and the activity."))
        boxes = []
        for text, attr, tip in (
            (_("MR numbers short: !42, repo!17"), "mr_short", _("Off — every MR as a full link.")),
            (_("The “Plans” line"), "show_plans", _("“2. Plans” in the day editor and the report.")),
            (_("The “Out of office / other” line"), "show_other", _("The last numbered line: time off, and anything else worth a line.")),
        ):
            box = QCheckBox(text, s.inner)
            box.setToolTip(tip)
            box.toggled.connect(lambda on, a=attr: setattr(self.layout_, a, on))
            g.addWidget(box)
            boxes.append(box)
        self.mr, self.plans, self.other = boxes

    # ---- save

    def save(self) -> None:
        layout = self.layout_
        if not layout.visible_columns() and not layout.visible_kinds():
            show_error(self.win, _("Keep at least one column or kind of activity shown."))
            return
        if layout.preset in jl.PRESETS and layout.to_settings() != jl.preset(layout.preset).to_settings():
            layout.preset = "custom"
        if not self.shell.apply_journal_layout(jl.copy(layout)):
            show_error(self.win, _("Could not write settings.json"))
            return
        self.fresh.clear()
        self.shell.set_status(_("Journal layout saved"))
        self.shown_saved()
