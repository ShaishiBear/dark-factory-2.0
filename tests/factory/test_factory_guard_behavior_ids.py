"""A guard behaviour keeps its `AC-N` id; `kind` is a field and never changes the id scheme.

The first build after D-058 (run 34015187797, issue #49) read the new guard wording in
`contract.md` and numbered its three guards `AC-G1..AC-G3`. The compiler refused
`invalid/duplicate behavior id AC-G1`, correctly (ids are `AC-N`, sequential), but the message
named neither the rule nor the fact that the behaviour was otherwise sound, and the build ended
at needs-human. These tests pin D-060: a guard with a sequential id compiles; an invented scheme
is refused with the rule named (`behavior ids must be AC-1..AC-N in order; got 'AC-G1'`) and,
when the id is the behaviour's only fault, the refusal says so; the compiler never renumbers;
the run's own contract is refused the same way and compiles once its guards are `AC-5..AC-7`;
and both prompts state the rule.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/factory/fixtures/contracts/run-34015187797-issue-49-guard-ids.json"
CONTRACT_PROMPT = ROOT / ".factory/prompts/contract.md"
TEST_AUTHOR_PROMPT = ROOT / ".factory/prompts/test-author.md"
RULE = "behavior ids must be AC-1..AC-N in order"


def load_protocol():
    spec = importlib.util.spec_from_file_location(
        "factory_protocol_guard_ids", ROOT / "scripts" / "factory_protocol.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def behavior(ac: str, **over) -> dict:
    b = {"id": ac, "given": "g", "when": "w", "then": "t is kept", "seam": "app/x.py#f"}
    b.update(over)
    return b


def contract(behaviors) -> dict:
    return {
        "version": "2.0",
        "issue": {"number": 49, "title": "forty-nine"},
        "summary": "a summary long enough to pass",
        "behaviors": behaviors,
        "invariants": [],
        "out_of_scope": [],
        "risks": [],
        "ambiguities": [],
    }


class _ProtocolCase(unittest.TestCase):
    def setUp(self) -> None:
        self.m = load_protocol()

    def refused(self, c: dict) -> str:
        err = io.StringIO()
        with (
            contextlib.redirect_stderr(err),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as ctx,
        ):
            self.m.validate_contract(c, 49)
        self.assertEqual(ctx.exception.code, 1)
        return err.getvalue()


class GuardIdTests(_ProtocolCase):
    def test_a_guard_behaviour_with_a_sequential_id_compiles(self):
        c = contract([behavior("AC-1"), behavior("AC-2", kind="guard")])
        digest = self.m.validate_contract(c, 49)
        self.assertEqual(len(digest), 64)
        self.assertEqual([b["id"] for b in c["behaviors"]], ["AC-1", "AC-2"])
        self.assertEqual(c["behaviors"][1]["kind"], "guard")
        self.assertNotIn("kind", c["behaviors"][0], "no default is inserted")

    def test_a_guard_prefixed_id_is_refused_and_the_refusal_names_the_rule(self):
        err = self.refused(contract([behavior("AC-1"), behavior("AC-G1", kind="guard")]))
        self.assertIn(f"PROTOCOL_FAIL: {RULE}; got 'AC-G1'", err)
        self.assertNotIn("invalid/duplicate", err)

    def test_the_refusal_says_when_the_id_is_the_only_fault(self):
        err = self.refused(contract([behavior("AC-1"), behavior("AC-G1", kind="guard")]))
        self.assertIn("the behaviour is otherwise valid and the id is its only fault", err)
        self.assertIn('`kind: "guard"` is a field on a behaviour and never changes its id', err)
        self.assertIn("a guard is AC-N like any other", err)

    def test_an_ordinary_behaviour_with_a_bad_id_is_also_called_otherwise_valid(self):
        err = self.refused(contract([behavior("AC-1"), behavior("G-2")]))
        self.assertIn(f"{RULE}; got 'G-2'; the behaviour is otherwise valid", err)
        self.assertNotIn("kind", err, "no guard was declared, so no guard sentence")

    def test_a_bad_id_on_a_faulty_behaviour_is_not_called_otherwise_valid(self):
        for faulty in (
            behavior("AC-G1", kind="guard", then="   "),
            behavior("AC-G1", kind="invariant"),
        ):
            with self.subTest(faulty=faulty):
                err = self.refused(contract([behavior("AC-1"), faulty]))
                self.assertIn(f"{RULE}; got 'AC-G1'", err)
                self.assertNotIn("otherwise valid", err)

    def test_the_other_faults_are_still_refused_in_their_own_words(self):
        err = self.refused(contract([behavior("AC-1", then=" ")]))
        self.assertIn("PROTOCOL_FAIL: behavior AC-1 has an empty field", err)
        err = self.refused(contract([behavior("AC-1", kind="invariant")]))
        self.assertIn("PROTOCOL_FAIL: behavior AC-1 kind must be one of", err)

    def test_a_duplicate_id_is_refused_with_the_rule(self):
        err = self.refused(contract([behavior("AC-1"), behavior("AC-1", kind="guard")]))
        self.assertIn(f"PROTOCOL_FAIL: {RULE}; got 'AC-1' twice", err)

    def test_the_keyed_form_refuses_a_guard_prefixed_key_with_the_rule(self):
        keyed = {"AC-1": behavior("AC-1"), "AC-G1": behavior("AC-G1", kind="guard")}
        for key in keyed.values():
            del key["id"]
        err = self.refused(contract(keyed))
        self.assertIn(f"PROTOCOL_FAIL: {RULE}; got 'AC-G1' as a behaviors key", err)

    def test_the_compiler_does_not_renumber(self):
        c = contract([behavior("AC-1"), behavior("AC-G1", kind="guard")])
        before = json.dumps(c["behaviors"], sort_keys=True)
        self.refused(c)
        self.assertEqual(json.dumps(c["behaviors"], sort_keys=True), before)
        self.assertEqual(c["behaviors"][1]["id"], "AC-G1")


class RunContractTests(_ProtocolCase):
    """The contract run 34015187797 wrote: refused as it was, compiled once its ids conform."""

    def raw(self) -> dict:
        return json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_the_run_wrote_three_guards_under_an_invented_scheme(self):
        ids = [(b["id"], b.get("kind")) for b in self.raw()["behaviors"]]
        self.assertEqual(
            ids,
            [
                ("AC-1", None),
                ("AC-2", None),
                ("AC-3", None),
                ("AC-4", None),
                ("AC-G1", "guard"),
                ("AC-G2", "guard"),
                ("AC-G3", "guard"),
            ],
        )

    def test_the_run_contract_is_refused_with_the_rule_and_the_only_fault_named(self):
        err = self.refused(self.raw())
        self.assertIn(f"PROTOCOL_FAIL: {RULE}; got 'AC-G1'", err)
        self.assertIn("the behaviour is otherwise valid and the id is its only fault", err)

    def test_the_run_contract_compiles_once_the_guards_are_numbered_in_order(self):
        c = self.raw()
        for n, b in enumerate(c["behaviors"], start=1):
            b["id"] = f"AC-{n}"
        digest = self.m.validate_contract(c, 49)
        self.assertEqual(len(digest), 64)
        self.assertEqual([b.get("kind") for b in c["behaviors"]][4:], ["guard"] * 3)
        self.assertEqual([b["id"] for b in c["behaviors"]][4:], ["AC-5", "AC-6", "AC-7"])


class PromptTests(unittest.TestCase):
    def test_the_contract_worker_is_told_kind_never_changes_the_id(self):
        text = CONTRACT_PROMPT.read_text(encoding="utf-8")
        self.assertIn("`kind` is a field on a behaviour and never changes its id", text)
        self.assertIn("ids are `AC-1`..`AC-N` in order across both kinds", text)
        self.assertIn("never `AC-G1`", text)
        self.assertIn(f"(`{RULE}`)", text)
        self.assertIn("does not renumber", text)

    def test_the_contract_prompt_shows_a_red_and_a_guard_side_by_side(self):
        text = CONTRACT_PROMPT.read_text(encoding="utf-8")
        self.assertIn("One ordinary behavior and one guard, side by side:", text)
        self.assertIn('{"id": "AC-2", "given":', text)
        self.assertIn('{"id": "AC-3", "kind": "guard", "given":', text)
        example = text.split("side by side:", 1)[1].split("```json", 1)[1].split("```", 1)[0]
        rows = [json.loads(line.rstrip(",")) for line in example.strip().splitlines()]
        self.assertEqual([r["id"] for r in rows], ["AC-2", "AC-3"])
        self.assertEqual([r.get("kind") for r in rows], [None, "guard"])
        for row in rows:
            self.assertEqual(
                set(row) - {"kind"}, {"id", "given", "when", "then", "seam"}, "the schema's fields"
            )

    def test_the_test_author_is_told_a_guard_ac_keeps_its_id_and_gets_one_guard_checkpoint(self):
        text = TEST_AUTHOR_PROMPT.read_text(encoding="utf-8")
        self.assertIn(
            "A guard AC keeps its `AC-N` id (`kind` is a field on the behaviour and never "
            "changes its id; there is no `AC-G1`) and gets exactly one `guard` checkpoint "
            "whose `acceptance_id` is that same `AC-N`.",
            text,
        )


if __name__ == "__main__":
    unittest.main()
