"""The sealed clipboard history: a repeat is not recorded twice, the limit keeps pinned
items, nothing is recorded while the vault is locked and the stored form is ciphertext
only. The clipboard probes run on a fake Windows API: the real clipboard is never touched."""
import json
import unittest
from unittest import mock

from daykeeper import clipboard
from daykeeper.cliphistory import MAX_CHARS, ClipHistory, ClipItem, looks_secret
from daykeeper.vault import Sealer


class RefusingSealer:
    """A locked vault's sealer: it has nothing to seal with."""

    def seal(self, text):
        raise RuntimeError("locked")

    def open(self, blob):
        raise RuntimeError("locked")


class CountingSealer(Sealer):
    """A real sealer that counts how often a sealed item is opened."""

    def __init__(self):
        super().__init__()
        self.opens = 0

    def open(self, blob):
        self.opens += 1
        return super().open(blob)


class ClipHistoryTest(unittest.TestCase):
    def setUp(self):
        self.sealer = Sealer()
        self.history = ClipHistory()

    def texts(self):
        return [item.text(self.sealer) for item in self.history.items]

    def test_a_repeat_of_the_newest_text_is_not_recorded(self):
        self.assertTrue(self.history.add("one", self.sealer, 1.0, 20))
        self.assertFalse(self.history.add("one", self.sealer, 2.0, 20))
        self.assertTrue(self.history.add("two", self.sealer, 3.0, 20))
        self.assertTrue(self.history.add("one", self.sealer, 4.0, 20), "not the newest any more")
        self.assertEqual(self.texts(), ["one", "two", "one"])

    def test_repeated_adds_do_not_open_the_newest_item(self):
        sealer = CountingSealer()
        self.history.add("one", sealer, 1.0, 20)
        for i in range(5):
            self.assertFalse(self.history.add("one", sealer, 2.0 + i, 20))
        self.assertTrue(self.history.add("two", sealer, 8.0, 20))
        self.assertFalse(self.history.add("two", sealer, 9.0, 20))
        self.assertEqual(sealer.opens, 0, "the repeat is spotted by the in-memory digest")

    def test_a_loaded_history_opens_the_newest_once_then_uses_the_digest(self):
        sealer = CountingSealer()
        self.history.add("one", sealer, 1.0, 20)
        loaded = ClipHistory.from_list(self.history.to_list())
        self.assertNotIn("_last", repr(loaded.to_list()), "the digest is never stored")
        self.assertFalse(loaded.add("one", sealer, 2.0, 20))
        self.assertFalse(loaded.add("one", sealer, 3.0, 20))
        self.assertEqual(sealer.opens, 1)

    def test_removing_the_newest_falls_back_to_opening(self):
        self.history.add("a", self.sealer, 1.0, 20)
        self.history.add("b", self.sealer, 2.0, 20)
        self.history.remove(0)
        self.assertFalse(self.history.add("a", self.sealer, 3.0, 20), "a is the newest again")
        self.assertTrue(self.history.add("b", self.sealer, 4.0, 20))
        self.assertEqual(self.texts(), ["b", "a"])

    def test_the_limit_drops_the_oldest_unpinned_and_keeps_pinned(self):
        for i in range(3):
            self.history.add(f"t{i}", self.sealer, float(i), 3)
        self.history.pin(2)  # t0, the oldest
        self.history.add("t3", self.sealer, 3.0, 3)
        self.assertEqual(self.texts(), ["t3", "t2", "t0"])
        self.history.pin(0)
        self.history.pin(1)
        self.history.add("t4", self.sealer, 4.0, 2)
        self.assertEqual(self.texts(), ["t4", "t3", "t2", "t0"], "pinned stay past the limit, the new text too")

    def test_a_text_too_long_or_blank_is_refused(self):
        self.assertFalse(self.history.add("x" * (MAX_CHARS + 1), self.sealer, 1.0, 20))
        self.assertFalse(self.history.add("   \n", self.sealer, 1.0, 20))
        self.assertTrue(self.history.add("x" * MAX_CHARS, self.sealer, 1.0, 20))
        self.assertEqual(len(self.history.items), 1)

    def test_nothing_is_recorded_while_the_vault_is_locked(self):
        self.assertFalse(self.history.add("one", RefusingSealer(), 1.0, 20))
        self.assertFalse(self.history.add("one", None, 1.0, 20))
        forgotten = Sealer()
        forgotten.forget()
        self.assertFalse(self.history.add("one", forgotten, 1.0, 20), "a zeroed key does not seal")
        self.assertEqual(self.history.items, [])

    def test_a_locked_vault_shows_nothing(self):
        self.history.add("one", self.sealer, 1.0, 20)
        self.assertEqual(self.history.items[0].text(RefusingSealer()), "")
        self.assertEqual(self.history.items[0].text(Sealer()), "", "another key does not open it")
        self.sealer.forget()
        self.assertEqual(self.history.items[0].text(self.sealer), "")

    def test_round_trip_keeps_only_ciphertext(self):
        self.history.add("plain password text", self.sealer, 1.5, 20)
        self.history.add("second clip", self.sealer, 2.5, 20)
        self.history.pin(1)
        dumped = json.dumps(self.history.to_list())
        self.assertNotIn("plain password text", dumped)
        self.assertNotIn("second clip", dumped)
        for item in self.history.items:
            self.assertNotIn(b"second clip", item.sealed)
        back = ClipHistory.from_list(json.loads(dumped))
        self.assertEqual([item.text(self.sealer) for item in back.items], ["second clip", "plain password text"])
        self.assertEqual([item.pinned for item in back.items], [False, True])
        self.assertEqual([item.ts for item in back.items], [2.5, 1.5])

    def test_from_list_skips_damaged_rows(self):
        good = ClipItem(1.0, self.sealer.seal("ok")).to_dict()
        rows = [good, {"sealed": "not base64!"}, {"ts": 2}, "text", {"sealed": good["sealed"], "ts": "bad"}]
        back = ClipHistory.from_list(rows)
        self.assertEqual([item.text(self.sealer) for item in back.items], ["ok", "ok"])
        self.assertEqual(back.items[1].ts, 0.0)
        self.assertEqual(ClipHistory.from_list(None).items, [])

    def test_remove_pin_and_clear(self):
        for text in ("a", "b", "c"):
            self.history.add(text, self.sealer, 1.0, 20)
        self.assertTrue(self.history.pin(1))
        self.assertFalse(self.history.pin(9))
        self.assertTrue(self.history.remove(0))
        self.assertFalse(self.history.remove(-1))
        self.assertEqual(self.texts(), ["b", "a"])
        self.history.clear()
        self.assertEqual(self.texts(), ["b"])
        self.history.clear(keep_pinned=False)
        self.assertEqual(self.history.items, [])

    def test_looks_secret(self):
        self.assertTrue(looks_secret("glpat-" + "Ab1" * 8))
        self.assertTrue(looks_secret("token Xy7Qm2Lp9Rt4Wv8Zk3Nb6Hd1Fg5Jc0Ve2As"))
        self.assertFalse(looks_secret("a plain sentence to keep"))
        self.assertFalse(looks_secret("https://example.org/Ab1Cd2Ef3Gh4Ij5Kl6"))


