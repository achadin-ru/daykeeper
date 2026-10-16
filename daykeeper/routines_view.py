"""The “Routine” tab: quick-launch tiles for scripts, commands, links, folders and files.

▶ runs a routine. Scripts and commands run in the background: the tile shows that the
routine runs and then how it ended, a note pops up in the corner, the output is a click
away; with “Open in a console” they run in a separate console window instead. A routine
can also start by itself each time the vault is unlocked, or at a time of day on chosen
weekdays (once a day; if the app was closed at that time, when it is opened later that
day). Automatic runs work quietly: the result goes to the run log on the left, a failure
also pops up in the corner.

A page of the window (TabPage); the runs are the window's `RoutineRuns`."""

from __future__ import annotations

import os
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, ttk

from . import palette
from .dialogs import Dialog, ask_yes_no, show_warning
from .i18n import _
from .popup import Dropdown, PopupMenu
from .routine_tile import RoutineTile
from .md_view import open_with_system, show_in_folder
from .routines import KINDS, RUN_LOG_KEEP, SCRIPT_CHANGED, SCRIPT_UNKNOWN, WEEKDAYS, Routine, Schedule, accept_script, check_script, launch_plan, now_local, risky_location
from .runner import Run, open_in_console
from .sidebar import SidebarKeeper
from .tabpage import Shell, TabPage, make_scrollable
from .timers import Ticker
from .toast import Toast
from .widgets import TileGrid, Tooltip, make_fonts, divider

SCHEDULE_TICK_MS = 20_000
KIND_LABELS = {"script": "Script", "command": "Command", "url": "Link", "folder": "Folder", "file": "File"}
KIND_COLOURS = {"script": palette.LINK, "command": "#4B5563", "url": "#0D9488", "folder": "#16A34A", "file": "#7C3AED"}


class OutputWindow(tk.Toplevel):
    """The live log of one run with Stop / Run again / Close."""

    closes_on_lock = True  # App.close_child_windows

    def __init__(self, app, run: Run):
        super().__init__(app)
        self.app, self.run = app, run
        self.title(f"{run.routine.name} — {palette.APP_TITLE}")
        self.geometry("820x520")
        self.configure(bg=palette.CARD)
        bar = tk.Frame(self, bg=palette.CARD, padx=14, pady=10)
        bar.pack(fill="x")
        tk.Label(bar, text=f"{run.routine.avatar()}  {run.routine.name}", bg=palette.CARD, fg=palette.TEXT, font=(palette.FONT, palette.pt(13), "bold")).pack(side="left")
        self.state_label = tk.Label(bar, text="", bg=palette.CARD, fg=palette.MUTED, font=(palette.FONT, palette.pt(10)))
        self.state_label.pack(side="left", padx=(12, 0))
        ttk.Button(bar, text=_("Close"), command=self.destroy).pack(side="right")
        self.again_btn = ttk.Button(bar, text=_("Run again"), style="Small.TButton", command=lambda: (self.destroy(), app.runs.run_routine(run.routine)))
        self.stop_btn = ttk.Button(bar, text=_("Stop"), style="Danger.TButton", command=run.stop)
        self.stop_btn.pack(side="right", padx=(0, 8))
        body = tk.Frame(self, bg=palette.CARD)
        body.pack(fill="both", expand=True)
        vsb = ttk.Scrollbar(body, orient="vertical")
        vsb.pack(side="right", fill="y")
        self.text = tk.Text(body, bg=palette.CONSOLE_BG, fg=palette.CONSOLE_TEXT, insertbackground=palette.CONSOLE_TEXT, font=(palette.MONO, palette.pt(10)), relief="flat", padx=12, pady=10, wrap="char", yscrollcommand=vsb.set)
        self.text.pack(side="left", fill="both", expand=True)
        vsb.config(command=self.text.yview)
        for line in run.lines:
            self.text.insert("end", line + "\n")
        self.text.config(state="disabled")
        self.bind("<Escape>", lambda _e: self.destroy())
        self.refresh()

    def append(self, line: str) -> None:
        if not self.winfo_exists():
            return
        at_end = self.text.yview()[1] > 0.98
        self.text.config(state="normal")
        self.text.insert("end", line + "\n")
        self.text.config(state="disabled")
        if at_end:
            self.text.see("end")

    def refresh(self) -> None:
        if not self.winfo_exists():
            return
        run = self.run
        if run.running:
            self.state_label.config(text=_("running… {s} s").format(s=int(run.duration())))
            return
        ok = run.code == 0
        self.state_label.config(
            text=(_("✓ done in {s} s") if ok else _("✗ exit code {code}, {s} s")).format(code=run.code, s=round(run.duration(), 1)),
            fg=palette.SUCCESS if ok else palette.DANGER,
        )
        self.stop_btn.pack_forget()
        self.again_btn.pack(side="right", padx=(0, 8))


