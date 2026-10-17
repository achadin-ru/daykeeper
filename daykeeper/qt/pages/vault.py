"""The “Passwords” tab of the Qt window: the list of entries on the left, the card with its
fields on the right. The same behaviour as vault_view.VaultPage of the Tk window; the list
is a model over the vault (10 000 entries without a widget per row).

Secrets stay sealed in the vault until they are needed: a secret field shows its value
only while it is revealed (the eye) or being typed into, and an emptied field keeps
nothing (`SecretEdit.wipe`). The guard over the tab hides the list and the card and
empties them; the guard screen is the only thing the keyboard can reach then."""

from __future__ import annotations

import time

from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QObject, QPersistentModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QListView,
    QMenu,
    QPlainTextEdit,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from ...bookmarks import entry_login, entry_secret
from ...clipboard import CLEAR_SEC
from ...fieldicons import summary as field_summary
from ...i18n import _, fmt_time, hotkeys
from ...security import GUARD_PIN
from ...vault import DEFAULT_KIND, KINDS, MAIN_GROUP, Entry, Field, generate_password, main_group_label
from .. import theme
from ..dialogs.base import ask_text, ask_yes_no, button, label, show_info, show_warning
from ..timers import Debounce
from ..widgets.common import divider, fill_menu, icon_button, more_button, popup_below, scroll_area, section
from ..widgets.flow import flow_host
from ..widgets.secret_edit import SecretEdit
from .base import TabPage

# Preset fields for the “+ Add field” button: (menu label key, field name key, secret?).
FIELD_PRESETS: list[tuple[str, str, bool]] = [
    ("🎟  Token", "token", True),
    ("👤  Login", "login", False),
    ("🔑  Password", "password", True),
    ("🌐  Site / URL", "site", False),
    ("✉  Mail", "mail", False),
    ("📞  Phone", "phone", False),
    ("📅  Valid until", "valid until", False),
    ("🔢  PIN code", "PIN", True),
    ("🛡  2FA key", "2FA key", True),
    ("🗝  Private key", "private key", True),
]
MAX_ROW_ICONS = 4  # more fields than this show as “+N”; the tooltip lists them all
ENTRY_KIND_ICONS = {"login": "🔑", "token": "🎟", "key": "🗝", "card": "💳", "note": "📝", "other": "📦"}
SEARCH_SETTLE_MS = 150  # the list is filtered once the typing pauses this long
MASK = "••••••••"
ROW_HEIGHT = 32  # px of a list row
RIGHT_ROLE = Qt.ItemDataRole.UserRole + 1
ENTRY_ROLE = Qt.ItemDataRole.UserRole + 2


def kind_icon(kind: str) -> str:
    return ENTRY_KIND_ICONS.get(kind, ENTRY_KIND_ICONS[DEFAULT_KIND])


class EntryModel(QAbstractListModel):
    """The entries the list shows, in their order. The right cell: icons of the filled
    fields, the kind while none is, or the list a search hit lives in."""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.rows: list[Entry] = []
        self.query = ""
        self.group = MAIN_GROUP

    def set_rows(self, rows: list[Entry], query: str, group: str) -> None:
        self.beginResetModel()
        self.rows, self.query, self.group = rows, query, group
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self.rows)

    def row_of(self, entry: Entry) -> int:
        return next((i for i, e in enumerate(self.rows) if e.id == entry.id), -1)

    def changed(self, entry: Entry) -> None:
        row = self.row_of(entry)
        if row >= 0:
            idx = self.index(row)
            self.dataChanged.emit(idx, idx)

    def right_text(self, e: Entry) -> str:
        if self.query.strip() and e.group != self.group:  # found in another list — say which
            return _("in “{group}”").format(group=e.group or main_group_label())
        icons = field_summary(e.fields)[0].split()  # what is inside, not the kind it started as
        if not icons:
            return e.kind_label
        return " ".join(icons[:MAX_ROW_ICONS]) + (f" +{len(icons) - MAX_ROW_ICONS}" if len(icons) > MAX_ROW_ICONS else "")

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        e = self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return f"{kind_icon(e.kind)}  {e.name or _('(untitled)')}"
        if role == RIGHT_ROLE:
            return self.right_text(e)
        if role == ENTRY_ROLE:
            return e
        if role == Qt.ItemDataRole.ToolTipRole:
            names = field_summary(e.fields)[1]
            return (e.name or _("(untitled)")) + "\n" + e.kind_label + "\n" + (_("Fields: {names}").format(names=", ".join(names)) if names else _("No filled fields"))
        return None


