"""Calendar: repeats, reminders, the month grid and the .ics import."""
import tempfile
import unittest
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from daykeeper import journal_layout as jl
from daykeeper.calendar_model import MAX_REMIND_MIN, CalEvent, Calendar, month_cells, read_ics
from daykeeper.gitlab import Event
from daykeeper.routines import Schedule
from daykeeper.vault import Vault
from tests.helpers import reset_globals


def dates_of(cal: Calendar, days: list[str]) -> list[str]:
    return [d for d in days if cal.on(d)]


class RepeatTest(unittest.TestCase):
    def test_single_event(self):
        cal = Calendar()
        cal.add("Dentist", "2026-10-20", time="11:00")
        self.assertEqual([e.title for e in cal.on("2026-10-20")], ["Dentist"])
        self.assertEqual(cal.on("2026-10-27"), [])
        self.assertEqual(cal.on("not a date"), [])

    def test_weekly_across_month_and_year_end(self):
        cal = Calendar()
        cal.add("Standup", "2026-12-24", time="09:30", repeat="weekly")
        self.assertTrue(cal.on("2026-12-31"))
        self.assertTrue(cal.on("2027-01-07"))
        self.assertFalse(cal.on("2027-01-06"))
        self.assertFalse(cal.on("2026-12-17"), "nothing before the first occurrence")

    def test_until_is_the_last_day(self):
        cal = Calendar()
        cal.add("Course", "2026-10-05", repeat="weekly", until="2026-10-19")
        self.assertEqual(dates_of(cal, ["2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26"]), ["2026-10-05", "2026-10-12", "2026-10-19"])

    def test_monthly_31st_falls_on_the_last_day(self):
        cal = Calendar()
        cal.add("Rent", "2026-01-31", repeat="monthly")
        self.assertTrue(cal.on("2026-02-28"))
        self.assertTrue(cal.on("2026-04-30"))
        self.assertFalse(cal.on("2026-04-29"))
        self.assertTrue(cal.on("2026-12-31"))
        self.assertTrue(cal.on("2028-02-29"), "a leap February ends on the 29th")
        self.assertFalse(cal.on("2028-02-28"))

    def test_yearly_29_february(self):
        cal = Calendar()
        cal.add("Birthday", "2024-02-29", repeat="yearly")
        self.assertTrue(cal.on("2025-02-28"))
        self.assertFalse(cal.on("2025-03-01"))
        self.assertTrue(cal.on("2028-02-29"))
        self.assertFalse(cal.on("2028-02-28"))

    def test_order_all_day_first(self):
        cal = Calendar()
        cal.add("Demo", "2026-10-20", time="15:00")
        cal.add("Holiday", "2026-10-20")
        cal.add("Standup", "2026-10-20", time="09:30")
        self.assertEqual([e.title for e in cal.on(date(2026, 10, 20))], ["Holiday", "Standup", "Demo"])


