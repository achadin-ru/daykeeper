"""A bookmark tile drawn on one canvas. A tile made of frames, labels and buttons was about
twenty Tk windows; a tab of 200 tiles spent seconds in Tk laying them out, again on every
visit and undo. One canvas is one window: its badge, texts and credential rows are items,
the clicks and tips go by item tag, and it is drawn again only when its size changes.

The page gives what the tile needs to act: `page.open_bookmark`, `page.tile_menu`,
`page.reveal`, `page.site_icon`, the fonts, the app (clipboard, the secret guard)."""

from __future__ import annotations

import base64
import tkinter as tk
from collections import OrderedDict

from . import palette
from .bookmarks import Bookmark, entry_login, entry_secret, has_secret
from .i18n import _
from .theme import blend, shape
from .widgets import bind_menu_key, Tooltip

TILE_BG = palette.TILE
CRED_BG = palette.CRED_BG
REVEAL_SEC = 10
RADIUS, ROW_RADIUS, BADGE_RADIUS = 12, 7, 11
BADGE = 40  # px at 100 %
ICON_SIZE = 26  # the site icon on a badge, in 96-dpi pixels
PAD_X, PAD_Y = 14, 12
TITLE_GAP = 4  # between the title and the site
ROWS_GAP, ROW_PAD, ROW_GAP = 14, 14, 4  # under the head; inside a credential row; between rows
FOOT_GAP = 16  # above the link to the entry
ELLIPSIS = "…"
_CACHE = 48  # rounded boxes kept: tile and row sizes × the hover colours


def _box(tile: BookmarkTile, w: int, h: int, radius: float, fill: str, border: str | None, width: float, matte: str) -> tk.PhotoImage:
    """A rounded box as an opaque image (drawn over its background colour, so Tk copies it
    instead of blending), shared by the tiles of one window: a tab of tiles has a few sizes.
    The cache is bounded — a resize makes new sizes — and a tile keeps the images it shows
    itself (`_shown`), so one that falls out of the cache stays on the tiles that use it."""
    root = tile._root()
    cache: OrderedDict[tuple, tk.PhotoImage] = root.__dict__.setdefault("_tile_boxes", OrderedDict())
    key = (w, h, radius, fill, border, width, matte)
    img = cache.get(key)
    if img is None:
        data = shape(w, h, radius, fill=fill, border=border, border_width=width, matte=matte)
        img = cache[key] = tk.PhotoImage(master=root, data=base64.b64encode(data))
        while len(cache) > _CACHE:
            cache.popitem(last=False)
    else:
        cache.move_to_end(key)
    tile._shown.append(img)
    return img


_widths: dict[tuple[str, str], int] = {}


def measure(font, text: str) -> int:
    """`font.measure`, remembered: a tab of tiles measures the same glyphs and names again
    on every redraw, and each measure is a trip into Tk."""
    key = (font.name, text)
    width = _widths.get(key)
    if width is None:
        if len(_widths) > 20_000:
            _widths.clear()
        width = _widths[key] = font.measure(text)
    return width


def fit_text(text: str, font, width: int) -> str:
    """`text` cut with an ellipsis to fit `width` px in `font`."""
    if width <= 0:
        return ""
    if measure(font, text) <= width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:  # the longest prefix that fits with the ellipsis
        mid = (lo + hi + 1) // 2
        if measure(font, text[:mid].rstrip() + ELLIPSIS) <= width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + ELLIPSIS if lo else ELLIPSIS


