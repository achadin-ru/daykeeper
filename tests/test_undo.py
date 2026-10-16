"""Undo and redo: the history itself and the vault's parts it puts back."""

import tempfile
import unittest
from pathlib import Path

from daykeeper.undo import History
from daykeeper.vault import UNDO_DOMAINS, Vault


class HistoryTest(unittest.TestCase):
    """The domains are dictionaries of objects: "a" holds objects "k" and "m", "b" holds "k"."""

    def setUp(self):
        self.data = {"a": {"k": "1", "m": "one"}, "b": {"k": "x"}}
        self.h = History(lambda d: dict(self.data[d]), ("a", "b"))
        self.h.reset()

    def write(self, d, key, v):
        if v is None:
            self.data[d].pop(key, None)
        else:
            self.data[d][key] = v

    def test_undo_and_redo(self):
        self.data["a"]["k"] = "2"
        self.assertTrue(self.h.capture())
        self.data["a"]["k"] = "3"
        self.data["b"]["k"] = "y"
        self.h.capture()
        self.assertEqual(self.h.undo(self.write), {"a", "b"})
        self.assertEqual(self.data, {"a": {"k": "2", "m": "one"}, "b": {"k": "x"}})
        self.assertEqual(self.h.undo(self.write), {"a"})
        self.assertEqual(self.data["a"]["k"], "1")
        self.assertEqual(self.h.undo(self.write), set(), "nothing more")
        self.h.redo(self.write)
        self.h.redo(self.write)
        self.assertEqual(self.data, {"a": {"k": "3", "m": "one"}, "b": {"k": "y"}})
        self.assertFalse(self.h.can_redo)

    def test_a_step_keeps_only_what_changed(self):
        self.data["a"]["k"] = "2"
        self.h.capture()
        self.assertEqual(self.h.undo_steps, [{"a": {"k": "1"}}], "object m and domain b are not in the step")
        self.data["a"]["new"] = "n"
        del self.data["a"]["m"]
        self.h.capture()
        self.assertEqual(self.h.undo_steps[-1], {"a": {"new": None, "m": "one"}})
        self.h.undo(self.write)
        self.assertEqual(self.data["a"], {"k": "2", "m": "one"}, "the new object went, the deleted one is back")
        self.h.redo(self.write)
        self.assertEqual(self.data["a"], {"k": "2", "new": "n"})

    def test_the_byte_budget_drops_the_oldest_steps(self):
        h = History(lambda d: dict(self.data[d]), ("a",), budget=50)
        h.reset()
        for i in range(10):
            self.data["a"]["k"] = str(i) * 20  # 20 characters a step
            h.capture()
        self.assertLessEqual(len(h.undo_steps), 3)
        self.assertLessEqual(h.bytes, 60, "one step over the budget at most")
        self.assertTrue(h.can_undo, "the newest step always stays")

    def test_nothing_changed_is_no_step(self):
        self.assertFalse(self.h.capture())
        self.assertFalse(self.h.can_undo)

    def test_a_new_change_ends_the_redo(self):
        self.data["a"]["k"] = "2"
        self.h.capture()
        self.h.undo(self.write)
        self.data["b"]["k"] = "z"
        self.assertEqual(self.h.redo(self.write), set())
        self.assertEqual(self.data, {"a": {"k": "1", "m": "one"}, "b": {"k": "z"}})

    def test_an_uncaptured_change_is_undone_first(self):
        self.data["a"]["k"] = "2"  # the window has not captured it yet
        self.h.undo(self.write)
        self.assertEqual(self.data["a"]["k"], "1")

    def test_rebase_keeps_what_came_by_itself(self):
        self.data["a"]["k"] = "2"
        self.data["b"]["k"] = "y"
        self.h.capture()
        self.data["b"]["k"] = "y+inbox"  # arrived on its own
        self.h.rebase("b")
        self.assertEqual(self.h.undo(self.write), {"a"})
        self.assertEqual(self.data, {"a": {"k": "1", "m": "one"}, "b": {"k": "y+inbox"}})

    def test_limit(self):
        h = History(lambda d: dict(self.data[d]), ("a",), limit=3)
        h.reset()
        for i in range(10):
            self.data["a"]["k"] = str(i)
            h.capture()
        self.assertEqual(len(h.undo_steps), 3)


