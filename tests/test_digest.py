"""The morning digest: sections in their order, the plans of the right day, the line limit,
MR links, keys that run out, the night, the text to copy and the once-a-day rule."""
import unittest
from datetime import datetime
from types import SimpleNamespace

from daykeeper import digest, journal_layout
from daykeeper.digest import Line, Section, as_text, build, mark_shown, should_show, switched
from daykeeper.gitlab import Event
from daykeeper.integrations import Integrations
from daykeeper.journal import Day, Journal
from daykeeper.locale_ru import RU
from daykeeper.notes import NoteItem, Notes
from daykeeper.routines import Routines
from daykeeper.vault import Entry, Field
from tests.helpers import reset_globals

TODAY = "2026-10-17"
NOW = datetime(2026, 10, 17, 9, 0).timestamp()


def local(day: int, hour: int, minute: int = 0) -> float:
    return datetime(2026, 10, day, hour, minute).timestamp()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat()


def make_vault(**kw) -> SimpleNamespace:
    v = SimpleNamespace(entries=[], journal=Journal(), notes=Notes(), integrations=Integrations(), routines=Routines())
    for k, value in kw.items():
        setattr(v, k, value)
    return v


def titles(sections: list[Section]) -> list[str]:
    return [s.title for s in sections]


class DigestTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        journal_layout.configure(journal_layout.preset("qa"))

    def tearDown(self):
        reset_globals()
        journal_layout.configure(journal_layout.preset("qa"))

    def test_empty_vault_gives_nothing(self):
        self.assertEqual(build(make_vault(), TODAY, now=NOW), [])

    def test_plans_of_yesterday_and_of_today(self):
        v = make_vault(journal=Journal([Day("2026-10-16", plans="• DEMO-3 retest\n\n- nightly triage")]))
        [plans] = build(v, TODAY, now=NOW)
        self.assertEqual(plans.title, digest.PLANS)
        self.assertEqual([x.text for x in plans.lines], ["DEMO-3 retest", "nightly triage"])
        self.assertEqual({x.tab for x in plans.lines}, {"journal"})
        self.assertEqual(plans.count, 2)

        v.journal.get(TODAY, create=True).plans = "write the cases"
        [plans] = build(v, TODAY, now=NOW)
        self.assertEqual([x.text for x in plans.lines], ["write the cases"])

    def test_plans_reach_back_over_a_weekend_but_not_further(self):
        v = make_vault(journal=Journal([Day("2026-10-16", plans="Friday's plan")]))
        self.assertEqual([x.text for x in build(v, "2026-10-19", now=NOW)[0].lines], ["Friday's plan"])
        self.assertEqual(build(v, "2026-10-30", now=NOW), [])
        self.assertEqual(build(v, TODAY, now=NOW, yesterday_plans=False), [])

    def test_plans_hidden_by_the_layout_are_left_out(self):
        layout = journal_layout.preset("qa")
        layout.show_plans = False
        journal_layout.configure(layout)
        v = make_vault(journal=Journal([Day(TODAY, plans="a plan")]))
        self.assertEqual(build(v, TODAY, now=NOW), [])

    def test_open_note_items_with_the_line_limit_and_the_count(self):
        notes = Notes()
        a = notes.add(date="2026-10-16", title="Work")
        a.items = [NoteItem(f"item {n}") for n in range(5)] + [NoteItem("done", done=True)]
        b = notes.add(date="2026-10-15", title="Home")
        b.items = [NoteItem("buy milk"), NoteItem("call the bank")]
        notes.add(date="2026-10-15", title="All done").items = [NoteItem("x", done=True)]
        [s] = build(make_vault(notes=notes), TODAY, now=NOW, max_lines=3)
        self.assertEqual(s.title, digest.NOTES)
        self.assertEqual(s.count, 7)
        self.assertEqual([x.text for x in s.lines], ["item 0", "item 1", "item 2"])
        self.assertEqual(s.more, 4)
        self.assertEqual(s.tab, "notes")
        self.assertEqual(s.detail, "2 cards · 7 items")

    def test_mrs_waiting_for_my_review_link_to_the_mr(self):
        integ = Integrations()
        integ.gitlab_state = {
            "group/repo!7": {"role": "reviewer", "reviewer": True, "title": "Fix the sync", "author": "alice", "url": "https://gitlab.example.com/group/repo/-/merge_requests/7"},
            "group/repo!8": {"role": "author", "reviewer": True, "title": "Mine, reviewing myself", "author": "me", "url": "u8"},
            "group/repo!9": {"role": "author", "title": "Mine", "author": "me", "url": "u9"},
            "group/other!3": {"role": "reviewer", "reviewer": True, "title": "No link", "author": "bob", "url": ""},
        }
        [s] = build(make_vault(integrations=integ), TODAY, now=NOW)
        self.assertEqual(s.title, digest.REVIEWS)
        self.assertEqual(s.count, 2)
        self.assertEqual([x.text for x in s.lines], ["other!3 No link — @bob", "repo!7 Fix the sync — @alice"])
        self.assertEqual(s.lines[1].url, "https://gitlab.example.com/group/repo/-/merge_requests/7")
        self.assertEqual((s.lines[0].url, s.lines[0].tab), ("", "integrations"))
        self.assertEqual([x.ref for x in s.lines], ["group/other!3", "group/repo!7"], "the MR each line is about")

    def test_keys_that_run_out_this_week(self):
        soon = Entry(name="GitLab PAT", fields=[Field("valid until", "2026-10-20")])
        later = Entry(name="VPN", fields=[Field("valid until", "2026-12-31")])
        gone = Entry(name="Old key", fields=[Field("expires", "2026-10-15")])
        put_off = Entry(name="Put off", fields=[Field("expires", "2026-10-18")])
        v = make_vault(entries=[soon, later, gone, put_off])
        [s] = build(v, TODAY, now=NOW, seen={f"{put_off.id}:expires": "2026-10-20"})
        self.assertEqual(s.title, digest.EXPIRING)
        self.assertEqual([x.text for x in s.lines], ["Old key · expires — expired 2 d ago", "GitLab PAT · valid until — in 3 d"])
        self.assertEqual([x.ok for x in s.lines], [False, None])
        self.assertEqual({x.tab for x in s.lines}, {"vault"})
        self.assertEqual([x.ref for x in s.lines], [gone.id, soon.id], "the entry an \"Open entry\" button targets")
        [s] = build(v, TODAY, now=NOW, expiry_days=1, seen={})
        self.assertEqual([x.text for x in s.lines], ["Old key · expires — expired 2 d ago", "Put off · expires — in 1 d"])

    def test_the_night_failed_and_green_pipelines_and_a_failed_routine(self):
        integ = Integrations()
        integ.gitlab_pipelines = {
            "group/repo@main:schedule": {"name": "Nightly", "status": "failed", "url": "https://gitlab.example.com/p/1", "finished_at": iso(local(17, 3, 12))},
            "group/repo@us": {"project": "group/repo", "ref": "us", "status": "success", "url": "u2", "finished_at": iso(local(17, 4, 40))},
            "group/repo@old": {"name": "Last week", "status": "failed", "finished_at": iso(local(10, 3))},
            "group/repo@noon": {"name": "Yesterday noon", "status": "failed", "finished_at": iso(local(16, 12))},
            "group/repo@err": {"name": "Broken row", "error": "HTTP 404"},
            "group/repo@run": {"name": "Running", "status": "running", "finished_at": iso(local(17, 2))},
        }
        routines = Routines()
        backup = routines.add(name="backup")
        backup.mark_started(local(17, 2), 1)
        routines.add(name="fine").mark_started(local(17, 2), 0)
        routines.add(name="running").mark_started(local(17, 2), None)
        routines.add(name="long ago").mark_started(local(15, 2), 2)
        [s] = build(make_vault(integrations=integ, routines=routines), TODAY, now=NOW)
        self.assertEqual(s.title, digest.NIGHT)
        self.assertEqual([x.text for x in s.lines], [
            "Nightly — failed at 03:12", "group/repo · us — green at 04:40", "Routine “backup” — code 1 at 02:00",
        ])
        self.assertEqual([x.ok for x in s.lines], [False, True, False])
        self.assertEqual(s.lines[0].url, "https://gitlab.example.com/p/1")

    def test_unread_notifications_of_the_night(self):
        integ = Integrations()
        integ.feed = [
            Event("approved", "group/repo!7", "T", "https://gitlab.example.com/7", "{who} approved", {"who": "alice"}, ts=local(17, 1)),
            Event("approved", "group/repo!8", "T", "u", "read already", ts=local(17, 2), read=True),
            Event("merged", "group/repo!9", "T", "u", "before the night", ts=local(16, 10)),
            Event("watch_failed", "group/repo@main", "N", "u", "The pipeline failed on {name}", {"name": "N"}, ts=local(17, 3)),
        ]
        [s] = build(make_vault(integrations=integ), TODAY, now=NOW)
        self.assertEqual(s.title, digest.FEED)
        self.assertEqual([(x.text, x.url) for x in s.lines], [("repo!7 — alice approved", "https://gitlab.example.com/7")])

    def test_sections_come_in_the_fixed_order(self):
        notes = Notes()
        notes.add().items = [NoteItem("one")]
        routines = Routines()
        routines.add(name="r").mark_started(local(17, 2), 3)
        integ = Integrations()
        integ.gitlab_state = {"g/r!1": {"role": "reviewer", "reviewer": True, "title": "t", "url": "u"}}
        integ.feed = [Event("approved", "g/r!2", "t", "u", "x", ts=local(17, 1))]
        v = make_vault(
            journal=Journal([Day(TODAY, plans="plan")]), notes=notes, integrations=integ, routines=routines,
            entries=[Entry(name="k", fields=[Field("expires", "2026-10-18")])],
        )
        self.assertEqual(titles(build(v, TODAY, now=NOW)), list(digest.SECTION_TITLES))

    def test_as_text(self):
        sections = [
            Section(digest.PLANS, [Line("DEMO-3 retest")], 1),
            Section(digest.NIGHT, [Line("Nightly — failed at 03:12", url="https://gitlab.example.com/p/1", ok=False), Line("ok one", ok=True)], 2),
            Section(digest.NOTES, [Line("a"), Line("b")], 5, detail="1 cards · 5 items"),
        ]
        self.assertEqual(as_text(sections), "\n".join([
            "Plans for today",
            "  • DEMO-3 retest",
            "",
            "Night (2)",
            "  ✗ Nightly — failed at 03:12 https://gitlab.example.com/p/1",
            "  ✓ ok one",
            "",
            "Open note items (5) — 1 cards · 5 items",
            "  • a",
            "  • b",
            "  …and 3 more",
        ]))
        self.assertEqual(as_text([]), "")

    def test_titles_are_translated(self):
        self.assertEqual([t for t in digest.SECTION_TITLES if t not in RU], [])


