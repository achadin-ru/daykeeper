"""The fixes of the deep audit of 16 October: the Passwords-tab guard against the keyboard,
a toast that must not outlive the lock, and the window-side checks of the other findings.
The pure-model fixes have their tests next to their modules (test_vault, test_hook, …)."""
from __future__ import annotations

import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

from daykeeper.security import GUARD_PIN
from daykeeper.settings import write_settings
from tests.helpers import has_display, hide, reset_globals

PASSWORD = "audit-1016-master"
SECRET = "TOP-SECRET-PASSWORD"


class _CtrlKey:
    """A Control-key event as App._on_control_key reads it (Windows virtual-key codes)."""

    def __init__(self, keycode: int, keysym: str, state: int = 4):
        self.keycode, self.keysym, self.state, self.widget = keycode, keysym, state, None


@unittest.skipUnless(has_display(), "no display")
class GuardAgainstTheKeyboardTest(unittest.TestCase):
    """H1: the guard over the Passwords tab must hold against Ctrl+F, Tab, Space and Copy."""

    def setUp(self) -> None:
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "guard.pkv"
        write_settings(path.parent / "settings.json", {"language": "en", "geometry": "1200x760+20+20"})
        from daykeeper.app import App

        self.app = App(path, preview=True)
        hide(self.app)
        self.app.update()
        self.app.unlock(PASSWORD)
        vault = self.app.vault
        self.entry = vault.add_entry("Bank")
        self.entry.fields[0].value = "alice"
        self.entry.fields[1].value = SECRET
        vault.security.set_pin("1234")
        vault.security.vault_guard = GUARD_PIN
        self.app.save_now()
        self.app.update_lock_button()
        self.page = self.app.pages["vault"]
        self.page.open_first()
        self.app.update()
        self.copied: list[str] = []
        self.app._clip.put = self.copied.append  # record instead of the real clipboard

    def tearDown(self) -> None:
        try:
            self.app.destroy()
        finally:
            self.tmp.cleanup()
            reset_globals()

    def _tab_walk(self) -> list[tk.Misc]:
        """Every widget Tab reaches from the guard's code field before coming back."""
        start = self.app.security._guard_frame.code_entry
        start.focus_set()
        self.app.update()
        seen: list[tk.Misc] = []
        w: tk.Misc | None = start
        for _ in range(200):
            w = w.tk_focusNext()
            if w is None or w is start:
                break
            seen.append(w)
        return seen

    def test_nothing_under_the_guard_is_reachable_and_the_card_is_empty(self) -> None:
        app, page = self.app, self.page
        self.assertEqual(page._field_rows[1][1].get(), SECRET, "the card shows the entry before the lock")
        app.security.lock_secrets()
        app.update()
        self.assertTrue(app.security.vault_locked)
        self.assertTrue(page.guarded)
        self.assertEqual(page._field_rows, [], "the card forgot its fields")
        self.assertEqual(page.tree.get_children(), (), "the list is empty")
        self.assertEqual(page.name_var.get(), "")
        guard = str(app.security._guard_frame)
        under = [w for w in self._tab_walk() if app.view_of(w) is page.frame and not str(w).startswith(guard)]
        self.assertEqual(under, [], "Tab must not reach a widget under the guard")

    def test_ctrl_f_and_the_shortcuts_do_not_reach_the_tab(self) -> None:
        app, page = self.app, self.page
        app.security.lock_secrets()
        app.update()
        with mock.patch("daykeeper.lock_view.ask_secret") as ask:
            app._on_control_key(_CtrlKey(70, "f"))  # Ctrl+F
            app.update()
            self.assertIsNot(app.focus_get(), page._search_entry, "the search under the guard did not take the focus")
            app._new_for_mode()  # Ctrl+N
            app.pages["vault"].move_entry(-1)  # Alt+Up
            app.pages["vault"].delete_entry()  # Delete
        self.assertEqual(len(app.vault.entries), 1, "nothing was made or deleted behind the guard")
        self.assertGreaterEqual(ask.call_count, 1, "the shortcuts ask for the code instead")

    def test_search_select_and_copy_do_nothing_behind_the_guard(self) -> None:
        app, page = self.app, self.page
        app.security.lock_secrets()
        app.update()
        page.search_var.set("Bank")
        page._search_later.flush()
        app.update()
        self.assertEqual(page.tree.get_children(), (), "a search does not fill the list")
        page._load_entry(self.entry)
        self.assertIsNone(page.current)
        self.assertEqual(page._field_rows, [], "the card cannot be loaded")
        app.copy_to_clipboard(SECRET)
        self.assertEqual(self.copied, [], "no secret reaches the clipboard while the guard is down")
        self.assertFalse(app.security.secrets_open())

    def test_unlock_brings_the_entry_back(self) -> None:
        app, page = self.app, self.page
        app.security.lock_secrets()
        app.update()
        app.security.unlock_secrets()
        app.update()
        self.assertFalse(page.guarded)
        self.assertIs(page.current, self.entry)
        self.assertEqual(page._field_rows[1][1].get(), SECRET)
        self.assertEqual(len(page.tree.get_children()), 1)
        app.copy_to_clipboard(SECRET)
        self.assertEqual(self.copied, [SECRET])

    def test_a_rebuilt_tab_stays_behind_the_guard(self) -> None:
        """An undo makes the Passwords tab anew: the new one is guarded as well."""
        app = self.app
        app.security.lock_secrets()
        app.update()
        app._rebuild_page("vault")
        app.update()
        page = app.pages["vault"]
        self.assertTrue(page.guarded)
        self.assertEqual(page._field_rows, [])
        self.assertTrue(app.security._guard_frame.winfo_exists())
        self.assertIs(app.security._guard_frame.master, page.frame)
        app.security.unlock_secrets()
        app.update()
        self.assertEqual(page._field_rows[1][1].get(), SECRET)

    def test_a_toast_does_not_outlive_the_lock(self) -> None:
        """L2: a toast with an MR title or a run's result closes with the other windows."""
        from daykeeper.toast import Toast

        app = self.app
        toast = Toast(app, "GitLab · a private title", "approved")
        app.update()
        self.assertIn(toast, Toast.stack)
        app.lock()
        app.update()
        self.assertFalse(toast.winfo_exists())
        self.assertNotIn(toast, Toast.stack)
        self.assertEqual([c for c in app.winfo_children() if isinstance(c, tk.Toplevel)], [])


