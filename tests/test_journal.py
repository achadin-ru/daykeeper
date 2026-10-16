import tempfile
import unittest
from pathlib import Path

from daykeeper.journal import (
    Day,
    Journal,
    append_inbox,
    build_report,
    clear_inbox,
    merge_inbox,
    normalize_item,
    normalize_mr,
    parse_activity_lines,
    parse_lines,
    read_inbox,
)
from daykeeper.vault import Vault

from daykeeper import journal
from daykeeper.settings import Links

LINKS = Links(jira_url="https://tracker.example.com", gitlab_url="https://gitlab.example.com", gitlab_project="team/testing")


class LinksCase(unittest.TestCase):
    """The report links are module state: set before each test, not once at import."""

    def setUp(self):
        journal.configure(LINKS)
URL = LINKS.jira_browse
MR_BROWSE = LINKS.mr_browse


class ItemsTest(LinksCase):
    def test_normalize(self):
        self.assertEqual(normalize_item("APP-1234"), "APP-1234")
        self.assertEqual(normalize_item("  app-1234 "), "APP-1234")
        self.assertEqual(normalize_item(URL + "WEB-603"), "WEB-603")
        self.assertEqual(normalize_item(URL + "APP-1?focusedCommentId=5"), "APP-1")
        self.assertEqual(normalize_item("MR review !42"), "MR review !42")  # not an issue — as is
        self.assertEqual(normalize_item("   "), "")

    def test_parse_lines_dedups(self):
        text = f"APP-1\n{URL}APP-1\n\nWEB-2\n  free text \n"
        self.assertEqual(parse_lines(text), ["APP-1", "WEB-2", "free text"])


class ReportTest(LinksCase):
    def test_report_format(self):
        day = Day("2026-10-13", summary="RFT, reviewed MRs", columns={"ok": ["APP-2812", "WEB-629"], "failed": ["APP-2938"]}, plans="RFT, finish waiting.")
        expected = "\n".join(
            [
                "1. RFT, reviewed MRs",
                "Tested successfully:",
                URL + "APP-2812",
                URL + "WEB-629",
                "",
                "Test failed:",
                URL + "APP-2938",
                "",
                "New bug:",
                "",
                "2. RFT, finish waiting.",
                "",
                "3. None",
            ]
        )
        self.assertEqual(build_report(day), expected)

    def test_report_empty_day(self):
        text = build_report(Day("2026-10-13"))
        self.assertTrue(text.startswith("1.\nTested successfully:\n\nTest failed:\n\nNew bug:\n\n2.\n\n3. None"))

    def test_day_add_and_roundtrip(self):
        day = Day("2026-10-13")
        self.assertTrue(day.add("ok", URL + "APP-5"))
        self.assertFalse(day.add("ok", "APP-5"))
        self.assertEqual(day.other, "")
        again = Day.from_dict(day.to_dict())
        self.assertEqual(again.section("ok"), ["APP-5"])
        self.assertEqual(again.other, "")


class ActivityTest(LinksCase):
    def test_normalize_mr(self):
        self.assertEqual(normalize_mr("42"), "!42")
        self.assertEqual(normalize_mr("!42"), "!42")
        self.assertEqual(normalize_mr(MR_BROWSE + "42/diffs"), "!42")
        other = "https://gitlab.example.com/team/product/-/merge_requests/17"
        self.assertEqual(normalize_mr(other), other)  # foreign repository — the link as is

    def test_parse_activity_lines(self):
        text = "MR reviews: !42, 43\nMRs opened: !45\nManual cases: APP-101\nnightly run 1042 triage\nMiscellaneous: call\nMR reviews: !42"
        got = parse_activity_lines(text)
        self.assertEqual(got, [
            {"kind": "review", "text": "!42"}, {"kind": "review", "text": "!43"},
            {"kind": "mr_open", "text": "!45"},
            {"kind": "manual", "text": "APP-101"},
            {"kind": "note", "text": "nightly run 1042 triage"},
            {"kind": "note", "text": "call"},
        ])
        day = Day("2026-10-13", activities=got)
        self.assertEqual(day.activity_lines().splitlines()[0], "MR reviews: !42, !43")
        self.assertEqual(parse_activity_lines(day.activity_lines()), got)  # round trip editor → data → editor

    def test_auto_summary_and_report(self):
        day = Day("2026-10-13", columns={"ok": ["APP-1"], "bugs": ["APP-9"]})
        day.add_activity("review", "42")
        self.assertFalse(day.add_activity("review", "!42"))
        day.add_activity("mr_merged", "!44")
        day.add_activity("auto", "APP-101")
        day.add_activity("note", "nightly triage")
        day.add_activity("manual", "APP-101")
        day.add_activity("mr_open", "https://gitlab.example.com/team/product/-/merge_requests/17")
        # opened and merged MRs are listed, not named in "1."; both kinds of tests are one phrase; a note goes into "1."
        self.assertEqual(day.auto_summary(), "tested tasks, reviewed MRs, new manual/autotests, nightly triage")
        text = build_report(day)
        lines = text.splitlines()
        self.assertEqual(lines[0], "1. tested tasks, reviewed MRs, new manual/autotests, nightly triage")
        self.assertEqual(lines[1:5], ["MR reviews: !42", "MRs opened: product!17", "MRs merged: !44", "New tests: APP-101"])
        self.assertEqual(lines[5], "Tested successfully:")
        day.summary = "own line"
        self.assertTrue(build_report(day).startswith("1. own line\n"))
        self.assertEqual(Day("2026-10-13", columns={"bugs": ["APP-9"]}).auto_summary(), "bugs filed")
        self.assertEqual(Day("2026-10-13").auto_summary(), "")

    def test_activity_roundtrip_dict(self):
        day = Day("2026-10-13")
        day.add_activity("mr_open", "45")
        again = Day.from_dict(day.to_dict())
        self.assertEqual(again.activities, [{"kind": "mr_open", "text": "!45"}])


