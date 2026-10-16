"""Undo and redo for the whole app: per-object snapshots of the parts of the vault the user edits.

A domain (the entries, the journal, notes, bookmarks, routines) is read as a dictionary
`key → state`: one entry, one day, one note, one tile at a time, each serialised on its own.
A step keeps only what changed since the last capture — `{domain: {key: state before}}`,
with None for an object that did not exist yet — so a step after one edit is a few hundred
bytes, not the whole domain (at 10 000 entries every step was 4.8 MB and a hundred steps
half a gigabyte). The window calls `capture` a moment after the user stops changing things
(typing in a field is one step, not one per letter); `undo` writes the states of the last
step back, object by object, and keeps the current ones for `redo`. A change the user did
not make — journal items from the inbox, say — calls `rebase`: the steps of that domain
are dropped, so an undo can never wipe what came in on its own.

The history is bounded twice: LIMIT steps, and BUDGET bytes of kept states (the oldest
steps go first). Pure: no tkinter, no vault import; the vault serialises and restores."""

from __future__ import annotations

from collections.abc import Callable

LIMIT = 100  # steps kept; the oldest goes first
BUDGET = 20 * 1024 * 1024  # bytes of states kept across the undo steps

States = dict[str, str]  # key → serialised object
Step = dict[str, dict[str, str | None]]  # domain → {key: state before, None when it did not exist}


class History:
    def __init__(self, read: Callable[[str], States], domains: tuple[str, ...], limit: int = LIMIT, budget: int = BUDGET):
        self.read, self.domains, self.limit, self.budget = read, domains, limit, budget
        self.last: dict[str, States] = {}
        self.undo_steps: list[Step] = []
        self.redo_steps: list[Step] = []

    def reset(self) -> None:
        """A fresh start (a vault opened): nothing to undo."""
        self.last = {d: self.read(d) for d in self.domains}
        self.undo_steps.clear()
        self.redo_steps.clear()

    @property
    def can_undo(self) -> bool:
        return bool(self.undo_steps)

    @property
    def can_redo(self) -> bool:
        return bool(self.redo_steps)

    @property
    def bytes(self) -> int:
        """What the kept steps hold, in characters of state."""
        return sum(len(s) for stack in (self.undo_steps, self.redo_steps) for step in stack for states in step.values() for s in states.values() if s)

    @staticmethod
    def _diff(before: States, now: States) -> dict[str, str | None]:
        """What `before` held where `now` differs: the old state, None where the object is new."""
        changed: dict[str, str | None] = {k: before.get(k) for k in now if before.get(k) != now[k]}
        changed.update({k: v for k, v in before.items() if k not in now})
        return changed

    def capture(self) -> bool:
        """Record what changed since the last capture as one step. True — a step was added."""
        step: Step = {}
        for d in self.domains:
            now = self.read(d)
            diff = self._diff(self.last.get(d, {}), now)
            if diff:
                step[d] = diff
                self.last[d] = now
        if not step:
            return False
        self.undo_steps.append(step)
        self.redo_steps.clear()
        self._trim()
        return True

    def _trim(self) -> None:
        del self.undo_steps[: -self.limit]
        while len(self.undo_steps) > 1 and self.bytes > self.budget:
            del self.undo_steps[0]

    def rebase(self, domain: str) -> None:
        """`domain` changed by itself: its steps go, its current state is the new start."""
        for stack in (self.undo_steps, self.redo_steps):
            for step in stack:
                step.pop(domain, None)
            stack[:] = [s for s in stack if s]
        self.last[domain] = self.read(domain)

    def _swap(self, take: list[Step], give: list[Step], write: Callable[[str, str, str | None], None]) -> set[str]:
        self.capture()  # a change still waiting becomes its own step first
        if not take:
            return set()
        step = take.pop()
        give.append({d: {k: self.last.get(d, {}).get(k) for k in states} for d, states in step.items()})
        for d, states in step.items():
            current = self.last.setdefault(d, {})
            for key, state in states.items():
                write(d, key, state)
                if state is None:
                    current.pop(key, None)
                else:
                    current[key] = state
        return set(step)

    def undo(self, write: Callable[[str, str, str | None], None]) -> set[str]:
        """Put the last step back through `write(domain, key, state)` (state None — remove
        the object); the domains it touched (empty — nothing to undo)."""
        return self._swap(self.undo_steps, self.redo_steps, write)

    def redo(self, write: Callable[[str, str, str | None], None]) -> set[str]:
        """Bring back what the last undo put away; a new change since then ends the redo."""
        return self._swap(self.redo_steps, self.undo_steps, write)
