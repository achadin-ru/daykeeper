"""The dialogs that move data in and out of the vault as readable files (data_dialogs of the
Tk window): the import of KEY=VALUE files, the export of the entries (text, CSV) or of
everything (JSON) after the master password, and the import of everything back.

Each returns the dialog it opened (None when it ended before one). `source` / `target`
stand in for the file dialogs (the tests pass them)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import shiboken6
from PySide6.QtWidgets import QButtonGroup, QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit, QRadioButton, QVBoxLayout, QWidget

from ...envimport import apply_plan, describe_plan, own_rules, parse_env, plan_import
from ...i18n import _
from .. import theme
from ..widgets.secret_edit import SecretEdit
from .base import Dialog, button, label, show_error, show_info


def _heading(text: str, parent: QWidget) -> QLabel:
    head = QLabel(text, parent)
    head.setFont(theme.font(13, bold=True))
    return head


def import_env_dialog(shell: Any, source: str | None = None) -> Dialog | None:
    """Import KEY=VALUE from a file: the plan is shown before writing, the main list is untouched."""
    if shell.vault is None:
        return None
    shell.save_now()
    if source is None:
        source, _f = QFileDialog.getOpenFileName(shell, _("File with variables (KEY=VALUE)"), "", _("Variable files") + " (*.env .env* *.txt *.properties);;" + _("All files") + " (*.*)")
    if not source or shell.vault is None:  # the idle lock does not wait for a file dialog: the window may have locked under it
        return None
    try:
        text = Path(source).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        show_error(shell, _("Could not read the file:\n{error}").format(error=exc))
        return None
    plan = plan_import(parse_env(text), own_rules(shell.session.settings()))
    if not plan:
        show_info(shell, _("The file has no KEY=VALUE line with a non-empty value."))
        return None
    dlg = Dialog(shell, _("Import from a .env file"))
    total = sum(len(i.fields) for i in plan)
    dlg.box.addWidget(_heading(_("Variables found: {fields}, entries: {entries}").format(fields=total, entries=len(plan)), dlg), 0, 0)
    dlg.box.addWidget(label(_("Keys are filed into the lists “Stands”, “Brokers”, “Integrations”, “Miscellaneous”.\nThe main list is not changed. An entry with the same name is extended, not duplicated.\nValues are not shown below — only variable names; 🔒 — the field will be hidden."), "muted", dlg), 1, 0)
    preview = QPlainTextEdit(dlg)
    preview.setReadOnly(True)
    preview.setFont(theme.font(10, mono=True))
    preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    preview.setPlainText(describe_plan(plan))
    fm = preview.fontMetrics()
    preview.setMinimumSize(fm.averageCharWidth() * 90 + 20, fm.lineSpacing() * 22 + 16)
    dlg.box.addWidget(preview, 2, 0)
    err = label("", "error", dlg)
    dlg.box.addWidget(err, 3, 0)

    def apply() -> None:
        try:
            result = apply_plan(shell.vault, plan)
        except ValueError as exc:
            err.setText(str(exc))
            return
        dlg.accept()
        shell.page("vault").reload()
        shell.set_status(_("Import: {summary}").format(summary=result.summary()))

    row = QHBoxLayout()
    row.addStretch(1)
    go = button(_("Import"), "primary", dlg)
    go.clicked.connect(apply)
    cancel = button(_("Cancel"), "", dlg)
    cancel.clicked.connect(dlg.reject)
    row.addWidget(go)
    row.addWidget(cancel)
    dlg.box.addLayout(row, 4, 0)
    dlg.open()
    dlg.apply = apply  # type: ignore[attr-defined]  # tests press it here
    return dlg


def export_dialog(shell: Any, target: str | None = None) -> Dialog | None:
    """Export to an unencrypted file — the entries as text or CSV, or everything as JSON —
    after re-entering the master password."""
    if shell.vault is None:
        return None
    shell.save_now()
    dlg = Dialog(shell, _("Export to a readable file"))
    dlg.box.addWidget(_heading(_("Export all entries"), dlg), 0, 0, 1, 2)
    dlg.box.addWidget(label(_("The file will NOT be encrypted: every password and token in it is plain text.\nDelete it as soon as it is no longer needed."), "muted", dlg), 1, 0, 1, 2)
    dlg.box.addWidget(QLabel(_("Format"), dlg), 2, 0)
    formats = QWidget(dlg)
    fl = QVBoxLayout(formats)
    fl.setContentsMargins(0, 0, 0, 0)
    group = QButtonGroup(dlg)
    for code, text in (("txt", _("Text (.txt) — to read")), ("csv", _("Table (.csv) — to open in Excel")), ("json", _("Everything (.json) — every tab, to keep or to move; imports back"))):
        radio = QRadioButton(text, formats)
        radio.setProperty("code", code)
        radio.setChecked(code == "txt")
        group.addButton(radio)
        fl.addWidget(radio)
    dlg.box.addWidget(formats, 2, 1)
    dlg.box.addWidget(QLabel(_("Master password"), dlg), 3, 0)
    pw = SecretEdit(dlg)
    pw.setMinimumWidth(pw.fontMetrics().averageCharWidth() * 30)
    dlg.box.addWidget(pw, 3, 1)
    err = label("", "error", dlg)
    dlg.box.addWidget(err, 4, 0, 1, 2)
    accept = shell.security.limited(shell.session.check_password, _("Wrong master password"))

    def apply() -> None:
        result = accept(pw.text())
        pw.wipe()
        if result is not True:
            err.setText(str(result))
            return
        checked = group.checkedButton()
        fmt = str(checked.property("code")) if checked is not None else "txt"
        path = target
        if path is None:
            stamp = time.strftime("%Y-%m-%d")
            filters = {"txt": _("Text") + " (*.txt)", "csv": "CSV (*.csv)", "json": "JSON (*.json)"}
            path, _f = QFileDialog.getSaveFileName(dlg, _("Where to save the export"), f"daykeeper-{stamp}.{fmt}", filters[fmt])
        if not path or not shiboken6.isValid(dlg) or shell.vault is None:  # locked under the file dialog
            return
        try:
            if fmt == "json":
                shell.session.write_full_export(Path(path))
            else:
                n = shell.session.write_export(fmt, Path(path))
        except OSError as exc:
            err.setText(_("Could not write: {error}").format(error=exc))
            return
        dlg.accept()
        if fmt == "json":
            shell.set_status(_("Exported everything → {path}").format(path=path))
        else:
            shell.set_status(_("Exported entries: {n} → {path}").format(n=n, path=path))

    go = button(_("Export"), "primary", dlg)
    go.clicked.connect(apply)
    dlg.box.addWidget(go, 5, 0, 1, 2)
    dlg.keys(apply)
    pw.setFocus()
    dlg.open()
    dlg.parts = {"password": pw, "formats": group, "error": err, "apply": apply}  # type: ignore[attr-defined]
    return dlg


def import_all_dialog(shell: Any, source: str | None = None) -> Dialog | None:
    """Replace everything in the vault with a JSON export — after the master password. The
    vault as it was is kept beside it; the lock settings stay."""
    if shell.vault is None:
        return None
    shell.save_now()
    if source is None:
        source, _f = QFileDialog.getOpenFileName(shell, _("A Daykeeper export (.json)"), "", "JSON (*.json);;" + _("All files") + " (*.*)")
    if not source or shell.vault is None:  # locked under the file dialog
        return None
    dlg = Dialog(shell, _("Import everything"))
    dlg.box.addWidget(_heading(_("Replace everything in this vault with the file?"), dlg), 0, 0, 1, 2)
    dlg.box.addWidget(label(_("Passwords, bookmarks, the journal, notes, routines and integrations become what the file holds.\nThe vault as it is now is kept beside it as a copy (Settings → Data → Copies). The lock settings stay."), "muted", dlg), 1, 0, 1, 2)
    dlg.box.addWidget(label(str(source), "muted", dlg), 2, 0, 1, 2)
    dlg.box.addWidget(QLabel(_("Master password"), dlg), 3, 0)
    pw = SecretEdit(dlg)
    pw.setMinimumWidth(pw.fontMetrics().averageCharWidth() * 30)
    dlg.box.addWidget(pw, 3, 1)
    err = label("", "error", dlg)
    dlg.box.addWidget(err, 4, 0, 1, 2)
    accept = shell.security.limited(shell.session.check_password, _("Wrong master password"))

    def apply() -> None:
        result = accept(pw.text())
        pw.wipe()
        if result is not True:
            err.setText(str(result))
            return
        try:
            keep = shell.session.import_full(Path(source))
        except (OSError, ValueError, TypeError) as exc:
            err.setText(_("Could not import: {error}").format(error=exc))
            return
        dlg.accept()
        shell.show_main()  # every tab from the new content; undo starts afresh
        shell.set_status(_("Imported everything. The vault as it was: {path}").format(path=keep.name))

    row = QHBoxLayout()
    row.addStretch(1)
    go = button(_("Replace everything"), "danger", dlg)
    go.clicked.connect(apply)
    cancel = button(_("Cancel"), "", dlg)
    cancel.clicked.connect(dlg.reject)
    row.addWidget(go)
    row.addWidget(cancel)
    dlg.box.addLayout(row, 5, 0, 1, 2)
    dlg.keys(apply)
    pw.setFocus()
    dlg.open()
    dlg.parts = {"password": pw, "error": err, "apply": apply}  # type: ignore[attr-defined]
    return dlg
