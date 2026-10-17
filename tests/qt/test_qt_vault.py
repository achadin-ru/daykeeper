"""The Passwords tab of the Qt window: the list, the card, secret fields that hold their
value only while shown, lists, the keyboard, the guard nobody can walk round, and the
speed on 10 000 entries."""
from __future__ import annotations

import tempfile
import time
import unittest
from unittest import mock

from tests import qt_speed
from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt, type_text, wait_until

require_qt()

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from daykeeper.qt.pages import vault as vault_page  # noqa: E402
from daykeeper.security import GUARD_PIN  # noqa: E402


def setUpModule():
    qt_app()


class VaultPageTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.errors: list[str] = []
        self.win = open_window(self.tmp.name, errors=self.errors)
        self.page = self.win.page("vault")

    def tearDown(self):
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def new_entry(self, name: str = "Mail"):
        self.page.new_entry()
        self.page.name_edit.setText("")
        type_text(self.page.name_edit, name)
        pump()
        return self.page.current

    def test_a_new_entry_shows_in_the_list_and_saves(self):
        entry = self.new_entry("Mail box")
        self.assertEqual(entry.name, "Mail box")
        self.assertEqual(self.page.model.rowCount(), 1)
        self.assertIn("Mail box", self.page.model.data(self.page.model.index(0)))
        self.assertEqual([f.key for f in entry.fields], ["login", "password", "site"], "the login kind's template")
        self.assertTrue(wait_until(lambda: self.win.vault_path.exists() and not self.win._save.pending, 3000))
        self.assertEqual(self.errors, [])

    def test_a_secret_field_holds_its_value_only_while_shown(self):
        entry = self.new_entry()
        row = next(r for r in self.page.fields.rows if r.f.secret)
        row.value.setFocus()
        type_text(row.value, "s3cret-пароль")
        self.assertEqual(entry.fields[1].value, "s3cret-пароль", "typing goes into the sealed field")
        self.page.name_edit.setFocus()
        pump()
        self.assertEqual(row.value.text(), "", "out of focus: the field holds nothing")
        self.assertEqual(row.value.placeholderText(), vault_page.MASK)
        self.assertFalse(row.value.isUndoAvailable())
        row.toggle_reveal()
        self.assertEqual(row.value.text(), "s3cret-пароль")
        row.toggle_reveal()
        self.assertEqual(row.value.text(), "")
        self.assertEqual(self.page.model.data(self.page.model.index(0), Qt.ItemDataRole.DisplayRole).count("s3cret"), 0)

    def test_copy_puts_the_secret_on_the_clipboard_and_wipes_it(self):
        entry = self.new_entry()
        entry.fields[1].value = "copy-me"
        self.page.copy_field("secret")
        self.assertEqual(self.win.fake_clip.text, "copy-me")
        self.assertTrue(self.win._clip_wipe.pending)
        self.win._clip_wipe.flush()
        self.assertIsNone(self.win.fake_clip.text, "wiped after the delay")

    def test_generate_lock_and_remove_a_field(self):
        entry = self.new_entry()
        row = self.page.fields.rows[0]  # login, not secret
        row.lock.setChecked(True)
        self.assertTrue(entry.fields[0].secret)
        row.generate()
        self.assertEqual(len(entry.fields[0].value), 20)
        self.page.add_field("token", True)
        self.assertEqual(len(entry.fields), 4)
        self.page.remove_field(entry.fields[-1])
        self.assertEqual(len(entry.fields), 3)

    def test_narrow_card_puts_the_name_above_the_value(self):
        self.new_entry()
        self.win.resize(900, 700)
        self.page.splitter.setSizes([520, 380])
        pump(30)
        self.assertTrue(all(r.compact for r in self.page.fields.rows))
        self.win.resize(1600, 800)
        self.page.splitter.setSizes([300, 1300])
        pump(30)
        self.assertFalse(any(r.compact for r in self.page.fields.rows))

    def test_search_enter_down_and_escape(self):
        for name in ("Alpha", "Beta", "Gamma"):
            self.new_entry(name)
        self.page.search.setFocus()
        type_text(self.page.search, "bet")
        self.assertTrue(self.page._search_later.pending, "filtered once the typing pauses")
        QTest.keyClick(self.page.search, Qt.Key.Key_Return)
        self.assertEqual([e.name for e in self.page.model.rows], ["Beta"])
        self.assertEqual(self.page.current.name, "Beta")
        QTest.keyClick(self.page.search, Qt.Key.Key_Down)
        self.assertIs(QApplication.focusWidget(), self.page.list)
        QTest.keyClick(self.page.list, Qt.Key.Key_Escape)
        self.assertEqual(self.page.search.text(), "")
        self.assertEqual(self.page.model.rowCount(), 3)

    def test_order_lists_and_moving(self):
        a = self.new_entry("A")
        b = self.new_entry("B")
        self.page.move_entry(-1)
        self.assertEqual([e.name for e in self.page.model.rows], ["B", "A"])
        with mock.patch.object(vault_page, "ask_text", return_value="Work"):
            self.page.new_group()
        self.assertEqual(self.page.group, "Work")
        self.assertEqual(self.page.model.rowCount(), 0)
        self.page.set_entry_group(a, "Work")
        self.assertEqual([e.name for e in self.page.model.rows], ["A"])
        with mock.patch.object(vault_page, "ask_yes_no", return_value=True):
            self.page.delete_group()
        self.assertEqual(a.group, "")
        self.assertEqual({e.name for e in self.page.model.rows}, {"A", "B"})
        self.assertIsNotNone(b)

    def test_delete_asks_first(self):
        self.new_entry("Gone")
        with mock.patch.object(vault_page, "ask_yes_no", return_value=False):
            self.page.delete_entry()
        self.assertEqual(len(self.win.vault.entries), 1)
        with mock.patch.object(vault_page, "ask_yes_no", return_value=True):
            self.page.delete_entry()
        self.assertEqual(self.win.vault.entries, [])
        self.assertIs(self.page.card_stack.currentWidget(), self.page.placeholder)

    def test_undo_brings_a_deleted_entry_back(self):
        self.new_entry("Keep me")
        self.win._capture.flush()
        with mock.patch.object(vault_page, "ask_yes_no", return_value=True):
            self.page.delete_entry()
        self.win._capture.flush()
        self.win.undo()
        self.assertEqual([e.name for e in self.win.vault.entries], ["Keep me"])
        self.assertEqual(self.win.page("vault").model.rowCount(), 1, "the tab was rebuilt")