@unittest.skipUnless(has_display(), "no display")
class RoutineAcceptanceTest(unittest.TestCase):
    """M5: the Routine tab runs a changed or never accepted script by itself no more."""

    def setUp(self) -> None:
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "r.pkv"
        write_settings(path.parent / "settings.json", {"language": "en", "geometry": "1200x760+20+20"})
        from daykeeper.app import App

        self.app = App(path, preview=True)
        hide(self.app)
        self.app.update()
        self.app.unlock(PASSWORD)
        self.script = Path(self.tmp.name) / "nightly.py"
        self.script.write_text("print('one')\n", encoding="utf-8")
        self.routine = self.app.vault.routines.add(name="Nightly", kind="script", target=str(self.script))
        self.started: list = []
        self.toasts: list[str] = []
        test = self

        class FakeRun:
            def __init__(self, routine, argv, shell, cwd, auto=False):
                self.routine, self.argv, self.auto = routine, argv, auto
                self.started, self.ended, self.code, self.lines = 1.0, 2.0, 0, []
                test.started.append(self)

            running = False

            def take(self):
                return []

            def duration(self):
                return 1.0

        self.patches = [mock.patch("daykeeper.routines_view.Run", FakeRun), mock.patch("daykeeper.routines_view.Toast", lambda _app, title, text, **kw: self.toasts.append(text))]
        for p in self.patches:
            p.start()

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()
        try:
            self.app.destroy()
        finally:
            self.tmp.cleanup()
            reset_globals()

    def test_an_automatic_run_waits_for_the_first_run_by_hand(self) -> None:
        self.app.runs.run_routine(self.routine, auto=True)
        self.assertEqual(self.started, [], "never accepted: does not start by itself")
        self.assertEqual(len(self.toasts), 1)
        self.app.runs.run_routine(self.routine)
        self.assertEqual(len(self.started), 1, "a run by hand is the consent")
        self.assertTrue(self.routine.target_hash)
        self.app.runs.run_routine(self.routine, auto=True)
        self.assertEqual(len(self.started), 2, "the accepted script starts by itself")

    def test_a_changed_script_is_refused_until_accepted_by_hand(self) -> None:
        self.app.runs.run_routine(self.routine)
        accepted = self.routine.target_hash
        self.script.write_text("print('swapped')\n", encoding="utf-8")
        self.app.runs.run_routine(self.routine, auto=True)
        self.assertEqual(len(self.started), 1, "a changed script does not start by itself")
        self.assertIn("changed", self.toasts[-1])
        with mock.patch("daykeeper.routines_view.ask_yes_no", return_value=False):
            self.app.runs.run_routine(self.routine)
        self.assertEqual(len(self.started), 1, "declined: not run")
        self.assertEqual(self.routine.target_hash, accepted)
        with mock.patch("daykeeper.routines_view.ask_yes_no", return_value=True):
            self.app.runs.run_routine(self.routine)
        self.assertEqual(len(self.started), 2)
        self.assertNotEqual(self.routine.target_hash, accepted, "the new version is the accepted one")
        self.app.runs.run_routine(self.routine, auto=True)
        self.assertEqual(len(self.started), 3)


