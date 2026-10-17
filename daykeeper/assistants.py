"""Sending work to an AI assistant the user already has — Claude Code, Codex, GeckIt — on
the user's own plan: Daykeeper calls no model itself and keeps no key for one.

An assignment is a text (a template filled from a note or a merge request), a project
folder and, for GeckIt, a goal. The text never goes on a command line: it is written to
`<data>/assignments/<id>.md` and the assistant gets one short line telling it to read
that file. Three channels deliver it:

- `terminal` — a console window with `claude` / `codex` in the project folder
  (`runner.open_in_console`);
- `geckit` — `geckit start`, a card on the GeckIt board that GeckIt asks the person about;
- `quiet` — `claude -p` in a scratch folder, the answer shown inside Daykeeper
  (`assistant_runs.py`).

Settings (plain, in settings.json — nothing secret):
    "ai_projects": [{"gitlab": "group/app", "folder": "E:/work/app", "geckit": "app"}],
    "ai_templates": {"presets": {"mr_brief": false}, "custom": [{…Template…}]},
    "ai_defaults": {"agent": "claude", "channel": "terminal", "model": "", "geckit": ""},
    "ai_last_choice": {"note_task": {"agent": "claude", "channel": "geckit", "project": "app"}}

Pure: no tkinter, no network; the disk only in `assignment_file`, `write_script` and
`prune_assignments`, so the unit tests run on temporary folders."""

from __future__ import annotations

import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

from .i18n import _
from .settings import private

KINDS = ("mr_review", "mr_fix", "note_task", "mr_brief", "call_digest", "custom")
CHANNELS = ("terminal", "geckit", "quiet")
AGENT_IDS = ("claude", "codex")
AGENT_NAMES = {"claude": "Claude Code", "codex": "Codex"}
PLACEHOLDERS = ("mr_url", "mr_title", "mr_project", "mr_iid", "note_title", "note_text", "note_items", "transcript", "today", "folder")
ASSIGNMENTS_DIR = "assignments"
KEEP_DAYS = 30

# The variables that take a session off the person's plan: a key, a token, a gateway or a
# cloud switch bills the work to something else (the same list as GeckIt's `planOnly`).
# Removed by name; their values are never read.
OFF_PLAN = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "CODEX_API_KEY",
    "AZURE_OPENAI_API_KEY",
)

_ID_RE = re.compile(r"^[0-9a-z]{6,32}$")
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
# In the launcher batch file the folder stands inside quotes, where only a quote or a line
# break breaks it; the assignment path stands between `\"` that cmd reads as the end and
# the start of a quoted part, so cmd's operators are refused there too. `%` is doubled.
_CMD_UNSAFE_FOLDER = set('"\r\n')
_CMD_UNSAFE_PATH = set('"&|<>^\r\n')


def _new_id() -> str:
    return secrets.token_hex(6)


def _text(item: Mapping, key: str) -> str:
    """A string field of an object read from settings.json, stripped; anything else is ""."""
    value = item.get(key)
    return value.strip() if isinstance(value, str) else ""


# ---- the assignment


@dataclass
class Assignment:
    kind: str  # one of KINDS
    title: str  # the GeckIt card title and the history line
    text: str  # the whole assignment: the rendered template
    folder: str = ""  # the project folder; "" — a scratch folder (quiet) or the home folder
    goal: str = ""  # when it is done (GeckIt)
    agent: str = "claude"  # one of AGENT_IDS
    channel: str = "terminal"  # one of CHANNELS
    project: str = ""  # the project's name in GeckIt
    links: list[str] = field(default_factory=list)  # merge requests and issues it is about
    id: str = field(default_factory=_new_id)
    created: float = field(default_factory=time.time)


# ---- the project map


@dataclass
class Project:
    """Where the work on a repository happens: its GitLab path, its folder, its GeckIt name."""

    gitlab: str = ""  # "group/app"
    folder: str = ""
    geckit: str = ""  # "" — the folder's name

    @property
    def geckit_name(self) -> str:
        return self.geckit or Path(self.folder).name

    def to_dict(self) -> dict:
        return {"gitlab": self.gitlab, "folder": self.folder, "geckit": self.geckit}


def is_repo(folder: str) -> bool:
    """A git checkout: `.git` is a folder, or a file in a worktree."""
    return bool(folder) and os.path.exists(os.path.join(folder, ".git"))


