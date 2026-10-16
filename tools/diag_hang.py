"""Which change makes the hidden-window smoke run hang on macOS. Each variant in its own
process with a time limit; prints the exit code (124 = hung) and, on a hang, the stacks.

    python tools/diag_hang.py
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VARIANTS = ["none", "noflow", "nokeeper", "nofit", "nointeg", "noprop", "all"]

if len(sys.argv) == 1:
    for v in VARIANTS:
        t0 = time.time()
        try:
            code = subprocess.run([sys.executable, "-X", "faulthandler", __file__, v], cwd=ROOT, timeout=90).returncode
        except subprocess.TimeoutExpired:
            code = 124
        print(f"RESULT {v:9} exit {code}  ({time.time() - t0:.1f} s)", flush=True)
    sys.exit(0)

variant = sys.argv[1]
sys.path.insert(0, str(ROOT))
import faulthandler  # noqa: E402

faulthandler.dump_traceback_later(60, exit=True)
tmp = Path(tempfile.mkdtemp())
for key in ("HOME", "APPDATA", "LOCALAPPDATA", "CLAUDE_CONFIG_DIR"):
    os.environ[key] = str(tmp / key.lower())

import tkinter as tk  # noqa: E402

from daykeeper import sidebar, widgets  # noqa: E402
from daykeeper.settings import write_settings  # noqa: E402

if variant in ("noflow", "all"):
    def plain(self):
        if getattr(self, "_plain_done", 0) == len(self._items):
            return
        self._plain_done = len(self._items)
        for w, padx, side in self._items:
            w.place_forget()
            w.pack(side=side, padx=padx)
    widgets.FlowRow.reflow = plain
if variant in ("nokeeper", "all"):
    def place(self, _event=None):
        if self._dragging:
            return
        total = self.paned.winfo_width()
        if total <= 1:
            return
        want = self.target(total)
        if self.paned.sashpos(0) != want:
            self.paned.sashpos(0, want)
    sidebar.SidebarKeeper.place = place
if variant in ("noprop", "all"):
    tk.Misc.pack_propagate = lambda self, flag=None: None
from daykeeper.app import App  # noqa: E402
from daykeeper.integrations_view import IntegrationsPage  # noqa: E402
from daykeeper.vault_view import VaultPage  # noqa: E402

if variant in ("nofit", "all"):
    VaultPage._fit_fields = lambda self, event: None
if variant in ("nointeg", "all"):
    IntegrationsPage._fit_settings = lambda self: None

write_settings(tmp / "settings.json", {"language": "en", "geometry": "1200x760+20+20"})
app = App(tmp / "smoke.pkv", preview=True)
app.withdraw()  # as the smoke test does
app.unlock("smoke-master-1")
for _ in range(3):
    app.update()
    app.after(5)
app.update()
print(f"{variant}: survived", flush=True)
app.destroy()
