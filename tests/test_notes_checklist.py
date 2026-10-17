"""A note card is one canvas (notes_view.NoteCard): the title, the text, the items, the
“+” row, the files and the footer are drawn; a field becomes a real widget over its spot
only while it is edited. Skipped without a display. Key events need the focus, which a
hidden window does not get, so the keyboard paths are driven through the methods the
bindings call; clicks are generated at the zones the card reports."""
from __future__ import annotations

import tempfile
import tkinter as tk
import unittest
from pathlib import Path

from daykeeper.journal import today
from daykeeper.settings import write_settings
from daykeeper.widgets import WrapLine
from tests.helpers import has_display, hide, reset_globals

PASSWORD = "checklist-master-1"


@unittest.skipUnless(has_display(), "no display")
class NoteCardTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "notes.pkv"
        write_settings(self.path.parent / "settings.json", {"language": "en", "geometry": "1200x760+20+20"})
        from daykeeper.app import App

        self.app = App(self.path, preview=True)
        hide(self.app)
        self.errors: list[str] = []
        self.app.report_callback_exception = lambda *exc: self.errors.append(repr(exc[1]))
        self.app.unlock(PASSWORD)
        self.note = self.app.vault.notes.add(today(), title="list", text="first\nsecond")
        # the long item must wrap at any card width the window may give it: on a runner
        # with a 1024 px screen the day is one column, and a tail of one line fits there
        long_tail = " and a long tail that wraps onto a second line of the card for sure" * 4
        for i in range(4):
            self.note.add_item(f"item {i}" + (long_tail if i == 1 else ""))
        self.note.toggle(self.note.items[3])
        self.app.session.touch("notes")
        self.app.show_page("notes")
        self.app.finish_chunks("notes")
        self.pump()
        self.page = self.app.pages["notes"]
        self.card = self.page._ncards[self.note.id]

    def tearDown(self) -> None:
        try:
            self.app.destroy()
        finally:
            self.tmp.cleanup()
            reset_globals()

    def pump(self) -> None:
        self.app.update_idletasks()
        self.app.update()

    def zone(self, kind: str, payload=None) -> tuple[int, int]:
        if kind == "text":  # the text is not a zone of its own: the room between the title and the items
            x, y, w, h = self.card._text_box
            return x + w // 2, y + h // 2
        for x0, y0, x1, y1, k, p in self.card.zones:
            if k == kind and (payload is None or p is payload):
                return (x0 + x1) // 2, (y0 + y1) // 2
        raise KeyError(kind)

    def click(self, kind: str, payload=None) -> None:
        x, y = self.zone(kind, payload)
        self.card.event_generate("<Button-1>", x=x, y=y)
        self.pump()

    def test_the_card_is_one_canvas_with_everything_drawn(self):
        self.assertEqual(self.card.winfo_children(), [], "no widget on the card until something is edited")
        kinds = [z[4] for z in self.card.zones]
        self.assertEqual(kinds.count("item"), 4)
        for kind in ("title", "pin", "add", "plus", "fold", "clear", "more", "colour", "attach"):
            self.assertIn(kind, kinds)
        rows = [z for z in self.card.zones if z[4] == "item"]
        self.assertGreater(rows[1][3] - rows[1][1], rows[0][3] - rows[0][1], "a long item wraps and its row is taller")
        self.assertEqual(self.card.winfo_reqheight(), self.card.natural_h)
        self.assertEqual(self.errors, [])

    def test_a_click_opens_an_editor_over_the_spot_and_typing_edits(self):
        item = self.note.items[0]
        self.click("item", item)
        editor = self.card.editor
        self.assertIsInstance(editor, WrapLine)
        self.assertEqual(self.card.editing, ("item", item))
        self.assertEqual(str(self.card.itemcget(f"item{id(item)}", "state")), "hidden", "the drawn text hides under the editor")
        editor.insert("end", "!")
        self.pump()
        self.assertEqual(item.text, "item 0!")
        self.card.close()
        self.pump()
        self.assertIsNone(self.card.editor)
        self.assertEqual(self.card.itemcget(f"item{id(item)}", "text"), "item 0!")
        self.click("title")
        self.assertIsInstance(self.card.editor, tk.Entry)
        self.card.editor.insert("end", "!")
        self.pump()
        self.assertEqual(self.note.title, "list!")
        self.card.close()
        self.click("text")
        self.assertIsInstance(self.card.editor, tk.Text)
        before = self.card.natural_h
        self.card.editor.insert("end", "\nthird\nfourth")
        self.card._text_grew(self.card.editor)
        self.pump()
        self.assertTrue(self.note.text.endswith("third\nfourth"))
        self.assertGreater(self.card.natural_h, before, "the card grows with the text")
        self.card.close()
        self.assertIn("fourth", self.card.itemcget("text", "text"))
        self.assertEqual(self.errors, [])

    def test_tab_moves_between_the_fields_and_past_the_last_to_the_next_card(self):
        self.card.edit("title")
        self.card._neighbour(+1)
        self.assertEqual(self.card.editing, ("text", None))
        self.card._neighbour(+1)
        self.assertEqual(self.card.editing, ("item", self.note.open_items()[0]))
        self.card._neighbour(-1)
        self.assertEqual(self.card.editing, ("text", None))
        for _ in range(6):
            self.card._neighbour(+1)
        self.assertIsNone(self.card.editor, "past the last field of the only card")
        self.assertEqual(self.errors, [])

    def test_the_check_box_ticks_and_the_cross_removes(self):
        self.click("check", self.note.items[2])
        self.assertTrue(self.note.items[2].done)
        self.assertEqual(len(self.note.open_items()), 2)
        self.click("rm", self.note.items[1])
        self.assertEqual([i.text for i in self.note.open_items()], ["item 0"])
        self.assertEqual(sum(1 for z in self.card.zones if z[4] == "item"), 3)
        self.assertEqual(self.errors, [])

    def test_adding_backspace_and_moving(self):
        self.card.add_var.set("new one")
        self.page._note_add_item(self.card, self.note, self.card.add_var)
        self.pump()
        self.assertEqual([i.text for i in self.note.open_items()][-1], "new one")
        self.assertEqual(self.card.editing, ("add", None), "Enter keeps the “+” row open")
        new = self.note.open_items()[-1]
        self.card.edit("item", new)
        self.card.editor.delete("1.0", "end")
        self.pump()
        self.page._note_backspace_item(self.card, self.note, new, self.card.editor)
        self.pump()
        self.assertEqual(len(self.note.open_items()), 3, "the emptied item is gone")
        self.assertEqual(self.card.editing, ("item", self.note.open_items()[2]), "the one above is being edited")
        first = self.note.open_items()[0]
        self.page._note_move_item(self.card, self.note, first, +1)
        self.pump()
        self.assertIs(self.note.open_items()[1], first)
        self.assertEqual(self.card.editing, ("item", first), "the moved item keeps its editor")
        self.card.close()
        self.assertEqual(self.errors, [])

    def test_folding_and_the_dragged_height(self):
        self.click("fold")
        self.assertTrue(self.note.hide_done)
        self.assertFalse(any(z[4] == "item" and z[5].done for z in self.card.zones))
        self.click("fold")
        self.assertTrue(any(z[4] == "item" and z[5].done for z in self.card.zones))
        natural = self.card.natural_h
        self.note.height = natural + 200
        self.page._place_note_cards()
        self.pump()
        self.assertGreaterEqual(self.card.winfo_height(), natural + 190)
        self.assertEqual(self.card._text_box[3] > 0, True)
        footer = next(z for z in self.card.zones if z[4] == "more")
        self.assertGreater(footer[1], natural, "the footer moved down into the stretched card")
        self.note.height = 0
        self.page._place_note_cards()
        self.pump()
        self.assertEqual(self.errors, [])

    def test_keyboard_pick_and_edit(self):
        self.card._select(0)
        self.assertEqual(self.card.sel, 0)
        self.assertTrue(self.card.find_withtag("sel"), "the picked field has a ring")
        self.card._select(2)
        self.card._edit_selected()
        self.assertEqual(self.card.editing, ("item", self.note.open_items()[0]))
        self.card.close()
        self.assertEqual(self.errors, [])

    def test_the_text_editor_grows_only_when_its_height_changes(self):
        self.card.edit("text")
        text = self.card.editor
        text.insert("end", "\nmore\nlines")
        self.card._text_grew(text)
        self.assertEqual(int(text.cget("height")), 5)
        seen: list[str] = []
        original = text.config

        def spy(*args, **kw):
            seen.append(repr(kw))
            return original(*args, **kw)

        text.config = spy  # type: ignore[method-assign]
        self.card._text_grew(text)
        self.assertEqual(seen, [], "the same height is not set again: Tk would lay the whole text out anew")
        self.card.close()


if __name__ == "__main__":
    unittest.main()
