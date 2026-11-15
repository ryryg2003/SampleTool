"""Run every tests/test_*.py with unittest and print a summary.

Usage: python tests/run_all.py [-v] [pattern]
  pattern defaults to "test_*.py". Exit code 0 only when everything passed.
Set SLO_E2E_LARGE=N to include the large-fixture performance test.
"""
from __future__ import annotations

import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    verbosity = 2 if "-v" in argv else 1
    argv = [a for a in argv if a != "-v"]
    pattern = argv[0] if argv else "test_*.py"
    for p in (REPO, HERE):
        if p not in sys.path:
            sys.path.insert(0, p)
    os.chdir(REPO)
    suite = unittest.defaultTestLoader.discover(HERE, pattern=pattern, top_level_dir=HERE)
    t0 = time.time()
    res = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    dt = time.time() - t0
    print()
    print("=" * 60)
    print(f"Python {sys.version.split()[0]} on {sys.platform}")
    print(f"Ran {res.testsRun} tests in {dt:.1f}s: "
          f"{res.testsRun - len(res.failures) - len(res.errors) - len(res.skipped)} passed, "
          f"{len(res.failures)} failed, {len(res.errors)} errors, {len(res.skipped)} skipped")
    for label, rows in (("FAILED", res.failures), ("ERROR", res.errors)):
        for test, _tb in rows:
            print(f"  {label}: {test.id()}")
    for test, why in res.skipped:
        print(f"  skipped: {test.id()} - {why}")
    print("RESULT:", "PASS" if res.wasSuccessful() else "FAIL")
    return 0 if res.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
