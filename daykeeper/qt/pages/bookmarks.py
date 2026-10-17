"""The “Bookmarks” tab of the Qt window: groups on the left, tiles on the right. The same
behaviour as bookmarks_view.BookmarksPage and bookmark_tile.BookmarkTile of the Tk window.

A tile opens its address with a click. A tile tied to a vault entry shows the entry's
login and copies its password; while the Passwords tab is locked the password row asks
for the code first. “Open and copy password” does both in one click. The site's icon and
colour are asked in the background; its icon is decoded by siteicon (our own limited
decoder) and handed to Qt as ready pixels, never to Qt's image loaders."""

from __future__ import annotations

import base64
import time
from typing import Any

from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMenu,
    QToolButton,
    QWidget,
)

from ... import siteicon
from ...bookmarks import TILE_COLORS, Bookmark, entry_login, entry_secret, entry_url, has_secret, host_of, is_web, normalize_url
from ...i18n import _
from ...jobs import JobPool
from .. import icons, theme
from ..dialogs.base import Dialog, ask_text, ask_yes_no, button, label, show_error, show_info
from ..jobs import Waker
from ..timers import Debounce
from ..widgets.common import fill_menu, more_button, scroll_area, search_field
from ..widgets.countlist import KEY_ROLE, add_count_item, count_list
from ..widgets.tiles import Item, TileBoard, TilePainter, elide, rounded
from .base import TabPage

ALL = "__all__"
UNGROUPED = "__ungrouped__"
REVEAL_SEC = 10
RADIUS, ROW_RADIUS, BADGE_RADIUS = 12, 7, 11
BADGE = 40  # px
ICON_SIZE = 26  # the site icon on a badge, px at 100 %
ROW_ICON = 14  # the drawn icons of the login and password rows, px
MORE_ICON = 16  # the ⋯ of a tile
LINK_ICON = 12  # before the name of the vault entry a bookmark is tied to
PAD_X, PAD_Y = 14, 12
TITLE_GAP = 4
ROWS_GAP, ROW_PAD, ROW_GAP = 14, 14, 4
FOOT_GAP = 16


def pixels_to_pixmap(png: bytes, ratio: float) -> QPixmap | None:
    """A PNG we made ourselves (siteicon.shrink_png), decoded by siteicon and handed to Qt
    as RGBA: no external picture reaches Qt's loaders."""
    try:
        px = siteicon.decode_png(png)
    except Exception:  # noqa: BLE001 — a damaged icon: the letter instead
        return None
    image = QImage(bytes(px.rgba), px.width, px.height, px.width * 4, QImage.Format.Format_RGBA8888).copy()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


