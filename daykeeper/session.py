"""The life of a vault apart from its window: where it is, its settings file, open and
lock, save, the change counters the tabs rebuild by, the journal inbox, the export.
No tkinter: tested on a temporary folder."""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path

from .export import export_csv, export_text
from .i18n import _, detect_system_language
from .journal import claim_inbox, inbox_path_for, merge_inbox, read_inbox
from .revisions import Revisions
from .settings import Links, read_settings, readable, settings_path_for, write_settings
from .vault import MAGICS, Vault, default_vault_path, write_private

STALE_TMP_SEC = 60  # a `.tmp` of a write older than this is a crash's leftover, not a write under way
COPY_PREFIX = "daykeeper-copy-"  # the temp folder of a copy opened for a look (Settings → Data → Copies → Open)
COPY_SWEEP_SEC = 24 * 3600  # a copy folder older than this was left by a crash: swept at the next Open


def is_preview_copy(vault_path: Path, tempdir: Path | None = None) -> bool:
    """The vault sits in a `daykeeper-copy-*` folder right under the temp directory: a
    duplicate made for a look, and nothing to keep once the look is over."""
    folder = Path(vault_path).parent
    try:
        base = (tempdir or Path(tempfile.gettempdir())).resolve()
        return folder.name.startswith(COPY_PREFIX) and folder.resolve().parent == base
    except OSError:
        return False


def remove_preview_copy(vault_path: Path, tempdir: Path | None = None) -> bool:
    """Delete the temp folder of a copy opened for a look (its vault, settings and log);
    True when it was one. The preview process calls this when its window closes."""
    if not is_preview_copy(vault_path, tempdir):
        return False
    shutil.rmtree(Path(vault_path).parent, ignore_errors=True)
    return True


def sweep_preview_copies(tempdir: Path | None = None, older_than: float = COPY_SWEEP_SEC, now: float | None = None) -> int:
    """Remove `daykeeper-copy-*` folders older than `older_than` seconds (a preview that
    crashed never removed its own); the number removed."""
    base = tempdir or Path(tempfile.gettempdir())
    now = now if now is not None else time.time()
    removed = 0
    try:
        folders = [f for f in base.glob(COPY_PREFIX + "*") if f.is_dir()]
    except OSError:
        return 0
    for folder in folders:
        try:
            if now - folder.stat().st_mtime > older_than:
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
        except OSError:
            pass
    return removed