class InboxTest(LinksCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.inbox = Path(self.tmp.name) / "inbox.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_read_merge_clear(self):
        self.assertEqual(append_inbox(self.inbox, "pass", ["APP-1", URL + "APP-2"], date="2026-10-12"), 2)
        self.assertEqual(append_inbox(self.inbox, "fail", ["APP-3"], date="2026-10-12"), 1)
        self.assertEqual(append_inbox(self.inbox, "bug", ["APP-9"], date="2026-10-13", note="duplicate"), 1)
        self.assertEqual(append_inbox(self.inbox, "pass", ["   "]), 0)
        self.assertEqual(append_inbox(self.inbox, "review", ["42", MR_BROWSE + "43"], date="2026-10-12"), 2)
        self.assertEqual(append_inbox(self.inbox, "mr-merged", ["!44"], date="2026-10-12"), 1)
        self.assertEqual(append_inbox(self.inbox, "note", ["nightly triage"], date="2026-10-12"), 1)
        with self.assertRaises(ValueError):
            append_inbox(self.inbox, "weird", ["APP-1"])
        self.inbox.write_text(self.inbox.read_text(encoding="utf-8") + "broken line\n", encoding="utf-8")

        items = read_inbox(self.inbox)
        self.assertEqual([(i.date, i.section, i.item, i.kind) for i in items], [
            ("2026-10-12", "ok", "APP-1", ""), ("2026-10-12", "ok", "APP-2", ""), ("2026-10-12", "failed", "APP-3", ""), ("2026-10-13", "bugs", "APP-9", ""),
            ("2026-10-12", "activity", "!42", "review"), ("2026-10-12", "activity", "!43", "review"),
            ("2026-10-12", "activity", "!44", "mr_merged"), ("2026-10-12", "activity", "nightly triage", "note"),
        ])
        journal = Journal([Day("2026-10-12", columns={"ok": ["APP-1"]})])
        self.assertEqual(merge_inbox(journal, items), 7)  # APP-1 was already there
        self.assertEqual(journal.days["2026-10-12"].activity_lines(), "MR reviews: !42, !43\nMRs merged: !44\nMiscellaneous: nightly triage")
        self.assertEqual(journal.days["2026-10-12"].section("ok"), ["APP-1", "APP-2"])
        self.assertEqual(journal.days["2026-10-12"].section("failed"), ["APP-3"])
        self.assertEqual(journal.days["2026-10-13"].section("bugs"), ["APP-9"])
        clear_inbox(self.inbox)
        self.assertFalse(self.inbox.exists())
        self.assertEqual(read_inbox(self.inbox), [])

    def test_journal_persists_in_vault(self):
        path = Path(self.tmp.name) / "v.pkv"
        v = Vault.create(path, "master-pass-1")
        day = v.journal.get("2026-10-13", create=True)
        day.add("failed", "APP-2938")
        day.plans = "RFT"
        v.save()
        v2 = Vault.open(path, "master-pass-1")
        self.assertEqual(v2.journal.dates(), ["2026-10-13"])
        self.assertEqual(v2.journal.days["2026-10-13"].section("failed"), ["APP-2938"])
        self.assertEqual(v2.journal.days["2026-10-13"].plans, "RFT")



class WorkOnlyTest(unittest.TestCase):
    def test_items_from_a_personal_repository_are_dropped(self):
        from daykeeper.journal import InboxItem, Journal, is_work, merge_inbox

        work = {"gitlab.example.com", "company.atlassian.net"}
        self.assertTrue(is_work("", work), "no repository: counts as work")
        self.assertTrue(is_work("gitlab.example.com", work))
        self.assertFalse(is_work("github.com", work))
        self.assertTrue(is_work("github.com", set()), "no hosts known: everything is work")
        journal = Journal()
        items = [InboxItem("2026-10-15", "activity", "nightly triage", "note", "", "gitlab.example.com"),
                 InboxItem("2026-10-15", "activity", "my tool refactor", "note", "", "github.com"),
                 InboxItem("2026-10-15", "ok", "APP-1", "", "", "")]
        self.assertEqual(merge_inbox(journal, items, work), 2)
        day = journal.get("2026-10-15")
        self.assertEqual([a["text"] for a in day.activities], ["nightly triage"])
        self.assertEqual(day.section("ok"), ["APP-1"])

    def test_the_origin_goes_through_the_inbox(self):
        import tempfile
        from pathlib import Path

        from daykeeper.journal import append_inbox, read_inbox

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inbox.jsonl"
            append_inbox(path, "note", ["x"], origin="github.com")
            append_inbox(path, "pass", ["APP-2"], origin="")
            self.assertEqual([i.origin for i in read_inbox(path)], ["github.com", ""])


if __name__ == "__main__":
    unittest.main()
