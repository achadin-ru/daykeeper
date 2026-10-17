"""The quiet channel and the GeckIt channel with a Python child standing in for `claude`
and `geckit`: the answer, an error, a timeout that ends the process, the cost. The real
assistants are never started."""

import json
import os
import sys
import threading
import time
import unittest

from daykeeper import assistant_runs as runs
from daykeeper.assistants import Assignment


def child(code: str) -> list[str]:
    return [sys.executable, "-c", code]


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
        code = (
            "import subprocess, sys, time\n"
            "kid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "print(kid.pid, flush=True)\n"
            "time.sleep(60)\n"
        )
        started = time.monotonic()
        run = runs.QuietRun(Assignment("custom", "t", "x"), child(code), timeout=1.5, notify=self.notify)
        self.assertTrue(self.event.wait(30))
        self.assertLess(time.monotonic() - started, 25)
        self.assertTrue(run.timed_out)
        self.assertFalse(run.ok)
        self.assertEqual(run.error, "The assistant did not answer in 2 s")
        self.assertIsNotNone(run.proc.poll())

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
        send = runs.GeckitSend(Assignment("note_task", "t", "x"), child(code), notify=lambda r: done.set())
        self.assertTrue(done.wait(20))
        self.assertEqual(send.answer, runs.GeckitAnswer("queued", "g7"))
        self.assertEqual(send.error, "")
        bad = runs.GeckitSend(Assignment("note_task", "t", "x"), child("import sys; sys.stderr.write('GeckIt is not running'); sys.exit(1)"))
        wait(bad)
        self.assertEqual((bad.answer.state, bad.error), ("error", "GeckIt is not running"))
        missing = runs.GeckitSend(Assignment("note_task", "t", "x"), ["no-such-program-xyz-" + "q" * 8])
        self.assertEqual(missing.answer.state, "error")


if __name__ == "__main__":
    unittest.main()