class VaultGuardTest(unittest.TestCase):
    """While the Passwords tab is locked, the keyboard reaches nothing under the guard."""

    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name)
        vault = self.win.vault
        entry = vault.add_entry(name="Bank")
        entry.fields[1].value = "hidden-value"
        vault.security.vault_guard = GUARD_PIN
        vault.security.set_pin("2468")
        self.win.show_main()
        self.page = self.win.page("vault")

    def tearDown(self):
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def test_the_guard_cannot_be_walked_round(self):
        win, page = self.win, self.page
        self.assertTrue(win.vault.security.guarded)
        win.security.lock_secrets()
        pump()
        self.assertTrue(page.guarded)
        self.assertFalse(page.content.isEnabled())
        self.assertEqual(page.model.rowCount(), 0, "the list is emptied")
        self.assertIsNone(page.current)
        self.assertFalse(win.main_screen.lock_tab_btn.isHidden(), "“Lock passwords” is on the bar while the tab has a guard")
        for _ in range(25):  # Tab round the whole window
            QTest.keyClick(QApplication.focusWidget() or win, Qt.Key.Key_Tab)
            focus = QApplication.focusWidget()
            self.assertFalse(focus is not None and page.content.isAncestorOf(focus), f"focus under the guard: {focus}")
        with mock.patch("daykeeper.qt.lock.ask_secret") as prompt:
            win._find_for_mode()  # Ctrl+F asks for the code instead
            page.new_item()
        self.assertEqual(prompt.call_count, 2)
        win.copy_to_clipboard("hidden-value")
        self.assertIsNone(win.fake_clip.text, "no secret leaves while locked")

        page.guard_screen.code.setFocus()
        type_text(page.guard_screen.code, "0000")
        QTest.keyClick(page.guard_screen.code, Qt.Key.Key_Return)
        self.assertTrue(page.guarded)
        self.assertEqual(page.guard_screen.error.text(), "Wrong code")
        type_text(page.guard_screen.code, "2468")
        QTest.keyClick(page.guard_screen.code, Qt.Key.Key_Return)
        self.assertFalse(page.guarded)
        self.assertEqual(page.current.name, "Bank", "the entry that was shown comes back")
        self.assertEqual(page.guard_screen.code.text(), "")

    def test_idle_locks_the_passwords(self):
        prefs = self.win.vault.security
        prefs.vault_lock_min = 1
        prefs.app_lock_min = 0
        self.win.security.check_idle(time.monotonic() + 61)
        self.assertTrue(self.page.guarded)
        self.assertIsNotNone(self.win.vault, "the app itself stays open")


class VaultSpeedTest(unittest.TestCase):
    """10 000 entries: the tab opens in 0.5 s, a search answers in 0.1 s. Only a gross slowdown fails here; tests/bench_qt_tabs.py prints the numbers."""

    def test_speed(self):
        reset_globals()
        results = qt_speed.measure_vault()
        print(chr(10) + qt_speed.report(results), end=" ")
        for key, value in results.items():
            self.assertLess(value, qt_speed.TARGETS[key] * qt_speed.SLACK, key)


if __name__ == "__main__":
    unittest.main()
