"""The dialogs that move data in and out of the vault as readable files: the import of
KEY=VALUE files, the export of the entries (text, CSV) or of everything (JSON) after the
master password, and the import of everything back from such a JSON.

Each returns the dialog it opened (None when it ended before one): the settings window
that started it opens again when it closes."""

from __future__ import annotations

import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from . import theme
from .dialogs import BULLET, Dialog, show_error, show_info
from .envimport import apply_plan, describe_plan, own_rules, parse_env, plan_import
from .i18n import _
from .palette import BG, FONT, MONO, TEXT, pt


def import_env_dialog(app: tk.Tk) -> Dialog | None:
    """Import KEY=VALUE from a file: the plan is shown before writing, the main list is untouched."""
    if app.vault is None:
        return None
    app.save_now()
    source = filedialog.askopenfilename(
        parent=app,
        title=_("File with variables (KEY=VALUE)"),
        filetypes=[(_("Variable files"), "*.env .env* *.txt *.properties"), (_("All files"), "*.*")],
    )
    if not source:
        return None
    try:
        text = Path(source).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        show_error(app, _("Could not read the file:\n{error}").format(error=exc))
        return None
    plan = plan_import(parse_env(text), own_rules(app.session.settings()))
    if not plan:
        show_info(app, _("The file has no KEY=VALUE line with a non-empty value."))
        return None

    dlg = Dialog(app, _("Import from a .env file"))
    box = dlg.box
    total_fields = sum(len(i.fields) for i in plan)
    ttk.Label(box, text=_("Variables found: {fields}, entries: {entries}").format(fields=total_fields, entries=len(plan)), font=(FONT, pt(13), "bold")).grid(
        row=0, column=0, sticky="w"
    )
    ttk.Label(
        box,
        text=_("Keys are filed into the lists “Stands”, “Brokers”, “Integrations”, “Miscellaneous”.\nThe main list is not changed. An entry with the same name is extended, not duplicated.\nValues are not shown below — only variable names; 🔒 — the field will be hidden."),
        style="Muted.TLabel",
        justify="left",
    ).grid(row=1, column=0, sticky="w", pady=(4, 10))
    preview = theme.RoundText(box, app, outer=BG, width=90, height=22, font=(MONO, pt(10)), wrap="none", fg=TEXT, padx=8, pady=6)
    preview.insert("1.0", describe_plan(plan))
    preview.config(state="disabled")
    preview.grid(row=2, column=0, sticky="nsew")
    err = ttk.Label(box, text="", style="Error.TLabel")
    err.grid(row=3, column=0)

    def apply():
        try:
            result = apply_plan(app.vault, plan)
        except ValueError as exc:
            err.config(text=str(exc))
            return
        dlg.destroy()
        app.pages["vault"].reload()
        app.set_status(_("Import: {summary}").format(summary=result.summary()))

    row = ttk.Frame(box)
    row.grid(row=4, column=0, sticky="ew", pady=(12, 0))
    ttk.Button(row, text=_("Cancel"), command=dlg.destroy).pack(side="right")
    ttk.Button(row, text=_("Import"), style="Primary.TButton", command=apply).pack(side="right", padx=(0, 8))
    dlg.keys()
    return dlg