class RoutineRuns:
    """The runs of routines through the life of the window: the log of runs, their output
    windows, the schedule tick and the pump that takes the lines of running processes.
    Made once with the window and kept through a lock, so a run that ends while the
    vault is locked still pops up its result and is in the log after the unlock. The
    tiles and the sidebar of the Routine tab follow when the tab is there."""

    def __init__(self, app: Shell):
        self.app = app
        self.runs: list[Run] = []
        self._run_windows: dict[int, OutputWindow] = {}
        self.tickers = (Ticker(app, SCHEDULE_TICK_MS, self._routine_tick), Ticker(app, 300, self._pump_runs))
        for ticker in self.tickers:
            ticker.start()

    @property
    def page(self) -> RoutinesPage | None:
        return self.app.pages.get("routines")

    def _page_update_tile(self, r: Routine) -> None:
        page = self.page
        if page is not None:
            page.update_routine_tile(r)

    def _page_refresh_sidebar(self) -> None:
        page = self.page
        if page is not None:
            page.refresh_routine_sidebar()

    def run_routine(self, r: Routine, auto: bool = False) -> None:
        """Start a routine. auto — started by the schedule: quiet, only a failure pops up,
        and only the script the user accepted runs (check_script); a changed or never
        accepted script waits for a run by hand, which is the acceptance."""
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
                Toast(self.app, f"⚡ {r.name}", msg)
            else:
                show_warning(self.app, msg)
            return
        r.mark_started(time.time())
        if plan.action == "open_url":
            webbrowser.open(plan.target)
            r.mark_finished(0)
        elif plan.action == "open_path":
            open_with_system(plan.target)
            r.mark_finished(0)
        elif r.terminal:
            self._run_in_console(r, plan)
        else:
            run = Run(r, plan.argv, plan.shell, plan.cwd, auto=auto)
            self.runs.insert(0, run)
            del self.runs[RUN_LOG_KEEP:]
            if run.ended is not None:  # could not start
                self._finish_run(run, auto)
        self.app.mark_dirty(None)  # the tile and the sidebar are updated below
        self.app.set_status(_("Started: {name}").format(name=r.name))
        self._page_update_tile(r)
        self._page_refresh_sidebar()

    def _script_accepted(self, r: Routine, auto: bool) -> bool:
        state = check_script(r)
        if state in ("", SCRIPT_UNKNOWN) and not auto:
            accept_script(r)  # a run by hand is the consent to this version of the file
            return True
        if not state:
            return True
        if auto:
            if state == SCRIPT_CHANGED:
                Toast(self.app, f"⚡ {r.name}", _("Did not start: the script changed since you last ran it. Run it by hand once to accept the new version."))
            elif state == SCRIPT_UNKNOWN:
                Toast(self.app, f"⚡ {r.name}", _("Did not start by itself: run it by hand once to accept the script."))
            else:
                Toast(self.app, f"⚡ {r.name}", _("Not found: {path}").format(path=r.target))
            self.app.set_status(_("Not started: {name}").format(name=r.name))
            return False
        if state == SCRIPT_CHANGED:
            if not ask_yes_no(self.app, _("“{name}”: the script file changed since you last ran it.\n\nRun this version? It becomes the accepted one and will start by itself again.").format(name=r.name)):
                return False
            accept_script(r)
            self.app.mark_dirty(None)
        return True  # unreadable: launch_plan says "not found"

    def _run_in_console(self, r: Routine, plan) -> None:
        """A separate console window that stays open after the script ends; without a
        terminal program (Linux) the routine runs quietly instead."""
        if not open_in_console(plan):
            self.runs.insert(0, Run(r, plan.argv, plan.shell, plan.cwd))

    def _pump_runs(self) -> None:
        changed = False
        for run in list(self.runs[:20]):
            if run.running:
                self._page_update_tile(run.routine)  # the seconds tick on the tile
            for kind, value in run.take():
                if kind == "line":
                    window = self._run_windows.get(id(run))
                    if window is not None:
                        window.append(value)
                else:
                    self._finish_run(run, run.auto)
                    changed = True
            window = self._run_windows.get(id(run))
            if window is not None:
                if window.winfo_exists():
                    window.refresh()
                else:
                    self._run_windows.pop(id(run), None)
        if changed:
            self._page_refresh_sidebar()

    def _finish_run(self, run: Run, auto: bool) -> None:
        run.routine.mark_finished(run.code)
        if self.app.vault is not None:
            self.app.mark_dirty(None)  # the tile is updated below, the sidebar by _pump_runs
        if run.code == 0 and not auto:
            Toast(self.app, f"⚡ {run.routine.name}", _("✓ done in {s} s").format(s=round(run.duration(), 1)), action=lambda: self.open_output(run))
        elif run.code != 0:
            Toast(self.app, f"⚡ {run.routine.name}", _("Failed with exit code {code}").format(code=run.code), action=lambda: self.open_output(run))
        self.app.set_status(_("{name}: exit code {code}").format(name=run.routine.name, code=run.code))
        self._page_update_tile(run.routine)

    def _routine_tick(self) -> None:
        if self.app.vault is None or not self.app.background_jobs or not self.app.tab_on("routines"):
            return
        for r in self.app.vault.routines.due(now_local()):
            self.run_routine(r, auto=True)

    def on_unlock(self) -> None:
        if not self.app.background_jobs or not self.app.tab_on("routines"):
            return
        for r in self.app.vault.routines.on_unlock():
            self.run_routine(r, auto=True)

    def latest(self, r: Routine) -> Run | None:
        return next((run for run in self.runs if run.routine.id == r.id), None)

    def stop(self, r: Routine) -> None:
        run = self.latest(r)
        if run is not None:
            run.stop()

    def open_output(self, run: Run | None) -> None:
        """The output of a run in its own window; one window per run."""
        if run is None:
            return
        window = self._run_windows.get(id(run))
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            return
        self._run_windows[id(run)] = OutputWindow(self.app, run)


