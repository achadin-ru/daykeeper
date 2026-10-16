# Changes

## 1.1.2 — the fixes of the audit of 16 October 2026

Every finding of `AUDIT_REPORT.md` (section 9 there lists each with its test and probe).

**Look**
- The restart after a theme switch failed in the one-file Windows build: the new copy
  inherited the old one's `_PYI_*` variables and ran from its temp folder, which the old
  copy could then not remove ("Failed to remove temporary directory"). The restart now
  starts a fresh copy (`PYINSTALLER_RESET_ENVIRONMENT`).
- A plain note, a script routine and the border and title of a tile under the pointer take
  the app's accent; they were still blue.
- The app has an icon: an amber tile with the ink letter D and a small key in its counter.
  It is the file icon of the Windows exe and the macOS bundle and the icon of every window
  (before, the exe showed the PyInstaller default and the windows Tk's feather). The
  picture is drawn by `tools/make_icon.py` into `assets/`.
- The colours follow the icon: the accent is amber with ink text on it (buttons, the active
  tab, checks) in both themes, and the logo on the unlock screen and in the tab bar is the
  icon itself (`assets/logo/`, one file per size the window shows it at); link-like text is a
  darker amber that keeps 4.5:1 on a card. Text is never drawn in the accent itself any more
  (it was in three places), and a rail tab under the pointer stays white.

**Security**
- The PIN / master-password guard over the Passwords tab was only a picture: `Ctrl+F`,
  `Tab` and `Space` reached the search, the list and the Copy button under it, and the card
  kept the plain password in its fields. Now a locked tab takes its list and card off the
  screen and empties the card; every shortcut of the tab asks for the code first; a secret
  cannot be copied while the guard is down; an undo while locked keeps the new tab locked.
- Automatic routine runs (on unlock, at a time of day) start only the script the user
  accepted: saving the tile or running it by hand records a hash of the file (and of what
  a shortcut points to); a changed or never-accepted script does not start by itself and
  says so in a corner note, a run by hand asks before accepting the new version. The
  editor warns when a script sits on a network share, in Downloads or outside the profile
  folder. `.ps1` scripts still run with `-ExecutionPolicy Bypass`: the tile's hash now
  authenticates the file's content, so the bypass is scoped to exactly the file the user
  accepted, and the tile's tooltip says so.
- A user hook rule with a backtracking regex (`^(a+)+$`) made every assistant tool call
  wait the full 15 s hook timeout. Patterns are now bounded: 500 characters, no nested
  repetition, no repeated choice with alike options, no two unbounded repeats in a row
  (the editor refuses them with a plain message); the search covers the first 16 KB of a
  command or answer; a longer text runs the regex in a child process killed after 1.5 s,
  and the skipped rule is named under "Recent triggers". A worker thread with a deadline,
  as the report proposed, cannot do it — CPython's `re` holds the GIL for the whole search.
- The unlock screen waits after wrong master passwords the way the PIN prompt does.
- A link in an attached document opens with the system only when it is a known kind of
  document; programs, scripts, Office files with macros, disk images and unknown types are
  shown in their folder (an allow-list instead of a block list).
- Site-icon fetches no longer follow a redirect from a public site into a loopback,
  link-local or private address.
- A new master password also brings a new field key for the sealed values.
- The hook reads a `@file` named in a command only when the file lies under the folder the
  command ran in. The console helper refuses a note or an item that looks like a token.
- The hook command written into the assistant's settings is quoted for the shell whenever
  the path holds anything but plain characters (`$`, a backtick, a quote, `;` …).
- The wipe timer of the clipboard keeps a hash of the copied secret, not the secret.
- A copied password cannot be read through a toast after the lock: toasts close with it.
- Argon2id runs six passes instead of three (about 0.1 s here); the header bound stays at
  twelve, so a forged header costs no more than before. Older files open unchanged.

**Data**
- A lone surrogate in any text (a Windows file name can carry one) made every autosave
  fail with `UnicodeEncodeError` until the window was closed. The vault and the sealed
  values carry such text through unchanged; readable exports, the inbox and `settings.json`
  write U+FFFD in its place; a save that still cannot serialise shows the error instead of
  dying silently.
- The journal inbox is moved aside before it is read, so a line the hook appends while the
  app reads and saves goes into a fresh inbox and is picked up next time instead of being
  deleted unread; a claim left by a crash is picked up as well.
- A synced replacement of the vault file with the same size and time (OneDrive, iCloud,
  `copy2`) is detected: the stamp carries the nonce of the file's header.
- A damaged sealed value is kept as it is and the card says "unreadable value"; before, it
  was silently emptied and lost at the next save.
- A copy is kept before a list or a day is deleted and at the first save after an unlock
  (undo dies with the lock), whatever the backup cadence.
- A hand-edited export with a list or a word where a number belongs imports (the field
  takes its default) instead of failing with an uncaught `TypeError`.
- `settings.json` is flushed to disk before the rename; a `vault.pkv.tmp` left by a crash is
  removed at the next open; the assistant's `settings.json` is edited under a lock file and
  re-read right before the write, so an edit Claude Code makes meanwhile is kept.
- The temporary duplicate of a copy opened for a look is removed when its window closes;
  folders a crashed preview left behind are swept at the next Open.

**Undo**
- The history keeps per-object snapshots (one entry, one day, one note, one tile) and only
  what changed in a step, under a budget of 20 MB: a step after one edit is a few hundred
  bytes instead of the whole domain (474 KB at 1 000 entries, 4.8 MB at 10 000), and a
  hundred steps no longer cost up to half a gigabyte.

**Interface**
- Passwords: `Enter` in the search puts the first hit on the card, `↓` walks into the
  list, `Esc` clears the search; `Ctrl+Shift+C` copies the password of the entry on the
  card, `Ctrl+B` its login — find and copy without the mouse.
- Journal: the verdict columns sit right under the summary (they were below the fold in a
  760 px window); "Links…" and "Set up the journal…" moved into a ⋯ menu; "Pick up from
  sessions" is "Read the inbox now"; the quick button reads "✗ Провал" in Russian.
- Routine: a tile is drawn on one canvas (as bookmark tiles are): a hundred routines open
  in 0.3 s instead of 3.3 s. The run state, the chips and the actions are redrawn in place.
- Plain words instead of team jargon: "tested tasks" in the auto-summary (was "RFT"),
  "Out of office / other" (was "Absence / extra info"). The error dialog says what to do
  with `errors.log`.
- Contrast: disabled text, the success colour, link text in the dark theme and the active
  tab reach 4.5:1.

**Build and CI**
- Tests, lint, types and a dependency audit (`pip-audit`) run on every push and pull
  request — Dependabot's included — on Linux under Xvfb and on Windows (`test.yml`).
- The release publishes a signed build-provenance attestation for each file
  (`gh attestation verify`). The binaries themselves stay unsigned: that needs a code-signing
  certificate the project does not have.
- One Python for the build, the tests and CI: 3.13 (`build.bat` makes its venv with it; the
  README says so).
- The macOS bundle carries the app's version and `LSMinimumSystemVersion` (it said 0.0.0).
- Linux: the Bookmarks and Routine tabs no longer crash on the first tile (the Windows-only
  `<App>` keysym is bound where Tk has it; `<Menu>` on X11); the suite runs under Xvfb.
- 17 Windows API calls declare their argument and result types (a handle above 2^31 was
  passed truncated); every timer stops with the window (`invalid command name` noise in the
  tests); 16 more pure modules are under mypy.

## 1.1.1

- macOS: a copied password is now wiped from the system clipboard after 30 seconds as
  well; before, only the app's own copy was cleared and the password stayed there.
- macOS: text at the size other Mac apps use (13 for body text); 1.1.0 drew it larger.
- The arrows and the bin under the Passwords list are narrower and stay on one line with
  "+ New entry" in a window from about 1000 px wide; the sidebar is wide enough for them.
- The strength hint and the errors under a new master password wrap at the field's width:
  the unlock panel no longer widens while one types.
- The long heading of the activities on the Journal tab wraps inside the card instead of
  being cut at its right edge in a narrow window.
- Tests: on macOS the window tests no longer hide their window (Tk 8.6 there never returns
  from an update over a hidden window that was never drawn); the whole suite runs on a Mac.

## 1.1.0

**Data**
- The full export: every tab as readable JSON, and the import of it back (Settings →
  Data). The vault as it was is kept beside it first; the lock settings stay.
- Copies: Settings → Data lists the automatic copies and the copies kept beside the
  vault; each opens in a separate window or becomes the vault.
- A conflict (another window saved the file) no longer loses what was changed here: on
  close or lock it is offered as a separate copy. The inbox no longer pops an error every
  minute while the conflict lasts.
- A broken line in the journal inbox is skipped instead of stopping the inbox for good.
- An error in a click or a timer goes to `errors.log` and a short dialog.
- Hand-edited `settings.json` with a wrong type no longer breaks the Journal tab or the hook.

**Security**
- Passwords and tokens are sealed a second time inside the vault with a field key of
  their own: they are plain text only while shown or copied — not in the content decrypted
  at unlock, not in every autosave, not in the undo history. A lock wipes both keys. The
  file gets a new mark (`PKV2`): version 1.0 refuses it instead of opening it with empty
  passwords; a file of 1.0 is converted on the first unlock, its old form stays in `backup`.
- Wrong codes are counted: after three the prompt waits, after ten the app locks.
- A lock closes every window that shows vault data: an opened file, the output of a run.
  An attached file being edited is saved first.
- The GitLab token does not follow a redirect to another port of the same host.
- A bookmark opens web addresses only; a link in an attached document never starts a program.
- A rule of the hook does not write text that looks like a token into the journal.
- Stop ends a routine together with the scripts it started.
- The start-with-the-system entry is no longer rewritten when the user edited its arguments.
- A forged vault header can ask for four times the usual key work at most (was 1 GiB and 64 passes).

**Speed**
- Bookmark tiles are drawn on one canvas each: the tab opens 5–8 times faster, an undo
  that touches bookmarks too.
- The tabs are made on their first visit; the master password is checked off the window's thread.
- The process ends with the window even while site icons are still being fetched.
- Typing a name in the Passwords list changes only that row; the search waits for a pause.
- Integrations opens up to six times faster: the section is drawn once instead of three
  times, and the notifications show the newest 20 with “Show more” below.
- Settings → Journal opens twice as fast: its lists are measured in one layout pass.
- A GitLab check asks about each MR only once an hour. In between it asks a project for
  its new approvals and pipelines with one request each, and reads the threads of an MR
  only when its count of notes moved or your threads in it are still open. A quiet check of
  20 open MRs takes about 6 requests instead of 60–80.

**Usability**
- A dark theme (Settings → General; takes effect after a restart).
- Integrations → GitLab → Projects: tick the projects to watch; none ticked — all of them.
- Long text wraps instead of running past the edge: checklist items in notes and the
  lines of the journal report (the copied report keeps its lines as they are).
- A narrow or short window keeps everything in view: the sidebar buttons stay at the
  bottom, the Passwords sidebar narrows and a field's name goes above its value, the rows
  of buttons in the Journal and the GitLab settings wrap onto the next line.
- Sharp on every monitor: moved to a monitor of another scale, the window draws itself
  again at that scale instead of being stretched by Windows.
- `Ctrl+1…6` and `Ctrl+Tab` switch tabs; bookmark and routine tiles work from the keyboard.
- The eye inside the password field on the unlock screen shows the password; a new master
  password gets a strength hint.
- Settings → General → About: the version and the releases page.
- The import and export dialogs return to the settings they were started from.
- Own rules for the .env import in `settings.json`.

**Under the hood**
- ruff and mypy in CI, `requirements-dev.txt`, dependabot, `SECURITY.md`, this file.
- `requirements.txt` pins the same `cryptography` as the build; builds run without UPX.

## 1.0.0

The first release: passwords, bookmarks, the verification journal, notes, the routine and
GitLab notifications in one encrypted file; the Claude Code hook and the journal helper;
Windows exe and macOS app.
