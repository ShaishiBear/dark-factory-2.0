#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import importlib.util
import io
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def load_ci():
    spec = importlib.util.spec_from_file_location("factory_harness_ci", ROOT / "harness" / "ci.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HarnessEvidenceContractTests(unittest.TestCase):
    def test_counted_static_evidence_survives_quick_transcript(self) -> None:
        ci = load_ci()
        ci.QUICK = True
        ci.CONFIG = {"driver": "http", "static": "fake-static", "unit": ""}
        output = io.StringIO()
        with mock.patch.object(ci, "run", return_value=(0, "  ok  ruff\nSTATIC_OK checks=5\n")):
            with contextlib.redirect_stdout(output):
                rc = ci.main()
        transcript = output.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("STATIC_OK checks=5", transcript)
        self.assertIn("GATE_OK mode=quick", transcript)


if __name__ == "__main__":
    unittest.main()
