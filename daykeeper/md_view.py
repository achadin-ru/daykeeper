"""Built-in viewer for files attached to notes.

Markdown is rendered (headings, lists, task boxes, quotes, code, tables, links); other
text files are shown as they are. “Edit” switches to the raw text and “Save” writes it
back; a click on a task box in the preview flips it in the file right away. The file is
re-read on every open and on “Reload”, so the viewer always shows its current version."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk

from . import palette
from .dialogs import ask_yes_no, show_error
from .i18n import MAC, _
from .markdown import Block, parse, table_lines, toggle_task
from .widgets import Tooltip, make_fonts, divider

MD_SUFFIXES = {".md", ".markdown", ".mdown"}
# a link in a document opens with the system only when it is a document of a known kind;
# anything else — a program, a script, an Office file with macros, a disk image, a type a
# handler was registered for — is shown in its folder. A block list missed too much.
VIEWER_OPENS = MD_SUFFIXES | {".txt", ".log", ".csv", ".json", ".yaml", ".yml", ".xml", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg",
                              ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".rtf", ".mp3", ".mp4", ".wav", ".zip"}


def link_action(target: Path) -> str:
    """What a click on a local link does: "view" (the built-in viewer), "open" (the system
    app), "folder" (shown in its folder, not opened) or "" (nothing there)."""
    suffix = target.suffix.lower()
    if target.is_file() and suffix in MD_SUFFIXES | {".txt"}:
        return "view"
    if not target.exists():
        return ""
    return "open" if target.is_file() and suffix in VIEWER_OPENS else "folder"


def open_with_system(path: str | os.PathLike) -> None:
    """Open a file or folder in the app the system associates with it."""
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
    else:
        subprocess.Popen(["open" if MAC else "xdg-open", str(path)])  # noqa: S603, S607


def show_in_folder(path: str | os.PathLike) -> None:
    """Open the folder of the file with the file selected where the system allows it."""
    path = Path(path)
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])  # noqa: S603, S607
    elif MAC:
        subprocess.Popen(["open", "-R", str(path)])  # noqa: S603, S607
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])  # noqa: S603, S607


def read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1251", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


class FileViewer(tk.Toplevel):
    """One window per file."""

    closes_on_lock = True  # App.close_child_windows

    def __init__(self, master: tk.Misc, path: str | os.PathLike):
        super().__init__(master)
        self.path = Path(path)
        self.is_md = self.path.suffix.lower() in MD_SUFFIXES
        self.source = ""
        self.editing = False
        self.title(f"{self.path.name} — {palette.APP_TITLE}")
        self.configure(bg=palette.CARD)
        self.geometry("860x760")
        self.minsize(520, 360)

        base = palette.FONT
        self.fonts = make_fonts({
            "body": (11,), "bold": (11, "bold"), "italic": (11, "italic"), "bold_italic": (11, "bold", "italic"),
            "code": (10, "mono"), "code_bold": (10, "mono", "bold"),
            "h1": (20, "bold"), "h2": (16, "bold"), "h3": (13, "bold"), "h4": (12, "bold"), "box": (13,),
        })

        bar = tk.Frame(self, bg=palette.CARD, padx=16, pady=10)
        bar.pack(fill="x")
        tk.Label(bar, text=("📝 " if self.is_md else "📄 ") + self.path.name, bg=palette.CARD, fg=palette.TEXT, font=(base, 13, "bold")).pack(side="left")
        self.edit_btn = ttk.Button(bar, text=_("Edit"), style="Small.TButton", command=self.toggle_edit)
        self.save_btn = ttk.Button(bar, text=_("Save"), style="Primary.TButton", command=self.save)
        for widget, tip in (
            (ttk.Button(bar, text="↗", style="Icon.TButton", command=lambda: open_with_system(self.path)), _("Open in the system app")),
            (ttk.Button(bar, text="📁", style="Icon.TButton", command=lambda: show_in_folder(self.path)), _("Show in folder")),
            (ttk.Button(bar, text="⟳", style="Icon.TButton", command=self.reload), _("Reload from disk")),
        ):
            widget.pack(side="right", padx=(4, 0))
            Tooltip(widget, tip)
        self.edit_btn.pack(side="right", padx=(8, 0))
        tk.Label(self, text=str(self.path), bg=palette.CARD, fg=palette.MUTED, font=(base, 9), anchor="w", padx=16).pack(fill="x")
        divider(self).pack(fill="x", pady=(8, 0))

        body = tk.Frame(self, bg=palette.CARD)
        body.pack(fill="both", expand=True)
        vsb = ttk.Scrollbar(body, orient="vertical")
        vsb.pack(side="right", fill="y")
        self.text = tk.Text(
            body, wrap="word", relief="flat", bd=0, bg=palette.CARD, fg=palette.TEXT, padx=28, pady=18,
            font=self.fonts["body"], spacing1=2, spacing3=2, yscrollcommand=vsb.set, insertbackground=palette.TEXT, undo=True,
            highlightthickness=0, cursor="arrow",
        )
        self.text.pack(side="left", fill="both", expand=True)
        vsb.config(command=self.text.yview)
        self._setup_tags()

        self.bind("<Escape>", lambda _e: self._close())
        self.bind(f"<{'Command' if MAC else 'Control'}-s>", lambda _e: self.save() if self.editing else None)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.reload()

    # ---- tags

    def _setup_tags(self) -> None:
        t, f = self.text, self.fonts
        t.tag_configure("bold", font=f["bold"])
        t.tag_configure("italic", font=f["italic"])
        t.tag_configure("bold_italic", font=f["bold_italic"])
        t.tag_configure("strike", overstrike=True, foreground=palette.MUTED)
        t.tag_configure("code", font=f["code"], background=palette.CODE_BG, foreground=palette.CODE_TEXT)
        t.tag_configure("link", foreground=palette.LINK, underline=True)
        t.tag_bind("link", "<Enter>", lambda _e: t.config(cursor="hand2"))
        t.tag_bind("link", "<Leave>", lambda _e: t.config(cursor="arrow"))
        for level in (1, 2, 3, 4):
            t.tag_configure(f"h{level}", font=f[f"h{min(level, 4)}"], spacing1=14 if level <= 2 else 10, spacing3=6)
        t.tag_configure("h1rule", foreground=palette.BORDER)
        t.tag_configure("quote", lmargin1=18, lmargin2=18, foreground=palette.MUTED, font=f["italic"])
        t.tag_configure("codeblock", font=f["code"], background=palette.CODEBLOCK_BG, lmargin1=16, lmargin2=16, rmargin=16, spacing1=0, spacing3=0)
        t.tag_configure("table", font=f["code"], lmargin1=8, lmargin2=8, spacing1=0, spacing3=0)
        t.tag_configure("table_head", font=f["code_bold"])
        t.tag_configure("rule", foreground=palette.BORDER, justify="center")
        t.tag_configure("box", font=f["box"], foreground=palette.ACCENT)
        t.tag_configure("done", overstrike=True, foreground=palette.MUTED)
        t.tag_bind("box", "<Enter>", lambda _e: t.config(cursor="hand2"))
        t.tag_bind("box", "<Leave>", lambda _e: t.config(cursor="arrow"))
        for depth in range(8):
            t.tag_configure(f"indent{depth}", lmargin1=8 + depth * 22, lmargin2=30 + depth * 22)

    # ---- content

    def reload(self) -> None:
        try:
            self.source = read_text_file(self.path)
        except OSError as exc:
            self.source = ""
            self._show_plain(_("Could not read the file:\n{error}").format(error=exc))
            return
        self.editing = False
        self._render()

    def _render(self) -> None:
        t = self.text
        t.config(state="normal", cursor="arrow", wrap="word", font=self.fonts["body"])
        t.delete("1.0", "end")
        self.save_btn.pack_forget()
        self.edit_btn.config(text=_("Edit"))
        if self.is_md:
            self._render_md(parse(self.source))
        else:
            t.insert("1.0", self.source)
        t.config(state="disabled")

    def _show_plain(self, message: str) -> None:
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", message)
        self.text.config(state="disabled")

    def _insert_spans(self, block: Block, extra: tuple[str, ...] = ()) -> None:
        for span in block.spans:
            tags = list(extra)
            styles = span.styles
            if "bold" in styles and "italic" in styles:
                tags.append("bold_italic")
            elif "bold" in styles:
                tags.append("bold")
            elif "italic" in styles:
                tags.append("italic")
            if "strike" in styles:
                tags.append("strike")
            if "code" in styles:
                tags.append("code")
            if "link" in styles and span.href:
                tag = f"href{id(span)}"
                self.text.tag_configure(tag)
                self.text.tag_bind(tag, "<Button-1>", lambda _e, h=span.href: self._open_link(h))
                tags += ["link", tag]
            self.text.insert("end", span.text, tuple(tags))

    def _render_md(self, blocks: list[Block]) -> None:
        t = self.text
        for block in blocks:
            kind = block.kind
            if kind == "blank":
                t.insert("end", "\n")
            elif kind == "heading":
                self._insert_spans(block, (f"h{min(block.level, 4)}",))
                t.insert("end", "\n", (f"h{min(block.level, 4)}",))
            elif kind == "para":
                self._insert_spans(block)
                t.insert("end", "\n")
            elif kind == "quote":
                t.insert("end", "▎ ", ("quote",))
                self._insert_spans(block, ("quote",))
                t.insert("end", "\n", ("quote",))
            elif kind == "item":
                indent = f"indent{min(block.level, 7)}"
                t.insert("end", f"{block.marker}  ", (indent,))
                self._insert_spans(block, (indent,))
                t.insert("end", "\n", (indent,))
            elif kind == "task":
                indent = f"indent{min(block.level, 7)}"
                tag = f"task{block.line}"
                t.tag_bind(tag, "<Button-1>", lambda _e, ln=block.line: self._toggle_task(ln))
                t.insert("end", ("☑" if block.checked else "☐") + "  ", (indent, "box", tag))
                self._insert_spans(block, (indent, "done") if block.checked else (indent,))
                t.insert("end", "\n", (indent,))
            elif kind == "code":
                t.insert("end", block.raw + "\n", ("codeblock",))
            elif kind == "table":
                for n, line in enumerate(table_lines(block.raw)):
                    t.insert("end", line + "\n", ("table", "table_head") if n == 0 else ("table",))
            elif kind == "rule":
                t.insert("end", "─" * 40 + "\n", ("rule",))

    def _open_link(self, href: str) -> None:
        if href.startswith(("http://", "https://", "mailto:")):
            webbrowser.open(href)
            return
        target = (self.path.parent / href.split("#", 1)[0]).resolve()
        action = link_action(target)
        if action == "view":
            FileViewer(self.master, target)
        elif action == "open":
            open_with_system(target)
        elif action == "folder":
            show_in_folder(target)

    def _toggle_task(self, line: int) -> None:
        updated = toggle_task(self.source, line)
        if updated == self.source:
            return
        try:
            self._write(updated)
        except OSError as exc:
            show_error(self, _("Could not write: {error}").format(error=exc))
            return
        self.source = updated
        top = self.text.yview()[0]
        self._render()
        self.text.yview_moveto(top)

    # ---- editing

    def toggle_edit(self) -> None:
        if self.editing:
            if self._dirty() and not ask_yes_no(self, _("Discard the changes?")):
                return
            self.editing = False
            self._render()
            return
        self.editing = True
        t = self.text
        t.config(state="normal", cursor="xterm", wrap="word")
        t.delete("1.0", "end")
        t.insert("1.0", self.source)
        t.edit_reset()
        t.edit_modified(False)
        for tag in t.tag_names():
            if tag != "sel":
                t.tag_remove(tag, "1.0", "end")
        t.config(font=self.fonts["code"])
        self.edit_btn.config(text=_("Preview"))
        self.save_btn.pack(side="right", padx=(8, 0), before=self.edit_btn)
        t.focus_set()

    def _dirty(self) -> bool:
        return self.editing and self.text.get("1.0", "end-1c") != self.source

    def _write(self, content: str) -> None:
        newline = "\r\n" if "\r\n" in self.source else "\n"
        with open(self.path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content.replace("\r\n", "\n").replace("\n", newline))

    def save(self) -> None:
        if not self.editing:
            return
        content = self.text.get("1.0", "end-1c")
        try:
            self._write(content)
        except OSError as exc:
            show_error(self, _("Could not write: {error}").format(error=exc))
            return
        self.source = read_text_file(self.path)
        self.text.config(font=self.fonts["body"])
        self.editing = False
        self._render()

    def on_lock(self) -> None:
        """The app locks and this window goes (App.close_child_windows). An idle lock comes
        while nobody is at the computer, so nothing is asked: unsaved edits are written to
        the file, or beside it as `<name>.unsaved-<time><suffix>` when the file cannot be."""
        if not self._dirty():
            return
        content = self.text.get("1.0", "end-1c")
        try:
            self._write(content)
        except OSError:
            spare = self.path.with_name(f"{self.path.stem}.unsaved-{time.strftime('%Y%m%d-%H%M%S')}{self.path.suffix}")
            try:
                spare.write_text(content, encoding="utf-8")
            except OSError:
                pass

    def _close(self) -> None:
        if self._dirty() and not ask_yes_no(self, _("Close without saving the changes?")):
            return
        self.destroy()
