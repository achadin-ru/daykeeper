"""The built-in viewer for files attached to notes, in the Qt window (md_view of the Tk one).

Markdown is rendered by our own parser (markdown.py): headings, lists, task boxes, quotes,
code, tables, links; other text files are shown as they are. “Edit” switches to the raw
text and “Save” writes it back; a click on a task box flips it in the file right away.
The file is re-read on every open and on “Reload”. Links open only web addresses; a local
link opens in this viewer, in the system app for known document kinds, or in its folder —
never a program (md_view.link_action decides)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QCloseEvent, QColor, QFont, QKeySequence, QShortcut, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget

from ... import palette
from ...i18n import MAC, _
from ...markdown import Block, parse, table_lines, toggle_task
from .. import theme
from .base import ask_yes_no, button, label, paint_caption, register, show_error
from ..widgets.common import divider, icon_button

MD_SUFFIXES = {".md", ".markdown", ".mdown"}
VIEWER_OPENS = MD_SUFFIXES | {".txt", ".log", ".csv", ".json", ".yaml", ".yml", ".xml", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg",
                              ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".rtf", ".mp3", ".mp4", ".wav", ".zip"}


def link_action(target: Path) -> str:
    """"view", "open", "folder" or "" — see md_view.link_action (the same rule)."""
    suffix = target.suffix.lower()
    if target.is_file() and suffix in MD_SUFFIXES | {".txt"}:
        return "view"
    if not target.exists():
        return ""
    return "open" if target.is_file() and suffix in VIEWER_OPENS else "folder"


def open_with_system(path: str | os.PathLike) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
    else:
        subprocess.Popen(["open" if MAC else "xdg-open", str(path)])  # noqa: S603, S607


def show_in_folder(path: str | os.PathLike) -> None:
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


class FileViewer(QWidget):
    """One window per file; closed (and its edits kept) on lock."""

    def __init__(self, window: QWidget, path: str | os.PathLike):
        super().__init__(window, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.path = Path(path)
        self.is_md = self.path.suffix.lower() in MD_SUFFIXES
        self.source = ""
        self.editing = False
        self._locking = False
        self.setWindowTitle(f"{self.path.name} — {palette.APP_TITLE}")
        self.setObjectName("FileViewer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.resize(860, 760)
        self.setMinimumSize(520, 360)
        register(window, self)

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(16, 10, 16, 4)
        head = label(("📝 " if self.is_md else "📄 ") + self.path.name, "", self)
        head.setFont(theme.font(13, bold=True))
        bar.addWidget(head)
        bar.addStretch(1)
        self.save_btn = button(_("Save"), "primary", self)
        self.save_btn.clicked.connect(self.save)
        self.save_btn.hide()
        self.edit_btn = button(_("Edit"), "small", self)
        self.edit_btn.clicked.connect(self.toggle_edit)
        bar.addWidget(self.save_btn)
        bar.addWidget(self.edit_btn)
        bar.addWidget(icon_button("↗", _("Open in the system app"), lambda: open_with_system(self.path), self))
        bar.addWidget(icon_button("📁", _("Show in folder"), lambda: show_in_folder(self.path), self))
        bar.addWidget(icon_button("⟳", _("Reload from disk"), self.reload, self))
        col.addLayout(bar)
        where = label(str(self.path), "muted", self)
        where.setContentsMargins(16, 0, 16, 0)
        col.addWidget(where)
        col.addSpacing(8)
        col.addWidget(divider(self))
        self.stack = QStackedWidget(self)
        self.view = QTextBrowser(self.stack)
        self.view.setOpenLinks(False)
        self.view.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.view.document().setDocumentMargin(24)
        self.view.anchorClicked.connect(self._anchor)
        self.editor = QPlainTextEdit(self.stack)
        self.editor.setFont(theme.font(10, mono=True))
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.editor)
        col.addWidget(self.stack, 1)
        QShortcut(QKeySequence("Escape"), self, self.close)
        QShortcut(QKeySequence("Ctrl+S"), self, lambda: self.save() if self.editing else None)
        self.reload()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        paint_caption(self)

    # ---- content

    def reload(self) -> None:
        try:
            self.source = read_text_file(self.path)
        except OSError as exc:
            self.source = ""
            self.view.setPlainText(_("Could not read the file:\n{error}").format(error=exc))
            return
        self.editing = False
        self._render()

    def _render(self) -> None:
        self.stack.setCurrentWidget(self.view)
        self.save_btn.hide()
        self.edit_btn.setText(_("Edit"))
        self.view.clear()
        if self.is_md:
            self._render_md(parse(self.source))
        else:
            self.view.setPlainText(self.source)

    def _fmt(self, size: float = 11, bold: bool = False, italic: bool = False, mono: bool = False, colour: str = "", back: str = "") -> QTextCharFormat:
        t = theme.current()
        f = QTextCharFormat()
        font = theme.font(size, bold=bold, mono=mono)
        font.setItalic(italic)
        f.setFont(font)
        f.setForeground(QColor(colour or t.TEXT))
        if back:
            f.setBackground(QColor(back))
        return f

    def _spans(self, cursor: QTextCursor, block: Block, base: QTextCharFormat) -> None:
        t = theme.current()
        for span in block.spans:
            f = QTextCharFormat(base)
            styles = span.styles
            font = QFont(f.font())
            if "bold" in styles:
                font.setBold(True)
            if "italic" in styles:
                font.setItalic(True)
            if "strike" in styles:
                font.setStrikeOut(True)
                f.setForeground(QColor(t.MUTED))
            if "code" in styles:
                font = theme.font(10, mono=True)
                f.setBackground(QColor(t.CODE_BG))
                f.setForeground(QColor(t.CODE_TEXT))
            f.setFont(font)
            if "link" in styles and span.href:
                f.setAnchor(True)
                f.setAnchorHref("link:" + span.href)
                f.setForeground(QColor(t.LINK))
                f.setFontUnderline(True)
            cursor.insertText(span.text, f)

    def _render_md(self, blocks: list[Block]) -> None:
        t = theme.current()
        doc = self.view.document()
        cursor = QTextCursor(doc)
        first = True
        heading = {1: 20, 2: 16, 3: 13, 4: 12}

        def new_block(indent: int = 0, back: str = "", top: float = 2, bottom: float = 2) -> None:
            nonlocal first
            bf = QTextBlockFormat()
            bf.setLeftMargin(indent)
            bf.setTopMargin(top)
            bf.setBottomMargin(bottom)
            if back:
                bf.setBackground(QColor(back))
            if first:
                cursor.setBlockFormat(bf)
                first = False
            else:
                cursor.insertBlock(bf)

        body = self._fmt()
        for block in blocks:
            kind = block.kind
            if kind == "blank":
                new_block()
            elif kind == "heading":
                level = min(block.level, 4)
                new_block(top=14 if level <= 2 else 10, bottom=6)
                self._spans(cursor, block, self._fmt(heading[level], bold=True))
            elif kind == "para":
                new_block()
                self._spans(cursor, block, body)
            elif kind == "quote":
                new_block(18)
                cursor.insertText("▎ ", self._fmt(italic=True, colour=t.MUTED))
                self._spans(cursor, block, self._fmt(italic=True, colour=t.MUTED))
            elif kind == "item":
                new_block(8 + min(block.level, 7) * 22)
                cursor.insertText(f"{block.marker}  ", body)
                self._spans(cursor, block, body)
            elif kind == "task":
                new_block(8 + min(block.level, 7) * 22)
                box = self._fmt(13, colour=t.LINK)
                box.setAnchor(True)
                box.setAnchorHref(f"task:{block.line}")
                cursor.insertText(("☑" if block.checked else "☐") + "  ", box)
                base = self._fmt(colour=t.MUTED) if block.checked else body
                if block.checked:
                    font = base.font()
                    font.setStrikeOut(True)
                    base.setFont(font)
                self._spans(cursor, block, base)
            elif kind == "code":
                for line in block.raw.split("\n"):
                    new_block(16, t.CODEBLOCK_BG, 0, 0)
                    cursor.insertText(line, self._fmt(10, mono=True))
            elif kind == "table":
                for n, line in enumerate(table_lines(block.raw)):
                    new_block(8, "", 0, 0)
                    cursor.insertText(line, self._fmt(10, bold=n == 0, mono=True))
            elif kind == "rule":
                new_block()
                bf = cursor.blockFormat()
                bf.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cursor.setBlockFormat(bf)
                cursor.insertText("─" * 40, self._fmt(colour=t.BORDER))

    def _anchor(self, url: QUrl) -> None:
        href = url.toString()
        if href.startswith("task:"):
            self._toggle_task(int(href[5:]))
        elif href.startswith("link:"):
            self._open_link(href[5:])

    def _open_link(self, href: str) -> None:
        if href.startswith(("http://", "https://", "mailto:")):
            webbrowser.open(href)
            return
        target = (self.path.parent / href.split("#", 1)[0]).resolve()
        action = link_action(target)
        if action == "view":
            FileViewer(self.parentWidget() or self, target).show()
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
        top = self.view.verticalScrollBar().value()
        self._render()
        self.view.verticalScrollBar().setValue(top)

    # ---- editing

    def toggle_edit(self) -> None:
        if self.editing:
            if self._dirty() and not ask_yes_no(self, _("Discard the changes?")):
                return
            self.editing = False
            self._render()
            return
        self.editing = True
        self.editor.setPlainText(self.source)
        self.stack.setCurrentWidget(self.editor)
        self.edit_btn.setText(_("Preview"))
        self.save_btn.show()
        self.editor.setFocus()

    def _dirty(self) -> bool:
        return self.editing and self.editor.toPlainText() != self.source.replace("\r\n", "\n")

    def _write(self, content: str) -> None:
        newline = "\r\n" if "\r\n" in self.source else "\n"
        with open(self.path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content.replace("\r\n", "\n").replace("\n", newline))

    def save(self) -> None:
        if not self.editing:
            return
        try:
            self._write(self.editor.toPlainText())
        except OSError as exc:
            show_error(self, _("Could not write: {error}").format(error=exc))
            return
        self.source = read_text_file(self.path)
        self.editing = False
        self._render()

    def on_lock(self) -> None:
        """The app locks: unsaved edits are written to the file, or beside it as
        `<name>.unsaved-<time><suffix>` when the file cannot be. Nothing is asked."""
        self._locking = True
        if not self._dirty():
            return
        content = self.editor.toPlainText()
        try:
            self._write(content)
        except OSError:
            spare = self.path.with_name(f"{self.path.stem}.unsaved-{time.strftime('%Y%m%d-%H%M%S')}{self.path.suffix}")
            try:
                spare.write_text(content, encoding="utf-8")
            except OSError:
                pass

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._locking and self._dirty() and not ask_yes_no(self, _("Close without saving the changes?")):
            event.ignore()
            return
        event.accept()