class VaultDomainsTest(unittest.TestCase):
    def test_every_domain_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = Vault.create(Path(tmp) / "v.pkv", "master-pass-1")
            v.add_entry("one")
            v.journal.get("2026-10-13", create=True).add("ok", "APP-1")
            before = {d: v.domain_state(d) for d in UNDO_DOMAINS}
            v.add_entry("two")
            v.journal.get("2026-10-13").add("ok", "APP-2")
            for d in UNDO_DOMAINS:
                v.set_domain_state(d, before[d])
            self.assertEqual([e.name for e in v.entries], ["one"])
            self.assertEqual(v.journal.get("2026-10-13").section("ok"), ["APP-1"])
            self.assertEqual({d: v.domain_state(d) for d in UNDO_DOMAINS}, before)

    def test_every_object_round_trips_by_key(self):
        """M4: the keyed form (object_states / set_object_state) puts back exactly the objects a step names."""
        with tempfile.TemporaryDirectory() as tmp:
            v = Vault.create(Path(tmp) / "v.pkv", "master-pass-1")
            one = v.add_entry("one")
            v.add_group("Work")
            v.journal.get("2026-10-13", create=True).add("ok", "APP-1")
            note = v.notes.add("2026-10-13", title="n")
            mark = v.bookmarks.add("site", "https://example.com", group="G")
            routine = v.routines.add(name="build", target="build.bat")
            before = {d: v.object_states(d) for d in UNDO_DOMAINS}
            self.assertEqual(set(before["vault"]), {one.id, Vault.GROUPS_KEY})
            self.assertEqual(set(before["journal"]), {"2026-10-13"})
            two = v.add_entry("two")
            one.name = "one renamed"
            v.set_group(two, "Work")
            v.journal.get("2026-10-13").add("ok", "APP-2")
            v.journal.get("2026-10-14", create=True).add("ok", "APP-3")
            note.title = "renamed"
            mark.title = "renamed"
            mark.favicon = "icon-bytes"
            routine.name = "build all"
            routine.mark_started(5.0, 0)
            after = {d: v.object_states(d) for d in UNDO_DOMAINS}
            for d in UNDO_DOMAINS:
                for key in set(before[d]) | set(after[d]):
                    if before[d].get(key) != after[d].get(key):
                        v.set_object_state(d, key, before[d].get(key))
            self.assertEqual([e.name for e in v.entries], ["one"])
            self.assertEqual(v.journal.get("2026-10-13").section("ok"), ["APP-1"])
            self.assertIsNone(v.journal.get("2026-10-14"), "a day that did not exist went")
            self.assertEqual(v.notes.notes[0].title, "n")
            self.assertEqual((v.bookmarks.items[0].title, v.bookmarks.items[0].favicon), ("site", "icon-bytes"))
            self.assertIs(v.routines.items[0], routine, "the routine object stays")
            self.assertEqual((routine.name, routine.last_run), ("build", 5.0))
            self.assertEqual({d: v.object_states(d) for d in UNDO_DOMAINS}, before)

    def test_a_routines_run_and_a_bookmarks_icon_are_not_undone(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = Vault.create(Path(tmp) / "v.pkv", "master-pass-1")
            r = v.routines.add(name="build", target="build.bat")
            b = v.bookmarks.add(title="site", url="https://example.com")
            routines, marks = v.domain_state("routines"), v.domain_state("bookmarks")
            r.mark_started(123.0, 0)
            b.favicon, b.opened = "data", 99.0
            self.assertEqual(v.domain_state("routines"), routines, "a run is not a change to undo")
            self.assertEqual(v.domain_state("bookmarks"), marks, "nor a fetched icon")
            r.name = "build all"
            v.set_domain_state("routines", routines)
            self.assertIs(v.routines.items[0], r, "the routine object stays: a run under way keeps it")
            self.assertEqual((r.name, r.last_run), ("build", 123.0))
            b.title = "renamed"
            v.set_domain_state("bookmarks", marks)
            self.assertEqual((v.bookmarks.items[0].title, v.bookmarks.items[0].favicon), ("site", "data"))


if __name__ == "__main__":
    unittest.main()
