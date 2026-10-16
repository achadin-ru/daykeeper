"""The build and CI files as the audit left them (M7, L27, L16, L17 of AUDIT_REPORT.md): tests
on every push, the dependency audit, the provenance step, one Python version, the macOS
bundle's version. Read as text — the workflows cannot run here, but a line that goes
missing should fail a test, not a release."""
from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


class CiFilesTest(unittest.TestCase):
    def test_tests_run_on_every_push_and_pull_request(self):
        """M7: lint, types, the audit and the suite on push and pull_request, Linux under Xvfb and Windows."""
        text = _read(".github/workflows/test.yml")
        self.assertRegex(text, r"on:\s*\n\s*push:\s*\n\s*pull_request:")
        for needle in ("python -m ruff check .", "python -m mypy", "pip_audit --require-hashes -r requirements-build.txt", "xvfb-run", "windows-latest", "ubuntu-latest", "unittest discover tests"):
            self.assertIn(needle, text, needle)
        self.assertNotIn("@v", re.sub(r"#.*", "", text), "actions are pinned to commit hashes, not tags")

    def test_release_publishes_provenance_and_the_mac_bundle_carries_its_version(self):
        """M7 (provenance), L27 (Info.plist version and minimum system)."""
        text = _read(".github/workflows/build.yml")
        self.assertIn("actions/attest-build-provenance@", text)
        self.assertIn("attestations: write", text)
        self.assertIn("id-token: write", text)
        for needle in ("CFBundleShortVersionString", "CFBundleVersion", "LSMinimumSystemVersion", "codesign --force --deep --sign -"):
            self.assertIn(needle, text, needle)
            self.assertIn(needle, _read("build.sh"), needle)
        self.assertIn("daykeeper.__version__", text)

    def test_one_python_version_everywhere(self):
        """M7: 3.13 in CI, in pyproject and in the local build script."""
        for wf in ("build.yml", "test.yml"):
            versions = set(re.findall(r'python-version:\s*"([\d.]+)"', _read(f".github/workflows/{wf}")))
            self.assertEqual(versions, {"3.13"}, wf)
        config = tomllib.loads(_read("pyproject.toml"))
        self.assertEqual(config["tool"]["mypy"]["python_version"], "3.13")
        self.assertEqual(config["tool"]["ruff"]["target-version"], "py313")
        self.assertIn("py -3.13 -m venv .venv", _read("build.bat"))
        self.assertIn("Python 3.13", _read("README.md"))

    def test_the_pure_modules_are_under_mypy(self):
        """L16: the modules the audit added to the checked set stay there."""
        files = tomllib.loads(_read("pyproject.toml"))["tool"]["mypy"]["files"]
        for name in ("undo", "timers", "revisions", "export", "markdown", "pngimage", "i18n", "autostart", "runner", "claudecode", "win32", "clipboard", "jobs", "layout", "tabs", "fieldicons"):
            self.assertIn(f"daykeeper/{name}.py", files, name)
        self.assertGreaterEqual(len(files), 31)

    def test_every_build_carries_the_icon(self):
        """The file icon of the exe and the bundle, and the assets folder for the window icon."""
        self.assertIn("--icon assets\\icon.ico", _read("build.bat"))
        self.assertIn('--add-data "assets;assets"', _read("build.bat"))
        self.assertIn("--icon assets/icon.icns", _read("build.sh"))
        self.assertIn('--add-data "assets:assets"', _read("build.sh"))
        workflow = _read(".github/workflows/build.yml")
        self.assertIn("--icon assets/icon.ico", workflow)
        self.assertIn("--icon assets/icon.icns", workflow)
        self.assertEqual(workflow.count("--add-data \"assets"), 2, "the Windows exe and the macOS bundle")

    def test_dev_tools_are_pinned(self):
        text = _read("requirements-dev.txt")
        self.assertRegex(text, r"(?m)^pip-audit==\d", msg="pip-audit pinned")
        self.assertRegex(text, r"(?m)^hypothesis==\d")


if __name__ == "__main__":
    unittest.main()