def read_projects(settings: Mapping, check: Callable[[str], bool] = is_repo) -> list[Project]:
    """The project map; entries that are not objects, have no folder, or whose folder is not
    a git checkout are dropped, and a GitLab path is kept once."""
    raw = settings.get("ai_projects")
    out: list[Project] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        gitlab, folder, geckit = (_text(item, k) for k in ("gitlab", "folder", "geckit"))
        gitlab = gitlab.strip("/")
        if not folder or not check(folder):
            continue
        if gitlab and gitlab.lower() in seen:
            continue
        seen.add(gitlab.lower())
        out.append(Project(gitlab, folder, geckit))
    return out


def write_projects(settings: dict, projects: list[Project]) -> None:
    settings["ai_projects"] = [p.to_dict() for p in projects]


def project_for(projects: list[Project], gitlab_path: str) -> Project | None:
    """The project of a GitLab path ("group/app"), case-insensitively."""
    key = gitlab_path.strip("/").lower()
    return next((p for p in projects if key and p.gitlab.lower() == key), None)


def project_named(projects: list[Project], name: str) -> Project | None:
    return next((p for p in projects if name and p.geckit_name == name), None)


# ---- templates


@dataclass
class Template:
    id: str
    kind: str
    title: str  # a preset's title is a UI key (shown through _()); a custom one is the user's text
    body: str  # what the assistant reads: Markdown with placeholders, not translated
    goal: str = ""
    channel: str = "terminal"
    agent: str = ""  # "" — the default assistant
    preset: bool = False
    enabled: bool = True

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title, "body": self.body,
                "goal": self.goal, "channel": self.channel, "agent": self.agent, "enabled": self.enabled}


PRESETS: tuple[Template, ...] = (
    Template(
        "mr_review", "mr_review", "Review a merge request",
        "Review merge request {mr_url} ({mr_project} !{mr_iid}) in this repository against its review rules. "
        "Post the review as the repository's practice requires (one resolvable thread with the findings and a verdict). "
        "Do not merge.",
        goal="A review with a verdict is posted on {mr_url}", preset=True,
    ),
    Template(
        "mr_fix", "mr_fix", "Fix the review notes",
        "Merge request {mr_url} is mine and has reviewer notes. Fetch the branch, read every unresolved thread, "
        "fix the code, run the project checks, push to the MR branch, reply in each thread and resolve it. Do not merge.",
        goal="Every thread on {mr_url} is resolved and the branch is pushed", preset=True,
    ),
    Template(
        "note_task", "note_task", "A note as a task",
        "{note_title}\n\n{note_text}\n\nTo do:\n{note_items}",
        goal="{note_title}", preset=True,
    ),
    Template(
        "mr_brief", "mr_brief", "Brief a merge request",
        "Summarize this merge request in one paragraph: what changes, what to look at first, risks. Diff follows.",
        channel="quiet", agent="claude", preset=True,
    ),
    Template(
        "call_digest", "call_digest", "Digest of a call",
        "From this call transcript extract: a 3-5 sentence summary, decisions, notes, and tasks "
        "(who, what, due when if said). Answer with JSON only:\n"
        '{"summary": "...", "notes": ["..."], "tasks": [{"text": "...", "owner": "...", "due": "..."}]}\n\n'
        "Transcript:\n{transcript}",
        channel="quiet", agent="claude", preset=True,
    ),
)
PRESET_IDS = tuple(t.id for t in PRESETS)


def _template_from(item: Mapping) -> Template | None:
    def text(key: str) -> str:
        value = item.get(key, "")
        return value if isinstance(value, str) else ""

    body, title = text("body"), text("title").strip()
    if not body.strip() or not title:
        return None
    tid = text("id").strip()
    kind, channel, agent = text("kind"), text("channel"), text("agent")
    return Template(
        id=tid if _ID_RE.match(tid) else _new_id(),
        kind=kind if kind in KINDS else "custom",
        title=title,
        body=body,
        goal=text("goal"),
        channel=channel if channel in CHANNELS else "terminal",
        agent=agent if agent in AGENT_IDS else "",
        enabled=item.get("enabled", True) is not False,
    )


def read_templates(settings: Mapping) -> list[Template]:
    """Every preset (with its on/off switch) and then the user's own templates; malformed
    entries are dropped."""
    found = settings.get("ai_templates")
    raw: dict = found if isinstance(found, dict) else {}
    given = raw.get("presets")
    switches: dict = given if isinstance(given, dict) else {}
    out = [replace(t, enabled=switches.get(t.id, True) is not False) for t in PRESETS]
    ids = set(PRESET_IDS)
    custom = raw.get("custom")
    for item in custom if isinstance(custom, list) else []:
        t = _template_from(item) if isinstance(item, dict) else None
        if t is None:
            continue
        if t.id in ids:
            t = replace(t, id=_new_id())
        ids.add(t.id)
        out.append(t)
    return out