def export_dialog(app: tk.Tk) -> Dialog | None:
    """Export to an unencrypted file — the entries as text or CSV, or everything as JSON —
    after re-entering the master password."""
    if app.vault is None:
        return None
    app.save_now()
    dlg = Dialog(app, _("Export to a readable file"))
    box = dlg.box
    ttk.Label(box, text=_("Export all entries"), font=(FONT, pt(13), "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
    ttk.Label(
        box,
        text=_("The file will NOT be encrypted: every password and token in it is plain text.\nDelete it as soon as it is no longer needed."),
        style="Muted.TLabel",
        justify="left",
    ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 14))

    ttk.Label(box, text=_("Format")).grid(row=2, column=0, sticky="w", padx=(0, 12))
    fmt_var = tk.StringVar(value="txt")
    fmt_row = ttk.Frame(box)
    fmt_row.grid(row=2, column=1, sticky="w")
    ttk.Radiobutton(fmt_row, text=_("Text (.txt) — to read"), variable=fmt_var, value="txt").pack(anchor="w")
    ttk.Radiobutton(fmt_row, text=_("Table (.csv) — to open in Excel"), variable=fmt_var, value="csv").pack(anchor="w")
    ttk.Radiobutton(fmt_row, text=_("Everything (.json) — every tab, to keep or to move; imports back"), variable=fmt_var, value="json").pack(anchor="w")

    ttk.Label(box, text=_("Master password")).grid(row=3, column=0, sticky="w", padx=(0, 12), pady=(12, 0))
    pw = ttk.Entry(box, show=BULLET, width=30)
    pw.grid(row=3, column=1, pady=(12, 0), sticky="ew")
    err = ttk.Label(box, text="", style="Error.TLabel")
    err.grid(row=4, column=0, columnspan=2)

    accept = app.security.limited(app.session.check_password, _("Wrong master password"))

    def apply():
        result = accept(pw.get())
        if result is not True:
            err.config(text=result)
            pw.delete(0, "end")
            return
        fmt = fmt_var.get()
        stamp = time.strftime("%Y-%m-%d")
        dlg.grab_release()
        target = filedialog.asksaveasfilename(
            parent=dlg,
            title=_("Where to save the export"),
            defaultextension=f".{fmt}",
            initialfile=f"daykeeper-{stamp}.{fmt}",
            filetypes={"txt": [(_("Text"), "*.txt")], "csv": [("CSV", "*.csv")], "json": [("JSON", "*.json")]}[fmt],
        )
        if not target:
            dlg.grab_set()
            return
        try:
            if fmt == "json":
                app.session.write_full_export(Path(target))
            else:
                n = app.session.write_export(fmt, Path(target))
        except OSError as exc:
            dlg.grab_set()
            err.config(text=_("Could not write: {error}").format(error=exc))
            return
        dlg.destroy()
        if fmt == "json":
            app.set_status(_("Exported everything → {path}").format(path=target))
        else:
            app.set_status(_("Exported entries: {n} → {path}").format(n=n, path=target))

    ttk.Button(box, text=_("Export"), style="Primary.TButton", command=apply).grid(
        row=5, column=0, columnspan=2, pady=(14, 0), sticky="ew"
    )
    dlg.keys(lambda _e: apply(), escape=False)
    pw.focus_set()
    return dlg


def import_all_dialog(app: tk.Tk) -> Dialog | None:
    """Replace everything in the vault with a JSON export — after the master password. The
    vault as it was is kept beside it; the lock settings stay."""
    if app.vault is None:
        return None
    app.save_now()
    source = filedialog.askopenfilename(parent=app, title=_("A Daykeeper export (.json)"), filetypes=[("JSON", "*.json"), (_("All files"), "*.*")])
    if not source:
        return None
    dlg = Dialog(app, _("Import everything"))
    box = dlg.box
    ttk.Label(box, text=_("Replace everything in this vault with the file?"), font=(FONT, pt(13), "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
    ttk.Label(
        box,
        text=_("Passwords, bookmarks, the journal, notes, routines and integrations become what the file holds.\nThe vault as it is now is kept beside it as a copy (Settings → Data → Copies). The lock settings stay."),
        style="Muted.TLabel", justify="left",
    ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 6))
    ttk.Label(box, text=str(source), style="Muted.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 14))
    ttk.Label(box, text=_("Master password")).grid(row=3, column=0, sticky="w", padx=(0, 12))
    pw = ttk.Entry(box, show=BULLET, width=30)
    pw.grid(row=3, column=1, sticky="ew")
    err = ttk.Label(box, text="", style="Error.TLabel")
    err.grid(row=4, column=0, columnspan=2)
    accept = app.security.limited(app.session.check_password, _("Wrong master password"))

    def apply(_e=None):
        result = accept(pw.get())
        if result is not True:
            err.config(text=result)
            pw.delete(0, "end")
            return
        try:
            keep = app.session.import_full(Path(source))
        except (OSError, ValueError, TypeError) as exc:  # json.JSONDecodeError is a ValueError; TypeError — a shape no parser expected
            err.config(text=_("Could not import: {error}").format(error=exc))
            return
        dlg.destroy()
        app.show_main()  # every tab from the new content; undo starts afresh
        app.set_status(_("Imported everything. The vault as it was: {path}").format(path=keep.name))

    row = ttk.Frame(box)
    row.grid(row=5, column=0, columnspan=2, sticky="e", pady=(14, 0))
    ttk.Button(row, text=_("Cancel"), command=dlg.destroy).pack(side="right")
    ttk.Button(row, text=_("Replace everything"), style="Danger.TButton", command=apply).pack(side="right", padx=(0, 8))
    dlg.keys(apply)
    pw.focus_set()
    return dlg
