"""The console helper: `daykeeper-cli.exe` in the built app, `inbox.py` and
`hooks/journal-inbox.py` in the source tree. Two jobs, no window and no vault:

    daykeeper-cli pass APP-1234 APP-5678      # drop an item into the journal inbox
    daykeeper-cli note "nightly run triage"
    daykeeper-cli hook                        # Claude Code PostToolUse hook: JSON on stdin

Imports only settings, i18n, journal and hookrules: no tkinter and no cryptography, so the
helper starts fast — the hook runs after every command Claude makes."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .hookrules import MR_URL_RE, HookConfig, looks_secret, match, regex_probe_main, slow_rules
from .journal import append_inbox, inbox_path_for, repo_host
from .journal_layout import Layout, configure as configure_layout, resolve_known
from .settings import Links, app_dir, read_settings, settings_path_for

LOG_NAME = "hook-log.jsonl"
LOG_LIMIT = 256 * 1024  # past this the older half goes
CACHE_NAME = "hook-cache.json"  # own GitLab login and MR authors: only reviews of others' MRs count


_data_dir: Path | None = None  # `--data`: the vault's folder when it is not the default one


def data_dir() -> Path:
    return _data_dir or app_dir()


def vault_path() -> Path:
    return data_dir() / "vault.pkv"  # same as default_vault_path(), without importing vault.py


def log_path() -> Path:
    return data_dir() / LOG_NAME


# ---- inbox


def inbox_main(argv: list[str] | None = None, prog: str | None = None) -> int:
    layout = Layout.from_settings(read_settings(settings_path_for(vault_path())))
    configure_layout(layout)
    words = [w for w, _l in layout.statuses()]
    parser = argparse.ArgumentParser(prog=prog, description="Daykeeper journal inbox")
    parser.add_argument("status", type=str.lower, help=" | ".join(words))
    parser.add_argument("items", nargs="+", help="issue keys (APP-1234), tracker links, MR numbers or the note text")
    parser.add_argument("--date", help="day (YYYY-MM-DD), today by default")
    parser.add_argument("--note", default="", help="short remark")
    parser.add_argument("--inbox", type=Path, help="inbox file (next to the vault by default)")
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--work", action="store_true", help="count as work whatever folder this runs in")
    where.add_argument("--personal", action="store_true", help="a personal project: keep it out of the journal")
    args = parser.parse_args(argv)
    if resolve_known(args.status, layout) is None:
        parser.error(f"status must be one of: {', '.join(words)}")
    origin = "" if args.work else "personal" if args.personal else None
    path = args.inbox or inbox_path_for(vault_path())
    secret = [x for x in [*args.items, args.note] if looks_secret(x)]
    if secret:  # the inbox and the journal are plain text: a token typed into a note would sit there in the clear
        parser.error("this looks like a token or a key, and the journal is not the place for one: " + ", ".join(w[:8] + "…" for w in secret))
    n = append_inbox(path, args.status, args.items, date=args.date, note=args.note, origin=origin)
    print(f"inbox: +{n} ({args.status}) -> {path}")
    return 0 if n else 1


# ---- hook: is this MR mine?


class OwnMr:
    """Am I the MR author? Asked through `glab`, remembered in hook-cache.json. None when
    GitLab is not configured or did not answer (the event stays)."""

    BUDGET = 8.0  # s for every glab call of one hook run: Claude Code gives the hook 15

    def __init__(self, links: Links, cache: Path):
        self.links, self.cache = links, cache
        self._deadline = time.monotonic() + self.BUDGET

    def project_and_number(self, item: str) -> tuple[str, str]:
        m = MR_URL_RE.search(item)
        return (m.group(1), m.group(2)) if m else (self.links.gitlab_project, item.lstrip("!"))

    def _glab(self, path: str) -> dict | None:
        glab = shutil.which("glab")
        left = self._deadline - time.monotonic()
        if not glab or not self.links.gitlab_host or left < 1:
            return None
        try:
            out = subprocess.run(  # noqa: S603 — the user's own glab, found on PATH
                [glab, "api", "--hostname", self.links.gitlab_host, path],
                capture_output=True, text=True, timeout=left, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            data = json.loads(out.stdout) if out.returncode == 0 else None
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _read(self) -> dict:
        try:
            data = json.loads(self.cache.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def __call__(self, item: str) -> bool | None:
        project, number = self.project_and_number(item)
        if not project or not self.links.gitlab_host:
            return None
        cache = self._read()
        me = cache.get("me")
        if not me:
            me = (self._glab("user") or {}).get("username")
            if not me:
                return None
            cache["me"] = me
        key = f"{project}!{number}"
        authors = cache.setdefault("authors", {})
        author = authors.get(key)
        if not author:
            mr = self._glab(f"projects/{project.replace('/', '%2F')}/merge_requests/{number}")
            author = ((mr or {}).get("author") or {}).get("username")
            if not author:
                return None
            authors[key] = author
        try:
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            self.cache.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return author == me


# ---- hook: the log the app shows under "Recent triggers"


def write_log(path: Path, row: dict) -> None:
    """One JSON line. Never the command or the answer: they can hold tokens."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > LOG_LIMIT:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            path.write_text("\n".join(lines[len(lines) // 2 :]) + "\n", encoding="utf-8")
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_log(path: Path, last: int = 20) -> list[dict]:
    """The newest `last` rows, newest first; broken lines are skipped."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    rows = []
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
            if len(rows) >= last:
                break
    return rows


def run_hook(payload: dict, settings: dict, inbox: Path, log: Path, own_mr=None) -> list:
    """Match one tool call, write what it found to the inbox and a line to the log."""
    links = Links.from_settings(settings)
    config = HookConfig.from_settings(settings)
    layout = Layout.from_settings(settings)
    configure_layout(layout)
    found = match(payload, config, links, own_mr)
    hits = [h for h in found if resolve_known(h.status, layout) is not None]  # another layout's line is kept hidden
    cwd = str(payload.get("cwd") or "")
    if slow_rules:  # a user regex ran out of time on this answer: the app shows it under "Recent triggers"
        write_log(log, {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "tool": str(payload.get("tool_name", "")), "cwd": cwd, "events": [], "slow_rules": list(slow_rules)})
    if not found:
        return []
    row = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "tool": str(payload.get("tool_name", "")), "cwd": cwd,
           "events": [[h.status, h.item, h.rule] for h in hits]}
    if not hits:
        row["events"], row["skipped"] = [[h.status, h.item, h.rule] for h in found], "layout"
        write_log(log, row)
        return []
    if not config.folder_allowed(cwd):
        row["skipped"] = "folder"
        write_log(log, row)
        return []
    origin = repo_host(cwd) if cwd else None
    for h in hits:
        append_inbox(inbox, h.status, [h.item], note="hook", origin=origin)
    write_log(log, row)
    return hits


def hook_main() -> int:
    """Never blocks Claude: any trouble ends in the log and exit code 0."""
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig", "replace"))  # UTF-8 whatever the console code page; a BOM is dropped
    except Exception:  # noqa: BLE001 — bad JSON, a closed pipe, or a RecursionError on `[` × 200 000: none may show a traceback to the user
        return 0
    if not isinstance(payload, dict):
        return 0
    log = log_path()
    try:
        settings = read_settings(settings_path_for(vault_path()))
        inbox = Path(os.environ["DAYKEEPER_INBOX"]) if os.environ.get("DAYKEEPER_INBOX") else inbox_path_for(vault_path())
        run_hook(payload, settings, inbox, log, own_mr=OwnMr(Links.from_settings(settings), data_dir() / CACHE_NAME))
    except Exception as exc:  # noqa: BLE001 — the hook must never break the tool
        write_log(log, {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "error": repr(exc)[:300]})
    return 0


def main(argv: list[str] | None = None) -> int:
    """`[--data FOLDER] hook` or `[--data FOLDER] <status> <items>…`."""
    global _data_dir
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--data"] and len(argv) > 1:
        _data_dir, argv = Path(argv[1]), argv[2:]
    if argv[:1] == ["hook"]:
        return hook_main()
    if argv[:1] == ["regex-probe"]:
        return regex_probe_main()
    return inbox_main(argv, prog="daykeeper-cli")