def write_templates(settings: dict, templates: list[Template]) -> None:
    preset_ids = set(PRESET_IDS)
    settings["ai_templates"] = {
        "presets": {t.id: t.enabled for t in templates if t.preset and t.id in preset_ids},
        "custom": [t.to_dict() for t in templates if not t.preset],
    }


def new_template_id() -> str:
    return _new_id()


def active_templates(templates: list[Template], kind: str = "") -> list[Template]:
    """The switched-on templates, of one kind when given."""
    return [t for t in templates if t.enabled and (not kind or t.kind == kind)]


def render(template: Template | str, context: Mapping[str, object]) -> str:
    """The text with the known placeholders filled: a missing value is empty, `{today}`
    defaults to today. Any other brace — JSON in the text, `{x}` the user wrote — stays.
    One pass: a value that itself holds `{mr_url}` is not filled again."""
    text = template.body if isinstance(template, Template) else template
    values = {"today": date.today().isoformat(), **context}

    def fill(m: re.Match) -> str:
        name = m.group(1)
        return str(values.get(name, "")) if name in PLACEHOLDERS else m.group(0)

    return _PLACEHOLDER_RE.sub(fill, text)


def note_context(title: str, text: str, items: list[str]) -> dict[str, str]:
    """A note card for the templates: the open checklist items as a Markdown list."""
    return {"note_title": title, "note_text": text, "note_items": "\n".join(f"- {i}" for i in items if i.strip())}


def mr_context(url: str, title: str, project: str, iid: int | str) -> dict[str, str]:
    return {"mr_url": url, "mr_title": title, "mr_project": project, "mr_iid": str(iid)}


def assignment_from(template: Template, context: Mapping[str, object], *, title: str = "", folder: str = "",
                    project: str = "", agent: str = "", channel: str = "", links: list[str] | None = None) -> Assignment:
    """An assignment from a template: body and goal rendered, the folder given as `{folder}`."""
    ctx = {"folder": folder, **context}
    if not title:
        if ctx.get("mr_iid"):
            title = f"{ctx.get('mr_project', '')} !{ctx['mr_iid']} {ctx.get('mr_title', '')}".strip()
        else:
            title = str(ctx.get("note_title") or template.title)
    return Assignment(
        kind=template.kind,
        title=title,
        text=render(template, ctx),
        folder=folder,
        goal=render(template.goal, ctx).strip(),
        agent=agent or template.agent or "claude",
        channel=channel or template.channel,
        project=project,
        links=list(links or []),
    )


# ---- defaults and the last choice


@dataclass
class Defaults:
    agent: str = "claude"
    channel: str = "terminal"
    model: str = ""  # the quiet channel's model; "" — the assistant's own default
    geckit: str = ""  # GeckIt's program set by hand; "" — found on its own


def read_defaults(settings: Mapping) -> Defaults:
    raw = settings.get("ai_defaults")
    raw = raw if isinstance(raw, dict) else {}

    def text(key: str) -> str:
        value = raw.get(key, "")
        return value.strip() if isinstance(value, str) else ""

    agent, channel = text("agent"), text("channel")
    return Defaults(agent if agent in AGENT_IDS else "claude", channel if channel in CHANNELS else "terminal", text("model"), text("geckit"))


def write_defaults(settings: dict, defaults: Defaults) -> None:
    settings["ai_defaults"] = {"agent": defaults.agent, "channel": defaults.channel, "model": defaults.model, "geckit": defaults.geckit}


@dataclass
class Choice:
    """What the send dialog chose last for a kind of assignment."""

    agent: str = "claude"
    channel: str = "terminal"
    project: str = ""


def read_last_choice(settings: Mapping, kind: str) -> Choice | None:
    raw = settings.get("ai_last_choice")
    item = raw.get(kind) if isinstance(raw, dict) else None
    if not isinstance(item, dict):
        return None
    agent, channel, project = (item.get(k) for k in ("agent", "channel", "project"))
    if agent not in AGENT_IDS or channel not in CHANNELS:
        return None
    return Choice(str(agent), str(channel), project if isinstance(project, str) else "")


