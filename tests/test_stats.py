"""The journal over a period: the period boundaries, the counts by the layout, the heat map
levels and the Markdown text."""

import unittest
from datetime import date

from daykeeper import journal_layout as jl
from daykeeper import stats
from daykeeper.journal import Day, Journal
from daykeeper.locale_ru import RU
from tests.helpers import reset_globals


def journal(*days: Day) -> Journal:
    return Journal(list(days))


class PeriodTest(unittest.TestCase):
    def test_week_runs_monday_to_sunday(self):
        self.assertEqual(stats.period("week", date(2026, 10, 14)), stats.Period("2026-10-12", "2026-10-18"))
        self.assertEqual(stats.period("week", date(2026, 10, 12)), stats.Period("2026-10-12", "2026-10-18"))
        self.assertEqual(stats.period("week", date(2026, 10, 18)), stats.Period("2026-10-12", "2026-10-18"))

    def test_week_across_month_and_year_ends(self):
        self.assertEqual(stats.period("week", date(2026, 12, 31)), stats.Period("2026-12-28", "2027-01-03"))
        self.assertEqual(stats.period("last_week", date(2027, 1, 2)), stats.Period("2026-12-21", "2026-12-27"))
        self.assertEqual(stats.period("last_week", date(2026, 11, 2)), stats.Period("2026-10-26", "2026-11-01"))

    def test_months(self):
        self.assertEqual(stats.period("month", date(2026, 2, 10)), stats.Period("2026-02-01", "2026-02-28"))
        self.assertEqual(stats.period("month", date(2028, 2, 29)), stats.Period("2028-02-01", "2028-02-29"))
        self.assertEqual(stats.period("month", date(2026, 12, 31)), stats.Period("2026-12-01", "2026-12-31"))
        self.assertEqual(stats.period("last_month", date(2027, 1, 1)), stats.Period("2026-12-01", "2026-12-31"))
        self.assertEqual(stats.period("last_month", date(2026, 3, 31)), stats.Period("2026-02-01", "2026-02-28"))

    def test_quarters(self):
        self.assertEqual(stats.period("quarter", date(2026, 1, 1)), stats.Period("2026-01-01", "2026-03-31"))
        self.assertEqual(stats.period("quarter", date(2026, 6, 30)), stats.Period("2026-04-01", "2026-06-30"))
        self.assertEqual(stats.period("quarter", date(2026, 8, 15)), stats.Period("2026-07-01", "2026-09-30"))
        self.assertEqual(stats.period("quarter", date(2026, 12, 31)), stats.Period("2026-10-01", "2026-12-31"))

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            stats.period("year", date(2026, 1, 1))

    def test_custom_dates(self):
        self.assertEqual(stats.custom("2026-10-20", date(2026, 10, 1)), stats.Period("2026-10-01", "2026-10-20"))
        self.assertEqual(stats.custom(" 2026-10-01 ", "2026-10-01").dates(), ["2026-10-01"])
        with self.assertRaises(ValueError):
            stats.custom("not a date", "2026-10-01")

    def test_dates_and_membership(self):
        span = stats.Period("2026-12-30", "2027-01-02")
        self.assertEqual(span.dates(), ["2026-12-30", "2026-12-31", "2027-01-01", "2027-01-02"])
        self.assertIn("2027-01-01", span)
        self.assertNotIn("2027-01-03", span)

    def test_every_kind_has_a_translated_label(self):
        self.assertEqual(list(stats.KIND_LABELS), list(stats.KINDS))
        self.assertEqual([v for v in stats.KIND_LABELS.values() if v not in RU], [])


class SummarizeTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.addCleanup(reset_globals)
        self.layout = jl.preset("qa")

    def test_empty_period(self):
        s = stats.summarize(journal(), stats.Period("2026-10-12", "2026-10-18"), self.layout)
        self.assertEqual(s.columns, {"ok": 0, "failed": 0, "bugs": 0})
        self.assertEqual(set(s.kinds), {"review", "mr_open", "mr_merged", "manual", "auto", "note"})
        self.assertEqual(len(s.per_day), 7)
        self.assertEqual(sum(s.per_day.values()), 0)
        self.assertEqual(s.days_with_entries, 0)
        self.assertEqual(s.notes, [])
        self.assertEqual([w.start for w in s.per_week], ["2026-10-12"])

    def test_counts_columns_kinds_days_and_weeks(self):
        j = journal(
            Day("2026-09-30", columns={"ok": ["DEMO-9"]}),  # outside the period
            Day("2026-10-01", columns={"ok": ["DEMO-1", "DEMO-2"], "bugs": ["DEMO-3"]}, activities=[{"kind": "review", "text": "!1"}, {"kind": "note", "text": "nightly run triage"}]),
            Day("2026-10-02", columns={"failed": ["DEMO-4"]}, activities=[{"kind": "note", "text": "planning"}, {"kind": "note", "text": "nightly run triage"}]),
            Day("2026-10-05", columns={"ok": ["DEMO-5"]}, activities=[{"kind": "auto", "text": "DEMO-5"}, {"kind": "note", "text": "planning"}, {"kind": "note", "text": "nightly run triage"}]),
            Day("2026-10-06"),  # an empty day
            Day("2026-11-01", columns={"ok": ["DEMO-6"]}),  # outside the period
        )
        s = stats.summarize(j, stats.period("month", date(2026, 10, 14)), self.layout)
        self.assertEqual(s.columns, {"ok": 3, "failed": 1, "bugs": 1})
        self.assertEqual(s.kinds["review"], 1)
        self.assertEqual(s.kinds["auto"], 1)
        self.assertEqual(s.kinds["note"], 5)
        self.assertEqual(s.per_day["2026-10-01"], 5)
        self.assertEqual(s.per_day["2026-10-02"], 3)
        self.assertEqual(s.per_day["2026-10-06"], 0)
        self.assertEqual(len(s.per_day), 31)
        self.assertEqual(s.total, sum(s.columns.values()) + sum(s.kinds.values()))
        self.assertEqual(s.days_with_entries, 3)
        self.assertEqual(s.notes, [("nightly run triage", 3), ("planning", 2)])
        # the month starts on a Thursday: the first row is that week's Monday, counting October only
        self.assertEqual(s.per_week[0].start, "2026-09-28")
        self.assertEqual(s.per_week[0].columns["ok"], 2)
        self.assertEqual(s.per_week[1].start, "2026-10-05")
        self.assertEqual(s.per_week[1].total, 4)
        self.assertEqual(len(s.per_week), 5)
        self.assertEqual(sum(w.total for w in s.per_week), s.total)

    def test_hidden_and_foreign_kinds_are_not_counted(self):
        layout = jl.copy(self.layout)
        layout.kind("review").hidden = True
        layout.column("bugs").hidden = True
        j = journal(Day("2026-10-01", columns={"bugs": ["DEMO-1"], "done": ["DEMO-2"]}, activities=[{"kind": "review", "text": "!1"}, {"kind": "meetings", "text": "sync"}]))
        s = stats.summarize(j, stats.custom("2026-10-01", "2026-10-01"), layout)
        self.assertNotIn("bugs", s.columns)
        self.assertNotIn("review", s.kinds)
        self.assertEqual(s.total, 0)
        self.assertEqual(s.days_with_entries, 0)

    def test_custom_layout_with_renamed_and_extra_columns(self):
        layout = jl.Layout(
            "custom",
            [jl.Column("shipped", "Shipped"), jl.Column("ok", "Accepted"), jl.Column("calls", "Calls")],
            [jl.Kind("meetings", "Meetings"), jl.Kind("thoughts", "Thoughts", role=jl.ROLE_NOTE)],
        )
        j = journal(Day("2026-10-01", columns={"shipped": ["a", "b"], "ok": ["DEMO-1"], "calls": ["c"]}, activities=[{"kind": "meetings", "text": "retro"}, {"kind": "thoughts", "text": "idea"}]))
        s = stats.summarize(j, stats.custom("2026-10-01", "2026-10-07"), layout)
        self.assertEqual(s.columns, {"shipped": 2, "ok": 1, "calls": 1})
        self.assertEqual(s.kinds, {"meetings": 1, "thoughts": 1})
        self.assertEqual(s.notes, [("idea", 1)])
        text = stats.as_markdown(s, layout)
        for label in ("| Shipped | 2 |", "| Accepted | 1 |", "| Calls | 1 |", "| Meetings | 1 |", "| Thoughts | 1 |", "- idea"):
            self.assertIn(label, text)
        self.assertNotIn("Tested successfully", text)