class EntryDelegate(QStyledItemDelegate):
    """A row: the icon and the name on the left, the right cell muted at the right edge."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        t = theme.current()
        painter.save()
        r = option.rect
        state = option.state
        if state & QStyle.StateFlag.State_Selected:
            painter.fillRect(r, QColor(t.ACCENT_SOFT))
        elif state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(r, QColor(t.HOVER))
        fm = option.fontMetrics
        right = str(index.data(RIGHT_ROLE) or "")
        right_w = min(fm.horizontalAdvance(right) + 12, r.width() // 2)
        painter.setPen(QColor(t.MUTED))
        painter.drawText(QRect(r.right() - right_w - 6, r.top(), right_w, r.height()), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight), fm.elidedText(right, Qt.TextElideMode.ElideRight, right_w))
        painter.setPen(QColor(t.TEXT))
        name_rect = QRect(r.left() + 8, r.top(), r.width() - right_w - 20, r.height())
        painter.drawText(name_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), fm.elidedText(str(index.data() or ""), Qt.TextElideMode.ElideRight, name_rect.width()))
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        return QSize(200, ROW_HEIGHT)


class FieldRow(QWidget):
    """One "name — value" line of the card: the name, the value, show, copy, generate,
    secret, delete. A secret value is put into the field only while it is revealed or
    being typed into; otherwise the field is empty and shows a mask."""

    changed = Signal()

    def __init__(self, page: VaultPage, f: Field, parent: QWidget):
        super().__init__(parent)
        self.page, self.f = page, f
        self.revealed = False
        self.key = QLineEdit(f.key, self)
        self.key.setProperty("role", "key")
        self.key.setFixedWidth(self.key.fontMetrics().averageCharWidth() * 15 + 20)
        self.value = SecretEdit(self)
        self.value.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu if not f.secret else Qt.ContextMenuPolicy.NoContextMenu)
        self.value.installEventFilter(self)
        self.eye = icon_button("👁", _("Show / hide value"), self.toggle_reveal, self)
        tip = _("To the clipboard; cleared after {n} s").format(n=CLEAR_SEC) + ("\n" + hotkeys(_("Ctrl+Shift+C copies the password of the entry, Ctrl+B its login")) if f.secret else "")
        self.copy = button(_("Copy"), "small", self)
        self.copy.setToolTip(tip)
        self.copy.clicked.connect(self.copy_value)
        self.gen = icon_button("🎲", _("Generate password"), self.generate, self)
        self.lock = icon_button("🔒", _("Secret: show as dots, do not search by value"), None, self)
        self.lock.setCheckable(True)
        self.lock.setChecked(f.secret)
        self.lock.toggled.connect(self.set_secret)
        self.rm = icon_button("✕", _("Delete field"), lambda: page.remove_field(f), self)
        self.key.textEdited.connect(self._key_edited)
        self.value.textEdited.connect(self._value_edited)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 2, 0, 2)
        self.grid.setHorizontalSpacing(4)
        self.grid.setVerticalSpacing(2)
        self.compact: bool | None = None
        self.set_compact(False)
        self.show_value()

    # ---- layout: one line, or the name above the value on a narrow card

    def set_compact(self, compact: bool) -> None:
        if compact == self.compact:
            return
        self.compact = compact
        for w in (self.key, self.value, self.eye, self.copy, self.gen, self.lock, self.rm):
            self.grid.removeWidget(w)
        line = 1 if compact else 0
        if compact:
            self.grid.addWidget(self.key, 0, 0, 1, 6, Qt.AlignmentFlag.AlignLeft)
            self.grid.addWidget(self.value, 1, 0)
            self.copy.setText("⧉")
        else:
            self.grid.addWidget(self.key, 0, 0)
            self.grid.addWidget(self.value, 0, 1)
            self.copy.setText(_("Copy"))
        col = 1 if compact else 2
        for i, b in enumerate((self.eye, self.copy, self.gen, self.lock, self.rm)):
            self.grid.addWidget(b, line, col + i)
        self.grid.setColumnStretch(0, 1 if compact else 0)
        self.grid.setColumnStretch(1, 0 if compact else 1)

    def one_line_width(self) -> int:
        """The width the row takes on one line with a usable value field."""
        widths = [self.key.width(), self.value.fontMetrics().averageCharWidth() * 18]
        widths += [w.sizeHint().width() for w in (self.eye, self.copy, self.gen, self.lock, self.rm)]
        return sum(widths) + 4 * 7

    # ---- the value

    def show_value(self) -> None:
        """What the field holds now: the value, or nothing and a mask (see the class)."""
        f = self.f
        if f.unreadable:
            self.value.wipe()
            self.value.setPlaceholderText(_("(unreadable value: restore a copy from Settings → Data)"))
            self.value.set_shown(True)
            return
        if not f.secret:
            self.value.set_shown(True)
            self.value.setPlaceholderText("")
            if self.value.text() != f.value:
                self.value.setText(f.value)
            return
        self.value.set_shown(self.revealed)
        if self.revealed or self.value.hasFocus():
            if self.value.text() != f.value:
                self.value.setText(f.value)
            self.value.setPlaceholderText("")
        else:
            self.value.wipe()
            self.value.setPlaceholderText(MASK if f.filled else "")

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.value and self.f.secret and event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self.show_value()  # typed into: the value comes in; left: it goes again
        return False

    def toggle_reveal(self) -> None:
        if not self.f.secret:
            return
        self.revealed = not self.revealed
        self.show_value()

    def set_secret(self, on: bool) -> None:
        self.f.secret = on
        self.revealed = False
        self.value.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu if on else Qt.ContextMenuPolicy.DefaultContextMenu)
        self.show_value()
        self.changed.emit()

    def generate(self) -> None:
        self.f.value = generate_password()
        self.show_value()
        self.changed.emit()

    def copy_value(self) -> None:
        self.page.shell.copy_to_clipboard(self.f.value)

    def _key_edited(self, text: str) -> None:
        self.f.key = text
        self.changed.emit()

    def _value_edited(self, text: str) -> None:
        self.f.value = text
        self.changed.emit()

    def wipe(self) -> None:
        self.value.wipe()


class FieldsBox(QWidget):
    """The field rows; each goes to two lines when one line no longer fits the card."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.rows: list[FieldRow] = []
        self.col = QVBoxLayout(self)
        self.col.setContentsMargins(0, 0, 0, 0)
        self.col.setSpacing(2)
        self._compact = False

    def clear(self) -> None:
        for row in self.rows:
            row.wipe()
            self.col.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.rows = []

    def add(self, row: FieldRow) -> None:
        self.rows.append(row)
        self.col.addWidget(row)
        row.set_compact(self._compact)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fit()

    def fit(self) -> None:
        if not self.rows:
            return
        compact = self.width() < max(r.one_line_width() for r in self.rows)
        if compact != self._compact:
            self._compact = compact
            for r in self.rows:
                r.set_compact(compact)


