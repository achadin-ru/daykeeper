# Third-party software

## Qt and Qt for Python (PySide6) — the Qt build only

The Qt build of Daykeeper (`main_qt.py`, the `Daykeeper-qt` folder) uses
[Qt](https://www.qt.io/) and [Qt for Python (PySide6, shiboken6)](https://doc.qt.io/qtforpython-6/),
© The Qt Company Ltd. and other contributors, under the
[GNU Lesser General Public License version 3](licenses/LGPL-3.0.txt), which builds on the
[GNU General Public License version 3](licenses/GPL-3.0.txt).

- Qt's libraries ship as separate files in the build folder, unmodified. You may replace
  them with your own build of the same Qt version; Daykeeper does not check them.
- The source code of Qt and of Qt for Python is available from
  [download.qt.io](https://download.qt.io/official_releases/) and
  [code.qt.io](https://code.qt.io/cgit/pyside/pyside-setup.git/) (the version is in
  `requirements-build.txt`).

## cryptography

The vault is encrypted with [cryptography](https://cryptography.io/) (Apache 2.0 / BSD).
