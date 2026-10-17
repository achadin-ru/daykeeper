"""Placeholder of the integrations tab while it is being ported."""

from __future__ import annotations

from .base import TabPage


class IntegrationsPage(TabPage):
    name = "integrations"

    def build(self) -> None:
        pass


class GitLabWatcher:
    def __init__(self, shell) -> None:
        self.shell = shell

    def close(self) -> None:
        pass
