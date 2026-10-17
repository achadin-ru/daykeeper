"""The borders of the Qt package, checked by reading the source (no Qt needed): PySide6 is
imported only inside daykeeper/qt, the hook and the CLI never reach that package, and
every UI string of the Qt window has a Russian translation."""
import ast
import unittest
from pathlib import Path

from daykeeper.locale_ru import RU

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "daykeeper"
QT = PACKAGE / "qt"


def imported_modules(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level + (node.module or "")
            names.add(prefix)
            names.update(f"{prefix}.{a.name}" if node.module else prefix + a.name for a in node.names)
    return names


class QtBorderTest(unittest.TestCase):
    def test_pyside_only_inside_the_qt_package(self):
        outside = [p for p in PACKAGE.rglob("*.py") if QT not in p.parents]
        outside += [ROOT / "main.py", ROOT / "cli_main.py"]
        bad = [str(p.relative_to(ROOT)) for p in outside if any(m.split(".")[0] in ("PySide6", "shiboken6") for m in imported_modules(p))]
        self.assertEqual(bad, [], "PySide6 belongs in daykeeper/qt only (the hook starts in 0.2 s without it)")

    def test_nothing_outside_imports_the_qt_package(self):
        outside = [p for p in PACKAGE.rglob("*.py") if QT not in p.parents]
        outside += [ROOT / "main.py", ROOT / "cli_main.py"]
        bad = [str(p.relative_to(ROOT)) for p in outside if any(m == "daykeeper.qt" or m.startswith("daykeeper.qt.") or m in (".qt", "..qt") or m.startswith(".qt.") for m in imported_modules(p))]
        self.assertEqual(bad, [])

    def test_the_qt_package_does_not_load_tk(self):
        """The Qt window must not pull tkinter in through a module of the Tk window, even
        a few imports away (lock_view, appicon and dialogs load Tk)."""
        bad = []
        for path in QT.rglob("*.py"):
            chain = tk_chain(module_name(path))
            if chain:
                bad.append(" → ".join(chain))
        self.assertEqual(bad, [])


    def test_every_event_handler_is_guarded(self):
        """An error escaping an event handler, or a virtual Qt calls back synchronously, can
        end the process (daykeeper/qt/errors.py): every class of the package that defines
        one carries @guard_events."""
        events = {"event", "eventFilter", "paintEvent", "resizeEvent", "moveEvent", "showEvent", "hideEvent", "closeEvent", "changeEvent",
                  "mousePressEvent", "mouseReleaseEvent", "mouseMoveEvent", "mouseDoubleClickEvent", "wheelEvent", "enterEvent", "leaveEvent",
                  "keyPressEvent", "keyReleaseEvent", "focusInEvent", "focusOutEvent", "focusNextPrevChild", "contextMenuEvent"}
        # the virtuals Qt calls back while our code waits (a layout's size hints, a delegate's
        # paint, a model's data): checked on classes that derive from a Qt class
        sync = {"sizeHint", "minimumSizeHint", "minimumSize", "heightForWidth", "hasHeightForWidth", "paint", "drawPrimitive",
                "setGeometry", "data", "rowCount", "pixelMetric", "pixmap", "scaledPixmap"}
        bad = []
        for path in QT.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.ClassDef):
                    continue
                qt_base = any(isinstance(b, ast.Name) and b.id.startswith("Q") for b in node.bases)
                names = {b.name for b in node.body if isinstance(b, ast.FunctionDef)}
                handles = bool(names & events) or (qt_base and bool(names & sync))
                if handles and not any(isinstance(d, ast.Name) and d.id == "guard_events" for d in node.decorator_list):  # type: ignore[attr-defined]
                    bad.append(f"{path.name}:{node.name}")  # type: ignore[attr-defined]
        self.assertEqual(bad, [])


def module_name(path: Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def module_file(name: str) -> Path | None:
    base = ROOT.joinpath(*name.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def absolute_imports(path: Path) -> set[str]:
    """Every module `path` imports, relative imports made absolute; `from x import y`
    counts both x and x.y (y may be a module)."""
    package = module_name(path).split(".")
    if path.name != "__init__.py":
        package = package[:-1]
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            mod = ".".join(base + ([node.module] if node.module else []))
            out.add(mod)
            out.update(f"{mod}.{a.name}" for a in node.names)
    return out


def tk_chain(start: str) -> list[str]:
    """The import path from `start` to tkinter through daykeeper modules; [] when none."""
    seen = {start}
    queue = [[start]]
    while queue:
        chain = queue.pop(0)
        path = module_file(chain[-1])
        if path is None:
            continue
        for mod in sorted(absolute_imports(path)):
            if mod.split(".")[0] == "tkinter":
                return chain + [mod]
            if mod.startswith("daykeeper") and mod not in seen and module_file(mod) is not None:
                seen.add(mod)
                queue.append(chain + [mod])
    return []


def ui_keys(folder: Path) -> dict[str, str]:
    keys: dict[str, str] = {}
    for path in sorted(folder.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_" and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                keys.setdefault(node.args[0].value, path.name)
    return keys


class QtCatalogTest(unittest.TestCase):
    def test_every_ui_string_of_the_qt_window_is_translated(self):
        missing = {k: where for k, where in ui_keys(QT).items() if k not in RU}
        self.assertEqual(missing, {}, "add these keys to locale_ru.py")


if __name__ == "__main__":
    unittest.main()
