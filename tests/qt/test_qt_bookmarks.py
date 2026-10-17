"""The Bookmarks tab of the Qt window: groups, search, tiles and their zones, the tie to a
password entry behind the guard, the editor, the keyboard, and 300 tiles."""
from __future__ import annotations

import tempfile
import unittest
from unittest import mock

from tests import qt_speed
from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt, type_text

require_qt()

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from daykeeper import siteicon  # noqa: E402
from daykeeper.qt.pages import bookmarks as bm_page  # noqa: E402
from daykeeper.security import GUARD_PIN  # noqa: E402


def setUpModule():
    qt_app()


class BookmarksPageTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name)
        vault = self.win.vault
        self.entry = vault.add_entry(name="Git")
        self.entry.fields[0].value = "me@example.com"
        self.entry.fields[1].value = "tile-secret"
        bms = vault.bookmarks
        bms.groups.append("Work")
        self.tied = bms.add("GitLab", "https://git.example.com", "Work", entry_id=self.entry.id)
        self.plain = bms.add("Docs", "https://docs.example.com", "")
        self.win.show_main()
        self.win.show_page("bookmarks")
        pump()
        self.page = self.win.page("bookmarks")

    def tearDown(self):
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def tile_zone(self, bm, zone):
        board = self.page.board
        i = board.index_of(bm)
        rect = dict(self.page.painter.zones(bm, board.rect_of(i))).get(zone)
        return rect.center() if rect is not None else board.rect_of(i).center()

    def test_all_shows_sections_and_groups_count(self):
        kinds = [(it.kind, it.data if it.kind != "tile" else it.data.title) for it in self.page.board.items]
        self.assertEqual(kinds[0], ("heading", "All bookmarks"))
        self.assertIn(("section", "Without a group"), kinds)
        self.assertIn(("tile", "GitLab"), kinds)
        names = [self.page.groups.item(i).text().strip() for i in range(self.page.groups.count())]
        self.assertEqual(names, ["★ All", "Without a group", "Work"])

    def test_group_and_search(self):
        self.page.groups.setCurrentRow(2)
        self.assertEqual([it.data.title for it in self.page.board.items if it.kind == "tile"], ["GitLab"])
        type_text(self.page.search, "docs")
        self.assertEqual([it.data.title for it in self.page.board.items if it.kind == "tile"], ["Docs"])
        self.page.search.setText("zzz")
        self.assertEqual(self.page.board.items[-1].data, "Nothing found.")

    def test_tied_tile_copies_login_and_secret(self):
        board = self.page.board
        QTest.mouseClick(board, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, self.tile_zone(self.tied, "login"))
        self.assertEqual(self.win.fake_clip.text, "me@example.com")
        QTest.mouseClick(board, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, self.tile_zone(self.tied, "secret"))
        self.assertEqual(self.win.fake_clip.text, "tile-secret")

    def test_click_opens_the_address(self):
        with mock.patch("webbrowser.open") as browser:
            QTest.mouseClick(self.page.board, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, self.tile_zone(self.plain, "open"))
        browser.assert_called_once_with("https://docs.example.com")
        self.assertGreater(self.plain.opened, 0)

    def test_reveal_and_the_guard(self):
        vault = self.win.vault
        self.page.reveal(self.tied)
        self.assertIn(self.tied.id, self.page.revealed)
        self.page.reveal(self.tied)
        self.assertNotIn(self.tied.id, self.page.revealed)
        vault.security.vault_guard = GUARD_PIN
        vault.security.set_pin("1357")
        self.win.security.lock_secrets()
        self.assertEqual(self.page.painter.rows(self.tied), ["login", "locked"], "the password row is locked")
        with mock.patch("daykeeper.qt.lock.ask_secret") as prompt:
            QTest.mouseClick(self.page.board, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, self.tile_zone(self.tied, "locked"))
        prompt.assert_called_once()

    def test_editor_creates_and_suggests_the_entry(self):
        self.entry.fields[2].value = "https://git.example.com/login"
        dlg = self.page.bookmark_dialog(None)
        parts = self.page.dialog_parts
        type_text(parts["url"], "git.example.com/x")
        self.assertEqual(parts["tied"].currentData(), self.entry.id, "tied by the host")
        parts["title"].setText("Second")
        parts["save"]()
        self.assertFalse(dlg.isVisible())
        made = [b for b in self.win.vault.bookmarks.items if b.title == "Second"]
        self.assertEqual(len(made), 1)
        self.assertEqual(made[0].url, "https://git.example.com/x")

    def test_editor_needs_a_title_or_address(self):
        self.page.bookmark_dialog(None)
        self.page.dialog_parts["save"]()
        self.assertEqual(self.page.dialog_parts["error"].text(), "Fill in a title or an address")

    def test_menu_move_and_delete(self):
        self.page.tile_menu(self.plain, QPoint(0, 0))
        labels = [a.text() for a in self.page.last_menu.actions() if a.text()]
        self.assertIn("Edit…", labels)
        self.page.last_menu.close()
        with mock.patch.object(bm_page, "ask_yes_no", return_value=True):
            self.page._delete(self.plain)
        self.assertNotIn(self.plain, self.win.vault.bookmarks.items)

    def test_keyboard_walks_the_tiles(self):
        board = self.page.board
        board.setFocus(Qt.FocusReason.TabFocusReason)
        tiles = board.tiles()
        self.assertEqual(board.focused, tiles[0])
        QTest.keyClick(board, Qt.Key.Key_Tab)
        self.assertEqual(board.focused, tiles[1])
        with mock.patch("webbrowser.open") as browser:
            QTest.keyClick(board, Qt.Key.Key_Return)
        browser.assert_called_once()
        QTest.keyClick(board, Qt.Key.Key_Tab)
        self.assertIsNot(QApplication.focusWidget(), board, "past the last tile the focus leaves the board")

    def test_site_icon_is_decoded_by_our_decoder(self):
        rgba = bytearray([255, 0, 0, 255] * 26 * 26)
        from daykeeper.pngimage import png

        data = png(26, 26, rgba)
        pixmap = bm_page.pixels_to_pixmap(data, 1.0)
        self.assertEqual((pixmap.width(), pixmap.height()), (26, 26))
        self.assertEqual(pixmap.toImage().pixelColor(5, 5).red(), 255)
        self.assertIs(siteicon.decode_png(data).width, 26)


class BookmarksSpeedTest(unittest.TestCase):
    """300 tiles. Only a gross slowdown fails here; tests/bench_qt_tabs.py prints the numbers."""

    def test_speed(self):
        reset_globals()
        results = qt_speed.measure_bookmarks()
        print(chr(10) + qt_speed.report(results), end=" ")
        for key, value in results.items():
            self.assertLess(value, qt_speed.TARGETS[key] * qt_speed.SLACK, key)


if __name__ == "__main__":
    unittest.main()
