"""Entry point of the Qt window: python main_qt.py [path_to_vault_file] [--preview] [--minimized].

Built next to the Tk window (main.py) until the two are compared. No `boot.apply_theme`:
the theme is an object here and is put on the application, not swapped into modules."""

import os
import sys

# a plugin path from the environment would load a foreign Qt plugin into the process that
# holds the vault: gone before Qt reads it, and the plugins taken from PySide6's own folder
for _name in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
    os.environ.pop(_name, None)


def main() -> int:
    import threading
    from pathlib import Path

    import PySide6
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    from daykeeper import autostart
    from daykeeper.boot import theme_for
    from daykeeper.session import remove_preview_copy

    QCoreApplication.setLibraryPaths([str(Path(PySide6.__file__).resolve().parent / "plugins")])
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")  # the same base on Windows and macOS; our style sheet goes over it

    from daykeeper.qt import theme
    from daykeeper.qt.app import MainWindow, install_excepthook

    argv = sys.argv[1:]
    flags = {"--preview", autostart.MINIMIZED}
    args = [a for a in argv if a not in flags]
    theme.apply(app, theme.theme_named(theme_for(argv)))
    window = MainWindow(Path(args[0]) if args else None, preview="--preview" in argv)
    install_excepthook(window)
    window.show_as_saved(minimized=autostart.MINIMIZED in argv)
    code = app.exec()
    if "--preview" in argv:
        remove_preview_copy(window.vault_path)  # the duplicate made for the look goes with the window
    # a site icon or a GitLab check may still be waiting on the network; the window is gone
    # and everything is saved, so the process does not wait for them
    if any(t.is_alive() and not t.daemon for t in threading.enumerate() if t is not threading.main_thread()):
        os._exit(code)
    return code


if __name__ == "__main__":
    sys.exit(main())
