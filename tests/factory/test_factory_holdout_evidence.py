"""The blinded code holdout is shown the RED half of the proof, not only the GREEN half.

Validation run 33955178802 (PR #96) was refused by the code holdout with a HIGH finding that
"RED-first proof is not established by the supplied evidence": the holdout's `proof_summary`
carried `test_commit`, `green_commit` and `green_results` and nothing else, so a judge that
takes "an absence of enough evidence is a blocking finding" seriously had to fail it. The
kernel had proved RED deterministically at the RED commit; it just never showed the judge.

These tests pin what the judge now sees (D-048): per-checkpoint RED outcomes with a failing
exit and the expected failure, the immutable acceptance files with their hashes, the RED
commit, base-versus-head test-definition counts for every test file the diff touches, and the
GREEN results it always had. They also pin that the builder pack is fetched and verified
*before* the holdout runs, since the RED evidence is cross-checked against the note-bound
pack, and that the holdout prompt names what the kernel has already proved so the judge
neither re-litigates it nor fails the PR for the absence of raw transcripts.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.runtime import KernelRuntime  # noqa: E402
from harness.rehearsal import (  # noqa: E402
    BASE, DEFAULT_RED_FILES, HEAD, TEST_COMMIT, Scenario, Trace, _pr_body, rehearse,
)

HOLDOUT_PROMPT = ROOT / ".factory" / "prompts" / "holdout.md"


def holdout_context(trace: Trace) -> dict:
    """The JSON the code holdout was actually handed, parsed from its verbatim prompt."""
    prompts = trace.agent_prompts.get("holdout") or []
    if not prompts:
        raise AssertionError(f"the code holdout never ran; trace={trace.names()}")
    _, marker, payload = prompts[0].partition("HOLDOUT INPUT:\n")
    if not marker:
        raise AssertionError("the holdout prompt carries no HOLDOUT INPUT block")
    return json.loads(payload)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class HoldoutSeesRedTests(unittest.TestCase):
    """What the judge is shown, on a rehearsed PR that adds one acceptance file."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.trace = rehearse(Scenario(
            "holdout-evidence",
            file_history={"tests/red_test.py": (None, "def test_ac1():\n    assert True\n")},
        ))
        cls.context = holdout_context(cls.trace)
        cls.summary = cls.context["proof_summary"]

    def test_the_rehearsal_merged_so_the_evidence_below_is_from_a_passing_run(self):
        self.assertEqual(self.trace.outcome, "returned", self.trace.error)
        self.assertTrue(self.trace.happened("merge_squash"))

    def test_red_results_carry_a_failing_exit_and_the_expected_failure_per_checkpoint(self):
        results = self.summary["red_results"]
        self.assertEqual([r["acceptance_id"] for r in results], ["AC-1"])
        for result in results:
            self.assertIsInstance(result["red_exit"], int)
            self.assertNotEqual(result["red_exit"], 0, "RED must be a failure, not a pass")
            self.assertTrue(result["expected_failure"].strip())
            self.assertTrue(result["matched"], "the expected failure must be visible")
            self.assertIn(result["expected_failure"], result["red_output_tail"])
            self.assertLessEqual(len(result["red_output_tail"]), KernelRuntime.HOLDOUT_RED_TAIL_CHARS)

    def test_red_commit_is_the_test_commit_and_the_acceptance_files_are_hashed(self):
        self.assertEqual(self.summary["red_commit"], TEST_COMMIT)
        self.assertEqual(self.summary["test_commit"], TEST_COMMIT)
        self.assertEqual(self.summary["red_files"], DEFAULT_RED_FILES)
        for digest in self.summary["red_files"].values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_green_results_are_still_supplied(self):
        self.assertEqual(self.summary["green_commit"], HEAD)
        self.assertEqual([g["exit"] for g in self.summary["green_results"]], [0])

    def test_preexisting_tests_count_definitions_at_base_and_head(self):
        self.assertEqual(self.summary["preexisting_tests"], [{
            "path": "tests/red_test.py", "base": 0, "head": 1,
            "present_at_base": False, "present_at_head": True,
        }])

    def test_the_holdout_context_has_gained_nothing_that_would_unblind_it(self):
        self.assertEqual(
            set(self.context),
            {"contract", "changed_files", "diff_sha256", "diff", "proof_summary"},
        )
        self.assertEqual(
            set(self.summary),
            {"test_commit", "green_commit", "green_results", "red_commit", "red_files",
             "red_results", "preexisting_tests"},
        )

    def test_the_pack_is_fetched_and_verified_before_the_holdout_runs(self):
        fetch = next(
            i for i, step in enumerate(self.trace.steps)
            if step.kind == "exec" and step.name == "factory_provenance.py" and "fetch" in step.argv
        )
        holdout = self.trace.index("holdout")
        self.assertLess(fetch, holdout, "the pack was fetched after the holdout")
        self.assertTrue(self.trace.before("verify_pack", "holdout"),
                        f"verify_pack ran after the holdout: {self.trace.names()}")


