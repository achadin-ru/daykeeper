"""The “Routine” tab of the Qt window: quick-launch tiles for scripts, commands, links,
folders and files. The same behaviour as routines_view and routine_tile of the Tk window.

▶ runs a routine. Scripts and commands run in the background: the tile shows that it runs
and then how it ended, a note pops up in the corner, the output is a click away; with
“Open in a console” they run in a separate console window. A routine can also start by
itself each time the vault is unlocked, or at a time of day on chosen weekdays. Automatic
runs work quietly: the result goes to the run log, a failure also pops up. A script runs
by itself only in the version the user accepted (a run by hand, or saving the tile)."""

from __future__ import annotations

import os
import time
import webbrowser
from typing import Any

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QColor, QFontMetrics, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidgetItem, QMenu, QPlainTextEdit, QVBoxLayout, QWidget

from ... import palette
from ...i18n import _
from ...routines import KINDS, RUN_LOG_KEEP, SCRIPT_CHANGED, SCRIPT_UNKNOWN, WEEKDAYS, Routine, Schedule, accept_script, check_script, describe_schedule, launch_plan, now_local, risky_location, subtitle
from ...runner import Run, open_in_console
from .. import theme
from ..dialogs.base import Dialog, ask_yes_no, button, label, paint_caption, register, show_warning
from ..dialogs.fileview import open_with_system, show_in_folder
from ..timers import Ticker
from ..widgets.common import divider, fill_menu, scroll_area, section
from ..widgets.countlist import COUNT_ROLE, count_list
from ..widgets.tiles import Item, TileBoard, TilePainter, elide, rounded
from ..widgets.toast import Toast
from .base import TabPage
from ..errors import guard_events

SCHEDULE_TICK_MS = 20_000
PUMP_MS = 300
UNLOCK_DELAY_MS = 800  # the auto runs of an unlock wait for the main screen to be on screen
KIND_LABELS = {"script": "Script", "command": "Command", "url": "Link", "folder": "Folder", "file": "File"}
KIND_COLOURS = {"script": "", "command": "#4B5563", "url": "#0D9488", "folder": "#16A34A", "file": "#7C3AED"}  # "": the theme's link colour
RADIUS, BADGE_RADIUS, CHIP_RADIUS = 14, 11, 6
BADGE = 40
PAD_X, PAD_Y = 14, 12
GAP = 8
CHIP_PAD_X, CHIP_PAD_Y, CHIP_GAP = 7, 2, 6
ACTION_GAP = 14
RUN_ROLE = Qt.ItemDataRole.UserRole + 3


def kind_colour(r: Routine) -> str:
    return KIND_COLOURS.get(r.kind) or theme.current().LINK


