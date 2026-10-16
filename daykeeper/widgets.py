"""Small widgets the tabs share: tooltips, the grey hint in an empty field, a set of
fonts, a clickable glyph, a chip, the grid of tiles, the “⋯” button with a menu."""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont, ttk
from collections.abc import Callable

from . import palette
from .layout import TILE_MARGIN, TILE_MIN_WIDTH, tile_columns
from .popup import PopupMenu
from .theme import RoundCard, RoundText, blend
from .timers import Debounce

TOOLTIP_DELAY_MS = 500
TOOLTIP_WRAP = 380  # px


class Tooltip:
    """Tooltip for an icon button. The bindings are made once for the whole application
    (`install`): a tooltip used to add three bindings to its widget, hundreds per tab build."""

    def __init__(self, widget: tk.Widget, text: str, attach: bool = True):
        """`attach=False` — the widget does not show it on its own: the owner calls
        `schedule()` and `hide()` (a part of a canvas), and may set `anchor`."""
        self.widget = widget
        self.text = text
        self.anchor = None  # () -> (x, y) on the screen where the tip hangs from; under the widget when None
        self._tip: tk.Toplevel | None = None
        self._delay = Debounce(widget, TOOLTIP_DELAY_MS, self._show)
        if attach:
            widget._tooltip = self  # type: ignore[attr-defined]

    @classmethod
    def install(cls, root: tk.Tk) -> None:
        """Once per application: the widget under the pointer says whether it has a tip."""
        root.bind_all("<Enter>", lambda e: cls._dispatch(e, "_schedule"), add="+")
        root.bind_all("<Leave>", lambda e: cls._dispatch(e, "_hide"), add="+")
        root.bind_all("<ButtonPress>", lambda e: cls._dispatch(e, "_hide"), add="+")

    @staticmethod
    def _dispatch(event: tk.Event, method: str) -> None:
        tip = getattr(event.widget, "_tooltip", None)
        if tip is not None:
            getattr(tip, method)(event)

    def _schedule(self, _e=None) -> None:
        self._delay.touch()

    def schedule(self) -> None:
        self.hide()
        self._delay.touch()

    def hide(self) -> None:
        self._hide()

    def _show(self) -> None:
        if self._tip is not None or not self.widget.winfo_exists():
            return
        if self.anchor is not None:
            x, y = self.anchor()
        else:
            x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._tip = tk.Toplevel(self.widget)
        self._tip.overrideredirect(True)
        self._tip.attributes("-topmost", True)
        label = tk.Label(
            self._tip,
            text=self.text,
            bg=palette.TEXT,
            fg=palette.WHITE,
            font=(palette.FONT, palette.pt(9)),
            padx=8,
            pady=4,
            wraplength=TOOLTIP_WRAP,  # a long explanation wraps instead of crossing the screen
            justify="left",
        )
        label.pack()
        self._tip.update_idletasks()
        width = self._tip.winfo_width()
        x = min(max(x - width // 2, 4), self.widget.winfo_screenwidth() - width - 4)
        self._tip.geometry(f"+{x}+{y}")

    def _hide(self, _e=None) -> None:
        self._delay.cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


HELP_SIZE = 16  # px at 100 %
HELP_TINT = 0.22  # share of the accent in the resting badge: a light tint, visible on white and grey


def help_icon(parent: tk.Widget, text: str, bg: str = palette.CARD) -> tk.Label:
    """A small round “?” badge that explains the control beside it on hover: a soft accent
    circle, drawn smooth by the Painter, that turns solid under the pointer."""
    painter = getattr(parent._root(), "painter", None)
    font = (palette.FONT, palette.pt(8), "bold")
    if painter is None:  # a bare Tk root (unit tests): the glyph alone
        badge = tk.Label(parent, text="?", bg=bg, fg=palette.ACCENT_INK, font=font, cursor="question_arrow")
    else:
        rest = painter.image(HELP_SIZE, HELP_SIZE, HELP_SIZE / 2, fill=blend(palette.ACCENT, palette.WHITE, HELP_TINT), matte=bg)
        hot = painter.image(HELP_SIZE, HELP_SIZE, HELP_SIZE / 2, fill=palette.ACCENT, matte=bg)
        badge = tk.Label(parent, image=rest, text="?", compound="center", bg=bg, fg=palette.ACCENT_INK, font=font,
                         bd=0, padx=0, pady=0, cursor="question_arrow")
        badge.bind("<Enter>", lambda _e: badge.config(image=hot, fg=palette.ON_ACCENT), add="+")
        badge.bind("<Leave>", lambda _e: badge.config(image=rest, fg=palette.ACCENT_INK), add="+")
    Tooltip(badge, text)
    return badge


def divider(parent: tk.Misc, vertical: bool = False) -> tk.Frame:
    """A thin line in the border colour. ttk's Separator draws itself light in the clam theme
    whatever its style says — a white line across the dark theme."""
    return tk.Frame(parent, bg=palette.BORDER, **({"width": 1} if vertical else {"height": 1}))


SHADE_HEIGHT = 10
SHADE_INK = palette.SHADE_INK  # the shadow's colour at its darkest, mixed thinly into the background
SHADE_DEPTH = 0.16


def soft_edge(parent: tk.Widget, bg: str = palette.CARD) -> tk.Canvas:
    """A soft shadow along the top of a bar pinned under scrolling content: darkest at
    the bar, fading upward, so the edge reads without a hard line."""
    shade = tk.Canvas(parent, height=SHADE_HEIGHT, bg=bg, highlightthickness=0, bd=0)
    for y in range(SHADE_HEIGHT):
        t = ((y + 1) / SHADE_HEIGHT) ** 2 * SHADE_DEPTH
        shade.create_line(0, y, 10000, y, fill=blend(SHADE_INK, bg, t))  # wider than any screen: no redraw on resize
    return shade


def scroll_area(parent: tk.Widget, bg: str = palette.BG) -> ttk.Frame:
    """A vertically scrolling frame filling `parent` (a settings section taller than the
    window): the wheel scrolls it through App._on_mousewheel."""
    canvas = tk.Canvas(parent, bg=bg, highlightthickness=0, bd=0)
    canvas.scrollable_canvas = True  # type: ignore[attr-defined]
    vsb = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    vsb.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    inner = ttk.Frame(canvas, padding=(0, 0, 14, 0))
    item = canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    # the content takes the new width once a drag pauses: laying out every group on each
    # step of a resize made the settings window shake
    settle = Debounce(canvas, 120, lambda: canvas.winfo_exists() and canvas.itemconfigure(item, width=canvas.winfo_width()))
    first = [True]

    def on_resize(_e) -> None:
        if first[0]:  # the first size at once, so the section opens laid out
            first[0] = False
            canvas.itemconfigure(item, width=canvas.winfo_width())
        else:
            settle.touch()

    canvas.bind("<Configure>", on_resize)
    return inner


class RowTip(Tooltip):
    """Tooltip for the row of a tree under the pointer; `text_for(iid)` gives its text."""

    def __init__(self, tree: ttk.Treeview, text_for):
        super().__init__(tree, "")
        self.text_for = text_for
        self._row = ""
        tree.bind("<Motion>", self._move, add="+")

    def _move(self, event: tk.Event) -> None:
        row = self.widget.identify_row(event.y)
        if row == self._row:
            return
        self._hide()
        self._row = row
        self.text = self.text_for(row) if row else ""
        if self.text:
            self._schedule()

    def _schedule(self, _e=None) -> None:
        if self.text:
            super()._schedule()

    def _show(self) -> None:
        super()._show()
        if self._tip is not None:  # under the pointer, not under the middle of the whole tree
            x, y = self.widget.winfo_pointerxy()
            self._tip.geometry(f"+{x + 14}+{y + 18}")

    def _hide(self, _e=None) -> None:
        super()._hide()
        if _e is not None and getattr(_e, "type", "") == tk.EventType.Leave:
            self._row = ""


class Placeholder:
    """A grey hint inside an empty field; a click on it focuses the field. With `var` the
    hint follows the text by itself — and hides while the field has the focus when
    `hide_on_focus`; without a variable the owner calls `show(text)` and `hide()`."""

    def __init__(self, field: tk.Entry | ttk.Entry, hint: str, bg: str, var: tk.StringVar | None = None, padx: int = 0, hide_on_focus: bool = True):
        self.field, self.var, self.padx, self.hide_on_focus = field, var, padx, hide_on_focus
        self.label = tk.Label(field, text=hint, bg=bg, fg=palette.MUTED, font=field.cget("font"), cursor="xterm", bd=0, padx=0, pady=0, highlightthickness=0)
        self.label.bind("<Button-1>", lambda _e: field.focus_set())
        if var is not None:
            var.trace_add("write", self.sync)
            if hide_on_focus:
                field.bind("<FocusIn>", self.sync, add="+")
                field.bind("<FocusOut>", self.sync, add="+")
            self.sync()

    def sync(self, *_args) -> None:
        if self.var.get() or (self.hide_on_focus and self.field.focus_get() is self.field):
            self.hide()
        else:
            self.show()

    def show(self, text: str | None = None) -> None:
        if text is not None:
            self.label.config(text=text)
        self.label.place(x=self.padx, rely=0.5, anchor="w")

    def hide(self) -> None:
        self.label.place_forget()


class TextPlaceholder:
    """The same for a multi-line text: shown while it is empty and not being typed in."""

    def __init__(self, text: tk.Text, hint: str, bg: str):
        self.text = text
        self.label = tk.Label(text, text=hint, bg=bg, fg=palette.MUTED, font=text.cget("font"), cursor="xterm", bd=0, padx=0, pady=0, highlightthickness=0)
        self.label.bind("<Button-1>", lambda _e: text.focus_set())
        text.bind("<FocusIn>", self.sync, add="+")
        text.bind("<FocusOut>", self.sync, add="+")
        text.bind("<KeyRelease>", self.sync, add="+")
        self.sync()

    def sync(self, _e=None) -> None:
        if self.text.get("1.0", "end-1c") or self.text.focus_get() is self.text:
            self.label.place_forget()
        else:
            self.label.place(x=3, y=5, anchor="nw")


class WrapLine(tk.Text):
    """One line of text that wraps by word at its width instead of running past it (an
    entry cannot wrap): as tall as its wrapped lines. Enter does not break it, a pasted
    text joins into one line. `on_change(value)` follows every edit; undo is its own, as in
    any text box."""

    def __init__(self, master: tk.Misc, value: str, on_change: Callable[[str], None], **kw):
        super().__init__(master, height=1, width=1, wrap="word", undo=True, padx=1, pady=1, **kw)  # width=1: packing decides
        self.insert("1.0", value)
        self.edit_reset()
        self.edit_modified(False)
        self._on_change = on_change
        self._lines = 1
        self.bind("<<Modified>>", self._modified, add="+")
        self.bind("<Configure>", lambda _e: self.fit(), add="+")
        self.bind("<Tab>", lambda _e: (self.tk_focusNext().focus_set(), "break")[1])

    def value(self) -> str:
        return self.get("1.0", "end-1c")

    def cursor_to_end(self) -> None:
        self.mark_set("insert", "end-1c")

    def _modified(self, _event=None) -> None:
        if not self.edit_modified():
            return
        self.edit_modified(False)
        value = self.value()
        if "\n" in value or "\t" in value:  # pasted lines become one
            joined = " ".join(value.split())
            self.delete("1.0", "end")
            self.insert("1.0", joined)
            self.edit_modified(False)
            value = joined
        self._on_change(value)
        self.fit()

    def fit(self) -> None:
        """As tall as the text takes at the present width."""
        try:
            lines = int(self.tk.call(self._w, "count", "-update", "-displaylines", "1.0", "end-1c")) + 1
        except (tk.TclError, ValueError):
            return
        if lines != self._lines:
            self._lines = lines
            self.configure(height=lines)


class FlowRow(ttk.Frame):
    """A row of widgets that goes on to the next line when the width runs out, instead of
    cutting the last ones off. The widgets are children of the row, put in with `add`
    in their order; `side="right"` ones keep to the right edge of their line. They are
    placed by coordinates: packing them into line frames (not their parent) crashed Tk on
    macOS."""

    def __init__(self, master: tk.Misc, gap: int = 6, **kw):
        super().__init__(master, **kw)
        self._gap = gap
        self._items: list[tuple[tk.Widget, tuple[int, int], str]] = []
        self._placed: tuple = ()  # what the last layout was made for
        self.bind("<Configure>", lambda _e: self.reflow(), add="+")

    def add(self, widget: tk.Widget, padx: tuple[int, int] = (0, 0), side: str = "left") -> None:
        self._items.append((widget, padx, side))
        widget.bind("<Configure>", lambda _e: self.reflow(), add="+")  # its text changed width
        self.reflow()

    def _padding(self) -> tuple[int, int, int, int]:
        raw = self.cget("padding")
        pad = [int(float(str(p))) for p in (raw if isinstance(raw, (tuple, list)) else str(raw).split())] or [0]
        while len(pad) < 4:
            pad.append(pad[len(pad) - 2] if len(pad) >= 2 else pad[0])  # Tk's rule: left, top, right, bottom
        return pad[0], pad[1], pad[2], pad[3]

    def reflow(self) -> None:
        left_pad, top_pad, right_pad, bottom_pad = self._padding()
        width = self.winfo_width()
        room = width - left_pad - right_pad if width > 1 else 1 << 30  # not laid out yet: one line
        sizes = [(w.winfo_reqwidth(), w.winfo_reqheight()) for w, _p, _s in self._items]
        key = (room, tuple(sizes))
        if key == self._placed:
            return
        self._placed = key
        lines: list[list[int]] = []
        used = 0
        for i, (_widget, (pl, pr), _side) in enumerate(self._items):
            need = sizes[i][0] + pl + pr
            if lines and used + need <= room:
                lines[-1].append(i)
                used += need
            else:
                lines.append([i])
                used = need - pl  # a line does not start with a gap
        y = 0  # placed inside the padding already: its origin is the padding's corner
        for n, line in enumerate(lines):
            if n:
                y += self._gap
            height = max(sizes[i][1] for i in line)
            x_left, x_right = 0, (room if width > 1 else used)
            for k, i in enumerate(line):
                widget, (pl, pr), side = self._items[i]
                w, h = sizes[i]
                if side == "right":
                    x_right -= pr + w
                    x = x_right
                    x_right -= pl
                else:
                    x_left += 0 if k == 0 else pl
                    x = x_left
                    x_left += w + pr
                widget.place(x=x, y=y + (height - h) // 2)
            y += height
        widest = max((sizes[i][0] + pl + pr for i, (_w, (pl, pr), _s) in enumerate(self._items)), default=0)
        self.configure(width=widest + left_pad + right_pad, height=y + top_pad + bottom_pad)  # placed widgets ask nothing of the row


class TextField(RoundText):
    """A multi-line field in the common style: a rounded box, accent on focus."""

    def __init__(self, master, height: int, outer: str = palette.CARD, **kw):
        super().__init__(
            master,
            master._root(),
            outer=outer,
            border=palette.BORDER,
            focus=palette.ACCENT,
            height=height,
            font=(palette.FONT, palette.pt(11)),
            wrap="word",
            undo=True,
            fg=palette.TEXT,
            insertbackground=palette.TEXT,
            padx=8,
            pady=6,
            **kw,
        )


def make_fonts(spec: dict[str, tuple]) -> dict[str, tkfont.Font]:
    """Fonts of the app's family by name: {"title": (13, "bold"), "code": (10, "mono"),
    "done": (11, "strike"), "quote": (11, "italic")}."""
    out: dict[str, tkfont.Font] = {}
    for name, (size, *flags) in spec.items():
        kw: dict = {"family": palette.MONO if "mono" in flags else palette.FONT, "size": palette.pt(size)}
        if "bold" in flags:
            kw["weight"] = "bold"
        if "italic" in flags:
            kw["slant"] = "italic"
        if "strike" in flags:
            kw["overstrike"] = True
        out[name] = tkfont.Font(**kw)
    return out


def glyph_button(parent: tk.Misc, glyph: str, bg: str, font: tkfont.Font, command, tip: str, active: bool = False) -> tk.Label:
    """A small clickable glyph that darkens on hover."""
    rest = palette.TEXT if active else palette.MUTED
    label = tk.Label(parent, text=glyph, bg=bg, fg=rest, cursor="hand2", font=font, padx=3)
    label.bind("<Enter>", lambda _e: label.config(fg=palette.TEXT), add="+")
    label.bind("<Leave>", lambda _e: label.config(fg=rest), add="+")
    if command is not None:
        label.bind("<Button-1>", lambda _e: command())
    Tooltip(label, tip)
    return label


def chip(parent: tk.Frame, app, text: str, font: tkfont.Font) -> RoundCard:
    """A small rounded label on a soft accent background, packed to the left."""
    card = RoundCard(parent, app, palette.ACCENT_SOFT, None, radius=6, outer=parent.cget("bg"), padx=3, pady=0)
    card.pack(side="left", padx=(0, 6))
    tk.Label(card.body, text=text, bg=palette.ACCENT_SOFT, fg=palette.ACCENT_INK, font=font, padx=0, pady=0).pack()
    return card


def more_button(parent: tk.Misc, items: list[tuple[str, Callable[[], None]]]) -> ttk.Button:
    """The “⋯” button whose menu lists `items` (label, command), opening under its right edge."""
    more = ttk.Button(parent, text="⋯", style="More.TButton")
    menu = PopupMenu(more)
    for label, command in items:
        menu.add_command(label=label, command=command)
    more.config(command=lambda: menu.post_below(more, align="right"))
    return more


class TileGrid:
    """Lays tiles out in as many columns of at least `min_width` as the frame allows.
    The layout is a list of ("line", (widget, pady)) — a full-width row such as a heading
    or a hint — and ("tiles", [widgets]) — a run of tiles wrapped into the columns. A
    resize that changes the number of columns places the same widgets again; nothing
    is rebuilt."""

    def __init__(self, frame: ttk.Frame, min_width: int = TILE_MIN_WIDTH):
        self.frame, self.min_width = frame, min_width
        self.cols = 3
        self.layout: list[tuple[str, object]] = []
        frame.bind("<Configure>", self._on_resize, add="+")

    def _on_resize(self, event: tk.Event) -> None:
        painter = getattr(self.frame._root(), "painter", None)
        scale = painter.scale if painter is not None else 1.0  # the width is at 100 %
        cols = tile_columns(event.width, round(self.min_width * scale), round(TILE_MARGIN * scale))
        if cols != self.cols:
            self.cols = cols
            self.place()

    def set(self, layout: list[tuple[str, object]]) -> None:
        self.layout = layout
        self.place()

    def place(self) -> None:
        for col in range(12):
            self.frame.columnconfigure(col, weight=1 if col < self.cols else 0, uniform="tile" if col < self.cols else "")
        row = 0
        for kind, item in self.layout:
            if kind == "line":
                widget, pady = item  # type: ignore[misc]
                widget.grid(row=row, column=0, columnspan=99, sticky="w", pady=pady)
                row += 1
                continue
            for i, tile in enumerate(item):  # type: ignore[arg-type]
                r, c = divmod(i, self.cols)
                tile.grid(row=row + r, column=c, sticky="nsew", padx=(0 if c == 0 else 12, 0), pady=(0, 12))
            row += (len(item) + self.cols - 1) // self.cols  # type: ignore[arg-type]

    def restack(self) -> None:
        """Tab goes through the tiles in the order they are shown: Tk follows the stacking
        order, and a tile kept from the previous build sits where it was made."""
        for kind, item in self.layout:
            if kind == "tiles":
                for tile in item:  # type: ignore[union-attr]
                    tile.tk.call("raise", tile._w)


def bind_menu_key(widget: tk.Misc, handler) -> None:
    """The context-menu key of the keyboard on a tile: `<App>` on Windows, `<Menu>` on X11
    (where Tk has no `App` keysym and raised on the bind, so the first tile never built),
    `Shift+F10` everywhere."""
    for key in ("<App>", "<Menu>", "<Shift-F10>"):
        try:
            widget.bind(key, handler)
        except tk.TclError:
            pass
