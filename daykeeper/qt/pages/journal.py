"""The “Journal” tab of the Qt window: the day list on the left, the day editor on the right
— the verdict columns, the activity lines, the plans — and the standup report built from a
day. The same behaviour as journal_view.JournalPage of the Tk window; the layout of the
editor comes from journal_layout (the columns and kinds set up in the settings)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLineEdit, QListWidgetItem, QMenu, QPlainTextEdit, QStackedWidget, QVBoxLayout, QWidget

from ...i18n import _, fmt_day
from ...journal import DEFAULT_OTHER, Day, build_report, inbox_path_for, parse_activity_lines, parse_lines, today
from ...journal_layout import ROLE_MR, current as current_layout
from .. import theme
from ..dialogs.base import Dialog, ask_yes_no, button, label
from ..widgets.common import more_button, popup_below, scroll_area, section
from ..widgets.countlist import COUNT_ROLE, KEY_ROLE, add_count_item, count_list
from ..widgets.flow import flow_host
from .base import TabPage


def text_field(parent: QWidget, lines: int) -> QPlainTextEdit:
    """A multi-line field `lines` high (the Tk window's TextField)."""
    field = QPlainTextEdit(parent)
    field.setFixedHeight(field.fontMetrics().lineSpacing() * lines + 16)
    field.setTabChangesFocus(True)
    return field


def set_text(field: QPlainTextEdit, value: str, parse: Callable[[str], object] | None = None) -> None:
    """Put `value` into the field unless what is there already means the same (`parse` reads
    both the way the day does): reloading a text being typed in moved the cursor and made
    Ctrl+Z wipe the field. A real change starts the history afresh and keeps the cursor."""
    current = field.toPlainText()
    if current == value or (parse is not None and parse(current) == parse(value)):
        return
    pos = field.textCursor().position()
    field.setPlainText(value)  # also resets the undo history
    cursor = field.textCursor()
    cursor.setPosition(min(pos, len(value)))
    field.setTextCursor(cursor)


class JournalPage(TabPage):
    """The Journal tab. `jday` is the day on the editor, None while no day is picked."""

    name = "journal"

    def build(self) -> None:
        self.jday: Day | None = None
        self._loading = False
        layout = current_layout()
        _splitter, left, col, right = self.split(300, 240)
        col.addWidget(section(_("DAYS"), left))
        self.days = count_list(left)
        self.days.setToolTip(_("The numbers on the right: {columns}").format(columns=" / ".join(_(c.label) for c in layout.visible_columns()) or _("items")))
        self.days.currentItemChanged.connect(self._on_day_select)
        col.addWidget(self.days, 1)
        row = QHBoxLayout()
        today_btn = button(_("+ Today"), "primary", left)
        today_btn.clicked.connect(self._add_today)
        row.addWidget(today_btn)
        row.addStretch(1)
        delete = button(_("Delete day"), "danger", left)
        delete.clicked.connect(self._delete_day)
        row.addWidget(delete)
        col.addLayout(row)

        self.card_stack = QStackedWidget(right)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(self.card_stack, 1)
        self.placeholder = label(_("Pick a day on the left\nor press “+ Today”"), "placeholder", right)
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        area, card, ccol = scroll_area(right, (28, 20, 28, 16))
        self.card_stack.addWidget(self.placeholder)
        self.card_stack.addWidget(area)
        self._build_editor(card, ccol, layout)

        actions_host, actions = flow_host(right, 8)
        actions_host.setContentsMargins(28, 12, 28, 16)
        line = QFrame(right)
        line.setObjectName("Divider")
        line.setFixedHeight(1)
        rl.addWidget(line)
        rl.addWidget(actions_host)
        report = button(_("📋  Build report"), "primary", actions_host)
        report.clicked.connect(self.report_dialog)
        actions.addWidget(report)
        inbox_btn = button(_("Read the inbox now"), "small", actions_host)
        inbox_btn.setToolTip(_("The sessions' inbox is read every minute by itself; this reads it right away"))
        inbox_btn.clicked.connect(lambda: self.shell.pull_inbox(loud=True))
        actions.addWidget(inbox_btn)
        more = more_button([(_("Links…"), lambda: self.shell.settings_dialog("links")), (_("Set up the journal…"), lambda: self.shell.settings_dialog("journal"))], _("Links to the tracker and GitLab; the columns, kinds of activity and the report"), actions_host)
        actions.addWidget(more)
        self.inbox_label = label("", "muted", actions_host)
        actions.addWidget(self.inbox_label)

    def _build_editor(self, card: QWidget, col: QVBoxLayout, layout) -> None:
        self.jtitle = label("", "heading", card)
        col.addWidget(self.jtitle)
        col.addSpacing(14)
        col.addWidget(section(_("1. DONE — what the day was about, one line"), card))
        col.addSpacing(4)
        self.summary = QLineEdit(card)
        self.summary.textEdited.connect(self._summary_edited)
        col.addWidget(self.summary)
        col.addSpacing(14)

        columns = layout.visible_columns()
        kinds = layout.visible_kinds()
        quick_host, quick = flow_host(card, 6)
        add = QWidget(quick_host)
        al = QHBoxLayout(add)
        al.setContentsMargins(0, 0, 0, 0)
        al.addWidget(label(_("Add"), "muted", add))
        self.quick = QLineEdit(add)
        self.quick.setFixedWidth(self.quick.fontMetrics().averageCharWidth() * 16 + 24)
        if columns:
            self.quick.returnPressed.connect(lambda c=columns[0].id: self._quick_add(c))
        al.addWidget(self.quick)
        quick.addWidget(add)
        for c in columns:
            b = button(_(c.button or c.label), "small", quick_host)
            b.clicked.connect(lambda _x=False, cid=c.id: self._quick_add(cid))
            quick.addWidget(b)
        if kinds:
            act = button(_("Activity") + "  ▾", "small", quick_host)
            act.setToolTip(_("Adds the text from the field on the left as activity of the chosen kind; for MRs — !42 or a link"))
            menu = QMenu(act)
            for kind in kinds:
                menu.addAction(_(kind.label)).triggered.connect(lambda _x=False, k=kind.id: self._quick_add_activity(k))
            act.clicked.connect(lambda: popup_below(menu, act))
            quick.addWidget(act)
        col.addWidget(quick_host)
        col.addSpacing(4)

        grid_host = QWidget(card)
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        self.sections: dict[str, QPlainTextEdit] = {}
        for i, c in enumerate(columns):
            grid.addWidget(section(_(c.label).upper(), grid_host), 0, i)
            field = text_field(grid_host, 8)
            field.textChanged.connect(lambda cid=c.id: self._text_changed(cid))
            grid.addWidget(field, 1, i)
            grid.setColumnStretch(i, 1)
            self.sections[c.id] = field
        col.addWidget(grid_host)

        example = next((k for k in kinds if k.role == ROLE_MR), kinds[0] if kinds else None)
        sample = f"{_(example.label)}: {'!42, !43' if example.role == ROLE_MR else '…'}" if example else ""
        activity_title = section(_("ACTIVITY — {kinds}; a line like “{sample}”").format(kinds=", ".join(_(k.label) for k in kinds), sample=sample), card)
        activity_title.setWordWrap(True)
        col.addSpacing(14)
        col.addWidget(activity_title)
        col.addSpacing(4)
        self.activity = text_field(card, 4)
        self.activity.textChanged.connect(lambda: self._text_changed("activities"))
        col.addWidget(self.activity)

        self.plans = text_field(card, 3)
        self.other = text_field(card, 2)
        self.plans.textChanged.connect(lambda: self._text_changed("plans"))
        self.other.textChanged.connect(lambda: self._text_changed("other"))
        if layout.show_plans:
            col.addSpacing(14)
            col.addWidget(section(_("2. PLANS"), card))
            col.addSpacing(4)
            col.addWidget(self.plans)
        else:
            self.plans.hide()
        if layout.show_other:
            col.addSpacing(14)
            col.addWidget(section(_("3. OUT OF OFFICE / OTHER") if layout.show_plans else _("2. OUT OF OFFICE / OTHER"), card))
            col.addSpacing(4)
            col.addWidget(self.other)
        else:
            self.other.hide()
        col.addStretch(1)

    # ---- what the window asks for

    def refresh(self, keep_scroll: bool = False) -> None:
        self._refresh_days(select=self.jday.date if self.jday else today())

    def new_item(self) -> None:
        self._add_today()

    # ---- days

    def _refresh_days(self, select: str | None = None) -> None:
        if self.vault is None:
            return
        journal = self.vault.journal
        journal.get(today(), create=True)  # today is always at hand
        self._loading = True
        try:
            self.days.clear()
            for date in journal.dates():
                add_count_item(self.days, f"  {fmt_day(date)}", date, self._counts(journal.days[date]))
            target = select if select in journal.days else (journal.dates()[0] if journal.dates() else None)
            if target is not None:
                self.days.setCurrentRow(journal.dates().index(target))
        finally:
            self._loading = False
        if target is None:
            self._load_day(None)
        else:
            self._load_day(journal.days[target])

    def _on_day_select(self, current: QListWidgetItem | None, _previous=None) -> None:
        if self._loading or self.vault is None or current is None:
            return
        date = current.data(KEY_ROLE)
        if date not in self.vault.journal.days or (self.jday is not None and self.jday.date == date):
            return
        self._load_day(self.vault.journal.days[date])

    def _add_today(self) -> None:
        if self.vault is None:
            return
        self.vault.journal.get(today(), create=True)
        self._refresh_days(select=today())
        self.quick.setFocus()

    def _delete_day(self) -> None:
        if self.vault is None or self.jday is None:
            return
        if not ask_yes_no(self.shell, _("Delete day {day} from the journal?").format(day=fmt_day(self.jday.date))):
            return
        self.vault.backup_now()  # a deleted day cannot be undone after a lock: a copy first
        self.vault.journal.remove(self.jday.date)
        self.jday = None
        self._refresh_days()
        self.shell.mark_dirty("journal")

    @staticmethod
    def _counts(day: Day) -> str:
        return " / ".join(str(n) for n in day.counts())

    def _update_day_counts(self, day: Day) -> None:
        for i in range(self.days.count()):
            item = self.days.item(i)
            if item.data(KEY_ROLE) == day.date:
                item.setData(COUNT_ROLE, self._counts(day))

    # ---- the day

    def _load_day(self, day: Day | None) -> None:
        self._loading = True
        try:
            self.jday = day
            if day is None:
                self.card_stack.setCurrentWidget(self.placeholder)
                return
            self.card_stack.setCurrentIndex(1)
            self.jtitle.setText(fmt_day(day.date) + (_("  · today") if day.date == today() else ""))
            if self.summary.text() != day.summary:
                self.summary.setText(day.summary)
            set_text(self.activity, day.activity_lines(), parse_activity_lines)
            for cid, field in self.sections.items():
                set_text(field, "\n".join(day.columns.get(cid, [])), parse_lines)
            set_text(self.plans, day.plans)
            set_text(self.other, day.other or _(DEFAULT_OTHER))
            self._update_summary_hint()
            inbox = inbox_path_for(self.shell.vault_path)
            self.inbox_label.setText(_("inbox: {name}").format(name=inbox.name))
            self.inbox_label.setToolTip(_("Sessions write the day's events here:") + "\n" + str(inbox))
        finally:
            self._loading = False

    def _summary_edited(self, text: str) -> None:
        self._update_summary_hint()
        if self._loading or self.jday is None:
            return
        self.jday.summary = text
        self.shell.mark_dirty("journal")

    def _update_summary_hint(self) -> None:
        """An empty “Done” line shows in grey what will be built from verdicts and activity."""
        if self.jday is None:
            self.summary.setPlaceholderText("")
            return
        self.summary.setPlaceholderText(_("will go into the report by itself: {auto}").format(auto=self.jday.auto_summary()))

    def _text_changed(self, what: str) -> None:
        if self._loading or self.jday is None:
            return
        if what == "plans":
            self.jday.plans = self.plans.toPlainText()
        elif what == "other":
            self.jday.other = self.other.toPlainText()
        elif what == "activities":
            self.jday.activities = parse_activity_lines(self.activity.toPlainText()) + self.jday.hidden_activities()  # hidden kinds stay
        else:
            self.jday.columns[what] = parse_lines(self.sections[what].toPlainText())
            self._update_day_counts(self.jday)
        self._update_summary_hint()
        self.shell.mark_dirty("journal")

    def _quick_add_activity(self, kind: str) -> None:
        if self.jday is None:
            self._add_today()
        if self.jday is None:
            return
        raw = self.quick.text()
        pieces = [x for x in raw.replace(",", " ").split() if x] if current_layout().role(kind) == ROLE_MR else [raw]
        added = [x for x in pieces if self.jday.add_activity(kind, x)]
        if not added:
            self.shell.set_status(_("Nothing to add: empty or already there"))
            return
        self.quick.setText("")
        self._load_day(self.jday)
        self.shell.mark_dirty("journal")
        self.shell.set_status(_("Added: {items}").format(items=", ".join(added)))

    def _quick_add(self, column: str) -> None:
        if self.jday is None:
            self._add_today()
        if self.jday is None:
            return
        raw = self.quick.text()
        added = [x for x in parse_lines(raw.replace(",", "\n").replace(" ", "\n")) if self.jday.add(column, x)]
        if not added:
            self.shell.set_status(_("Nothing to add: empty or already in this section"))
            return
        self.quick.setText("")
        self._load_day(self.jday)
        self._update_day_counts(self.jday)
        self.shell.mark_dirty("journal")
        self.shell.set_status(_("Added: {items}").format(items=", ".join(added)))

    # ---- the report

    def report_dialog(self) -> Dialog | None:
        if self.vault is None:
            return None
        if self.jday is None:
            self.shell.show_page("journal")
        if self.jday is None:
            return None
        self.shell.save_now()
        text = build_report(self.jday)
        self.shell.copy_to_clipboard(text, secret=False)
        dlg = Dialog(self.shell, _("Report for {day}").format(day=fmt_day(self.jday.date)))
        head = label(_("Report copied to the clipboard"), "", dlg)
        head.setFont(theme.font(13, bold=True))
        dlg.box.addWidget(head, 0, 0)
        view = QPlainTextEdit(dlg)  # editable: a word fixed here goes out with "Copy", the day stays as it was
        view.setFont(theme.font(10, mono=True))
        view.setPlainText(text)
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        fm = view.fontMetrics()
        view.setMinimumWidth(fm.averageCharWidth() * 78 + 24)
        view.setMinimumHeight(fm.lineSpacing() * min(32, text.count("\n") + 3) + 16)
        dlg.box.addWidget(view, 1, 0)
        row = QHBoxLayout()
        row.addStretch(1)
        copy = button(_("Copy"), "primary", dlg)

        def copy_again() -> None:
            self.shell.copy_to_clipboard(view.toPlainText(), secret=False)
            self.shell.set_status(_("Report copied to the clipboard"))

        copy.clicked.connect(copy_again)
        close = button(_("Close"), "", dlg)
        close.clicked.connect(dlg.reject)
        row.addWidget(copy)
        row.addWidget(close)
        dlg.box.addLayout(row, 2, 0)
        dlg.open()
        self.shell.set_status(_("Report copied to the clipboard"))
        self.last_dialog = dlg
        self.report_view = view
        return dlg
