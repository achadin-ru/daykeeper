# -*- mode: python -*-
# The Qt window as a folder (the LGPL needs Qt's libraries as files of their own, and a
# folder starts three times faster than one file): dist/Daykeeper-qt/, and on macOS
# dist/Daykeeper Qt.app. Build: python -m PyInstaller --noconfirm --clean packaging/daykeeper-qt.spec
# It carries the console helper when dist/daykeeper-cli/ is there (build it first, as the
# Tk build does), the assets, and the license texts of Qt / Qt for Python.
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, SPECPATH)
from qt_filter import QT_EXCLUDE_MODULES, filt  # noqa: E402

MAC = sys.platform == "darwin"
datas = [(os.path.join(ROOT, "assets"), "assets"), (os.path.join(ROOT, "licenses"), "licenses"), (os.path.join(ROOT, "THIRD_PARTY.md"), ".")]
helper = os.path.join(ROOT, "dist", "daykeeper-cli")
if os.path.isdir(helper) and not MAC:
    datas.append((helper, "daykeeper-cli"))

a = Analysis([os.path.join(ROOT, "main_qt.py")], pathex=[ROOT], datas=datas, excludes=QT_EXCLUDE_MODULES, noarchive=False)
a.binaries = filt(a.binaries)
a.datas = filt(a.datas)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Daykeeper", console=False, upx=False,
          icon=os.path.join(ROOT, "assets", "icon.icns" if MAC else "icon.ico"))
coll = COLLECT(exe, a.binaries, a.datas, name="Daykeeper-qt", upx=False)
if MAC:
    sys.path.insert(0, ROOT)
    from daykeeper import __version__  # noqa: E402

    version = __version__.split("-")[0]  # macOS takes digits and dots only: 1.1.2-beta.2 -> 1.1.2
    # the version (PyInstaller writes 0.0.0) and the minimum system of the PySide6 wheel,
    # as build.sh puts them into the Tk bundle
    app = BUNDLE(coll, name="Daykeeper Qt.app", icon=os.path.join(ROOT, "assets", "icon.icns"), bundle_identifier="ru.achadin.daykeeper.qt",
                 version=version, info_plist={"CFBundleVersion": version, "LSMinimumSystemVersion": "14.0", "NSHighResolutionCapable": True})