class DueTest(unittest.TestCase):
    def test_shown_once_per_occurrence(self):
        cal = Calendar()
        e = cal.add("Standup", "2026-10-19", time="11:00", repeat="weekly", remind_min=5)
        self.assertEqual(cal.due(datetime(2026, 10, 19, 10, 54)), [], "too early")
        self.assertEqual(cal.due(datetime(2026, 10, 19, 10, 55)), [e])
        self.assertEqual(e.notified, "2026-10-19")
        self.assertEqual(cal.due(datetime(2026, 10, 19, 10, 57)), [], "already shown")
        self.assertEqual(cal.due(datetime(2026, 10, 19, 11, 0)), [], "begun")
        self.assertEqual(cal.due(datetime(2026, 10, 26, 10, 58)), [e], "next week again")
        self.assertEqual(e.notified, "2026-10-26")

    def test_no_reminder_without_minutes_or_time(self):
        cal = Calendar()
        cal.add("No reminder", "2026-10-19", time="11:00")
        cal.add("All day", "2026-10-19", remind_min=30)
        self.assertEqual(cal.due(datetime(2026, 10, 19, 10, 59)), [])

    def test_reminder_over_midnight(self):
        cal = Calendar()
        e = cal.add("Early call", "2027-01-01", time="00:10", remind_min=30)
        self.assertEqual(cal.due(datetime(2026, 12, 31, 23, 45)), [e])
        self.assertEqual(e.notified, "2027-01-01")

    def test_huge_minutes_do_not_break_the_other_reminders(self):
        cal = Calendar()
        cal.add("Crafted", "2026-10-19", time="11:00", remind_min=99999999 * 24 * 60)
        broken = cal.add("Broken time", "2026-10-19", time="99:99", remind_min=5)
        ok = cal.add("Standup", "2026-10-19", time="11:00", remind_min=5)
        found = cal.due(datetime(2026, 10, 19, 10, 56))
        self.assertIn(ok, found)
        self.assertNotIn(broken, found)

    def test_minutes_cut_to_a_week(self):
        e = CalEvent.from_dict({"date": "2026-10-19", "time": "11:00", "remind_min": 10**12})
        assert e is not None
        self.assertEqual(e.remind_min, MAX_REMIND_MIN)
        cal = Calendar([e])
        self.assertEqual(cal.due(datetime(2026, 10, 12, 10, 59)), [], "more than a week ahead")
        self.assertEqual(cal.due(datetime(2026, 10, 12, 11, 1)), [e])


class StorageTest(unittest.TestCase):
    def test_round_trip_and_garbage(self):
        cal = Calendar()
        cal.add("Standup", "2026-10-19", time="09:30", repeat="weekly", until="2026-12-31", remind_min=5, url="https://example.com/m", note="room 2")
        back = Calendar.from_list(cal.to_list())
        self.assertEqual(back.events, cal.events)
        rows = [
            "junk", {"title": "no date"}, {"date": "2026-02-30"},
            {"date": "2026-10-19", "time": "9:30", "repeat": "hourly", "remind_min": "x", "until": 5, "notified": None},
        ]
        loaded = Calendar.from_list(rows).events
        self.assertEqual(len(loaded), 1)
        e = loaded[0]
        self.assertEqual((e.time, e.repeat, e.remind_min, e.until, e.notified), ("09:30", "", 0, "", ""))
        self.assertEqual(Calendar.from_list({"not": "a list"}).events, [])

    def test_get_and_remove(self):
        cal = Calendar()
        e = cal.add("A", "2026-10-19")
        self.assertIs(cal.get(e.id), e)
        cal.remove(e)
        self.assertIsNone(cal.get(e.id))


def ics(*events: str) -> str:
    body = "\r\n".join(f"BEGIN:VEVENT\r\n{e.strip()}\r\nEND:VEVENT" for e in events)
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n{body}\r\nEND:VCALENDAR\r\n"