class PreexistingTestCountTests(unittest.TestCase):
    def test_a_shrinking_existing_test_file_is_counted_not_inferred(self):
        red = {"tests/red_test.py": sha("assert True\n")}
        before = "def test_a():\n    pass\n\ndef test_b():\n    pass\n\ndef test_c():\n    pass\n"
        after = "def test_a():\n    pass\n\ndef test_b():\n    pass\n"
        trace = rehearse(Scenario(
            "shrinking-tests", red_files=red,
            file_history={"tests/red_test.py": (before, after)},
        ))
        counts = holdout_context(trace)["proof_summary"]["preexisting_tests"]
        self.assertEqual(counts, [{
            "path": "tests/red_test.py", "base": 3, "head": 2,
            "present_at_base": True, "present_at_head": True,
        }])

    def test_only_test_shaped_paths_are_counted(self):
        trace = rehearse(Scenario("happy-counts"))
        counts = holdout_context(trace)["proof_summary"]["preexisting_tests"]
        self.assertEqual([c["path"] for c in counts], ["tests/red_test.py"],
                         "app/backend/main.py is not a test file and must not be counted")

    def test_javascript_and_python_definitions_are_both_counted(self):
        pattern = KernelRuntime._TEST_DEFINITION
        js = "describe('x', () => {\n  it('a', () => {});\n  test('b', () => {});\n  it.skip('c');\n});\n"
        py = "def test_one():\n    pass\nasync def test_two():\n    pass\ndef helper():\n    pass\n"
        self.assertEqual(len(pattern.findall(js)), 2)
        self.assertEqual(len(pattern.findall(py)), 2)

    def test_test_shaped_paths(self):
        pattern = KernelRuntime._TEST_PATH
        for path in ("app/frontend/src/lib/exportMarkdown.test.ts", "app/backend/tests/test_x.py",
                     "src/__tests__/a.tsx", "tests/factory/test_y.py", "pkg/a_test.py",
                     "src/a.spec.js"):
            self.assertTrue(pattern.search(path), path)
        for path in ("app/backend/main.py", "app/frontend/src/lib/exportMarkdown.ts",
                     "docs/testing.md", "app/backend/rag/tools.py"):
            self.assertFalse(pattern.search(path), path)


class RedExcerptTests(unittest.TestCase):
    def test_the_excerpt_is_placed_so_the_expected_failure_stays_visible(self):
        tail = "x" * 3000 + "AssertionError: the snippet was dropped" + "y" * 1500
        excerpt, matched = KernelRuntime._red_excerpt(tail, "AssertionError", 600)
        self.assertTrue(matched)
        self.assertIn("AssertionError: the snippet was dropped", excerpt)
        self.assertLessEqual(len(excerpt), 600)

    def test_an_expected_failure_outside_the_retained_tail_is_reported_not_faked(self):
        excerpt, matched = KernelRuntime._red_excerpt("z" * 900, "AssertionError", 600)
        self.assertFalse(matched)
        self.assertEqual(excerpt, "z" * 600)


class RefusalTests(unittest.TestCase):
    """A proof that cannot show RED is refused before any judge is asked to trust it."""

    def test_a_checkpoint_recorded_as_passing_in_red_refuses_before_the_holdout(self):
        body = _pr_body().replace('"red_exit": 1', '"red_exit": 0')
        trace = rehearse(Scenario("red-passed", body=body))
        self.assertEqual(trace.outcome, "NeedsHuman")
        self.assertIn("did not record a failing exit", trace.error)
        self.assertNotIn("holdout", trace.names("agent"))
        self.assertFalse(trace.happened("merge_squash"))

    def test_an_attached_proof_without_checkpoints_refuses_before_the_holdout(self):
        body = _pr_body()
        proof_line = next(line for line in body.splitlines() if '"checkpoints"' in line)
        proof = json.loads(proof_line)
        del proof["checkpoints"]
        trace = rehearse(Scenario("no-checkpoints", body=body.replace(proof_line, json.dumps(proof))))
        self.assertEqual(trace.outcome, "NeedsHuman")
        self.assertIn("no RED checkpoints", trace.error)
        self.assertNotIn("holdout", trace.names("agent"))

    def test_an_attached_proof_bound_to_a_different_red_commit_than_the_pack_refuses(self):
        body = _pr_body().replace(TEST_COMMIT, "9" * 40)
        trace = rehearse(Scenario("red-commit-mismatch", body=body))
        self.assertEqual(trace.outcome, "NeedsHuman")
        self.assertIn("disagree on the RED test commit", trace.error)
        self.assertNotIn("holdout", trace.names("agent"))

    def test_an_attached_file_map_that_differs_from_the_pack_refuses(self):
        forged = {"tests/red_test.py": "f" * 64}
        body = _pr_body(red_files=forged)
        trace = rehearse(Scenario("file-map-mismatch", body=body))  # pack keeps the default map
        self.assertEqual(trace.outcome, "NeedsHuman")
        self.assertIn("disagree on the immutable acceptance files", trace.error)


class HoldoutPromptTests(unittest.TestCase):
    """The prompt names the proved claims and the judged questions, and keeps its output shape."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = HOLDOUT_PROMPT.read_text(encoding="utf-8")

    def test_the_prompt_names_what_the_kernel_has_already_proved(self):
        self.assertIn("What the kernel has already proved deterministically", self.text)
        for claim in ("RED", "red_commit", "red_results", "expected_failure", "GREEN",
                      "green_commit", "green_results", "red_files", "Immutable acceptance files",
                      "Static checks", "preexisting_tests", "full harness"):
            self.assertIn(claim, self.text, claim)
        self.assertIn("Do not re-litigate", self.text)

    def test_the_prompt_names_what_the_judge_must_decide(self):
        self.assertIn("What you must judge", self.text)
        for question in ("contract behaviour", "collateral", "existing tests", "scope"):
            self.assertIn(question, self.text, question)

    def test_the_output_shape_is_unchanged(self):
        self.assertIn('{ "version":"1.0", "verdict":"pass|fail", "findings":[...] }', self.text)
        self.assertIn("Critical/high findings require `fail`", self.text)
        self.assertIn("An absence of enough evidence to establish a material claim is a blocking "
                      "finding", self.text)

    def test_the_prompt_still_blinds_the_judge(self):
        self.assertIn("not been given the builder's plan", self.text)
        self.assertIn("Judge only the task contract, public diff/evidence", self.text)


if __name__ == "__main__":
    unittest.main()
