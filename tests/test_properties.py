"""Property-based tests (hypothesis) over the pure parsers and formats: whatever the input,
a parser answers or raises the one error its caller catches, and a round trip through a
format gives the same data back. Skipped where hypothesis is not installed (it is not a
build dependency); `pip install hypothesis` to run them."""
from __future__ import annotations

import json
import struct
import unittest

try:
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st
except ImportError:  # pragma: no cover - the suite still runs without hypothesis
    raise unittest.SkipTest("hypothesis is not installed") from None

from daykeeper import envimport, hookrules, journal, journal_layout, markdown, routines, security, siteicon
from daykeeper.bookmarks import Bookmarks
from daykeeper.integrations import Integrations
from daykeeper.notes import Notes
from daykeeper.settings import Links
from daykeeper.vault import BadFile, Entry, Field, Sealer, backups_to_drop, read_header

SLOW = settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
# the parsers below take any text; the hand-edited settings strategy stays off lone surrogates
# (a file on disk cannot carry one), the vault round trips take them as well (M3 of the audit)
TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=40)
ANY_TEXT = st.text(max_size=40)
JSONISH = st.recursive(st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | TEXT,
                       lambda inner: st.lists(inner, max_size=4) | st.dictionaries(TEXT, inner, max_size=4), max_leaves=12)


