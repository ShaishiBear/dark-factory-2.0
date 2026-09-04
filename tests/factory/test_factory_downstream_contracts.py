"""The stages after the architecture governor describe, and are handed, what their validators need.

A read-only audit before canary attempt 9 found the same class of defect D-028/D-029 fixed up to
the governor, one stage later each: information only the kernel holds (a deferred repro symptom,
the conformance compiler's policy-ID basis) never reached the worker that must echo it, and three
authorities classified test paths three ways. These tests pin the resolutions (D-030).
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[2]
for entry in (str(ROOT), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from factory_kernel.git_authority import _test_oriented as commit_test_shaped  # noqa: E402
from factory_kernel.repro import OBSERVED_ARTIFACT, RED_TAIL_CHARS  # noqa: E402
from factory_kernel.runtime import KernelRuntime  # noqa: E402
from factory_shapes import test_shaped  # noqa: E402

PROMPTS = ROOT / ".factory" / "prompts"
METHODS = ROOT / ".factory" / "methods"
RUNTIME_SOURCE = (ROOT / "factory_kernel" / "runtime.py").read_text(encoding="utf-8")


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bare_runtime() -> KernelRuntime:
    rt = KernelRuntime.__new__(KernelRuntime)
    rt.config = mock.Mock()
    rt.config.default_branch = "main"
    rt.repo_root = ROOT
    return rt


# ------------------------------------------------------------ 1. deferred symptom reaches the author
class DeferredSymptomBriefTests(unittest.TestCase):
    def _brief(self, record: dict | None) -> str:
        rt = bare_runtime()
        with tempfile.TemporaryDirectory() as tmp:
            art = Path(tmp)
            if record is not None:
                (art / OBSERVED_ARTIFACT).write_text(json.dumps(record), encoding="utf-8")
            return rt._deferred_symptom_brief(art)

    def test_deferred_record_puts_the_symptom_in_the_test_author_context(self):
        text = self._brief({"mode": "deferred", "expected_symptom": "(timestamp link unavailable) — 0:10–0:20"})
        self.assertIn("DEFERRED REPRO SYMPTOM", text)
        self.assertIn("(timestamp link unavailable) — 0:10–0:20", text)
        self.assertIn(str(RED_TAIL_CHARS), text, "the author is told the window the gate searches")

    def test_red_tail_window_is_one_number_in_both_authorities(self):
        proof = load_script("factory_proof")
        self.assertEqual(proof.RED_TAIL_CHARS, RED_TAIL_CHARS)

    def test_executed_record_adds_nothing(self):
        self.assertEqual(self._brief({"mode": "executed", "matched_symptom": "boom"}), "")

    def test_no_record_adds_nothing(self):
        self.assertEqual(self._brief(None), "")

    def test_test_author_call_site_appends_the_brief(self):
        self.assertIn(
            ") + self._deferred_symptom_brief(paths.artifacts),",
            RUNTIME_SOURCE,
        )

    def test_test_author_prompt_names_the_symptom_contract(self):
        text = (PROMPTS / "test-author.md").read_text(encoding="utf-8")
        self.assertIn("DEFERRED REPRO SYMPTOM", text)
        self.assertIn("2000", text)


# ------------------------------------------------------------ 2. conformance ids from changed files
class ConformancePolicyIdsTests(unittest.TestCase):
    def _runtime_with_git(self, changed: list[str]) -> KernelRuntime:
        rt = bare_runtime()

        def fake_git(*args, cwd=None):
            if "--name-only" in args:
                return "\n".join(changed) + "\n"
            if "--stat" in args:
                return " x | 1 +"
            return "diff --git a/x b/x\n+changed\n"

        rt._git = fake_git
        return rt

    def test_changed_files_come_from_the_merge_base_diff(self):
        rt = self._runtime_with_git(["app/frontend/src/lib/exportMarkdown.ts", "app/frontend/src/lib/exportMarkdown.test.ts"])
        self.assertEqual(
            rt._changed_files(Path("/wt"), {"FACTORY_BASE_REF": "origin/main"}),
            ["app/frontend/src/lib/exportMarkdown.test.ts", "app/frontend/src/lib/exportMarkdown.ts"],
        )

    def test_conformance_context_carries_ids_computed_from_changed_files_exactly_as_the_compiler(self):
        arch = load_script("factory_architecture")
        policy = json.loads((ROOT / ".factory" / "architecture.json").read_text(encoding="utf-8"))
        changed = ["app/frontend/src/lib/exportMarkdown.ts", "app/frontend/src/lib/exportMarkdown.test.ts"]
        expected = {
            "principles": arch.applicable(policy["principles"], changed, "scope"),
            "migrations": arch.applicable(policy["migrations"], changed, "paths", active_only=True),
            "debts": arch.applicable(policy["debt"], changed, "paths"),
        }
        rt = self._runtime_with_git(changed)
        text = rt._conformance_context(Path("/wt"), mock.Mock(artifacts=Path("/art")), {"FACTORY_BASE_REF": "origin/main"})
        self.assertIn("APPLICABLE ARCHITECTURE POLICY IDS FOR THE CHANGED FILES", text)
        self.assertIn(json.dumps(expected, sort_keys=True), text)
        self.assertIn("MERGE-BASE DIFF", text)

    def test_explicit_file_set_overrides_the_governor_basis(self):
        rt = bare_runtime()
        rt._read_json = lambda path: (
            {"principles": [{"id": "ARCH-A", "scope": ["app/backend"]}, {"id": "ARCH-B", "scope": ["app/frontend"]}],
             "migrations": [], "debt": []}
            if str(path).endswith("architecture.json") else {"files": ["app/backend/x.py"], "planned_files": ["app/backend/x.py"]}
        )
        governor_basis = rt._applicable_policy_ids(mock.Mock(artifacts=Path("/art")))
        changed_basis = rt._applicable_policy_ids(mock.Mock(artifacts=Path("/art")), files=["app/frontend/y.ts"])
        self.assertEqual(governor_basis["principles"], ["ARCH-A"])
        self.assertEqual(changed_basis["principles"], ["ARCH-B"])

    def test_both_conformance_call_sites_use_the_conformance_context(self):
        self.assertEqual(
            RUNTIME_SOURCE.count(
                '"conformance", worktree.path, paths,\n                context=self._conformance_context(worktree.path, paths, env), env=env,'
            ),
            2,
        )
        self.assertNotIn('"conformance", worktree.path, paths,\n                context=self._diff_context(', RUNTIME_SOURCE)

    def test_conformance_prompt_states_the_changed_files_basis(self):
        text = (PROMPTS / "conformance.md").read_text(encoding="utf-8")
        self.assertIn("changed files of the supplied diff", text)
        self.assertIn("APPLICABLE ARCHITECTURE POLICY IDS FOR THE CHANGED FILES", text)
        self.assertNotIn("`context.json`'s `files` or `design.json`'s `planned_files`", text)


# ------------------------------------------------------------ 3. test-spec.json accepts no object spelling
class TestSpecSpellingTests(unittest.TestCase):
    def test_prompt_says_test_spec_takes_plain_strings_only(self):
        text = (PROMPTS / "test-author.md").read_text(encoding="utf-8")
        self.assertIn("accepts no object spelling", text)

    def test_red_gate_still_refuses_object_entries(self):
        proof = load_script("factory_proof")
        with self.assertRaises(SystemExit):
            proof.checkpoint({"acceptance_id": "AC-1", "cwd": ".", "argv": [{"name": "pytest"}],
                              "files": ["tests/test_x.py"], "expected_failure": "boom"})


# ------------------------------------------------------------ 4. one test-path predicate
class SharedTestPredicateTests(unittest.TestCase):
    PATHS = {
        "app/backend/tests/test_export.py": True,
        "app/backend/routes/test_export.py": True,          # basename rule; guard used to say product
        "app/backend/routes/conftest.py": True,
        "app/frontend/src/lib/exportMarkdown.test.ts": True,
        "app/frontend/src/lib/exportMarkdown.spec.ts": True,
        "app/frontend/src/__tests__/Thing.tsx": True,
        "app/frontend/src/lib/exportMarkdown.ts": False,
        "app/backend/routes/export.py": False,
    }

    def test_commit_envelope_red_gate_and_architecture_guard_agree(self):
        proof = load_script("factory_proof")
        guard = load_script("factory_architecture_guard")
        for path, expected in self.PATHS.items():
            with self.subTest(path=path):
                self.assertEqual(test_shaped(path), expected)
                self.assertEqual(commit_test_shaped(path), expected)
                self.assertEqual(proof.test_oriented(path), expected)
                # the guard classifies product = under a product prefix and not a test
                self.assertEqual(guard.is_product(path), path.startswith(("app/backend/", "app/frontend/")) and not expected)

    def test_prompt_states_the_shared_rule(self):
        text = (PROMPTS / "test-author.md").read_text(encoding="utf-8")
        self.assertIn("post-code architecture guard", text)


# ------------------------------------------------------------ 5. review methods write a file
class ReviewMethodsWriteAFileTests(unittest.TestCase):
    def test_methods_say_write_to_the_artifact_path(self):
        for name in ("code-review-spec.md", "code-review-standards.md"):
            with self.subTest(name=name):
                text = (METHODS / name).read_text(encoding="utf-8")
                self.assertIn("Write exactly one JSON object to the artifact path your role prompt names", text)
                self.assertNotIn("Output exactly one JSON object", text)


# ------------------------------------------------------------ 6. validator-side hardening
class ValidatorSideBriefTests(unittest.TestCase):
    def test_architecture_holdout_is_handed_the_ids_for_changed_files(self):
        self.assertIn('context["applicable_policy_ids"] = self._applicable_policy_ids(paths, files=changed_files)', RUNTIME_SOURCE)
        self.assertIn("the policy IDs in applicable_policy_ids (copy them verbatim)", RUNTIME_SOURCE)

    def test_certifier_suffix_is_a_literal_skeleton_with_certifies_filled(self):
        rt = bare_runtime()
        rt.config.prompt_path = lambda role, root: ROOT / ".factory" / "prompts" / "holdout.md"
        rt.config.provider.model = "m"
        captured = {}

        class Provider:
            def run(self, request):
                captured["prompt"] = request.prompt
                return mock.Mock(structured_output={"version": "1.0", "certifies": "contract", "verdict": "pass", "findings": []})

        rt.provider = Provider()
        with tempfile.TemporaryDirectory() as tmp:
            rt._write_json = lambda path, value: None
            rt._run_precode_certifier(mock.Mock(artifacts=Path(tmp)), claim_id="contract", role="contract-certifier", inputs={})
        self.assertIn('"certifies": "contract"', captured["prompt"])
        self.assertIn('"verdict": "pass|fail"', captured["prompt"])
        self.assertIn('"severity": "critical|high|medium|low"', captured["prompt"])
        self.assertIn("Return ONLY this JSON object", captured["prompt"])


if __name__ == "__main__":
    unittest.main()
