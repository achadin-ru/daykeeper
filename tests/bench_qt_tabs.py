"""Speed of the Qt tabs on big data against the targets of the port plan (section 4):
10 000 password entries, 300 bookmark tiles, a busy day of notes. Not part of the unit
run; run by hand after a change to a tab:

    python -X utf8 tests/bench_qt_tabs.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests import qt_speed  # noqa: E402
from tests.qt_helpers import qt_app  # noqa: E402


def main() -> int:
    qt_app()
    worst = 0.0
    for measure in (qt_speed.measure_vault, qt_speed.measure_bookmarks, qt_speed.measure_notes):
        results = measure()
        for key, value in results.items():
            target = qt_speed.TARGETS[key]
            worst = max(worst, value / target)
            print(f"{key:14} {value * 1000:8.1f} ms   target {target * 1000:6.0f} ms   {'ok' if value <= target else 'SLOW'}")
    return 0 if worst <= 1 else 1


if __name__ == "__main__":
    sys.exit(main())
