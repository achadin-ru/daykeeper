"""What the Claude Code hook counts as a journal event: ready-made rule sets (GitLab, GitHub,
the tracker, verdict comments) that are switched on and off, and the user's own rules
("if the command has … — write …"). Pure: no tkinter, no disk, no network, so the helper
exe, the app's "Check" box and the unit tests run the same code.

Config — the `hook` key of settings.json next to the vault:
    {"presets": {"gitlab": true, "github": false, …},
     "rules": [{"name": …, "status": "note", "pattern": …, …}],
     "only_folders": [], "skip_folders": []}"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import NamedTuple
from collections.abc import Callable

from .i18n import variants
from .settings import Links, app_dir

COMMAND_TOOLS = ("Bash", "PowerShell")
MATCHER = "Bash|PowerShell|mcp__.*"  # Claude Code calls the hook only for these tools

MR_URL_RE = re.compile(r"https?://[^/\s\"']+/([\w./-]+?)/-/merge_requests/(\d+)")
PR_URL_RE = re.compile(r"https?://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
ISSUE_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
KEY_IN_RESPONSE = re.compile(r"\bkey\"?\s*:\s*\"?([A-Z][A-Z0-9]+-\d+)")
POST_RE = re.compile(r"(?i)(-X\s*POST|--request\s*POST|--method\s*POST|-Method\s*Post|requests\.post|\.post\()")
SELF_TEST_MARKERS = ("journal-inbox.py", "daykeeper-cli", "cli_main.py", "DAYKEEPER_INBOX", '"tool_name"')  # the hook's own checks are not events


def _fail_words() -> tuple[str, ...]:
    return tuple(v.lower() for key in ("needs rework", "reproduces again", "not fixed", "test failed", "reproduces") for v in variants(key))


def _verdict_head() -> re.Pattern:
    return re.compile("|".join(re.escape(v) for v in variants("tested on")), re.IGNORECASE)


def flat_text(value) -> str:
    """Tool input or response as flat text: dictionary values are joined, not JSON-escaped."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(flat_text(v) if isinstance(v, (dict, list)) else f"{k}: {v}" for k, v in value.items())
    if isinstance(value, list):
        return "\n".join(flat_text(v) for v in value)
    return str(value)


class Hit(NamedTuple):
    status: str
    item: str
    rule: str  # preset id or the user rule's name: shown in "Check" and in the log


@dataclass
class Call:
    """One tool call as the rules see it. `command` — the shell command, or for any other tool
    its name and input as text, so a user rule can match an MCP tool too."""

    tool: str
    command: str
    response: str
    cwd: str = ""

    @classmethod
    def from_payload(cls, payload: dict) -> Call:
        tool = str(payload.get("tool_name", ""))
        tool_input = payload.get("tool_input") or {}
        if tool in COMMAND_TOOLS and isinstance(tool_input, dict):
            command = flat_text(tool_input.get("command", ""))
        else:
            command = f"{tool}\n{flat_text(tool_input)}"
        return cls(tool, command, flat_text(payload.get("tool_response", "")), str(payload.get("cwd") or ""))

    @property
    def is_command(self) -> bool:
        return self.tool in COMMAND_TOOLS


# ---- ready-made sets


def _body_text(command: str, cwd: str = "") -> str:
    """Request body from the command: inline after -d/--data or a file after @path. Only a
    file under the folder the command ran in is read (a relative path when the folder is
    not known): the hook looks for a verdict in the body it sent, not in any file a
    command happens to name."""
    parts = [command]
    for m in re.finditer(r"@(['\"]?)([^\s'\"]+)\1", command):
        path = _under_cwd(Path(m.group(2)), cwd)
        if path is None:
            continue
        try:
            if path.is_file() and path.stat().st_size < 512_000:
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return "\n".join(parts)


