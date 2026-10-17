"""Daily routine: schedules, launch plans, order and storage."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from daykeeper.gitlab import Event
from daykeeper.routines import (
    EVENT_RUNS_MAX, Routine, Routines, Schedule, describe_schedule, event_context, event_runs, fill_args, interpreter_for, launch_plan, quote_arg,
    split_args, subtitle,
)
from daykeeper.vault import Vault

WED_0930 = datetime(2026, 10, 14, 9, 30)  # a Wednesday


class ScheduleTest(unittest.TestCase):
    def test_due_once_a_day_after_the_time(self):
        s = Schedule(at="09:15")
        self.assertTrue(s.due(WED_0930, last_run=0))
        ran = datetime(2026, 10, 14, 9, 16).timestamp()
        self.assertFalse(s.due(WED_0930, last_run=ran), "already ran today")
        yesterday = datetime(2026, 10, 13, 9, 16).timestamp()
        self.assertTrue(s.due(WED_0930, last_run=yesterday))
        self.assertFalse(s.due(datetime(2026, 10, 14, 9, 0), last_run=0), "not yet")

    def test_weekdays_and_bad_times(self):
        self.assertFalse(Schedule(at="09:00", days=[5, 6]).due(WED_0930, 0))
        for bad in ("", "9", "25:00", "09:60", "ab:cd"):
            self.assertFalse(Schedule(at=bad).due(WED_0930, 0), bad)

    def test_next_run(self):
        self.assertEqual(Schedule(at="10:00").next_run(WED_0930), datetime(2026, 10, 14, 10, 0))
        self.assertEqual(Schedule(at="09:00").next_run(WED_0930), datetime(2026, 10, 15, 9, 0))
        self.assertEqual(Schedule(at="09:00", days=[0]).next_run(WED_0930), datetime(2026, 10, 19, 9, 0))
        self.assertIsNone(Schedule(at="").next_run(WED_0930))

    def test_describe(self):
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        self.assertEqual(describe_schedule(Schedule(at="09:30"), names), "09:30 Mon–Fri")
        self.assertEqual(describe_schedule(Schedule(at="18:00", days=list(range(7))), names), "18:00")
        self.assertEqual(describe_schedule(Schedule(at="08:00", days=[5, 6]), names), "08:00 Sat, Sun")
        self.assertEqual(describe_schedule(Schedule(), names), "")


class LaunchPlanTest(unittest.TestCase):
    def which(self, name):
        return {"py": "C:/py.exe", "python3": "/usr/bin/python3", "bash": "/bin/bash"}.get(name)

    def test_interpreters(self):
        self.assertEqual(interpreter_for("a.py", "win32", self.which), ["py", "-3"])
        self.assertEqual(interpreter_for("a.py", "darwin", self.which), ["/usr/bin/python3"])
        self.assertEqual(interpreter_for("a.ps1", "win32", lambda n: None)[0], "powershell")
        self.assertEqual(interpreter_for("a.BAT", "win32", self.which), ["cmd", "/c"])
        self.assertEqual(interpreter_for("a.sh", "linux", self.which), ["/bin/bash"])
        self.assertIsNone(interpreter_for("tool.exe", "win32", self.which))

    def test_script_plan(self):
        r = Routine(kind="script", target="C:/s/report.py", args='--day today "two words"')
        plan = launch_plan(r, "win32", self.which, exists=lambda p: True)
        self.assertEqual(plan.argv, ["py", "-3", "C:/s/report.py", "--day", "today", "two words"])
        self.assertEqual(plan.cwd.replace("\\", "/"), "C:/s")
        self.assertEqual(launch_plan(r, "win32", self.which, exists=lambda p: False).problem, "missing")

    def test_other_kinds(self):
        self.assertEqual(launch_plan(Routine(kind="url", target="example.com")).target, "https://example.com")
        cmd = launch_plan(Routine(kind="command", target="git -C E:/x pull", cwd="E:/x"))
        self.assertEqual((cmd.action, cmd.shell, cmd.cwd), ("run", "git -C E:/x pull", "E:/x"))
        self.assertEqual(launch_plan(Routine(kind="folder", target="Z:/nope"), exists=lambda p: False).problem, "missing")
        self.assertEqual(launch_plan(Routine(target="  ")).problem, "empty")

    def test_subtitle_names_the_script_not_the_interpreter(self):
        ps = Routine(kind="script", target=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", args=r'-NoProfile -File "D:\jobs\update monitoring.ps1"')
        self.assertEqual(subtitle(ps), "update monitoring.ps1 · powershell")
        self.assertEqual(subtitle(Routine(kind="script", target="/home/me/bin/backup.sh", args="-v")), "backup.sh")
        self.assertEqual(subtitle(Routine(kind="folder", target="D:\\work\\repo\\")), "repo")
        self.assertEqual(subtitle(Routine(kind="url", target="https://grafana.example.com/")), "grafana.example.com")
        self.assertEqual(subtitle(Routine(kind="command", target="git pull")), "git pull")

    def test_split_args(self):
        self.assertEqual(split_args("-a 'b c'", "linux"), ["-a", "b c"])
        self.assertEqual(split_args("", "win32"), [])
        self.assertEqual(split_args('--out "C:/My Files/r.txt" -v', "win32"), ["--out", "C:/My Files/r.txt", "-v"])


class RoutinesTest(unittest.TestCase):
    def test_order_due_and_unlock(self):
        rs = Routines()
        a = rs.add(name="a", target="x", schedule=Schedule(on_unlock=True))
        b = rs.add(name="b", target="y", schedule=Schedule(at="09:00"))
        self.assertEqual(rs.on_unlock(), [a])
        self.assertEqual(rs.due(WED_0930), [b])
        self.assertTrue(rs.move(b, -1))
        self.assertEqual(rs.ordered(), [b, a])
        self.assertFalse(rs.move(b, -1))

    def test_round_trip_in_the_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v.pkv"
            v = Vault.create(path, "master-pass-1")
            v.routines.add(name="Pull", kind="command", target="git pull", schedule=Schedule(True, "08:45", [0, 2]), terminal=True)
            v.routines.items[0].last_code = 3
            v.save()
            r = Vault.open(path, "master-pass-1").routines.items[0]
            self.assertEqual((r.name, r.kind, r.terminal, r.last_code), ("Pull", "command", True, 3))
            self.assertEqual(r.schedule.to_dict(), {"on_unlock": True, "at": "08:45", "days": [0, 2], "on_events": [], "projects": []})
        self.assertEqual(Routine.from_dict({"kind": "evil"}).kind, "script")



MERGED = Event("merged", "group/repo!12", "Fix the login", "https://gitlab.example.com/group/repo/-/merge_requests/12", "{mr} was merged", {"mr": "group/repo!12"})


class EventRoutinesTest(unittest.TestCase):
    def test_for_event_with_a_project_filter(self):
        rs = Routines()
        any_project = rs.add(name="any", target="x", schedule=Schedule(on_events=["merged"]))
        only_other = rs.add(name="other", target="x", schedule=Schedule(on_events=["merged", "watch_failed"], projects=["group/other"]))
        rs.add(name="plain", target="x", schedule=Schedule(at="09:00"))
        self.assertEqual(rs.for_event("merged", "group/repo"), [any_project])
        self.assertEqual(rs.for_event("merged", "group/other"), [any_project, only_other])
        self.assertEqual(rs.for_event("watch_failed", "group/other"), [only_other])
        self.assertEqual(rs.for_event("approved", "group/repo"), [])

    def test_schedule_round_trip_and_old_data(self):
        s = Schedule(on_events=["merged"], projects=["group/repo"])
        self.assertEqual(Schedule.from_dict(s.to_dict()), s)
        old = Schedule.from_dict({"on_unlock": True, "at": "08:45", "days": [0]})
        self.assertEqual((old.on_events, old.projects), ([], []))
        self.assertEqual(Schedule.from_dict({"on_events": ["merged", 3, "", "merged"], "projects": "group/repo"}).to_dict()["on_events"], ["merged"])
        self.assertEqual(Schedule.from_dict({"projects": "group/repo"}).projects, [])

    def test_event_context(self):
        ctx = event_context(MERGED)
        self.assertEqual(ctx, {
            "DAYKEEPER_EVENT": "merged", "DAYKEEPER_SOURCE": "gitlab", "DAYKEEPER_REF": "group/repo!12",
            "DAYKEEPER_URL": "https://gitlab.example.com/group/repo/-/merge_requests/12", "DAYKEEPER_TITLE": "Fix the login",
            "DAYKEEPER_PROJECT": "group/repo", "DAYKEEPER_TEXT": "group/repo!12 was merged",
        })
        self.assertEqual(event_context(MERGED, translate=lambda t: "Смержен {mr}")["DAYKEEPER_TEXT"], "Смержен group/repo!12")
        branch = Event("branch_commits", "group/repo@release/1.2", "t", "", "{missing} text", {})
        self.assertEqual((event_context(branch)["DAYKEEPER_PROJECT"], event_context(branch)["DAYKEEPER_TEXT"]), ("group/repo", "{missing} text"))
        jira = Event("assigned", "KEY-1", "t", "", "x", source="jira")
        self.assertEqual((event_context(jira)["DAYKEEPER_PROJECT"], event_context(jira)["DAYKEEPER_SOURCE"]), ("", "jira"))

    def test_fill_args_leaves_an_unknown_brace(self):
        ctx = event_context(MERGED)
        raw = lambda v: v  # noqa: E731
        self.assertEqual(fill_args("--ref {ref} --url={url} {unknown} {} {{x}}", ctx, quote=raw), "--ref group/repo!12 --url=https://gitlab.example.com/group/repo/-/merge_requests/12 {unknown} {} {{x}}")
        self.assertEqual(fill_args("{event} {project}", {}), "{event} {project}")

    def test_a_quoted_value_stays_one_argument(self):
        ctx = {"DAYKEEPER_TITLE": 'Fix "the" login & more; rm -rf x'}
        for platform in ("win32", "linux"):
            args = fill_args("--title {title} --x", ctx, quote=lambda v, p=platform: quote_arg(v, p))
            parts = split_args(args, platform)
            self.assertEqual(len(parts), 3, (platform, parts))
            self.assertEqual(parts[0], "--title")
        self.assertEqual(split_args(fill_args("{title}", ctx, quote=lambda v: quote_arg(v, "linux")), "linux"), [ctx["DAYKEEPER_TITLE"]])

    def test_values_are_quoted_by_default(self):
        ctx = {"DAYKEEPER_TITLE": 'a" --flag "b'}
        parts = split_args(fill_args("--title {title}", ctx))
        self.assertEqual(len(parts), 2, parts)
        self.assertEqual(parts[0], "--title")
        self.assertNotIn("--flag", parts)

    def test_windows_values_lose_percent_and_line_breaks(self):
        ctx = {"DAYKEEPER_TITLE": "%SOME_TOKEN% line1\r\nline2\nline3\rend"}
        args = fill_args("{title}", ctx, quote=lambda v: quote_arg(v, "win32"))
        self.assertNotIn("%", args, "cmd must find no %VAR% to expand")
        self.assertNotIn("\n", args)
        self.assertNotIn("\r", args)
        self.assertEqual(split_args(args, "win32"), ["％SOME_TOKEN％ line1 line2 line3 end"])
        posix = split_args(fill_args("{title}", ctx, quote=lambda v: quote_arg(v, "linux")), "linux")
        self.assertEqual(posix, ["%SOME_TOKEN% line1 line2 line3 end"], "posix quoting keeps %, still drops line breaks")

    def test_event_context_drops_nul(self):
        ev = Event("merged", "group/repo!12", "Fix\0 the\0 login", "https://gitlab.example.com/x\0", "t\0ext", {})
        ctx = event_context(ev)
        self.assertTrue(all("\0" not in v for v in ctx.values()), ctx)
        self.assertEqual((ctx["DAYKEEPER_TITLE"], ctx["DAYKEEPER_URL"], ctx["DAYKEEPER_TEXT"]), ("Fix the login", "https://gitlab.example.com/x", "text"))

    def test_the_storm_limit(self):
        rs = Routines()
        a = rs.add(name="a", target="x", schedule=Schedule(on_events=["merged"]))
        b = rs.add(name="b", target="x", schedule=Schedule(on_events=["merged"], projects=["group/repo"]))
        events = [Event("merged", f"group/repo!{i}", "t", "", "x") for i in range(4)]
        runs, skipped = event_runs(rs, events)
        self.assertEqual(len(runs), EVENT_RUNS_MAX)
        self.assertEqual(skipped, 8 - EVENT_RUNS_MAX)
        self.assertEqual(runs[:3], [(a, events[0]), (b, events[0]), (a, events[1])], "one run of each routine per event, in order")
        self.assertEqual(event_runs(rs, events[:1]), ([(a, events[0]), (b, events[0])], 0))
        self.assertEqual(event_runs(rs, [Event("approved", "group/repo!1", "t", "", "x")]), ([], 0))


if __name__ == "__main__":
    unittest.main()
