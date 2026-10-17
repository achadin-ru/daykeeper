"""Speed measurements of the Qt tabs on big data, shared by the unit tests (which only catch
a gross slowdown: SLACK times the target, a loaded machine must not fail them) and
tests/bench_qt_tabs.py (which prints the numbers against the targets of the port plan,
section 4). Every number includes the layout and the painting (repaint)."""
from __future__ import annotations

import os
import statistics
import tempfile
import time

from tests.qt_helpers import close_window, open_window

SLACK = 10 if os.environ.get("CI") else 3  # the unit tests fail only past this many times the target (a CI runner is slow and shared)

# the targets of the port plan (section 4), in seconds
TARGETS = {
    "vault_open": 0.5, "vault_search": 0.1,
    "tiles_first": 0.3, "tiles_resize": 0.05,
    "notes_first": 0.3, "notes_back": 0.05, "notes_resize": 0.05, "notes_key": 0.010,
}


def _resize_steps(win, widths, height=800) -> list[float]:
    from PySide6.QtWidgets import QApplication

    steps = []
    for w in widths:
        t = time.perf_counter()
        win.resize(w, height)
        QApplication.processEvents()
        win.repaint()
        steps.append(time.perf_counter() - t)
    return steps


def measure_vault(entries: int = 10_000) -> dict[str, float]:
    from PySide6.QtWidgets import QApplication

    from daykeeper.vault import Field

    with tempfile.TemporaryDirectory() as tmp:
        win = open_window(tmp)
        for i in range(entries):
            e = win.vault.add_entry(name=f"Site {i}", kind="note")
            e.fields.append(Field("login", f"user{i}@example.com"))
        t = time.perf_counter()
        win.show_main()
        win.repaint()
        QApplication.processEvents()
        opened = time.perf_counter() - t
        page = win.page("vault")
        page.search.setText(f"user{entries - 1}")
        t = time.perf_counter()
        page._search_later.flush()
        page.list.repaint()
        searched = time.perf_counter() - t
        found = [e.name for e in page.model.rows]
        close_window(win)
    assert found == [f"Site {entries - 1}"], found
    return {"vault_open": opened, "vault_search": searched}


def measure_bookmarks(tiles: int = 300) -> dict[str, float]:
    from PySide6.QtWidgets import QApplication

    with tempfile.TemporaryDirectory() as tmp:
        win = open_window(tmp)
        for i in range(tiles):
            win.vault.bookmarks.add(f"Site {i}", f"https://s{i}.example.com", "")
        win.show_main()
        t = time.perf_counter()
        win.show_page("bookmarks")
        win.repaint()
        QApplication.processEvents()
        first = time.perf_counter() - t
        steps = _resize_steps(win, (900, 1300, 1000, 1600, 1100))
        close_window(win)
    return {"tiles_first": first, "tiles_resize": max(steps)}


def measure_notes() -> dict[str, float]:
    """The day of the report of 17.10: 16 cards, 120 items, one note of 30 KB."""
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from daykeeper.journal import today

    with tempfile.TemporaryDirectory() as tmp:
        win = open_window(tmp)
        notes = win.vault.notes
        for c in range(16):
            n = notes.add(today(), title=f"Card {c}", text="some text\nmore" if c else ("Lorem ipsum dolor sit amet " * 1200)[:30_000])
            for i in range(120 // 16 + (1 if c < 120 % 16 else 0)):
                n.add_item(f"item {i} of card {c} " + ("with a longer tail " * (i % 3)))
        win.session.touch("notes")
        win.show_page("vault")
        t = time.perf_counter()
        win.show_page("notes")
        win.repaint()
        QApplication.processEvents()
        first = time.perf_counter() - t
        win.show_page("vault")
        t = time.perf_counter()
        win.show_page("notes")
        win.repaint()
        QApplication.processEvents()
        back = time.perf_counter() - t
        steps = _resize_steps(win, (1000, 1400, 1100, 1600, 1200))
        page = win.page("notes")
        big = next(c for c in page.cards.values() if len(c.note.text) > 20_000)
        big.edit("text")
        editor = big.editor
        editor.moveCursor(editor.textCursor().MoveOperation.Start)
        keys = []
        for _ in range(20):
            t = time.perf_counter()
            QTest.keyClick(editor, "b")
            QApplication.processEvents()
            keys.append(time.perf_counter() - t)
        close_window(win)
    return {"notes_first": first, "notes_back": back, "notes_resize": max(steps), "notes_key": statistics.median(keys)}


def report(results: dict[str, float]) -> str:
    return ", ".join(f"{k} {v * 1000:.1f} ms (target {TARGETS[k] * 1000:.0f})" for k, v in results.items())
