"""Non-secret settings next to the vault: `settings.json` (window size, language, tracker
and GitLab links). Read by the app and by the hook, so this module has no tkinter and no
third-party imports."""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

APP_NAME = "Daykeeper"
LEGACY_APP_NAME = "PassKeeper"  # data folder of the previous name; renamed on first start
SETTINGS_NAME = "settings.json"


def data_base_dir(platform: str = sys.platform, env: Mapping[str, str] = os.environ, home: Path | None = None) -> Path:
    """Where per-user application data goes on this system (the app folder is created inside).

    Windows — %APPDATA%; macOS — ~/Library/Application Support; elsewhere — $XDG_DATA_HOME
    or ~/.local/share. Pure, so the choice is unit-tested without touching the disk."""
    home = home or Path.home()
    if platform == "win32":
        return Path(env.get("APPDATA") or home)
    if platform == "darwin":
        return home / "Library" / "Application Support"
    return Path(env.get("XDG_DATA_HOME") or home / ".local" / "share")


def app_dir() -> Path:
    """Data folder: %APPDATA%\\Daykeeper on Windows, ~/Library/Application Support/Daykeeper
    on macOS, ~/.local/share/Daykeeper elsewhere.

    A folder left by the previous app name is renamed in place, so the vault, backups and
    settings carry over. If the rename fails (folder in use), the old folder keeps working."""
    base = data_base_dir()
    new, old = base / APP_NAME, base / LEGACY_APP_NAME
    if not new.exists() and old.exists():
        try:
            old.rename(new)
        except OSError:
            return old
    return new


def settings_path_for(vault_path: Path) -> Path:
    return Path(vault_path).parent / SETTINGS_NAME


_SURROGATES = re.compile("[\ud800-\udfff]")


def number(kind, value, default):
    """A number from a file edited by hand or written by another version: a wrong type or
    shape gives the default, not an import that dies on a TypeError."""
    try:
        return kind(value)
    except (TypeError, ValueError, OverflowError):
        return default


def readable(text: str) -> str:
    """Text for a plain file (settings.json, the inbox, an export): an unpaired surrogate
    — Python keeps one in a str, strict UTF-8 refuses it — becomes U+FFFD, so the file is
    written, stays valid UTF-8 and reads back. The vault itself carries them through."""
    return _SURROGATES.sub("\ufffd", text)


def read_settings(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def private(path: Path, folder: bool = False) -> None:
    """Owner-only access (0600 for a file, 0700 for a folder) where the system has POSIX
    modes; on Windows the per-user ACL of the profile folder already does this."""
    if os.name == "posix":
        try:
            os.chmod(path, 0o700 if folder else 0o600)
        except OSError:
            pass


def write_settings(path: Path, data: dict) -> None:
    path = Path(path)
    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        private(path.parent, folder=True)
    tmp = path.with_suffix(path.suffix + ".tmp")  # a crash mid-write must not leave half a file
    # fsync before the rename: a power loss right after left an empty settings.json (read as {})
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(readable(json.dumps(data, ensure_ascii=False, indent=2)))
        fh.flush()
        os.fsync(fh.fileno())
    private(tmp)
    os.replace(tmp, path)


@dataclass(frozen=True)
class Links:
    """Where issue keys and MR numbers point. Empty — they stay plain text.

    jira_url       — tracker address, e.g. https://company.atlassian.net
    gitlab_url     — GitLab address, e.g. https://gitlab.example.com
    gitlab_project — own repository (`group/repo`): its MRs are written short, as `!42`,
                     others as a full link."""

    jira_url: str = ""
    gitlab_url: str = ""
    gitlab_project: str = ""

    @classmethod
    def from_settings(cls, settings: dict) -> Links:
        def clean(key: str) -> str:
            return str(settings.get(key) or "").strip().rstrip("/")

        return cls(jira_url=clean("jira_url"), gitlab_url=clean("gitlab_url"), gitlab_project=clean("gitlab_project").strip("/"))

    def to_settings(self) -> dict:
        return {"jira_url": self.jira_url, "gitlab_url": self.gitlab_url, "gitlab_project": self.gitlab_project}

    @property
    def jira_browse(self) -> str:
        return f"{self.jira_url}/browse/" if self.jira_url else ""

    @property
    def mr_browse(self) -> str:
        return f"{self.gitlab_url}/{self.gitlab_project}/-/merge_requests/" if self.gitlab_url and self.gitlab_project else ""

    @property
    def work_hosts(self) -> set[str]:
        """Hosts of the tracker and GitLab: an inbox item written in a repository elsewhere
        is personal and stays out of the journal."""
        return {urlparse(u).hostname or "" for u in (self.jira_url, self.gitlab_url) if u} - {""}

    @property
    def gitlab_host(self) -> str:
        return urlparse(self.gitlab_url).netloc if self.gitlab_url else ""