class FakeUser32:
    """The few user32 calls the probes make; the formats on the "clipboard" are a set."""

    def __init__(self, formats=(), sequence=7):
        self.registered = {}
        self.formats = set(formats)
        self.sequence = sequence
        self.calls = []

    def RegisterClipboardFormatW(self, name):
        return self.registered.setdefault(name, 0xC000 + len(self.registered))

    def IsClipboardFormatAvailable(self, fmt):
        self.calls.append(("available", fmt))
        return any(self.registered.get(name) == fmt for name in self.formats)

    def GetClipboardSequenceNumber(self):
        return self.sequence

    def __getattr__(self, name):  # OpenClipboard, GetClipboardData, EmptyClipboard, …
        raise AssertionError(f"the probe must not call {name}")


class ClipboardProbeTest(unittest.TestCase):
    def probe(self, user32):
        return mock.patch.object(clipboard, "_api", return_value=(user32, object()))

    def test_sequence_number(self):
        with self.probe(FakeUser32(sequence=42)):
            self.assertEqual(clipboard.sequence_number(), 42)
        with self.probe(FakeUser32(sequence=0)):
            self.assertIsNone(clipboard.sequence_number(), "no access: nothing to compare")
        with mock.patch.object(clipboard, "_api", return_value=None):
            self.assertIsNone(clipboard.sequence_number())

    def test_excluded_from_history_on_either_mark(self):
        for marks in ([clipboard._EXCLUDE], [clipboard._HISTORY], [clipboard._EXCLUDE, clipboard._HISTORY]):
            with self.subTest(marks=marks), self.probe(FakeUser32(marks)):
                self.assertTrue(clipboard.excluded_from_history())

    def test_plain_text_is_not_excluded_and_nothing_is_opened(self):
        user32 = FakeUser32([clipboard._CLOUD])
        with self.probe(user32):
            self.assertFalse(clipboard.excluded_from_history())
        self.assertEqual(len(user32.calls), 2, "only the two marks were asked about")
        with mock.patch.object(clipboard, "_api", return_value=None):
            self.assertFalse(clipboard.excluded_from_history())


if __name__ == "__main__":
    unittest.main()
