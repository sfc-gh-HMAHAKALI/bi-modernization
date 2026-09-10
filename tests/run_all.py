#!/usr/bin/env python3
"""Run every test suite in this directory.

    python3 tests/run_all.py

Suites that need a generated app skip themselves unless BIM_APP_DIR points at
one, so a bare run is still meaningful.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITES = [
    "test_filters.py",
    "test_components.py",
    "test_inspector_contract.py",
    "test_pages.py",
]


def main() -> int:
    failed: list[str] = []
    for suite in SUITES:
        path = HERE / suite
        if not path.exists():
            print(f"MISSING  {suite}")
            failed.append(suite)
            continue
        proc = subprocess.run([sys.executable, str(path)],
                              capture_output=True, text=True)
        tail = [l for l in proc.stdout.strip().split("\n") if l.strip()]
        verdict = tail[-1] if tail else "(no output)"
        if proc.returncode == 0:
            print(f"PASS     {suite:<32} {verdict}")
        else:
            print(f"FAIL     {suite:<32} {verdict}")
            # Surface the failing assertions, not the whole log.
            for line in tail:
                if "FAIL" in line:
                    print(f"           {line.strip()}")
            failed.append(suite)

    print()
    if failed:
        print(f"{len(failed)} suite(s) failed: {', '.join(failed)}")
        return 1
    print(f"All {len(SUITES)} suites passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
