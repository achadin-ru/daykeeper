"""Modal dialogs in the app's look: the window itself (centred on its parent, Return and
Escape), a one-field question, a code or password prompt, and the message boxes with the
app's title and the buttons in the UI language.

Every dialog registers with the main window (`register_child`), which closes it on lock:
a dialog left open would show what the vault holds over the unlock screen."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QWidget

from ... import palette, win32
from ...i18n import _
from .. import theme
from ..widgets.secret_edit import SecretEdit
from ..errors import guard_events


def register(window: QWidget | None, child: QWidget) -> None:
    """Tell the main window about a window that must close on lock (see the module text)."""
    owner = window.window() if window is not None else None
    hook = getattr(owner, "register_child", None)
    if hook is not None:
        hook(child)


def paint_caption(window: QWidget) -> None:
    """The Windows 11 title bar in the theme's colours; nothing elsewhere."""
    t = theme.current()
    win32.caption_colour(int(window.winId()), t.SIDEBAR, t.TEXT)


def button(text: str, role: str = "", parent: QWidget | None = None) -> QPushButton:
    """A push button of the app's look: role "primary", "link", "danger" or plain."""
    b = QPushButton(text, parent)
    if role:
        b.setProperty("role", role)
    b.setAutoDefault(False)  # Return belongs to the dialog's own `ok`, not to whichever button has the focus
    return b


def label(text: str, role: str = "", parent: QWidget | None = None) -> QLabel:
    lab = QLabel(text, parent)
    if role:
        lab.setProperty("role", role)
    return lab


