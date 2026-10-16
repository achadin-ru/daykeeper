"""A small always-on-top card in the bottom-right corner of the screen; several stack."""

from __future__ import annotations

import tkinter as tk
import webbrowser

from . import palette
from .popup import round_corners

TOAST_MS = 8_000


class Toast(tk.Toplevel):
    """A card with a title and a text; a click opens the link or runs `action`."""

    stack: list[Toast] = []
    closes_on_lock = True  # App.close_child_windows: an MR title or a run's result must not outlive the lock

    def on_lock(self) -> None:
        if self in Toast.stack:
            Toast.stack.remove(self)
        self.after_cancel(self._close_job)  # the window goes now; its timer must not fire on a dead window

    def __init__(self, master: tk.Misc, title: str, text: str, url: str = "", action=None):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=palette.BORDER)
        round_corners(self, palette.BORDER)
        body = tk.Frame(self, bg=palette.WHITE, padx=14, pady=10, cursor="hand2" if url or action else "")
        body.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(body, text=title, bg=palette.WHITE, fg=palette.MUTED, font=(palette.FONT, palette.pt(9), "bold"), anchor="w").pack(fill="x")
        tk.Label(body, text=text, bg=palette.WHITE, fg=palette.TEXT, font=(palette.FONT, palette.pt(11)), anchor="w", justify="left", wraplength=340).pack(fill="x", pady=(2, 0))
        for w in (body, *body.winfo_children()):
            w.bind("<Button-1>", lambda _e: self._click(url, action))
        Toast.stack.append(self)
        self.update_idletasks()
        self._place()
        self._close_job = self.after(TOAST_MS, self._close)

    def _place(self) -> None:
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        y = sh - 60
        for toast in Toast.stack:
            if not toast.winfo_exists():
                continue
            toast.update_idletasks()
            w, h = toast.winfo_reqwidth(), toast.winfo_reqheight()
            y -= h + 8
            toast.geometry(f"{max(w, 360)}x{h}+{sw - max(w, 360) - 24}+{y}")

    def _click(self, url: str, action=None) -> None:
        if url:
            webbrowser.open(url)
        self._close()
        if action is not None:
            action()

    def _close(self) -> None:
        if self in Toast.stack:
            Toast.stack.remove(self)
        if self.winfo_exists():
            self.destroy()
        for toast in Toast.stack:
            if toast.winfo_exists():
                toast._place()
                break
