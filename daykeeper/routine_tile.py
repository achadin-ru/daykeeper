"""A routine tile drawn on one canvas, the way `BookmarkTile` is. A tile made of frames,
labels, chips and buttons was about ten Tk windows, and the Routine tab spent three
seconds building a hundred of them. One canvas is one window: the badge, the texts, the
chips, the status line and the actions are items; clicks go by item tag; the tile is
drawn again only when its size or its run state changes.

The page gives what the tile needs: the fonts (`_rfonts`), the colour of a kind
(`kind_colour`), the run state to show (`run_status`), the menu (`_routine_menu`) and the
window's `runs` (run, stop, output)."""

from __future__ import annotations

import tkinter as tk

from . import palette
from .bookmark_tile import _box as rounded_box, fit_text, measure
from .i18n import _
from .routines import Routine, describe_schedule, subtitle
from .theme import blend
from .widgets import Tooltip, bind_menu_key

RADIUS, BADGE_RADIUS, CHIP_RADIUS = 14, 11, 6
BADGE = 40  # px at 100 %
PAD_X, PAD_Y = 14, 12
GAP = 8  # between the head, the chips, the status and the actions
CHIP_PAD_X, CHIP_PAD_Y, CHIP_GAP = 7, 2, 6
ACTION_GAP = 14


class _MenuAt:
    """Where a menu opened from the keyboard appears: under the top of the tile."""

    def __init__(self, tile: tk.Widget):
        self.x_root = tile.winfo_rootx() + tile.winfo_width() // 2
        self.y_root = tile.winfo_rooty() + 40


