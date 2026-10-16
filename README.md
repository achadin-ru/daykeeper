# Daykeeper Multitool

A personal multitool for your working day on Windows and macOS, all in one encrypted file
under one master password:

- **Passwords** — logins, API tokens, keys and cards;
- **Bookmarks** — tiles with links, tied to password entries for one-click login;
- **Journal** — what was verified, failed and filed, turned into a standup report;
- **Notes** — cards by day with checklists and attached Markdown files;
- **Routine** — quick-launch scripts, commands, links and folders, also on a schedule;
- **Integrations** — GitLab notifications about your merge requests.

Everything stays on your computer. The program file and the data folder keep the short
name, Daykeeper.

UI in English and Russian, light or dark (Settings → General). Day-to-day usage and
troubleshooting — [PLAYBOOK.md](PLAYBOOK.md); what changed between releases —
[CHANGELOG.md](CHANGELOG.md); how to report a weakness — [SECURITY.md](SECURITY.md).

## Run

Ready builds are on the [Releases](../../releases) page, no installation needed.
Settings → General → Start opens the app when you sign in (per user: the Run key on
Windows, a LaunchAgent on macOS, ~/.config/autostart elsewhere), optionally minimized;
it still asks for the master password:

- Windows — `Daykeeper.exe`. On the first start Windows may warn about an unknown
  publisher: the file is not signed.
- macOS (Apple Silicon: M1 and later) — `Daykeeper-macos.zip`, unpack and drag
  `Daykeeper.app` to Applications. The app is not signed with an Apple certificate, so
  macOS stops the first start. Once, either way:

  1. Through the settings: double-click the app and close the warning, then System
     Settings → Privacy & Security → Security → "Open Anyway" next to Daykeeper, confirm
     with the Mac's password or Touch ID and press "Open". (Right-click → Open no longer
     does this since macOS 15.)
  2. In Terminal, also when macOS says the app is damaged or "Open Anyway" is not there —
     this removes the "downloaded from the internet" mark:

     ```
     xattr -dr com.apple.quarantine /Applications/Daykeeper.app
     ```