class BookmarkPainter(TilePainter):
    """One bookmark tile: a tinted badge with the site icon or the letter, the title and
    the site; when tied to an entry, its login and password rows and a link to the entry."""

    def __init__(self, page: BookmarksPage):
        self.page = page
        self.f = {"title": theme.font(12, bold=True), "host": theme.font(9), "row": theme.font(10), "mono": theme.font(10, mono=True), "avatar": theme.font(15, bold=True), "button": theme.font(12)}
        self.fm = {k: QFontMetrics(v) for k, v in self.f.items()}

    def entry(self, bm: Bookmark):
        return self.page.vault.entry_by_id(bm.entry_id) if self.page.vault is not None else None

    def rows(self, bm: Bookmark) -> list[str]:
        entry = self.entry(bm)
        if entry is None:
            return []
        rows = ["login"] if entry_login(entry) else []
        if has_secret(entry):
            rows.append("secret" if self.page.shell.security.secrets_open() else "locked")
        return rows

    def row_height(self) -> int:
        return self.fm["row"].lineSpacing() + ROW_PAD

    def foot_height(self) -> int:
        return max(self.fm["host"].lineSpacing(), self.fm["button"].lineSpacing()) + 12

    def height(self, bm: Bookmark, width: int) -> int:
        h = PAD_Y + BADGE
        rows = self.rows(bm)
        if rows:
            h += ROWS_GAP + len(rows) * self.row_height() + (len(rows) - 1) * ROW_GAP
        if self.entry(bm) is not None:
            h += FOOT_GAP + self.foot_height()
        return h + PAD_Y

    def _geometry(self, bm: Bookmark, r: QRect) -> dict[str, QRect]:
        """Every place of the tile, for painting and for clicks."""
        g: dict[str, QRect] = {}
        x0, y0, right = r.x() + PAD_X, r.y() + PAD_Y, r.right() - PAD_X
        g["badge"] = QRect(x0, y0, BADGE, BADGE)
        more_w = MORE_ICON + 8
        g["more"] = QRect(right - more_w, y0 - 4, more_w + 6, self.fm["title"].height() + 6)
        y = y0 + BADGE + ROWS_GAP
        rh = self.row_height()
        for row in self.rows(bm):
            g[row] = QRect(x0, y, right - x0, rh)
            if row == "secret":
                eye_w = ROW_ICON + 12
                copy_w = ROW_ICON + 14
                g["eye"] = QRect(right - copy_w - eye_w, y, eye_w, rh)
            y += rh + ROW_GAP
        entry = self.entry(bm)
        if entry is not None:
            y += FOOT_GAP - (ROW_GAP if self.rows(bm) else 0)
            fh = self.foot_height()
            if has_secret(entry):
                both_w = 2 * ROW_ICON + self.fm["button"].horizontalAdvance(" + ") + 8
                g["both"] = QRect(right - 36 - both_w, y, both_w + 8, fh)
            g["link"] = QRect(x0, y, (g["both"].x() - x0 - 12) if "both" in g else right - x0, fh)
        return g

    def zones(self, bm: Bookmark, rect: QRect) -> list[tuple[str, QRect]]:
        g = self._geometry(bm, rect)
        order = ["more", "eye", "login", "secret", "locked", "both", "link"]
        return [(k, g[k]) for k in order if k in g]

    def paint(self, p: QPainter, r: QRect, bm: Bookmark, hot: bool, focused: bool) -> None:
        t = theme.current()
        lit = hot or focused
        rounded(p, QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, t.TILE, t.ACCENT if lit else t.BORDER, 1.5 if lit else 1.0)
        g = self._geometry(bm, r)
        colour = bm.colour()
        rounded(p, QRectF(g["badge"]), BADGE_RADIUS, theme.blend(colour, t.TILE, 0.14))
        icon = self.page.site_icon(bm) if not bm.icon.strip() else None
        if icon is not None:
            size = icon.deviceIndependentSize()
            c = g["badge"].center()
            p.drawPixmap(QPoint(int(c.x() - size.width() / 2 + 1), int(c.y() - size.height() / 2 + 1)), icon)
        else:
            p.setFont(self.f["avatar"])
            p.setPen(QColor(colour))
            p.drawText(g["badge"], int(Qt.AlignmentFlag.AlignCenter), bm.avatar())
        icons.paint(p, "more", g["more"], t.MUTED, size=MORE_ICON)
        tx = g["badge"].right() + 13
        room = g["more"].x() - tx
        th, hh = self.fm["title"].height(), self.fm["host"].height()
        ty = g["badge"].y() + (BADGE - th - hh - TITLE_GAP) // 2
        p.setPen(QColor(t.LINK if lit else t.TEXT))
        p.drawText(QRect(tx, ty, room, th), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), elide(self.fm["title"], bm.title or bm.host or _("(untitled)"), room))
        p.setFont(self.f["host"])
        p.setPen(QColor(t.MUTED))
        host_room = r.right() - PAD_X - tx
        p.drawText(QRect(tx, ty + th + TITLE_GAP, host_room, hh), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), elide(self.fm["host"], bm.host or _("no address"), host_room))
        entry = self.entry(bm)
        for row in self.rows(bm):
            self._paint_row(p, row, g[row], bm, entry)
        if entry is not None:
            if "both" in g:
                b = g["both"]
                plus = self.fm["button"].horizontalAdvance(" + ")
                icons.paint(p, "key", QRect(b.right() - ROW_ICON, b.y(), ROW_ICON, b.height()), t.LINK, size=ROW_ICON)
                p.setFont(self.f["button"])
                p.setPen(QColor(t.LINK))
                p.drawText(QRect(b.right() - ROW_ICON - plus, b.y(), plus, b.height()), int(Qt.AlignmentFlag.AlignCenter), " + ")
                icons.paint(p, "external", QRect(b.right() - 2 * ROW_ICON - plus, b.y(), ROW_ICON, b.height()), t.LINK, size=ROW_ICON)
            link = g["link"]
            icons.paint(p, "link", QRect(link.x(), link.y(), LINK_ICON, link.height()), t.MUTED, size=LINK_ICON)
            p.setFont(self.f["host"])
            p.setPen(QColor(t.MUTED))
            room = link.width() - LINK_ICON - 5
            p.drawText(QRect(link.x() + LINK_ICON + 5, link.y(), room, link.height()), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), elide(self.fm["host"], entry.name, room))

    def _paint_row(self, p: QPainter, row: str, r: QRect, bm: Bookmark, entry) -> None:
        t = theme.current()
        rounded(p, QRectF(r), ROW_RADIUS, t.CRED_BG)
        icon = {"login": "user", "secret": "key", "locked": "padlock"}[row]
        icons.paint(p, icon, QRect(r.x() + 8, r.y(), ROW_ICON, r.height()), t.TEXT, size=ROW_ICON)
        end = r.right() - 8
        if row != "locked":
            icons.paint(p, "copy", QRect(end - ROW_ICON, r.y(), ROW_ICON, r.height()), t.MUTED, size=ROW_ICON)
            end -= ROW_ICON + 6
        revealed = bm.id in self.page.revealed
        if row == "secret":
            icons.paint(p, "eye_off" if revealed else "eye", QRect(end - ROW_ICON, r.y(), ROW_ICON, r.height()), t.TEXT if revealed else t.MUTED, size=ROW_ICON)
            end -= ROW_ICON + 6
        tx = r.x() + 8 + ROW_ICON + 6
        if row == "login":
            text, f, ink = entry_login(entry), "row", t.TEXT
        elif row == "secret":
            text, f, ink = (entry_secret(entry) if revealed else "•" * 10), "mono", t.TEXT
        else:
            text, f, ink = _("locked"), "row", t.MUTED
        p.setFont(self.f[f])
        p.setPen(QColor(ink))
        p.drawText(QRect(tx, r.y(), end - tx, r.height()), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), elide(self.fm[f], text, end - tx))

    def tip(self, bm: Bookmark, zone: str) -> str:
        return {
            "login": _("Copy login"),
            "secret": _("Copy password"),
            "locked": _("Enter the code to see the password"),
            "eye": _("Hide") if bm.id in self.page.revealed else _("Show for {n} s").format(n=REVEAL_SEC),
            "link": _("Open the entry on the Passwords tab"),
            "both": _("Open and copy the password"),
            "open": bm.url + (f"\n{bm.note}" if bm.note else ""),
        }.get(zone, "")