@guard_events
class OutputWindow(QWidget):
    """The live log of one run with Stop / Run again / Close; closes on lock."""

    def __init__(self, shell: Any, run: Run):
        super().__init__(shell, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.shell, self.run = shell, run
        self.setWindowTitle(f"{run.routine.name} — {palette.APP_TITLE}")
        self.setObjectName("FileViewer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.resize(820, 520)
        register(shell, self)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(14, 10, 14, 10)
        head = label(f"{run.routine.avatar()}  {run.routine.name}", "", self)
        head.setFont(theme.font(13, bold=True))
        bar.addWidget(head)
        self.state = label("", "muted", self)
        bar.addWidget(self.state)
        bar.addStretch(1)
        self.stop_btn = button(_("Stop"), "danger", self)
        self.stop_btn.clicked.connect(run.stop)
        self.again_btn = button(_("Run again"), "small", self)
        self.again_btn.clicked.connect(self._again)
        self.again_btn.hide()
        close = button(_("Close"), "", self)
        close.clicked.connect(self.close)
        for w in (self.stop_btn, self.again_btn, close):
            bar.addWidget(w)
        col.addLayout(bar)
        self.text = QPlainTextEdit(self)
        self.text.setProperty("role", "console")
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.text.setFont(theme.font(10, mono=True))
        for line in run.lines:
            self.text.appendPlainText(line)
        col.addWidget(self.text, 1)
        QShortcut(QKeySequence("Escape"), self, self.close)
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        paint_caption(self)

    def _again(self) -> None:
        self.close()
        self.shell.runs.run_routine(self.run.routine)

    def append(self, line: str) -> None:
        bar = self.text.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        self.text.appendPlainText(line)
        if at_end:
            bar.setValue(bar.maximum())

    def refresh(self) -> None:
        run = self.run
        t = theme.current()
        if run.running:
            self.state.setText(_("running… {s} s").format(s=int(run.duration())))
            return
        ok = run.code == 0
        self.state.setText((_("✓ done in {s} s") if ok else _("✗ exit code {code}, {s} s")).format(code=run.code, s=round(run.duration(), 1)))
        self.state.setStyleSheet(f"color: {t.SUCCESS if ok else t.DANGER};")
        self.stop_btn.hide()
        self.again_btn.show()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.shell.runs.forget_window(self.run)
        event.accept()


class RoutineRuns:
    """The runs of routines through the life of the window: the log of runs, their output
    windows, the schedule tick and the pump that takes the lines of running processes.
    Made once with the window and kept through a lock."""

    def __init__(self, shell: Any):
        self.shell = shell
        self.runs: list[Run] = []
        self._windows: dict[int, OutputWindow] = {}
        self.tickers = (Ticker(shell, SCHEDULE_TICK_MS, self._tick), Ticker(shell, PUMP_MS, self._pump))
        for ticker in self.tickers:
            ticker.start()

    @property
    def page(self) -> RoutinesPage | None:
        return self.shell.pages.get("routines")

    def _update_tile(self, r: Routine) -> None:
        page = self.page
        if page is not None:
            page.update_tile(r)

    def _refresh_sidebar(self) -> None:
        page = self.page
        if page is not None:
            page.refresh_sidebar()

    def run_routine(self, r: Routine, auto: bool = False) -> None:
        """Start a routine (see routines_view.RoutineRuns.run_routine: the same rules)."""
        if not self._script_accepted(r, auto):
            return
        plan = launch_plan(r)
        if plan.problem:
            if plan.problem == "empty":
                msg = _("Nothing to run: the target is empty")
            elif plan.problem == "interpreter":
                msg = _("“{name}” is the program that runs scripts, not a script: started alone it ends at once. Pick the script or the shortcut itself, or put the script into “Arguments”.").format(name=os.path.basename(plan.target))
            else:
                msg = _("Not found: {path}").format(path=plan.target or r.target)
            if auto:
                Toast(self.shell, f"⚡ {r.name}", msg)
            else:
                show_warning(self.shell, msg)
            return
        r.mark_started(time.time())
        if plan.action == "open_url":
            webbrowser.open(plan.target)
            r.mark_finished(0)
        elif plan.action == "open_path":
            open_with_system(plan.target)
            r.mark_finished(0)
        elif r.terminal:
            if not open_in_console(plan):
                self.runs.insert(0, Run(r, plan.argv, plan.shell, plan.cwd))
        else:
            run = Run(r, plan.argv, plan.shell, plan.cwd, auto=auto)
            self.runs.insert(0, run)
            del self.runs[RUN_LOG_KEEP:]
            if run.ended is not None:  # could not start
                self._finish(run, auto)
        self.shell.mark_dirty(None)
        self.shell.set_status(_("Started: {name}").format(name=r.name))
        self._update_tile(r)
        self._refresh_sidebar()

    def _script_accepted(self, r: Routine, auto: bool) -> bool:
        state = check_script(r)
        if state in ("", SCRIPT_UNKNOWN) and not auto:
            accept_script(r)  # a run by hand is the consent to this version of the file
            return True
        if not state:
            return True
        if auto:
            if state == SCRIPT_CHANGED:
                Toast(self.shell, f"⚡ {r.name}", _("Did not start: the script changed since you last ran it. Run it by hand once to accept the new version."))
            elif state == SCRIPT_UNKNOWN:
                Toast(self.shell, f"⚡ {r.name}", _("Did not start by itself: run it by hand once to accept the script."))
            else:
                Toast(self.shell, f"⚡ {r.name}", _("Not found: {path}").format(path=r.target))
            self.shell.set_status(_("Not started: {name}").format(name=r.name))
            return False
        if state == SCRIPT_CHANGED:
            if not ask_yes_no(self.shell, _("“{name}”: the script file changed since you last ran it.\n\nRun this version? It becomes the accepted one and will start by itself again.").format(name=r.name)):
                return False
            accept_script(r)
            self.shell.mark_dirty(None)
        return True

    def _pump(self) -> None:
        changed = False
        for run in list(self.runs[:20]):
            if run.running:
                self._update_tile(run.routine)  # the seconds tick on the tile
            for kind, value in run.take():
                if kind == "line":
                    window = self._windows.get(id(run))
                    if window is not None:
                        window.append(value)
                else:
                    self._finish(run, run.auto)
                    changed = True
            window = self._windows.get(id(run))
            if window is not None:
                window.refresh()
        if changed:
            self._refresh_sidebar()

    def _finish(self, run: Run, auto: bool) -> None:
        run.routine.mark_finished(run.code)
        if self.shell.vault is not None:
            self.shell.mark_dirty(None)
        if run.code == 0 and not auto:
            Toast(self.shell, f"⚡ {run.routine.name}", _("✓ done in {s} s").format(s=round(run.duration(), 1)), action=lambda: self.open_output(run))
        elif run.code != 0:
            Toast(self.shell, f"⚡ {run.routine.name}", _("Failed with exit code {code}").format(code=run.code), action=lambda: self.open_output(run))
        self.shell.set_status(_("{name}: exit code {code}").format(name=run.routine.name, code=run.code))
        self._update_tile(run.routine)

    def _tick(self) -> None:
        if self.shell.vault is None or not self.shell.background_jobs or not self.shell.tab_on("routines"):
            return
        for r in self.shell.vault.routines.due(now_local()):
            self.run_routine(r, auto=True)

    def on_unlock(self) -> None:
        if self.shell.vault is None or not self.shell.background_jobs or not self.shell.tab_on("routines"):
            return
        for r in self.shell.vault.routines.on_unlock():
            self.run_routine(r, auto=True)

    def on_unlock_later(self) -> None:
        QTimer.singleShot(UNLOCK_DELAY_MS, self.shell, self.on_unlock)

    def latest(self, r: Routine) -> Run | None:
        return next((run for run in self.runs if run.routine.id == r.id), None)

    def stop(self, r: Routine) -> None:
        run = self.latest(r)
        if run is not None:
            run.stop()

    def open_output(self, run: Run | None) -> OutputWindow | None:
        """The output of a run in its own window; one window per run."""
        if run is None:
            return None
        window = self._windows.get(id(run))
        if window is None:
            window = self._windows[id(run)] = OutputWindow(self.shell, run)
        window.show()
        window.raise_()
        return window

    def forget_window(self, run: Run) -> None:
        self._windows.pop(id(run), None)


class RoutinePainter(TilePainter):
    """One routine: a badge with its glyph, the name on one or two lines, what it starts,
    the schedule chips, the state of the last run and the actions."""

    def __init__(self, page: RoutinesPage):
        self.page = page
        self.f = {"title": theme.font(12, bold=True), "small": theme.font(9), "row": theme.font(10, bold=True), "avatar": theme.font(16)}
        self.fm = {k: QFontMetrics(v) for k, v in self.f.items()}

    def chips(self, r: Routine) -> list[str]:
        chips = []
        if r.schedule.on_unlock:
            chips.append(_("⚡ on unlock"))
        when = describe_schedule(r.schedule, self.page.days())
        if when:
            chips.append(f"⏰ {when}")
        if r.terminal:
            chips.append(_("console"))
        return chips

    def title_lines(self, r: Routine, width: int) -> list[str]:
        room = self._title_room(width)
        text = r.name or _("(untitled)")
        fm = self.fm["title"]
        if fm.horizontalAdvance(text) <= room:
            return [text]
        words = text.split()
        first, rest = "", words
        for i in range(len(words), 0, -1):
            if fm.horizontalAdvance(" ".join(words[:i])) <= room:
                first, rest = " ".join(words[:i]), words[i:]
                break
        if not first:
            first = elide(fm, text, room).rstrip("…")
            rest = [text[len(first):]]
        return [first, elide(fm, " ".join(rest), room)]

    def _title_room(self, width: int) -> int:
        more_w = self.fm["title"].horizontalAdvance("⋯") + 8
        return width - 2 * PAD_X - BADGE - 12 - more_w

    def height(self, r: Routine, width: int) -> int:
        lines = self.title_lines(r, width)
        head = max(BADGE, len(lines) * self.fm["title"].lineSpacing() + 2 + self.fm["small"].lineSpacing())
        h = PAD_Y + head
        if self.chips(r):
            h += GAP + self.fm["small"].lineSpacing() + 2 * CHIP_PAD_Y
        h += GAP + self.fm["small"].lineSpacing()
        h += GAP + self.fm["row"].lineSpacing() + 4
        return h + PAD_Y

    def _actions(self, r: Routine, rect: QRect) -> list[tuple[str, str, QRect]]:
        _text, _colour, busy, has_run = self.page.run_status(r)
        y = rect.bottom() - PAD_Y - self.fm["row"].lineSpacing() - 3
        x = rect.x() + PAD_X
        out = []
        for tag, text in ((("stop", _("■ Stop")) if busy else ("run", _("▶ Run"))), *((("output", _("Output")),) if has_run else ())):
            w = self.fm["row"].horizontalAdvance(text)
            out.append((tag, text, QRect(x, y, w, self.fm["row"].lineSpacing())))
            x += w + ACTION_GAP
        return out

    def zones(self, r: Routine, rect: QRect) -> list[tuple[str, QRect]]:
        more_w = self.fm["title"].horizontalAdvance("⋯") + 8
        out = [("more", QRect(rect.right() - PAD_X - more_w, rect.y() + PAD_Y - 4, more_w + 6, self.fm["title"].height() + 6))]
        out += [(tag, r_.adjusted(-3, -2, 3, 2)) for tag, _t, r_ in self._actions(r, rect)]
        tx = rect.x() + PAD_X + BADGE + 12
        lines = self.title_lines(r, rect.width())
        sy = rect.y() + PAD_Y + len(lines) * self.fm["title"].lineSpacing() + 2
        out.append(("sub", QRect(tx, sy, rect.right() - PAD_X - tx, self.fm["small"].lineSpacing())))
        return out

    def paint(self, p: QPainter, rect: QRect, r: Routine, hot: bool, focused: bool) -> None:
        t = theme.current()
        lit = hot or focused
        colour = kind_colour(r)
        rounded(p, QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, t.TILE, t.ACCENT if lit else t.BORDER, 1.5 if lit else 1.0)
        x0, y0, right = rect.x() + PAD_X, rect.y() + PAD_Y, rect.right() - PAD_X
        badge = QRect(x0, y0, BADGE, BADGE)
        rounded(p, QRectF(badge), BADGE_RADIUS, theme.blend(colour, t.TILE, 0.14))
        p.setFont(self.f["avatar"])
        p.setPen(QColor(colour))
        p.drawText(badge, int(Qt.AlignmentFlag.AlignCenter), r.avatar())
        p.setFont(self.f["title"])
        p.setPen(QColor(t.MUTED))
        more = self.zones(r, rect)[0][1]
        p.drawText(more, int(Qt.AlignmentFlag.AlignCenter), "⋯")
        tx = x0 + BADGE + 12
        th = self.fm["title"].lineSpacing()
        y = y0
        p.setPen(QColor(t.LINK if lit else t.TEXT))
        lines = self.title_lines(r, rect.width())
        for line in lines:
            p.drawText(QRect(tx, y, right - tx, th), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), line)
            y += th
        p.setFont(self.f["small"])
        p.setPen(QColor(t.MUTED))
        p.drawText(QRect(tx, y + 2, right - tx, self.fm["small"].lineSpacing()), int(Qt.AlignmentFlag.AlignLeft), elide(self.fm["small"], f"{self.page.kind_label(r)} · {subtitle(r)}", right - tx))
        y = y0 + max(BADGE, len(lines) * th + 2 + self.fm["small"].lineSpacing())
        chips = self.chips(r)
        if chips:
            y += GAP
            cx, ch = x0, self.fm["small"].lineSpacing() + 2 * CHIP_PAD_Y
            for chip in chips:
                cw = self.fm["small"].horizontalAdvance(chip) + 2 * CHIP_PAD_X
                if cx + cw > right and cx > x0:
                    break
                rounded(p, QRectF(cx, y, cw, ch), CHIP_RADIUS, t.ACCENT_SOFT)
                p.setPen(QColor(t.ACCENT_INK))
                p.drawText(QRect(cx, y, cw, ch), int(Qt.AlignmentFlag.AlignCenter), chip)
                cx += cw + CHIP_GAP
            y += ch
        y += GAP
        text, status_colour, _busy, _has_run = self.page.run_status(r)
        p.setPen(QColor(status_colour))
        p.drawText(QRect(x0, y, right - x0, self.fm["small"].lineSpacing()), int(Qt.AlignmentFlag.AlignLeft), elide(self.fm["small"], text, right - x0))
        p.setFont(self.f["row"])
        p.setPen(QColor(t.LINK))
        for _tag, action, ar in self._actions(r, rect):
            p.drawText(ar, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), action)

    def tip(self, r: Routine, zone: str) -> str:
        if zone != "sub":
            return ""
        text = (r.target + (" " + r.args if r.args.strip() else "")).strip()
        if r.kind == "script" and r.target.strip().lower().endswith(".ps1"):
            text += "\n" + _("PowerShell, with the execution policy bypassed for this file")
        return text


