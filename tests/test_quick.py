"""The quick window's core: the search index (never a secret value), the ranking, and the
quick journal entry."""
import unittest
from pathlib import Path
from unittest import mock

from daykeeper import journal_layout as jl
from daykeeper import quick
from daykeeper.journal import Journal
from daykeeper.locale_ru import RU
from daykeeper.quick import Item, index, parse_entry, rank, write_entry
from daykeeper.vault import Field, Sealer, Vault
from tests.helpers import reset_globals

TODAY = "2026-10-17"


def make_vault() -> Vault:
    vault = Vault(Path("unused.pkv"), b"\x00" * 32, b"\x00" * 16, (1, 8, 1), [])
    mail = vault.add_entry("Mail service", kind="login")
    mail.fields = [Field("login", "anna@example.com"), Field("password", "hunter-swordfish", secret=True), Field("site", "https://mail.example.com")]
    vault.add_group("Work")
    token = vault.add_entry("Build token", kind="token", group="Work")
    token.fields = [Field("token", "glpat-zebra-quokka", secret=True), Field("service / URL", "ci.example.com"), Field("2FA", "JBSWY3DPEHPK3PXP", secret=True)]
    vault.add_entry("Плановый пароль", kind="other")
    vault._normalize()  # binds the fields: the secret values are sealed from here on
    vault.bookmarks.add("Docs", "https://docs.example.com/start", group="Reading", entry_id=mail.id)
    vault.notes.add(TODAY, title="Mailbox cleanup")
    vault.notes.add("2026-08-01", title="Mailbox from summer")  # older than 30 days
    vault.routines.add(name="Mail backup", target="backup.cmd")
    return vault


class Template:
    def __init__(self, tid: str, name: str):
        self.id, self.name = tid, name


class IndexTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.vault = make_vault()

    def tearDown(self):
        reset_globals()

    def build(self) -> list[Item]:
        return index(self.vault, [Template("t1", "Mail reply"), {"id": "t2", "name": "Standup"}], jl.preset("qa"), today=TODAY)

    def test_no_secret_value_and_nothing_unsealed(self):
        with mock.patch.object(Sealer, "open", side_effect=AssertionError("a sealed value was opened")):
            items = self.build()
            for secret in ("hunter", "swordfish", "zebra", "JBSWY3"):
                self.assertEqual(rank(items, secret, []), [], secret)
        text = repr([(i.title, i.subtitle, i.keywords) for i in items]).lower()
        for secret in ("hunter", "swordfish", "zebra", "jbswy3"):
            self.assertNotIn(secret, text)

    def test_open_fields_are_found(self):
        items = self.build()
        self.assertEqual(rank(items, "anna@", [])[0].title, "Mail service")
        self.assertEqual(rank(items, "ci.example", [])[0].title, "Build token")
        self.assertEqual(rank(items, "work", [])[0].title, "Build token")  # the list

    def test_every_source_and_its_actions(self):
        by = {i.key: i for i in self.build()}
        mail = next(i for i in by.values() if i.title == "Mail service")
        self.assertEqual(mail.actions, ["copy_password", "copy_login", "open_site", "show"])
        token = next(i for i in by.values() if i.title == "Build token")
        self.assertEqual(token.actions, ["copy_password", "copy_code", "open_site", "show"])
        bookmark = next(i for i in by.values() if i.kind == "bookmark")
        self.assertEqual((bookmark.actions[0], "docs.example.com" in bookmark.keywords), ("open", True))
        self.assertIn("copy_password", bookmark.actions)  # the tied entry
        notes = [i.title for i in by.values() if i.kind == "note"]
        self.assertEqual(notes, ["Mailbox cleanup"])  # the summer note is too old
        self.assertEqual(next(i for i in by.values() if i.kind == "routine").actions, ["run", "show"])
        self.assertEqual(sorted(i.title for i in by.values() if i.kind == "template"), ["Mail reply", "Standup"])
        self.assertEqual({i.id for i in by.values() if i.kind == "command"}, {"lock", "digest", "today", "clip", "settings"})

    def test_commands(self):
        items = self.build()
        self.assertEqual(rank(items, "/lo", [])[0].id, "lock")
        self.assertEqual(rank(items, "/", [], limit=10)[0].kind, "command")
        self.assertEqual(rank(items, "review", [])[0].id, "today")  # a journal word finds the journal
        self.assertEqual(rank(items, "", []), [i for i in items if i.kind == "command"])

    def test_cyrillic_and_case(self):
        items = self.build()
        self.assertEqual(rank(items, "ПЛАН", [])[0].title, "Плановый пароль")
        self.assertEqual(rank(items, "пароль", [])[0].title, "Плановый пароль")

    def test_labels_are_translated(self):
        keys = list(quick.ACTION_LABELS.values()) + [h for _t, h in quick.COMMANDS.values()] + list(quick.KIND_LABELS.values())
        keys += list(quick.VERBS) + list(quick.DAY_WORDS)
        self.assertEqual([k for k in keys if k not in RU], [])


