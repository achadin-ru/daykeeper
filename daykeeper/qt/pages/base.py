"""What a tab of the Qt window is made of (the Tk window's tabpage.TabPage): a widget the
window puts in its page stack, a view key that says when it must be rebuilt, and the
calls the window makes on it. The window is the page's `shell` (Shell in tabpage.py:
vault, mark_dirty, set_status, show_page, copy_to_clipboard, security, page, …)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QSplitter, QVBoxLayout, QWidget

from ...journal import today
from ...revisions import VIEW_DOMAINS
from .. import theme

SIDEBAR_MARGINS = (16, 16, 12, 12)


class TabPage(QWidget):
    """One tab. The window asks `view_key()` before showing it and calls `refresh()` when
    the key changed, `on_shown()` on every visit, `focus_search()` / `new_item()` for the
    shortcuts and `cancel_jobs()` before it drops the page (lock, undo)."""

    name = ""  # the mode the window knows the page by; also its domain in VIEW_DOMAINS

    def __init__(self, shell: Any):
        super().__init__()
        self.shell = shell
        self.setObjectName("Page")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.build()

    @property
    def vault(self):
        return self.shell.vault

    def build(self) -> None:
        raise NotImplementedError

    def view_key(self) -> tuple:
        """What the page shows depends on: the changes to the domains it draws from and the day."""
        return self.shell.session.rev.of(*VIEW_DOMAINS.get(self.name, ())) + (today(),)

    def refresh(self, keep_scroll: bool = False) -> None:
        """Rebuild for a visit after the data changed."""

    def on_shown(self) -> None:
        """Every visit, after refresh when there was one."""

    def focus_search(self) -> None:
        """Ctrl+F on this tab."""

    def new_item(self) -> None:
        """Ctrl+N on this tab."""

    def cancel_jobs(self) -> None:
        """The page is going away: timers and background answers must not reach it."""

    # ---- building blocks

    def split(self, sidebar_width: int = 300, minimum: int = 240) -> tuple[QSplitter, QFrame, QVBoxLayout, QFrame]:
        """The sidebar on the left and the card on the right; the card takes the resize and
        the sidebar never collapses (the Tk window's SidebarKeeper). Both widths grow with
        the system's text size, as the buttons and names in the sidebar do."""
        sidebar_width, minimum = theme.px(sidebar_width), theme.px(minimum)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)
        left = QFrame(splitter)
        left.setObjectName("Sidebar")
        left.setMinimumWidth(minimum)
        col = QVBoxLayout(left)
        col.setContentsMargins(*SIDEBAR_MARGINS)
        col.setSpacing(10)
        right = QFrame(splitter)
        right.setObjectName("Card")
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([sidebar_width, 10_000])
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(splitter)
        return splitter, left, col, right