class RoutinesPage(TabPage):
    """The Routine tab: the tiles, the sidebar with today's schedule and the run log."""

    name = "routines"

    def build(self) -> None:
        _splitter, left, col, right = self.split(330, 240)
        col.addWidget(section(_("TODAY"), left))
        self.today_box = QVBoxLayout()
        self.today_box.setSpacing(2)
        col.addLayout(self.today_box)
        col.addSpacing(6)
        col.addWidget(section(_("RUN LOG"), left))
        self.log = count_list(left)
        self.log.setToolTip(_("Double-click shows the output"))
        self.log.itemDoubleClicked.connect(self._open_log_entry)
        col.addWidget(self.log, 1)
        add = button(_("+ Routine"), "primary", left)
        add.clicked.connect(lambda: self.routine_dialog(None))
        col.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)
        self.area, inner, icol = scroll_area(right, (24, 20, 24, 20))
        self.painter = RoutinePainter(self)
        self.board = TileBoard(self.painter, self._activate, self.routine_menu, inner)
        icol.addWidget(self.board)
        icol.addStretch(1)
        rl = QHBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.area)

    # ---- what the window asks for

    def refresh(self, keep_scroll: bool = False) -> None:
        if self.vault is None:
            return
        top = self.area.verticalScrollBar().value()
        items = [Item("heading", _("Daily routine"))]
        routines = self.vault.routines.ordered()
        if not routines:
            items.append(Item("hint", _("“+ Routine” on the left adds a tile: a script, a shell command, a link, a folder or a file. ▶ starts it; it can also start by itself when the vault is unlocked or at a time of day.")))
        items += [Item("tile", r) for r in routines]
        self.board.set_items(items)
        self.area.verticalScrollBar().setValue(top if keep_scroll else 0)
        self.refresh_sidebar()

    def on_shown(self) -> None:
        self.board.update()  # the clock moved: last run, what is due today
        self.refresh_sidebar()

    def new_item(self) -> None:
        self.routine_dialog(None)

    # ---- tiles

    def days(self) -> list[str]:
        return [_(d) for d in WEEKDAYS]

    def kind_label(self, r: Routine) -> str:
        return _(KIND_LABELS[r.kind])

    def run_status(self, r: Routine) -> tuple[str, str, bool, bool]:
        """(text, colour, running now, a run to show)."""
        t = theme.current()
        run = self.shell.runs.latest(r)
        if run is not None and run.running:
            return "⏳ " + _("running… {s} s").format(s=int(run.duration())), t.LINK, True, True
        if run is not None:
            ok = run.code == 0
            when = time.strftime("%d.%m %H:%M", time.localtime(run.started))
            text = (_("✓ {when} · done in {s} s") if ok else _("✗ {when}, exit code {code}, {s} s")).format(when=when, code=run.code, s=round(run.duration(), 1))
            return text, t.SUCCESS if ok else t.DANGER, False, True
        if not r.last_run:
            return _("never run"), t.MUTED, False, False
        stamp = time.strftime("%d.%m %H:%M", time.localtime(r.last_run))
        if r.last_code is None:
            return _("started {when}").format(when=stamp), t.MUTED, False, False
        text = (_("✓ {when}") if r.last_code == 0 else _("✗ {when}, exit code {code}")).format(when=stamp, code=r.last_code)
        return text, t.SUCCESS if r.last_code == 0 else t.DANGER, False, False

    def update_tile(self, r: Routine) -> None:
        i = next((k for k, it in enumerate(self.board.items) if it.kind == "tile" and it.data.id == r.id), -1)
        if i >= 0:
            self.board.update(self.board.rect_of(i))

    def _activate(self, r: Routine, zone: str) -> None:
        runs = self.shell.runs
        if zone in ("run", "enter", "double"):
            runs.run_routine(r)
        elif zone == "stop":
            runs.stop(r)
        elif zone == "output":
            runs.open_output(runs.latest(r))

    def refresh_sidebar(self) -> None:
        if self.vault is None:
            return
        while self.today_box.count():
            item = self.today_box.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        now = now_local()
        today = [(r.schedule.valid_time(), r) for r in self.vault.routines.ordered() if r.schedule.valid_time() and now.weekday() in r.schedule.days]
        today.sort(key=lambda x: x[0])
        if not today:
            self.today_box.addWidget(label(_("Nothing scheduled today"), "muted"))
        for (h, m), r in today:
            done = r.schedule.due(now, r.last_run) is False and now.replace(hour=h, minute=m) <= now
            row = QLabel(f"{'✓' if done else '○'}  {h:02d}:{m:02d}  {r.name}")
            row.setFont(theme.font(10, bold=True))
            if done:
                row.setProperty("role", "muted")
            self.today_box.addWidget(row)
        self.log.clear()
        for run in self.shell.runs.runs:
            code = "…" if run.running else ("✓" if run.code == 0 else f"✗ {run.code}")
            log_row = QListWidgetItem(f"{time.strftime('%H:%M', time.localtime(run.started))}  {run.routine.name}", self.log)
            log_row.setData(RUN_ROLE, id(run))
            log_row.setData(COUNT_ROLE, code)

    def _open_log_entry(self, item: QListWidgetItem) -> None:
        key = item.data(RUN_ROLE)
        self.shell.runs.open_output(next((x for x in self.shell.runs.runs if id(x) == key), None))

    def routine_menu(self, r: Routine, pos: QPoint) -> None:
        menu = QMenu(self.board)
        items: list = [(_("▶ Run"), lambda: self.shell.runs.run_routine(r)), (_("Edit…"), lambda: self.routine_dialog(r))]
        if r.kind in ("script", "file", "folder") and r.target:
            items.append((_("Show in folder"), lambda: self._show_in_folder(r)))
        items += [None, (_("Move left"), lambda: self._move(r, -1)), (_("Move right"), lambda: self._move(r, +1)), None, (_("Delete"), lambda: self._delete(r))]
        fill_menu(menu, items)
        menu.popup(pos)
        self.last_menu = menu

    @staticmethod
    def _show_in_folder(r: Routine) -> None:
        if os.path.exists(r.target):
            show_in_folder(r.target)

    def _move(self, r: Routine, delta: int) -> None:
        if self.vault.routines.move(r, delta):
            self.shell.mark_dirty("routines")
            self.refresh(keep_scroll=True)

    def _delete(self, r: Routine) -> None:
        if ask_yes_no(self.shell, _("Delete routine “{name}”?").format(name=r.name)):
            self.vault.routines.remove(r)
            self.shell.mark_dirty("routines")
            self.refresh(keep_scroll=True)

    # ---- the editor

    def routine_dialog(self, r: Routine | None) -> Dialog | None:
        if self.vault is None:
            return None
        dlg = Dialog(self.shell, _("New routine") if r is None else _("Routine"))
        width = QFontMetrics(dlg.font()).averageCharWidth() * 46
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        name = QLineEdit(r.name if r else "", dlg)
        name.setMinimumWidth(width)
        kind = QComboBox(dlg)
        for k in KINDS:
            kind.addItem(_(KIND_LABELS[k]), k)
        kind.setCurrentIndex(list(KINDS).index(r.kind) if r else 0)
        target_label = QLabel("", dlg)
        target = QLineEdit(r.target if r else "", dlg)
        browse = button(_("Browse…"), "small", dlg)
        where = label("", "muted", dlg)
        where.setWordWrap(True)
        args_label, args = QLabel(_("Arguments"), dlg), QLineEdit(r.args if r else "", dlg)
        cwd_label, cwd = QLabel(_("Working folder"), dlg), QLineEdit(r.cwd if r else "", dlg)
        cwd_browse = button(_("Browse…"), "small", dlg)
        terminal = QCheckBox(_("Open in a separate console window (for interactive scripts)"), dlg)
        terminal.setChecked(r.terminal if r else False)
        icon_row = QWidget(dlg)
        il = QHBoxLayout(icon_row)
        il.setContentsMargins(0, 0, 0, 0)
        icon = QLineEdit(r.icon if r else "", icon_row)
        icon.setFixedWidth(QFontMetrics(icon.font()).averageCharWidth() * 6 + 20)
        il.addWidget(icon)
        il.addWidget(label(_("An emoji; empty — the icon of the type"), "muted", icon_row))
        il.addStretch(1)
        rows = [(QLabel(_("Name"), dlg), name, None), (QLabel(_("Type"), dlg), kind, None), (target_label, target, browse), (args_label, args, None), (cwd_label, cwd, cwd_browse)]
        for i, (lab, field, extra) in enumerate(rows):
            grid.addWidget(lab, i, 0)
            grid.addWidget(field, i, 1)
            if extra is not None:
                grid.addWidget(extra, i, 2)
        grid.addWidget(terminal, 5, 1, 1, 2)
        grid.addWidget(QLabel(_("Icon"), dlg), 6, 0)
        grid.addWidget(icon_row, 6, 1, 1, 2)
        grid.addWidget(where, 7, 1, 1, 2)
        dlg.box.addLayout(grid, 0, 0)
        dlg.box.addWidget(divider(dlg), 1, 0)
        when = QGridLayout()
        title = QLabel(_("Start by itself"), dlg)
        title.setFont(theme.font(11, bold=True))
        when.addWidget(title, 0, 0, 1, 3)
        unlock = QCheckBox(_("Each time the vault is unlocked"), dlg)
        unlock.setChecked(r.schedule.on_unlock if r else False)
        when.addWidget(unlock, 1, 0, 1, 3)
        when.addWidget(QLabel(_("At (HH:MM)"), dlg), 2, 0)
        at = QLineEdit(r.schedule.at if r else "", dlg)
        at.setFixedWidth(QFontMetrics(at.font()).averageCharWidth() * 8 + 20)
        when.addWidget(at, 2, 1)
        when.addWidget(label(_("empty — not by time"), "muted", dlg), 2, 2)
        days_row = QHBoxLayout()
        day_boxes = []
        for d, day in enumerate(self.days()):
            box = QCheckBox(day, dlg)
            box.setChecked(d in (r.schedule.days if r else [0, 1, 2, 3, 4]))
            days_row.addWidget(box)
            day_boxes.append(box)
        days_row.addStretch(1)
        when.addLayout(days_row, 3, 0, 1, 3)
        dlg.box.addLayout(when, 2, 0)
        err = label("", "error", dlg)
        dlg.box.addWidget(err, 3, 0)

        def current_kind() -> str:
            return kind.currentData() or "script"

        def on_target(_t: str = "") -> None:
            place = risky_location(target.text()) if current_kind() == "script" else ""
            if place:
                where_text = {"share": _("on a network share"), "downloads": _("in Downloads"), "outside": _("outside your profile folder")}[place]
                where.setText(_("The script is {where}: someone else may be able to change it. Saving accepts the file as it is now; a changed file does not start by itself.").format(where=where_text))
                where.show()
            else:
                where.hide()

        def on_kind(_i: int = 0) -> None:
            k = current_kind()
            target_label.setText({"script": _("Script file"), "command": _("Command"), "url": _("Address"), "folder": _("Folder"), "file": _("File")}[k])
            runs = k in ("script", "command")
            for field in (args_label, args):
                field.setVisible(k == "script")
            for part in (cwd_label, cwd, cwd_browse, terminal):
                part.setVisible(runs)
            browse.setVisible(k not in ("url", "command"))
            on_target()

        def do_browse() -> None:
            k = current_kind()
            if k == "folder":
                path = QFileDialog.getExistingDirectory(dlg)
            else:
                filt = _("Scripts") + " (*.py *.ps1 *.bat *.cmd *.sh *.exe *.lnk);;" + _("All files") + " (*.*)" if k == "script" else _("All files") + " (*.*)"
                path, _f = QFileDialog.getOpenFileName(dlg, "", "", filt)
            if path:
                target.setText(os.path.normpath(path))
                if not name.text().strip():
                    name.setText(os.path.splitext(os.path.basename(path))[0])
                on_target()

        def pick_cwd() -> None:
            folder = QFileDialog.getExistingDirectory(dlg)
            if folder:
                cwd.setText(folder)

        browse.clicked.connect(do_browse)
        cwd_browse.clicked.connect(pick_cwd)
        kind.currentIndexChanged.connect(on_kind)
        target.textChanged.connect(on_target)
        on_kind()

        def save() -> None:
            k = current_kind()
            what = target.text().strip()
            if not what:
                err.setText(_("Fill in what to start"))
                return
            schedule = Schedule(unlock.isChecked(), at.text().strip(), [d for d in range(7) if day_boxes[d].isChecked()])
            if schedule.at and schedule.valid_time() is None:
                err.setText(_("Time must look like 09:30"))
                return
            fields = {
                "name": name.text().strip() or os.path.basename(what) or what, "kind": k, "target": what,
                "args": args.text().strip() if k == "script" else "", "cwd": cwd.text().strip() if k in ("script", "command") else "",
                "terminal": terminal.isChecked() if k in ("script", "command") else False, "icon": icon.text().strip(), "schedule": schedule,
            }
            saved = self.vault.routines.upsert(r, fields, now_local())
            accept_script(saved)  # saving the tile is the consent to the file as it is now
            self.shell.mark_dirty("routines")
            dlg.accept()
            self.refresh(keep_scroll=True)

        def delete() -> None:
            dlg.accept()
            if r is not None:
                self._delete(r)

        dlg.editor_buttons(4, 1, save, delete=delete if r is not None else None)
        dlg.keys(save)
        name.setFocus()
        dlg.open()
        self.last_dialog = dlg
        self.dialog_parts = {"name": name, "kind": kind, "target": target, "at": at, "unlock": unlock, "error": err, "save": save, "args": args}
        return dlg