class IcsTest(unittest.TestCase):
    def test_single_event_with_escapes_and_folding(self):
        text = ics(
            "UID:one@example.com\r\n"
            "DTSTART;TZID=\"Europe/Somewhere\":20261020T110000\r\n"
            "DTEND;TZID=\"Europe/Somewhere\":20261020T113000\r\n"
            "SUMMARY:Plan\\, review\\; demo\r\n"
            "DESCRIPTION:First line\\nsecond\r\n"
            "  line folded\r\n"
            "URL:https://example.com/meet\r\n"
            "BEGIN:VALARM\r\nTRIGGER:-PT15M\r\nACTION:DISPLAY\r\nEND:VALARM"
        )
        (e,) = read_ics(text)
        self.assertEqual((e.date, e.time, e.end), ("2026-10-20", "11:00", "11:30"))
        self.assertEqual(e.title, "Plan, review; demo")
        self.assertEqual(e.note, "First line\nsecond line folded")
        self.assertEqual((e.url, e.uid, e.remind_min, e.repeat), ("https://example.com/meet", "one@example.com", 15, ""))

    def test_all_day_and_utc(self):
        day, utc = read_ics(ics(
            "UID:a\r\nDTSTART;VALUE=DATE:20261021\r\nSUMMARY:Holiday",
            "UID:b\r\nDTSTART:20261021T090000Z\r\nSUMMARY:Call",
        ))
        self.assertEqual((day.date, day.time), ("2026-10-21", ""))
        local = datetime(2026, 10, 21, 9, 0, tzinfo=UTC).astimezone()
        self.assertEqual((utc.date, utc.time), (local.date().isoformat(), local.strftime("%H:%M")))

    def test_unsafe_url_dropped_and_broken_skipped(self):
        events = read_ics(ics(
            "UID:x\r\nDTSTART:20261020T100000\r\nSUMMARY:A\r\nURL:file:///C:/x.exe",
            "UID:y\r\nSUMMARY:no start",
            "UID:z\r\nDTSTART:garbage\r\nSUMMARY:bad start",
        ))
        self.assertEqual([(e.title, e.url) for e in events], [("A", "")])
        self.assertEqual(read_ics("not a calendar at all"), [])

    def test_weekly_with_several_days_becomes_several_events(self):
        events = read_ics(ics("UID:s\r\nDTSTART:20261019T093000\r\nSUMMARY:Standup\r\nRRULE:FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=20261104"))
        self.assertEqual(sorted((e.uid, e.date, e.repeat, e.until) for e in events), [
            ("s#MO", "2026-10-19", "weekly", "2026-11-04"),
            ("s#WE", "2026-10-21", "weekly", "2026-11-04"),
        ])
        cal = Calendar(events)
        self.assertTrue(cal.on("2026-11-04"))
        self.assertFalse(cal.on("2026-11-09"))

    def test_weekly_single_day_keeps_uid_and_count(self):
        (e,) = read_ics(ics("UID:w\r\nDTSTART:20261023T150000\r\nSUMMARY:Demo\r\nRRULE:FREQ=WEEKLY;COUNT=3"))
        self.assertEqual((e.uid, e.repeat, e.until), ("w", "weekly", "2026-11-06"))

    def test_daily_becomes_seven_weekly_events(self):
        events = read_ics(ics("UID:d\r\nDTSTART:20261019T080000\r\nSUMMARY:Walk\r\nRRULE:FREQ=DAILY;COUNT=10"))
        self.assertEqual(len(events), 7)
        cal = Calendar(events)
        days = [f"2026-10-{d}" for d in range(18, 31)]
        self.assertEqual(dates_of(cal, days), [f"2026-10-{d}" for d in range(19, 29)])

    def test_daily_with_byday_is_workdays(self):
        events = read_ics(ics("UID:d\r\nDTSTART:20261019T080000\r\nSUMMARY:Sync\r\nRRULE:FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR"))
        self.assertEqual(len(events), 5)
        cal = Calendar(events)
        self.assertTrue(cal.on("2026-10-23"))
        self.assertFalse(cal.on("2026-10-24"))

    def test_other_rules_become_single_events(self):
        events = read_ics(ics(
            "UID:i\r\nDTSTART:20261019T080000\r\nSUMMARY:Biweekly\r\nRRULE:FREQ=WEEKLY;INTERVAL=2",
            "UID:m\r\nDTSTART:20261031T080000\r\nSUMMARY:Last\r\nRRULE:FREQ=MONTHLY",
            "UID:p\r\nDTSTART:20261019T080000\r\nSUMMARY:First Monday\r\nRRULE:FREQ=MONTHLY;BYDAY=1MO",
        ))
        self.assertEqual([e.repeat for e in events], ["", "", ""])

    def test_plain_monthly_and_yearly_map(self):
        m, y = read_ics(ics(
            "UID:m\r\nDTSTART;VALUE=DATE:20261015\r\nSUMMARY:Bills\r\nRRULE:FREQ=MONTHLY;COUNT=3",
            "UID:y\r\nDTSTART;VALUE=DATE:20261015\r\nSUMMARY:Anniversary\r\nRRULE:FREQ=YEARLY",
        ))
        self.assertEqual((m.repeat, m.until), ("monthly", "2026-12-15"))
        self.assertEqual((y.repeat, y.until), ("yearly", ""))

    def test_reimport_updates_instead_of_duplicating(self):
        cal = Calendar()
        first = read_ics(ics("UID:s\r\nDTSTART:20261019T093000\r\nSUMMARY:Standup\r\nRRULE:FREQ=WEEKLY;BYDAY=MO,WE"))
        self.assertEqual(cal.upsert_ics(first), (2, 0))
        monday = next(e for e in cal.events if e.uid == "s#MO")
        monday.notified, monday.remind_min = "2026-10-19", 5
        own = cal.add("Own", "2026-10-19")
        again = read_ics(ics("UID:s\r\nDTSTART:20261019T100000\r\nSUMMARY:Standup (moved)\r\nRRULE:FREQ=WEEKLY;BYDAY=MO"))
        self.assertEqual(cal.upsert_ics(again), (0, 1))
        self.assertEqual(len(cal.events), 2, "Wednesday dropped, own event kept")
        self.assertIn(own, cal.events)
        updated = next(e for e in cal.events if e.uid.startswith("s"))
        self.assertEqual((updated.id, updated.title, updated.time, updated.notified, updated.remind_min), (monday.id, "Standup (moved)", "10:00", "2026-10-19", 5))

    def test_huge_trigger_is_cut_to_a_week_and_fires_with_the_others(self):
        crafted, ok = read_ics(ics(
            "UID:c\r\nDTSTART:20261020T110000\r\nSUMMARY:Crafted\r\nBEGIN:VALARM\r\nTRIGGER:-P99999999D\r\nEND:VALARM",
            "UID:o\r\nDTSTART:20261020T110000\r\nSUMMARY:Ok\r\nBEGIN:VALARM\r\nTRIGGER:-PT10M\r\nEND:VALARM",
        ))
        self.assertEqual((crafted.remind_min, ok.remind_min), (MAX_REMIND_MIN, 10))
        cal = Calendar.from_list([crafted.to_dict(), ok.to_dict()])
        self.assertEqual([e.title for e in cal.due(datetime(2026, 10, 20, 10, 55))], ["Crafted", "Ok"])

    def test_dates_near_the_year_9999_skip_only_that_event(self):
        events = read_ics(ics(
            "UID:y\r\nDTSTART;VALUE=DATE:99900101\r\nSUMMARY:Yearly\r\nRRULE:FREQ=YEARLY;COUNT=20",
            "UID:m\r\nDTSTART;VALUE=DATE:99991115\r\nSUMMARY:Monthly\r\nRRULE:FREQ=MONTHLY;COUNT=5",
            "UID:w\r\nDTSTART;VALUE=DATE:99991230\r\nSUMMARY:Weekly\r\nRRULE:FREQ=WEEKLY;BYDAY=MO,TH",
            "UID:k\r\nDTSTART:20261020T110000\r\nSUMMARY:Kept",
        ))
        self.assertEqual([e.title for e in events], ["Kept"])

    def test_recurrence_override_is_its_own_event(self):
        events = read_ics(ics(
            "UID:r\r\nDTSTART:20261019T093000\r\nSUMMARY:Standup\r\nRRULE:FREQ=WEEKLY",
            "UID:r\r\nRECURRENCE-ID:20261026T093000\r\nDTSTART:20261026T120000\r\nSUMMARY:Standup late\r\nRRULE:FREQ=WEEKLY",
        ))
        self.assertEqual([(e.uid, e.repeat) for e in events], [("r", "weekly"), ("r#20261026T093000", "")])


