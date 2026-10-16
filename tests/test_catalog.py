"""Every UI string written in code as _("…") has a Russian translation."""
import ast
import unittest
from pathlib import Path

from daykeeper.locale_ru import RU

PACKAGE = Path(__file__).resolve().parent.parent / "daykeeper"


def ui_keys() -> dict[str, str]:
    """Literal keys of _() calls → the file they are in."""
    keys: dict[str, str] = {}
    for path in sorted(PACKAGE.glob("*.py")):
        if path.name == "locale_ru.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                keys.setdefault(node.args[0].value, path.name)
    return keys


class CatalogTest(unittest.TestCase):
    def test_every_ui_string_is_translated(self):
        missing = {key: where for key, where in ui_keys().items() if key not in RU}
        self.assertEqual(missing, {}, "add these keys to locale_ru.py")

    def test_journal_layout_words_are_translated(self):
        """Labels and phrases of the ready-made layouts, the roles and the layout names."""
        from daykeeper import journal_layout as jl

        keys = list(jl.PRESETS.values()) + list(jl.PRESET_HINTS.values()) + list(jl.ROLE_LABELS.values()) + list(jl.ROLE_HINTS.values())
        for pid in jl.PRESETS:
            layout = jl.preset(pid)
            for item in layout.columns + layout.kinds:
                keys += [item.label] + ([item.phrase] if item.phrase else [])
                keys += [item.button] if getattr(item, "button", "") else []
        self.assertEqual(sorted({k for k in keys if k not in RU}), [])

    def test_tab_tips_are_translated(self):
        from daykeeper.app import TAB_TIPS

        self.assertEqual([t for t in TAB_TIPS.values() if t not in RU], [])

    def test_assistant_notes_are_translated(self):
        from daykeeper.claudecode import AGENTS

        self.assertEqual([a.trust_note for a in AGENTS if a.trust_note and a.trust_note not in RU], [])

    def test_hook_rule_labels_are_translated(self):
        """Keys kept in tables and passed to _() as variables: the AST walk above misses them."""
        from daykeeper import hookrules as h

        keys = [p.title for p in h.PRESETS] + [p.hint for p in h.PRESETS] + [p.example for p in h.PRESETS] + list(h.TAKE_LABELS.values()) + list(h.WHERE_LABELS.values())
        keys += [h.Rule(pattern="").problem(), h.Rule(pattern="(", regex=True).problem(), h.Rule(pattern="x", text="").problem()]
        keys += [h.Rule(pattern="^(a+)+$", regex=True).problem(), h.Rule(pattern="^(a|aa)+$", regex=True).problem(), h.Rule(pattern="\\d+\\d+", regex=True).problem(), h.Rule(pattern="x" * 501).problem()]
        self.assertEqual([k for k in keys if k not in RU], [])

    def test_no_parameter_hides_the_translator(self):
        """A callback written as `def f(*_)` makes `_` a tuple inside it; calling _() there
        crashes only when the callback fires."""
        bad = []
        for path in sorted(PACKAGE.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, (ast.FunctionDef, ast.Lambda)):
                    a = node.args
                    names = [x.arg for x in a.posonlyargs + a.args + a.kwonlyargs] + [x.arg for x in (a.vararg, a.kwarg) if x]
                    calls = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_" for n in ast.walk(node))
                    if "_" in names and calls:
                        bad.append(f"{path.name}:{node.lineno}")
        self.assertEqual(bad, [])

    def test_placeholders_match(self):
        import string

        def fields(text: str) -> set[str]:
            return {name for _lit, name, _spec, _conv in string.Formatter().parse(text) if name}

        bad = {k: v for k, v in RU.items() if fields(k) != fields(v)}
        self.assertEqual(bad, {})


if __name__ == "__main__":
    unittest.main()
