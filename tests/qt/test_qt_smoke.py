"""The Qt window on a temporary vault, without a screen: create the vault, the empty main
screen, lock by Ctrl+L, the wrong password, unlock again, and what the unlock screen
leaves behind. The first gate of the port; it grows with every tab."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from tests.helpers import reset_globals
from tests.qt_helpers import activate, pump, qt_app, require_qt, type_text, wait_until

require_qt()

import shiboken6  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from daykeeper.settings import read_settings, write_settings  # noqa: E402
from daykeeper.qt.app import MainWindow  # noqa: E402

PASSWORD = "smoke-master-1"


def setUpModule():
    qt_app()


class QtSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "smoke.pkv"
        write_settings(self.path.parent / "settings.json", {"language": "en", "geometry": "1200x760+20+20"})
        self.errors: list[str] = []
        self.win = self.make_window()

    def make_window(self) -> MainWindow:
        win = MainWindow(self.path, preview=True)
        win.report_error = lambda t, e, tb: self.errors.append(repr(e))  # type: ignore[method-assign]
        activate(win)
        return win

    def tearDown(self) -> None:
        try:
            self.win.close()
            self.win.deleteLater()
            pump()
        finally:
            self.tmp.cleanup()
            reset_globals()

    def enter(self, *passwords: str) -> None:
        screen = self.win.unlock_screen
        for field, pw in zip((screen.pw1, screen.pw2), passwords, strict=False):
            field.setFocus()
            type_text(field, pw)
        QTest.keyClick(screen.pw1, Qt.Key.Key_Return)

    def test_create_unlock_lock_and_open_again(self) -> None:
        win, screen = self.win, self.win.unlock_screen
        self.assertIs(win.screens.currentWidget(), screen)
        self.assertTrue(screen.creating, "no file yet: the password is asked twice")
        self.assertTrue(screen.pw2.isVisible())

        self.enter(PASSWORD, PASSWORD)
        self.assertTrue(wait_until(lambda: win.vault is not None), screen.error.text())
        self.assertIs(win.screens.currentWidget(), win.main_screen)
        self.assertTrue(self.path.exists(), "the vault was created on disk")
        self.assertIn("vault", win.pages, "the Passwords tab is made at unlock")
        self.assertEqual((screen.pw1.text(), screen.pw2.text()), ("", ""), "nothing typed stays behind")

        QTest.keyClick(win, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier)
        pump()
        self.assertIsNone(win.vault, "Ctrl+L locked")
        self.assertIs(win.screens.currentWidget(), screen)
        self.assertFalse(screen.creating)
        self.assertFalse(screen.pw2.isVisible(), "the file exists now: one field")
        self.assertFalse(screen.pw1.isUndoAvailable())

        self.enter("wrong-password")
        self.assertTrue(wait_until(lambda: screen.error.text() == "Wrong password"), screen.error.text())
        self.assertEqual(screen.pw1.text(), "")
        self.assertEqual(win.security.attempts.failed, 1)

        self.enter(PASSWORD)
        self.assertTrue(wait_until(lambda: win.vault is not None), screen.error.text())
        self.assertEqual(win.security.attempts.failed, 0, "the master password clears the count")
        self.assertEqual(self.errors, [])

    def test_form_errors_before_any_key_is_derived(self) -> None:
        screen = self.win.unlock_screen
        self.enter("")
        self.assertEqual(screen.error.text(), "Password is empty")
        self.enter("one-password", "another-one")
        self.assertEqual(screen.error.text(), "Passwords do not match")
        screen.refresh()
        self.enter("short", "short")
        self.assertEqual(screen.error.text(), "At least 8 characters")
        self.assertIsNone(self.win.vault)
        self.assertFalse(self.path.exists())

    def test_strength_hint_and_the_eye(self) -> None:
        screen = self.win.unlock_screen
        type_text(screen.pw1, "abc")
        self.assertTrue(screen.strength.text().startswith("Weak"))
        screen.eye.trigger()
        self.assertTrue(screen.pw1.shown() and screen.pw2.shown())
        screen.eye.trigger()
        self.assertFalse(screen.pw1.shown())

    def test_the_wait_after_three_wrong_tries(self) -> None:
        self.win.unlock(PASSWORD)
        self.win.lock()
        screen = self.win.unlock_screen
        for _ in range(3):
            self.win.security.attempts.fail(time.monotonic())
        self.enter(PASSWORD)
        self.assertTrue(screen.error.text().startswith("Too many wrong tries"))
        self.assertIsNone(self.win.vault)

    def test_idle_and_minimize_lock(self) -> None:
        win = self.win
        win.unlock(PASSWORD)
        win.vault.security.app_lock_min = 1
        win.security.check_idle(time.monotonic() + 30)
        self.assertIsNotNone(win.vault, "half a minute idle is not yet a minute")
        win.security.check_idle(time.monotonic() + 61)
        self.assertIsNone(win.vault, "a minute idle locks")

        win.unlock(PASSWORD)
        win.vault.security.lock_on_minimize = True
        win.showMinimized()
        self.assertTrue(wait_until(lambda: win.vault is None, 2000))

    def test_lock_closes_the_windows_that_show_the_vault(self) -> None:
        from daykeeper.qt.dialogs.base import Dialog
        from daykeeper.qt.widgets.toast import Toast

        win = self.win
        win.unlock(PASSWORD)
        dlg = Dialog(win, "entry")
        dlg.show()
        toast = Toast(win, "GitLab", "a merge request title")
        pump()
        win.lock()
        pump()
        self.assertFalse(shiboken6.isValid(dlg), "a closed dialog is deleted, with whatever it showed")
        self.assertNotIn(toast, Toast.stack)

    def test_language_switch_and_the_window_place(self) -> None:
        win, screen = self.win, self.win.unlock_screen
        screen.lang_btn.click()
        self.assertEqual(screen.open_btn.text(), "Создать хранилище")
        self.assertEqual(read_settings(self.path.parent / "settings.json")["language"], "ru")
        win.resize(1000, 700)
        win.close()
        saved = read_settings(self.path.parent / "settings.json")
        self.assertTrue(saved["geometry"].startswith("1000x700+"), saved)
        self.assertFalse(saved["zoomed"])

    def test_an_unexpected_error_goes_to_the_report(self) -> None:
        screen = self.win.unlock_screen
        self.win.session.open_vault = lambda pw: (_ for _ in ()).throw(RuntimeError("disk on fire"))  # type: ignore[method-assign]
        self.enter(PASSWORD, PASSWORD)
        self.assertTrue(wait_until(lambda: self.errors, 3000))
        self.assertIn("disk on fire", self.errors[0])
        self.assertFalse(screen.opening)


if __name__ == "__main__":
    unittest.main()
