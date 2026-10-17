"""Sending work to an assistant: templates, the project map, the assignment file and the
command of each channel. Temporary folders and fake lookups only; no assistant or GeckIt
is ever started."""

import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from daykeeper import assistants as ai
from daykeeper.locale_ru import RU

SECRET_TEXT = "Line one of the task\nwith a \"quote\" & an ampersand"


def yes(_path: str) -> bool:
    """A folder check that finds every folder: the plans below name folders of no real disk."""
    return True


def make_assignment(**kw):
    base = {"kind": "note_task", "title": "Card", "text": SECRET_TEXT, "folder": "", "id": "a1b2c3d4e5f6"}
    base.update(kw)
    return ai.Assignment(**base)


class RenderTest(unittest.TestCase):
    def test_every_placeholder_is_filled(self):
        body = " | ".join("{" + p + "}" for p in ai.PLACEHOLDERS)
        context = {p: f"<{p}>" for p in ai.PLACEHOLDERS}
        self.assertEqual(ai.render(body, context), " | ".join(f"<{p}>" for p in ai.PLACEHOLDERS))

    def test_unknown_braces_stay_and_missing_values_are_empty(self):
        text = '{"summary": "..."} {x} {mr_url}!'
        self.assertEqual(ai.render(text, {}), '{"summary": "..."} {x} !')

    def test_today_defaults_to_today(self):
        self.assertEqual(ai.render("{today}", {}), date.today().isoformat())

    def test_a_value_is_not_filled_again(self):
        self.assertEqual(ai.render("{note_text}", {"note_text": "{mr_url}", "mr_url": "u"}), "{mr_url}")

    def test_presets(self):
        templates = {t.id: t for t in ai.PRESETS}
        mr = ai.mr_context("https://git.example.com/group/app/-/merge_requests/7", "Fix login", "group/app", 7)
        text = ai.render(templates["mr_review"], mr)
        self.assertIn("(group/app !7)", text)
        self.assertIn("Do not merge.", text)
        note = ai.note_context("Plan", "Some text", ["first", " ", "second"])
        self.assertEqual(ai.render(templates["note_task"], note), "Plan\n\nSome text\n\nTo do:\n- first\n- second")
        self.assertNotIn("call_digest", templates)  # the calls module owns that prompt
        self.assertNotIn("call_digest", ai.KINDS)

    def test_assignment_from_a_template(self):
        mr_fix = next(t for t in ai.PRESETS if t.id == "mr_fix")
        a = ai.assignment_from(mr_fix, ai.mr_context("https://x/group/app/-/merge_requests/3", "T", "group/app", 3),
                               folder="/work/app", project="app", links=["https://x/group/app/-/merge_requests/3"])
        self.assertEqual(a.kind, "mr_fix")
        self.assertEqual(a.title, "group/app !3 T")
        self.assertEqual(a.goal, "Every thread on https://x/group/app/-/merge_requests/3 is resolved and the branch is pushed")
        self.assertEqual((a.agent, a.channel, a.folder, a.project), ("claude", "terminal", "/work/app", "app"))
        note = next(t for t in ai.PRESETS if t.id == "note_task")
        b = ai.assignment_from(note, ai.note_context("Plan", "", []), channel="geckit", agent="codex")
        self.assertEqual((b.title, b.goal, b.channel, b.agent), ("Plan", "Plan", "geckit", "codex"))
        self.assertNotEqual(a.id, b.id)

    def test_preset_titles_are_translated(self):
        self.assertEqual([t.title for t in ai.PRESETS if t.title not in RU], [])


class TemplatesSettingsTest(unittest.TestCase):
    def test_round_trip_and_switches(self):
        settings: dict = {}
        templates = ai.read_templates(settings)
        self.assertEqual([t.id for t in templates], list(ai.PRESET_IDS))
        templates[3].enabled = False
        templates.append(ai.Template(ai.new_template_id(), "custom", "Mine", "Do {note_title}", channel="quiet"))
        ai.write_templates(settings, templates)
        again = ai.read_templates(settings)
        self.assertEqual(settings["ai_templates"]["presets"]["mr_brief"], False)
        self.assertFalse(again[3].enabled)
        self.assertEqual((again[-1].title, again[-1].channel, again[-1].preset), ("Mine", "quiet", False))
        self.assertEqual([t.id for t in ai.active_templates(again, "mr_brief")], [])

    def test_garbage_is_dropped(self):
        settings = {"ai_templates": {"presets": "x", "custom": [
            1, None, {"title": "No body"}, {"body": "No title"},
            {"id": "mr_review", "title": "Clash", "body": "b", "kind": "weird", "channel": "fax", "agent": "gpt"},
        ]}}
        templates = ai.read_templates(settings)
        self.assertEqual(len(templates), len(ai.PRESETS) + 1)
        clash = templates[-1]
        self.assertNotIn(clash.id, ai.PRESET_IDS)
        self.assertEqual((clash.kind, clash.channel, clash.agent), ("custom", "terminal", ""))
        self.assertEqual(ai.read_templates({"ai_templates": []})[0].id, "mr_review")


class ProjectsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.repo = root / "app"
        (self.repo / ".git").mkdir(parents=True)
        self.worktree = root / "app-wt"
        self.worktree.mkdir()
        (self.worktree / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
        self.plain = root / "plain"
        self.plain.mkdir()

    def test_garbage_and_folders_without_git_are_dropped(self):
        settings = {"ai_projects": [
            "text", 5, None, {"gitlab": "x"}, {"folder": 3},
            {"gitlab": "/group/app/", "folder": str(self.repo), "geckit": ""},
            {"gitlab": "GROUP/APP", "folder": str(self.worktree)},  # the same path again
            {"gitlab": "group/wt", "folder": str(self.worktree), "geckit": "wt"},
            {"gitlab": "group/plain", "folder": str(self.plain)},
            {"gitlab": "group/gone", "folder": str(Path(self.tmp.name) / "missing")},
        ]}
        projects = ai.read_projects(settings)
        self.assertEqual([(p.gitlab, p.geckit_name) for p in projects], [("group/app", "app"), ("group/wt", "wt")])
        self.assertEqual(ai.read_projects({"ai_projects": {"a": 1}}), [])
        self.assertEqual(ai.project_for(projects, "Group/App").folder, str(self.repo))
        self.assertIsNone(ai.project_for(projects, ""))
        self.assertEqual(ai.project_named(projects, "wt").gitlab, "group/wt")

    def test_write_round_trip(self):
        settings: dict = {}
        ai.write_projects(settings, [ai.Project("group/app", str(self.repo), "")])
        self.assertEqual(settings["ai_projects"], [{"gitlab": "group/app", "folder": str(self.repo), "geckit": ""}])
        self.assertEqual(len(ai.read_projects(settings)), 1)
        self.assertEqual(len(ai.read_projects(settings, check=lambda folder: False)), 0)


class DefaultsTest(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(ai.read_defaults({}), ai.Defaults())
        self.assertEqual(ai.read_defaults({"ai_defaults": {"agent": "gpt", "channel": "fax", "model": 3}}), ai.Defaults())
        settings: dict = {}
        ai.write_defaults(settings, ai.Defaults("codex", "geckit", "sonnet", "/opt/GeckIt"))
        self.assertEqual(ai.read_defaults(settings), ai.Defaults("codex", "geckit", "sonnet", "/opt/GeckIt"))

    def test_last_choice(self):
        settings: dict = {"ai_last_choice": {"junk": 1, "mr_review": {"agent": "x"}}}
        self.assertIsNone(ai.read_last_choice(settings, "note_task"))
        self.assertIsNone(ai.read_last_choice(settings, "mr_review"))
        ai.remember_choice(settings, "note_task", ai.Choice("codex", "geckit", "app"))
        self.assertEqual(ai.read_last_choice(settings, "note_task"), ai.Choice("codex", "geckit", "app"))
        self.assertNotIn("junk", settings["ai_last_choice"])
        ai.remember_choice(settings, "not-a-kind", ai.Choice())
        self.assertNotIn("not-a-kind", settings["ai_last_choice"])


class AssignmentFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)

    def test_written_under_assignments_as_md(self):
        a = make_assignment()
        path = ai.assignment_file(self.data, a)
        self.assertEqual(path, self.data / "assignments" / "a1b2c3d4e5f6.md")
        self.assertEqual(path.read_bytes().decode("utf-8"), SECRET_TEXT)
        self.assertFalse(path.with_suffix(".md.tmp").exists())
        if os.name == "posix":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_a_bad_id_is_refused(self):
        with self.assertRaises(ValueError):
            ai.assignment_file(self.data, make_assignment(id="../x"))

    def test_an_id_with_a_trailing_newline_is_refused(self):
        with self.assertRaises(ValueError):
            ai.assignment_file(self.data, make_assignment(id="a1b2c3d4e5f6\n"))
        self.assertFalse((self.data / "assignments").exists())
        template = ai.read_templates({"ai_templates": {"custom": [{"id": "abcdef123\n", "title": "T", "body": "b"}]}})[-1]
        self.assertNotEqual(template.id, "abcdef123\n")
        self.assertRegex(template.id, r"\A[0-9a-z]{6,32}\Z")

    def test_a_new_assignment_has_a_valid_id(self):
        path = ai.assignment_file(self.data, ai.Assignment("custom", "t", "x"))
        self.assertEqual(path.suffix, ".md")

    def test_prune(self):
        old = ai.assignment_file(self.data, make_assignment(id="aaaaaa111111"))
        new = ai.assignment_file(self.data, make_assignment(id="bbbbbb222222"))
        launcher = old.with_suffix(".cmd")
        launcher.write_text("x", encoding="utf-8")
        other = old.parent / "keep.txt"
        other.write_text("x", encoding="utf-8")
        past = time.time() - 40 * 86400
        for p in (old, launcher, other):
            os.utime(p, (past, past))
        self.assertEqual(ai.prune_assignments(self.data), 1)
        self.assertEqual(sorted(p.name for p in old.parent.iterdir()), sorted([new.name, "keep.txt"]))
        self.assertEqual(ai.prune_assignments(self.data / "none"), 0)


class CommandsTest(unittest.TestCase):
    PATH = Path("C:/Users/me/AppData/Roaming/Daykeeper/assignments/a1b2c3d4e5f6.md")

    def test_launch_line(self):
        line = ai.launch_line(make_assignment(), self.PATH)
        self.assertEqual(line, f'Read the file "{self.PATH}" and do what it says. Report in the language of the file.')

    def test_terminal_plan_on_windows(self):
        a = make_assignment(folder="E:/work/app 2")
        env = {"anthropic_api_key": "k", "PATH": "x", "OPENAI_API_KEY": "k2"}
        plan = ai.terminal_plan(a, self.PATH, "claude", platform="win32", environ=env, exists=lambda p: True)
        self.assertEqual(plan.problem, "")
        self.assertEqual(plan.shell, str(self.PATH.with_suffix(".cmd")))  # the full path, not the bare name
        self.assertEqual(Path(plan.cwd), self.PATH.parent)
        self.assertEqual(Path(plan.script_path), self.PATH.with_suffix(".cmd"))
        lines = plan.script.split("\r\n")
        self.assertIn('set "OPENAI_API_KEY="', lines)
        self.assertIn('set "anthropic_api_key="', lines)
        self.assertNotIn("PATH", plan.script)
        self.assertIn('cd /d "E:/work/app 2" || (echo Could not open the project folder: "E:/work/app 2" & pause & exit /b 1)', lines)
        launch = subprocess.list2cmdline(["claude", ai.launch_line(a, self.PATH)])
        self.assertIn(launch, lines)
        self.assertTrue(launch.startswith('claude "Read the file \\"C:'))
        self.assertNotIn(SECRET_TEXT, plan.script + plan.shell)

    def test_windows_percent_is_doubled_and_unsafe_paths_are_refused(self):
        a = make_assignment(folder="E:/work/100%")
        plan = ai.terminal_plan(a, self.PATH, "codex", platform="win32", environ={}, exists=yes)
        self.assertIn('cd /d "E:/work/100%%"', plan.script)
        self.assertIn("codex ", plan.script)
        bad = ai.terminal_plan(a, Path("C:/Users/A&B/x.md"), platform="win32", environ={}, exists=yes)
        self.assertIn("A&B", bad.problem)
        self.assertEqual(bad.shell, "")
        self.assertIn('a"b', ai.terminal_plan(make_assignment(folder='E:/a"b'), self.PATH, platform="win32", environ={}, exists=yes).problem)
        # the launcher's own path is the whole cmd line: cmd drops list2cmdline's quotes around `(`
        parens = ai.terminal_plan(make_assignment(folder="E:/w"), Path("C:/Users/A (B)/x.md"), platform="win32", environ={}, exists=yes)
        self.assertIn("A (B)", parens.problem)

    def test_windows_launcher_stops_when_the_folder_cannot_be_opened(self):
        plan = ai.terminal_plan(make_assignment(folder="E:/work (old)/a&b"), self.PATH, platform="win32", environ={}, exists=yes)
        cd = next(x for x in plan.script.split("\r\n") if " || " in x)
        self.assertEqual(cd, 'cd /d "E:/work (old)/a&b" || (echo Could not open the project folder: "E:/work (old)/a&b" & pause & exit /b 1)')
        unc = ai.terminal_plan(make_assignment(folder="\\\\server\\share\\app"), self.PATH, platform="win32", environ={}, exists=yes)
        self.assertIn('pushd "\\\\server\\share\\app" || (echo ', unc.script)
        self.assertNotIn("cd /d", unc.script)

    def test_the_folder_message_is_safe_in_a_batch_block(self):
        """The message stands unquoted inside `( … )` in the launcher, in both languages."""
        key = "Could not open the project folder:"
        for text in (key, RU[key]):
            self.assertEqual(set(text) & set('()&|<>^%"!'), set(), text)

    def test_a_missing_folder_is_refused_before_any_script(self):
        a = make_assignment(folder="E:/gone")
        for platform in ("win32", "linux"):
            plan = ai.terminal_plan(a, self.PATH, platform=platform, environ={}, exists=lambda p: False)
            self.assertEqual(plan.problem, "The project folder is not there: E:/gone")
            self.assertEqual((plan.shell, plan.script, plan.argv), ("", "", None))

    def test_an_unknown_agent_is_refused(self):
        for agent in ("claude & calc", "gpt", "Claude"):
            for platform in ("win32", "linux"):
                plan = ai.terminal_plan(make_assignment(folder="E:/w"), self.PATH, agent, platform=platform, environ={}, exists=yes)
                self.assertEqual(plan.problem, f"Not a known assistant: {agent}")
                self.assertEqual((plan.shell, plan.script), ("", ""))
        from_assignment = ai.terminal_plan(make_assignment(folder="E:/w", agent="x"), self.PATH, platform="win32", environ={}, exists=yes)
        self.assertEqual(from_assignment.problem, "Not a known assistant: x")

    def test_terminal_plan_on_macos_and_linux(self):
        a = make_assignment(folder="/home/me/work app")
        path = "/home/me/.local/share/Daykeeper/assignments/a1b2c3d4e5f6.md"
        for platform in ("darwin", "linux"):
            plan = ai.terminal_plan(a, path, "claude", platform=platform, environ={"ANTHROPIC_API_KEY": "k", "HOME": "/home/me"}, exists=yes)
            self.assertEqual(plan.cwd, "/home/me/work app")
            self.assertEqual(plan.argv, ["claude", ai.launch_line(a, path)])
            self.assertEqual(plan.shell, "cd '/home/me/work app' || exit 1; "
                             + shlex.join(["env", "-u", "ANTHROPIC_API_KEY", "claude", ai.launch_line(a, path)]))
            self.assertEqual(plan.script, "")
            self.assertNotIn(SECRET_TEXT, plan.shell)
        plain = ai.terminal_plan(make_assignment(), path, platform="linux", environ={}, home="/home/me", exists=yes)
        self.assertEqual(plain.shell, "cd /home/me || exit 1; " + shlex.join(["claude", ai.launch_line(a, path)]))
        self.assertEqual(plain.cwd, "/home/me")

    def test_write_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a1b2c3d4e5f6.md"
            plan = ai.terminal_plan(make_assignment(folder=tmp), path, platform="win32", environ={})
            ai.write_script(plan)
            self.assertEqual(Path(plan.script_path).read_bytes().decode("utf-8"), plan.script)
            ai.write_script(ai.ConsolePlan(argv=["x"]))  # nothing to write

    def test_plan_works_with_open_in_console(self):
        """open_in_console reads only argv, shell and cwd; the shell line wins over argv."""
        a = make_assignment(folder="/w")
        plan = ai.terminal_plan(a, "/d/x.md", platform="linux", environ={}, exists=yes)
        self.assertEqual(plan.problem, "")
        self.assertEqual(plan.argv, ["claude", ai.launch_line(a, "/d/x.md")])
        self.assertEqual(plan.cwd, "/w")
        self.assertEqual(shlex.split(plan.shell), ["cd", "/w", "||", "exit", "1;", "claude", ai.launch_line(a, "/d/x.md")])

    def test_quiet_argv(self):
        a = make_assignment()
        self.assertEqual(ai.quiet_argv(a, "claude"), ["claude", "-p", "--restricted", "--no-session-persistence", "--output-format", "json"])
        self.assertEqual(ai.quiet_argv(a, "claude", "sonnet", program="C:/bin/claude.exe")[-2:], ["--model", "sonnet"])
        self.assertNotIn(SECRET_TEXT, " ".join(ai.quiet_argv(a)))
        with self.assertRaises(ValueError):
            ai.quiet_argv(a, "codex")

    def test_installed_agents(self):
        self.assertEqual(ai.installed_agents(lambda name: "/bin/codex" if name == "codex" else None), ["codex"])
        self.assertEqual(ai.installed_agents(lambda name: None), [])
        self.assertEqual(ai.installed_agents(lambda name: "/bin/" + name), ["claude", "codex"])

    def test_claude_command(self):
        files = {os.path.join("C:/npm", "claude.cmd"), os.path.join("C:/npm", "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe")}
        env = {"Path": os.pathsep.join(["", "C:/other", "C:/npm"])}
        self.assertEqual(ai.claude_command(env, files.__contains__, "win32"), os.path.join("C:/npm", "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe"))
        files.add(os.path.join("C:/other", "claude.exe"))
        self.assertEqual(ai.claude_command(env, files.__contains__, "win32"), os.path.join("C:/other", "claude.exe"))
        self.assertEqual(ai.claude_command({}, files.__contains__, "win32"), "claude")
        self.assertEqual(ai.claude_command(env, files.__contains__, "darwin"), "claude")

    def test_plan_env_drops_provider_keys(self):
        env = {"ANTHROPIC_API_KEY": "1", "anthropic_auth_token": "2", "OPENAI_API_KEY": "3", "CLAUDE_CODE_USE_BEDROCK": "1",
               "ANTHROPIC_BASE_URL": "u", "PATH": "p", "HOME": "h", "MY_ANTHROPIC_NOTE": "keep"}
        self.assertEqual(ai.plan_env(env), {"PATH": "p", "HOME": "h", "MY_ANTHROPIC_NOTE": "keep"})
        self.assertEqual(env["ANTHROPIC_API_KEY"], "1")  # the caller's mapping is not changed


SHIM = (
    "import json, os, sys\n"
    "names = set(n.upper() for n in os.environ)\n"
    "with open({out!r}, 'w', encoding='utf-8') as f:\n"
    "    json.dump({{'argv': sys.argv[1:], 'cwd': os.getcwd(), 'keys': sorted(names & {off!r})}}, f)\n"
)


@unittest.skipUnless(sys.platform == "win32", "the launcher is a Windows batch file")
class WindowsLauncherRunTest(unittest.TestCase):
    """The generated .cmd really run by cmd, with a `claude.cmd` stand-in on a PATH that holds
    only it and System32 — the real assistant cannot be found. `cmd /c` with no window, as
    open_in_console starts it (`cmd /k` there) with the plan's shell line and folder."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="dk launcher ")  # a space: the shell line is quoted
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.out = self.root / "seen.json"
        (self.bin / "shim.py").write_text(SHIM.format(out=str(self.out), off=set(ai.OFF_PLAN)), encoding="utf-8")
        (self.bin / "claude.cmd").write_text(f'@"{sys.executable}" "{self.bin / "shim.py"}" %*\r\n', encoding="utf-8")
        self.project = self.root / "проект app"
        self.project.mkdir()
        system32 = os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32")
        self.cmd = os.path.join(system32, "cmd.exe")
        self.env = {
            "PATH": os.pathsep.join([str(self.bin), system32]),
            "PATHEXT": ".COM;.EXE;.BAT;.CMD",
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"),
            "NoDefaultCurrentDirectoryInExePath": "1",  # cmd does not look in the current folder
            "ANTHROPIC_API_KEY": "parent-only-key",
            "openai_api_key": "parent-only-key",
        }
        self.a = make_assignment(folder=str(self.project))
        self.path = ai.assignment_file(self.root / "data dir", self.a)

    def run_launcher(self, plan: ai.ConsolePlan) -> subprocess.CompletedProcess:
        return subprocess.run(  # noqa: S603 — cmd on a launcher this test wrote
            [self.cmd, "/c", plan.shell], cwd=plan.cwd, env=self.env, stdin=subprocess.DEVNULL,
            capture_output=True, timeout=60, creationflags=0x08000000,  # CREATE_NO_WINDOW
        )

    def test_the_stand_in_gets_the_launch_line_in_the_project_folder(self):
        plan = ai.terminal_plan(self.a, self.path, "claude", platform="win32", environ=self.env)
        self.assertEqual(plan.problem, "")
        ai.write_script(plan)
        done = self.run_launcher(plan)
        self.assertTrue(self.out.exists(), done.stdout.decode("utf-8", "replace") + done.stderr.decode("utf-8", "replace"))
        seen = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(seen["argv"], [ai.launch_line(self.a, self.path)])
        self.assertEqual(os.path.normcase(seen["cwd"]), os.path.normcase(str(self.project)))
        self.assertEqual(seen["keys"], [])

    def test_a_folder_gone_after_planning_stops_the_launcher(self):
        plan = ai.terminal_plan(self.a, self.path, "claude", platform="win32", environ=self.env)
        ai.write_script(plan)
        self.project.rmdir()
        done = self.run_launcher(plan)
        self.assertFalse(self.out.exists())
        self.assertEqual(done.returncode, 1)
        self.assertIn("Could not open the project folder", done.stdout.decode("utf-8", "replace"))


class GeckitTest(unittest.TestCase):
    def test_the_channel_is_not_offered_yet(self):
        install = ai.GeckitInstall("G.exe", "cli.js", "D:/data/geckit")
        self.assertFalse(ai.GECKIT_CHANNEL_READY)
        self.assertFalse(ai.geckit_available(install))
        self.assertFalse(ai.geckit_available(None))
        with mock.patch.object(ai, "GECKIT_CHANNEL_READY", True):  # the day GeckIt can take it
            self.assertTrue(ai.geckit_available(install))
            self.assertFalse(ai.geckit_available(None))

    def test_install_on_windows(self):
        env = {"LOCALAPPDATA": "C:/Users/me/AppData/Local", "APPDATA": "C:/Users/me/AppData/Roaming"}
        exe = os.path.join("C:/Users/me/AppData/Local", "Programs", "geckit", "GeckIt.exe")
        found = ai.geckit_install(env, {exe}.__contains__, "win32")
        self.assertEqual(found.exe, exe)
        self.assertEqual(found.cli, os.path.join(os.path.dirname(exe), "resources", "app.asar", "out", "main", "cli.js"))
        self.assertEqual(found.data, os.path.join("C:/Users/me/AppData/Roaming", "geckit"))
        self.assertIsNone(ai.geckit_install(env, lambda p: False, "win32"))
        self.assertIsNone(ai.geckit_install({}, lambda p: True, "win32"))
        manual = ai.geckit_install(env, {"D:/apps/GeckIt.exe"}.__contains__, "win32", path="D:/apps/GeckIt.exe")
        self.assertEqual(manual.exe, "D:/apps/GeckIt.exe")

    def test_install_on_macos(self):
        exe = "/Applications/GeckIt.app/Contents/MacOS/GeckIt"
        found = ai.geckit_install({}, {exe}.__contains__, "darwin", home="/Users/me")
        self.assertEqual(found.exe, exe)
        self.assertEqual(found.cli, "/Applications/GeckIt.app/Contents/Resources/app.asar/out/main/cli.js")
        self.assertEqual(found.data, "/Users/me/Library/Application Support/geckit")
        self.assertIsNone(ai.geckit_install({}, lambda p: False, "darwin"))
        self.assertIsNone(ai.geckit_install({}, lambda p: True, "linux"))

    def test_argv_and_env(self):
        install = ai.GeckitInstall("G.exe", "cli.js", "D:/data/geckit")
        a = make_assignment(project="app", goal="All done", agent="codex", title="Card")
        path = Path("/d/a1b2c3d4e5f6.md")
        self.assertEqual(ai.geckit_argv(a, path, install), [
            "G.exe", "cli.js", "start", "--project", "app", "--title", "Card", "--goal", "All done",
            "--provider", "codex", "--json", ai.launch_line(a, path),
        ])
        self.assertNotIn("--goal", ai.geckit_argv(make_assignment(project="app"), path, install))
        self.assertNotIn(SECRET_TEXT, " ".join(ai.geckit_argv(a, path, install)))
        env = ai.geckit_env(install, {"PATH": "p", "ANTHROPIC_API_KEY": "k"})
        self.assertEqual(env, {"PATH": "p", "ELECTRON_RUN_AS_NODE": "1", "GECKIT_DATA": "D:/data/geckit"})


if __name__ == "__main__":
    unittest.main()