class ParsersNeverCrash(unittest.TestCase):
    """Text from a file, a hook payload or a hand-edited settings.json: the parser gives an
    answer for any input, never an exception that would stop the import, the hook or the
    Journal tab."""

    @SLOW
    @given(st.text(max_size=400))
    def test_env_file(self, text):
        pairs = envimport.parse_env(text)
        for key, value in pairs:
            self.assertRegex(key, r"^[A-Za-z_][A-Za-z0-9_]*$")
            self.assertIsInstance(value, str)
        envimport.describe_plan(envimport.plan_import(pairs))

    @SLOW
    @given(st.text(max_size=600))
    def test_markdown(self, text):
        blocks = markdown.parse(text)
        for b in blocks:
            self.assertIn(b.kind, ("heading", "para", "item", "task", "quote", "code", "table", "rule", "blank"))
            if b.kind == "table":
                markdown.table_lines(b.raw)
        if blocks:
            self.assertNotEqual(blocks[-1].kind, "blank")

    @SLOW
    @given(st.text(max_size=300), st.integers(-5, 50))
    def test_toggle_task_keeps_other_lines(self, text, line):
        out = markdown.toggle_task(text, line)
        self.assertEqual(len(out.splitlines()), len(text.splitlines()))

    @SLOW
    @given(JSONISH)
    def test_hook_payload(self, payload):
        hits = hookrules.match(payload if isinstance(payload, dict) else {"tool_input": payload}, hookrules.HookConfig(), Links())
        for h in hits:
            self.assertTrue(h.status and h.item)

    @SLOW
    @given(JSONISH)
    def test_settings_json_shapes(self, raw):
        settings = {"hook": raw, "journal": raw, "env_import": raw}
        hookrules.HookConfig.from_settings(settings)
        layout = journal_layout.Layout.from_settings(settings)
        self.assertTrue(layout.columns or layout.kinds)
        envimport.own_rules(settings)
        Links.from_settings(settings if isinstance(raw, dict) else {})
        security.SecurityPrefs.from_dict(raw)
        Integrations.from_dict(raw)
        routines.Routines.from_list(raw if isinstance(raw, list) else [raw])
        Bookmarks.from_dict(raw)
        Notes.from_list(raw if isinstance(raw, list) else [raw])
        journal.Journal.from_list(raw if isinstance(raw, list) else [raw])

    @SLOW
    @given(st.lists(st.text(max_size=120), max_size=8))
    def test_inbox_lines(self, lines):
        import tempfile
        from pathlib import Path

        path = Path(tempfile.mkdtemp()) / "inbox.jsonl"
        path.write_text("\n".join(lines), encoding="utf-8", errors="replace")
        for item in journal.read_inbox(path):
            self.assertRegex(item.date, r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(item.item)

    @SLOW
    @given(st.binary(max_size=600))
    def test_shortcut_bytes(self, data):
        try:
            routines.read_shortcut(data)
        except (ValueError, IndexError, struct.error):
            pass  # what resolve_shortcut catches

    @SLOW
    @given(st.binary(max_size=2000))
    def test_icon_bytes(self, data):
        for prefix in (b"", b"\x89PNG\r\n\x1a\n", b"\x00\x00\x01\x00"):
            try:
                siteicon.decode(prefix + data)
            except (ValueError, struct.error, IndexError, KeyError, ZeroDivisionError):
                pass  # siteicon.fetch catches Exception around this

    @SLOW
    @given(st.binary(max_size=200))
    def test_vault_header(self, blob):
        try:
            read_header(blob)
        except BadFile:
            pass


class RoundTrips(unittest.TestCase):
    @SLOW
    @given(ANY_TEXT, ANY_TEXT, st.booleans())
    def test_field_seal_unseal(self, key, value, secret):
        sealer = Sealer()
        f = Field(key, value, secret, sealer)
        self.assertEqual(f.value, value)
        back = Field.from_dict(f.to_dict(), sealer)
        self.assertEqual((back.key, back.value, back.secret), (key, value, secret))
        plain = Field.from_dict(f.to_dict(plain=True), Sealer())
        self.assertEqual(plain.value, value)
        if secret and len(value) >= 4:  # a short value can occur inside the base64 by chance
            self.assertNotIn(value, f.to_dict()["sealed"])

    @SLOW
    @given(st.lists(st.tuples(ANY_TEXT, ANY_TEXT, st.booleans()), max_size=5), ANY_TEXT, ANY_TEXT, st.sampled_from(list(("login", "token", "key", "card", "note", "other"))))
    def test_entry_round_trip(self, fields, name, note, kind):
        sealer = Sealer()
        e = Entry(name=name, fields=[Field(k, v, s, sealer) for k, v, s in fields], note=note, kind=kind)
        text = json.dumps(e.to_dict(), ensure_ascii=False).encode("utf-8", "surrogatepass").decode("utf-8", "surrogatepass")  # as the vault file carries it
        back = Entry.from_dict(json.loads(text), sealer)
        self.assertEqual(back.to_dict(plain=True), e.to_dict(plain=True))

    @SLOW
    @given(st.text(alphabet="ABCD-0123456789/browse?#. ", max_size=30))
    def test_normalize_item_is_idempotent(self, text):
        once = journal.normalize_item(text)
        self.assertEqual(journal.normalize_item(once), once)

    @SLOW
    @given(st.text(alphabet="!0123456789 ", max_size=12))
    def test_normalize_mr_is_idempotent(self, text):
        once = journal.normalize_mr(text)
        self.assertEqual(journal.normalize_mr(once), once)

    @SLOW
    @given(st.lists(st.integers(0, 40 * 86400), min_size=0, max_size=60), st.integers(0, 10 * 86400))
    def test_backups_to_drop_keeps_the_newest(self, ages, skew):
        import time
        from pathlib import Path

        now = 1_800_000_000.0 + skew
        stamps = sorted({now - a for a in ages})
        copies = [Path(f"backup/v-{time.strftime('%Y%m%d-%H%M%S', time.localtime(t))}.pkv") for t in stamps]
        copies = list(dict.fromkeys(copies))
        dropped = set(backups_to_drop(copies, now))
        for c in copies[-20:]:
            self.assertNotIn(c, dropped)
        self.assertTrue(dropped.issubset(copies))

    @SLOW
    @given(st.integers(0, 30), st.floats(0, 600, allow_nan=False))
    def test_attempts_only_grow_until_ok(self, fails, now):
        a = security.Attempts()
        locked = False
        for _ in range(fails):
            locked = a.fail(now) or locked
        self.assertEqual(locked, fails >= security.LOCK_AFTER)
        self.assertLessEqual(a.wait(now), security.MAX_PAUSE + 1e-6)  # now + 60 - now rounds a hair above 60
        a.ok()
        self.assertEqual(a.wait(now), 0)


if __name__ == "__main__":
    unittest.main()