def remember_choice(settings: dict, kind: str, choice: Choice) -> None:
    raw = settings.get("ai_last_choice")
    kept = {k: v for k, v in raw.items() if k in KINDS and isinstance(v, dict)} if isinstance(raw, dict) else {}
    if kind in KINDS:
        kept[kind] = {"agent": choice.agent, "channel": choice.channel, "project": choice.project}
    settings["ai_last_choice"] = kept


# ---- the assignment file


def assignments_dir(data_dir: Path) -> Path:
    return Path(data_dir) / ASSIGNMENTS_DIR


def assignment_file(data_dir: Path, a: Assignment) -> Path:
    """Write the assignment's text to `<data>/assignments/<id>.md`, owner-only like the
    app's other private files, and return the path."""
    from .vault import write_private  # the vault module pulls in the crypto: only when a file is written

    if not _ID_RE.match(a.id):
        raise ValueError(f"not an assignment id: {a.id!r}")
    folder = assignments_dir(data_dir)
    if not folder.exists():
        folder.mkdir(parents=True, exist_ok=True)
        private(folder, folder=True)
    path = folder / f"{a.id}.md"
    write_private(path, a.text.encode("utf-8"))
    return path


def prune_assignments(data_dir: Path, days: int = KEEP_DAYS, now: float | None = None) -> int:
    """Delete assignment files (and their launchers) older than `days`; how many went."""
    folder = assignments_dir(data_dir)
    limit = (time.time() if now is None else now) - days * 86400
    gone = 0
    for path in folder.glob("*") if folder.is_dir() else []:
        if path.suffix not in (".md", ".cmd") or not _ID_RE.match(path.stem):
            continue
        try:
            if path.stat().st_mtime < limit:
                path.unlink()
                gone += path.suffix == ".md"
        except OSError:
            pass
    return gone


def launch_line(a: Assignment, path: Path | str) -> str:
    """The short line the assistant is started with: read the file, do what it says."""
    return f'Read the file "{path}" and do what it says. Report in the language of the file.'


# ---- the environment


def plan_env(environ: Mapping[str, str]) -> dict[str, str]:
    """The environment without the variables that take a session off the user's plan. Names
    are compared without case: Windows does."""
    off = set(OFF_PLAN)
    return {k: v for k, v in environ.items() if k.upper() not in off}


def off_plan_names(environ: Mapping[str, str]) -> list[str]:
    off = set(OFF_PLAN)
    return sorted(k for k in environ if k.upper() in off)


# ---- the terminal channel


@dataclass
class ConsolePlan:
    """What `runner.open_in_console` starts: `shell` (or `argv`) in `cwd`. On Windows the
    command is a launcher batch file beside the assignment (`script` is its text, written by
    `write_script`): `open_in_console` hands its line to `cmd /k` through list2cmdline,
    which turns every inner quote into `\\"` — cmd does not read that back, so a quoted
    prompt cannot go on that line. `problem` — why it cannot start ("" — it can)."""

    argv: list[str] | None = None
    shell: str = ""
    cwd: str = ""
    script: str = ""
    script_path: str = ""
    problem: str = ""


def _batch_text(value: str) -> str:
    return value.replace("%", "%%")


def terminal_plan(a: Assignment, path: Path | str, agent: str = "", platform: str = sys.platform,
                  environ: Mapping[str, str] = os.environ, home: str = "") -> ConsolePlan:
    """The assistant in a console window, in the project folder, told to read the
    assignment file. The variables of OFF_PLAN present in `environ` are cleared for it."""
    agent = agent or a.agent or "claude"
    line = launch_line(a, path)
    folder = a.folder or home or str(Path.home())
    off = off_plan_names(environ)
    if platform == "win32":
        bad = folder if set(folder) & _CMD_UNSAFE_FOLDER else str(path) if set(str(path)) & _CMD_UNSAFE_PATH else ""
        if bad:
            return ConsolePlan(problem=_("The folder has characters the console cannot pass: {path}").format(path=bad))
        script_path = Path(path).with_suffix(".cmd")
        lines = ["@echo off", "chcp 65001 >nul"]  # the file is UTF-8: a folder in Cyrillic reads right
        lines += [f'set "{name}="' for name in off]
        lines += [f'cd /d "{_batch_text(folder)}"', _batch_text(subprocess.list2cmdline([agent, line]))]
        return ConsolePlan(
            shell=script_path.name, cwd=str(script_path.parent),
            script="\r\n".join(lines) + "\r\n", script_path=str(script_path),
        )
    argv = [agent, line]
    prefix = ["env"] + [x for name in off for x in ("-u", name)] if off else []
    return ConsolePlan(argv=argv, shell=shlex.join(prefix + argv), cwd=folder)


