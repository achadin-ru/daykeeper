"""The global hotkey: reading a combination, and on Windows registering and freeing one.
No key is ever pressed here; every registered combination is freed again."""
import sys
import unittest

from daykeeper import hotkey
from daykeeper.hotkey import MODS, WindowsHotkey, parse_combo

# a combination no keyboard has: free on any machine, and nobody presses it by accident
UNUSUAL = "ctrl+alt+shift+F24"


class ParseComboTest(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(parse_combo("ctrl+shift+space"), (MODS["ctrl"] | MODS["shift"], 0x20))
        self.assertEqual(parse_combo(" Ctrl + Shift + Space "), (MODS["ctrl"] | MODS["shift"], 0x20))
        self.assertEqual(parse_combo("alt+K"), (MODS["alt"], ord("K")))
        self.assertEqual(parse_combo("win+alt+7"), (MODS["win"] | MODS["alt"], ord("7")))
        self.assertEqual(parse_combo("control+f12"), (MODS["ctrl"], 0x7B))
        self.assertEqual(parse_combo(UNUSUAL), (MODS["ctrl"] | MODS["alt"] | MODS["shift"], 0x87))
        self.assertEqual(parse_combo("F9"), (0, 0x78))  # a function key alone is allowed
        self.assertEqual(parse_combo("ctrl+plus"), (MODS["ctrl"], 0xBB))

    def test_bad_combos(self):
        for text in ("", "ctrl", "ctrl+shift", "ctrl+a+b", "ctrl++", "ctrl+nokey", "a", "space", "ctrl+f25", "+a", "ctrl+ф"):
            self.assertIsNone(parse_combo(text), text)

    def test_bad_combo_is_a_problem_text(self):
        problem = WindowsHotkey("ctrl+nokey").start()
        self.assertIn("ctrl+nokey", problem)

    def test_a_press_reaches_the_queue_and_the_callback(self):
        """The message a press would bring, handed to the loop's handler directly."""
        import queue

        ticks: queue.Queue = queue.Queue()
        calls = []
        key = WindowsHotkey(UNUSUAL, ticks, notify=lambda: calls.append(1))
        key._press()
        self.assertEqual((ticks.get_nowait(), calls), (True, [1]))
        broken = WindowsHotkey(UNUSUAL, notify=lambda: 1 / 0)
        broken._press()  # a broken callback does not end the loop

    @unittest.skipIf(sys.platform == "win32", "the other systems")
    def test_other_systems_say_why(self):
        key = WindowsHotkey(UNUSUAL)
        self.assertTrue(key.start())
        key.stop()  # nothing to stop: no error


@unittest.skipUnless(sys.platform == "win32", "RegisterHotKey is Windows only")
class WindowsHotkeyTest(unittest.TestCase):
    def test_register_and_free(self):
        first = WindowsHotkey(UNUSUAL)
        try:
            self.assertEqual(first.start(), "")
            self.assertTrue(first.running)
            self.assertEqual(first.start(), "")  # a second start of the same object: nothing new
            second = WindowsHotkey(UNUSUAL)
            try:
                problem = second.start()  # taken, by the first: words, not an exception
                self.assertIn(UNUSUAL, problem)
                self.assertFalse(second.running)
            finally:
                second.stop()
        finally:
            first.stop()
        self.assertFalse(first.running)
        again = WindowsHotkey(UNUSUAL)  # freed: it can be taken again
        try:
            self.assertEqual(again.start(), "")
        finally:
            again.stop()

    def test_stop_without_start(self):
        WindowsHotkey(UNUSUAL).stop()
        self.assertIsNotNone(hotkey._user32())


if __name__ == "__main__":
    unittest.main()
