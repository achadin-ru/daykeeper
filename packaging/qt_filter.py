"""What the Qt build leaves out (the port plan 5.1, measured by the PySide6 probe: 119 MB
without this, 59 MB with it). Imported by daykeeper-qt.spec.

The app uses QtCore, QtGui and QtWidgets only. Out go the other Qt modules and their DLLs,
the software OpenGL renderer, the plugins nothing here loads (the virtual keyboard pulls in
Quick and Qml, the TLS ones a second OpenSSL), every image format plugin but ICO (Qt reads
PNG without one) and every translation but Qt's own Russian and English."""

from __future__ import annotations

import re

QT_EXCLUDE_MODULES = [
    "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
    "PySide6.QtSvg", "PySide6.QtSvgWidgets", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtCharts", "PySide6.QtMultimedia", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.Qt3DCore", "PySide6.QtWebChannel", "PySide6.QtWebSockets",
    "tkinter", "_tkinter",
]
_BAD = re.compile(r"""(?ix)
  (^|[\/])(
    (lib)?Qt6?(WebEngine|Quick|Qml|3D|Charts|Multimedia|Network|Sql|Test|Pdf|Svg|OpenGL|VirtualKeyboard|WebChannel|WebSockets)\w*(\.dll|\.so[.\d]*|\.dylib)?
  | Qt(WebEngine|Quick|Qml|3D|Charts|Multimedia|Network|Sql|Test|Pdf|Svg|OpenGL)\w*\.(pyd|so|abi3\.so)
  | opengl32sw\.dll | d3dcompiler\w*\.dll
  )$
  | [\/]plugins[\/](platforminputcontexts|tls|networkinformation|iconengines|generic|qmltooling)[\/]
  | [\/]plugins[\/]platforms[\/](qminimal|qdirect2d)\.dll$
""")
_IMG_KEEP = re.compile(r"(?i)[\/]plugins[\/]imageformats[\/](lib)?qico\.(dll|dylib|so)$")
_IMG = re.compile(r"(?i)[\/]plugins[\/]imageformats[\/]")
_TR = re.compile(r"(?i)[\/]translations[\/]")
_TR_KEEP = re.compile(r"(?i)[\/]translations[\/]qtbase_(ru|en)\.qm$")


def keep(dest: str) -> bool:
    d = "/" + dest.replace("\\", "/")
    if _BAD.search(d):
        return False
    if _IMG.search(d) and not _IMG_KEEP.search(d):
        return False
    return not (_TR.search(d) and not _TR_KEEP.search(d))


def filt(toc):
    return [entry for entry in toc if keep(entry[0])]