def _under_cwd(path: Path, cwd: str) -> Path | None:
    """The file's full path when it lies under `cwd`; None otherwise. Without a known
    folder only a relative path without `..` qualifies."""
    if not cwd:
        return None if path.is_absolute() or ".." in path.parts else path
    try:
        base = Path(cwd).resolve()
        full = (base / path).resolve() if not path.is_absolute() else path.resolve()
    except OSError:
        return None
    return full if full == base or base in full.parents else None


class Ctx(NamedTuple):
    call: Call
    links: Links
    own_mr: Callable[[str], bool | None] | None


def _mr_item(m: re.Match, links: Links) -> str:
    """Own repository → `!N`, a foreign one — the full link as is."""
    return f"!{m.group(2)}" if m.group(1) == links.gitlab_project else m.group(0)


def _mr_from_command(command: str, links: Links) -> str | None:
    """MR number from a glab command (`glab mr merge 44`, `glab mr approve !44`, a link)."""
    m = MR_URL_RE.search(command)
    if m:
        return _mr_item(m, links)
    m = re.search(r"\bglab\s+mr\s+(?:merge|approve)\s+(?:--\S+\s+)*!?(\d+)", command)
    if m:
        return f"!{m.group(1)}"
    m = re.search(r"merge_requests/(\d+)\b", command)  # glab api projects/<id>/merge_requests/N/… — own repository
    return f"!{m.group(1)}" if m else None


def _gitlab(ctx: Ctx) -> list[tuple[str, str]]:
    call, links = ctx.call, ctx.links
    found: list[tuple[str, str]] = []
    if not call.is_command:
        return found
    command, response = call.command, call.response
    if re.search(r"\bglab\s+mr\s+create\b", command) or re.search(r"POST\b.*merge_requests(?![/\d])", command):
        found += [("mr-open", _mr_item(m, links)) for m in MR_URL_RE.finditer(response)]
    if re.search(r"\bglab\s+mr\s+merge\b", command) or re.search(r"merge_requests/\d+/merge\b", command):
        item = _mr_from_command(command, links)
        if item and "error" not in response.lower()[:200]:
            found.append(("mr-merged", item))
    # Review: an approval, a NEW thread or a general comment. A reply in an existing thread
    # (…/discussions/<id>/notes) is work on someone's review of an own MR, not a review.
    if re.search(r"\bglab\s+mr\s+approve\b", command) or (
        re.search(r"merge_requests/\d+/(?:approve|discussions|notes)\b(?!/)", command)
        and not re.search(r"merge_requests/\d+/discussions/[^/\s\"']+/notes", command)
        and POST_RE.search(command)
    ):
        item = _mr_from_command(command, links)
        if item and not (ctx.own_mr is not None and ctx.own_mr(item) is True):
            found.append(("review", item))
    return found


def _github(ctx: Ctx) -> list[tuple[str, str]]:
    call = ctx.call
    found: list[tuple[str, str]] = []
    if not call.is_command:
        return found
    command, response = call.command, call.response
    if re.search(r"\bgh\s+pr\s+create\b", command):
        found += [("mr-open", m.group(0)) for m in PR_URL_RE.finditer(response)]

    def pr_from_command() -> str | None:
        m = PR_URL_RE.search(command)
        if m:
            return m.group(0)
        m = re.search(r"\bgh\s+pr\s+(?:merge|review)\s+(?:--?\S+\s+)*#?(\d+)", command)
        if m:
            repo = re.search(r"(?:-R|--repo)\s+([\w.-]+/[\w.-]+)", command)
            return f"https://github.com/{repo.group(1)}/pull/{m.group(1)}" if repo else f"#{m.group(1)}"
        return None

    if re.search(r"\bgh\s+pr\s+merge\b", command) and "error" not in response.lower()[:200]:
        item = pr_from_command()
        if item:
            found.append(("mr-merged", item))
    if re.search(r"\bgh\s+pr\s+review\b", command):
        item = pr_from_command()
        if item:
            found.append(("review", item))
    return found