class RoutineTile(tk.Canvas):
    """One routine: a badge with its glyph, the name on one or two lines, what it starts,
    the schedule chips, the state of the last run and the actions. Keyboard: Tab reaches
    it, Enter runs it, the menu key (or Shift+F10) opens its menu."""

    def __init__(self, master: tk.Misc, page, routine: Routine):
        super().__init__(master, bg=palette.CARD, highlightthickness=0, bd=0, width=1, height=1, takefocus=1)
        self.page, self.app, self.routine = page, page.app, routine
        self.px = self.app.painter.px
        self.fonts = page._rfonts
        self.colour = page.kind_colour(routine)
        self.hot = False
        self._size = self._drawn = (0, 0)
        self._title_lines = 1
        self._shown: list[tk.PhotoImage] = []  # the images on it now: Tk forgets an image nobody holds
        self.tip = Tooltip(self, "", attach=False)
        self.configure(height=self._content_height())
        runs = self.app.runs
        self.bind("<Configure>", self._on_configure)
        self.bind("<Map>", lambda _e: self._draw_if_resized())
        self.bind("<Enter>", lambda _e: self._set_hot(True))
        self.bind("<Leave>", lambda _e: self._set_hot(self.focus_get() is self))
        self.bind("<FocusIn>", lambda _e: self._set_hot(True))
        self.bind("<FocusOut>", lambda _e: self._set_hot(False))
        self.bind("<ButtonPress>", lambda _e: self.tip.hide(), add="+")
        self.bind("<Button-3>", lambda e: page._routine_menu(routine, e))
        self.bind("<Double-1>", lambda _e: runs.run_routine(routine))
        self.bind("<Return>", lambda _e: runs.run_routine(routine))
        self.bind("<KP_Enter>", lambda _e: runs.run_routine(routine))
        bind_menu_key(self, lambda _e: page._routine_menu(routine, _MenuAt(self)))
        actions = {
            "run": lambda: runs.run_routine(routine),
            "stop": lambda: runs.stop(routine),
            "output": lambda: runs.open_output(runs.latest(routine)),
        }
        for tag, act in actions.items():
            self.tag_bind(tag, "<Button-1>", lambda _e, act=act: act())
            self.tag_bind(tag, "<Double-1>", lambda _e: "break")  # a click on an action is not a double click on the tile
        self.tag_bind("more", "<Button-1>", lambda e: page._routine_menu(routine, e))
        self.tag_bind("sub", "<Enter>", lambda _e: self._tip_for("sub"))
        self.tag_bind("sub", "<Leave>", lambda _e: self.tip.hide())
        for tag in actions:
            self.tag_bind(tag, "<Enter>", lambda _e: self.configure(cursor="hand2"))
            self.tag_bind(tag, "<Leave>", lambda _e: self.configure(cursor=""))

    # ---- sizes

    def _line(self, name: str) -> int:
        return self.fonts[name].metrics("linespace")

    def _chips(self) -> list[str]:
        r = self.routine
        chips = []
        if r.schedule.on_unlock:
            chips.append(_("⚡ on unlock"))
        when = describe_schedule(r.schedule, self.page._days())
        if when:
            chips.append(f"⏰ {when}")
        if r.terminal:
            chips.append(_("console"))
        return chips

    def _chip_height(self) -> int:
        return self._line("small") + 2 * self.px(CHIP_PAD_Y)

    def _content_height(self) -> int:
        px = self.px
        head = max(px(BADGE), self._title_lines * self._line("title") + px(2) + self._line("small"))
        h = px(PAD_Y) + head
        if self._chips():
            h += px(GAP) + self._chip_height()
        h += px(GAP) + self._line("small")  # the status line
        h += px(GAP) + self._line("row") + px(4)  # the actions
        return h + px(PAD_Y)

    # ---- drawing

    def _on_configure(self, event: tk.Event) -> None:
        self._size = (event.width, event.height)
        if self.winfo_ismapped():
            self._draw_if_resized()

    def _draw_if_resized(self) -> None:
        if self._size != self._drawn:
            self.draw()

    def update_state(self) -> None:
        """The run state changed (a run began or ended, the clock moved): drawn again in place."""
        if self._drawn != (0, 0):
            self.draw()

    def _title_fit(self, text: str, room: int) -> list[str]:
        """The name on one line when it fits, else on two (the second cut with an ellipsis)."""
        font = self.fonts["title"]
        if measure(font, text) <= room:
            return [text]
        words = text.split()
        first, rest = "", words
        for i in range(len(words), 0, -1):
            if measure(font, " ".join(words[:i])) <= room:
                first, rest = " ".join(words[:i]), words[i:]
                break
        if not first:  # one long word: cut it
            first = fit_text(text, font, room).rstrip("…")
            rest = [text[len(first):]]
        return [first, fit_text(" ".join(rest), font, room)]

    def draw(self) -> None:
        w, h = self._drawn = self._size
        if w < 10 or h < 10:
            return
        px, f, r = self.px, self.fonts, self.routine
        scale = self.app.painter.scale
        x0, y0, right = px(PAD_X), px(PAD_Y), w - px(PAD_X)
        size = px(BADGE)
        more_w = measure(f["title"], "⋯") + px(8)
        tx = x0 + size + px(12)
        lines = self._title_fit(r.name or _("(untitled)"), right - more_w - tx)
        if len(lines) != self._title_lines:  # the height follows the name's lines; the grid then sends a new size
            self._title_lines = len(lines)
            self._drawn = (0, 0)
            self.configure(height=self._content_height())
            return
        self.delete("all")
        self._shown = []
        border, bw = (palette.ACCENT, 1.5) if self.hot else (palette.BORDER, 1.0)  # under the pointer: the app's accent, as every tile
        self.create_image(0, 0, anchor="nw", tags=("bg",), image=rounded_box(self, w, h, RADIUS * scale, palette.TILE, border, bw * scale, palette.CARD))
        self.create_image(x0, y0, anchor="nw", tags=("badge",), image=rounded_box(self, size, size, BADGE_RADIUS * scale, blend(self.colour, palette.TILE, 0.14), None, 1.0, palette.TILE))
        self.create_text(x0 + size // 2, y0 + size // 2, text=r.avatar(), fill=self.colour, font=f["avatar"], tags=("badge",))
        self.create_text(right, y0 - px(2), text="⋯", anchor="ne", fill=palette.MUTED, font=f["title"], tags=("more",))
        th = self._line("title")
        y = y0
        for line in lines:
            self.create_text(tx, y, anchor="nw", text=line, font=f["title"], fill=palette.LINK if self.hot else palette.TEXT, tags=("title",))
            y += th
        self.create_text(tx, y + px(2), anchor="nw", text=fit_text(f"{self.page.kind_label(r)} · {subtitle(r)}", f["small"], right - tx), font=f["small"], fill=palette.MUTED, tags=("sub",))
        y = y0 + max(size, len(lines) * th + px(2) + self._line("small"))
        chips = self._chips()
        if chips:
            y += px(GAP)
            cx, ch = x0, self._chip_height()
            for chip in chips:
                cw = measure(f["small"], chip) + 2 * px(CHIP_PAD_X)
                if cx + cw > right and cx > x0:
                    break  # a narrow tile shows the chips that fit
                self.create_image(cx, y, anchor="nw", image=rounded_box(self, cw, ch, CHIP_RADIUS * scale, palette.ACCENT_SOFT, None, 1.0, palette.TILE), tags=("chip",))
                self.create_text(cx + cw // 2, y + ch // 2, text=chip, font=f["small"], fill=palette.ACCENT_INK, tags=("chip",))
                cx += cw + px(CHIP_GAP)
            y += ch
        y += px(GAP)
        text, colour, busy, has_run = self.page.run_status(r)
        self.create_text(x0, y, anchor="nw", text=fit_text(text, f["small"], right - x0), font=f["small"], fill=colour, tags=("status",))
        y += self._line("small") + px(GAP)
        ax = x0
        for tag, label in ((("stop", _("■ Stop")) if busy else ("run", _("▶ Run"))), *((("output", _("Output")),) if has_run else ())):
            self.create_text(ax, y, anchor="nw", text=label, font=f["row"], fill=palette.LINK, tags=(tag, "action"))
            ax += measure(f["row"], label) + px(ACTION_GAP)

    # ---- acting

    def _set_hot(self, on: bool) -> None:
        if on != self.hot:
            self.hot = on
            if self._drawn != (0, 0):
                self.draw()

    def _tip_for(self, tag: str) -> None:
        r = self.routine
        text = (r.target + (" " + r.args if r.args.strip() else "")).strip()
        if r.kind == "script" and r.target.strip().lower().endswith(".ps1"):
            text += "\n" + _("PowerShell, with the execution policy bypassed for this file")
        box = self.bbox("current") or self.bbox(tag)
        if not text.strip() or not box:
            return
        self.tip.text = text
        self.tip.anchor = lambda: (self.winfo_rootx() + (box[0] + box[2]) // 2, self.winfo_rooty() + box[3] + 4)
        self.tip.schedule()
