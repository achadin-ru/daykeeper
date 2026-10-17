"""The Qt (PySide6) window of Daykeeper, built next to the Tk one until the two are compared.

Everything that imports PySide6 lives in this package and nowhere else: the core, the
hook and `daykeeper-cli` must start without Qt (tests/test_qt_package.py keeps it so)."""