def _tracker(ctx: Ctx) -> list[tuple[str, str]]:
    """A new issue (REST POST …/issue or the Atlassian tools) and a move to Test failed."""
    call, found = ctx.call, []
    if call.is_command:
        command = call.command
        if re.search(r"/rest/api/[23]/issue/?(?:[\"'?]|\s|$)", command) and POST_RE.search(command):
            m = KEY_IN_RESPONSE.search(call.response)
            if m:
                found.append(("bug", m.group(1)))
        m = re.search(r"/rest/api/[23]/issue/([A-Z][A-Z0-9]+-\d+)/transitions", command)
        if m and "test failed" in _body_text(command, call.cwd).lower():
            found.append(("fail", m.group(1)))
    elif _is_tracker_tool(call.tool):
        name, inp = call.tool.lower(), call.command
        if "createjiraissue" in name or ("create" in name and "issue" in name):
            m = KEY_IN_RESPONSE.search(call.response)
            if m:
                found.append(("bug", m.group(1)))
        elif "transition" in name and "test failed" in inp.lower():
            key = ISSUE_KEY_RE.search(inp.split("\n", 1)[-1])
            if key:
                found.append(("fail", key.group(1)))
    return found


def _verdict(ctx: Ctx) -> list[tuple[str, str]]:
    """A tracker comment that starts with "Tested on …": pass, or fail when it says so."""
    call, head = ctx.call, _verdict_head()
    if call.is_command:
        m = re.search(r"/rest/api/[23]/issue/([A-Z][A-Z0-9]+-\d+)/comment", call.command)
        if m:
            body = _body_text(call.command, call.cwd)
            if head.search(body):
                return [("fail" if any(w in body.lower() for w in _fail_words()) else "pass", m.group(1))]
    elif _is_tracker_tool(call.tool) and "comment" in call.tool.lower():
        inp = call.command.split("\n", 1)[-1]
        if head.search(inp):
            key = ISSUE_KEY_RE.search(inp)
            if key:
                return [("fail" if any(w in inp.lower() for w in _fail_words()) else "pass", key.group(1))]
    return []


def _is_tracker_tool(tool: str) -> bool:
    low = tool.lower()
    return low.startswith("mcp__") and ("atlassian" in low or "jira" in low)


@dataclass(frozen=True)
class Preset:
    id: str
    title: str  # UI key
    hint: str  # UI key
    find: Callable[[Ctx], list[tuple[str, str]]]
    example: str = ""  # UI key: the tooltip, a command and what it writes


PRESETS: tuple[Preset, ...] = (
    Preset("gitlab", "GitLab", "glab: an MR opened, merged, approved or a new thread in someone else's MR", _gitlab,
           "For example, Claude runs “glab mr merge 44” — the journal gets “MRs merged: !44”. Replies in threads of your own MR are not counted as a review."),
    Preset("github", "GitHub", "gh: a pull request opened, merged or reviewed", _github,
           "For example, Claude runs “gh pr create” — the journal gets “MRs opened” with the pull request link."),
    Preset("tracker", "Issue tracker", "Jira: a new issue, a move to Test failed (REST and the Atlassian tools)", _tracker,
           "For example, Claude files a bug through the Jira API and gets APP-4127 back — the journal gets “New bug: APP-4127”."),
    Preset("verdict", "Verdict comments", "a tracker comment that starts with “Tested on …”: passed, or failed if it says so", _verdict,
           "For example, a comment “Tested on dev. Works.” on APP-1 — “Tested successfully: APP-1”; with “Needs rework” — “Test failed: APP-1”."),
)
PRESET_IDS = tuple(p.id for p in PRESETS)


# ---- own rules