@dataclass
class FakeExpiry:
    when: date
    entry_name: str


class MonthCellsTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(reset_globals)

    def test_leap_february_monday_first(self):
        vault = Vault.create(Path(self.tmp.name) / "v.pkv", "master-pass-1")
        rows = month_cells(2028, 2, vault, [], "2028-02-29", Calendar())
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(len(r) == 7 for r in rows))
        flat = [c for r in rows for c in r]
        self.assertEqual(flat[0].date, "2028-01-31", "1 February 2028 is a Tuesday: the grid starts on Monday")
        self.assertEqual(date.fromisoformat(flat[0].date).weekday(), 0)
        in_month = [c.date for c in flat if c.in_month]
        self.assertEqual((in_month[0], in_month[-1], len(in_month)), ("2028-02-01", "2028-02-29", 29))
        self.assertEqual([c.date for c in flat if c.today], ["2028-02-29"])
        self.assertTrue(flat[5].weekend and flat[6].weekend and not flat[4].weekend)

    def test_counts_from_a_vault(self):
        vault = Vault.create(Path(self.tmp.name) / "v.pkv", "master-pass-1")
        day = vault.journal.get("2026-10-14", create=True)
        assert day is not None
        day.add("ok", "APP-1")
        day.add("ok", "APP-2")
        day.add_activity("review", "!42")
        note = vault.notes.add("2026-10-14", title="Plan")
        note.add_item("one")
        note.add_item("two", done=True)
        note.add_item("three")
        vault.routines.add(name="Backup", schedule=Schedule(at="9:05", days=[2]))
        vault.routines.add(name="Unlock only", schedule=Schedule(on_unlock=True))
        vault.integrations.add_events([
            Event("merged", "group/repo!1", "t", "", "x", ts=datetime(2026, 10, 14, 10, 0).timestamp()),
            Event("merged", "group/repo!2", "t", "", "x", ts=datetime(2026, 10, 14, 18, 0).timestamp()),
            Event("merged", "group/repo!3", "t", "", "x", ts=datetime(2026, 10, 15, 8, 0).timestamp()),
        ])
        cal = Calendar()
        cal.add("Standup", "2026-10-07", time="11:00", repeat="weekly")
        expiries = [FakeExpiry(date(2026, 10, 14), "Server key"), FakeExpiry(date(2026, 10, 30), "Other")]
        rows = month_cells(2026, 10, vault, expiries, "2026-10-18", cal)
        cells = {c.date: c for r in rows for c in r}
        wed = cells["2026-10-14"]
        self.assertEqual(wed.journal, 3)
        self.assertEqual((wed.notes_open, wed.notes_total), (2, 3))
        self.assertEqual(wed.routines, ["09:05 Backup"])
        self.assertEqual(wed.expiries, ["Server key"])
        self.assertEqual([e.title for e in wed.events], ["Standup"])
        self.assertEqual(wed.feed, 2)
        thu = cells["2026-10-15"]
        self.assertEqual((thu.journal, thu.notes_total, thu.routines, thu.events, thu.feed), (0, 0, [], [], 1))
        self.assertEqual(cells["2026-10-21"].routines, ["09:05 Backup"])
        self.assertEqual(rows[0][0].date, "2026-09-28")

    def test_hidden_kind_not_counted(self):
        layout = jl.preset("qa")
        kind = layout.kind("manual")
        assert kind is not None
        kind.hidden = True
        jl.configure(layout)
        self.addCleanup(jl.configure, jl.preset("qa"))
        vault = Vault.create(Path(self.tmp.name) / "v.pkv", "master-pass-1")
        day = vault.journal.get("2026-10-14", create=True)
        assert day is not None
        day.add("ok", "APP-1")
        day.activities += [{"kind": "manual", "text": "APP-2"}, {"kind": "review", "text": "!5"}]
        cells = {c.date: c for r in month_cells(2026, 10, vault, [], "2026-10-18", Calendar()) for c in r}
        self.assertEqual(cells["2026-10-14"].journal, 2, "the column item and the review; the hidden manual case is not counted")

    def test_calendar_taken_from_the_vault_when_present(self):
        vault = Vault.create(Path(self.tmp.name) / "v.pkv", "master-pass-1")
        cal = Calendar()
        cal.add("Own", "2026-10-02")
        vault.calendar = cal  # type: ignore[attr-defined]  # the key the integrator adds
        cells = {c.date: c for r in month_cells(2026, 10, vault, [], "2026-10-18") for c in r}
        self.assertEqual([e.title for e in cells["2026-10-02"].events], ["Own"])


if __name__ == "__main__":
    unittest.main()
