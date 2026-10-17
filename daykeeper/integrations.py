"""Integrations kept in the vault (key `integrations`): GitLab settings, the last snapshot
of my merge requests, the last look at the watched branches and the feed of notifications. Pure (no tkinter, no network)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .gitlab import FEED_KEEP, FULL_EVERY, CheckResult, Event, GitLabSettings


@dataclass
class Integrations:
    gitlab: GitLabSettings = field(default_factory=GitLabSettings)
    gitlab_state: dict[str, dict] = field(default_factory=dict)  # the last snapshot; {} — none yet
    gitlab_checked: float = 0.0  # when the last check finished
    gitlab_synced: float = 0.0  # when the last check that worked began: a light one looks back to it
    gitlab_full: float = 0.0  # when the last full check began
    gitlab_projects: list[str] = field(default_factory=list)  # the projects with my open MRs, watched or not
    gitlab_pipelines: dict[str, dict] = field(default_factory=dict)  # watch key → its last pipeline and head; {} — none yet
    feed: list[Event] = field(default_factory=list)  # newest first

    def add_events(self, events: list[Event]) -> None:
        self.feed = sorted(events, key=lambda e: e.ts, reverse=True) + self.feed
        del self.feed[FEED_KEEP:]

    def since(self, now: float) -> float:
        """Where the next check looks back to: 0 — a full check (the first one, once an
        hour, or after the watched projects changed)."""
        if not self.gitlab_state or not self.gitlab_synced or now - self.gitlab_full >= FULL_EVERY:
            return 0.0
        return self.gitlab_synced

    def apply_check(self, result: CheckResult, now: float) -> bool:
        """Take in what a check brought: the user, the snapshot, the events into the feed.
        True when it was the first check (there was no snapshot to compare against)."""
        self.gitlab_checked = now
        self.gitlab_synced = result.started
        if result.full:
            self.gitlab_full = result.started
        self.gitlab_projects = sorted(set(result.projects) | set(self.gitlab.projects))  # a chosen one stays listed
        self.gitlab.username = result.username
        first = not self.gitlab_state
        self.gitlab_state = result.snapshot
        if result.pipelines is not None:
            self.gitlab_pipelines = result.pipelines
        if result.events:
            self.add_events(result.events)
        return first

    def note_failed_check(self, now: float) -> None:
        """A check that failed still counts as done: the next one waits the whole interval."""
        self.gitlab_checked = now

    def unread(self) -> int:
        return sum(1 for e in self.feed if not e.read)

    def mark_all_read(self) -> None:
        for e in self.feed:
            e.read = True

    def clear_feed(self) -> None:
        self.feed = []

    def to_dict(self) -> dict:
        return {
            "gitlab": self.gitlab.to_dict(),
            "gitlab_state": self.gitlab_state,
            "gitlab_checked": self.gitlab_checked,
            "gitlab_synced": self.gitlab_synced,
            "gitlab_full": self.gitlab_full,
            "gitlab_projects": list(self.gitlab_projects),
            "gitlab_pipelines": self.gitlab_pipelines,
            "feed": [e.to_dict() for e in self.feed],
        }

    @classmethod
    def from_dict(cls, d) -> Integrations:
        if not isinstance(d, dict):
            return cls()
        state = d.get("gitlab_state")
        pipes = d.get("gitlab_pipelines")
        return cls(
            gitlab=GitLabSettings.from_dict(d.get("gitlab")),
            gitlab_state=state if isinstance(state, dict) else {},
            gitlab_checked=_number(d.get("gitlab_checked")),
            gitlab_synced=_number(d.get("gitlab_synced")),
            gitlab_full=_number(d.get("gitlab_full")),
            gitlab_projects=[x for x in projects if isinstance(x, str)] if isinstance(projects := d.get("gitlab_projects"), list) else [],
            gitlab_pipelines={k: v for k, v in pipes.items() if isinstance(k, str) and isinstance(v, dict)} if isinstance(pipes, dict) else {},
            feed=[Event.from_dict(e) for e in d.get("feed", []) if isinstance(e, dict)][:FEED_KEEP],
        )


def _number(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