@guard_events
class Dialog(QDialog):
    """A modal window over `parent` with a grid `box` to build in. `keys(ok)` makes
    Return run `ok` (Escape always closes); `editor_buttons` makes the Delete / Save /
    Cancel row of an editor."""

    def __init__(self, parent: QWidget | None, title: str):
        super().__init__(parent)
        # closed is gone: a dialog kept hidden would hold what it showed (a password typed
        # into an editor) and slow every later restyle of the app (the theme switch)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.box = QGridLayout(self)
        self.box.setContentsMargins(24, 24, 24, 24)
        self.box.setHorizontalSpacing(12)
        self.box.setVerticalSpacing(6)
        self._ok: Callable[[], object] | None = None
        self._centred = False
        register(parent, self)

    def keys(self, ok: Callable[[], object] | None = None) -> None:
        self._ok = ok

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._ok is not None:
            self._ok()
            return
        super().keyPressEvent(event)  # Escape → reject

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._centred:  # once: a dialog the user moved stays where it was put
            self._centred = True
            # the width is fixed, the height follows the content (see event)
            self.setFixedWidth(self.sizeHint().width())
            parent = self.parentWidget()
            if parent is not None:
                p = parent.window().frameGeometry()
                self.move(p.x() + (p.width() - self.width()) // 2, p.y() + max(0, (p.height() - self.height()) // 3))
            paint_caption(self)

    def event(self, event: QEvent) -> bool:
        done = super().event(event)
        if event.type() == QEvent.Type.LayoutRequest and self._centred:
            # an error line set after the dialog opened may wrap onto more lines (Russian
            # texts do): the dialog grows to show it instead of cutting it
            need = self.box.totalHeightForWidth(self.width()) if self.box.hasHeightForWidth() else self.box.totalMinimumSize().height()
            if need > self.height():
                self.resize(self.width(), need)
        return done

    def on_lock(self) -> None:
        """The window locks: nothing of the dialog's own to keep; `close` follows."""

    def editor_buttons(self, row: int, columnspan: int, save: Callable[[], object], delete: Callable[[], object] | None = None) -> QHBoxLayout:
        """Delete (when given), Save and Cancel in the bottom-right corner of the box."""
        row_box = QHBoxLayout()
        row_box.addStretch(1)
        if delete is not None:
            b = button(_("Delete"), "danger", self)
            b.clicked.connect(delete)
            row_box.addWidget(b)
            row_box.addSpacing(24)
        save_btn = button(_("Save"), "primary", self)
        save_btn.clicked.connect(save)
        cancel = button(_("Cancel"), "", self)
        cancel.clicked.connect(self.reject)
        row_box.addWidget(save_btn)
        row_box.addWidget(cancel)
        self.box.addLayout(row_box, row, 0, 1, columnspan)
        return row_box


def ask_text(parent: QWidget | None, title: str, prompt: str, initial: str = "") -> str | None:
    """A small dialog with one field. None — cancelled."""
    dlg = Dialog(parent, title)
    dlg.box.addWidget(label(prompt, "", dlg), 0, 0)
    entry = QLineEdit(initial, dlg)
    entry.setMinimumWidth(entry.fontMetrics().averageCharWidth() * 34)
    entry.selectAll()
    dlg.box.addWidget(entry, 1, 0)
    row = QHBoxLayout()
    row.addStretch(1)
    done = button(_("Done"), "primary", dlg)
    cancel = button(_("Cancel"), "", dlg)
    done.clicked.connect(dlg.accept)
    cancel.clicked.connect(dlg.reject)
    row.addWidget(done)
    row.addWidget(cancel)
    dlg.box.addLayout(row, 2, 0)
    dlg.keys(dlg.accept)
    entry.setFocus()
    answer: list[str] = []
    dlg.accepted.connect(lambda: answer.append(entry.text()))  # read before the dialog goes
    dlg.exec()
    return answer[0] if answer else None


def ask_secret(parent: QWidget | None, title: str, prompt: str, button_text: str, accept: Callable[[str], bool | str], then: Callable[[], object]) -> Dialog:
    """A code or password prompt: `accept(code)` is True when it is right, else the text to
    show (and the field is wiped); `then()` runs once the dialog has closed on a right one.
    Not blocking, as in the Tk window: the dialog is returned at once."""
    dlg = Dialog(parent, title)
    dlg.box.addWidget(label(prompt, "", dlg), 0, 0)
    code = SecretEdit(dlg)
    code.setMinimumWidth(code.fontMetrics().averageCharWidth() * 26)
    dlg.box.addWidget(code, 0, 1)
    err = label("", "error", dlg)
    err.setWordWrap(True)
    dlg.box.addWidget(err, 1, 0, 1, 2)
    go = button(button_text, "primary", dlg)
    dlg.box.addWidget(go, 2, 0, 1, 2)

    def submit() -> None:
        result = accept(code.text())
        if result is not True:
            err.setText(str(result))
            code.wipe()
            return
        code.wipe()
        dlg.accept()
        then()

    go.clicked.connect(submit)
    dlg.keys(submit)
    dlg.finished.connect(code.wipe)  # closed any way, the field forgets what was typed
    code.setFocus()
    dlg.open()
    return dlg


# ---- message boxes with the app's title

def _box(parent: QWidget | None, icon: QMessageBox.Icon, text: str, buttons: QMessageBox.StandardButton) -> QMessageBox:
    box = QMessageBox(icon, palette.APP_TITLE, text, buttons, parent)
    box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    names = {
        QMessageBox.StandardButton.Ok: _("OK"),
        QMessageBox.StandardButton.Yes: _("Yes"),
        QMessageBox.StandardButton.No: _("No"),
    }
    for which, name in names.items():
        b = box.button(which)
        if b is not None:
            b.setText(name)
    register(parent, box)
    return box


def ask_yes_no(parent: QWidget | None, text: str) -> bool:
    box = _box(parent, QMessageBox.Icon.Question, text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    return box.exec() == QMessageBox.StandardButton.Yes


def show_info(parent: QWidget | None, text: str) -> None:
    _box(parent, QMessageBox.Icon.Information, text, QMessageBox.StandardButton.Ok).exec()


def show_warning(parent: QWidget | None, text: str) -> None:
    _box(parent, QMessageBox.Icon.Warning, text, QMessageBox.StandardButton.Ok).exec()


def show_error(parent: QWidget | None, text: str) -> None:
    _box(parent, QMessageBox.Icon.Critical, text, QMessageBox.StandardButton.Ok).exec()