class BookmarksPage(TabPage):
    """The Bookmarks tab. `group` is the group on the left (ALL, UNGROUPED or "g:<name>")."""

    name = "bookmarks"

    def build(self) -> None:
        self.group = ALL
        self._loading = False
        self.revealed: dict[str, Debounce] = {}  # bookmark id → the timer that hides it again
        self._icons: dict[tuple, QPixmap | None] = {}
        self._site_wake = Waker(self._take_sites, owner=self)  # an answer is taken as it comes
        self._site_jobs = JobPool(workers=3, name="site-icon", notify=self._site_wake)
        self._site_busy: set[str] = set()
        self._small_busy: set[str] = set()
        self._site_on = True
        _splitter, left, col, right = self.split(300, 240)
        self.search = QLineEdit(left)
        search_field(self.search, _("🔍 Search bookmarks"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self.refresh_tiles())
        col.addWidget(self.search)
        head = QHBoxLayout()
        head.addWidget(label(_("GROUPS"), "section", left))
        head.addStretch(1)
        head.addWidget(more_button([(_("New group…"), self.group_new), (_("Rename group…"), self.group_rename), (_("Delete group"), self.group_delete)], "", left))
        col.addLayout(head)
        self.groups = count_list(left)
        self.groups.currentItemChanged.connect(self._on_group_select)
        self.groups.itemClicked.connect(self._on_group_click)
        col.addWidget(self.groups, 1)
        add = button(_("+ Bookmark"), "primary", left)
        add.clicked.connect(lambda: self.bookmark_dialog(None))
        col.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)

        area, _inner, inner_col = scroll_area(right, (24, 20, 24, 20))
        self.area = area
        self.painter = BookmarkPainter(self)
        self.board = TileBoard(self.painter, self._activate, self.tile_menu, _inner, second="both")
        inner_col.addWidget(self.board)
        inner_col.addStretch(1)
        rl = QHBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(area)

    # ---- what the window asks for

    def refresh(self, keep_scroll: bool = False) -> None:
        self._refresh_groups(keep_scroll=keep_scroll)

    def new_item(self, entry=None) -> None:
        self.bookmark_dialog(None, entry=entry)

    def focus_search(self) -> None:
        self.search.setFocus()

    def cancel_jobs(self) -> None:
        for hide in self.revealed.values():
            hide.cancel()
        self.revealed.clear()
        self._site_wake.close()
        self._site_jobs.close()

    def from_entry(self, entry) -> None:
        """A bookmark for a vault entry: its name, its address, tied to it."""
        self.shell.show_page("bookmarks")
        self.bookmark_dialog(None, entry=entry)

    # ---- groups

    def _refresh_groups(self, select: str | None = None, keep_scroll: bool = False) -> None:
        if self.vault is None:
            return
        bms = self.vault.bookmarks
        self._loading = True
        try:
            self.groups.clear()
            add_count_item(self.groups, _("  ★ All"), ALL, len(bms.items))
            ungrouped = bms.in_group("")
            if ungrouped and bms.groups:
                add_count_item(self.groups, _("  Without a group"), UNGROUPED, len(ungrouped))
            for g in bms.groups:
                add_count_item(self.groups, f"  {g}", f"g:{g}", len(bms.in_group(g)))
            target = select or self.group
            keys = [self.groups.item(i).data(KEY_ROLE) for i in range(self.groups.count())]
            if target not in keys:
                target = ALL
            self.group = target
            self.groups.setCurrentRow(keys.index(target))
        finally:
            self._loading = False
        self.refresh_tiles(keep_scroll=keep_scroll)

    def _on_group_select(self, current: QListWidgetItem | None, _previous=None) -> None:
        if self._loading or current is None or self.vault is None:
            return
        key = current.data(KEY_ROLE)
        if key == self.group:
            return
        self.group = key
        if self.search.text():
            self.search.setText("")  # its signal draws the tiles
        else:
            self.refresh_tiles()

    def _on_group_click(self, item: QListWidgetItem) -> None:
        """A click on the group already selected while a search is on: back to the group."""
        if self.search.text() and item.data(KEY_ROLE) == self.group:
            self.search.setText("")

    def current_group(self) -> str:
        return self.group[2:] if self.group.startswith("g:") else ""

    def group_new(self) -> None:
        name = (ask_text(self.shell, _("New group"), _("Group name")) or "").strip()
        if not name:
            return
        if name in self.vault.bookmarks.groups:
            show_error(self.shell, _("Group “{name}” already exists").format(name=name))
            return
        self.vault.bookmarks.groups.append(name)
        self.shell.mark_dirty("bookmarks")
        self._refresh_groups(select=f"g:{name}")

    def group_rename(self) -> None:
        old = self.current_group()
        if not old:
            show_info(self.shell, _("Pick a group on the left first."))
            return
        new = (ask_text(self.shell, _("Rename group"), _("Group name"), old) or "").strip()
        if not new or new == old:
            return
        try:
            self.vault.bookmarks.rename_group(old, new)
        except ValueError:
            show_error(self.shell, _("Group name is empty or taken"))
            return
        self.shell.mark_dirty("bookmarks")
        self._refresh_groups(select=f"g:{new}")

    def group_delete(self) -> None:
        name = self.current_group()
        if not name:
            show_info(self.shell, _("Pick a group on the left first."))
            return
        if not ask_yes_no(self.shell, _("Delete group “{name}”? Its bookmarks stay, without a group.").format(name=name)):
            return
        self.vault.bookmarks.remove_group(name)
        self.shell.mark_dirty("bookmarks")
        self._refresh_groups(select=ALL)

    # ---- tiles

    def sections(self) -> tuple[str, list[tuple[str, list[Bookmark]]]]:
        bms = self.vault.bookmarks
        query = self.search.text().strip()
        if query:
            return _("Search “{query}”").format(query=query), [("", bms.search(query))]
        if self.group == UNGROUPED:
            return _("Without a group"), [("", bms.in_group(""))]
        if self.group != ALL:
            name = self.group[2:]
            return name, [("", bms.in_group(name))]
        sections = [(g or (_("Without a group") if bms.groups else ""), bms.in_group(g)) for g in ["", *bms.groups]]
        return _("All bookmarks"), [(t, items) for t, items in sections if items]

    def refresh_tiles(self, keep_scroll: bool = False) -> None:
        if self.vault is None:
            return
        top = self.area.verticalScrollBar().value()
        self._site_on = self.site_icons_on()
        heading, sections = self.sections()
        total = sum(len(items) for _t, items in sections)
        items = [Item("heading", heading, str(total) if total else "")]
        if not total:
            if self.search.text().strip():
                items.append(Item("hint", _("Nothing found.")))
            else:
                items.append(Item("hint", _("“+ Bookmark” on the left adds a tile: a link to a service, and if you tie it to a password entry — its login and password in one click.")))
        for title, bms in sections:
            if title and len(sections) > 1:
                items.append(Item("section", title))
            items.extend(Item("tile", bm) for bm in bms)
        self.board.set_items(items)
        self.area.verticalScrollBar().setValue(top if keep_scroll else 0)
        self._ask_sites()

    def _activate(self, bm: Bookmark, zone: str) -> None:
        entry = self.painter.entry(bm)
        if zone in ("open", "enter"):
            self.open_bookmark(bm)
        elif zone == "both":
            self.open_bookmark(bm, copy_secret=bool(entry is not None and has_secret(entry)))
        elif zone == "login" and entry is not None:
            self.shell.copy_to_clipboard(entry_login(entry), secret=False)
        elif zone == "secret" and entry is not None:
            self.shell.security.require_secrets(lambda: self.shell.copy_to_clipboard(entry_secret(entry)))
        elif zone == "locked":
            self.shell.security.require_secrets(lambda: None)
        elif zone == "eye":
            self.reveal(bm)
        elif zone == "link" and entry is not None:
            self.shell.show_page("vault", select_entry=entry)

    def _repaint(self, bm: Bookmark) -> None:
        i = self.board.index_of(bm)
        if i >= 0:
            self.board.update(self.board.rect_of(i))

    def reveal(self, bm: Bookmark) -> None:
        """The eye shows the password for REVEAL_SEC seconds; a second click hides it at once."""
        if bm.id in self.revealed:
            self._hide_revealed(bm.id)
            return

        def show() -> None:
            hide = self.revealed[bm.id] = Debounce(self, REVEAL_SEC * 1000, self._hide_revealed)
            hide.touch(bm.id)
            self.refresh_tiles(keep_scroll=True)

        self.shell.security.require_secrets(show)

    def _hide_revealed(self, bid: str) -> None:
        hide = self.revealed.pop(bid, None)
        if hide is None:
            return
        hide.cancel()
        bm = self.vault.bookmarks.get(bid) if self.vault is not None else None
        if bm is not None:
            self._repaint(bm)

    def open_bookmark(self, bm: Bookmark, copy_secret: bool = False) -> None:
        if not bm.url:
            show_info(self.shell, _("This bookmark has no address."))
            return
        if not is_web(bm.url):
            show_info(self.shell, _("A bookmark opens web addresses only (http, https, mailto). A file or a program is started from the Routine tab."))
            return

        def go() -> None:
            if copy_secret:
                entry = self.vault.entry_by_id(bm.entry_id)
                if entry is not None and has_secret(entry):
                    self.shell.copy_to_clipboard(entry_secret(entry))
            import webbrowser

            webbrowser.open(bm.url)
            bm.opened = time.time()
            self.shell.mark_dirty(None)

        if copy_secret:
            self.shell.security.require_secrets(go)
        else:
            go()

    def tile_menu(self, bm: Bookmark, pos: QPoint) -> None:
        menu = QMenu(self.board)
        items: list = [(_("Open"), lambda: self.open_bookmark(bm)), (_("Copy address"), lambda: self.shell.copy_to_clipboard(bm.url, secret=False)), (_("Edit…"), lambda: self.bookmark_dialog(bm))]
        if bm.url.startswith(("http://", "https://")) and self.site_icons_on():
            items.append((_("Refresh the site icon"), lambda: self._refresh_site(bm)))
        items += [None, (_("Move left"), lambda: self._move(bm, -1)), (_("Move right"), lambda: self._move(bm, +1)), None, (_("Delete"), lambda: self._delete(bm))]
        fill_menu(menu, items)
        menu.popup(pos)
        self.last_menu = menu  # tests reach it here

    def _move(self, bm: Bookmark, delta: int) -> None:
        if self.vault.bookmarks.move(bm, delta):
            self.shell.mark_dirty("bookmarks")
            self.refresh_tiles(keep_scroll=True)

    def _delete(self, bm: Bookmark) -> None:
        if not ask_yes_no(self.shell, _("Delete bookmark “{title}”?").format(title=bm.title or bm.host)):
            return
        self.vault.bookmarks.remove(bm)
        self.shell.mark_dirty("bookmarks")
        self._refresh_groups()

    # ---- the site's look, asked in the background

    def site_icons_on(self) -> bool:
        return bool(self.shell.session.settings().get("site_icons", True))

    def icon_px(self) -> int:
        return round(ICON_SIZE * self.devicePixelRatioF())

    def site_icon(self, bm: Bookmark) -> QPixmap | None:
        """The stored site icon at the badge size; None — none, off, or not shrunk to this
        size yet (an older file): then it is shrunk in the background and the tile repainted."""
        if not bm.favicon or not self._site_on:
            return None
        size = self.icon_px()
        small = bm.small_icon_at(size)
        if not small:
            self._ask_small_icon(bm, size)
            return None
        key = (bm.id, hash(small), size)
        if key not in self._icons:
            try:
                self._icons[key] = pixels_to_pixmap(base64.b64decode(small), self.devicePixelRatioF())
            except ValueError:
                self._icons[key] = None
        return self._icons[key]

    def _ask_small_icon(self, bm: Bookmark, size: int) -> None:
        if bm.id in self._small_busy or not self.shell.background_jobs:
            return
        self._small_busy.add(bm.id)

        def work(favicon: str = bm.favicon) -> bytes:
            try:
                return siteicon.shrink_png(base64.b64decode(favicon), size)
            except Exception:  # noqa: BLE001 — a damaged icon: the letter stays
                return b""

        self._site_jobs.submit("small", work, bid=bm.id, favicon=bm.favicon, size=size)

    def _ask_sites(self, force: Bookmark | None = None) -> None:
        if self.vault is None or not self.shell.background_jobs or not self._site_on:
            return
        now = time.time()
        wanted = [force] if force is not None else [b for b in self.vault.bookmarks.items if b.needs_site_info(now)]
        size = self.icon_px()
        for bm in wanted:
            if bm.id in self._site_busy or not bm.url.startswith(("http://", "https://")):
                continue
            self._site_busy.add(bm.id)

            def work(url: str = bm.url) -> tuple[siteicon.SiteInfo, bytes]:
                info = siteicon.fetch(url)
                small = b""
                if info.icon:
                    try:
                        small = siteicon.shrink_png(info.icon, size)
                    except Exception:  # noqa: BLE001 — the big icon is kept, the letter shows
                        small = b""
                return info, small

            self._site_jobs.submit("site", work, bid=bm.id, url=bm.url)

    def _take_sites(self) -> None:
        for result in self._site_jobs.drain():
            bid = result.meta["bid"]
            (self._site_busy if result.kind == "site" else self._small_busy).discard(bid)
            bm = self.vault.bookmarks.get(bid) if self.vault is not None else None
            if bm is None or not result.ok:
                continue
            if result.kind == "site":
                info, small = result.value
                if not bm.apply_site_info(result.meta["url"], info.icon, info.colour, small, self.icon_px(), time.time()):
                    continue
                self.shell.mark_dirty(None)
            elif not bm.apply_small_icon(result.meta["favicon"], result.value, result.meta["size"]):
                continue
            self._repaint(bm)

    def _refresh_site(self, bm: Bookmark) -> None:
        bm.site_checked = 0.0
        self._ask_sites(force=bm)
        self.shell.set_status(_("Asking {host} for its icon…").format(host=bm.host))

    # ---- the editor

    def bookmark_dialog(self, bm: Bookmark | None, entry=None) -> Dialog | None:
        """Create (bm is None) or edit a bookmark. `entry` — tie a new bookmark to this vault entry."""
        if self.vault is None:
            return None
        bms = self.vault.bookmarks
        dlg = Dialog(self.shell, _("New bookmark") if bm is None else _("Bookmark"))
        box = dlg.box
        entries = sorted(self.vault.entries, key=lambda e: e.name.lower())
        start_entry = entry or (self.vault.entry_by_id(bm.entry_id) if bm else None)
        width = QFontMetrics(dlg.font()).averageCharWidth() * 44
        title = QLineEdit(bm.title if bm else (entry.name if entry else ""), dlg)
        url = QLineEdit(bm.url if bm else (entry_url(entry) if entry else ""), dlg)
        group = QComboBox(dlg)
        group.setEditable(True)
        group.addItems(bms.groups)
        group.setCurrentText(bm.group if bm else self.current_group())
        tied = QComboBox(dlg)
        tied.addItem(_("— not tied —"), "")
        for e in entries:
            tied.addItem(f"{e.name}  ·  {e.kind_label}", e.id)
        tied.setCurrentIndex(entries.index(start_entry) + 1 if start_entry in entries else 0)
        icon_row = QWidget(dlg)
        il = QHBoxLayout(icon_row)
        il.setContentsMargins(0, 0, 0, 0)
        icon = QLineEdit(bm.icon if bm else "", icon_row)
        icon.setFixedWidth(QFontMetrics(icon.font()).averageCharWidth() * 6 + 20)
        il.addWidget(icon)
        il.addWidget(label(_("An emoji or one or two letters; empty — the first letter of the title"), "muted", icon_row))
        note = QLineEdit(bm.note if bm else "", dlg)
        rows: list[tuple[str, QWidget]] = [(_("Title"), title), (_("Address"), url), (_("Group"), group), (_("Password entry"), tied), (_("Icon"), icon_row), (_("Note"), note)]
        for i, (text, widget) in enumerate(rows):
            box.addWidget(QLabel(text, dlg), i, 0)
            if widget is not icon_row:
                widget.setMinimumWidth(width)
            box.addWidget(widget, i, 1)
        hint = label("", "muted", dlg)
        box.addWidget(hint, len(rows), 1)

        colour = {"code": bm.color if bm else ""}
        swatches: dict[str, QToolButton] = {}
        swatch_row = QWidget(dlg)
        sl = QHBoxLayout(swatch_row)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(4)

        def paint_swatches() -> None:
            t = theme.current()
            for code, b in swatches.items():
                fill = dict((c, v) for c, _l, v in TILE_COLORS)[code]
                if not code and bm is not None and bm.site_colour:
                    fill = bm.site_colour
                ring = t.TEXT if code == colour["code"] else fill
                b.setStyleSheet(f"QToolButton {{ background: {fill}; border: 2px solid {ring}; border-radius: 7px; color: {t.ON_ACCENT}; font-weight: bold; }}")

        def pick(code: str) -> None:
            colour["code"] = code
            paint_swatches()

        for code, text, _v in TILE_COLORS:
            b = QToolButton(swatch_row)
            b.setFixedSize(26, 26)
            b.setText("✦" if not code else "")
            b.setToolTip(_(text))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, c=code: pick(c))
            sl.addWidget(b)
            swatches[code] = b
        sl.addStretch(1)
        paint_swatches()
        box.addWidget(QLabel(_("Colour"), dlg), len(rows) + 1, 0)
        box.addWidget(swatch_row, len(rows) + 1, 1)
        err = label("", "error", dlg)
        box.addWidget(err, len(rows) + 2, 0, 1, 2)

        def suggest(_text: str = "") -> None:
            """Tie to an entry whose address has the same host, if the user has not picked one."""
            if tied.currentIndex() != 0 or not url.text().strip():
                hint.setText("")
                return
            target = self.vault.entry_by_id(bms.suggest_entry(url.text(), entries))
            if target is not None:
                tied.setCurrentIndex(entries.index(target) + 1)
                hint.setText(_("Tied to “{name}” by the address").format(name=target.name))

        def picked() -> Any:
            eid = tied.currentData()
            target = self.vault.entry_by_id(eid) if eid else None
            if target is not None:
                if not url.text().strip() and entry_url(target):
                    url.setText(entry_url(target))
                if not title.text().strip():
                    title.setText(target.name)
            return target

        url.textEdited.connect(suggest)
        tied.activated.connect(lambda _i: picked())

        def save() -> None:
            address = normalize_url(url.text())
            name = title.text().strip() or host_of(address)
            if not name and not address:
                err.setText(_("Fill in a title or an address"))
                return
            target = picked()
            fields = {"title": name, "url": address, "group": group.currentText().strip(), "icon": icon.text().strip(), "color": colour["code"], "entry_id": target.id if target else "", "note": note.text().strip()}
            select = None
            if bm is None:
                g = fields.pop("group")
                created = bms.add(fields.pop("title"), fields.pop("url"), g, **fields)
                select = f"g:{created.group}" if created.group else ALL
            else:
                bms.update(bm, **fields)
            self.shell.mark_dirty("bookmarks")
            dlg.accept()
            self._refresh_groups(select=select)

        def delete() -> None:
            dlg.accept()
            if bm is not None:
                self._delete(bm)

        dlg.editor_buttons(len(rows) + 3, 2, save, delete=delete if bm is not None else None)
        dlg.keys(save)
        title.setFocus()
        if entry is not None:
            suggest()
        dlg.open()
        self.last_dialog = dlg  # tests reach it here
        self.dialog_parts = {"title": title, "url": url, "group": group, "tied": tied, "icon": icon, "note": note, "error": err, "save": save}
        return dlg