class GuardScreen(QWidget):
    """Over the Passwords tab while it is locked: the code field and Unlock. Everything
    under it is hidden and disabled, so Tab, Ctrl+F or Space reach nothing there."""

    def __init__(self, page: VaultPage):
        super().__init__(page)
        self.page = page
        self.setObjectName("Guard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        box = QVBoxLayout()
        box.setSpacing(4)
        icon = label("🔒", "title", self)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = label(_("Passwords are locked"), "title", self)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint = label("", "subtitle", self)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code = SecretEdit(self)
        self.code.setProperty("role", "big")
        self.code.setFixedWidth(self.code.fontMetrics().averageCharWidth() * 28 + 24)
        self.code.returnPressed.connect(self.submit)
        self.error = label("", "error", self)
        self.error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        go = button(_("Unlock"), "primary", self)
        go.clicked.connect(self.submit)
        for w in (icon, self.title, self.hint):
            box.addWidget(w)
        box.addSpacing(10)
        box.addWidget(self.code, 0, Qt.AlignmentFlag.AlignHCenter)
        box.addWidget(self.error)
        box.addSpacing(6)
        box.addWidget(go)
        outer = QVBoxLayout(self)
        outer.addStretch(4)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addLayout(box)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(6)

    def prepare(self) -> None:
        vault = self.page.vault
        pin = vault is not None and vault.security.vault_guard == GUARD_PIN
        self.hint.setText(_("Enter the PIN code to see them") if pin else _("Enter the master password to see them"))
        self.error.setText("")
        self.code.wipe()

    def submit(self) -> None:
        security = self.page.shell.security
        result = security.limited(security.code_matches, _("Wrong code"))(self.code.text())
        self.code.wipe()
        if result is True:
            security.unlock_secrets()
        else:
            self.error.setText(str(result))


class VaultPage(TabPage):
    """The Passwords tab. `current` is the entry on the card, `group` the list shown."""

    name = "vault"

    def build(self) -> None:
        self.current: Entry | None = None
        self.group: str = MAIN_GROUP
        self._loading = False
        self._guarded_id: str | None = None
        self.guarded = False
        self.stack = QStackedWidget(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.stack)
        self.content = QWidget(self.stack)
        self.guard_screen = GuardScreen(self)
        self.stack.addWidget(self.content)
        self.stack.addWidget(self.guard_screen)
        self._build_content(self.content)

    # ---- building

    def _build_content(self, host: QWidget) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal, host)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(splitter)
        left = QFrame(splitter)
        left.setObjectName("Sidebar")
        left.setMinimumWidth(280)
        right = QFrame(splitter)
        right.setObjectName("Card")
        right.setMinimumWidth(360)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([400, 10_000])
        self.splitter = splitter
        self._build_sidebar(left)
        self._build_card(right)
        self._refresh_group_widgets()

    def _build_sidebar(self, left: QFrame) -> None:
        col = QVBoxLayout(left)
        col.setContentsMargins(16, 16, 12, 12)
        col.setSpacing(10)
        row = QHBoxLayout()
        row.addWidget(label(_("List"), "muted", left))
        self.group_box = QComboBox(left)
        self.group_box.activated.connect(self._on_group_select)
        row.addWidget(self.group_box, 1)
        more = more_button([(_("New list…"), self.new_group), (_("Rename list…"), self.rename_group), (_("Delete list"), self.delete_group)], _("Create, rename or delete a list"), left)
        row.addWidget(more)
        col.addLayout(row)

        self.search = QLineEdit(left)
        self.search.setPlaceholderText(hotkeys(_("🔍  Search  (Ctrl+F)")))
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip(hotkeys(_("Enter — the first hit onto the card, ↓ — into the list, Esc — clear.\nCtrl+Shift+C copies the password of the entry on the card, Ctrl+B its login.")))
        self.search.textChanged.connect(self._on_search_change)
        self.search.installEventFilter(self)
        self._search_later = Debounce(self, SEARCH_SETTLE_MS, self.refresh_list)
        col.addWidget(self.search)

        self.model = EntryModel(self)
        self.list = QListView(left)
        self.list.setObjectName("EntryList")
        self.list.setModel(self.model)
        self.list.setItemDelegate(EntryDelegate(self.list))
        self.list.setUniformItemSizes(True)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setMouseTracking(True)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_context_menu)
        self.list.selectionModel().currentChanged.connect(self._on_current_changed)
        self.list.installEventFilter(self)
        col.addWidget(self.list, 1)
        self.empty_hint = label("", "muted", left)
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setWordWrap(True)
        self.empty_hint.hide()
        col.addWidget(self.empty_hint)

        host, flow = flow_host(left, 6)
        self.new_btn = button(_("+ New entry") + "   ▾", "primary", host)
        new_menu = QMenu(self.new_btn)
        for kind, text in KINDS.items():
            new_menu.addAction(f"{ENTRY_KIND_ICONS[kind]}  {_(text)}").triggered.connect(lambda _c=False, k=kind: self.new_entry(k))
        self.new_btn.clicked.connect(lambda: popup_below(new_menu, self.new_btn))
        flow.addWidget(self.new_btn)
        tools = QWidget(host)
        tl = QHBoxLayout(tools)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(2)
        tl.addWidget(icon_button("▲", _("Up  (Alt+↑)"), lambda: self.move_entry(-1), tools))
        tl.addWidget(icon_button("▼", _("Down  (Alt+↓)"), lambda: self.move_entry(+1), tools))
        tl.addWidget(icon_button("🗑", _("Delete"), self.delete_entry, tools, role="danger"))
        flow.addWidget(tools)
        col.addWidget(host)
        self._refresh_group_widgets()

    def _build_card(self, right: QFrame) -> None:
        self.card_stack = QStackedWidget(right)
        lay = QVBoxLayout(right)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.card_stack)
        self.placeholder = label(_("Pick an entry on the left\nor create a new one"), "placeholder", right)
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        area, card, col = scroll_area(right)
        self.card_area = area
        self.card_stack.addWidget(self.placeholder)
        self.card_stack.addWidget(area)

        self.name_edit = QLineEdit(card)
        self.name_edit.setProperty("role", "name")
        self.name_edit.textEdited.connect(self._on_name_change)
        col.addWidget(self.name_edit)
        col.addSpacing(10)
        meta_host, meta = flow_host(card, 12)
        kind = QWidget(meta_host)
        kl = QHBoxLayout(kind)
        kl.setContentsMargins(0, 0, 0, 0)
        kl.addWidget(label(_("Kind"), "muted", kind))
        self.kind_box = QComboBox(kind)
        for k, v in KINDS.items():
            self.kind_box.addItem(f"{ENTRY_KIND_ICONS[k]}  {_(v)}", k)
        self.kind_box.activated.connect(self._on_kind_change)
        kl.addWidget(self.kind_box)
        meta.addWidget(kind)
        grp = QWidget(meta_host)
        gl = QHBoxLayout(grp)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.addWidget(label(_("List"), "muted", grp))
        self.entry_group_box = QComboBox(grp)
        self.entry_group_box.setMinimumContentsLength(14)
        self.entry_group_box.activated.connect(self._on_entry_group_change)
        gl.addWidget(self.entry_group_box)
        meta.addWidget(grp)
        self.updated_label = label("", "muted", meta_host)
        meta.addWidget(self.updated_label)
        col.addWidget(meta_host)
        col.addSpacing(18)
        col.addWidget(divider(card))
        col.addSpacing(14)
        col.addWidget(section(_("FIELDS"), card))
        col.addSpacing(6)
        self.fields = FieldsBox(card)
        col.addWidget(self.fields)
        col.addSpacing(6)
        self.add_field_btn = button(_("+ Add field  ▾"), "link", card)
        self.add_field_btn.setToolTip(_("Token, login, password, site… or a custom field"))
        self.add_field_btn.clicked.connect(self._show_add_field_menu)
        col.addWidget(self.add_field_btn, 0, Qt.AlignmentFlag.AlignLeft)
        col.addSpacing(18)
        col.addWidget(section(_("NOTE"), card))
        col.addSpacing(6)
        self.note = QPlainTextEdit(card)
        self.note.setFixedHeight(self.note.fontMetrics().lineSpacing() * 8 + 20)
        self.note.textChanged.connect(self._on_note_change)
        col.addWidget(self.note)
        col.addStretch(1)

    # ---- keys of the search and the list

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.KeyPress:
            return False
        key = event.key()  # type: ignore[attr-defined]
        if obj is self.search:
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._search_pick_first()
                return True
            if key == Qt.Key.Key_Down:
                self._focus_list()
                return True
            if key == Qt.Key.Key_Escape:
                self._clear_search()
                return True
        elif obj is self.list:
            if key == Qt.Key.Key_Delete:
                self.delete_entry()
                return True
            if key == Qt.Key.Key_Escape:
                self._clear_search()
                return True
        return False

    # ---- what the window asks for

    def open_first(self) -> None:
        self.refresh_list()
        self._load_entry(self.model.rows[0] if self.model.rows else None)
        if self.current is not None:
            self._select_in_list(self.current)
        if self.vault is not None:
            self.shell.set_status(_("Entries: {n}").format(n=len(self.vault.entries)))

    def new_item(self) -> None:
        self.shell.security.require_secrets(self.new_entry)

    def focus_search(self) -> None:
        self.shell.security.require_secrets(self.search.setFocus)

    def cancel_jobs(self) -> None:
        self._search_later.cancel()
        self.fields.clear()

    def guard(self, on: bool) -> None:
        """Behind the guard: the list and the card are emptied, hidden and disabled; the
        guard screen takes the keyboard. `guard(False)` reopens the entry that was shown."""
        if on:
            self._guarded_id = self.current.id if self.current is not None else None
            self._search_later.cancel()
            self.search.setText("")
            self._load_entry(None)
            self.model.set_rows([], "", self.group)
            self.guarded = True
            self.content.setEnabled(False)
            self.guard_screen.prepare()
            self.stack.setCurrentWidget(self.guard_screen)
            if self.shell.mode == self.name:
                self.guard_screen.code.setFocus()
            return
        self.guarded = False
        self.content.setEnabled(True)
        self.stack.setCurrentWidget(self.content)
        self.guard_screen.code.wipe()
        self.refresh_list()
        again = self.vault.entry_by_id(self._guarded_id or "") if self.vault is not None else None
        if again is not None:
            self._load_entry(again)
            self._select_in_list(again)
        else:
            self.open_first()

    def focus_guard(self) -> None:
        if self.guarded:
            self.guard_screen.code.setFocus()

    def select_entry(self, entry: Entry) -> None:
        """Show `entry` on the card, switching to its list first (a bookmark tile's link)."""
        if self.guarded:
            self.shell.security.require_secrets(lambda: self.select_entry(entry))
            return
        if entry.group != self.group:
            self.group = entry.group
            self._refresh_group_widgets()
        self.search.setText("")
        self.refresh_list()
        self._load_entry(entry)
        self._select_in_list(entry)

    def reload(self) -> None:
        """The vault changed under the tab (an import): the lists, the list and the card follow."""
        self._refresh_group_widgets()
        self.refresh_list()
        if self.current is not None:
            self._load_entry(self.current)
        self.edited()

    def edited(self, touch_entry: bool = True) -> None:
        if touch_entry and self.current is not None:
            self.current.updated = time.time()
            self.updated_label.setText(_("Updated {stamp}").format(stamp=fmt_time(self.current.updated)))
            if self._tied_to_bookmark(self.current):
                self.shell.session.touch("bookmarks")
        self.shell.mark_dirty("vault")

    def copy_field(self, kind: str) -> None:
        """Ctrl+Shift+C / Ctrl+B: the password or the login of the entry on the card."""
        if self.current is None or self.guarded:
            return
        value = entry_login(self.current) if kind == "login" else entry_secret(self.current)
        if not value:
            self.shell.set_status(_("This entry has no login") if kind == "login" else _("This entry has no password"))
            return
        self.shell.copy_to_clipboard(value, secret=kind != "login")

    # ---- the list

    def _on_search_change(self, text: str) -> None:
        if text.strip():
            self._search_later.touch()
        else:
            self._search_later.cancel()
            self.refresh_list()

    def _filter_now(self) -> None:
        if self._search_later.pending:
            self._search_later.flush()
        else:
            self.refresh_list()

    def _search_pick_first(self) -> None:
        self._filter_now()
        if self.model.rows:
            self._load_entry(self.model.rows[0])
            self._select_in_list(self.model.rows[0])

    def _focus_list(self) -> None:
        self._filter_now()
        rows = self.model.rows
        if not rows:
            return
        if self.current is None or all(e.id != self.current.id for e in rows):
            self._load_entry(rows[0])
        if self.current is not None:
            self._select_in_list(self.current)
        self.list.setFocus()

    def _clear_search(self) -> None:
        if self.search.text():
            self.search.setText("")
        self.search.setFocus()

    def refresh_list(self) -> None:
        if self.vault is None or self.guarded:
            return
        query = self.search.text()
        self.model.set_rows(self.vault.search(query, self.group), query, self.group)
        if self.current is not None:
            self._select_in_list(self.current)
        if self.model.rows:
            self.empty_hint.hide()
        else:
            self.empty_hint.setText(_("Nothing found") if query.strip() else _("This list is empty.\nCreate an entry or move one here."))
            self.empty_hint.show()

    def _select_in_list(self, entry: Entry) -> None:
        row = self.model.row_of(entry)
        if row < 0:
            return
        self._loading = True
        try:
            idx = self.model.index(row)
            self.list.setCurrentIndex(idx)
            self.list.scrollTo(idx)
        finally:
            self._loading = False

    def _on_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if self._loading or not current.isValid():
            return
        entry = current.data(ENTRY_ROLE)
        if entry is None:
            return
        if self.current is None or entry.id != self.current.id:
            self._load_entry(entry)
        if entry.group != self.group:  # a search hit from another list was picked — switch to that list
            self.group = entry.group
            self._refresh_group_widgets()

    # ---- entries

    def new_entry(self, kind: str = DEFAULT_KIND) -> None:
        if self.vault is None:
            return
        entry = self.vault.add_entry(kind=kind, group=self.group)
        self.current = entry
        self.search.setText("")
        self.refresh_list()
        self._load_entry(entry)
        self._select_in_list(entry)
        self.name_edit.setFocus()
        self.name_edit.selectAll()
        self.edited()

    def delete_entry(self) -> None:
        if self.vault is None or self.current is None or self.guarded:
            return
        if not ask_yes_no(self.shell, _("Delete “{name}”?").format(name=self.current.name)):
            return
        if self._tied_to_bookmark(self.current):
            self.shell.session.touch("bookmarks")
        self.vault.remove_entry(self.current)
        self.current = None
        self.refresh_list()
        first = self.model.rows[0] if self.model.rows else None
        self._load_entry(first)
        if first is not None:
            self._select_in_list(first)
        self.edited()

    def move_entry(self, delta: int) -> None:
        if self.vault is None or self.current is None or self.guarded:
            return
        if self.search.text().strip():
            self.shell.set_status(_("Order changes in the list, not in search results"))
            return
        if self.vault.move_entry(self.current, delta):
            self.refresh_list()
            self.edited()

    # ---- the card

    def _load_entry(self, entry: Entry | None) -> None:
        if entry is not None and self.guarded:
            entry = None  # behind the guard the card shows nothing, whoever asks
        self._loading = True
        try:
            self.current = entry
            self.fields.clear()
            self.note.setPlainText("")
            if entry is None:
                self.name_edit.setText("")
                self.card_stack.setCurrentWidget(self.placeholder)
                return
            self.card_stack.setCurrentWidget(self.card_area)
            self.name_edit.setText(entry.name)
            self.kind_box.setCurrentIndex(max(0, list(KINDS).index(entry.kind) if entry.kind in KINDS else 0))
            self.entry_group_box.setCurrentText(entry.group or main_group_label())
            self.updated_label.setText(_("Updated {stamp}").format(stamp=fmt_time(entry.updated)))
            self.note.setPlainText(entry.note)
            for f in entry.fields:
                self._add_row(f)
            self.card_area.verticalScrollBar().setValue(0)
        finally:
            self._loading = False

    def _add_row(self, f: Field) -> FieldRow:
        row = FieldRow(self, f, self.fields)
        row.changed.connect(self._field_changed)
        self.fields.add(row)
        return row

    def _field_changed(self) -> None:
        if self._loading or self.current is None:
            return
        self.edited()
        self.model.changed(self.current)

    def _show_add_field_menu(self) -> None:
        if self.current is None:
            return
        menu = QMenu(self.add_field_btn)
        for text, key, secret in FIELD_PRESETS:
            menu.addAction(_(text)).triggered.connect(lambda _c=False, k=key, s=secret: self.add_field(_(k), s))
        menu.addSeparator()
        menu.addAction(_("Custom field…")).triggered.connect(lambda: self.add_field())
        popup_below(menu, self.add_field_btn)

    def add_field(self, key: str = "", secret: bool = False) -> None:
        if self.current is None:
            return
        f = Field(key, secret=secret)
        self.current.fields.append(f)
        row = self._add_row(f)
        self.edited()
        (row.value if key else row.key).setFocus()
        bar = self.card_area.verticalScrollBar()
        bar.setValue(bar.maximum())

    def remove_field(self, f: Field) -> None:
        if self.current is None:
            return
        self.current.fields = [x for x in self.current.fields if x is not f]
        self._load_entry(self.current)
        self.edited()
        self.model.changed(self.current)

    def _on_name_change(self, text: str) -> None:
        if self._loading or self.current is None:
            return
        self.current.name = text
        self._name_row(self.current)
        self.edited()

    def _name_row(self, e: Entry) -> None:
        """Only the entry's row says its new name; a search may no longer find it, then the list is filtered again."""
        if self.search.text().strip() or self.model.row_of(e) < 0:
            self.refresh_list()
            return
        self.model.changed(e)

    def _on_kind_change(self, index: int) -> None:
        if self._loading or self.current is None or index < 0:
            return
        self.current.kind = list(KINDS)[index]
        self._name_row(self.current)
        self.edited()

    def _on_entry_group_change(self, _index: int) -> None:
        if self._loading or self.current is None:
            return
        self.set_entry_group(self.current, self._group_from_label(self.entry_group_box.currentText()))

    def set_entry_group(self, entry: Entry, group: str) -> None:
        if self.vault is None or entry.group == group:
            return
        self.vault.set_group(entry, group)
        self.group = group
        self._refresh_group_widgets()
        self.search.setText("")
        self.refresh_list()
        self._select_in_list(entry)
        self.edited()
        self.shell.set_status(_("“{name}” → list “{group}”").format(name=entry.name, group=group or main_group_label()))

    def _on_note_change(self) -> None:
        if self._loading or self.current is None:
            return
        self.current.note = self.note.toPlainText()
        self.edited()

    # ---- lists

    def _group_values(self) -> list[str]:
        return [main_group_label()] + list(self.vault.groups) if self.vault else [main_group_label()]

    def _group_from_label(self, text: str) -> str:
        return MAIN_GROUP if text == main_group_label() else text

    def _refresh_group_widgets(self) -> None:
        values = self._group_values()
        for box in (self.group_box, getattr(self, "entry_group_box", None)):
            if box is None:
                continue
            box.blockSignals(True)
            box.clear()
            box.addItems(values)
            box.blockSignals(False)
        self.group_box.setCurrentText(self.group or main_group_label())
        if self.current is not None:
            self.entry_group_box.setCurrentText(self.current.group or main_group_label())

    def _on_group_select(self, _index: int = 0) -> None:
        self.group = self._group_from_label(self.group_box.currentText())
        self.search.setText("")
        self.current = None
        self.refresh_list()
        first = self.model.rows[0] if self.model.rows else None
        self._load_entry(first)
        if first is not None:
            self._select_in_list(first)

    def new_group(self) -> None:
        if self.vault is None:
            return
        name = ask_text(self.shell, _("New list"), _("List name"))
        if name is None:
            return
        try:
            self.group = self.vault.add_group(name)
        except ValueError as exc:
            show_warning(self.shell, str(exc))
            return
        self._refresh_group_widgets()
        self._on_group_select()
        self.edited(touch_entry=False)

    def rename_group(self) -> None:
        if self.vault is None:
            return
        if self.group == MAIN_GROUP:
            show_info(self.shell, _("The main list cannot be renamed."))
            return
        name = ask_text(self.shell, _("Rename list"), _("New name"), initial=self.group)
        if name is None:
            return
        try:
            self.group = self.vault.rename_group(self.group, name)
        except ValueError as exc:
            show_warning(self.shell, str(exc))
            return
        self._refresh_group_widgets()
        self.edited(touch_entry=False)

    def delete_group(self) -> None:
        if self.vault is None:
            return
        if self.group == MAIN_GROUP:
            show_info(self.shell, _("The main list cannot be deleted."))
            return
        count = len(self.vault.search("", self.group))
        if not ask_yes_no(self.shell, _("Delete list “{name}”?\nEntries in it: {n}. They will not be lost, they move to the main list.").format(name=self.group, n=count)):
            return
        self.vault.backup_now()  # a deleted list cannot be undone after a lock: a copy first
        self.vault.remove_group(self.group)
        self.group = MAIN_GROUP
        self._refresh_group_widgets()
        self._on_group_select()
        self.edited(touch_entry=False)

    # ---- the context menu of a row

    def _show_context_menu(self, pos) -> None:
        idx = self.list.indexAt(pos)
        if not idx.isValid():
            return
        self.list.setCurrentIndex(idx)
        menu = QMenu(self.list)
        fill_menu(menu, [(_("Up\tAlt+↑"), lambda: self.move_entry(-1)), (_("Down\tAlt+↓"), lambda: self.move_entry(+1)), None])
        move_to = menu.addMenu(_("Move to list"))
        for text in self._group_values():
            group = self._group_from_label(text)
            act = move_to.addAction(text)
            act.setEnabled(self.current is None or group != self.current.group)
            act.triggered.connect(lambda _c=False, g=group: self._move_current_to(g))
        move_to.addSeparator()
        move_to.addAction(_("New list…")).triggered.connect(self._move_to_new_group)
        menu.addAction(_("Make a bookmark…")).triggered.connect(self._make_bookmark)
        menu.addSeparator()
        menu.addAction(_("Delete\tDelete")).triggered.connect(self.delete_entry)
        menu.popup(self.list.viewport().mapToGlobal(pos))
        self.context_menu = menu  # tests reach it here

    def _move_current_to(self, group: str) -> None:
        if self.current is not None:
            self.set_entry_group(self.current, group)

    def _make_bookmark(self) -> None:
        if self.current is not None:
            self.shell.page("bookmarks").from_entry(self.current)

    def _move_to_new_group(self) -> None:
        entry = self.current
        if self.vault is None or entry is None:
            return
        name = ask_text(self.shell, _("New list"), _("List name"))
        if name is None:
            return
        try:
            group = self.vault.add_group(name)
        except ValueError as exc:
            show_warning(self.shell, str(exc))
            return
        self._refresh_group_widgets()
        self.set_entry_group(entry, group)

    def _tied_to_bookmark(self, entry: Entry) -> bool:
        return self.vault is not None and any(b.entry_id == entry.id for b in self.vault.bookmarks.items)

