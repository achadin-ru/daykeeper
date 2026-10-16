# Daykeeper audit report

Date: 2026-10-16. Commit audited: `970c94d` (1.1.1), branch `main`, in a separate worktree
`E:\repos\daykeeper-audit` (branch `audit/deep-1016`). Platform: Windows 11 (26200),
Python 3.14.4 locally (CI builds with 3.13), Tk 8.6. Everything ran on temporary vaults,
a temporary `HOME` / `APPDATA` / `LOCALAPPDATA` / `CLAUDE_CONFIG_DIR` / `CODEX_HOME`;
the real vault, settings and assistant configs were not touched; nothing was committed or
pushed. The probe scripts and their raw output are in `E:\probes\daykeeper-audit3\`
(`probe_*.py`, `shots\`, `build.log`, `coverage.txt`).

What was run: the app (source and the freshly built `dist\Daykeeper.exe`), the full test
suite (346 tests, 28 s, 3 skipped, all green), `ruff` (clean), `mypy` (clean on the 15
configured modules; 310 errors when pointed at the whole package), `coverage` (68 %
of statements), `pip-audit` (no known vulnerabilities in the pinned dependencies),
`build.bat` (success, 25.3 MB exe, SHA256 `bad0f73d…`), screenshots of every screen in both
themes, resize/tab-switch/search timings on vaults of 1 000 and 10 000 entries, header
fuzzing (3 000 mutations), hook fuzzing, ReDoS, inbox race and clipboard probes.
A new property-based test module was added: `tests/test_properties.py` (15 tests on
hypothesis; skipped automatically where hypothesis is not installed).

---

## 1. Summary

The core is in good shape: the file format is sound (Argon2id + AES-256-GCM with the
header as AAD, bounded KDF parameters, a second sealing layer for secrets, zeroed keys,
atomic writes, conflict detection, encrypted backups), the hook/CLI is fast (0.15–0.2 s)
and never blocks the assistant on bad input, the token goes only over https to the typed
host, site icons are decoded by a bounded pure-Python decoder, the UI is responsive
(26–28 ms per resize step on every tab, 7–47 ms tab switches) and looks far better than a
typical tkinter app. Nothing critical was found. One finding is high: the Passwords-tab
guard (PIN / master password) can be walked around from the keyboard.

### Top 5 risks

| # | Risk | Severity |
|---|------|----------|
| H1 | The PIN/master guard over the Passwords tab is only a visual overlay: `Ctrl+F`, `Tab` and `Space` reach the search, the list and the **Copy** button underneath, and the card keeps the plain secret in its field variables. The password is copied while `secrets_open()` is `False`. | High |
| M1 | A user hook rule with a backtracking regex (`^(a+)+$`) makes every Claude Code / Codex tool call wait the full 15 s hook timeout; the text searched is the tool output, which the user does not control. | Medium |
| M2 | Inbox race: a journal item written by the CLI/hook while the app is pulling the inbox is deleted unread (silent loss). Reproduced. | Medium |
| M3 | A lone surrogate in any text (e.g. a Windows file name attached to a note) makes every autosave raise `UnicodeEncodeError`; nothing is saved until the user finds it, the conflict copy fails too. Reproduced. | Medium |
| M5 | Auto-run routines (on unlock / at a time of day) execute whatever file sits at the stored path at that moment, `.ps1` with `-ExecutionPolicy Bypass`; a swapped script in a shared, network or Downloads folder runs silently with the user's rights. | Medium |

Also medium: undo history memory grows linearly with the vault (467 MB for 100 steps at
10 000 entries, M4); a sync tool replacing the file with the same size and mtime is not
detected (M6); tests do not run on push / PR / Dependabot, builds are unsigned and carry no
provenance (M7).

### Top 5 UX / performance improvements

| # | Improvement | Gain |
|---|-------------|------|
| U1 | "Find and copy a password" needs typing plus two mouse clicks. Make `Enter` in the search select the first hit, `↓` move into the list, `Ctrl+Shift+C` / `Ctrl+B` copy the password / login of the selected entry, `Esc` clear the search. | The everyday flow becomes keyboard-only: `Ctrl+F`, type, `Ctrl+Shift+C`. |
| U2 | A global quick search (`Ctrl+K`) over passwords, bookmarks, routines and notes with a default action on `Enter` (copy / open / run). | One box instead of six tabs; the app starts to feel like a launcher. |
| U3 | Routine tiles take 3.3 s to build for 100 routines (bookmarks 1.5 s for 300): a routine tile is ~10 widgets. Draw routine tiles on one canvas as `BookmarkTile` already does (5–8× faster there). | First open of the Routine tab from seconds to well under one. |
| U4 | Journal tab at 1200×760: the three verdict columns sit below the fold under a 170 px action bar; the quick buttons mix languages ("✓ Успешно", "✗ Failed", "🐞 Баг"). Move "Links…" and "Set up the journal…" into a ⋯ menu, put the columns above the Activity box, translate the button. | The main purpose of the tab (what passed / failed) is visible without scrolling. |
| U5 | Undo snapshots: store per-entry / per-day diffs instead of the whole domain string (M4); cap the history by bytes. | Memory flat; undo capture no longer serialises 5 MB on every edit. |

---

## 2. Findings

Severity: Critical / High / Medium / Low. Effort: S (≤ 2 h), M (half a day), L (1–2 days).

### High

#### H1. The Passwords-tab guard (PIN / master password) is bypassed from the keyboard

**Evidence.** `lock_view.py:135–168` (`_show_guard`) covers the tab with a `place`d frame;
nothing underneath is disabled or unmapped. `app.py:325–338` handles `Ctrl+F` as
`self.page("vault").focus_search()` without asking `secrets_open()`;
`vault_view.py:91–92` focuses the search entry. `vault_view.py:432–464` builds every field row
with `val_var = tk.StringVar(value=f.value)` — the plain secret — and a **Copy** button
that calls `copy_to_clipboard(val_var.get())`; `lock_secrets()` (`lock_view.py:91–100`)
does not clear the card. Probe `probe_pin.py`:

```
vault_locked: True | guard frame on top: 1
Ctrl+F while guarded -> focus is the search field under the guard: True
widgets reachable by Tab that lie under the guard: 34
copied through the Copy button under the guard: ['TOP-SECRET-PASSWORD'] | secrets_open(): False
the value field under the guard holds the plain secret in its StringVar: TOP-SECRET-PASSWORD
```

**Impact.** The guard is the one control that protects passwords while the app is open and
the user has stepped away; it is defeated with `Ctrl+F`, typing, `Tab`, `Space` in a few
seconds, without the PIN, and the attempt counter never fires. Bookmark tiles honour the
guard (`bookmarks_view.py` `reveal` / `open_bookmark` go through `require_secrets`), so the
Passwords tab is the hole.

**Reproduction.** Settings → Security → "Ask for a PIN code", set a PIN, Save; press 🔒 on
the tab (or wait for the idle lock). Press `Ctrl+F`, type part of an entry name, `Tab` to
the list, `↓`, `Tab` to the password row's **Copy** button, `Space`. Paste anywhere.

**Fix (S–M).** In `lock_secrets()`: `_load_entry(None)` (empties every `StringVar`),
`paned.pack_forget()` or `state=["disabled"]` on the sidebar and card (or simply
`place_forget` the page frame and show the guard in its place); make `Ctrl+F`, `Ctrl+N`,
`Alt+↑/↓`, `Delete` and `select_entry` go through `require_secrets`; on `unlock_secrets()`
rebuild the card. Add a test that asserts no widget under the guard is reachable by
`tk_focusNext()` and that `copy_to_clipboard` is never called while `vault_locked`.

### Medium

#### M1. ReDoS through a user hook rule stalls every assistant tool call

**Evidence.** `hookrules.py:324–327` (`Rule._find`) runs `re.search(self.pattern, text)` on
the whole command and tool output with no length cap and no time limit; the hook's own
budget (`cli.py:94`, 8 s for `glab`) does not cover it. Probe `probe_hook.py`, rule
`^(a+)+$` on the response:

```
response 'a'*20+'!': exit=0 0.19s
response 'a'*26+'!': exit=0 2.69s
response 'a'*30+'!': exit=TIMEOUT 15.01s   (killed by the 15 s Claude Code hook timeout)
```

The presets are fine: 2 MB adversarial responses took ≤ 0.03 s.

**Impact.** One badly written rule (nested quantifiers are easy to write by accident) and
one tool output containing a long run of the right characters, and every subsequent
`Bash`/`PowerShell`/MCP call waits 15 s. The user sees the assistant "hang" and does not
connect it to the journal.

**Fix (S).** Search at most the first 64 KB of the command and of the response; run the
user regex in a worker thread with a 1 s deadline and log "rule X timed out"; reject
patterns with nested quantifiers (`(…+)+`, `(…*)*`, `(a|a)+`) in `Rule.problem()` with a plain
message; mention the limit in the rule dialog.

#### M2. Inbox items written during a pull are deleted unread

**Evidence.** `session.py:114–129` (`pull_inbox`): `read_inbox` → `merge_inbox` →
`vault.save()` (a backup copy and an fsync, tens to hundreds of ms) → `clear_inbox` unlinks
the file. `journal.py:339–365` (`append_inbox`) appends to the same name at any moment
(the hook runs after every assistant command). Probe `probe_hook.py` with the save slowed to
300 ms and a second writer appending at +100 ms:

```
pulled: (1, 1) | inbox file after pull exists: False | journal has APP-2 (written during the pull): False
```

**Impact.** Silent loss of a journal line; the user never learns the verdict was dropped.
The window is small per poll, but the poll runs every minute for hours and the hook fires
after every command.

**Fix (S).** Claim by rename: `os.replace(inbox, inbox.with_name(f"inbox.jsonl.{pid}-{ts}"))`
(atomic on NTFS and POSIX), read and merge the claimed file, delete it only after the save
succeeded; writers keep appending to `inbox.jsonl`. Also pick up stray `inbox.jsonl.*`
files on the next pull (a crash between claim and save).

#### M3. A lone surrogate in any text breaks every save

**Evidence.** `vault.py:488–502` (`_payload`): `json.dumps(..., ensure_ascii=False).encode("utf-8")`.
Python keeps unpaired surrogates in `str` (Windows file names may contain them; a
`filedialog` result goes straight into `notes.Attachment.path`, `notes.py:397–405`); UTF-8
refuses them. Probe `probe_crypto.py` §9:

```
save FAILED: UnicodeEncodeError 'utf-8' codec can't encode character '\ud83d' in position 426
```

`save_now` (`app.py:997–1015`) catches `ChangedOnDisk` and `OSError` only, so this goes to
`report_callback_exception` (errors.log + a dialog every 30 s) and the vault is never written
again in that session; `save_conflict_copy` fails the same way on close.

**Impact.** Data loss of a whole session with a confusing error, triggered by one odd file
name or pasted text.

**Fix (S).** Encode with `errors="surrogatepass"` (round-trips) or sanitise text at the
model boundary (`text.encode("utf-8", "replace").decode()` in `Field.value`, `Note`,
`Attachment`, journal items); treat `UnicodeEncodeError`/`ValueError` in `save_now` like an
`OSError` with a message that names the field. Add the surrogate case to `test_properties`
once the policy is chosen (today the strategy excludes category `Cs`).

#### M4. Undo history memory and capture time grow with the whole vault

**Evidence.** `undo.py:40–53` keeps the full `domain_state` text per changed domain per
step (`LIMIT = 100`, `undo.py:16`); `vault.domain_state("vault")` serialises every entry
(`vault.py:590–607`) on every capture (0.7 s after an edit). Measured (`probe_perf.py`):

| vault | state size per step | capture time | 30 edits | 100 steps |
|-------|--------------------:|-------------:|---------:|----------:|
| 1 000 entries | 474 KB | 7–17 ms | 13.9 MB | ~46 MB |
| 10 000 entries | 4.8 MB | 94–98 ms | 140 MB | ~467 MB |

**Impact.** At the sizes the app will reach over years (M-sized journal, thousands of
entries) a few minutes of typing cost hundreds of MB and ~100 ms stalls on the UI thread
per captured step. This is where the architecture breaks first as data grows.

**Fix (L).** Snapshot per object (entry id → its dict, day → its dict) and diff against the
previous snapshot; keep the history under a byte budget (e.g. 20 MB) instead of 100 steps;
compute the snapshot off the UI thread or incrementally (the tab already knows which
entry was edited).

#### M5. Auto-run routines trust the file at the path, and `.ps1` runs with the policy bypassed

**Evidence.** `routines.py:335–359` (`launch_plan`) resolves the target at run time;
`routines.py:299` adds `-NoProfile -ExecutionPolicy Bypass -File` for `.ps1`;
`routines_view.py:209–219` runs due routines every 20 s and on every unlock without a
prompt (`run_routine(r, auto=True)`); `.lnk` targets are followed (`routines.py:320–332`).
The vault authenticates the *path*, not the script.

**Impact.** A script in a shared or synced folder, a network share or Downloads can be
replaced by another user, a sync conflict or malware with user rights; the next unlock
runs it quietly under the user's account, with a console-less window. The README calls
adding a tile "the consent to run that script" — but the consent is to a path.

**Fix (M).** Store `sha256(target)` (and of the `.lnk` target) when the tile is saved and
after each manual run; before an *automatic* run compare, and if it differs skip the run
and toast "script changed since you last ran it — open the tile to accept". Warn in the
editor when the target is on a UNC path, in Downloads or outside the profile. Keep
`-ExecutionPolicy Bypass` (documented) but show the policy word in the tile's tooltip.

#### M6. A synced replacement with the same size and mtime is overwritten silently

**Evidence.** `vault.py:320–325` (`_stamp_of`) identifies the file by `(st_mtime_ns, st_size)`;
`vault.py:514` compares it before a write. OneDrive, iCloud and `copy2` preserve mtime. Probe
`probe_integrity.py` §2 (another version of the same length, mtime copied):

```
sizes equal: True
C saved over the synced version silently (stamp = mtime+size cannot tell): True
```

Two windows on one file (different mtime) are detected correctly (§1) and the conflict
copy opens.

**Impact.** Rare but silent: the other side's changes vanish, no conflict copy is offered.

**Fix (S).** Keep `sha256` of the bytes last read/written (`_saved_digest` already exists for
the payload; add one for the file: 5 MB hashes in ~10 ms) and compare it before writing;
or compare the AES nonce in the header (12 random bytes, unique per write) — cheapest
and sufficient.

#### M7. CI: tests do not run on push / PR / Dependabot; no audit, signing or provenance; local Python differs from CI

**Evidence.** `.github/workflows/build.yml:7–10`: `on: push: tags: ["v*"]` and
`workflow_dispatch` only; `dependabot.yml` opens weekly PRs that nothing tests. Coverage is
68 % overall, with `data_dialogs.py` 9 %, `claude_view.py` 14 %, `journal_settings_view.py`
15 %, `popup.py` 22 %, `toast.py` 24 %, `clipboard.py` 28 %, `settings_view.py` 33 %
(`coverage.txt`). No `pip-audit` step (run here: dependencies clean). The release job
uploads unsigned binaries with `SHA256SUMS.txt` but no attestation. `pyproject.toml`
targets `py313` and CI uses 3.13, while the local `.venv` (and the exe built from it) is
Python 3.14.4; `requirements-build.txt` hashes happen to cover both. No app-side update
check: `SECURITY.md` says only the latest release gets fixes, but a 1.0 user is never told.

**Impact.** A dependency bump or a refactor can land red; a build can be swapped on the
way to the user without a signature; users stay on vulnerable versions.

**Fix (M).** Add `test.yml` on `push`/`pull_request` (ubuntu + windows, `xvfb-run` for the
Tk tests, ruff + mypy + unittest + `pip-audit -r requirements-build.txt`); add
`actions/attest-build-provenance` to the release job; consider SignPath (free for OSS) for
the exe and Apple notarisation for the app; an opt-in weekly `GET releases/latest`
("a newer version is available" in About); pin the local venv to 3.13 in `build.bat`.

### Low

#### L1. The hook exits 1 with a traceback on deeply nested JSON
`cli.py:204–209` catches `ValueError, OSError, AttributeError`; `json.loads` raises
`RecursionError` on `[`×200 000 (probe: `exit=1`, traceback on stderr, which Claude Code shows
to the user). Fix (S): `except Exception` there, as in the inner handler.

#### L2. A toast survives the lock
`toast.py:14` has no `closes_on_lock`; a GitLab toast with an MR title stays over the unlock
screen (probe `probe_pin.py` §5: `toplevels alive after lock: [('Toast', …)]`). Fix (S): add the flag.

#### L3. "Open" a copy leaves encrypted duplicates in `%TEMP%` forever
`settings_view.py:582–592` (`_open_copy`) copies the vault into `tempfile.mkdtemp(prefix="daykeeper-copy-")`
and never removes it; the preview process also writes `settings.json` / `errors.log` beside
it. Fix (S): delete the folder when the preview process exits (`--preview` could delete its
own folder on close), or reuse one folder.

#### L4. A hand-edited export can crash the import with an uncaught `TypeError`
`bookmarks.py:203–223` (`Bookmark.from_dict`), `routines.py:117–127` (`Routine.from_dict`)
and `gitlab.py:133–137` (`Event.from_dict`) use bare `int()` / `float()` / `dict()`;
`data_dialogs.py:179–192` catches `OSError, ValueError` only. Probe §10: `"order": [1]` →
`TypeError` → error dialog, nothing imported; `"abc"` → `ValueError` (caught). Fix (S): the
`_number()` helper from `Entry.from_dict` everywhere.

#### L5. A damaged sealed value is silently emptied
`vault.py:250–254`: a `sealed` string that is not base64 becomes an empty field (probe §8:
`value: '' | filled: False`). Fix (S): keep the raw string and show "value unreadable" /
write to errors.log, so the user restores a copy instead of re-typing nothing.

#### L6. CLI items are not filtered for tokens; the inbox is plain text
`cli.py:50–70` / `journal.py:339–365` write any `note`/`pass` text; the hook filters
`TAKE_MATCH` only (`hookrules.py:267–277`). Probe §5: `note "deployed with glpat-…"` lands in
`inbox.jsonl` in the clear (until the next pull, ≤ 1 min) and later in the clipboard report.
Fix (S): run `looks_secret()` on CLI items and refuse with a message.

#### L7. Hook command quoting covers space, `&`, `(`, `)` only
`claudecode.py:161–167`: a data folder or user name with `$`, backtick, `%`, `^`, `'`, `;`, `|`
produces a command line that the shell Claude Code uses mangles (probe `probe_platform.py`
§2: none of those is quoted). Fix (S): always quote, escape `"`, `$`, backtick and `\` for bash.

#### L8. `win32.py`: 17 ctypes calls without `argtypes`/`restype`
Only `work_area_at` declares them (`win32.py:118–135`). On x64 a handle or pointer above
2³¹ is passed as a negative `int`; probe §4: `GetAncestor(0x80000001)` → `0`, so caption
colours, DPI and flashing silently do nothing for such a window. Fix (S): declare once
per function at module load.

#### L9. Journal: "✗ Failed" button and "TEST FAILED" column untranslated in the RU UI
`journal_layout.py:236–247`: `button="✗ Failed"` and `label="Test failed"` are data strings,
so `tests/test_catalog.py` (which checks `_("…")` literals) does not see them; screenshot
`light-04-tab-journal.png`. If "Test failed" is the tracker's status name on purpose, the
quick button should still say "✗ Провал"/"✗ Не прошло". Fix (S).

#### L10. The field key is not rotated on a master-password change
`vault.py:547–556` keeps `self._sealer`; probe §6: `field key rotated: False`. Whoever held
an old file *and* the old password already had everything, so this is defence in depth
only. Fix (S): a new `Sealer` in `change_password` and re-bind the fields.

#### L11. Backup cadence hides a burst of mistakes
`vault.py:68` `BACKUP_EVERY = 600`: five saves in a row made 0 copies (probe `probe_integrity.py`
§4); undo dies with the lock. Fix (S): force a copy before destructive actions (delete a
list / a day / "Replace everything" already does, "Restore…" does) and before the first save
after a lock.

#### L12. `vault.pkv.tmp` survives a crash; `settings.json` is written without fsync
`vault.py:135–144`, `settings.py:71–79`. Harmless (`O_TRUNC` on the next write) but the leftover
confuses "Copies" and a power loss can leave an empty `settings.json` (which reads as `{}`).
Fix (S): unlink stray `.tmp` at start; `fsync` in `write_settings`.

#### L13. `Restore…` calls `path.stat()` outside the `try`
`settings_view.py:594–596`: a copy deleted meanwhile raises into the error dialog instead of
the "Could not restore" message. Fix (S).

#### L14. The copied secret stays referenced for 30 s; "Copy address"/login go to history by design
`app.py:1032–1033` keeps the value in the wipe timer's arguments (`timers.Debounce._args`);
on macOS Universal Clipboard may sync within the 30 s. Documented limitation; fix (S): keep
only a hash for the comparison (`clear_if` can compare `sha256(read_text())`).

#### L15. Test noise and UI coverage
The suite prints `invalid command name "…_tick"` (Tk timers firing after `destroy()` in
`test_app_smoke` / `test_audit_fixes`), which can hide real errors; UI modules are 9–33 %
covered (see M7). Fix (S): stop the tickers in `App.destroy` (`_inbox_ticker`, the
security/gitlab/routine tickers) and cancel `Debounce` jobs.

#### L16. Types: 28 % of lines under mypy; 59 `type: ignore`
`pyproject.toml` lists 15 modules (5 010 of 17 949 lines); `mypy daykeeper` reports 310 errors
in 24 files; 47 of the 59 ignores are `attr-defined` for ad-hoc attributes hung on Tk
widgets in `notes_view.py` (`card.note`, `card.item_rows`, …). Fix (M): a small `NoteCard`
dataclass holding the widgets; add the view modules to mypy one at a time.

#### L17. Argon2id cost is on the low side for this hardware
`vault.py:54–56` (64 MiB, t=3, p=4 — RFC 9106's second recommendation) derives in 55 ms here
(probe §1); the README promises 0.2–0.4 s. The derivation runs on a thread
(`app.py:499–539`), so t=4–6 or 128 MiB costs nothing in UI terms; keep the 4× header bound
(`vault.py:63–65`, a forged header costs at most 2.3 s per attempt — measured). Fix (S).

#### L18. Contrast
Light: `DISABLED_TEXT` on menu white 1.88:1, `SUCCESS` on white 3.30:1 (AA for large text
only); dark: `DISABLED_TEXT` 2.35:1, `ACCENT` links on card 3.53:1, white on the active tab
4.47:1. Everything else ≥ 4.5:1 (probe `probe_contrast.py`). Fix (S): `#8A919E` / `#6F7889`
for disabled, `#15803D` for success, `#818CF8` for dark links.

#### L19. Linux: the pure parts work, the Bookmarks and Routine tabs crash on any tile
Run on Fedora 43 in WSL (Python 3.14.0, Tk 9.0, Xvfb; `linux_run.sh`, `probe_linux.py`):
the data folder is `~/.local/share/Daykeeper` with `drwx------` and every file `-rw-------`
(vault, settings.json, backups); the `.desktop` autostart entry renders and reads back;
without a terminal program `open_in_console` returns `False` as documented; the clipboard
falls back to Tk; the window opens, unlocks (0.26 s) and switches tabs (18–74 ms); H1
reproduces there too; the crypto, hook, inbox-race and assistant-wiring probes gave the
same results as on Windows. **But** the suite ends `347 tests, 1 failure, 2 errors`: both
errors are `bookmark_tile.py:115` (and the same loop in `routines_view.py`) binding the
Windows-only keysym `<App>` — X11 Tk raises `TclError: bad event type or keysym "App"`, so
building the first bookmark or routine tile fails and those two tabs never open on Linux
(the probe vault had no tiles, the test vault does; on a populated vault — 300 bookmarks,
100 routines — both tabs crash as shipped and open in 697 / 864 ms once the bind is
guarded, `probe_linux_ui.py`). The failure is a DPI assertion
(`painter.scale == 1.0` vs `1.00197` on Xvfb's 96.19 dpi). No CI job runs the Tk tests on
Linux, no artifact is built and README says Windows and macOS. X11 Tk accepts `<Menu>` and `<Shift-F10>` but not `<App>` (verified). Fix (S): bind `<App>`
inside `try/except tk.TclError` and `<Menu>` beside it, round the scale in the test,
add an ubuntu job with `xvfb-run`, and say "Linux: from source" in README.

#### L20. The hook reads any local file named after `@` in a command
`hookrules.py:87–97` (`_body_text`) opens `@path` files up to 512 KB to look for a verdict
body. Only searched, never written or logged, but surprising for a hook. Fix (S): restrict
to files under `cwd` and to `curl`/`glab`/`http` commands.

#### L21. `cmd /c start "" /wait <lnk>` splits a shortcut name on `&`
`routines.py:326`: an unreadable `.lnk` without spaces but with `&` in its name
(`a&b.lnk`) becomes two cmd commands. Fix (S): always quote the path.

#### L22. Unlock screen has no attempt throttle
`security.Attempts` guards the prompts of an *open* app (`lock_view.py:115–130`); the
unlock screen relies on Argon2 (55 ms/try here ≈ 65 000 tries per hour at the keyboard).
Anyone with the file can run that offline anyway, so this is low. Fix (S): the same
1-2-4 s backoff after three wrong master passwords.

#### L23. Lost-update window on `~/.claude/settings.json`
`claudecode.py:230–250` reads, edits and replaces the file; Claude Code rewrites the same
file when permissions change. A rare lost update of the other side's edit; a `.before-daykeeper`
copy is kept once. Fix (S): re-read right before `os.replace` and merge, or write under a lock file.

#### L27. The macOS bundle reports version 0.0.0 and no minimum system version
`Info.plist` of the published `Daykeeper.app` (v1.1.1): `CFBundleShortVersionString 0.0.0`,
no `LSMinimumSystemVersion`; `build.yml:84–86` passes no version to PyInstaller. Finder,
crash reports and "About This Mac" cannot tell releases apart. Fix (S): pass
`--osx-bundle-identifier` plus a `version` file / `--version-file` built from
`daykeeper.__version__`, and set `LSMinimumSystemVersion` to the tested macOS.

#### L24. The document viewer opens "non-runnable" links by a block list
`md_view.py:27–31` (`RUNNABLE_SUFFIXES`) and `md_view.py:234–244` (`_open_link`): a link in an
attached Markdown file whose suffix is not on the list is handed to `os.startfile` /
`open` / `xdg-open`. The list misses Office files with macros (`.docm`, `.xlsm`, `.pptm`),
`.chm`, `.msc`, `.scf`, `.library-ms`, `.settingcontent-ms`, `.iso`/`.vhd` (mounted),
`.pl`/`.rb`/`.php` and anything a user registered a handler for; a UNC path
(`\\server\share\x.docm`) also qualifies. Probe `probe_links.py` (the system calls are
recorded, not run): `macro.docm`, `sheet.xlsm`, `help.chm`, `panel.msc`, `shell.scf`,
`lib.library-ms`, `page.settingcontent-ms`, `disk.iso`, `script.pl` all went to
`open_with_system`; `tool.exe`, `run.ps1`, `file:///…/calc.exe` went to "show in folder";
`javascript:` did nothing. The attached file is the user's own, but it can come from a
shared folder or a repository. Fix (S): allow-list what the viewer opens itself (`.md`,
`.txt`, `.log`, `.pdf`, images) and show everything else in its folder.

#### L25. Site-icon fetches follow redirects to any host and port
`siteicon.py:449–452` uses `urllib.request.urlopen` with its default redirect handler; probe
`probe_net.py` §4: an icon link redirected to another loopback port was fetched. Only the
user's own bookmarks trigger it, no credential is sent and the answer is only decoded as an
image, so this is a privacy/intranet-probing note, not a leak. Fix (S): refuse redirects
to private/loopback ranges when the bookmark host is public, or keep icon fetches on the
bookmark's host.

---

## 3. Measured numbers

Machine: this Windows 11 workstation; Python 3.14 from source unless stated. Vault sizes:
"1k" = 1 000 entries × 3 fields, 300 bookmarks (tied), 100 routines, 365 journal days,
365 notes; "10k" = 10 000 entries, the rest the same.

| What | 1k | 10k | Notes |
|------|---:|----:|-------|
| Vault file | 843 KB | 5.1 MB | JSON inside AES-GCM |
| `Vault.open` (Argon2id + parse + seal binding) | 0.097 s | 0.251 s | Argon2id alone 0.055 s |
| `_payload()` (every autosave) | 10 ms | 65 ms | on the UI thread |
| `save()` (payload + AES + fsync) | 16 ms | 115 ms | on the UI thread |
| `domain_state("vault")` (undo capture) | 7 ms / 474 KB | 94 ms / 4.8 MB | see M4 |
| `search("bank")` | 2 ms | 21 ms | |
| `App()` to first paint | 0.61 s | 0.59 s | source; exe 1.7–2.0 s to window (onefile extraction) |
| unlock → main screen painted | 0.68 s | 1.25 s | Passwords tab + list of all entries |
| Passwords tab first open | 138 ms | 136 ms | second visit 6–9 ms |
| Bookmarks (300 tiles) first open | 1 506 ms | 1 385 ms | chunked: first screen earlier; second visit 7–9 ms |
| Journal (365 days) first open | 306 ms | 259 ms | second 7–8 ms |
| Notes first open | 550 ms | 508 ms | second 15–47 ms |
| Routine (100 tiles) first open | 3 263 ms | 2 924 ms | second 30 ms — see U3 |
| Integrations first open | 203 ms | 184 ms | |
| search-as-you-type refresh | 13 ms (143 rows) | 56 ms (1 429 rows) | 150 ms debounce |
| clear search → full list | 19 ms | 111 ms | Treeview rebuilt whole |
| one letter in the entry name | 15 ms | 12 ms | row-only update |
| window resize, median per step | 26–28 ms on every tab (max 32–50 ms), 20–55 ms after the pause | | 1100→1296 px drag, `main_resize.py` |
| helper `daykeeper-cli.exe` (note / hook) | 0.18–0.20 s | | from source via python: 0.15–0.23 s |
| hook on a 5 MB tool response | 0.20 s | | garbage / empty stdin: 0.12 s, exit 0 |
| header fuzz (3 000 mutations) | 0 unexpected exceptions; worst attempt 2.27 s (max allowed params) | | |
| unit tests | 346 in 28 s (+15 property tests in 4 s) | | 3 skipped |

Tab switching, search and resize are smooth at both sizes; the costs that scale with the
data are the autosave, the undo capture and the first build of tile tabs.

---

## 4. What is already done well

- **File format.** Argon2id with a per-file salt; AES-256-GCM with a fresh random nonce per
  write (300/300 distinct); the header is the AAD, so a magic downgrade to `PKV1` is
  rejected (`WrongPassword`); KDF parameters are bounds-checked before derivation
  (`check_params`), 3 000 random mutations gave only `BadFile`/`WrongPassword`; the
  `PKV1 → PKV2` conversion keeps the old file in `backup\` and seals the secrets.
- **Second sealing layer.** Secret values stay AES-GCM-sealed in memory under a field
  key kept inside the content; the autosave payload and the undo snapshots contain no plain
  secret (verified by probe); `forget()` zeroes both keys (bytearrays) and a read after the
  lock fails closed (`InvalidTag`).
- **Writes.** Temp file + fsync + `os.replace`; a simulated crash before the rename left
  the vault intact; conflict detection between two windows works and offers a conflict copy;
  change of the master password re-encrypts backups and deletes copies under older passwords.
- **Clipboard on Windows.** Secrets are put with `ExcludeClipboardContentFromMonitorProcessing`,
  `CanIncludeInClipboardHistory=0`, `CanUploadToCloudClipboard=0` (verified live), wiped
  after 30 s only if unchanged; macOS wipes the system pasteboard too.
- **Network.** The GitLab token goes only over https (plain http to loopback only) and
  only to the typed host **and port**, redirects included, through the system proxy when
  there is one; TLS with the default verified context; a failed check waits the full
  interval. Site icons: 512 KB page / 1 MB icon / 512 px side limits, a bounded inflate,
  a pure-Python decoder that survived random bytes (property test), fetched on a pool and
  stored as a small PNG — no third-party icon service.
- **Hook.** Fast (0.2 s), exit 0 on any garbage, logs no commands or answers, filters
  token-looking matches, ignores its own self-tests, respects folder allow/deny lists,
  asks `glab` with a budget; the `settings.json` editing is atomic, keeps a one-time
  backup and refuses to write over invalid JSON.
- **Links.** Bookmarks open http/https/mailto only; a link in an attached document never
  starts a program; CSV cells starting with `=+-@` are escaped.
- **Engineering.** Three layers with pure models under tests; `ruff` (incl. bandit and
  bugbear) and `mypy` clean on the configured set; every `# noqa` carries a reason (50,
  mostly S603/S607 for the user's own commands and BLE001 in "never crash" paths); pinned
  and hashed build dependencies, pinned actions; timers, jobs and the runner are unit-tested
  on fakes; `errors.log` with rotation; `knownDefect`-style honesty in the README's
  "Limitation" paragraph.
- **UI.** For tkinter it is unusually modern: anti-aliased rounded controls drawn as
  nine-slice PNGs, a consistent 8-pt grid, a dark theme, per-monitor DPI redraw, custom
  menus with keyboard navigation, keyboard-reachable tiles, chunked building of long tabs,
  a quiet-resize path that keeps drags at ~27 ms per step, i18n enforced by a test.

---

## 5. UX, look and feel, keyboard

**Everyday flows (clicks / keys counted on the sample vault).**

| Flow | Today | Friction |
|------|-------|----------|
| Find and copy a password | `Ctrl+F`, type → list filters after 150 ms → **click** the row → **click** Copy (2 clicks + mouse travel) | No `Enter`/`↓` from the search into the list; no copy shortcut; `Esc` does not clear the search. |
| Open a bookmark with login | `Ctrl+2` → **click** "↗ + 🔑" (password copied, site opens) → paste; login is a second click (👤) | Good. Tiles are keyboard-reachable (`Tab`, `Enter`, `Shift+Enter`). |
| Log the day, build the standup | `Ctrl+3` → type keys in "Add" → `Enter` (= first column) or click ✓/✗/🐞 → `Ctrl+R` (report copied) | Good; the quick field accepts several keys. The columns are below the fold at 760 px. |
| Run a routine | `Ctrl+5` → double-click the tile or `Tab`+`Enter` | Good; no global hotkey per routine. |
| First run | Create a password (strength hint, eye) → empty tabs each with a one-paragraph hint | Clear, but no guided next step (import `.env`, connect the assistant, turn on a guard). |

**Wording.** "RFT" in the auto-summary and "Absence / extra info" are team jargon for a
public product; "Pick up from sessions" is clearer as "Read the inbox now". The error
dialog "Something went wrong: ValueError" could say what to do ("send errors.log"). The
settings "Where it works" / "Teach the assistant" read well. Tooltips are everywhere and
name the shortcut — good.

**Consistency.** The Passwords tab is a list + card, Bookmarks and Routine are tiles,
Notes are cards, Journal is a form, Integrations a form + feed. Each fits its content; the
sidebar/"+ New" button placement is consistent. Per-field icon buttons (👁 ⧉ 🎲 🔒 ✕) rely
on emoji glyphs that render monochrome and at slightly different sizes on Windows (see the
screenshots): a small set of drawn icons through the existing `Painter` would unify them.

**Modern look.** Compared with current desktop apps (Bitwarden, 1Password, Obsidian):
spacing, radius, typography (Segoe UI, Helvetica Neue) and the dark theme are at par;
HiDPI is sharp (PMv2 + redraw). What tkinter cannot give: real animations (the toast and
tabs appear instantly), native title bars beyond colour, a native menu bar on macOS, blur /
Mica, font fallback for colour emoji on Windows, inertial scrolling, true layered
overlays (H1 is a symptom: a "modal" layer is just a frame on top). The product is at the
point where the next big step in feel would cost more in tkinter than it returns; the
clean three-layer split (pure models and services with no tkinter) means a port of the
window layer (e.g. to a web view or Qt) would not touch the vault, journal, hook or
GitLab code. Not urgent — the current look is good enough for daily use.

---

## 6. Scalability and extensibility

- **Data growth.** The file is one JSON object: fine to ~10–20 MB (10 k entries + years of
  journal/notes). Costs that scale with the total: autosave (65 ms + 115 ms at 10 k, on the
  UI thread), undo capture (M4, the first thing to break), `Vault.open` (0.25 s, fine).
  Journal/notes/bookmarks are iterated per visit but each visit builds only its tab.
  A year of journal (365 days) costs 0.3 s on the first visit. At 100 k journal items the
  day list is still a Treeview insert per day — fine. Recommendation: diff-based undo and
  an autosave that serialises on a worker thread (the models are plain Python; take a
  snapshot under a lock) before any format change.
- **New tab.** Six places: `PAGE_TYPES` + `tab_label` + `TAB_TIPS` in `app.py`,
  `revisions.VIEW_DOMAINS`, `tabs.TAB_IDS`, a `TabPage` subclass, the vault part
  (`Vault._load` / `_payload` / `domain_state`), the RU catalog. The `Shell` protocol keeps
  the tab away from `App` internals — good. A registry (`tabs.py` listing id, label, tip,
  page class, domains) would make it two places.
- **New integration (Jira next).** `Integrations` (`integrations.py`) is GitLab-shaped:
  flat `gitlab_*` fields, one watcher class, the tab hardcodes `"gitlab"`/`"jira"` rows and
  a stub. Generalise to `connections: dict[str, Connection]` with a `Watcher` interface
  (`check(settings, old) -> CheckResult`, `rules`, `interval`) before adding Jira, or the
  second integration will copy 500 lines.
- **New language.** `CATALOGS` + `LANGUAGES` + a `locale_xx.py`; `variants()` and the
  catalog test are language-agnostic; `detect_system_language` knows only RU/EN. Easy.
- **New platform.** `win32.py`, `clipboard.py`, `autostart.py`, `runner.py`, `settings.data_base_dir`
  isolate the OS; Linux is mostly there (L19). The hard part is Tk itself on each OS
  (the macOS hangs already found).

---

## 7. What to fix first

1. **H1** — close the guard bypass (clear the card, unmap/disable the tab, gate the shortcuts) and add the focus-traversal test. Half a day.
2. **M3** — surrogate-safe serialisation; **M2** — claim-by-rename for the inbox; **M6** — nonce/hash in the disk stamp. Two hours together, all three are data-loss paths.
3. **M1** — bound the user regex (size cap, deadline, pattern check). Two hours.
4. **M5** — script hash check before automatic runs. Half a day.
5. **M7** — tests on push/PR + pip-audit + provenance; pin local Python to 3.13. Half a day.
6. **M4** — diff-based undo with a byte budget. One to two days; do it before the vault grows.
7. Quick lows in one pass: L1, L2, L4, L5, L6, L9, L13, L19 (the `<App>` bind), L21, L22, L24, L25, L27 (each ≤ 1 h).
8. UX: U1 keyboard flow, U4 journal fold, U3 canvas routine tiles.

## 8a. Sink inventory: every place that executes, writes or talks to the network

To make "every" checkable, every sink in `daykeeper/` was enumerated by grep
(`subprocess`, `os.startfile`, `webbrowser.open`, `eval`/`exec`/`pickle`/`shell=`, `ctypes`,
`urlopen`/`HTTPConnection`/`socket`, plain-text writes) and judged one by one:

| Sink | Where | Input | Verdict |
|------|-------|-------|---------|
| `subprocess.Popen(argv or shell=True)` | `runner.py:51` | the user's own routine from the authenticated vault; `shell=True` only for the "command" kind | by design; M5 (path trusted, not content) |
| `cmd /k …`, `osascript`, `sh -c` | `runner.py:130–141` | same plan, console mode | by design; L21 (`&` in an unquoted `.lnk` name) |
| `taskkill /T /F`, `os.killpg` | `runner.py:86–96` | own child pid | fine |
| `subprocess.Popen(args)` restart / open a copy | `app.py:257`, `settings_view.py:590` | `sys.executable` + own paths | fine; L3 (temp copies) |
| `pbcopy`, `defaults read`, `git remote get-url`, `glab api` | `app.py:1058`, `i18n.py:89`, `journal.py:323`, `cli.py:96` | fixed argv; `git -C <cwd>` with the hook's cwd; `glab api <path>` with a path built from `[\w./-]` | fine (no shell, timeouts); `glab` is GET-only |
| `os.startfile` / `open` / `xdg-open` | `app.py:1069–1071` (vault folder), `md_view.py:37–50` (viewer links, "show in folder"), `routines_view.py` via `open_with_system` (file/folder routines) | folder of the vault; a link in an attached document; the user's routine | L24 for the viewer (block list) |
| `webbrowser.open` | `bookmarks_view.py:460` (http/https/mailto only), `md_view.py:236` (http/https/mailto only), `integrations_view.py:495` + `toast.py:49` (`web_url()`-filtered), `settings_view.py:187` (constant), `routines_view.py:152` (the user's "url" routine, any scheme) | — | fine; a `url` routine with `file:` is the user's own choice |
| `ctypes.windll.*` | `clipboard.py`, `win32.py` | fixed Windows APIs | L8 (argtypes) |
| `eval`, `exec`, `pickle`, `marshal`, `yaml`, `importlib` | — | not used anywhere | — |
| `http.client.HTTPSConnection` / `HTTPConnection`, `urllib` opener | `gitlab.py:171–206` | the typed GitLab address, redirects checked by `_token_may_go` | proven live (§8) |
| `urllib.request.urlopen` | `siteicon.py:449–452` | the user's bookmark URL | L25 (follows redirects anywhere, no credential) |
| `socket`, `requests`, unverified TLS | — | not used | — |
| plain-text writes | `settings.py:77` (settings.json), `journal.py:361` (inbox), `cli.py:135/150` (hook cache/log), `errlog.py:29`, `autostart.py:87`, `claudecode.py:234–418` (assistant files), `md_view.py:289/318` (the user's document), `settings_view.py:586–588` (temp copy of the encrypted vault) | no secret reaches any of them (probes: hook log, inbox, errors.log); L6 is the one exception (a note typed with a token) | fine |
| user-supplied regex | `hookrules.py:317/326` | the user's own rule | M1 |

## 8. Live checks that confirmed the defences

Run against local servers and temporary config folders (`probe_net.py`, `probe_codex.py`,
`probe_locks.py`, `probe_integrity.py`, `probe_links.py`):

| Claim | Result |
|-------|--------|
| GitLab token follows only same-host, same-port redirects | `/redir-same` followed with the token; `/redir-other` (another loopback port) and `/redir-https` (another host) refused with "sends the request to another server"; the other server never received a request. |
| …also through a system proxy (urllib path) | Proxy set via `http_proxy`: `/user` and `/redir-same` answered, `/redir-other` refused; the proxy saw only requests to the typed host. |
| A slow / non-JSON / 404 server cannot hang or crash the check | 3 s stall → "GitLab did not answer in time" at the 1.5 s timeout; `<html>` → "Not JSON"; 404 → "HTTP 404 on …"; `check()` on an empty server → one `GitLabError`. |
| Site icons: zlib bomb, 3 MB icon, 6 s stall, SVG, 4 MB page | `fetch()` returned in 4.05 s (the stall hit the 4 s timeout), no icon, theme colour kept, no exception; the oversized page was cut at 512 KB. |
| Idle lock, lock on minimize | 61 s idle → vault `None`, unlock screen shown; `iconify()` → locked within 50 ms. |
| Attempt limit | Ten wrong PINs: waits 0,0,1,2,4,8,16,32,60,60 s, then the whole app locked; the master password reset the counter. |
| Lock closes windows with vault data | A routine output window and the settings window were gone after `lock()`; a `Toast` stayed (L2). |
| `errors.log` holds no secret | An exception raised with the secret in a local variable logged the traceback and the entry name only. |
| Claude Code and Codex wiring | On temp `CLAUDE_CONFIG_DIR` / `CODEX_HOME`: our entry added once (connecting twice does not duplicate), other hooks and `permissions` kept, one-time `.before-daykeeper` backup, broken JSON refused and left untouched, instruction block inserted / replaced / removed without touching the user's text; the hook accepted a Codex-shaped payload and wrote the PR event. |
| Two windows on one file; crash mid-write; clipboard marks | See §4 and `probe_integrity.py`: conflict detected and a copy offered; the old file intact after a simulated crash; `ExcludeClipboardContentFromMonitorProcessing` / no-history marks present and the wipe only when unchanged. |

| A real GitLab over real TLS (gitlab.com, no credential; `probe_gitlab_com.py`) | `gitlab.com` typed without a scheme → https; `/user` and `/version` → "401 Unauthorized — the token is wrong or expired" in ~1 s; `http://gitlab.com` refused before any request; 150 merged MRs of a public project paged over one kept-alive connection in 8 s; `check()` without a token → one `GitLabError`; `expired`, `self-signed` and `wrong.host` badssl hosts refused with `CERTIFICATE_VERIFY_FAILED`. |
| Linux (Fedora 43, Tk 9.0, Xvfb) | See L19: pure parts and the window work, two tabs crash on the `<App>` keysym. |
| Build signing | `Get-AuthenticodeSignature` on the built `Daykeeper.exe` and `daykeeper-cli.exe`: `NotSigned` (M7). The published `Daykeeper-macos.zip` of v1.1.1 (SHA256 matches `SHA256SUMS.txt`) was parsed: the Mach-O carries a code-signature superblob with a `CodeDirectory` (identifier `ru.achadin.daykeeper`) and an **empty CMS blob (8 bytes)** — an ad-hoc signature, no Developer ID, not notarised; `Info.plist` says `CFBundleShortVersionString 0.0.0` (L27). |
| macOS (GitHub Actions, same commit `970c94d`) | Run `38928501799`: `lint`, `windows`, `macos`, `release` all `success`; the macOS job ran `346 tests … OK (skipped=24)` — the 24 are the window tests the project skips on that runner. The `screenshots.yml` runs of today (`38906011544`, `38905851802`, `38897820439`) succeeded, so every screen was drawn on a Mac for the commits just before this one. No Mac was reachable from here for interactive probes (SSH timed out twice). |
| Linux UI on a populated vault (1 000 entries, 300 bookmarks, 100 routines, 365 days) | As shipped: Bookmarks and Routine crash on `<App>`; the other tabs 27–153 ms, unlock 0.33 s, search 8 ms, resize 3.5 ms/step. With the keysym bind guarded (the proposed fix): Bookmarks 697 ms, Routine 864 ms, everything else unchanged — faster than on Windows. |
| Second scanner | `bandit` over the sources: 1 HIGH (`runner.py:53`, `shell=True` — the user's own command routine, M5), 1 MEDIUM (`routines.py:351`, the shell line of that routine), 48 LOW (`subprocess` calls already in the inventory, "hardcoded password" hits that are UI label keys such as `"password"`, partial executable paths, one `escape()` use in the plist writer). Nothing the grep inventory had missed. |

**What could not be exercised from this machine, and what stands in for it:** interactive
macOS probes (the Mac timed out on SSH twice; the macOS CI job of this very commit passed
346 tests, the screenshots workflow drew every screen today, and the published bundle was
parsed for its signature and plist — see the table); the Codex application itself (not
installed; its hooks file, instruction file and a Codex-shaped hook payload were exercised
on both Windows and Linux); notarisation (proven absent from the artifact, nothing to run).
The sink inventory in §8a plus the independent `bandit` pass are the exhaustiveness
argument: every execution, network and plain-text-write site in the sources is listed with
a verdict, every parser that takes outside input was fuzzed or property-tested, every lock
and every network rule was exercised live, and every finding above traces to one of them.

**Changes left in the worktree (uncommitted):** `tests/test_properties.py` (new, 15 tests,
needs `hypothesis` — add it to `requirements-dev.txt` if kept), this report, and build
output in `dist\` / `build\` (git-ignored). The probes are outside the repository.


## 9. Fix status

Fixed on 16–17.10.2026 in the same worktree, branch `fix/audit-1016` (renamed from
`audit/deep-1016`); **nothing committed, nothing pushed**: the commit is the owner's. The
last code change was followed by the whole verification again: the full suite (432
tests, 3 skipped, green) on Windows and 410 tests under Xvfb on Fedora 43 (green, 5
skipped); `ruff` and `mypy` clean (31 modules under mypy, 15 before); `build.bat` produced
`dist\Daykeeper.exe` (25.3 MB, SHA256 `480bb207…`, version 1.1.2) and `dist\daykeeper-cli\`; the hook from the
built helper 0.15–0.22 s (0.15–0.16 s from source); `probe_perf.py 1000` / `10000` and
`main_resize.py` no worse than §3. Final re-run after the version bump (17.10, 01:55, the
owner's Daykeeper closed): `main_resize.py` median per step 25.5 / 24.7 / 25.2 / 27.0 / 26.8 /
28.2 ms on vault / bookmarks / journal / notes / routines / integrations (`after\main_resize_final.txt`);
the settings sections, measured the same way by the new `settings_resize.py`, 13.5–17.5 ms
(`after\settings_resize_final.txt`); `probe_perf.py 10000`: `one edited entry = 423 B per step`,
`30 undo steps … 12.3 KB`, routines tab first open 346 ms (`after\probe_perf_10000_final.txt`). The
"after" output of every probe is in `E:\probes\daykeeper-audit3\after\`; the findings that had
no probe got one, `probe_after_fixes.py`, whose lines are quoted below. Regression tests live
in `tests/test_audit_1016.py` (the window), `tests/test_audit_1016_models.py` (the models),
`tests/test_ci_files.py` (the build and CI files) and `tests/test_properties.py` (hypothesis,
now in `requirements-dev.txt`); the names below are the test methods or classes.

Two things the report proposed were done differently, and the table says so: the thread
deadline of M1 (CPython's `re` holds the GIL for the whole search — measured: `join(1.0)`
never returns on `"a"*60`), replaced by static pattern checks plus a child process with a
deadline for long texts; and the exe signature of M7 (no certificate — recorded as not done
inside an otherwise fixed item).

Verification environment: every test and probe ran on temporary vaults with `APPDATA`,
`LOCALAPPDATA`, `HOME`, `CLAUDE_CONFIG_DIR` and `CODEX_HOME` pointed at temporary folders;
no test or probe uses the default vault path (grep over `tests/` and the probes). The real
`%APPDATA%\Daykeeper\vault.pkv` kept being written during the work by the owner's own
running `E:\repos\daykeeper\dist\Daykeeper.exe` (started 18:45, before the session): its
inbox is fed by the Claude Code hook of other sessions (`hook-log.jsonl` there records
merge requests opened at 22:58 and 23:26 from other project folders) and it saves
the file once a minute after a pull. Its backup copies are 60 KB each — copies of the real
vault that only the process holding its key can make; the vaults of this work are 1.2 KB
to 5 MB in `%TEMP%`. No `inbox.jsonl.*` claim file of the new code exists in the real
folder. `~/.claude/settings.json` was last changed on 15.10, `~/.codex` on 14.10. The
`AUDIT_REPORT.md` of the main checkout was never under git (empty history); before this
section was written into it, its first 641 lines were compared with the worktree's copy
and were identical.

| id | status | regression test | probe · one line of its re-run output | commit |
|----|--------|-----------------|---------------------------------------|--------|
| H1 | fixed | `GuardAgainstTheKeyboardTest` (5 tests: Tab walk, Ctrl+F/N/Alt/Delete, search/select/copy, unlock, rebuilt tab) | `probe_pin.py` · `widgets reachable by Tab that lie under the guard: 0` · `copied through the Copy button under the guard: [] \| secrets_open(): False`; `probe_linux.py` · `H1 on Linux — copied under the guard: []` | uncommitted |
| M1 | fixed | `BoundedRuleRegexTest` (7 tests: nested, alike options, adjacent repeats, 16 KB cap, child deadline, child == inline, slow rule logged) | `probe_hook.py` §4 · `response 'a'*30+'!': exit=0 0.12s` (was TIMEOUT 15.01 s); built helper on `.*error.*failed.*done` over 16 KB · `1.64s exit 0 \| "slow_rules": ["triple"]` (inline: 153 s). Static checks + a child process, not a thread (`re` holds the GIL) | uncommitted |
| M2 | fixed | `InboxClaimTest` (5 tests) | `probe_hook.py` §6 · `inbox file after pull exists: True` — the line appended during the pull waits in the fresh inbox (was deleted unread) | uncommitted |
| M3 | fixed | `SurrogateSafeSerialisationTest` (3 tests); `test_properties.test_field_seal_unseal`, `test_entry_round_trip` over any text | `probe_crypto.py` §9 · `saved fine` (was `save FAILED: UnicodeEncodeError`) | uncommitted |
| M4 | fixed | `HistoryTest.test_a_step_keeps_only_what_changed`, `VaultDomainsTest.test_every_object_round_trips_by_key` | `probe_perf.py 10000` · `one edited entry = 423 B per step` (was 4.8 MB) · `30 undo steps … history holds 12.3 KB` (was 140 MB); at 1000 · `12.3 KB` (was 13.9 MB). Capture time unchanged (~100 ms at 10k) | uncommitted |
| M5 | fixed | `AcceptedScriptTest` (4 tests), `RoutineAcceptanceTest` (2 tests) | `probe_after_fixes.py` · `auto run of a never-accepted script started: False` · `auto run of a CHANGED script started: False \| toast: Не запущено: файл скрипта изменился…` · `run by hand, accepted -> started: True \| auto run again: True`. `-ExecutionPolicy Bypass` kept, justified in CHANGELOG, named in the tile's tooltip | uncommitted |
| M6 | fixed | `DiskStampTest` (2 tests) | `probe_integrity.py` §2 · `sizes equal: True` · `C refused: ok` (was `saved over the synced version silently`) | uncommitted |
| M7 | fixed | `CiFilesTest.test_tests_run_on_every_push_and_pull_request`, `test_release_publishes_provenance…`, `test_one_python_version_everywhere`, `test_dev_tools_are_pinned` | `test.yml` on push/PR (ubuntu + xvfb, windows, ruff, mypy, pip-audit); provenance step in `build.yml`; `build.log` · `Python 3.14.4` — **this machine has no 3.13** (`py -0`), so the pin applies to a new venv and CI; **the exe signature is not done: a certificate is required**; the update check is deferred as a feature | uncommitted |
| L1 | fixed | `HookSmallThingsTest.test_deeply_nested_json_does_not_leave_a_traceback` | `probe_hook.py` §2 · `deep  exit=0 0.13s stderr=` (was exit=1 with a traceback) | uncommitted |
| L2 | fixed | `GuardAgainstTheKeyboardTest.test_a_toast_does_not_outlive_the_lock` | `probe_pin.py` §5 · `toplevels alive after lock: []` (was `[('Toast', …)]`) | uncommitted |
| L3 | fixed | `RemainingLowsTest.test_preview_copies_are_recognised_removed_and_swept` | `probe_after_fixes.py` · `is a preview copy: True \| removed when the preview closes: True \| folder left: False` · `stale folders swept at the next Open: 1` | uncommitted |
| L4 | fixed | `QuickLowsTest.test_a_hand_edited_export_with_wrong_shapes_still_imports` | `probe_crypto.py` §10 · `bookmark order=[1] -> imported (tolerant)` (was `raised TypeError (NOT caught…)`) | uncommitted |
| L5 | fixed | `QuickLowsTest.test_a_damaged_sealed_value_is_kept_and_named`, `UnlockScreenAndCopiesTest.test_an_unreadable_value_is_named_on_the_card` | `probe_crypto.py` §8 · `value: '' \| filled: True` — kept in the file, the card says "unreadable value" (was `filled: False`, lost at the next save) | uncommitted |
| L6 | fixed | `HookSmallThingsTest.test_the_cli_refuses_a_token_in_a_note` | `probe_hook.py` §5 · `inbox has the token: False` (was True) | uncommitted |
| L7 | fixed | `RemainingLowsTest.test_the_hook_command_is_quoted_for_the_shell` | `probe_platform.py` §2 · `quote() wraps a path with '$': True` … `'\|': True` (all eight were False) | uncommitted |
| L8 | fixed | `RemainingLowsTest.test_windows_calls_carry_their_signatures` | `probe_platform.py` §4 · `functions with argtypes+restype declared at import: 15 of 15` (was 1) | uncommitted |
| L9 | fixed | `RussianCatalogTest.test_the_failed_button_and_the_journal_words_are_russian` | `probe_after_fixes.py` · `'✗ Failed' -> ✗ Провал` (the column keeps the tracker's status name) | uncommitted |
| L10 | fixed | `RemainingLowsTest.test_a_new_master_password_brings_a_new_field_key` | `probe_crypto.py` §6 · `field key rotated on password change: True` (was False) | uncommitted |
| L11 | fixed | `RemainingLowsTest.test_a_copy_after_an_unlock_and_before_a_deleted_list` | `probe_integrity.py` §4 · `backups made during 5 saves: 1` (was 0): the first save after an open keeps a copy; `backup_now()` before a deleted list or day | uncommitted |
| L12 | fixed | `HookSmallThingsTest.test_a_stale_tmp_is_dropped_at_open_and_a_fresh_one_kept`, `test_settings_are_written_whole` | `probe_integrity.py` §3 · `temp file left behind: ['v.pkv.tmp']` right after the simulated crash — removed at the next open once older than 60 s; `settings.json` is fsync'd | uncommitted |
| L13 | fixed | `UnlockScreenAndCopiesTest.test_restore_of_a_copy_deleted_meanwhile_says_so` | `probe_after_fixes.py` · `message shown instead of the error dialog: True \| Не удалось восстановить: [WinError 2]…` | uncommitted |
| L14 | fixed | `RemainingLowsTest.test_the_wipe_timer_holds_a_digest_not_the_password`, `TimersAndClipboardTest` | `probe_after_fixes.py` · `timer pending: True \| secret in the timer's arguments: False \| argument: 208d8828f8b3cd3a…`; the Windows marks unchanged (`probe_integrity.py` §5) | uncommitted |
| L15 | fixed | `TimersAndClipboardTest.test_every_timer_stops_with_the_window…` | full suite · `invalid command name` lines 87 → 2 (a tooltip's debounce and one lambda remain); UI coverage itself not raised | uncommitted |
| L16 | fixed in part, the rest declined | `CiFilesTest.test_the_pure_modules_are_under_mypy` | `mypy` · `Success: no issues found in 31 source files` (15 before). Declined: the 310 errors of the view modules — a `NoteCard` refactor with no behaviour change, a change of its own | uncommitted |
| L17 | fixed | `RemainingLowsTest.test_argon2_cost_and_bounds` | `probe_crypto.py` §1 · `open x3 [0.123, 0.127, 0.118] s` (was 0.055); fuzz `worst single attempt: 2.555s` — the header bound stays 12 passes | uncommitted |
| L18 | fixed | `ContrastTest.test_text_pairs_in_both_themes` | `probe_contrast.py` · light `DISABLED_TEXT on WHITE 4.6`, `SUCCESS on CARD 5.0`; dark `DISABLED_TEXT 4.8`, `LINK on CARD 5.3`, `WHITE on RAIL_ACTIVE 5.4` (were 1.9 / 3.3 / 2.4 / 3.5 / 4.5); left: the dark `ACCENT` fill (no longer used for text) and decorative borders | uncommitted |
| L19 | fixed | `MenuKeyBindingTest.test_the_menu_key_binds_where_tk_has_it`; the DPI test rounds to two places | `linux_rerun.txt` · `Ran 410 tests … OK (skipped=5)`; `probe_linux_ui.py` as shipped · `tab bookmarks 773 ms`, `tab routines 263 ms` (both crashed before); `test.yml` runs the suite under Xvfb; README says Linux is from source | uncommitted |
| L20 | fixed | `HookSmallThingsTest.test_a_body_file_is_read_only_under_the_command_folder` | `probe_after_fixes.py` · `file in the folder: [('pass', 'APP-1')] \| file elsewhere: [] \| no folder known, absolute path: []` | uncommitted |
| L21 | fixed | `QuickLowsTest.test_a_shortcut_name_with_an_ampersand_is_quoted` | `probe_after_fixes.py` · `argv: None \| shell: start "" /wait "C:\tools\a&b.lnk"` (was an unquoted argv) | uncommitted |
| L22 | fixed | `UnlockScreenAndCopiesTest.test_wrong_master_passwords_are_throttled` | `probe_after_fixes.py` · `three wrong: ['Неверный пароль', …] \| fourth: Слишком много неверных попыток. Подождите 1 с` · `right password after the pause: (opened)` | uncommitted |
| L23 | fixed | `RemainingLowsTest.test_settings_json_edits_merge_the_latest_file` | `probe_codex.py` · `our entries after connecting twice: 1 \| other hooks kept … \| permissions kept` — the same result, now under a lock with a re-read right before the write | uncommitted |
| L24 | fixed | `QuickLowsTest.test_the_viewer_opens_documents_and_shows_the_rest_in_the_folder` | `probe_links.py` · `SHOW_IN_FOLDER macro.docm … disk.iso … script.pl`; `handed to the system app: ['report.pdf', 'pic.png']` (was nine more) | uncommitted |
| L25 | fixed | `QuickLowsTest.test_icon_fetches_do_not_follow_a_redirect_into_the_private_network`, `test_the_opener_refuses_the_redirect_live` | `probe_net.py` §4 · `fetch /redir … requests=['/redir', '/to-other', '/apple-touch-icon.png', '/favicon.ico']` — the other port never asked, the "WAS followed" note gone | uncommitted |
| L27 | fixed | `CiFilesTest.test_release_publishes_provenance_and_the_mac_bundle_carries_its_version` | `build.yml` / `build.sh` · `plutil -replace CFBundleShortVersionString … LSMinimumSystemVersion 14.0`, then `codesign --force --deep --sign -`; runs on the macOS job only (no Mac here) | uncommitted |
| U1 | fixed | `KeyboardFlowsTest.test_type_enter_copy`, `test_down_walks_into_the_list`, `test_copy_shortcuts_respect_the_guard` | `probe_after_fixes.py` · `Enter -> card shows: Mail \| Ctrl+Shift+C copied: ['mail-pass'] \| Ctrl+B copied: ['bob@example.com']` · `↓ -> focus to the list: True` · `Esc -> search: '' \| rows: 1` · `under the guard … copied: [] []` | uncommitted |
| U2 | deferred | — | — · a global `Ctrl+K` search is a feature of its own (a new window, ranking across six domains), not started so that the fixes stay reviewable | — |
| U3 | fixed | `KeyboardFlowsTest.test_routine_tiles_are_canvases_that_follow_the_run` | `probe_perf.py` · Routine tab, 100 tiles, first open `314 ms` at 1k / `450 ms` at 10k (761 ms in a run under 58 % background load; was 3263 / 2924 ms); `probe_linux_ui.py` · `263 ms` | uncommitted |
| U4 | fixed | `KeyboardFlowsTest.test_journal_columns_sit_above_the_activity_box` | `probe_after_fixes.py` · `rows: summary 2 < columns 4 < activity 11 \| bar: ['📋 Сформировать отчёт', 'Прочитать входящие сейчас', '⋯', …]` | uncommitted |
| U5 | fixed | `HistoryTest.test_the_byte_budget_drops_the_oldest_steps` (the 20 MB budget), `test_a_step_keeps_only_what_changed` (the diffs) | `probe_perf.py 10000` · `history holds 12.3 KB` after 30 steps against a 20 MB budget (was 140 MB and growing); the capture no longer serialises 5 MB per step — 423 B | uncommitted |
| §5 wording | fixed | `RussianCatalogTest`, `test_catalog` (new keys translated), `test_journal_layout.test_the_tester_report_keeps_its_shape` | `probe_after_fixes.py` · `'Read the inbox now' -> Прочитать входящие сейчас \| 'tested tasks' -> проверенные задачи \| old keys left: []`; the error dialog names `errors.log` and what to do with it | uncommitted |
| §6 tab registry | deferred | — | — · a refactor of six places with no behaviour change, left for a change of its own | — |
| §6 integrations | deferred | — | — · worth doing right before the second integration (Jira), not inside a fix branch | — |

Residual notes: M4 — the capture still serialises every object on the UI thread (~100 ms at
10 000 entries); the memory is flat now, the time is the next step (a worker thread with a
retry when the data moved meanwhile). M1 — a pattern that passes the static checks and
runs long on a short text (≤ 2 KB) still runs inline; at that size the worst measured
case was 0.3 s. L15 — UI coverage was not raised; the tests of this fix cover the paths
that changed.