@unittest.skipUnless(has_display(), "no display")
class UnlockScreenAndCopiesTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "u.pkv"
        write_settings(self.path.parent / "settings.json", {"language": "en", "geometry": "1200x760+20+20"})
        from daykeeper.app import App

        self.app = App(self.path, preview=True)
        hide(self.app)
        self.app.update()

    def tearDown(self) -> None:
        try:
            self.app.destroy()
        finally:
            self.tmp.cleanup()
            reset_globals()

    def _try(self, password: str) -> str:
        app = self.app
        app._pw1.delete(0, "end")
        app._pw1.insert(0, password)
        app._do_unlock()
        for _ in range(400):
            app.update()
            if not app._opening:
                break
            app.after(10)
        return app._unlock_err.cget("text") if app._unlock_err.winfo_exists() else ""  # gone: the vault opened

    def test_wrong_master_passwords_are_throttled(self) -> None:
        """L22: the unlock screen waits after wrong tries like the PIN prompt does."""
        from daykeeper.vault import Vault

        Vault.create(self.path, PASSWORD)
        self.app.show_unlock()
        self.app.update()
        for _ in range(3):
            self.assertEqual(self._try("wrong-" + PASSWORD), "Wrong password")
        self.assertIn("Wait", self._try("wrong-" + PASSWORD), "the fourth try is refused for a while")
        self.assertIsNone(self.app.vault)
        self.app.security.attempts.ok()
        self._try(PASSWORD)
        self.assertIsNotNone(self.app.vault, "the right password opens the vault once the pause is over")

    def test_restore_of_a_copy_deleted_meanwhile_says_so(self) -> None:
        """L13: Restore… on a copy that vanished shows the message, not the error dialog."""
        self.app.unlock(PASSWORD)
        self.app.update()
        self.app.settings_dialog("data")
        win = self.app._settings_win
        with mock.patch("daykeeper.settings_view.show_error") as err, mock.patch("daykeeper.settings_view.ask_yes_no") as ask:
            win._restore_copy(self.path.with_name("gone.pkv"))
        self.assertEqual(err.call_count, 1)
        ask.assert_not_called()

    def test_an_unreadable_value_is_named_on_the_card(self) -> None:
        """L5: the card says the value cannot be read instead of showing an empty field."""
        from daykeeper.vault import Field

        self.app.unlock(PASSWORD)
        e = self.app.vault.add_entry("Bank")
        e.fields[1] = Field.from_dict({"key": "password", "sealed": "!!!not-base64!!!", "secret": True}, self.app.vault._sealer)
        page = self.app.pages["vault"]
        page.open_first()
        self.app.update()
        self.assertIn("unreadable", page._field_rows[1][1].get())
        copied: list[str] = []
        self.app._clip.put = copied.append
        page._field_rows[1][3].invoke()
        self.assertEqual(copied, [], "Copy copies nothing, not the placeholder text")


