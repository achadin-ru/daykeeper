"""Fail when the Qt build grew past its ceiling (the port plan 5.1: 75 MB for the folder;
the probe measured 59). python packaging/check_size.py <folder> [MB]"""

from __future__ import annotations

import sys
from pathlib import Path


def size_mb(folder: Path) -> float:
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file()) / 1024 / 1024


def main(argv: list[str]) -> int:
    folder = Path(argv[1])
    limit = float(argv[2]) if len(argv) > 2 else 75.0
    size = size_mb(folder)
    print(f"{folder}: {size:.1f} MB (ceiling {limit:.0f} MB)")
    return 0 if size <= limit else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
