"""Run: a Python child's lines and exit code through the queue; stop; decode_line."""
import sys
import time
import unittest

from daykeeper import win32
from daykeeper.runner import Run, decode_line


class _Routine:
    def __init__(self, name="r"):
        self.name = name
        self.id = "rid"


def drain_until_end(run: Run, timeout: float = 15.0) -> list:
    got = []
    end = time.monotonic() + timeout
    while run.running and time.monotonic() < end:
        got += run.take()
        time.sleep(0.02)
    got += run.take()
    return got


class RunTest(unittest.TestCase):
    def test_lines_and_exit_code(self):
        run = Run(_Routine(), [sys.executable, "-c", "print('a'); print('b'); import sys; sys.exit(3)"], "", "")
        self.assertTrue(run.running)
        got = drain_until_end(run)
        self.assertEqual(run.lines, ["a", "b"])
        self.assertEqual(run.code, 3)
        self.assertFalse(run.running)
        self.assertEqual(got[-1], ("end", 3))
        self.assertGreaterEqual(run.duration(), 0.0)
        self.assertFalse(run.auto)

    def test_stop_ends_a_sleeping_child(self):
        run = Run(_Routine(), [sys.executable, "-c", "import time; time.sleep(30)"], "", "", auto=True)
        run.stop()
        drain_until_end(run)
        self.assertFalse(run.running)
        self.assertNotEqual(run.code, 0)
        self.assertTrue(run.auto)

    def test_a_program_that_cannot_start(self):
        run = Run(_Routine(), ["no-such-program-xyz-" + "q" * 8], "", "")
        self.assertFalse(run.running)
        self.assertEqual(run.code, -1)
        self.assertEqual(len(run.lines), 1)

    def test_extra_environment_reaches_the_child(self):
        code = "import os; print(os.environ['DAYKEEPER_REF']); print(os.environ.get('PYTHONUTF8')); print('PATH' in os.environ)"
        run = Run(_Routine(), [sys.executable, "-c", code], "", "", auto=True, env={"DAYKEEPER_REF": "group/repo!12"})
        drain_until_end(run)
        self.assertEqual(run.lines, ["group/repo!12", "1", "True"], "merged over the current environment")
        self.assertEqual(run.code, 0)

    def test_decode_line(self):
        self.assertEqual(decode_line("привет\n".encode()), "привет\n")
        cp866 = "привет".encode("cp866")
        text = decode_line(cp866)
        self.assertIsInstance(text, str)
        if win32.oem_codepage() == "cp866":
            self.assertEqual(text, "привет")


if __name__ == "__main__":
    unittest.main()
