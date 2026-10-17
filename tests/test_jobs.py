"""JobPool: answers come through drain(), an exception comes as an error result."""
import time
import unittest

from daykeeper.jobs import JobPool


def wait_for(pool: JobPool, n: int, timeout: float = 5.0) -> list:
    out = []
    end = time.monotonic() + timeout
    while len(out) < n and time.monotonic() < end:
        out += pool.drain()
        time.sleep(0.01)
    return out


class JobPoolTest(unittest.TestCase):
    def test_result_and_meta_come_back(self):
        pool = JobPool(workers=2, name="t")
        pool.submit("double", lambda x: x * 2, 21, bid="b1")
        self.assertEqual(pool.pending, 1)
        results = wait_for(pool, 1)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual((r.kind, r.meta, r.value, r.ok), ("double", {"bid": "b1"}, 42, True))
        self.assertEqual(pool.pending, 0)
        pool.close()

    def test_an_exception_is_an_error_result(self):
        pool = JobPool(workers=1)

        def boom():
            raise ValueError("bad site")

        pool.submit("site", boom, bid="x")
        r = wait_for(pool, 1)[0]
        self.assertFalse(r.ok)
        self.assertIsInstance(r.error, ValueError)
        self.assertEqual(r.meta, {"bid": "x"})
        self.assertIsNone(r.value)
        pool.close()

    def test_drain_never_blocks_and_close_does_not_hang(self):
        pool = JobPool(workers=1)
        self.assertEqual(pool.drain(), [])
        pool.submit("slow", time.sleep, 0.05)
        t0 = time.monotonic()
        pool.close()
        self.assertLess(time.monotonic() - t0, 1.0)
        pool.close()  # twice is fine

    def test_many_jobs_all_arrive(self):
        pool = JobPool(workers=3)
        for i in range(20):
            pool.submit("n", lambda v: v, i)
        values = sorted(r.value for r in wait_for(pool, 20))
        self.assertEqual(values, list(range(20)))
        pool.close()

    def test_notify_comes_after_each_answer_is_queued(self):
        seen: list[int] = []
        pool = JobPool(workers=2, notify=lambda: seen.append(len(pool._results.queue)))
        for i in range(5):
            pool.submit("n", lambda v: v, i)
        self.assertEqual(len(wait_for(pool, 5)), 5)
        end = time.monotonic() + 2
        while len(seen) < 5 and time.monotonic() < end:
            time.sleep(0.01)
        self.assertEqual(len(seen), 5)
        self.assertTrue(all(n >= 1 for n in seen), "the answer is there when notify is called")
        pool.close()


if __name__ == "__main__":
    unittest.main()