TAKE_KEY, TAKE_MR, TAKE_MATCH, TAKE_TEXT = "key", "mr", "match", "text"
TAKES = (TAKE_KEY, TAKE_MR, TAKE_MATCH, TAKE_TEXT)
TAKE_LABELS = {
    TAKE_KEY: "the issue key (APP-123) from the command or the answer",
    TAKE_MR: "the MR link or number from the command or the answer",
    TAKE_MATCH: "the matched text (the first group of a pattern)",
    TAKE_TEXT: "the fixed text below",
}
WHERE_COMMAND, WHERE_RESPONSE, WHERE_ANY = "command", "response", "any"
WHERES = (WHERE_COMMAND, WHERE_RESPONSE, WHERE_ANY)
WHERE_LABELS = {WHERE_COMMAND: "in the command", WHERE_RESPONSE: "in the answer", WHERE_ANY: "in either"}


SECRET_PREFIXES = ("glpat-", "gldt-", "ghp_", "gho_", "ghs_", "github_pat_", "sk-", "xox", "AKIA", "eyJ")
SECRET_RE = re.compile(r"[A-Za-z0-9_\-+=.]{32,}")


def looks_secret(text: str) -> bool:
    """A token or a key rather than words: a known token prefix, or a long run without
    spaces mixing upper and lower case and digits. An address or a path is not."""
    for word in text.split():
        if "://" in word or "/" in word:
            continue
        if word.startswith(SECRET_PREFIXES) and len(word) >= 16:
            return True
        if SECRET_RE.fullmatch(word) and any(c.isupper() for c in word) and any(c.islower() for c in word) and any(c.isdigit() for c in word):
            return True
    return False


# characters of the command and of the answer a user rule searches: an unanchored pattern
# that starts with `.*` is quadratic on a text it does not match, and 16 KB keeps that
# at ~0.15 s (64 KB took 2.3 s); what the journal looks for sits in the first lines anyway
SCAN_LIMIT = 16 * 1024
PATTERN_LIMIT = 500  # characters of a pattern
INLINE_LIMIT = 2048  # characters: a longer text runs the user regex in a child process with a deadline
MATCH_DEADLINE = 1.5  # s for that child, its start included (the hook has 15 s in all)
slow_rules: list[str] = []  # the rules whose child hit the deadline in the last `match` (the hook logs them)
# the child: a regex search on JSON from stdin, the groups as JSON on stdout; bytes in and
# out, so the console code page and a lone surrogate in the text do not matter
PROBE_CODE = (
    "import json,re,sys\n"
    "d=json.loads(sys.stdin.buffer.read().decode('utf-8','surrogatepass'))\n"
    "try: m=re.search(d['pattern'],d['text'],d['flags'])\n"
    "except Exception: m=None\n"
    "sys.stdout.buffer.write(json.dumps(None if m is None else [m.group(0),*m.groups()]).encode('utf-8','surrogatepass'))\n"
)


class Found(NamedTuple):
    """What a rule found: the whole match first, then the groups (None for one that did not take part)."""

    groups: tuple

    def group(self, i: int = 0) -> str | None:
        return self.groups[i]

    @property
    def ngroups(self) -> int:
        return len(self.groups) - 1


def _found(m: re.Match | None) -> Found | None:
    return None if m is None else Found((m.group(0), *m.groups()))


def regex_probe_main() -> int:
    """The child process (`daykeeper-cli regex-probe`): PROBE_CODE as a function."""
    exec(PROBE_CODE, {})  # noqa: S102 — our own constant, the same code the source tree runs with -c
    return 0


def _probe_argv() -> list[str] | None:
    """How to start the child: this Python with the code, or the built helper. None — no
    child can be started (the built app without its helper)."""
    if not getattr(sys, "frozen", False):
        return [sys.executable.replace("pythonw.exe", "python.exe"), "-I", "-S", "-c", PROBE_CODE]
    exe = Path(sys.executable)
    if exe.stem == "daykeeper-cli":
        return [str(exe), "regex-probe"]
    # the app: its installed helper (where claudecode.helper_dir puts it; that module imports this one)
    local = os.environ.get("LOCALAPPDATA")
    helper = (Path(local) / "Daykeeper" if local else app_dir()) / "bin" / "daykeeper-cli" / ("daykeeper-cli.exe" if os.name == "nt" else "daykeeper-cli")
    return [str(helper), "regex-probe"] if helper.exists() else None