@unittest.skipUnless(has_display(), "no display")
class TimersAndClipboardTest(unittest.TestCase):
    def test_every_timer_stops_with_the_window_and_the_wipe_timer_holds_no_secret(self) -> None:
        """L15: destroy() stops the tickers (no `invalid command name` after the window is gone);
        L14: the wipe timer's argument is a hash."""
        reset_globals()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.pkv"
            write_settings(path.parent / "settings.json", {"language": "en", "geometry": "1200x760+20+20"})
            from daykeeper.app import App

            app = App(path, preview=True)
            hide(app)
            app.update()
            app.unlock(PASSWORD)
            app.update()
            app._clip.put = lambda text: None
            app.copy_to_clipboard(SECRET)
            self.assertTrue(app._clip_wipe.pending)
            self.assertNotIn(SECRET, repr(app._clip_wipe._args))
            tickers = [app._inbox_ticker, app.security.ticker, *app.runs.tickers, app.gitlab._gl_ticker]
            self.assertTrue(any(t.running for t in tickers))
            app.destroy()
            for ticker in tickers:
                self.assertFalse(ticker.running)
            self.assertFalse(app._clip_wipe.pending)
            self.assertFalse(app._save.pending)
        reset_globals()


@unittest.skipUnless(has_display(), "no display")
class KeyboardFlowsTest(unittest.TestCase):
    """U1: find and copy a password without the mouse; U4: the journal's columns above the fold; U3: routine tiles on canvases."""

    def setUp(self) -> None:
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "k.pkv"
        write_settings(path.parent / "settings.json", {"language": "en", "geometry": "1200x760+20+20"})
        from daykeeper.app import App

        self.app = App(path, preview=True)
        hide(self.app)
        self.app.update()
        self.app.unlock(PASSWORD)
        vault = self.app.vault
        self.bank = vault.add_entry("Bank")
        self.bank.fields[0].value = "alice"
        self.bank.fields[1].value = SECRET
        self.mail = vault.add_entry("Mail")
        self.mail.fields[0].value = "bob@example.com"
        self.mail.fields[1].value = "mail-pass"
        self.page = self.app.pages["vault"]
        self.page.open_first()
        self.app.update()
        self.secrets: list[str] = []
        self.plain: list[str] = []
        self.app._clip.put = self.secrets.append
        self.app._tk_clip_put = self.plain.append

    def tearDown(self) -> None:
        try:
            self.app.destroy()
        finally:
            self.tmp.cleanup()
            reset_globals()

    def test_type_enter_copy(self) -> None:
        app, page = self.app, self.page
        # the window is hidden, so Tk does not move the focus: the focus_set calls are watched instead
        with mock.patch.object(page._search_entry, "focus_set") as search_focus, mock.patch.object(page.tree, "focus_set") as tree_focus:
            app._on_control_key(_CtrlKey(70, "f"))  # Ctrl+F
            app.update()
            self.assertEqual(search_focus.call_count, 1)
            page.search_var.set("Ma")
            page._search_pick_first()  # Enter in the search (a hidden window gets no key events)
            app.update()
            self.assertIs(page.current, self.mail, "Enter puts the first hit on the card")
            tree_focus.assert_not_called()  # the cursor stays in the search
        app._on_control_key(_CtrlKey(67, "C", state=5))  # Ctrl+Shift+C
        self.assertEqual(self.secrets, ["mail-pass"])
        app._on_control_key(_CtrlKey(66, "b"))  # Ctrl+B
        self.assertEqual(self.plain, ["bob@example.com"])
        page._clear_search()  # Esc
        app.update()
        self.assertEqual(page.search_var.get(), "")
        self.assertEqual(len(page.tree.get_children()), 2, "Esc shows the whole list again")

    def test_down_walks_into_the_list(self) -> None:
        app, page = self.app, self.page
        page.search_var.set("Bank")
        with mock.patch.object(page.tree, "focus_set") as tree_focus, mock.patch.object(page._search_entry, "focus_set") as search_focus:
            page._focus_list()  # ↓ in the search
            app.update()
            self.assertEqual(tree_focus.call_count, 1)
            self.assertEqual(page.tree.selection(), (self.bank.id,))
            self.assertEqual(page.tree.focus(), self.bank.id)
            page._clear_search()  # Esc in the list
            app.update()
            self.assertEqual(search_focus.call_count, 1)
        self.assertEqual(page.search_var.get(), "")

    def test_copy_shortcuts_respect_the_guard(self) -> None:
        from daykeeper.security import GUARD_PIN

        app = self.app
        app.vault.security.set_pin("1234")
        app.vault.security.vault_guard = GUARD_PIN
        app.security.lock_secrets()
        app.update()
        app._on_control_key(_CtrlKey(67, "C", state=5))
        app._on_control_key(_CtrlKey(66, "b"))
        self.assertEqual((self.secrets, self.plain), ([], []))

    def test_journal_columns_sit_above_the_activity_box(self) -> None:
        app = self.app
        app.show_page("journal")
        app.update()
        journal = app.pages["journal"]
        def grid_row(widget) -> int:
            while "row" not in widget.grid_info():  # a text field sits in a rounded box of its own
                widget = widget.master
            return int(widget.grid_info()["row"])

        first_column = next(iter(journal.jsections.values()))
        self.assertLess(grid_row(first_column), grid_row(journal.jactivity))
        self.assertLess(grid_row(journal.jsummary_entry), grid_row(first_column))
        bar = journal.jmore.master
        texts = [str(w.cget("text")) for w in bar.winfo_children() if "text" in w.keys()]  # noqa: SIM118 — Tk's keys() is a method
        self.assertNotIn("Links…", texts, "the settings moved into the ⋯ menu")
        self.assertIn("⋯", texts)
        self.assertIn("Read the inbox now", texts)

    def test_routine_tiles_are_canvases_that_follow_the_run(self) -> None:
        from daykeeper.routine_tile import RoutineTile

        app = self.app
        routines = [app.vault.routines.add(name=f"Routine {i} with a rather long name that wraps", kind="command", target="echo hi", terminal=(i == 1)) for i in range(3)]
        routines[0].schedule.on_unlock = True
        app.session.touch("routines")
        app.show_page("routines")
        app.finish_chunks("routines")
        app.update()
        page = app.pages["routines"]
        tiles = [page._rtiles_by_id[r.id] for r in routines]
        self.assertTrue(all(isinstance(t, RoutineTile) for t in tiles))
        for tile in tiles:
            tile.update()
            self.assertTrue(tile.find_withtag("run"), "the Run action is drawn")
            self.assertFalse(tile.find_withtag("output"), "no run yet: no Output")
        self.assertTrue(tiles[0].find_withtag("chip"), "the on-unlock chip")
        self.assertGreaterEqual(tiles[0]._title_lines, 1)

        class FakeRun:
            def __init__(self, routine):
                self.routine, self.started, self.ended, self.code, self.lines, self.auto = routine, 1.0, None, None, [], False

            running = True

            def duration(self):
                return 2.0

            def take(self):
                return []

        app.runs.runs.insert(0, FakeRun(routines[1]))
        page.update_routine_tile(routines[1])
        self.assertTrue(tiles[1].find_withtag("stop"))
        self.assertTrue(tiles[1].find_withtag("output"))
        self.assertFalse(tiles[1].find_withtag("run"))
        app.runs.runs[0].running, app.runs.runs[0].ended, app.runs.runs[0].code = False, 3.0, 0
        page.update_routine_tile(routines[1])
        self.assertTrue(tiles[1].find_withtag("run"))
        self.assertTrue(tiles[1].find_withtag("output"))


@unittest.skipUnless(has_display(), "no display")
class MenuKeyBindingTest(unittest.TestCase):
    def test_the_menu_key_binds_where_tk_has_it(self) -> None:
        """L19: `<App>` is Windows-only; the helper binds it, `<Menu>` and `Shift+F10` without raising on any Tk."""
        import tkinter as tk

        from daykeeper.widgets import bind_menu_key

        root = tk.Tk()
        try:
            hide(root)
            canvas = tk.Canvas(root)
            fired: list[str] = []
            bind_menu_key(canvas, lambda _e: fired.append("menu"))
            bound = {key for key in ("<App>", "<Menu>", "<Shift-F10>") if canvas.bind(key)}
            self.assertIn("<Shift-F10>", bound)
            self.assertTrue(bound & {"<App>", "<Menu>"}, "at least one context-menu keysym of this Tk")
            with mock.patch.object(tk.Misc, "bind", side_effect=tk.TclError('bad event type or keysym "App"')):
                bind_menu_key(canvas, lambda _e: None)  # X11: the bind raises, the tile is still built
        finally:
            root.destroy()
