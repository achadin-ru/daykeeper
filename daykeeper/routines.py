"""Daily routine: quick-launch tiles for scripts, commands, links, folders and files, with an
optional automatic start when the app is unlocked or at a time of day.

Stored inside the encrypted vault (key `routines`): the vault is authenticated, so nobody
can slip a command into the auto-start list without the master password. Pure (no
tkinter); the tab runs what `launch_plan` describes.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shlex
import shutil
import struct
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .settings import number

KINDS = ("script", "command", "url", "folder", "file")
KIND_ICONS = {"script": "📜", "command": "⌨", "url": "🌐", "folder": "📁", "file": "📄"}
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")  # UI keys
RUN_LOG_KEEP = 100
# Files a shortcut may point to that are programs to run (and wait for), not documents to open.
RUNNABLE = {".exe", ".com", ".bat", ".cmd", ".ps1", ".py", ".pyw", ".sh", ".vbs", ".js", ".wsf"}
# Programs that run scripts: started with no arguments they do nothing and end at once.
INTERPRETERS = {"powershell.exe", "pwsh.exe", "cmd.exe", "python.exe", "pythonw.exe", "py.exe", "wscript.exe", "cscript.exe", "bash.exe", "wsl.exe"}


def _new_id() -> str:
    return secrets.token_hex(6)


@dataclass
class Schedule:
    on_unlock: bool = False  # run once each time the vault is unlocked
    at: str = ""  # "HH:MM" — run at this time of day; "" — not by time
    days: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])  # 0 = Monday

    def valid_time(self) -> tuple[int, int] | None:
        try:
            hh, mm = self.at.strip().split(":")
            h, m = int(hh), int(mm)
        except ValueError:
            return None
        return (h, m) if 0 <= h < 24 and 0 <= m < 60 else None

    def due(self, now: datetime, last_run: float) -> bool:
        """The time of day has come today (on a chosen weekday) and the routine has not run
        since. If the app was closed at that time it runs once when opened later that day."""
        hm = self.valid_time()
        if hm is None or now.weekday() not in self.days:
            return False
        slot = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        return now >= slot and last_run < slot.timestamp()

    def next_run(self, now: datetime) -> datetime | None:
        hm = self.valid_time()
        if hm is None or not self.days:
            return None
        for ahead in range(8):
            day = now + timedelta(days=ahead)
            slot = day.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
            if slot.weekday() in self.days and slot > now:
                return slot
        return None

    def to_dict(self) -> dict:
        return {"on_unlock": self.on_unlock, "at": self.at, "days": list(self.days)}

    @classmethod
    def from_dict(cls, d) -> Schedule:
        if not isinstance(d, dict):
            return cls()
        days = sorted({int(x) for x in d.get("days", [0, 1, 2, 3, 4]) if str(x).isdigit() and 0 <= int(x) <= 6})
        return cls(bool(d.get("on_unlock", False)), str(d.get("at", "")), days)


@dataclass
class Routine:
    name: str = ""
    kind: str = "script"
    target: str = ""  # a path, a shell command or a URL
    args: str = ""  # script arguments, split like a shell does
    cwd: str = ""  # working folder; "" — the folder of the script
    terminal: bool = False  # run in a separate console window instead of the output window
    icon: str = ""
    color: str = ""
    schedule: Schedule = field(default_factory=Schedule)
    order: int = 0
    id: str = field(default_factory=_new_id)
    last_run: float = 0.0
    last_code: int | None = None  # exit code of the last run; None — still running or unknown
    # sha256 of the script file (and of the .lnk target) as it was when the user last saved the
    # tile or ran it by hand: an automatic run starts only the version the user accepted —
    # the vault authenticates the path, this authenticates the content
    target_hash: str = ""

    def avatar(self) -> str:
        return self.icon.strip()[:2] or KIND_ICONS.get(self.kind, "▶")

    def mark_started(self, now: float, code: int | None = None) -> None:
        """A run began at `now`; `code` is known at once for a link or a file (0), None
        while a script runs."""
        self.last_run = now
        self.last_code = code

    def mark_finished(self, code: int | None) -> None:
        self.last_code = code

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "kind": self.kind, "target": self.target, "args": self.args,
            "cwd": self.cwd, "terminal": self.terminal, "icon": self.icon, "color": self.color,
            "schedule": self.schedule.to_dict(), "order": self.order, "last_run": self.last_run, "last_code": self.last_code,
            "target_hash": self.target_hash,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Routine:
        kind = str(d.get("kind", "script"))
        code = d.get("last_code")
        return cls(
            name=str(d.get("name", "")), kind=kind if kind in KINDS else "script", target=str(d.get("target", "")),
            args=str(d.get("args", "")), cwd=str(d.get("cwd", "")), terminal=bool(d.get("terminal", False)),
            icon=str(d.get("icon", "")), color=str(d.get("color", "")), schedule=Schedule.from_dict(d.get("schedule")),
            order=number(int, d.get("order"), 0), id=str(d.get("id") or _new_id()), last_run=number(float, d.get("last_run"), 0.0),
            last_code=int(code) if isinstance(code, int) else None, target_hash=str(d.get("target_hash") or ""),
        )


class Routines:
    def __init__(self, items: list[Routine] | None = None):
        self.items: list[Routine] = list(items or [])

    def ordered(self) -> list[Routine]:
        return sorted(self.items, key=lambda r: r.order)

    def add(self, **kw) -> Routine:
        r = Routine(order=max((x.order for x in self.items), default=-1) + 1, **kw)
        self.items.append(r)
        return r

    def remove(self, r: Routine) -> None:
        if r in self.items:
            self.items.remove(r)

    def upsert(self, r: Routine | None, fields: dict, now: datetime) -> Routine:
        """Create (`r` is None) or change a routine from the editor's fields. A time of day
        that has already passed today does not fire today: a new routine, or one whose
        time was changed, counts as run at `now` and waits for the next day."""
        schedule: Schedule = fields["schedule"]
        if r is None:
            r = self.add(**fields)
            if schedule.at and schedule.due(now, 0):
                r.last_run = now.timestamp()
            return r
        old_at = r.schedule.at
        for key, value in fields.items():
            setattr(r, key, value)
        if schedule.at != old_at and schedule.due(now, r.last_run):
            r.last_run = now.timestamp()
        return r

    def get(self, rid: str) -> Routine | None:
        return next((r for r in self.items if r.id == rid), None)

    def move(self, r: Routine, delta: int) -> bool:
        order = self.ordered()
        i = order.index(r)
        j = i + delta
        if not 0 <= j < len(order):
            return False
        for k, x in enumerate(order):
            x.order = k
        r.order, order[j].order = order[j].order, r.order
        return True

    def on_unlock(self) -> list[Routine]:
        return [r for r in self.ordered() if r.schedule.on_unlock]

    def due(self, now: datetime) -> list[Routine]:
        return [r for r in self.ordered() if r.schedule.due(now, r.last_run)]

    def to_list(self) -> list[dict]:
        return [r.to_dict() for r in self.items]

    @classmethod
    def from_list(cls, rows) -> Routines:
        return cls([Routine.from_dict(x) for x in rows or [] if isinstance(x, dict)])


# ---- the accepted script

SCRIPT_UNKNOWN, SCRIPT_CHANGED, SCRIPT_UNREADABLE = "unknown", "changed", "unreadable"


def script_digest(r: Routine, shortcut=None) -> str | None:
    """sha256 of what a script routine runs: the file at the target and, for a shortcut,
    the file it points to. None when there is nothing to hash — another kind of routine
    (a command is text inside the vault; a link, a folder, a file open by their program),
    or a file that cannot be read."""
    if r.kind != "script":
        return None
    target = r.target.strip()
    paths = [target]
    if target.lower().endswith(".lnk"):
        sc = (shortcut or resolve_shortcut)(target)
        if sc is not None:
            paths.append(sc.target)
    h = hashlib.sha256()
    for p in paths:
        try:
            h.update(Path(p).read_bytes())
        except OSError:
            return None
        h.update(b"\0")
    return h.hexdigest()


def check_script(r: Routine, shortcut=None) -> str:
    """"" when the script is the one the user accepted; SCRIPT_UNKNOWN when none was
    accepted yet (a tile from before this check, or never run), SCRIPT_CHANGED when the file
    differs from the accepted one, SCRIPT_UNREADABLE when it cannot be read."""
    if r.kind != "script":
        return ""
    digest = script_digest(r, shortcut)
    if digest is None:
        return SCRIPT_UNREADABLE
    if not r.target_hash:
        return SCRIPT_UNKNOWN
    return "" if digest == r.target_hash else SCRIPT_CHANGED


def accept_script(r: Routine, shortcut=None) -> None:
    """The user saved the tile or ran it by hand: the file as it is now is the accepted one."""
    r.target_hash = script_digest(r, shortcut) or ""


def risky_location(path: str, home: Path | None = None) -> str:
    """Where a script may be swapped by someone else: "share" for a network path,
    "downloads" for the Downloads folder, "outside" for a file outside the user's profile;
    "" for a file under the profile."""
    p = path.strip().strip("\"'")
    if not p:
        return ""
    if p.startswith(("\\\\", "//")):
        return "share"
    home = home or Path.home()
    try:
        full = Path(p).resolve()
        home = home.resolve()
    except OSError:
        return ""
    if full == home / "Downloads" or (home / "Downloads") in full.parents:
        return "downloads"
    if full != home and home not in full.parents:
        return "outside"
    return ""


@dataclass
class LaunchPlan:
    """How to start a routine: open (a URL or a path with the system app) or run a process."""

    action: str  # "open_url" | "open_path" | "run"
    argv: list[str] | None = None  # for "run" without a shell
    shell: str = ""  # for "run" through the shell
    cwd: str = ""
    target: str = ""  # for the open actions
    problem: str = ""  # why it cannot start ("" — it can)


@dataclass
class Shortcut:
    """What a Windows shortcut (.lnk) starts."""

    target: str
    args: str = ""
    cwd: str = ""


def read_shortcut(data: bytes, expand=os.path.expandvars) -> Shortcut | None:
    """Parse a .lnk file (the MS-SHLLINK format): the target path, its arguments and the
    working folder. None — not a shortcut, or one that points to no file (a Control Panel
    item, a Store app)."""
    if len(data) < 0x4C or struct.unpack_from("<I", data, 0)[0] != 0x4C:
        return None
    flags = struct.unpack_from("<I", data, 0x14)[0]
    unicode = bool(flags & 0x80)
    pos = 0x4C
    if flags & 0x01:  # an ID list: the shell's own path form, skipped
        pos += 2 + struct.unpack_from("<H", data, pos)[0]
    target = ""
    if flags & 0x02:  # LinkInfo: the local or network path
        start = pos
        size, head, info_flags, _vol, local_at, net_at, suffix_at = struct.unpack_from("<7I", data, pos)

        def cstr(at: int) -> str:
            end = data.index(b"\x00", at)
            return data[at:end].decode("mbcs" if sys.platform == "win32" else "latin-1", errors="replace")

        def wstr(at: int) -> str:
            end = at
            while data[end : end + 2] != b"\x00\x00":
                if end + 2 > len(data):  # no terminator: a broken file, not an endless loop
                    raise ValueError("unterminated string in a shortcut")
                end += 2
            return data[at:end].decode("utf-16-le", errors="replace")

        suffix = cstr(start + suffix_at) if suffix_at else ""
        if head >= 0x24:
            local_w, suffix_w = struct.unpack_from("<2I", data, pos + 28)
            if suffix_w:
                suffix = wstr(start + suffix_w)
            if info_flags & 1 and local_w:
                target = wstr(start + local_w) + suffix
        if not target and info_flags & 1 and local_at:
            target = cstr(start + local_at) + suffix
        if not target and info_flags & 2 and net_at:
            net_name_at = struct.unpack_from("<I", data, start + net_at + 8)[0]
            target = cstr(start + net_at + net_name_at).rstrip("\\") + ("\\" + suffix if suffix else "")
        pos = start + size
    strings = []
    for bit in (0x04, 0x08, 0x10, 0x20, 0x40):  # name, relative path, working folder, arguments, icon
        if not flags & bit:
            strings.append("")
            continue
        count = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        n = count * 2 if unicode else count
        strings.append(data[pos : pos + n].decode("utf-16-le" if unicode else "latin-1", errors="replace"))
        pos += n
    _name, relative, cwd, args, _icon = strings
    while not target and pos + 8 <= len(data):  # extra blocks: a target written with %VARIABLES%
        size, sig = struct.unpack_from("<2I", data, pos)
        if size < 8:
            break
        if sig == 0xA0000001 and size >= 0x314:
            raw = data[pos + 0x10C : pos + 0x314]
            end = next((i for i in range(0, len(raw) - 1, 2) if raw[i : i + 2] == b"\x00\x00"), len(raw))
            target = expand(raw[:end].decode("utf-16-le", errors="replace"))
        pos += size
    if not target and relative:
        target = relative  # relative to the shortcut; the caller resolves it
    if not target:
        return None
    return Shortcut(target=target, args=args, cwd=expand(cwd) if cwd else "")


def resolve_shortcut(path: str) -> Shortcut | None:
    """The shortcut at `path`, its relative target made absolute."""
    try:
        sc = read_shortcut(Path(path).read_bytes())
    except (OSError, ValueError, IndexError, struct.error):
        return None
    if sc is not None and not os.path.isabs(sc.target):
        sc.target = os.path.normpath(os.path.join(os.path.dirname(path), sc.target))
    return sc


def interpreter_for(path: str, platform: str = sys.platform, which=shutil.which) -> list[str] | None:
    """The command line prefix that runs a script of this type; None — run it directly."""
    ext = Path(path).suffix.lower()
    if ext == ".py":
        if platform == "win32" and which("py"):
            return ["py", "-3"]
        return [which("python3") or which("python") or "python"]
    if ext == ".ps1":
        return [which("pwsh") or "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    if ext in (".bat", ".cmd"):
        return ["cmd", "/c"]
    if ext == ".sh":
        return [which("bash") or "sh"]
    return None


def split_args(args: str, platform: str = sys.platform) -> list[str]:
    if not args.strip():
        return []
    posix = platform != "win32"
    try:
        parts = shlex.split(args, posix=posix)
    except ValueError:
        return args.split()
    if not posix:  # Windows mode keeps the quotes in the token; the script must not see them
        parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]
    return parts


def _shortcut_plan(r: Routine, target: str, platform: str, which, shortcut) -> LaunchPlan | None:
    """A shortcut to a program or a script runs what it points to, with its arguments, and
    the run lasts as long as that program does; os.startfile would return at once."""
    sc = shortcut(target)
    if sc is None:
        if platform == "win32":  # unreadable: let Windows start it, and still wait for it
            # through the shell with the path quoted: as a bare argument, `a&b.lnk` was two commands to cmd
            return LaunchPlan("run", shell=f'start "" /wait "{target}"', cwd=str(Path(target).parent))  # noqa: S604 — the user's own shortcut from the vault, quoted
        return None
    if Path(sc.target).suffix.lower() not in RUNNABLE:
        return None  # a shortcut to a document or a folder: just open it
    prefix = interpreter_for(sc.target, platform, which) or []
    argv = prefix + [sc.target] + split_args(sc.args, platform) + split_args(r.args, platform)
    return LaunchPlan("run", argv=argv, cwd=r.cwd or sc.cwd or str(Path(sc.target).parent))


def launch_plan(r: Routine, platform: str = sys.platform, which=shutil.which, exists=os.path.exists, shortcut=resolve_shortcut) -> LaunchPlan:
    target = r.target.strip()
    if not target:
        return LaunchPlan("run", problem="empty")
    if r.kind in ("script", "file") and target.lower().endswith(".lnk") and exists(target):
        plan = _shortcut_plan(r, target, platform, which, shortcut)
        if plan is not None:
            return plan
    if r.kind == "url":
        url = target if "://" in target else "https://" + target
        return LaunchPlan("open_url", target=url)
    if r.kind in ("folder", "file"):
        if not exists(target):
            return LaunchPlan("open_path", target=target, problem="missing")
        return LaunchPlan("open_path", target=target)
    if r.kind == "command":
        return LaunchPlan("run", shell=target + (" " + r.args if r.args.strip() else ""), cwd=r.cwd or str(Path.home()))
    # script
    if not exists(target):
        return LaunchPlan("run", problem="missing", target=target)
    if _base_name(target).lower() in INTERPRETERS and not r.args.strip():
        return LaunchPlan("run", problem="interpreter", target=target)
    prefix = interpreter_for(target, platform, which)
    argv = (prefix or []) + [target] + split_args(r.args, platform)
    return LaunchPlan("run", argv=argv, cwd=r.cwd or str(Path(target).parent))


def _base_name(path: str) -> str:
    """The last part of a path written for any system (a vault moves between them)."""
    return re.split(r"[\\/]", path.strip().strip("\"'").rstrip("\\/"))[-1]


def subtitle(r: Routine) -> str:
    """What a tile shows under the name: the script and the program that runs it, the site,
    the command — not the full path to an interpreter, which says nothing in a narrow tile."""
    target = r.target.strip()
    if r.kind == "url":
        return re.sub(r"^[a-z]+://", "", target, flags=re.I).rstrip("/")
    if r.kind == "command":
        return target
    name = _base_name(target)
    if r.kind == "script":
        paths = [a for a in split_args(r.args, "win32") if "\\" in a or "/" in a or re.search(r"\.\w{1,5}$", a)]
        if paths and _base_name(paths[-1]):
            return f"{_base_name(paths[-1])} · {Path(name).stem or name}"
    return name


def describe_schedule(s: Schedule, day_names: list[str]) -> str:
    """“09:30 Mon–Fri” style text for a tile; day_names are the translated WEEKDAYS."""
    parts = []
    if s.valid_time():
        if sorted(s.days) == list(range(7)):
            days = ""
        elif sorted(s.days) == [0, 1, 2, 3, 4]:
            days = f"{day_names[0]}–{day_names[4]}"
        else:
            days = ", ".join(day_names[d] for d in sorted(s.days))
        parts.append(f"{s.at} {days}".strip())
    return " · ".join(parts)


def now_local() -> datetime:
    return datetime.fromtimestamp(time.time())