class BookmarkTile(tk.Canvas):
    """One bookmark: a tinted badge with the site icon or the letter, the title and the
    site; when tied to an entry, its login and password rows and a link to the entry.
    Keyboard: Tab reaches it, Enter opens, Shift+Enter opens and copies the password,
    the menu key (or Shift+F10) opens its menu."""

    def __init__(self, master: tk.Misc, page, bm: Bookmark):
        super().__init__(master, bg=palette.CARD, highlightthickness=0, bd=0, width=1, height=1, cursor="hand2", takefocus=1)
        self.page, self.app, self.bm = page, page.app, bm
        self.px = self.app.painter.px
        self.fonts = page.fonts
        self.entry = page.vault.entry_by_id(bm.entry_id)
        self.hot = False  # under the pointer or in focus
        self._size = self._drawn = (0, 0)
        self._shown: list[tk.PhotoImage] = []  # the images on it now: Tk forgets an image nobody holds
        self.tip = Tooltip(self, "", attach=False)
        self.configure(height=self._content_height())
        self.bind("<Configure>", self._on_configure)
        self.bind("<Map>", lambda _e: self._draw_if_resized())
        self.bind("<Enter>", lambda _e: self._set_hot(True))
        self.bind("<Leave>", lambda _e: self._set_hot(self.focus_get() is self))
        self.bind("<FocusIn>", lambda _e: self._set_hot(True))
        self.bind("<FocusOut>", lambda _e: self._set_hot(False))
        self.bind("<ButtonPress>", lambda _e: self.tip.hide(), add="+")
        self.bind("<Button-3>", lambda e: page.tile_menu(bm, e))
        self.bind("<Return>", lambda _e: page.open_bookmark(bm))
        self.bind("<KP_Enter>", lambda _e: page.open_bookmark(bm))
        self.bind("<Shift-Return>", lambda _e: page.open_bookmark(bm, copy_secret=bool(self.entry and has_secret(self.entry))))
        bind_menu_key(self, lambda _e: page.tile_menu(bm, _At(self)))
        actions = {
            "open": lambda: page.open_bookmark(bm),
            "more": lambda: None,  # its own binding below: the menu needs the event
            "login": self._copy_login,
            "secret": self._copy_secret,
            "locked": lambda: self.app.security.require_secrets(lambda: None),
            "eye": lambda: page.reveal(bm),
            "link": lambda: self.app.show_page("vault", select_entry=self.entry),
            "both": lambda: page.open_bookmark(bm, copy_secret=True),
        }
        for tag, act in actions.items():
            if tag != "more":
                self.tag_bind(tag, "<Button-1>", lambda _e, act=act: act())
        self.tag_bind("more", "<Button-1>", lambda e: page.tile_menu(bm, e))
        for tag in ("login", "secret", "locked", "eye", "link", "both", "title"):
            self.tag_bind(tag, "<Enter>", lambda _e, tag=tag: self._tip_for(tag))
            self.tag_bind(tag, "<Leave>", lambda _e: self.tip.hide())

    # ---- what it shows

    @property
    def revealed(self) -> bool:
        return self.bm.id in self.page.revealed

    def _rows(self) -> list[str]:
        """The credential rows under the head: "login", "secret" / "locked"."""
        if self.entry is None:
            return []
        rows = ["login"] if entry_login(self.entry) else []
        if has_secret(self.entry):
            rows.append("secret" if self.app.security.secrets_open() else "locked")
        return rows

    def _row_height(self) -> int:
        return self.fonts["row"].metrics("linespace") + self.px(ROW_PAD)

    def _content_height(self) -> int:
        px = self.px
        h = px(PAD_Y) + px(BADGE)
        rows = self._rows()
        if rows:
            h += px(ROWS_GAP) + len(rows) * self._row_height() + (len(rows) - 1) * px(ROW_GAP)
        if self.entry is not None:
            h += px(FOOT_GAP) + self._foot_height()
        return h + px(PAD_Y)

    def _foot_height(self) -> int:
        return max(self.fonts["host"].metrics("linespace"), self.fonts["button"].metrics("linespace")) + self.px(12)

    # ---- drawing

    def _on_configure(self, event: tk.Event) -> None:
        self._size = (event.width, event.height)
        # a tab is built before it is placed, at a width it does not have yet: a tile off
        # screen waits for <Map> and is drawn once, at its real size
        if self.winfo_ismapped():
            self._draw_if_resized()

    def _draw_if_resized(self) -> None:
        if self._size != self._drawn:
            self.draw()

    def draw(self) -> None:
        """Everything again at the size the grid gave: a few items, cheap."""
        w, h = self._drawn = self._size
        if w < 10 or h < 10:
            return
        px, f, bm = self.px, self.fonts, self.bm
        self.delete("all")
        self._shown = []
        colour = bm.colour()
        border, bw = (colour, 1.5) if self.hot else (palette.BORDER, 1.0)
        scale = self.app.painter.scale
        self.create_image(0, 0, anchor="nw", tags=("open", "bg"),
                          image=_box(self, w, h, RADIUS * scale, TILE_BG, border, bw * scale, palette.CARD))
        x0, y0, right = px(PAD_X), px(PAD_Y), w - px(PAD_X)
        size = px(BADGE)
        self.create_image(x0, y0, anchor="nw", tags=("open", "badge"),
                          image=_box(self, size, size, BADGE_RADIUS * scale, blend(colour, TILE_BG, 0.14), None, 1.0, TILE_BG))
        self.paint_badge()

        more_w = measure(f["title"], "⋯") + px(8)
        self.create_text(right, y0 - px(2), text="⋯", anchor="ne", fill=palette.MUTED, font=f["title"], tags=("more",))
        tx = x0 + size + px(12)
        room = right - more_w - tx
        th, hh = f["title"].metrics("linespace"), f["host"].metrics("linespace")
        ty = y0 + (size - th - hh - self.px(TITLE_GAP)) // 2
        self.create_text(tx, ty, anchor="nw", tags=("open", "title"), font=f["title"], fill=colour if self.hot else palette.TEXT,
                         text=fit_text(bm.title or bm.host or _("(untitled)"), f["title"], room))
        self.create_text(tx, ty + th + self.px(TITLE_GAP), anchor="nw", tags=("open",), font=f["host"], fill=palette.MUTED,
                         text=fit_text(bm.host or _("no address"), f["host"], right - tx))

        y = y0 + size + px(ROWS_GAP)
        rh = self._row_height()
        for row in self._rows():
            self._draw_row(row, x0, y, right, rh)
            y += rh + px(ROW_GAP)
        if self.entry is not None:
            y += px(FOOT_GAP) - (px(ROW_GAP) if self._rows() else 0)
            mid = y + self._foot_height() // 2
            both = ""
            if has_secret(self.entry):
                both = "↗ + 🔑"
                self.create_text(right - px(36), mid, anchor="e", text=both, font=f["button"], fill=palette.ACCENT, tags=("both",))
            room = right - x0 - (measure(f["button"], both) + px(48) if both else 0)
            self.create_text(x0, mid, anchor="w", text=fit_text("🔗 " + self.entry.name, f["host"], room), font=f["host"], fill=palette.MUTED, tags=("link",))

    def _draw_row(self, row: str, x0: int, y: int, right: int, rh: int) -> None:
        """A rounded grey row: an icon, a text and the copy glyph (and the eye on the password)."""
        px, f = self.px, self.fonts
        scale = self.app.painter.scale
        tags = (row,)
        self.create_image(x0, y, anchor="nw", tags=tags,
                          image=_box(self, right - x0, rh, ROW_RADIUS * scale, CRED_BG, None, 1.0, TILE_BG))
        mid = y + rh // 2
        icon = {"login": "👤", "secret": "🔑", "locked": "🔒"}[row]
        self.create_text(x0 + px(8), mid, anchor="w", text=icon, font=f["row"], fill=palette.TEXT, tags=tags)
        end = right - px(8)
        if row != "locked":
            self.create_text(end, mid, anchor="e", text="⧉", font=f["row"], fill=palette.MUTED, tags=tags)
            end -= measure(f["row"], "⧉") + px(6)
        if row == "secret":
            eye = "🙈" if self.revealed else "👁"
            self.create_text(end, mid, anchor="e", text=eye, font=f["row"], fill=palette.TEXT if self.revealed else palette.MUTED, tags=("eye",))
            end -= measure(f["row"], eye) + px(6)
        tx = x0 + px(8) + measure(f["row"], icon) + px(6)
        if row == "login":
            text, font, ink = entry_login(self.entry), f["row"], palette.TEXT
        elif row == "secret":
            text, font, ink = (entry_secret(self.entry) if self.revealed else "•" * 10), f["mono"], palette.TEXT
        else:
            text, font, ink = _("locked"), f["row"], palette.MUTED
        self.create_text(tx, mid, anchor="w", text=fit_text(text, font, end - tx), font=font, fill=ink, tags=tags)

    def paint_badge(self) -> None:
        """The icon or the letter on the badge; again in place when the site answers."""
        self.delete("mark")
        px = self.px
        c = px(PAD_X) + px(BADGE) // 2, px(PAD_Y) + px(BADGE) // 2
        icon = self.page.site_icon(self.bm, px(ICON_SIZE)) if not self.bm.icon.strip() else None
        if icon is not None:
            self.create_image(*c, image=icon, tags=("open", "mark"))
        else:
            self.create_text(*c, text=self.bm.avatar(), fill=self.bm.colour(), font=self.fonts["avatar"], tags=("open", "mark"))

    # ---- acting

    def _set_hot(self, on: bool) -> None:
        if on != self.hot:
            self.hot = on
            self.draw()

    def _copy_login(self) -> None:
        self.app.copy_to_clipboard(entry_login(self.entry), secret=False)

    def _copy_secret(self) -> None:
        entry = self.entry
        self.app.security.require_secrets(lambda: self.app.copy_to_clipboard(entry_secret(entry)))

    def _tip_for(self, tag: str) -> None:
        bm = self.bm
        text = {
            "login": _("Copy login"),
            "secret": _("Copy password"),
            "locked": _("Enter the code to see the password"),
            "eye": _("Hide") if self.revealed else _("Show for {n} s").format(n=REVEAL_SEC),
            "link": _("Open the entry on the Passwords tab"),
            "both": _("Open and copy the password"),
            "title": bm.url + (f"\n{bm.note}" if bm.note else ""),
        }[tag]
        if not text.strip():
            return
        box = self.bbox("current") or self.bbox(tag)
        if not box:
            return
        self.tip.text = text
        self.tip.anchor = lambda: (self.winfo_rootx() + (box[0] + box[2]) // 2, self.winfo_rooty() + box[3] + 4)
        self.tip.schedule()


class _At:
    """Where a menu opened from the keyboard appears: under the tile's title."""

    def __init__(self, tile: tk.Widget):
        self.x_root = tile.winfo_rootx() + tile.winfo_width() // 2
        self.y_root = tile.winfo_rooty() + tile.app.painter.px(PAD_Y + BADGE)
