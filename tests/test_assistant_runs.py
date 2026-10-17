"""The quiet channel and the GeckIt channel with a Python child standing in for `claude`
and `geckit`: the answer, an error, a timeout that ends the process, the cost. The real
assistants are never started."""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from daykeeper import assistant_runs as runs
from daykeeper.assistants import Assignment, GeckitInstall

INSTALL = GeckitInstall("GeckIt.exe", "cli.js", "D:/data/geckit")


def child(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def alive(pid: int) -> bool:
    """Whether a process is still running, without touching it."""
    if sys.platform == "win32":
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait(run, timeout: float = 20.0) -> None:
    end = time.monotonic() + timeout
    while run.running and time.monotonic() < end:
        time.sleep(0.02)


ECHO_JSON = (
    "import sys, json, os\n"
    "text = sys.stdin.read()\n"
    "print(json.dumps({'type': 'result', 'result': ' got: ' + text + ' | ' + os.getcwd(), 'total_cost_usd': 0.0123,"
    " 'duration_ms': 1500, 'is_error': False}))\n"
)


class QuietRunTest(unittest.TestCase):
    def setUp(self):
        self.done: list = []
        self.event = threading.Event()

    def notify(self, run):
        self.done.append((run, threading.current_thread() is threading.main_thread()))
        self.event.set()

    def test_normal_answer_with_cost(self):
        a = Assignment("custom", "t", "привет")
        pool = runs.QuietRuns(self.notify)
        run = pool.start(a, child(ECHO_JSON), env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        self.assertTrue(self.event.wait(20))
        wait(run)
        self.assertTrue(run.ok, run.error)
        self.assertTrue(run.text.startswith("got: привет | "))
        self.assertNotEqual(os.path.normcase(run.text.split(" | ")[1]), os.path.normcase(os.getcwd()))  # a scratch folder
        self.assertFalse(os.path.exists(run.scratch))  # removed after
        self.assertEqual((run.cost, run.duration_ms, run.is_error, run.code), (0.0123, 1500, False, 0))
        self.assertEqual(self.done, [(run, False)])  # called from the reading thread
        self.assertEqual(list(pool.history), [run])

    def test_error_answer(self):
        code = "import json; print(json.dumps({'result': 'Not signed in', 'is_error': True, 'total_cost_usd': 0}))"
        run = runs.QuietRun(Assignment("custom", "t", "x"), child(code), notify=self.notify)
        self.assertTrue(self.event.wait(20))
        wait(run)
        self.assertFalse(run.ok)
        self.assertTrue(run.is_error)
        self.assertEqual(run.error, "Not signed in")
        self.assertEqual(run.cost, 0.0)

    def test_unreadable_answer_shows_stderr(self):
        code = "import sys; sys.stderr.write('boom happened'); sys.exit(2)"
        run = runs.QuietRun(Assignment("custom", "t", "x"), child(code), notify=self.notify)
        self.assertTrue(self.event.wait(20))
        wait(run)
        self.assertEqual((run.code, run.error), (2, "boom happened"))

    def test_empty_result(self):
        run = runs.QuietRun(Assignment("custom", "t", "x"), child("print('{\"result\": \"  \"}')"), notify=self.notify)
        self.assertTrue(self.event.wait(20))
        wait(run)
        self.assertFalse(run.ok)
        self.assertEqual(run.error, "The assistant answered with nothing")

    def test_timeout_kills_the_process_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "grandchild.pid"
            code = (
                "import pathlib, subprocess, sys, time\n"
                "kid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                f"pathlib.Path({str(pid_file)!r}).write_text(str(kid.pid))\n"
                "time.sleep(60)\n"
            )
            started = time.monotonic()
            run = runs.QuietRun(Assignment("custom", "t", "x"), child(code), timeout=3, notify=self.notify)
            self.assertTrue(self.event.wait(30))
            self.assertLess(time.monotonic() - started, 25)
            self.assertTrue(run.timed_out)
            self.assertFalse(run.ok)
            self.assertEqual(run.error, "The assistant did not answer in 3 s")
            self.assertIsNotNone(run.proc.poll())
            grandchild = int(pid_file.read_text())
            end = time.monotonic() + 10
            while alive(grandchild) and time.monotonic() < end:
                time.sleep(0.05)
            self.assertFalse(alive(grandchild), "the grandchild outlived the timeout")

    def test_stop(self):
        run = runs.QuietRun(Assignment("custom", "t", "x"), child("import time; time.sleep(60)"), notify=self.notify)
        time.sleep(0.3)
        run.stop()
        self.assertTrue(self.event.wait(20))
        self.assertEqual(run.error, "Stopped")
        self.assertFalse(run.running)

    def test_a_program_that_cannot_start(self):
        run = runs.QuietRun(Assignment("custom", "t", "x"), ["no-such-program-xyz-" + "q" * 8], notify=self.notify)
        self.assertFalse(run.running)
        self.assertEqual(run.code, -1)
        self.assertIn("no-such-program", run.error)
        self.assertEqual(len(self.done), 1)

    def test_provider_keys_stay_in_the_parent_by_default(self):
        code = "import json, os; print(json.dumps({'result': repr(os.environ.get('ANTHROPIC_API_KEY')) + ' ' + os.environ.get('DK_PLAIN', '')}))"
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "parent-only", "DK_PLAIN": "kept"}):
            run = runs.QuietRun(Assignment("custom", "t", "x"), child(code), notify=self.notify)
            self.assertTrue(self.event.wait(20))
        wait(run)
        self.assertEqual(run.text, "None kept")

    def test_an_exception_in_finish_still_ends_the_run(self):
        class Broken(runs.QuietRun):
            def finish(self, out, err):
                raise RuntimeError("bad answer")

        run = Broken(Assignment("custom", "t", "x"), child("print('x')"), notify=self.notify)
        self.assertTrue(self.event.wait(20))
        self.assertFalse(run.running)
        self.assertEqual(run.error, "Daykeeper could not read the answer: bad answer")
        self.assertFalse(os.path.exists(run.scratch))

    def test_deeply_nested_json_is_not_an_answer(self):
        code = "import sys; sys.stdout.write('[' * 200000 + ']' * 200000)"
        run = runs.QuietRun(Assignment("custom", "t", "x"), child(code), notify=self.notify)
        self.assertTrue(self.event.wait(20))
        self.assertFalse(run.running)
        self.assertEqual(run.code, 0)  # it ran and answered; the answer is what is refused
        self.assertTrue(run.error.endswith("]]]"), run.error[:80])
        self.assertFalse(os.path.exists(run.scratch))

    def test_history_keeps_the_last_ones(self):
        pool = runs.QuietRuns(keep=2)
        made = [pool.start(Assignment("custom", "t", "x"), ["no-such-program-xyz-" + "q" * 8]) for _i in range(3)]
        self.assertEqual(list(pool.history), made[1:])
        self.assertEqual(pool.running(), [])
        pool.stop_all()