def search_with_deadline(pattern: str, text: str, flags: int = 0, deadline: float = MATCH_DEADLINE) -> Found | None | bool:
    """`re.search` in a child process that is killed at `deadline`: the match, None, or
    False when it ran out of time. A worker thread would not do — CPython's `re` holds
    the GIL for the whole search, so the thread would stall the hook with it. Without a
    child to start the text is cut to INLINE_LIMIT and searched here."""
    argv = _probe_argv()
    if argv is None:
        try:
            return _found(re.search(pattern, text[:INLINE_LIMIT], flags))
        except (re.error, RecursionError):
            return None
    payload = json.dumps({"pattern": pattern, "text": text, "flags": flags}).encode("utf-8", "surrogatepass")
    try:
        out = subprocess.run(argv, input=payload, capture_output=True, timeout=deadline, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # noqa: S603 — this very program
    except subprocess.TimeoutExpired:
        return False
    except OSError:
        return None
    if out.returncode != 0:
        return None
    try:
        groups = json.loads(out.stdout.decode("utf-8", "surrogatepass"))
    except ValueError:
        return None
    return None if groups is None else Found(tuple(groups))
RISK_NESTED, RISK_OVERLAP, RISK_ADJACENT = "nested", "overlap", "adjacent"
_SAMPLE = "".join(chr(c) for c in range(32, 127)) + "\n\t\r\u00e9\u0436\u4e2d"  # characters two one-character atoms are compared on
_ZERO_WIDTH = {"^", "$", "\\b", "\\B", "\\A", "\\Z"}


def _parse(pattern: str, i: int = 0) -> tuple[list[list[tuple]], int]:
    """A rough parse of a regex into branches of (atom, quantifier) — an atom is the text of
    a literal, an escape, a class or a dot, or a list of branches for a group. Enough to
    see the two shapes behind catastrophic backtracking; `re.compile` has checked the
    syntax before this runs."""
    branches: list[list[tuple]] = [[]]
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        atom: object
        if ch == "\\":
            atom, i = pattern[i : i + 2], i + 2
        elif ch == "[":
            j = i + 1
            if pattern[j : j + 1] == "^":
                j += 1
            if pattern[j : j + 1] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 2 if pattern[j] == "\\" else 1
            atom, i = pattern[i : j + 1], j + 1
        elif ch == "(":
            j = i + 1
            if pattern[j : j + 1] == "?":  # (?:…) (?P<n>…) (?=…) (?!…) (?<=…) (?<!…) (?i:…)
                j += 1
                if pattern[j : j + 1] == "P" and pattern[j + 1 : j + 2] == "<":
                    j = pattern.index(">", j) + 1
                else:
                    while j < n and pattern[j] not in ":)":
                        j += 1
                    if pattern[j : j + 1] == ":":
                        j += 1
            inner, i = _parse(pattern, j)
            atom = inner
        elif ch == ")":
            return branches, i + 1
        elif ch == "|":
            branches.append([])
            i += 1
            continue
        else:
            atom, i = ch, i + 1
        q = ""
        if i < n and pattern[i] in "+*?":
            q, i = pattern[i], i + 1
        elif i < n and pattern[i] == "{":
            m = re.match(r"\{\d*,?\d*\}", pattern[i:])
            if m:
                q, i = m.group(0), i + m.end()
        if q and i < n and pattern[i] == "?":  # lazy
            i += 1
        branches[-1].append((atom, q))
    return branches, i


def _repeats(q: str) -> bool:
    return q in ("+", "*") or q.startswith("{")


def _unbounded(q: str) -> bool:
    return q in ("+", "*") or (q.startswith("{") and q.endswith(",}"))


def _has_repeat(branches: list[list[tuple]]) -> bool:
    return any(_repeats(q) or (isinstance(atom, list) and _has_repeat(atom)) for branch in branches for atom, q in branch)


def _first_atoms(branch: list[tuple]) -> tuple[set[str], bool]:
    """The one-character atoms a branch can start with, and whether it can be empty."""
    first: set[str] = set()
    for atom, q in branch:
        if isinstance(atom, list):
            for sub in atom:
                inner, empty = _first_atoms(sub)
                first |= inner
                if not empty and q not in ("?", "*"):
                    return first, False
            if q in ("?", "*") or any(_first_atoms(sub)[1] for sub in atom):
                continue
            return first, False
        if atom in _ZERO_WIDTH:
            continue
        first.add(atom)
        if q not in ("?", "*"):
            return first, False
    return first, True


def _atoms_overlap(a: str, b: str) -> bool:
    try:
        ra, rb = re.compile(a), re.compile(b)
    except re.error:
        return True
    return any(ra.fullmatch(c) and rb.fullmatch(c) for c in _SAMPLE + a + b)


def _adjacent_overlap(branches: list[list[tuple]]) -> bool:
    """Two unbounded repeats in a row (a skippable atom between them allowed) that can
    match the same character — `\\d+\\d+`, `.*.*`, `.+\\d+`: quadratic on a long answer
    that does not match."""
    for branch in branches:
        carried: set[str] = set()  # the atoms of the last unbounded repeat still adjacent
        for atom, q in branch:
            if isinstance(atom, list):
                if _adjacent_overlap(atom):
                    return True
                firsts = {a for sub in atom for a in _first_atoms(sub)[0]}
                if _unbounded(q) and any(_atoms_overlap(a, b) for a in carried for b in firsts):
                    return True
                carried = firsts if _unbounded(q) else (carried if q in ("?", "*") else set())
                continue
            if atom in _ZERO_WIDTH:
                continue
            if _unbounded(q):
                if any(_atoms_overlap(a, atom) for a in carried):
                    return True
                carried = {atom} | (carried if q == "*" or q.startswith("{0") else set())  # a repeat that may be empty keeps its neighbours adjacent
            elif q != "?":
                carried = set()
    return False


def pattern_risk(pattern: str) -> str:
    """"" when the pattern is bounded on the hook's text; RISK_NESTED for a repeated group
    that repeats inside (`(a+)+`), RISK_OVERLAP for a repeated group whose alternatives
    can start alike or be empty (`(a|aa)+`, `(.|x)*`), RISK_ADJACENT for two unbounded
    repeats in a row that can match the same characters (`\\d+\\d+`, `.*.*`). On the right
    input the first two shapes run for hours and the third for minutes, and a worker
    thread cannot cut either short: `re` holds the GIL."""
    try:
        branches, _ = _parse(pattern)
    except (IndexError, ValueError):
        return ""

    def walk(branches: list[list[tuple]]) -> str:
        for branch in branches:
            for atom, q in branch:
                if not isinstance(atom, list):
                    continue
                if _repeats(q):
                    if _has_repeat(atom):
                        return RISK_NESTED
                    if len(atom) > 1:
                        firsts = [_first_atoms(sub) for sub in atom]
                        if any(empty for _f, empty in firsts):
                            return RISK_OVERLAP
                        for x in range(len(firsts)):
                            for y in range(x + 1, len(firsts)):
                                if any(_atoms_overlap(a, b) for a in firsts[x][0] for b in firsts[y][0]):
                                    return RISK_OVERLAP
                found = walk(atom)
                if found:
                    return found
        return ""

    return walk(branches) or (RISK_ADJACENT if _adjacent_overlap(branches) else "")


def nested_quantifier(pattern: str) -> bool:
    return pattern_risk(pattern) == RISK_NESTED


@dataclass
class Rule:
    """“If the command (answer) has `pattern` — write `status` with an item taken by `take`.”
    `also` — a second piece of text the answer must contain (e.g. a success word).
    A regex pattern is bounded: at most PATTERN_LIMIT characters, none of the shapes
    behind catastrophic backtracking (pattern_risk), searched in the first SCAN_LIMIT
    characters, and on a text longer than INLINE_LIMIT in a child process with a deadline
    — a rule written by accident as `^(a+)+$` once made every tool call of the assistant
    wait the full hook timeout."""

    name: str = ""
    status: str = "note"
    pattern: str = ""
    where: str = WHERE_COMMAND
    regex: bool = False
    take: str = TAKE_TEXT
    text: str = ""
    also: str = ""
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> Rule:
        known = {f.name for f in fields(cls)}
        rule = cls(**{k: v for k, v in data.items() if k in known})
        # settings.json may be edited by hand: a number where a text belongs must not kill the hook
        for name in ("name", "pattern", "text", "also"):
            setattr(rule, name, "" if getattr(rule, name) is None else str(getattr(rule, name)))
        rule.status = str(rule.status).strip().lower() or "note"
        rule.where = rule.where if rule.where in WHERES else WHERE_COMMAND
        rule.take = rule.take if rule.take in TAKES else TAKE_TEXT
        rule.regex, rule.enabled = bool(rule.regex), bool(rule.enabled)
        return rule

    def to_dict(self) -> dict:
        return asdict(self)

    def problem(self) -> str:
        """Why the rule cannot be saved (a UI key), or ""."""
        if not self.pattern.strip():
            return "Say what to look for"
        if len(self.pattern) > PATTERN_LIMIT:
            return "The pattern is too long (500 characters at most)"
        if self.regex:
            try:
                re.compile(self.pattern)
            except re.error:
                return "The pattern is not a valid regular expression"
            risk = pattern_risk(self.pattern)
            if risk == RISK_NESTED:
                return "The pattern repeats a repeated group, like (a+)+: on a long answer it would run for minutes"
            if risk == RISK_OVERLAP:
                return "The pattern repeats a choice whose options can start alike, like (a|aa)+: on a long answer it would run for minutes"
            if risk == RISK_ADJACENT:
                return "The pattern has two repeats in a row that can match the same characters, like \\d+\\d+: on a long answer it would run for minutes"
        if self.take == TAKE_TEXT and not self.text.strip():
            return "Say what to write"
        return ""

    def _find(self, text: str) -> Found | None:
        text = text[:SCAN_LIMIT]
        if not self.regex:
            return _found(re.search(re.escape(self.pattern), text, re.IGNORECASE))
        if len(self.pattern) > PATTERN_LIMIT or pattern_risk(self.pattern):
            return None  # saved by hand into settings.json: the same rule as in the editor
        if len(text) <= INLINE_LIMIT:
            try:
                return _found(re.search(self.pattern, text))
            except (re.error, RecursionError):
                return None
        found = search_with_deadline(self.pattern, text)
        if found is False:
            slow_rules.append(self.name or self.pattern)
        return found if isinstance(found, Found) else None

    def apply(self, call: Call, links: Links) -> list[tuple[str, str]]:
        if not self.enabled or not self.pattern:
            return []
        texts = {WHERE_COMMAND: [call.command], WHERE_RESPONSE: [call.response], WHERE_ANY: [call.command, call.response]}[self.where]
        m = next((m for m in (self._find(t) for t in texts) if m), None)
        if m is None or (self.also and self.also.lower() not in call.response.lower()):
            return []
        both = f"{call.command}\n{call.response}"
        if self.take == TAKE_TEXT:
            item = self.text.strip()
        elif self.take == TAKE_MATCH:
            item = ((m.group(1) if m.ngroups else m.group(0)) or "").strip()
            if looks_secret(item):
                return []  # the journal is plain text: a pattern that caught a token must not write it there
        elif self.take == TAKE_KEY:
            key = ISSUE_KEY_RE.search(both)
            item = key.group(1) if key else ""
        else:
            mr = MR_URL_RE.search(both)
            pr = PR_URL_RE.search(both)
            short = re.search(r"(?<![\w/])!(\d+)\b", both)
            item = _mr_item(mr, links) if mr else pr.group(0) if pr else f"!{short.group(1)}" if short else ""
        return [(self.status, item)] if item else []


# ---- config


@dataclass
class HookConfig:
    presets: dict[str, bool] = field(default_factory=lambda: {p: True for p in PRESET_IDS})
    rules: list[Rule] = field(default_factory=list)
    only_folders: list[str] = field(default_factory=list)  # empty — every folder
    skip_folders: list[str] = field(default_factory=list)

    @classmethod
    def from_settings(cls, settings: dict) -> HookConfig:
        raw = settings.get("hook")
        if not isinstance(raw, dict):
            raw = {}
        presets = {p: True for p in PRESET_IDS}
        if isinstance(raw.get("presets"), dict):
            presets.update({k: bool(v) for k, v in raw["presets"].items() if k in PRESET_IDS})
        rules = [Rule.from_dict(r) for r in raw.get("rules") or [] if isinstance(r, dict)]

        def folders(key: str) -> list[str]:
            return [str(f).strip() for f in raw.get(key) or [] if str(f).strip()]

        return cls(presets, rules, folders("only_folders"), folders("skip_folders"))

    def to_settings(self) -> dict:
        return {"hook": {
            "presets": dict(self.presets),
            "rules": [r.to_dict() for r in self.rules],
            "only_folders": list(self.only_folders),
            "skip_folders": list(self.skip_folders),
        }}

    def folder_allowed(self, cwd: str) -> bool:
        """`only_folders` (when set) must hold the folder; `skip_folders` must not."""
        if not cwd:
            return True
        here = _folder_key(cwd)

        def under(base: str) -> bool:
            b = _folder_key(base)
            return here == b or here.startswith(b + "/")

        if any(under(f) for f in self.skip_folders):
            return False
        return not self.only_folders or any(under(f) for f in self.only_folders)


def _folder_key(path: str) -> str:
    """Comparable form of a folder: forward slashes, no trailing slash, case-folded, and Git
    Bash's /e/repos written as e:/repos."""
    p = path.strip().replace("\\", "/").rstrip("/").casefold()
    m = re.match(r"^/([a-z])(/|$)", p)
    return f"{m.group(1)}:{p[2:]}" if m else p


def match(payload: dict, config: HookConfig, links: Links, own_mr: Callable[[str], bool | None] | None = None) -> list[Hit]:
    """Journal events in one tool call, each with the rule that found it. Duplicates dropped."""
    call = Call.from_payload(payload)
    slow_rules.clear()
    if any(marker in call.command for marker in SELF_TEST_MARKERS):
        return []
    ctx = Ctx(call, links, own_mr)
    hits: list[Hit] = []
    for preset in PRESETS:
        if config.presets.get(preset.id, True):
            hits += [Hit(s, i, preset.id) for s, i in preset.find(ctx)]
    for rule in config.rules:
        hits += [Hit(s, i, rule.name or rule.pattern) for s, i in rule.apply(call, links)]
    first: dict[tuple[str, str], Hit] = {}
    for h in hits:  # the first rule that found a line keeps it
        first.setdefault((h.status, h.item), h)
    return list(first.values())


def classify(payload: dict, links: Links, own_mr: Callable[[str], bool | None] | None = None, config: HookConfig | None = None) -> list[tuple[str, str]]:
    """(status, item) pairs: `match` without the rule names."""
    return [(h.status, h.item) for h in match(payload, config or HookConfig(), links, own_mr)]


def test_payload(command: str, response: str, tool: str = "Bash") -> dict:
    """The "Check" box: what a command and its answer look like to the hook."""
    if tool in COMMAND_TOOLS:
        return {"tool_name": tool, "tool_input": {"command": command}, "tool_response": response}
    try:
        tool_input = json.loads(command)
    except ValueError:
        tool_input = command
    return {"tool_name": tool, "tool_input": tool_input, "tool_response": response}
