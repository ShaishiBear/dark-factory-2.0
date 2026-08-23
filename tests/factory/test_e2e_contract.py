from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "harness"
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

spec = importlib.util.spec_from_file_location("factory_e2e", HARNESS / "e2e.py")
assert spec and spec.loader
factory_e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(factory_e2e)


class E2EContractTests(unittest.TestCase):
    def test_ref_extracts_accessible_element(self) -> None:
        snap = '- textbox "Ask anything about the video library…" [ref=e12]'
        self.assertEqual(factory_e2e._ref(snap, "Ask anything"), "e12")

    def test_ref_fails_closed_when_element_missing(self) -> None:
        with self.assertRaises(factory_e2e.E2EFailure):
            factory_e2e._ref('- button "Other" [ref=e1]', "Send message")

    def test_citation_extracts_timestamp_and_ref(self) -> None:
        snap = '- button "12:34 — Locked Fixture" [ref=e42]'
        ref, label = factory_e2e._citation(snap)
        self.assertEqual(ref, "e42")
        self.assertEqual(factory_e2e._timestamp_seconds(label), 754)

    def test_youtube_urls_must_match_id_and_exact_time(self) -> None:
        external = "https://www.youtube.com/watch?v=pjF-0dliYhg&t=754s"
        embed = "https://www.youtube.com/embed/pjF-0dliYhg?start=754&autoplay=1"
        self.assertTrue(factory_e2e._youtube_matches(external, embed, "pjF-0dliYhg", 754))
        self.assertFalse(factory_e2e._youtube_matches(external, embed, "pjF-0dliYhg", 755))

    def test_scalar_preserves_json_encoded_multiline_metadata(self) -> None:
        raw = '"Video at 12:34\\nQuoted transcript evidence"'
        self.assertEqual(factory_e2e._scalar(raw), "Video at 12:34\nQuoted transcript evidence")

    def test_validator_delegates_to_one_base_branch_browser_authority(self) -> None:
        command = (
            ROOT / ".archon" / "commands" / "dark-factory-behavioral-e2e.md"
        ).read_text(encoding="utf-8")
        harness_source = (HARNESS / "e2e.py").read_text(encoding="utf-8")

        self.assertIn("git archive origin/main harness", command)
        self.assertIn('python "$HOLDOUT_ROOT/harness/e2e.py"', command)
        self.assertIn("--backend-port", harness_source)
        self.assertIn("--frontend-port", harness_source)
        self.assertIn("E2E_PASSED steps=", harness_source)

        for competing_procedure in (
            "agent-browser open",
            "agent-browser snapshot",
            "agent-browser screenshot",
        ):
            self.assertNotIn(competing_procedure, command)


if __name__ == "__main__":
    unittest.main()