class ParseAnswerTest(unittest.TestCase):
    def test_shapes(self):
        a = runs.parse_answer('{"result": " hi ", "total_cost_usd": 0.5, "duration_ms": 12, "is_error": false}')
        self.assertEqual((a.text, a.cost, a.duration_ms, a.is_error), ("hi", 0.5, 12, False))
        listed = json.dumps([{"type": "system"}, {"type": "result", "result": "x", "total_cost_usd": 1}])
        self.assertEqual(runs.parse_answer(listed).cost, 1.0)
        self.assertEqual(runs.parse_answer('noise\n{"result": "late"}').text, "late")
        self.assertIsNone(runs.parse_answer("not json"))
        self.assertIsNone(runs.parse_answer('{"other": 1}'))
        odd = runs.parse_answer('{"result": 5, "total_cost_usd": "cheap", "duration_ms": true}')
        self.assertEqual((odd.text, odd.cost, odd.duration_ms), ("", None, None))

    def test_deep_nesting(self):
        deep = '{"a":' * 100_000 + "1" + "}" * 100_000
        with self.assertRaises(RecursionError):
            json.loads(deep)  # what the parser has to survive
        self.assertIsNone(runs.parse_answer(deep))
        self.assertIsNone(runs.parse_answer("noise\n" + deep))
        self.assertEqual(runs.parse_geckit_answer(deep).state, "error")


class GeckitAnswerTest(unittest.TestCase):
    def test_plain_lines(self):
        self.assertEqual(runs.parse_geckit_answer("1 Started abc123: go on\nReply: ok"), runs.GeckitAnswer("started", "abc123", "go on", "ok"))
        self.assertEqual(runs.parse_geckit_answer("1 Queued q42"), runs.GeckitAnswer("queued", "q42"))
        self.assertEqual(runs.parse_geckit_answer("1 Refused\nReply: not now"), runs.GeckitAnswer("refused", reply="not now"))

    def test_json(self):
        text = json.dumps({"tasks": [{"task": 1, "answer": "started", "id": "s1", "note": "n"}], "reply": "r"})
        self.assertEqual(runs.parse_geckit_answer(text), runs.GeckitAnswer("started", "s1", "n", "r"))
        self.assertEqual(runs.parse_geckit_answer(json.dumps({"tasks": [{"answer": "refused"}]})).state, "refused")
        self.assertEqual(runs.parse_geckit_answer(json.dumps({"tasks": [{"answer": "maybe"}]})).state, "error")

    def test_errors(self):
        answer = runs.parse_geckit_answer("GeckIt is not running, so there is nobody to ask.")
        self.assertEqual((answer.state, answer.note), ("error", "GeckIt is not running, so there is nobody to ask."))
        self.assertEqual(runs.parse_geckit_answer("").state, "error")

    def test_send_with_a_stand_in(self):
        done = threading.Event()
        code = "import json; print(json.dumps({'tasks': [{'task': 1, 'answer': 'queued', 'id': 'g7'}]}))"
        send = runs.GeckitSend(Assignment("note_task", "t", "x"), child(code), INSTALL, notify=lambda r: done.set())
        self.assertTrue(done.wait(20))
        self.assertEqual(send.answer, runs.GeckitAnswer("queued", "g7"))
        self.assertEqual(send.error, "")
        bad = runs.GeckitSend(Assignment("note_task", "t", "x"), child("import sys; sys.stderr.write('GeckIt is not running'); sys.exit(1)"), INSTALL)
        wait(bad)
        self.assertEqual((bad.answer.state, bad.error), ("error", "GeckIt is not running"))
        missing = runs.GeckitSend(Assignment("note_task", "t", "x"), ["no-such-program-xyz-" + "q" * 8], INSTALL)
        self.assertEqual(missing.answer.state, "error")

    def test_send_runs_with_the_geckit_environment(self):
        """Run as Node (never GeckIt's window), on its data folder, without provider keys."""
        code = ("import json, os; print(json.dumps({'tasks': [{'answer': 'queued', 'id': ' '.join("
                "[os.environ.get('ELECTRON_RUN_AS_NODE', '-'), os.environ.get('GECKIT_DATA', '-'), "
                "os.environ.get('OPENAI_API_KEY', '-')])}]}))")
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "parent-only"}):
            send = runs.GeckitSend(Assignment("note_task", "t", "x"), child(code), INSTALL)
        wait(send)
        self.assertEqual(send.answer, runs.GeckitAnswer("queued", "1 D:/data/geckit -"))


if __name__ == "__main__":
    unittest.main()
