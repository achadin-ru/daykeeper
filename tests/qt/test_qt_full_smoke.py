"""The whole Qt window on a temporary vault, the gate of the port's parity (the same path as
tests/test_app_smoke.py walks in the Tk window): unlock, every tab, one item on each, the
editors and the settings open, save, the file that comes out, the guard over Passwords,
the clipboard, lock. Plus an error raised in a slot reaching errors.log."""
from __future__ import annotations

import sys
import tempfile
import unittest

from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt, wait_until

require_qt()

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from daykeeper.journal import today  # noqa: E402
from daykeeper.journal_layout import current as current_layout  # noqa: E402
from daykeeper.security import GUARD_MASTER  # noqa: E402
from daykeeper.vault import Vault  # noqa: E402

PASSWORD = "smoke-master-1"
TABS = ("vault", "bookmarks", "journal", "notes", "routines", "integrations")


def setUpModule():
    qt_app()


def open_dialogs() -> list[QDialog]:
    return [w for w in QApplication.topLevelWidgets() if isinstance(w, QDialog) and w.isVisible()]


class QtFullSmokeTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.errors: list[str] = []
        self.win = open_window(self.tmp.name, password=PASSWORD, errors=self.errors)

    def tearDown(self):
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def close_dialogs(self) -> int:
        dialogs = open_dialogs()
        for d in dialogs:
            d.close()
        pump()
        return len(dialogs)

    def test_unlock_every_tab_save_and_lock(self):
        win = self.win
        self.assertIsNotNone(win.vault)
        for tab in TABS:
            win.show_page(tab)
            pump()
        self.assertEqual(self.errors, [])

        win.show_page("vault")
        page = win.page("vault")
        page.new_entry()
        page.name_edit.setText("Smoke entry")
        page._on_name_change("Smoke entry")

        win.show_page("notes")
        win.page("notes").new_item()
        self.assertEqual(len(win.vault.notes.notes), 1)

        win.show_page("journal")
        journal = win.page("journal")
        journal._add_today()
        journal.quick.setText("APP-1")
        first_column = current_layout().visible_columns()[0].id
        journal._quick_add(first_column)

        win.vault.bookmarks.add("Example", "example.com")
        win.session.touch("bookmarks")
        win.show_page("bookmarks")
        win.page("bookmarks").new_item()
        pump()
        self.assertEqual(self.close_dialogs(), 1, "the bookmark editor opened")

        win.vault.routines.add(name="Example", kind="url", target="https://example.com")
        win.session.touch("routines")
        win.show_page("routines")
        win.page("routines").new_item()
        pump()
        self.assertEqual(self.close_dialogs(), 1, "the routine editor opened")

        win.show_page("integrations")
        win.settings_dialog()
        pump()
        self.assertEqual(self.close_dialogs(), 1, "the settings window opened")
        self.assertEqual(self.errors, [])

        win.save_now()
        saved = Vault.open(win.vault_path, PASSWORD)
        self.assertEqual([e.name for e in saved.entries], ["Smoke entry"])
        self.assertEqual(len(saved.notes.notes), 1)
        self.assertEqual([b.title for b in saved.bookmarks.items], ["Example"])
        self.assertEqual([r.name for r in saved.routines.ordered()], ["Example"])
        self.assertEqual(saved.journal.get(today()).section(first_column), ["APP-1"])

        win.vault.security.vault_guard = GUARD_MASTER
        win.show_page("vault")
        win.security.lock_secrets()
        self.assertFalse(win.security.secrets_open(), "the Passwords tab is behind its guard")
        win.show_page("bookmarks")
        win.show_page("vault")
        win.security.unlock_secrets()
        self.assertTrue(win.security.secrets_open())

        win.copy_to_clipboard("a-secret")
        self.assertEqual(win.fake_clip.text, "a-secret")
        win.lock()
        pump()
        self.assertIsNone(win.fake_clip.text, "a lock wipes a copied secret at once")
        self.assertIsNone(win.vault)
        self.assertEqual(win.pages, {}, "no tab outlives the lock")
        self.assertEqual(self.errors, [])

    def test_undo_across_tabs(self):
        win = self.win
        win.show_page("notes")
        win.page("notes").new_item()
        win._capture.flush()
        self.assertEqual(len(win.vault.notes.notes), 1)
        win.undo()
        self.assertEqual(len(win.vault.notes.notes), 0)
        win.redo()
        self.assertEqual(len(win.vault.notes.notes), 1)
        self.assertIn("Redone", win.main_screen.status.text())

    def test_the_tabs_carry_drawn_icons_not_emoji(self):
        from PySide6.QtCore import Qt

        bar = self.win.main_screen
        bar.set_compact(False)
        for mode, btn in bar.tab_buttons.items():
            self.assertFalse(btn.icon().isNull(), mode)
            self.assertTrue(btn.text()[:1].isalpha(), f"{mode}: {btn.text()!r} starts with its name, not a glyph")
        bar.set_compact(True)
        btn = bar.tab_buttons["vault"]
        self.assertEqual((btn.text(), btn.toolButtonStyle()), ("", Qt.ToolButtonStyle.ToolButtonIconOnly))

    def test_a_lock_asked_again_while_the_save_question_waits_is_nothing(self):
        """The conflict question runs its own loop; the idle tick fires meanwhile and calls
        lock() again — that second call must not ask again nor half-lock under the dialog."""
        win = self.win
        calls = []
        real = win._save_or_keep_copy

        def asking() -> None:
            calls.append(1)
            win.lock()  # what the idle ticker does while the question is open
            self.assertIsNotNone(win.vault, "nothing locked under the open question")
            real()

        win._save_or_keep_copy = asking  # type: ignore[method-assign]
        win.lock()
        self.assertEqual(len(calls), 1)
        self.assertIsNone(win.vault)

    def test_tab_shortcuts(self):
        win = self.win
        win.show_tab_number(4)
        self.assertEqual(win.mode, list(win.main_screen.tab_buttons)[3])
        win._next_tab(+1)
        self.assertEqual(win.mode, list(win.main_screen.tab_buttons)[4])


class ErrorLogTest(unittest.TestCase):
    """An error in a timer or a click goes into errors.log beside the vault, not to a
    console nobody sees (install_excepthook)."""

    def test_an_error_in_a_slot_is_logged(self):
        from daykeeper.qt.app import install_excepthook
        from daykeeper.qt.dialogs import base as dialogs

        reset_globals()
        previous = sys.excepthook
        with tempfile.TemporaryDirectory() as tmp:
            win = open_window(tmp)
            del win.report_error  # the real report, not the test's sink
            shown: list[str] = []
            original = dialogs.show_error
            try:
                import daykeeper.qt.app as app_mod

                app_mod.show_error = lambda parent, text: shown.append(text)  # type: ignore[assignment]
                install_excepthook(win)

                def boom() -> None:
                    raise RuntimeError("from a timer")

                QTimer.singleShot(0, boom)
                self.assertTrue(wait_until(lambda: shown, 3000))
                log = (win.vault_path.parent / "errors.log").read_text(encoding="utf-8")
                self.assertIn("RuntimeError: from a timer", log)
                self.assertIn("errors.log", shown[0])
            finally:
                app_mod.show_error = original  # type: ignore[assignment]
                sys.excepthook = previous
                close_window(win)
        reset_globals()


if __name__ == "__main__":
    unittest.main()