class HeatLevelsTest(unittest.TestCase):
    def test_zero_is_zero(self):
        self.assertEqual(stats.heat_levels({"a": 0, "b": 0}), {"a": 0, "b": 0})
        self.assertEqual(stats.heat_levels({}), {})

    def test_equal_numbers_share_the_top_level(self):
        self.assertEqual(stats.heat_levels({"a": 3, "b": 3, "c": 3, "d": 0}), {"a": 4, "b": 4, "c": 4, "d": 0})

    def test_quarters_of_busy_days(self):
        levels = stats.heat_levels({"a": 1, "b": 2, "c": 3, "d": 4, "e": 0})
        self.assertEqual(levels, {"a": 1, "b": 2, "c": 3, "d": 4, "e": 0})

    def test_ties_get_one_level_and_the_busiest_is_four(self):
        levels = stats.heat_levels({"a": 1, "b": 1, "c": 1, "d": 9})
        self.assertEqual(levels["a"], levels["b"])
        self.assertEqual(levels["b"], levels["c"])
        self.assertEqual(levels["d"], 4)
        self.assertLess(levels["a"], 4)

    def test_levels_never_go_down_as_the_total_grows(self):
        per_day = {f"d{n}": n % 7 for n in range(40)}
        levels = stats.heat_levels(per_day)
        ordered = [levels[d] for d in sorted(per_day, key=per_day.__getitem__)]
        self.assertEqual(ordered, sorted(ordered))
        self.assertTrue(all(0 <= v <= 4 for v in ordered))
        self.assertTrue(all((levels[d] == 0) == (per_day[d] == 0) for d in per_day))


class MarkdownTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.addCleanup(reset_globals)
        self.layout = jl.preset("qa")
        self.journal = journal(
            Day("2026-10-01", columns={"ok": ["DEMO-1"]}, activities=[{"kind": "note", "text": "a | b"}]),
            Day("2026-10-08", columns={"failed": ["DEMO-2"]}),
        )

    def test_totals_weeks_and_notes(self):
        s = stats.summarize(self.journal, stats.period("month", date(2026, 10, 1)), self.layout)
        text = stats.as_markdown(s, self.layout)
        self.assertTrue(text.startswith("## Report for 01.10.2026 – 31.10.2026"))
        self.assertIn("| Tested successfully | 1 |", text)
        self.assertIn("| Test failed | 1 |", text)
        self.assertIn("| MR reviews | 0 |", text)
        self.assertIn("Days with entries: 2", text)
        self.assertIn("| Week | Tested successfully |", text)
        self.assertIn("| 28.09.2026 | 1 | 0 |", text)
        self.assertIn("### Frequent notes", text)
        self.assertIn("- a | b", text)  # a note line is not a table cell

    def test_one_week_has_no_week_table(self):
        s = stats.summarize(self.journal, stats.period("week", date(2026, 10, 1)), self.layout)
        text = stats.as_markdown(s, self.layout)
        self.assertNotIn("| Week |", text)

    def test_pipe_in_a_label_does_not_break_the_table(self):
        layout = jl.Layout("custom", [jl.Column("x", "Done | shipped")], [])
        s = stats.summarize(journal(), stats.custom("2026-10-01", "2026-10-01"), layout)
        self.assertIn("| Done \\| shipped | 0 |", stats.as_markdown(s, layout))

    def test_russian_labels(self):
        from daykeeper.i18n import set_language

        set_language("ru")
        s = stats.summarize(self.journal, stats.period("month", date(2026, 10, 1)), self.layout)
        text = stats.as_markdown(s, self.layout)
        self.assertIn("Отчёт за 01.10.2026 – 31.10.2026", text)
        self.assertIn(f"| {RU['Tested successfully']} | 1 |", text)
        self.assertIn("Частые заметки", text)


if __name__ == "__main__":
    unittest.main()