class OnceADayTest(unittest.TestCase):
    def test_should_show_once_a_day(self):
        self.assertTrue(should_show(None, TODAY))
        self.assertTrue(should_show({}, TODAY))
        self.assertTrue(should_show({"on_unlock": True, "shown": "2026-10-16"}, TODAY))
        self.assertFalse(should_show({"on_unlock": True, "shown": TODAY}, TODAY))
        self.assertFalse(should_show({"on_unlock": False, "shown": ""}, TODAY))
        self.assertTrue(should_show("broken", TODAY))

    def test_mark_shown_keeps_the_switch(self):
        before = {"on_unlock": False, "shown": "2026-10-16"}
        self.assertEqual(mark_shown(before, TODAY), {"on_unlock": False, "shown": TODAY})
        self.assertEqual(before["shown"], "2026-10-16")  # a new dict
        self.assertEqual(mark_shown(None, TODAY), {"on_unlock": True, "shown": TODAY})
        self.assertFalse(should_show(mark_shown({}, TODAY), TODAY))

    def test_switched(self):
        self.assertEqual(switched({"shown": TODAY}, False), {"shown": TODAY, "on_unlock": False})
        self.assertTrue(should_show(switched({"on_unlock": False}, True), TODAY))


if __name__ == "__main__":
    unittest.main()
