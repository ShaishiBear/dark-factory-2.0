from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "factory_impact.py"
spec = importlib.util.spec_from_file_location("factory_impact", SCRIPT)
assert spec and spec.loader
impact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(impact)


class ImpactTests(unittest.TestCase):
    def test_test_detection(self) -> None:
        self.assertTrue(impact.is_test("app/backend/tests/test_auth.py"))
        self.assertTrue(impact.is_test("app/frontend/src/__tests__/App.test.tsx"))
        self.assertFalse(impact.is_test("app/backend/auth/service.py"))

    def test_python_symbols_respect_changed_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            file = root / "sample.py"
            file.write_text("def alpha():\n    return 1\n\ndef beta():\n    return 2\n", encoding="utf-8")
            old_root = impact.ROOT
            try:
                impact.ROOT = root
                names = [x["name"] for x in impact.py_symbols(file, [(4, 5)])]
            finally:
                impact.ROOT = old_root
            self.assertEqual(names, ["beta"])

    def test_public_route_is_recognised(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            file = root / "route.py"
            file.write_text("@router.get('/x')\ndef _hidden():\n    return 1\n", encoding="utf-8")
            old_root = impact.ROOT
            try:
                impact.ROOT = root
                symbols = impact.py_symbols(file)
            finally:
                impact.ROOT = old_root
            self.assertTrue(symbols[0]["public"])

    def test_canonical_is_stable(self) -> None:
        self.assertEqual(impact.canonical({"b": 1, "a": 2}), '{"a":2,"b":1}')


if __name__ == "__main__":
    unittest.main()