From source (Python 3.13 with tkinter — the version the builds and CI use, `pyproject.toml`; `build.bat` makes its venv with it):

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt      # macOS / Linux: .venv/bin/pip
.venv\Scripts\python main.py                        # macOS / Linux: .venv/bin/python main.py
```

Linux: from source only (the same command; `python3-tkinter` from the distribution). The
suite runs there under Xvfb in CI; there is no Linux build on the Releases page.

Build it yourself — `build.bat` on Windows (one windowless file, `dist\Daykeeper.exe`, which
carries the console helper `dist\daykeeper-cli\` inside),
`build.sh` on macOS (`dist/Daykeeper.app`); both install the build tools from
`requirements-build.txt` (exact versions with hashes) and print the SHA256 of the result.
The icon (`assets/icon.ico`, `.icns`, `.png` — the file icon and the window icon) is
drawn by `tools/make_icon.py`; rerun it only after changing the design (needs Pillow).
Releases are built by GitHub Actions: a tag like `v1.2.0` runs the tests and builds on
Windows and macOS, then attaches both files and `SHA256SUMS.txt` to the release
(`.github/workflows/build.yml`, actions pinned to commit hashes). Check a download:

```
certutil -hashfile Daykeeper.exe SHA256          # Windows
shasum -a 256 Daykeeper-macos.zip                # macOS
gh attestation verify Daykeeper.exe --owner <the repository owner>   # provenance: which workflow run built it
```

The release also publishes a signed provenance statement for each file (GitHub's build
attestation): `gh attestation verify` proves the file came out of this repository's
workflow at a given commit. The binaries themselves are not code-signed — that needs a
certificate (a Windows code-signing certificate, an Apple Developer ID) that the project
does not have; until it does, Windows SmartScreen and macOS Gatekeeper will warn as
described above.

The vault file lives in the data folder — `%APPDATA%\Daykeeper\vault.pkv` on Windows,
`~/Library/Application Support/Daykeeper/vault.pkv` on macOS; another path can be passed
as the first argument. Before a save the previous version is copied to `backup\` next
to it (the latest 20 and the last one of each of the past 14 days). A data folder left by
the previous name, PassKeeper, is renamed on the first start.

Shortcuts are Ctrl+letter on Windows and Cmd+letter on macOS; tooltips show the one for
your system. `Ctrl+1…6` and `Ctrl+Tab` switch the tabs.

## Passwords

- Entry kinds: login and password, API token, SSH key / certificate, bank card, note,
  other. Each kind starts with its own set of fields ("+ New entry"); the kind can be
  changed in the card, the fields are kept.
- An entry holds any number of "name — value" fields. "+ Add field" offers presets:
  token, login, password, site, mail, phone, valid until, PIN, 2FA key, private key —
  or a custom one. A field can be locked: the value is shown as dots and does not take
  part in search. So a login with a password and a token live in one entry.
- Per-field buttons: show, copy, generate a password, lock, delete.
- Manual order: ▲ ▼ under the list, `Alt+↑` / `Alt+↓` or the right-click menu. A new
  entry goes to the end.
- Lists: the "List" switcher above the search, the gear next to it creates, renames and
  deletes a list (its entries move to the main list). Move an entry — the "List" field
  in the card or right-click → "Move to list". Search covers all lists and says where
  the hit lives.
- Import from a variables file (Settings → Data → "Import from a .env file…"): `KEY=VALUE`
  lines are filed into the lists "Stands" (by stand prefix: `DEV_`, `QA_EU_`, `DEV_QA_`…),
  "Brokers" (`ALOR_TOKEN_<owner>`, `BYBIT_*`…), "Integrations" (`TELEGRAM_*`, `JIRA_*`…)
  and "Miscellaneous" (by the first word of the key). A plan without values is shown
  before writing. The main list is untouched; an entry with the same name gets the new
  fields. Own rules go first — in `settings.json`:

  ```
  "env_import": [{"prefix": "STRIPE_", "list": "Payments", "entry": "Stripe"}]
  ```
- Export (Settings → Data, `Ctrl+E`): the entries as text for reading or CSV for Excel,
  or everything as JSON (see "Data" below). The file is NOT encrypted; the app asks for
  the master password again.
- The clipboard is cleared 30 seconds after copying. On Windows a copied secret is
  marked so that the clipboard history (Win+V) and cloud sync do not keep it.
- Autosave one second after an edit. `Ctrl+S` saves at once.
- Locks (Settings → Security): the whole app after N idle minutes or when the window is
  minimized; a separate guard on the Passwords tab — the master password again or a PIN
  — that locks it again after N idle minutes or by the "🔒 Lock passwords" button. "No
  extra code" leaves just the master password at start. `Ctrl+L` locks the app by hand.
  Wrong codes are counted on every prompt: after three the prompt waits 1, 2, 4… s,
  after ten the whole app locks.
- Search by name, note, field names and open values. Locked values are not searched.
- Change the master password in Settings → Master password; the current one is asked
  first, and a new one gets a hint of how hard it is to guess. Copies made under the old
  password are re-encrypted, older automatic ones are deleted.
- Right-click → "Make a bookmark…" turns an entry into a bookmark tile.

Shortcuts: `Ctrl+N` new entry (login and password), `Ctrl+F` search, `Ctrl+E` export,
`Delete` in the list — delete. Without the mouse: `Ctrl+F`, type, `Enter` (the first hit
is on the card), `Ctrl+Shift+C` copies its password, `Ctrl+B` its login; `↓` walks from
the search into the list, `Esc` clears the search.

## Bookmarks

Tiles with links to the services you use, in groups (the gear above the group list
creates, renames and deletes them). A tile has an avatar — the first letter, two letters
or an emoji, in one of eight colours — a title and the host. A click opens the address.

A tile can be tied to a password entry: it then shows the entry's login (click — copy)
and copies its password; "↗ + 🔑" opens the address and copies the password in one go;
👁 shows the password for ten seconds. When the Passwords tab is guarded and locked, the
password row asks for the code first. The editor ties a new bookmark to the entry with
the same host by itself. Deleting an entry unties its bookmarks. A bookmark opens web
addresses only (`http`, `https`, `mailto`). From the keyboard: `Tab` to a tile, `Enter`
opens, `Shift+Enter` opens and copies the password, the menu key opens its menu. A tile
is drawn on one canvas (`daykeeper/bookmark_tile.py`): a tab of 200 tiles opens in well
under a second. Stored in the encrypted file (key `bookmarks`); model —
`daykeeper/bookmarks.py`, tests — `tests/test_bookmarks.py`.

## Journal

**Its shape is yours** (Settings → Journal, `daykeeper/journal_layout.py`): ready-made
layouts — tester (verified / failed / new bug, MR reviews and test cases; the default),
developer (tasks done / in progress / bugs fixed, MRs and code review), analyst or manager
(tasks, documents, decisions, meetings), plain diary (done / in the way) — and then any
column or kind of activity can be renamed, hidden, moved, added or removed. A kind has a
role: MR numbers, test cases (one shared "New tests" line), a list, or notes that go into
line 1. Each has a word for line 1 of the report and a command word for `daykeeper-cli`
(`pass`, `review`, or one made from the label — «Созвоны» → `sozvony`). The layout lives in
`settings.json` (key `journal`), so the helper, the hook and "Teach Claude" follow it.
Nothing written is deleted: a hidden or removed column keeps its items in the vault.
The lists of columns and kinds fit the settings window: a table row when it is wide,
the line-1 word and the role on a second line when it is narrower; "Save" stays at the
bottom while the lists scroll.

The "Journal" tab next to the passwords. A day is a card: "1. Done" (one line), three
columns "Tested successfully / Test failed / New bug" (one issue key per line, tracker
links turn into keys), "2. Plans", "3. Absence / extra info" ("None" by default). Stored
inside the same encrypted file. The buttons under the day ("Build report", "Links…",
"Set up the journal…", "Pick up from sessions") stay at the bottom of the pane; only the
day above them scrolls.

- "Build report" (`Ctrl+R`) assembles the standup text and copies it to the clipboard:
  issue keys and MR numbers expand into tracker and GitLab links.
- "Links…" — the tracker address (Jira: key `APP-1` → `<address>/browse/APP-1`), the
  GitLab address and the own repository (`group/repo`: its MRs are written short as
  `!42`, others as a full link). Stored in `settings.json` next to the vault
  (`jira_url`, `gitlab_url`, `gitlab_project`). Empty — keys and numbers stay text.
- The "Add" line with the buttons "Passed / Failed / Bug" and the MR menu adds by hand;
  several keys can be typed with spaces or commas. The columns and the "Activity"
  block are also edited directly (a line like `MR reviews: !42, !43`).
- "Activity" — MR reviews, opened and merged MRs, new manual test cases and autotests,
  miscellaneous. In the report it goes as lines under "1.". An empty "1. Done" line is
  built by itself: "RFT, reviewed MRs, new autotests".
- **Inbox from sessions.** Scripts and AI sessions drop results into
  `inbox.jsonl` next to the vault without opening the app. The command is the console
  helper: `daykeeper-cli.exe` in the built app (put into `%LOCALAPPDATA%\Daykeeper\bin\daykeeper-cli\`
  when Claude Code is connected), `python inbox.py` in the source tree:

  ```
  daykeeper-cli pass APP-1234 APP-5678
  daykeeper-cli fail APP-2938
  daykeeper-cli bug  APP-4127 --date 2026-10-12
  daykeeper-cli review !42
  daykeeper-cli mr-open !45
  daykeeper-cli mr-merged !44
  daykeeper-cli manual APP-101
  daykeeper-cli auto APP-101
  daykeeper-cli note "nightly run 1042 triage"
  daykeeper-cli --data D:\vaults pass APP-1      # a vault outside the default folder
  ```

  The app picks the file up when the vault is opened and once a minute, files the items
  by day (duplicates are skipped) and deletes the file. "Journal → Pick up from
  sessions" — by hand.
- **Claude Code and Codex** (Settings → AI assistants). Everything is done from the app, no
  file is edited by hand; each assistant is connected and taught on its own (Codex: the hook
  goes into `~/.codex/hooks.json`, the instructions into `~/.codex/AGENTS.md`, and Codex asks
  to trust a new hook once with `/hooks`):
  - "Connect" puts the helper in place and adds a PostToolUse hook to
    `~/.claude/settings.json` (only for `Bash`, `PowerShell` and MCP tools). The rest of
    the file stays as it was; a copy `settings.json.before-daykeeper` is kept before the
    first change; a file with a JSON error is not touched. "Disconnect" removes the entry.
    An older entry (the source-tree script, a moved app) shows as "connect again".
  - "What counts as an event" — ready-made sets: GitLab (`glab`: MR opened, merged,
    approved, a new thread in someone else's MR — the author is asked through `glab` and
    cached in `hook-cache.json`), GitHub (`gh pr create / merge / review`), the issue
    tracker (a new issue, a move to Test failed; REST and the Atlassian tools), verdict
    comments ("Tested on …" → pass, or fail when it says so).
  - "Own rules" — “if the command (the answer) has … — write …”: a piece of text or a
    regular expression, an optional success word in the answer, the journal line kind,
    and what to write (the issue key, the MR, the matched text or a fixed text).
  - "Where it works" — every project, or only some folders; and folders never to log.
  - "Test a command" — paste a command and its answer, see what would be written and by
    which rule. "Recent triggers" — the last events with the rule that found them, from
    `hook-log.jsonl` (no commands or answers are logged: they can hold tokens).
  - "Teach Claude to keep the journal…" — a block for `~/.claude/CLAUDE.md` (which kinds,
    the note language, the text can be edited) between `<!-- daykeeper:begin/end -->`
    markers: inserted again, it replaces itself; "Remove" takes it out.
  - The rules live in `settings.json` next to the vault, key `hook`. Code:
    `daykeeper/hookrules.py` (matching), `daykeeper/cli.py` (the helper),
    `daykeeper/claudecode.py` (the files), `daykeeper/claude_view.py` (the section).

**Undo and redo** — `Ctrl+Z` / `Ctrl+Y` (or `Ctrl+Shift+Z`) and ↶ ↷ in the top bar: the
last changes to passwords, the journal, notes, bookmarks and routines (100 steps or 20 MB
of kept state, whichever comes first; changes 0.7 s apart are one step). A step keeps only
the objects that changed — one entry, one day, one tile — so the history stays small on a
large vault. Inside a text field `Ctrl+Z` undoes the typing. Items from the inbox, a
routine's run, the script a routine accepted and a bookmark's fetched icon are not undone
(`daykeeper/undo.py`).

## Notes

The "Notes" tab — cards by day (`daykeeper/notes.py`, view — `daykeeper/notes_view.py`):
a title, text, a checklist and attached files.

- Checklist items are added from the "+ Add item" row at the bottom of the list: Enter
  adds and keeps typing. A tick moves the item under "Done (n)", which folds; "Clear"
  removes done items. A progress bar sits above the list. `Alt+↑/↓` moves an item,
  Backspace in an empty item removes it, right-click opens the item menu.
- "⋯" holds the card settings: a wide card over two columns, folding of done items,
  nine colours with an accent stripe, files, carrying undone items over to today,
  deletion. 📌 pins a card to the top of the day.
- 📎 attaches files by path (the vault keeps the link, so the file is always its current
  version). Markdown and text open in a built-in viewer: headings, lists, task boxes
  that tick in the file, quotes, code, tables, links; "Edit" switches to the raw text and
  saves it back. Other files open in the system app. "📎 With files" on the left lists
  every card with files.
- Search across all days covers titles, text, items and file names; "Unfinished" lists
  every card with open items.

Stored in the same encrypted file (key `notes`). Tests — `tests/test_notes.py`,
`tests/test_markdown.py`.

## Routine

The "Routine" tab — quick-launch tiles (`daykeeper/routines.py`, view —
`daykeeper/routines_view.py`). A tile starts a script (`.py`, `.ps1`, `.bat`/`.cmd`,
`.sh` or a program, with arguments and a working folder), a shell command, a link, a
folder or a file. ▶ runs it: scripts and commands show an output window with the live
log, Stop (it ends the script with everything it started) and Run again; "Open in a
separate console window" is for interactive scripts. `Tab` reaches a tile, `Enter` runs it.
A `.ps1` script always runs with `-ExecutionPolicy Bypass`: adding a tile is the
consent to run that script, whatever the machine-wide PowerShell policy says — and the
consent is to the file as it is, not to the path: saving the tile or running it by hand
records a hash of the script (and of what a shortcut points to), and an automatic run
starts only that version. A changed or never-accepted script waits for a run by hand,
which asks before accepting the new version; the editor warns when the script sits on a
network share, in Downloads or outside the profile folder.

A routine can start by itself each time the vault is unlocked, or at a time of day on
chosen weekdays — once a day; if the app was closed at that time, it runs when the app
is opened later that day. Automatic runs are quiet: the result goes to the run log on the
left (double-click shows the output), a failure also pops up in the corner of the screen.
Routines live in the encrypted, authenticated file, so nothing can be added to the
auto-start list without the master password. Tests — `tests/test_routines.py`.

## Integrations

The "Integrations" tab. **GitLab**: the address, a password entry whose secret field holds
a personal access token with the `read_api` scope, and how often to check (2–60 minutes).
"Watch" turns it on. The first check only remembers the state; after that the app reports,
for the rules ticked on the tab:

- an approval on my MR, all threads on my MR resolved, its pipeline failed, it was merged;
- I was asked to review an MR; my threads were resolved or answered in an MR I review.

"Projects" lists the projects with my open MRs after the first check; ticked ones are the
only ones watched, none ticked — all. A check asks about every MR once an hour; in
between, new approvals and pipelines come with one request a project, and the threads of
an MR are read only when its count of notes changed or my threads in it are still open.
The `updated_at` of an MR does not tell: approvals, finished pipelines and resolved
threads leave it as it was.

Events go to the feed on the tab (unread ones are counted on the tab button), to pop-ups
in the screen corner that open the MR on click, and the taskbar button flashes on Windows.
Checks run in the background only while the vault is open. Jira is listed as the next
connection. Watcher logic — `daykeeper/gitlab.py`, tests against a fake API —
`tests/test_gitlab.py`.

## Languages

English and Russian. The language is picked in Settings → General (or the link on the
unlock screen) and stored in `settings.json`; without a setting the system language
decides (Windows UI language, macOS locale, `LANG` elsewhere). Code and comments are English; the Russian catalog is `daykeeper/locale_ru.py`.
Report headings follow the UI language.

## Data: export, import, copies

Settings → Data:

- **Everything as JSON** — the export dialog's third format: every tab (entries, journal,
  notes, bookmarks, routines, integrations) as readable JSON, to keep or to move. Not the
  lock settings: a PIN hash means nothing outside, and an import must not switch locks off.
- **Import everything from an export** replaces every tab with such a file after the master
  password. The vault as it was is kept beside it first (`vault-before-import-<time>.pkv`).
- **Copies** — the automatic ones from `backup\` and the ones kept beside the vault: a
  conflict, before an import, before a restore. "Open" shows a copy in a second window, on
  a duplicate in a temporary folder, so looking changes nothing; "Restore…" makes it the
  vault (the current one is kept beside it first) and locks the app.
- **A conflict** — another window or a sync tool saved the vault file: nothing is written
  over it; what was changed here is offered as `vault-conflict-<time>.pkv` on close or lock.
- An error in a click or a timer goes to `errors.log` next to the vault (the traceback,
  no field values) and a short dialog: a built app has no console.

## Security

- The master password becomes a key through Argon2id (64 MiB, 6 iterations, about 0.1 s
  on a desktop of 2026; files written with 3 open as before), on a background thread: the
  window keeps painting while it works. Wrong passwords on the unlock screen wait like
  wrong PIN codes do.
- The value of every secret field is sealed a second time (AES-256-GCM) with a field key
  that lives inside the encrypted content. In memory it stays sealed and is opened only
  for the moment it is shown or copied, so the content decrypted at unlock, each autosave
  and the undo history hold no secret in the clear. A lock zeroes the master key and the
  field key. The file is marked `PKV2`; version 1.0 refuses it rather than open it with
  empty passwords; a `PKV1` file is converted on the first unlock (the old one stays in
  `backup`).
- The content is encrypted with AES-256-GCM. A tampered or damaged file does not open.
  The Argon2 parameters in the file header are checked before a key is derived and may be
  at most four times the defaults, so a forged header cannot exhaust memory or freeze the
  window.
- Everything — entries, journal, notes, bookmarks, routines, integration settings and the
  lock settings — is inside the encrypted file; `settings.json` holds only the window,
  the language and the report links.
- Writes are atomic: a temporary file first, then a rename. On macOS and Linux the data
  folder is `0700` and the files are `0600`.
- Idle locks, the Passwords tab guard with its count of wrong codes, the private clipboard
  and re-encrypted copies — see "Passwords" above. A lock closes every window that shows
  vault data (the settings, an opened file, the output of a run).
- The GitLab token goes only over https and only to the server the user typed — a
  redirect to another host or another port of it stops the request.
- A link in an attached document never starts a program (its folder opens); a rule of the
  hook never writes text that looks like a token or a key into the journal.
- The CSV export puts an apostrophe before values starting with `=`, `+`, `-` or `@`, so
  spreadsheets show them as text instead of running them as formulas.
- A forgotten master password cannot be recovered.

Limitation: Python cannot wipe strings from memory. A secret that was shown or copied,
and the entry open on the card, may stay in memory after the lock until it is overwritten;
the keys are wiped, the rest of the secrets never were plain. Someone who can read the
memory of the running app while it is unlocked can read the vault — as with any password
manager.

## Code map

`daykeeper/` is three layers. Pure modules without tkinter — the models (`vault`,
`journal`, `notes`, `bookmarks`, `routines`, `integrations`, `security`, `settings`),
the GitLab client (`gitlab`), the site icons (`siteicon`), the Markdown parser, the
import and export, `revisions` (what changed since a tab was drawn), `layout`
(geometry maths), `palette` (colours and fonts, the light and dark themes), `boot` (the
theme is taken before the window's modules are imported), `errlog` (`errors.log`),
`i18n` with `locale_ru`. Services
without tkinter — `session` (the vault's life: open, save, lock, the inbox, the
export), `jobs` (a thread pool read on the window's thread), `runner` (a routine's
subprocess), `clipboard`, `win32` (every call into Windows). The window — `app`
(the window, the unlock screen, the tab bar, the shortcuts), `style` (the ttk look),
`tabpage` (what a tab is made of) and one `*_view` module per tab (`vault_view`,
`journal_view`, `notes_view`, `bookmarks_view` with `bookmark_tile`, `routines_view`,
`integrations_view`), with `lock_view` (the locks), `settings_view` (the settings window),
`data_dialogs` (import and export), `widgets`, `dialogs`, `toast`, `popup`, `theme`,
`md_view`. A tab reaches the window only through `tabpage.Shell`; a tab is made on its
first visit.

## Tests

```
.venv\Scripts\python -m unittest discover tests
```

The models, the session, the timers, the jobs and the runner are tested without a
window; `tests/test_app_smoke.py`, `tests/test_audit_fixes.py` and `tests/test_audit_1016.py` open the whole window
on a temporary vault (every tab, the locks, a conflict, the dark theme in a separate
process) and are skipped where there is no display.

Lint and types — `ruff` over everything, `mypy` over the pure modules (`pyproject.toml`);
CI runs both before the tests:

```
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m mypy
```

## License

MIT, see [LICENSE](LICENSE).
