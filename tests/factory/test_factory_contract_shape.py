"""The contract compiler accepts `behaviors` keyed by AC id and normalises it to the list.

Canary run 33912650468 produced a complete, correct contract with behaviors spelled as
`{"AC-1": {...}}` and the compiler refused it as having no behaviors (D-027). The two
spellings carry identical content; the compiled form is always the list.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests" / "factory" / "fixtures" / "contracts" / "run-33912650468-issue-49-keyed.json"
PROMPT = ROOT / ".factory" / "prompts" / "contract.md"


def load_protocol():
    spec = importlib.util.spec_from_file_location("factory_protocol_under_test", ROOT / "scripts" / "factory_protocol.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def behavior(n: int) -> dict:
    return {"given": f"state {n}", "when": f"action {n}", "then": f"result {n}", "seam": f"app/x.py#f{n}"}


def contract(behaviors) -> dict:
    return {
        "version": "2.0", "issue": {"number": 7, "title": "seven"},
        "summary": "a summary long enough to pass",
        "behaviors": behaviors, "invariants": ["i"], "out_of_scope": ["o"], "risks": ["r"], "ambiguities": [],
    }


class KeyedBehaviorsTests(unittest.TestCase):
    def setUp(self):
        self.m = load_protocol()

    def refused(self, c):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.m.validate_contract(c, 7)

    def test_keyed_form_hashes_identically_to_the_list_form(self):
        listed = contract([{"id": "AC-1", **behavior(1)}, {"id": "AC-2", **behavior(2)}])
        keyed = contract({"AC-2": behavior(2), "AC-1": behavior(1)})
        self.assertEqual(self.m.validate_contract(keyed, 7), self.m.validate_contract(listed, 7))
        self.assertIsInstance(keyed["behaviors"], list)
        self.assertEqual([b["id"] for b in keyed["behaviors"]], ["AC-1", "AC-2"])

    def test_keyed_order_is_numeric_not_lexical(self):
        keyed = contract({"AC-10": behavior(10), "AC-2": behavior(2), "AC-1": behavior(1)})
        self.m.validate_contract(keyed, 7)
        self.assertEqual([b["id"] for b in keyed["behaviors"]], ["AC-1", "AC-2", "AC-10"])

    def test_list_form_is_unchanged(self):
        listed = contract([{"id": "AC-1", **behavior(1)}])
        before = json.dumps(listed["behaviors"], sort_keys=True)
        self.m.validate_contract(listed, 7)
        self.assertEqual(json.dumps(listed["behaviors"], sort_keys=True), before)

    def test_keyed_with_matching_inner_id_is_accepted(self):
        keyed = contract({"AC-1": {"id": "AC-1", **behavior(1)}})
        self.m.validate_contract(keyed, 7)
        self.assertEqual(keyed["behaviors"], [{"id": "AC-1", **behavior(1)}])

    def test_keyed_with_non_ac_key_is_refused(self):
        self.refused(contract({"AC-1": behavior(1), "extra": behavior(2)}))

    def test_keyed_with_conflicting_inner_id_is_refused(self):
        self.refused(contract({"AC-1": {"id": "AC-2", **behavior(1)}}))

    def test_keyed_with_non_object_value_is_refused(self):
        self.refused(contract({"AC-1": "not an object"}))

    def test_empty_keyed_form_is_still_no_behaviors(self):
        self.refused(contract({}))

    def test_the_refused_canary_contract_now_compiles(self):
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertIsInstance(raw["behaviors"], dict)
        digest = self.m.validate_contract(raw, 49)
        self.assertEqual(len(digest), 64)
        self.assertEqual([b["id"] for b in raw["behaviors"]], ["AC-1", "AC-2", "AC-3"])

    def test_compiled_output_is_the_list_form(self):
        """`run_contract` writes the canonical list, so downstream consumers never see a dict."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "raw.json"; out = Path(tmp) / "out.json"; h = Path(tmp) / "h.txt"
            src.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
            args = type("A", (), {"input": str(src), "output": str(out), "hash_output": str(h), "issue": 49})()
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                self.m.run_contract(args)
            compiled = json.loads(out.read_text(encoding="utf-8"))
            self.assertIsInstance(compiled["behaviors"], list)
            self.assertEqual(len(compiled["behaviors"]), 3)
            self.assertIn("criteria=3", captured.getvalue())
            self.assertEqual(h.read_text(encoding="utf-8").strip(), self.m.validate_contract(json.loads(FIXTURE.read_text(encoding="utf-8")), 49))


class PromptTests(unittest.TestCase):
    def test_prompt_shows_the_schema_and_says_behaviors_is_a_list(self):
        text = PROMPT.read_text(encoding="utf-8")
        self.assertIn("`behaviors` is a list; each item carries its own `id`.", text)
        self.assertIn('"behaviors": [', text)
        self.assertIn('{"id": "AC-1", "given":', text)
        self.assertNotIn("`behaviors` as `AC-N` objects", text)
        for kept in ("MUST state that symptom verbatim in its `then`", "`## Dependency justification`",
                     "Do not invent requirements", "the deterministic compiler will stop rather than guess"):
            self.assertIn(kept, text)


if __name__ == "__main__":
    unittest.main()
