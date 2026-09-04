"""Every prompt describing a validated artifact shows the shape its validator accepts, and the
kernel supplies what a shell-less worker cannot compute (diffs, applicable policy IDs).

A read-only audit after canary attempt 6 (worker run 33912650468) found the pattern behind that
run's refusal repeated across the build path: a prompt described an artifact in prose, a worker
read the prose literally, and a deterministic gate refused a correct artifact for its spelling.
These tests pin the resolutions (D-028).
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel import worker_runtime as wr  # noqa: E402
from factory_kernel.agents import AgentResult  # noqa: E402
from factory_kernel.git_authority import _test_oriented as commit_authority_test_shaped  # noqa: E402
from factory_kernel.repro import EVAL_FLAGS, ALLOWED_SHAPES  # noqa: E402
from factory_kernel.runtime import KernelRuntime, NeedsHuman  # noqa: E402

PROMPTS = ROOT / ".factory" / "prompts"
METHODS = ROOT / ".factory" / "methods"
HAVE_PROMPTS = (PROMPTS / "investigate.md").exists()


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def load_proof():
    spec = importlib.util.spec_from_file_location("factory_proof_pc", ROOT / "scripts" / "factory_proof.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------- R1
class ContextIsNotRenderedTests(unittest.TestCase):
    """The issue body is untrusted text; a `$PATH` in it must reach the worker, not refuse the run."""

    def _run_agent(self, context: str) -> str:
        rt = wr.WorkerControlledRuntime.__new__(wr.WorkerControlledRuntime)
        seen: dict[str, str] = {}

        class Provider:
            def run(self, request):
                seen["prompt"] = request.prompt
                return AgentResult(provider_id="fake", model="m", content="{}")

        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"
            artifacts.mkdir()
            prompt_file = Path(tmp) / "role.md"
            prompt_file.write_text("Write $ARTIFACTS_DIR/out.json\n", encoding="utf-8")
            rt.config = mock.Mock()
            rt.config.prompt_path = lambda role, cwd: prompt_file
            rt.config.provider.model = "m"
            rt.provider = Provider()
            rt.check_stop = lambda: None
            rt._record_agent = lambda *a, **k: None
            rt._refuse_literal_artifacts_dir = lambda cwd: None
            rt._assert_clean = lambda cwd: None
            with mock.patch.object(wr, "method_block", return_value=""), \
                    mock.patch.object(wr, "may_change_repo", return_value=False):
                rt._agent("plan", Path(tmp), mock.Mock(transcripts=Path(tmp)), context=context, env={"ARTIFACTS_DIR": str(artifacts)})
            seen["artifacts"] = str(artifacts)
        return seen["prompt"].replace(seen["artifacts"], "<ART>")

    def test_untrusted_context_with_dollar_names_reaches_the_worker_verbatim(self):
        body = "Repro: run with $PATH unset and $GITHUB_TOKEN present; see ${HOME}/x"
        prompt = self._run_agent(body)
        self.assertIn(body, prompt)
        self.assertIn("Write <ART>/out.json", prompt, "the role prompt is still rendered")
        self.assertNotIn("$ARTIFACTS_DIR", prompt)

    def test_role_prompt_unknown_placeholder_is_still_refused(self):
        rt = wr.WorkerControlledRuntime.__new__(wr.WorkerControlledRuntime)
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"
            artifacts.mkdir()
            prompt_file = Path(tmp) / "role.md"
            prompt_file.write_text("Write $ARTIFACTS_DIR/out.json and $NOT_A_THING\n", encoding="utf-8")
            rt.config = mock.Mock()
            rt.config.prompt_path = lambda role, cwd: prompt_file
            rt.check_stop = lambda: None
            with mock.patch.object(wr, "method_block", return_value=""), \
                    self.assertRaisesRegex(Exception, r"\$NOT_A_THING"):
                rt._agent("plan", Path(tmp), mock.Mock(transcripts=Path(tmp)), context="", env={"ARTIFACTS_DIR": str(artifacts)})


# ---------------------------------------------------------------------------- R3, R11
@unittest.skipUnless(HAVE_PROMPTS, "repo-shaped copy without prompts (mutation runner)")
class InvestigatePromptAgreesWithReproValidatorTests(unittest.TestCase):
    def test_prompt_lists_no_flag_the_validator_refuses(self):
        text = read(".factory/prompts/investigate.md")
        flags_line = text.split("selection flags (", 1)[1].split(")", 1)[0].split(";", 1)[0]
        listed = {tok.strip("` ,;") for tok in flags_line.split("`") if tok.strip("` ,;").startswith("-")}
        self.assertTrue(listed, flags_line)
        self.assertFalse(listed & EVAL_FLAGS, f"prompt names refused flags: {listed & EVAL_FLAGS}")
        self.assertIn("not `-x`", text)

    def test_prompt_lists_every_allowed_shape(self):
        text = read(".factory/prompts/investigate.md")
        for shape in ALLOWED_SHAPES:
            self.assertIn(f"`{' '.join(shape)}`", text, shape)

    def test_prompt_states_the_red_tail_window(self):
        proof = load_proof()
        text = read(".factory/prompts/investigate.md")
        self.assertIn(f"last {proof.RED_TAIL_CHARS} characters", text)

    def test_method_text_names_only_runner_shapes_and_both_records(self):
        method = read(".factory/methods/diagnosing-bugs.md")
        self.assertNotIn("a script, or a targeted check", method)
        self.assertIn("only one of the repository's test-runner shapes", method)
        self.assertNotIn("the structured `repro` block", method)
        self.assertIn("`repro.json`", method)
        self.assertIn("`repro-deferred.json`", method)


# ---------------------------------------------------------------------------- R4-R10, R12-R14 prompt text
@unittest.skipUnless(HAVE_PROMPTS, "repo-shaped copy without prompts (mutation runner)")
class PromptSkeletonTests(unittest.TestCase):
    def test_architecture_prompt_shows_the_governor_schema(self):
        text = read(".factory/prompts/architecture.md")
        self.assertIn('"rationale": ["one point per entry"', text)
        self.assertIn("`rationale` is an array of strings", text)
        self.assertIn("exactly `[]` for `proceed`", text)
        self.assertIn("required and non-empty for `prefactor` or `decompose`", text)
        self.assertIn("prefix-overlaps any file in `context.json`'s `files` or `design.json`'s `planned_files`", text)
        self.assertIn("migrations only where `active` is true; debts from the policy's `debt` list", text)
        self.assertIn("Copy those sets verbatim", text)

    def test_conformance_prompt_shows_the_schema_and_findings_coupling(self):
        text = read(".factory/prompts/conformance.md")
        self.assertIn('"findings": []', text)
        self.assertIn("`findings` is an array of plain strings (not objects): exactly `[]` when `verdict` is `conform`, non-empty when `deviates`", text)
        self.assertIn("`rationale` is an array of strings", text)
        self.assertIn("prefix-overlaps any file in `context.json`'s `files` or `design.json`'s `planned_files`", text)
        self.assertIn("the diff supplied in the invocation context (merge-base to HEAD)", text)
        self.assertNotIn("the merge-base diff,", text)

    def test_context_prompt_states_array_values_and_uniqueness(self):
        text = read(".factory/prompts/context.md")
        self.assertIn("arrays of one or more names copied verbatim from `seams` (an array even when there is exactly one)", text)
        self.assertIn("No duplicate entries in any of these arrays", text)

    def test_contract_prompt_states_string_arrays(self):
        text = read(".factory/prompts/contract.md")
        self.assertIn("`invariants`, `out_of_scope`, `risks` and `ambiguities` are string arrays", text)

    def test_reviewers_are_told_the_diff_comes_from_the_kernel(self):
        for name in ("review-spec.md", "review-standards.md"):
            text = read(f".factory/prompts/{name}")
            self.assertIn("the diff supplied in the invocation context (merge-base to HEAD)", text, name)
            self.assertNotIn("Review the merge-base diff", text, name)

    def test_repair_prompt_has_an_explicit_nothing_to_change_path(self):
        text = read(".factory/prompts/repair.md")
        self.assertIn("You must leave at least one production edit in the checkout", text)
        self.assertIn("fail the attempt explicitly rather than finishing with an unchanged checkout", text)

    def test_test_author_prompt_states_the_naming_rule(self):
        text = read(".factory/prompts/test-author.md")
        self.assertIn("`*.test.*`, `*.spec.*`, `test_*` or `conftest.py`", text)


# ---------------------------------------------------------------------------- R14 both authorities agree
class TestShapedPredicatesAgreeTests(unittest.TestCase):
    PATHS = {
        "app/frontend/src/lib/exportMarkdown.test.ts": True,
        "app/frontend/src/foo.spec.ts": True,
        "app/frontend/src/__tests__/Thing.tsx": True,
        "app/backend/tests/test_x.py": True,
        "app/backend/tests/conftest.py": True,
        "tests/factory/test_y.py": True,
        "app/backend/routes/messages.py": False,
        "app/frontend/src/lib/api.ts": False,
        "docs/testing-notes.md": True,  # "test" in the name is accepted by both; not a regression
    }

    def test_commit_authority_and_red_gate_accept_the_same_paths(self):
        proof = load_proof()
        for path, expected in self.PATHS.items():
            with self.subTest(path=path):
                red = proof.test_oriented(path)
                commit = commit_authority_test_shaped(path)
                self.assertEqual(commit, red, f"authorities disagree on {path}: commit={commit} red={red}")
                self.assertEqual(red, expected)


# ---------------------------------------------------------------------------- R12 kernel supplies the diff
class KernelSuppliesDiffTests(unittest.TestCase):
    def _runtime(self, calls: list):
        rt = KernelRuntime.__new__(KernelRuntime)
        rt.config = mock.Mock()
        rt.config.default_branch = "main"

        def fake_git(*args, cwd=None):
            calls.append(args)
            if "--stat" in args:
                return " a.py | 2 +-"
            return "diff --git a/a.py b/a.py\n+changed\n"

        rt._git = fake_git
        return rt

    def test_diff_context_carries_stat_and_body_from_the_base_ref(self):
        calls: list = []
        rt = self._runtime(calls)
        text = rt._diff_context(Path("/wt"), {"FACTORY_BASE_REF": "origin/main"})
        self.assertIn("MERGE-BASE DIFF (origin/main...HEAD)", text)
        self.assertIn(" a.py | 2 +-", text)
        self.assertIn("+changed", text)
        self.assertEqual(calls[0][:3], ("diff", "--stat", "origin/main...HEAD"))

    def test_diff_context_is_truncated_with_a_notice(self):
        rt = self._runtime([])
        rt._git = lambda *a, cwd=None: ("x" * (KernelRuntime.DIFF_CONTEXT_CHARS + 500)) if "--stat" not in a else "stat"
        text = rt._diff_context(Path("/wt"), {})
        self.assertIn("[diff truncated after", text)
        self.assertLess(len(text), KernelRuntime.DIFF_CONTEXT_CHARS + 400)

    def test_two_axis_review_and_conformance_receive_the_diff(self):
        rt = KernelRuntime.__new__(KernelRuntime)
        received: dict[str, str] = {}

        def fake_agent(role, cwd, p, *, context="", env):
            received[role] = context
            if role.startswith("review-"):
                ax = role.split("-")[1]
                (p.artifacts / f"review-{ax}.json").write_text(json.dumps(
                    {"version": "1.0", "axis": ax, "verdict": "pass", "findings": []}), encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"
            artifacts.mkdir()
            paths = mock.Mock(artifacts=artifacts, transcripts=Path(tmp))
            rt._agent = fake_agent
            rt._diff_context = lambda worktree, env: "MERGE-BASE DIFF (origin/main...HEAD)\n+changed"
            rt._read_json = lambda path: json.loads(Path(path).read_text(encoding="utf-8"))
            rt._write_json = lambda path, value: Path(path).write_text(json.dumps(value), encoding="utf-8")
            rt._two_axis_review(mock.Mock(path=Path(tmp)), paths, {}, context="fresh")
        for role in ("review-spec", "review-standards"):
            self.assertIn("+changed", received[role], role)
            self.assertTrue(received[role].startswith("fresh"), role)

    def test_runtime_source_passes_diff_context_to_conformance(self):
        source = (ROOT / "factory_kernel" / "runtime.py").read_text(encoding="utf-8")
        self.assertEqual(source.count('"conformance", worktree.path, paths,\n                context=self._diff_context(worktree.path, env), env=env,'), 2,
                         "both the build and the re-head conformance invocations must carry the diff")


# ---------------------------------------------------------------------------- R6 kernel supplies applicable ids
class ApplicablePolicyIdsTests(unittest.TestCase):
    def test_kernel_computes_ids_exactly_as_the_compiler_does(self):
        """Mirror of scripts/factory_architecture.py applicable()/overlaps(): prefix overlap in
        either direction over context.files ∪ design.planned_files; active migrations only;
        debts from the policy's `debt` list."""
        rt = KernelRuntime.__new__(KernelRuntime)
        policy = {
            "principles": [{"id": "ARCH-A", "scope": ["app"]}, {"id": "ARCH-F", "scope": ["factory_kernel"]}],
            "migrations": [{"id": "MIG-ON", "paths": ["app/frontend"], "active": True},
                           {"id": "MIG-OFF", "paths": ["app/frontend"], "active": False}],
            "debt": [{"id": "DEBT-X", "paths": ["app/frontend/src/components/ChatArea.tsx"]},
                     {"id": "DEBT-Y", "paths": ["app/backend/db/repository.py"]}],
        }
        files = {"context": {"files": ["app/frontend/src/lib/exportMarkdown.ts"]},
                 "design": {"planned_files": ["app/frontend/src/components/ChatArea.tsx"]}}
        rt.repo_root = Path("/repo")
        rt._read_json = lambda path: policy if str(path).endswith("architecture.json") else files["context"] if str(path).endswith("context.json") else files["design"]
        ids = rt._applicable_policy_ids(mock.Mock(artifacts=Path("/art")))
        self.assertEqual(ids, {"principles": ["ARCH-A"], "migrations": ["MIG-ON"], "debts": ["DEBT-X"]})

    def test_architecture_brief_carries_the_sets(self):
        rt = KernelRuntime.__new__(KernelRuntime)
        with tempfile.TemporaryDirectory() as tmp:
            art = Path(tmp)
            (art / "task-contract.json").write_text("{}", encoding="utf-8")
            (art / "context.json").write_text(json.dumps({"files": ["a.py"]}), encoding="utf-8")
            (art / "design.json").write_text(json.dumps({"planned_files": ["a.py"], "seams": []}), encoding="utf-8")
            rt._read_json = lambda path: json.loads(Path(path).read_text(encoding="utf-8"))
            rt._applicable_policy_ids = lambda paths: {"principles": ["ARCH-A"], "migrations": [], "debts": []}
            brief = rt._worker_brief(mock.Mock(artifacts=art), contract_hash="h", issue_context="issue",
                                     include_design=True, include_applicable_policy=True)
        self.assertIn("APPLICABLE ARCHITECTURE POLICY IDS", brief)
        self.assertIn('"principles": ["ARCH-A"]', brief)

    def test_runtime_source_briefs_the_governor_with_policy_ids(self):
        source = (ROOT / "factory_kernel" / "runtime.py").read_text(encoding="utf-8")
        self.assertIn("include_design=True, include_applicable_policy=True,", source)


# ---------------------------------------------------------------------------- orphan check
class StageNoteRequiredTests(unittest.TestCase):
    def test_missing_or_empty_plan_escalates(self):
        rt = KernelRuntime.__new__(KernelRuntime)
        with tempfile.TemporaryDirectory() as tmp:
            art = Path(tmp)
            with self.assertRaisesRegex(NeedsHuman, "plan worker wrote no plan.md"):
                rt._require_stage_note(art, "plan")
            (art / "investigation.md").write_text("   \n", encoding="utf-8")
            with self.assertRaisesRegex(NeedsHuman, "investigate worker wrote no investigation.md"):
                rt._require_stage_note(art, "investigate")
            (art / "investigation.md").write_text("# facts\n", encoding="utf-8")
            rt._require_stage_note(art, "investigate")

    def test_build_issue_checks_the_note_before_contracting(self):
        source = (ROOT / "factory_kernel" / "runtime.py").read_text(encoding="utf-8")
        note = source.index("self._require_stage_note(paths.artifacts, role)")
        contract = source.index('self._agent("contract", worktree.path, paths, context=contract_context, env=env)')
        self.assertLess(note, contract)


if __name__ == "__main__":
    unittest.main()
