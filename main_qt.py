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

    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    from daykeeper import autostart
    from daykeeper.boot import theme_for
    from daykeeper.session import remove_preview_copy

    QCoreApplication.setLibraryPaths([plugin_folder()])
    app = QApplication(sys.argv[:1])

    from daykeeper.qt import theme

    app.setStyle(theme.make_style())  # Fusion on Windows and macOS alike, our check boxes; the style sheet goes over it
    from daykeeper.qt.app import MainWindow, install_excepthook

    argv = sys.argv[1:]
    flags = {"--preview", autostart.MINIMIZED}
    args = [a for a in argv if a not in flags]
    theme.apply(app, theme.theme_named(theme_for(argv)))
    window = MainWindow(Path(args[0]) if args else None, preview="--preview" in argv)
    install_excepthook(window)
    # logging off or shutting down: Windows asks every program before the windows go (Qt's
    # commitData); what is typed is saved then, whether or not a close event follows
    def save_for_logoff(_manager) -> None:
        window.flush_edits()
        window.save_now()

    app.commitDataRequest.connect(save_for_logoff)
    window.show_as_saved(minimized=autostart.MINIMIZED in argv)
    # refresh_autostart is not called while the Qt build lives next to the Tk one: the
    # entry of the Tk build would start the Qt build from then on. It is turned on with the
    # switch to the Qt window (stage 11 of the port).
    code = app.exec()
    if "--preview" in argv:
        remove_preview_copy(window.vault_path)  # the duplicate made for the look goes with the window
    # a site icon or a GitLab check may still be waiting on the network; the window is gone
    # and everything is saved, so the process does not wait for them
    if any(t.is_alive() and not t.daemon for t in threading.enumerate() if t is not threading.main_thread()):
        os._exit(code)
    return code


def plugin_folder() -> str:
    """Qt's plugins inside PySide6, as Qt's own qt.conf there says: PySide6/plugins on
    Windows, PySide6/Qt/plugins on macOS and Linux (a fixed "plugins" left macOS without
    its platform plugin, and the window could not start there)."""
    from pathlib import Path

    import PySide6
    from PySide6.QtCore import QLibraryInfo

    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)
    return folder if Path(folder).is_dir() else str(Path(PySide6.__file__).resolve().parent / "plugins")


def refresh_autostart(vault_path) -> None:
    """An autostart entry left by a copy of the app elsewhere now starts this one (as
    app._refresh_autostart of the Tk window); see main() for why it waits."""
    from daykeeper import autostart
    from daykeeper.vault import default_vault_path

    try:
        autostart.refresh(autostart.launch_args(autostart.is_minimized(), vault_path, default_vault_path()))
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