def item(title: str, iid: str = "", keywords: tuple[str, ...] = ()) -> Item:
    return Item("entry", iid or title, title, "", [title.lower(), *keywords], ["show"])


class RankTest(unittest.TestCase):
    def setUp(self):
        self.items = [item("Wiki mail"), item("Mailbox"), item("Gmail"), item("Admin", keywords=("mail.example.com",)), item("Other")]

    def test_title_prefix_then_word_prefix_then_substring(self):
        titles = [i.title for i in rank(self.items, "mail", [])]
        self.assertEqual(titles, ["Mailbox", "Wiki mail", "Admin", "Gmail"])

    def test_recent_is_lifted(self):
        titles = [i.title for i in rank(self.items, "mail", ["entry:Gmail"])]
        self.assertEqual(titles, ["Mailbox", "Gmail", "Wiki mail", "Admin"])  # above the word prefixes, not above the title prefix
        titles = [i.title for i in rank(self.items, "mail", ["Admin", "Wiki mail"])]  # bare ids, the newest first
        self.assertEqual(titles, ["Admin", "Wiki mail", "Mailbox", "Gmail"])

    def test_empty_query_and_limit(self):
        self.assertEqual([i.title for i in rank(self.items, "  ", ["entry:Other", "entry:Gmail"])], ["Other", "Gmail"])
        self.assertEqual(len(rank([item(f"Mail {n}") for n in range(20)], "mail", [])), 8)
        self.assertEqual(rank(self.items, "zzz", []), [])

    def test_every_word_must_match(self):
        self.assertEqual([i.title for i in rank(self.items, "wiki ma", [])], ["Wiki mail"])
        self.assertEqual(rank(self.items, "wiki box", []), [])


class ParseEntryTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.qa = jl.preset("qa")

    def tearDown(self):
        reset_globals()

    def parse(self, text: str, layout=None):
        found = parse_entry(text, layout or self.qa, TODAY)
        return None if found is None else (found.section, found.status, found.items, found.date)

    def test_status_word_first(self):
        self.assertEqual(self.parse("pass DEMO-12 DEMO-13"), ("column", "ok", ["DEMO-12", "DEMO-13"], TODAY))
        self.assertEqual(self.parse("bug DEMO-41"), ("column", "bugs", ["DEMO-41"], TODAY))
        self.assertEqual(self.parse("mr-open !7"), ("activity", "mr_open", ["!7"], TODAY))
        self.assertEqual(self.parse("fail DEMO-2 DEMO-2"), ("column", "failed", ["DEMO-2"], TODAY))

    def test_verbs_in_both_languages(self):
        self.assertEqual(self.parse("DEMO-12 verified"), ("column", "ok", ["DEMO-12"], TODAY))
        self.assertEqual(self.parse("DEMO-12 проверил"), ("column", "ok", ["DEMO-12"], TODAY))
        self.assertEqual(self.parse("DEMO-12 проверила"), ("column", "ok", ["DEMO-12"], TODAY))
        self.assertEqual(self.parse("DEMO-41 завёл"), ("column", "bugs", ["DEMO-41"], TODAY))
        self.assertEqual(self.parse("DEMO-41 завел"), ("column", "bugs", ["DEMO-41"], TODAY))
        self.assertEqual(self.parse("!7 открыл"), ("activity", "mr_open", ["!7"], TODAY))
        self.assertEqual(self.parse("!8 ревью"), ("activity", "review", ["!8"], TODAY))
        self.assertEqual(self.parse("merged !9"), ("activity", "mr_merged", ["!9"], TODAY))

    def test_note(self):
        self.assertEqual(self.parse("note nightly run 7 triage"), ("activity", "note", ["nightly run 7 triage"], TODAY))
        self.assertEqual(self.parse("note разбор ночника вчера"), ("activity", "note", ["разбор ночника"], "2026-10-16"))

    def test_mixed_case(self):
        self.assertEqual(self.parse("PASS demo-12"), ("column", "ok", ["DEMO-12"], TODAY))
        self.assertEqual(self.parse("Demo-12 Verified"), ("column", "ok", ["DEMO-12"], TODAY))
        self.assertEqual(self.parse("DEMO-12 ПРОВЕРИЛ"), ("column", "ok", ["DEMO-12"], TODAY))

    def test_link_instead_of_key(self):
        self.assertEqual(self.parse("https://tracker.example.com/browse/DEMO-12 verified"), ("column", "ok", ["DEMO-12"], TODAY))
        self.assertEqual(self.parse("review https://git.example.com/group/repo/-/merge_requests/7"),
                         ("activity", "review", ["https://git.example.com/group/repo/-/merge_requests/7"], TODAY))

    def test_yesterday(self):
        self.assertEqual(self.parse("pass DEMO-12 yesterday"), ("column", "ok", ["DEMO-12"], "2026-10-16"))
        self.assertEqual(self.parse("вчера DEMO-12 проверил"), ("column", "ok", ["DEMO-12"], "2026-10-16"))
        self.assertEqual(self.parse("DEMO-12 verified Yesterday")[3], "2026-10-16")

    def test_custom_layout_with_renamed_columns(self):
        layout = jl.Layout("custom", [jl.Column("ok", "Green", command="green"), jl.Column("checks", "Checks", command="check")],
                           [jl.Kind("calls", "Calls", command="call"), jl.Kind("misc", "Misc", command="misc", role=jl.ROLE_NOTE)])
        self.assertEqual(self.parse("green DEMO-1", layout), ("column", "ok", ["DEMO-1"], TODAY))
        self.assertEqual(self.parse("DEMO-1 verified", layout), ("column", "ok", ["DEMO-1"], TODAY))  # the old alias of the column id
        self.assertEqual(self.parse("check DEMO-2", layout), ("column", "checks", ["DEMO-2"], TODAY))
        self.assertEqual(self.parse("call planning with the team", layout), ("activity", "calls", ["planning with the team"], TODAY))
        self.assertEqual(self.parse("misc anything at all", layout), ("activity", "misc", ["anything at all"], TODAY))
        self.assertIsNone(self.parse("DEMO-3 opened", layout))  # no MR kind in this layout
        self.assertIsNone(self.parse("note text", layout))  # not a word of this layout

    def test_no_journal_meaning(self):
        for text in ("", "   ", "DEMO-12", "mail service", "pass manager", "DEMO-1 verified failed", "verified", "вчера", "pass", "DEMO-12 verified the fix"):
            self.assertIsNone(self.parse(text), text)

    def test_preview_label(self):
        found = parse_entry("pass DEMO-12 DEMO-13", self.qa, TODAY)
        self.assertEqual(found.label, "Tested successfully ← DEMO-12, DEMO-13 · today")
        self.assertTrue(parse_entry("pass DEMO-1 yesterday", self.qa, TODAY).label.endswith("· yesterday"))

    def test_write_entry_files_the_items(self):
        jl.configure(self.qa)
        j = Journal()
        self.assertEqual(write_entry(parse_entry("pass DEMO-12 DEMO-13", self.qa, TODAY), j), 2)
        self.assertEqual(write_entry(parse_entry("!7 opened yesterday", self.qa, TODAY), j), 1)
        self.assertEqual(write_entry(parse_entry("pass DEMO-12", self.qa, TODAY), j), 0)  # already there
        self.assertEqual(j.get(TODAY).columns["ok"], ["DEMO-12", "DEMO-13"])
        self.assertEqual(j.get("2026-10-16").activities, [{"kind": "mr_open", "text": "!7"}])


if __name__ == "__main__":
    unittest.main()