class Session:
    """One vault file and what belongs to it. `vault` is None while locked."""

    def __init__(self, vault_path: Path | None = None):
        self.vault_path = Path(vault_path) if vault_path is not None else default_vault_path()
        self.settings_path = settings_path_for(self.vault_path)
        self._settings_cache: dict | None = None
        self.vault: Vault | None = None
        self.rev = Revisions()  # which tab must be rebuilt on its next visit

    # ---- settings.json

    def settings(self) -> dict:
        """settings.json, read from disk once and kept: the tabs, the routine and GitLab
        ticks and every redraw of the bookmarks ask for it. A copy, so a caller may edit it."""
        if self._settings_cache is None:
            self._settings_cache = read_settings(self.settings_path)
        return dict(self._settings_cache)

    def write_settings(self, update: dict) -> bool:
        """Merge `update` into settings.json; False when the file could not be written."""
        settings = read_settings(self.settings_path)  # fresh: an edit by hand is not thrown away
        settings.update(update)
        try:
            write_settings(self.settings_path, settings)
        except OSError:
            return False
        self._settings_cache = settings
        return True

    def language(self) -> str:
        """The UI language from the settings, else the system's."""
        return str(self.settings().get("language") or "") or detect_system_language()

    def links(self) -> Links:
        return Links.from_settings(self.settings())

    # ---- the vault

    @property
    def exists(self) -> bool:
        return self.vault_path.exists()

    @property
    def open(self) -> bool:
        return self.vault is not None

    def open_vault(self, password: str) -> Vault:
        """Open the vault file, or create it when there is none yet, without making it the
        open one — safe on a thread (the key is derived here). WrongPassword and BadFile
        come through from the vault."""
        self._drop_stale_tmp()
        return Vault.open(self.vault_path, password) if self.exists else Vault.create(self.vault_path, password)

    def _drop_stale_tmp(self) -> None:
        """A `vault.pkv.tmp` left by a crash mid-write (the next write truncates it anyway,
        but it confuses the list of copies). A fresh one may be another window's write."""
        tmp = self.vault_path.with_suffix(self.vault_path.suffix + ".tmp")
        try:
            if tmp.exists() and time.time() - tmp.stat().st_mtime > STALE_TMP_SEC:
                tmp.unlink()
        except OSError:
            pass

    def unlock(self, password: str, vault: Vault | None = None) -> Vault:
        """Make the vault the open one: `vault` opened already (open_vault), else opened now."""
        self.vault = vault if vault is not None else self.open_vault(password)
        if self.vault.upgraded:  # a file of 1.0: written at once in the new form (the old one goes to backup)
            self.vault.save(force=True)
            self.vault.upgraded = False
        return self.vault

    def lock(self) -> None:
        if self.vault is not None:
            self.vault.forget()
        self.vault = None

    def save(self) -> None:
        """Write the vault (a backup first); nothing while locked. OSError comes through."""
        if self.vault is not None:
            self.vault.save()

    def save_conflict_copy(self) -> Path:
        """The open vault into `<name>-conflict-<time>.pkv` next to the vault file; its path.
        A file there is the user's: change_password re-encrypts it and never deletes it."""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = self.vault_path.with_name(f"{self.vault_path.stem}-conflict-{stamp}{self.vault_path.suffix}")
        self._open_vault().save_copy(target)
        return target

    def _open_vault(self) -> Vault:
        """The open vault for what makes sense only while it is open."""
        if self.vault is None:
            raise RuntimeError("the vault is locked")
        return self.vault

    def touch(self, domain: str) -> None:
        """Data of `domain` changed: the tabs drawing from it are rebuilt on their next visit."""
        self.rev.touch(domain)

    # ---- the journal inbox

    @property
    def inbox_path(self) -> Path:
        return inbox_path_for(self.vault_path)

    def pull_inbox(self) -> tuple[int, int] | None:
        """File what the sessions wrote into the inbox and delete it: (new items, items
        read), None when there was nothing or the vault is locked. The inbox is first
        moved aside (claim_inbox): a line the hook appends while it is read and saved goes
        to a fresh inbox and is picked up next time, not deleted unread. The save's
        OSError comes through, with the claimed file left for the next pull."""
        if self.vault is None:
            return None
        try:
            claimed = claim_inbox(self.inbox_path)
            items = [item for path in claimed for item in read_inbox(path)]
        except OSError:
            return None
        if not items:
            for path in claimed:
                path.unlink(missing_ok=True)
            return None
        added = merge_inbox(self.vault.journal, items, self.links().work_hosts)
        self.vault.save()
        for path in claimed:
            path.unlink(missing_ok=True)
        return added, len(items)

    # ---- export

    def check_password(self, password: str) -> bool:
        return self.vault is not None and self.vault.check_password(password)

    def write_export(self, fmt: str, path: Path) -> int:
        """Write every entry readable to `path` as "txt" or "csv"; the number of entries.
        The caller has asked for the master password (`export` does both)."""
        if self.vault is None:
            return 0
        target = Path(path)
        if fmt == "txt":
            data = readable(export_text(self.vault.entries)).encode("utf-8")
        else:
            data = readable(export_csv(self.vault.entries)).encode("utf-8-sig")
        write_private(target, data)  # every password in the clear: owner-only, as the vault
        return len(self.vault.entries)

    def write_full_export(self, path: Path) -> None:
        """Everything the tabs hold into `path` as readable JSON, owner-only. The caller has
        asked for the master password."""
        data = readable(json.dumps(self._open_vault().export_data(), ensure_ascii=False, indent=1)).encode("utf-8")
        write_private(Path(path), data)

    def import_full(self, path: Path) -> Path:
        """Replace the content of the open vault with an export file and save. The vault as
        it was is kept first as `<name>-before-import-<time>.pkv` beside it; its path.
        ValueError — not an export; OSError — not read or not written."""
        doc = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        stamp = time.strftime("%Y%m%d-%H%M%S")
        keep = self.vault_path.with_name(f"{self.vault_path.stem}-before-import-{stamp}{self.vault_path.suffix}")
        probe = Vault(self.vault_path, b"", b"", (0, 0, 0), [])
        probe.import_data(doc)  # a broken file fails here, before anything is touched
        vault = self._open_vault()
        vault.save_copy(keep)
        vault.import_data(doc)
        vault.save()
        for domain in ("vault", "journal", "notes", "bookmarks", "integrations", "routines"):
            self.touch(domain)
        return keep

    def copies(self) -> list[tuple[Path, str]]:
        """The copies of the vault the user may go back to, newest first: the automatic
        backups and the copies kept beside the vault (conflict, before import, before a
        restore). (path, kind) with kind "backup" / "conflict" / "import" / "restore" / "other"."""
        out: list[tuple[Path, str]] = []
        folder = self.vault_path.parent
        stem, suffix = self.vault_path.stem, self.vault_path.suffix
        backup = folder / "backup"
        if backup.is_dir():
            out += [(p, "backup") for p in backup.glob(f"{stem}-*{suffix}")]
        for p in folder.glob(f"*{suffix}"):
            if p.resolve() == self.vault_path.resolve():
                continue
            kind = "conflict" if "-conflict-" in p.name else "import" if "-before-import-" in p.name else "restore" if "-before-restore-" in p.name else "other"
            out.append((p, kind))
        return sorted(out, key=lambda pk: pk[0].stat().st_mtime if pk[0].exists() else 0, reverse=True)

    def restore_copy(self, copy: Path) -> Path:
        """Make a copy the vault file: the file as it is now is kept beside it first as
        `<name>-before-restore-<time>.pkv`; its path. The caller locks the app after this —
        the copy may have another password, and this window holds the old content."""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        keep = self.vault_path.with_name(f"{self.vault_path.stem}-before-restore-{stamp}{self.vault_path.suffix}")
        data = Path(copy).read_bytes()
        if not data.startswith(MAGICS):
            raise ValueError(_("{path} is not a vault file").format(path=copy))
        write_private(keep, self.vault_path.read_bytes())
        write_private(self.vault_path, data)
        return keep

    def export(self, fmt: str, path: Path, password: str) -> bool:
        """Export after the master password is given again; False (and no file) when it is wrong."""
        if not self.check_password(password):
            return False
        self.write_export(fmt, path)
        return True
