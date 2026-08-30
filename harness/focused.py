#!/usr/bin/env python3
"""Run the factory's own detector suite and report a counted, unambiguous result.

`unittest discover` reports its total on stderr in prose. The genesis driver needs a single
machine-readable count from this stage's own output, and "zero tests discovered" must never look
like success -- an empty run exits 0 and reads as green, which is the failure this repository
guards against everywhere else.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests" / "factory"


def load(path: Path) -> unittest.TestSuite:
    """Load by file path rather than package import.

    `unittest discover` needs the start directory to be an importable package on some Python
    versions and not others; the detector suite must run identically everywhere it is measured.
    """
    spec = importlib.util.spec_from_file_location(f"factory_tests.{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return unittest.defaultTestLoader.loadTestsFromModule(module)


def main() -> int:
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts"))
    files = sorted(TESTS.glob("test_*.py"))
    if not files:
        print("FOCUSED_FAILED discovered no test files", flush=True)
        return 1
    suite = unittest.TestSuite(load(path) for path in files)
    result = unittest.TextTestRunner(verbosity=1, stream=sys.stderr).run(suite)
    if result.testsRun == 0:
        print("FOCUSED_FAILED discovered no tests", flush=True)
        return 1
    if not result.wasSuccessful():
        print(
            f"FOCUSED_FAILED tests={result.testsRun} "
            f"failures={len(result.failures)} errors={len(result.errors)}",
            flush=True,
        )
        return 1
    print(f"FOCUSED_OK tests={result.testsRun}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
