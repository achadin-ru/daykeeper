"""The Notes tab of the Qt window: the card is one painted widget with editors over its
spots (the same behaviour tests/test_notes_checklist.py asks of the Tk card), the file
viewer, and the speed of a busy day."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import qt_speed
from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt, type_text

require_qt()

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QLineEdit, QPlainTextEdit  # noqa: E402

from daykeeper.journal import today  # noqa: E402
from daykeeper.qt.dialogs.fileview import FileViewer  # noqa: E402
from daykeeper.qt.pages import notes as notes_page  # noqa: E402

LONG_TAIL = " and a long tail that wraps onto a second line of the card for sure" * 4


def setUpModule():
    qt_app()


class NoteCardTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name)
        self.note = self.win.vault.notes.add(today(), title="list", text="first\nsecond")
        for i in range(4):
            self.note.add_item(f"item {i}" + (LONG_TAIL if i == 1 else ""))
        self.note.toggle(self.note.items[3])
        self.win.session.touch("notes")
        self.win.show_page("notes")
        pump()
        self.page = self.win.page("notes")
        self.card = self.page.cards[self.note.id]

    def tearDown(self):
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def zone(self, kind, payload=None) -> QPoint:
        if kind == "text":
            return self.card.text_box.center()
        for rect, k, p in self.card.zones:
            if k == kind and (payload is None or p is payload):
                return rect.center()
        raise KeyError(kind)

    def click(self, kind, payload=None):
        QTest.mouseClick(self.card, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, self.zone(kind, payload))
        pump()

    def test_the_card_is_one_widget_with_everything_drawn(self):
        self.assertEqual([c for c in self.card.children() if c.isWidgetType()], [], "no widget on the card until something is edited")
        kinds = [z[1] for z in self.card.zones]
        self.assertEqual(kinds.count("item"), 4)
        for kind in ("title", "pin", "add", "plus", "fold", "clear", "more", "colour", "attach"):
            self.assertIn(kind, kinds)
        rows = [z[0] for z in self.card.zones if z[1] == "item"]
        self.assertGreater(rows[1].height(), rows[0].height(), "a long item wraps and its row is taller")
        self.assertEqual(self.card.height(), self.card.natural_h)

    def test_a_click_opens_an_editor_over_the_spot_and_typing_edits(self):
        item = self.note.items[0]
        self.click("item", item)
        self.assertIsInstance(self.card.editor, notes_page.ItemEdit)
        self.assertEqual(self.card.editing, ("item", item))
        type_text(self.card.editor, "!")
        self.assertEqual(item.text.rstrip("!") + "!", item.text)
        self.assertTrue(item.text.endswith("!"))
        self.card.close_editor()
        self.assertIsNone(self.card.editor)
        self.click("title")
        self.assertIsInstance(self.card.editor, QLineEdit)
        self.card.editor.end(False)
        type_text(self.card.editor, "!")
        self.assertEqual(self.note.title, "list!")
        self.card.close_editor()
        self.click("text")
        self.assertIsInstance(self.card.editor, QPlainTextEdit)
        before = self.card.natural_h
        self.card.editor.moveCursor(self.card.editor.textCursor().MoveOperation.End)
        self.card.editor.insertPlainText("\nthird\nfourth")
        pump()
        self.assertTrue(self.note.text.endswith("third\nfourth"))
        self.assertGreater(self.card.natural_h, before, "the card grows with the text")
        self.card.close_editor()
        self.assertIn("fourth", self.card.preview())

    def test_tab_moves_between_the_fields_and_past_the_last(self):
        self.card.edit("title")
        QTest.keyClick(self.card.editor, Qt.Key.Key_Tab)
        self.assertEqual(self.card.editing, ("text", None))
        QTest.keyClick(self.card.editor, Qt.Key.Key_Tab)
        self.assertEqual(self.card.editing, ("item", self.note.open_items()[0]))
        QTest.keyClick(self.card.editor, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.card.editing, ("text", None))
        for _ in range(6):
            self.card.neighbour(+1)
        self.assertIsNone(self.card.editor, "past the last field of the only card")

    def test_the_check_box_ticks_and_the_cross_removes(self):
        self.click("check", self.note.items[2])
        self.assertTrue(self.note.items[2].done)
        self.assertEqual(len(self.note.open_items()), 2)
        self.click("rm", self.note.items[1])
        self.assertEqual([i.text for i in self.note.open_items()], ["item 0"])
        self.assertEqual(sum(1 for z in self.card.zones if z[1] == "item"), 3)

    def test_adding_backspace_and_moving(self):
        self.card.edit("add")
        type_text(self.card.editor, "new one")
        QTest.keyClick(self.card.editor, Qt.Key.Key_Return)
        self.assertEqual(self.note.open_items()[-1].text, "new one")
        self.assertEqual(self.card.editing, ("add", None), "Enter keeps the “+” row open")
        new = self.note.open_items()[-1]
        self.card.edit("item", new)
        self.card.editor.setPlainText("")
        QTest.keyClick(self.card.editor, Qt.Key.Key_Backspace)
        self.assertEqual(len(self.note.open_items()), 3, "the emptied item is gone")
        self.assertEqual(self.card.editing, ("item", self.note.open_items()[2]), "the one above is being edited")
        first = self.note.open_items()[0]
        self.card.edit("item", first)
        QTest.keyClick(self.card.editor, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
        self.assertIs(self.note.open_items()[1], first)
        self.assertEqual(self.card.editing, ("item", first), "the moved item keeps its editor")

    def test_text_left_in_the_add_row_becomes_an_item_when_the_user_moves_on(self):
        self.card.edit("add")
        type_text(self.card.editor, "left behind")
        self.page.search.setFocus()
        pump(20)
        self.assertEqual(self.note.open_items()[-1].text, "left behind")

    def saved_items(self) -> list[str]:
        from daykeeper.vault import Vault

        saved = Vault.open(self.win.vault_path, "test-master-1")
        return [i.text for n in saved.notes.notes if n.id == self.note.id for i in n.items]

    def test_text_in_the_add_row_is_saved_by_a_lock(self):
        """Typed but not yet an item: the lock saves it as one (the Tk window lost it)."""
        self.card.edit("add")
        type_text(self.card.editor, "typed before the lock")
        self.win.lock()
        pump()
        self.assertIn("typed before the lock", self.saved_items())

    def test_text_in_the_add_row_is_saved_by_closing_the_window(self):
        self.card.edit("add")
        type_text(self.card.editor, "typed before closing")
        self.win.close()
        pump()
        self.assertIn("typed before closing", self.saved_items())

    def test_text_in_the_add_row_is_kept_by_a_language_switch(self):
        self.card.edit("add")
        type_text(self.card.editor, "typed before the switch")
        self.win.set_ui_language("ru")
        pump()
        self.assertIn("typed before the switch", [i.text for i in self.note.items])

    def test_the_add_row_of_a_note_an_undo_replaced_goes_nowhere(self):
        """An undo rebuilds the day with new note objects: the text left in the old card must
        not be appended to a note nothing saves any more (it is dropped, as the undo asks)."""
        self.card.add_text = "stale"
        stale = self.card.note
        self.win.vault.notes.notes = [n for n in self.win.vault.notes.notes if n is not stale]
        before = len(stale.items)
        self.page.commit_left_item(self.card)
        self.assertEqual(len(stale.items), before)

    def test_folding_and_the_dragged_height(self):
        self.click("fold")
        self.assertTrue(self.note.hide_done)
        self.assertFalse(any(z[1] == "item" and z[2].done for z in self.card.zones))
        self.click("fold")
        self.assertTrue(any(z[1] == "item" and z[2].done for z in self.card.zones))
        natural = self.card.natural_h
        self.note.height = natural + 200
        self.page.place_cards()
        pump()
        self.assertGreaterEqual(self.card.height(), natural + 190)
        footer = next(z for z in self.card.zones if z[1] == "more")
        self.assertGreater(footer[0].y(), natural, "the footer moved down into the stretched card")

    def test_drag_the_corner_and_double_click_back(self):
        start = QPoint(self.card.width() - 3, self.card.height() - 3)
        QTest.mousePress(self.card, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        QTest.mouseMove(self.card, start + QPoint(-60, 80))
        self.card._drag_to(self.card.mapToGlobal(start + QPoint(-60, 80)))
        QTest.mouseRelease(self.card, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(-60, 80))
        self.assertGreater(self.note.width, 0)
        self.assertGreater(self.note.height, 0)
        QTest.mouseDClick(self.card, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(self.card.width() - 3, self.card.height() - 3))
        self.assertEqual((self.note.width, self.note.height), (0, 0))

    def test_keyboard_pick_and_edit(self):
        self.card.setFocus()
        self.card.select(0)
        self.assertEqual(self.card.sel, 0)
        QTest.keyClick(self.card, Qt.Key.Key_Down)
        QTest.keyClick(self.card, Qt.Key.Key_Down)
        QTest.keyClick(self.card, Qt.Key.Key_Return)
        self.assertEqual(self.card.editing, ("item", self.note.open_items()[0]))
        self.card.close_editor()
        self.card.setFocus()
        self.card.select(0)
        QTest.keyClick(self.card, "x")
        self.assertEqual(self.card.editing, ("title", None))
        self.assertEqual(self.note.title, "listx")

    def test_menu_colour_pin_carry_and_delete(self):
        self.page.note_menu(self.note, QPoint(0, 0))
        texts = [a.text() for a in self.page.last_menu.actions() if a.text()]
        self.assertIn("Wide card", texts)
        self.page.last_menu.close()
        self.page.note_set_color(self.note, "green")
        self.assertEqual(notes_page.note_colours(self.note)[1], "#16A34A")
        self.page.note_pin(self.note)
        self.assertTrue(self.note.pinned)
        with mock.patch.object(notes_page, "ask_yes_no", return_value=True):
            self.page.note_delete(self.note)
        self.assertEqual(self.win.vault.notes.for_day(today()), [])

    def test_attach_and_open_a_markdown_file(self):
        md = Path(self.tmp.name) / "plan.md"
        md.write_text("# Plan\n\n- [ ] first task\n- [x] done task\n\n[site](https://example.com)\n", encoding="utf-8")
        self.page.note_attach(self.note, [str(md)])
        self.assertEqual(len(self.note.attachments), 1)
        self.assertIn("file", [z[1] for z in self.card.zones])
        viewer = FileViewer(self.win, md)
        text = viewer.view.toPlainText()
        self.assertIn("☐", text)
        self.assertIn("☑", text)
        viewer._toggle_task(2)
        self.assertIn("- [x] first task", md.read_text(encoding="utf-8"))
        viewer.toggle_edit()
        viewer.editor.setPlainText("changed")
        viewer.on_lock()
        self.assertEqual(md.read_text(encoding="utf-8"), "changed", "a lock keeps the edit in the file")
        viewer.close()


class NotesSpeedTest(unittest.TestCase):
    """A busy day: 16 cards, 120 items, a 30 KB note. Only a gross slowdown fails here; tests/bench_qt_tabs.py prints the numbers."""

    def test_speed(self):
        reset_globals()
        results = qt_speed.measure_notes()
        print(chr(10) + qt_speed.report(results), end=" ")
        for key, value in results.items():
            self.assertLess(value, qt_speed.TARGETS[key] * qt_speed.SLACK, key)


if __name__ == "__main__":
    unittest.main()