class RoutinesPage(TabPage):
    """The Routine tab: the tiles, the sidebar with today's schedule and the run log; the
    runs themselves are the window's `runs`."""

    name = "routines"

    def __init__(self, app: Shell, parent: tk.Misc):
        self._rtiles_by_id: dict[str, RoutineTile] = {}  # routine id → its tile, redrawn when a run begins or ends
        self._rtiles: list[tk.Widget] = []
        super().__init__(app, parent)

    # ---- what the window asks for

    def refresh(self, keep_scroll: bool = False) -> None:
        self._refresh_routines(keep_scroll=keep_scroll)

    def on_shown(self) -> None:
        self._refresh_routine_status()  # the clock moved: last run, what is due today

    def new_item(self) -> None:
        self.routine_dialog(None)


    # ---- the tab

    def build(self, frame: ttk.Frame) -> None:
        paned = ttk.PanedWindow(frame, orient="horizontal")
        paned.pack(fill="both", expand=True)
        left = ttk.Frame(paned, style="Sidebar.TFrame", padding=(16, 16, 12, 12))
        paned.add(left, weight=0)
        ttk.Label(left, text=_("TODAY"), style="SidebarMuted.TLabel", font=(palette.FONT, palette.pt(10), "bold")).pack(anchor="w", pady=(0, 6))
        self.rtoday = tk.Frame(left, bg=palette.SIDEBAR)
        self.rtoday.pack(fill="x")
        ttk.Label(left, text=_("RUN LOG"), style="SidebarMuted.TLabel", font=(palette.FONT, palette.pt(10), "bold")).pack(anchor="w", pady=(16, 6))
        self.rlog = ttk.Treeview(left, columns=("code",), show="tree", selectmode="browse")
        self.rlog.column("#0", width=200, stretch=True)
        self.rlog.column("code", width=44, anchor="e", stretch=False)
        self.rlog.pack(fill="both", expand=True)
        self.rlog.bind("<Double-1>", self._open_log_entry)
        Tooltip(self.rlog, _("Double-click shows the output"))
        btns = ttk.Frame(left, style="Sidebar.TFrame")
        btns.pack(side="bottom", fill="x", pady=(12, 0), before=self.rlog)  # packed first from the bottom: a short window shrinks the list, not the buttons
        ttk.Button(btns, text=_("+ Routine"), style="Primary.TButton", command=lambda: self.routine_dialog(None)).pack(side="left")

        host = ttk.Frame(paned, style="Card.TFrame")
        paned.add(host, weight=1)
        SidebarKeeper(paned, minimum=self.app.painter.px(240), width=self.app.painter.px(330))
        self.rcanvas, _vsb, self.rgrid = make_scrollable(self.app, host, (24, 20, 24, 20))
        self._rtile_grid = TileGrid(self.rgrid)  # the heading, the hint and the tiles in the columns the width allows
        self._rfonts = make_fonts({"title": (12, "bold"), "small": (9,), "row": (10, "bold"), "avatar": (16,), "head": (17, "bold")})

    def _refresh_routines(self, keep_scroll: bool = False) -> None:
        if self.vault is None:
            return
        top = self.rcanvas.yview()[0]
        for child in self.rgrid.winfo_children():
            child.destroy()
        self._rtiles_by_id.clear()
        items = self.vault.routines.ordered()
        head = tk.Label(self.rgrid, text=_("Daily routine"), bg=palette.CARD, fg=palette.TEXT, font=self._rfonts["head"])
        layout: list[tuple[str, object]] = [("line", (head, (0, 12)))]
        if not items:
            hint = tk.Label(
                self.rgrid,
                text=_("“+ Routine” on the left adds a tile: a script, a shell command, a link, a folder or a file. ▶ starts it; it can also start by itself when the vault is unlocked or at a time of day."),
                bg=palette.CARD, fg=palette.MUTED, font=self._rfonts["row"], justify="left", wraplength=600,
            )
            layout.append(("line", (hint, (0, 0))))
        self._rtiles = []
        layout.append(("tiles", self._rtiles))
        self._rtile_grid.layout = layout

        def build(r: Routine) -> None:
            self._rtiles.append(self._build_routine_tile(r))

        def done() -> None:
            self._rtile_grid.restack()  # Tab walks the tiles in the order shown
            self.rcanvas.update_idletasks()
            self.rcanvas.yview_moveto(top if keep_scroll else 0)
            self.refresh_routine_sidebar()

        self.app.build_in_chunks("routines", items, build, self._rtile_grid.place, done)

    def _days(self) -> list[str]:
        return [_(d) for d in WEEKDAYS]

    def kind_colour(self, r: Routine) -> str:
        return KIND_COLOURS.get(r.kind, palette.ACCENT)

    def kind_label(self, r: Routine) -> str:
        return _(KIND_LABELS[r.kind])

    def _build_routine_tile(self, r: Routine) -> RoutineTile:
        tile = self._rtiles_by_id[r.id] = RoutineTile(self.rgrid, self, r)
        return tile

    def run_status(self, r: Routine) -> tuple[str, str, bool, bool]:
        """What the tile says about the run: (text, colour, running now, a run to show)."""
        run = self.app.runs.latest(r)
        if run is not None and run.running:
            return "⏳ " + _("running… {s} s").format(s=int(run.duration())), palette.LINK, True, True
        if run is not None:
            ok = run.code == 0
            when = time.strftime("%d.%m %H:%M", time.localtime(run.started))  # when it ran, not only how long
            text = (_("✓ {when} · done in {s} s") if ok else _("✗ {when}, exit code {code}, {s} s")).format(when=when, code=run.code, s=round(run.duration(), 1))
            return text, palette.SUCCESS if ok else palette.DANGER, False, True
        return self._last_run_text(r), self._last_run_colour(r), False, False

    def update_routine_tile(self, r: Routine) -> None:
        """The run state on the tile: running with Stop, or how the last run ended."""
        tile = self._rtiles_by_id.get(r.id)
        if tile is not None and tile.winfo_exists():
            tile.update_state()

    def _refresh_routine_status(self) -> None:
        """A visit to the tab: the state on every tile and the sidebar follow the clock
        (a run that ended meanwhile, what is due today) without rebuilding the tiles."""
        if self.vault is None:
            return
        for r in self.vault.routines.ordered():
            self.update_routine_tile(r)
        self.refresh_routine_sidebar()


    def _last_run_text(self, r: Routine) -> str:
        if not r.last_run:
            return _("never run")
        stamp = time.strftime("%d.%m %H:%M", time.localtime(r.last_run))
        if r.last_code is None:
            return _("started {when}").format(when=stamp)
        return (_("✓ {when}") if r.last_code == 0 else _("✗ {when}, exit code {code}")).format(when=stamp, code=r.last_code)

    def _last_run_colour(self, r: Routine) -> str:
        if r.last_code is None or not r.last_run:
            return palette.MUTED
        return palette.SUCCESS if r.last_code == 0 else palette.DANGER

    def refresh_routine_sidebar(self) -> None:
        if self.vault is None or not self.rtoday.winfo_exists():
            return
        for child in self.rtoday.winfo_children():
            child.destroy()
        now = now_local()
        today = [(r.schedule.valid_time(), r) for r in self.vault.routines.ordered() if r.schedule.valid_time() and now.weekday() in r.schedule.days]
        today.sort(key=lambda x: x[0])
        if not today:
            tk.Label(self.rtoday, text=_("Nothing scheduled today"), bg=palette.SIDEBAR, fg=palette.MUTED, font=self._rfonts["small"]).pack(anchor="w")
        for (h, m), r in today:
            done = r.schedule.due(now, r.last_run) is False and now.replace(hour=h, minute=m) <= now
            mark = "✓" if done else "○"
            tk.Label(self.rtoday, text=f"{mark}  {h:02d}:{m:02d}  {r.name}", bg=palette.SIDEBAR, fg=palette.MUTED if done else palette.TEXT, font=self._rfonts["row"], anchor="w").pack(fill="x")
        self.rlog.delete(*self.rlog.get_children())
        for run in self.app.runs.runs:
            code = "…" if run.running else ("✓" if run.code == 0 else f"✗ {run.code}")
            self.rlog.insert("", "end", iid=str(id(run)), text=f"  {time.strftime('%H:%M', time.localtime(run.started))}  {run.routine.name}", values=(code,))

    def _open_log_entry(self, _event=None) -> None:
        sel = self.rlog.selection()
        self.app.runs.open_output(next((x for x in self.app.runs.runs if str(id(x)) == (sel[0] if sel else "")), None))

    def _routine_menu(self, r: Routine, event: tk.Event) -> None:
        menu = PopupMenu(self.app, tearoff=False)
        menu.add_command(label=_("▶ Run"), command=lambda: self.app.runs.run_routine(r))
        menu.add_command(label=_("Edit…"), command=lambda: self.routine_dialog(r))
        if r.kind in ("script", "file", "folder") and r.target:
            menu.add_command(label=_("Show in folder"), command=lambda: os.path.exists(r.target) and show_in_folder(r.target))
        menu.add_separator()
        menu.add_command(label=_("Move left"), command=lambda: self._routine_move(r, -1))
        menu.add_command(label=_("Move right"), command=lambda: self._routine_move(r, +1))
        menu.add_separator()
        menu.add_command(label=_("Delete"), command=lambda: self._routine_delete(r))
        menu.tk_popup(event.x_root, event.y_root)

    def _routine_move(self, r: Routine, delta: int) -> None:
        if self.vault.routines.move(r, delta):
            self.app.mark_dirty("routines")
            self._refresh_routines(keep_scroll=True)

    def _routine_delete(self, r: Routine) -> None:
        if ask_yes_no(self.app, _("Delete routine “{name}”?").format(name=r.name)):
            self.vault.routines.remove(r)
            self.app.mark_dirty("routines")
            self._refresh_routines(keep_scroll=True)

    # ---- editor

    def routine_dialog(self, r: Routine | None) -> None:
        if self.vault is None:
            return
        dlg = Dialog(self.app, _("New routine") if r is None else _("Routine"))
        box = dlg.box
        kind_labels = [_(KIND_LABELS[k]) for k in KINDS]
        name_var = tk.StringVar(value=r.name if r else "")
        kind_var = tk.StringVar(value=_(KIND_LABELS[r.kind if r else "script"]))
        target_var = tk.StringVar(value=r.target if r else "")
        args_var = tk.StringVar(value=r.args if r else "")
        cwd_var = tk.StringVar(value=r.cwd if r else "")
        icon_var = tk.StringVar(value=r.icon if r else "")
        terminal_var = tk.BooleanVar(value=r.terminal if r else False)
        unlock_var = tk.BooleanVar(value=r.schedule.on_unlock if r else False)
        at_var = tk.StringVar(value=r.schedule.at if r else "")
        day_vars = [tk.BooleanVar(value=(d in (r.schedule.days if r else [0, 1, 2, 3, 4]))) for d in range(7)]

        def kind() -> str:
            return KINDS[kind_labels.index(kind_var.get())] if kind_var.get() in kind_labels else "script"

        grid = ttk.Frame(box)
        grid.grid(row=0, column=0, sticky="w")
        ttk.Label(grid, text=_("Name")).grid(row=0, column=0, sticky="w", padx=(0, 12), pady=4)
        ttk.Entry(grid, textvariable=name_var, width=46).grid(row=0, column=1, columnspan=2, sticky="w", pady=4)
        ttk.Label(grid, text=_("Type")).grid(row=1, column=0, sticky="w", padx=(0, 12), pady=4)
        Dropdown(grid, textvariable=kind_var, values=kind_labels, width=18).grid(row=1, column=1, sticky="w", pady=4)
        target_label = ttk.Label(grid, text="")
        target_label.grid(row=2, column=0, sticky="w", padx=(0, 12), pady=4)
        ttk.Entry(grid, textvariable=target_var, width=46).grid(row=2, column=1, sticky="w", pady=4)
        browse = ttk.Button(grid, text=_("Browse…"), style="Small.TButton")
        browse.grid(row=2, column=2, sticky="w", padx=(6, 0))
        where_label = ttk.Label(grid, text="", style="Muted.TLabel", wraplength=420, justify="left")

        def on_target(*_args):
            where = risky_location(target_var.get()) if kind() == "script" else ""
            if where:
                place = {"share": _("on a network share"), "downloads": _("in Downloads"), "outside": _("outside your profile folder")}[where]
                where_label.config(text=_("The script is {where}: someone else may be able to change it. Saving accepts the file as it is now; a changed file does not start by itself.").format(where=place))
                where_label.grid(row=7, column=1, columnspan=2, sticky="w", pady=(2, 4))
            else:
                where_label.grid_remove()

        target_var.trace_add("write", on_target)
        args_label = ttk.Label(grid, text=_("Arguments"))
        args_entry = ttk.Entry(grid, textvariable=args_var, width=46)
        cwd_label = ttk.Label(grid, text=_("Working folder"))
        cwd_entry = ttk.Entry(grid, textvariable=cwd_var, width=46)
        cwd_browse = ttk.Button(grid, text=_("Browse…"), style="Small.TButton", command=lambda: cwd_var.set(filedialog.askdirectory(parent=dlg) or cwd_var.get()))
        terminal_box = ttk.Checkbutton(grid, text=_("Open in a separate console window (for interactive scripts)"), variable=terminal_var)
        ttk.Label(grid, text=_("Icon")).grid(row=6, column=0, sticky="w", padx=(0, 12), pady=4)
        icon_row = ttk.Frame(grid)
        icon_row.grid(row=6, column=1, columnspan=2, sticky="w")
        ttk.Entry(icon_row, textvariable=icon_var, width=6).pack(side="left")
        ttk.Label(icon_row, text=_("An emoji; empty — the icon of the type"), style="Muted.TLabel").pack(side="left", padx=(8, 0))

        def on_kind(*_args):
            k = kind()
            target_label.config(text={"script": _("Script file"), "command": _("Command"), "url": _("Address"), "folder": _("Folder"), "file": _("File")}[k])
            runs = k in ("script", "command")
            for w, r_, c_ in ((args_label, 3, 0), (args_entry, 3, 1), (cwd_label, 4, 0), (cwd_entry, 4, 1), (cwd_browse, 4, 2)):
                if runs and not (k == "command" and w in (args_label, args_entry)):
                    w.grid(row=r_, column=c_, sticky="w", padx=(0, 12) if c_ == 0 else (6, 0) if c_ == 2 else 0, pady=4)
                else:
                    w.grid_remove()
            if runs:
                terminal_box.grid(row=5, column=1, columnspan=2, sticky="w", pady=(2, 4))
            else:
                terminal_box.grid_remove()
            if k in ("url", "command"):
                browse.grid_remove()
            else:
                browse.grid(row=2, column=2, sticky="w", padx=(6, 0))

        def do_browse():
            k = kind()
            if k == "folder":
                path = filedialog.askdirectory(parent=dlg)
            else:
                types = [(_("Scripts"), "*.py *.ps1 *.bat *.cmd *.sh *.exe *.lnk"), (_("All files"), "*.*")] if k == "script" else [(_("All files"), "*.*")]
                path = filedialog.askopenfilename(parent=dlg, filetypes=types)
            if path:
                target_var.set(os.path.normpath(path))
                if not name_var.get().strip():
                    name_var.set(os.path.splitext(os.path.basename(path))[0])

        browse.config(command=do_browse)
        kind_var.trace_add("write", on_kind)
        kind_var.trace_add("write", on_target)
        on_kind()
        on_target()

        divider(box).grid(row=1, column=0, sticky="ew", pady=12)
        when = ttk.Frame(box)
        when.grid(row=2, column=0, sticky="w")
        ttk.Label(when, text=_("Start by itself"), font=(palette.FONT, palette.pt(11), "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(when, text=_("Each time the vault is unlocked"), variable=unlock_var).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 2))
        ttk.Label(when, text=_("At (HH:MM)")).grid(row=2, column=0, sticky="w", padx=(0, 12), pady=4)
        ttk.Entry(when, textvariable=at_var, width=8).grid(row=2, column=1, sticky="w")
        ttk.Label(when, text=_("empty — not by time"), style="Muted.TLabel").grid(row=2, column=2, sticky="w", padx=(8, 0))
        days = ttk.Frame(when)
        days.grid(row=3, column=0, columnspan=3, sticky="w", pady=(2, 0))
        for d, name in enumerate(self._days()):
            ttk.Checkbutton(days, text=name, variable=day_vars[d]).pack(side="left", padx=(0, 6))
        err = ttk.Label(box, text="", style="Error.TLabel")
        err.grid(row=3, column=0, sticky="w")

        def save(_e=None):
            k = kind()
            target = target_var.get().strip()
            if not target:
                err.config(text=_("Fill in what to start"))
                return
            schedule = Schedule(unlock_var.get(), at_var.get().strip(), [d for d in range(7) if day_vars[d].get()])
            if schedule.at and schedule.valid_time() is None:
                err.config(text=_("Time must look like 09:30"))
                return
            fields = {
                "name": name_var.get().strip() or os.path.basename(target) or target, "kind": k, "target": target,
                "args": args_var.get().strip() if k == "script" else "", "cwd": cwd_var.get().strip() if k in ("script", "command") else "",
                "terminal": terminal_var.get() if k in ("script", "command") else False, "icon": icon_var.get().strip(), "schedule": schedule,
            }
            saved = self.vault.routines.upsert(r, fields, now_local())
            accept_script(saved)  # saving the tile is the consent to the file as it is now
            self.app.mark_dirty("routines")
            dlg.destroy()
            self._refresh_routines(keep_scroll=True)

        dlg.editor_buttons(4, 1, (12, 0), save, delete=(lambda: (dlg.destroy(), self._routine_delete(r))) if r is not None else None)
        dlg.keys(save)
