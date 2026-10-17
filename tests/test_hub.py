"""Hub: every subscriber gets the batch; one that fails goes to the error sink, the rest still run."""
import unittest

from daykeeper.gitlab import Event
from daykeeper.hub import Hub


class HubTest(unittest.TestCase):
    def test_every_subscriber_gets_the_whole_batch(self):
        hub = Hub()
        got_a, got_b = [], []
        hub.subscribe(got_a.extend)
        hub.subscribe(got_b.extend)
        hub.subscribe(got_b.extend)  # twice is once
        batch = [Event("merged", "group/repo!1", "t", "", "x"), Event("approved", "group/repo!2", "t", "", "y")]
        hub.publish(batch)
        self.assertEqual((got_a, got_b), (batch, batch))
        hub.publish([])
        self.assertEqual(len(got_a), 2, "an empty batch calls nobody")

    def test_an_error_does_not_stop_the_others(self):
        errors, got = [], []
        hub = Hub(on_error=lambda t, e, tb: errors.append((t, str(e), tb is not None)))

        def broken(events):
            events.clear()
            raise RuntimeError("boom")

        hub.subscribe(broken)
        hub.subscribe(got.extend)
        hub.publish([Event("merged", "group/repo!1", "t", "", "x")])
        self.assertEqual(errors, [(RuntimeError, "boom", True)])
        self.assertEqual(len(got), 1, "the next subscriber still gets the batch the broken one emptied")

    def test_a_broken_error_sink_is_survived(self):
        got = []

        def sink(*_exc):
            raise OSError("disk full")

        hub = Hub(on_error=sink)
        hub.subscribe(lambda events: 1 / 0)
        hub.subscribe(got.extend)
        hub.publish([Event("merged", "group/repo!1", "t", "", "x")])
        self.assertEqual(len(got), 1)

    def test_unsubscribe_and_errlog_as_the_sink(self):
        import functools
        import tempfile
        from pathlib import Path

        from daykeeper.errlog import write_error

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "errors.log"
            hub = Hub(on_error=functools.partial(write_error, log))

            def fails(events):
                raise ValueError("bad subscriber")

            hub.subscribe(fails)
            hub.publish([Event("merged", "group/repo!1", "t", "", "x")])
            self.assertIn("ValueError: bad subscriber", log.read_text(encoding="utf-8"))
            hub.unsubscribe(fails)
            hub.unsubscribe(fails)
            hub.publish([Event("merged", "group/repo!1", "t", "", "x")])
            self.assertEqual(log.read_text(encoding="utf-8").count("ValueError: bad subscriber"), 1)


if __name__ == "__main__":
    unittest.main()
