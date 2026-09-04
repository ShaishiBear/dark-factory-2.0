"""Triage judges an issue on a body window wide enough to hold a real specification.

The window used to be an undocumented 2000-character slice. Triage rejects what looks
underspecified, so a long, well-specified issue whose acceptance criteria fell past the cut was
judged on its preamble. The window is a named constant, documented in FACTORY_RULES section 1,
and wide enough that the decision-critical part of a normal issue is never cut.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.triage import TRIAGE_BODY_CHARS, TriageEngine  # noqa: E402


def issue(body: str) -> dict:
    return {"number": 7, "title": "t", "body": body, "author": {"login": "someone"},
            "createdAt": "2026-09-04T00:00:00Z", "labels": []}


class TriageWindowTests(unittest.TestCase):
    def test_window_is_wide_enough_for_a_specified_issue(self):
        self.assertGreaterEqual(TRIAGE_BODY_CHARS, 8000)

    def test_a_six_thousand_character_body_reaches_the_worker_intact(self):
        body = ("## Acceptance criteria\n" + "Given x When y Then z\n" * 300)[:6000]
        self.assertEqual(len(body), 6000)
        self.assertEqual(TriageEngine._bounded_issue(issue(body))["body"], body)

    def test_the_window_is_the_constant_not_a_literal(self):
        body = "x" * (TRIAGE_BODY_CHARS + 500)
        self.assertEqual(len(TriageEngine._bounded_issue(issue(body))["body"]), TRIAGE_BODY_CHARS)
        source = (ROOT / "factory_kernel" / "triage.py").read_text(encoding="utf-8")
        self.assertNotIn("[:2000]", source)
        self.assertIn("[:TRIAGE_BODY_CHARS]", source)


if __name__ == "__main__":
    unittest.main()