def write_script(plan: ConsolePlan) -> None:
    """Write the plan's launcher, if it has one, owner-only."""
    if plan.script and plan.script_path:
        from .vault import write_private

        write_private(Path(plan.script_path), plan.script.encode("utf-8"))


def installed_agents(which: Callable[[str], str | None] = shutil.which) -> list[str]:
    """The assistants found on the path, in AGENT_IDS order."""
    return [a for a in AGENT_IDS if which(a)]


def claude_command(environ: Mapping[str, str] = os.environ, exists: Callable[[str], bool] = os.path.isfile,
                   platform: str = sys.platform) -> str:
    """What to start for `claude` without a shell. Windows starts only an .exe that way, and
    npm puts `claude.cmd` on the path — a shim over the `claude.exe` in its package — so
    that one is started instead (as GeckIt does)."""
    if platform != "win32":
        return "claude"
    key = next((k for k in environ if k.upper() == "PATH"), "PATH")
    for folder in str(environ.get(key, "")).split(os.pathsep):
        if not folder:
            continue
        exe = os.path.join(folder, "claude.exe")
        if exists(exe):
            return exe
        shimmed = os.path.join(folder, "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe")
        if exists(os.path.join(folder, "claude.cmd")) and exists(shimmed):
            return shimmed
    return "claude"


# ---- the quiet channel


def quiet_argv(a: Assignment, agent: str = "claude", model: str = "", program: str = "") -> list[str]:
    """One answer from `claude -p`: no tools and no project settings (`--restricted`), no
    conversation left behind, the answer as JSON. The text goes on stdin, not here."""
    if (agent or "claude") != "claude":
        raise ValueError(f"the quiet channel runs Claude Code only, not {agent!r}")
    argv = [program or "claude", "-p", "--restricted", "--no-session-persistence", "--output-format", "json"]
    return argv + (["--model", model] if model else [])


# ---- the GeckIt channel


@dataclass
class GeckitInstall:
    exe: str  # GeckIt's program
    cli: str  # its command-line script inside the app bundle
    data: str  # its data folder, GECKIT_DATA


def geckit_install(environ: Mapping[str, str] = os.environ, exists: Callable[[str], bool] = os.path.isfile,
                   platform: str = sys.platform, path: str = "", home: str = "") -> GeckitInstall | None:
    """The installed GeckIt: %LOCALAPPDATA%\\Programs\\geckit\\GeckIt.exe on Windows,
    /Applications/GeckIt.app on macOS, or `path` set by hand (the program, or the .app).
    None — not found (or another system)."""
    home = home or str(Path.home())
    if platform == "win32":
        exe = path or (os.path.join(environ["LOCALAPPDATA"], "Programs", "geckit", "GeckIt.exe") if environ.get("LOCALAPPDATA") else "")
        if not exe or not exists(exe):
            return None
        root = os.path.dirname(exe)
        cli = os.path.join(root, "resources", "app.asar", "out", "main", "cli.js")
        data = os.path.join(environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming"), "geckit")
        return GeckitInstall(exe, cli, data)
    if platform == "darwin":
        app = path or "/Applications/GeckIt.app"
        if app.endswith(".app"):
            exe, resources = f"{app}/Contents/MacOS/GeckIt", f"{app}/Contents/Resources"
        else:
            exe, resources = app, str(Path(app).parent.parent / "Resources")
        if not exists(exe):
            return None
        return GeckitInstall(exe, f"{resources}/app.asar/out/main/cli.js", f"{home}/Library/Application Support/geckit")
    return None


def geckit_argv(a: Assignment, path: Path | str, install: GeckitInstall) -> list[str]:
    """`geckit start` without the sh launcher: GeckIt's own program run as Node on its CLI
    script (with `geckit_env`). The text is the launch line; the answer is JSON."""
    argv = [install.exe, install.cli, "start", "--project", a.project, "--title", a.title]
    if a.goal:
        argv += ["--goal", a.goal]
    if a.agent in AGENT_IDS:
        argv += ["--provider", a.agent]
    return argv + ["--json", launch_line(a, path)]


def geckit_env(install: GeckitInstall, environ: Mapping[str, str] = os.environ) -> dict[str, str]:
    return dict(plan_env(environ), ELECTRON_RUN_AS_NODE="1", GECKIT_DATA=install.data)

