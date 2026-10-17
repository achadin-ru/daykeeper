"""Placeholder of the settings window while it is being ported."""

from PySide6.QtWidgets import QDialog


class SettingsWindow(QDialog):
    def __init__(self, shell, section: str = "general"):
        super().__init__(shell)
        self.section = section

    def show_section(self, section: str) -> None:
        self.section = section
