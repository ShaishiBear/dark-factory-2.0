"""The inner experiment loop: exact patches, frozen evaluator, in-place disposable stage (LINE_LEVEL 3-4, C08).

The LINE_LEVEL_CLAIMS section 8 example drives the end-to-end case: `now <= expiry` versus
`now < expiry` differ by one character; a frozen boundary contract at equality distinguishes
them; the correct operator is selected, applied and committed through the design-envelope
authority; the losing alternative stays in the record. Negative cases named by the
specification: candidate edits a frozen test, patch outside the envelope, patch does not apply,
too many candidates, malformed request, evaluator timeout excluded and reported, combined
winners are not implied, a tie keeps the baseline, and a resumed run never re-measures.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from factory_kernel import code_experiments as experiments
from factory_kernel.canonical import sha256_bytes
from factory_kernel.code_experiments import (
    BASELINE_ID, InvestigationRefused, WorktreeStage, compile_code_experiment, compare_candidates, execute_registered,
    freeze_candidate, lesson_proposal, parse_request, prepare_selected_patch, select_finite,
)
from factory_kernel.evaluation_protocol import Evaluator
from factory_kernel.runtime import KernelRuntime, NeedsHuman, RunPaths

ROOT = Path(__file__).resolve().parents[2]
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example"}

MODULE_WRONG = "def is_valid(now, expiry):\n    return now <= expiry\n"
MODULE_RIGHT = "def is_valid(now, expiry):\n    return now < expiry\n"
TEST = textwrap.dedent('''\
    import sys, unittest
    sys.path.insert(0, ".")
    from app.jobs import is_valid

    class Boundary(unittest.TestCase):
        def test_valid_strictly_before_expiry(self):
            self.assertTrue(is_valid(9, 10))
            self.assertFalse(is_valid(10, 10))
            self.assertFalse(is_valid(11, 10))

    if __name__ == "__main__":
        unittest.main()
''')


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, env={**os.environ, **GIT_ENV}, check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()


def make_repo(root: Path, module: str = MODULE_WRONG) -> Path:
    repo = root / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "app" / "jobs.py").write_text(module, encoding="utf-8")
    (repo / "tests" / "test_jobs.py").write_text(TEST, encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "implementer baseline")
    return repo


def diff(repo: Path, path: str, new_text: str) -> str:
    """A unified diff for `path` produced by git itself, so it applies exactly."""
    original = (repo / path).read_text(encoding="utf-8")
    (repo / path).write_text(new_text, encoding="utf-8")
    try:
        proc = subprocess.run(["git", "diff", "--", path], cwd=repo, capture_output=True, text=True, encoding="utf-8")
        return proc.stdout
    finally:
        (repo / path).write_text(original, encoding="utf-8")


def request(candidates):
    return {"schema": "dark-factory/investigation-request", "schema_version": "1.0", "acceptance_ids": ["AC-1"],
            "predicate_id": "contract-pass-v1", "hypothesis": "A job is valid strictly before its expiry.",
            "causal_mechanism": "At equality the boundary contract fails with <=.", "candidates": candidates}


def unittest_evaluator(repo: Path) -> Evaluator:
    argv = ("python", "-m", "unittest", "tests.test_jobs")
    return Evaluator("boundary-unittest-v1", "1.0", sha256_bytes(TEST.encode() + b"|".join(a.encode() for a in argv)),
                     "frozen boundary contract", argv)


class RequestTests(unittest.TestCase):
    def test_strict_bounded_request_parsing(self):
        good = json.dumps(request([{"id": "strict", "mechanism_family": "operator", "patch": "--- a\n+++ b\n", "predicted_effects": []}])).encode()
        parsed = parse_request(good)
        self.assertEqual(parsed["candidates"][0]["id"], "strict")
        cases = (
            b"{", b"[]", good.replace(b'"predicate_id": "contract-pass-v1"', b'"predicate_id": "feels-faster"'),
            json.dumps({**request([]), }).encode(),
            json.dumps(request([{"id": "baseline", "mechanism_family": "x", "patch": "p", "predicted_effects": []}])).encode(),
            json.dumps(request([{"id": "a", "mechanism_family": "x", "patch": "p", "predicted_effects": []}] * 2)).encode(),
            json.dumps(request([{"id": f"c{i}", "mechanism_family": "x", "patch": "p", "predicted_effects": []} for i in range(4)])).encode(),
            json.dumps(request([{"id": "c", "mechanism_family": "x", "patch": "p" * 70000, "predicted_effects": []}])).encode(),
            json.dumps(request([{"id": "c", "mechanism_family": "x", "patch": "p", "predicted_effects": [], "extra": 1}])).encode(),
            json.dumps({**request([{"id": "c", "mechanism_family": "x", "patch": "p", "predicted_effects": []}]), "schema_version": "2.0"}).encode(),
            b'{"schema": "dark-factory/investigation-request", "schema": "twice"}',
            good + b" " * 200_001,
        )
        for raw in cases:
            with self.subTest(raw=raw[:60]), self.assertRaises(InvestigationRefused):
                parse_request(raw)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.repo = make_repo(self.root)
        self.stage = WorktreeStage(self.repo)
        self.baseline_commit, self.baseline_tree = self.stage.head_commit(), self.stage.head_tree()
        self.evaluator = unittest_evaluator(self.repo)
        self.strict = diff(self.repo, "app/jobs.py", MODULE_RIGHT)
        self.plan = compile_code_experiment(
            {"exact_baseline_tree": self.baseline_tree, "decision_id": "issue-7-implement", "parent_claims": ["AC-1"]},
            ["app/jobs.py"], request([{"id": "strict", "mechanism_family": "operator", "patch": self.strict, "predicted_effects": ["equality fails"]}]),
            {"immutable_paths": ["tests/test_jobs.py"], "evaluator": self.evaluator,
             "cost_and_resource_limits": {"evaluator_timeout_seconds": 120}})

    def candidate(self, cid, patch, family="operator"):
        return {"id": cid, "mechanism_family": family, "patch": patch, "predicted_effects": []}

    def test_plan_is_frozen_before_search_and_refuses_protected_subjects(self):
        self.assertEqual(self.plan.protocol.adapter, "finite-contract-v1")
        self.assertEqual(self.plan.evaluator_closure_digest, self.evaluator.authority_closure_digest)
        self.assertEqual(self.plan.digest(), compile_code_experiment(
            {"exact_baseline_tree": self.baseline_tree, "decision_id": "issue-7-implement", "parent_claims": ["AC-1"]},
            ["app/jobs.py"], request([self.candidate("strict", self.strict)]),
            {"immutable_paths": ["tests/test_jobs.py"], "evaluator": self.evaluator,
             "cost_and_resource_limits": {"evaluator_timeout_seconds": 120}}).digest(), "same inputs, same frozen plan")
        self.assertNotEqual(self.plan.digest(), compile_code_experiment(
            {"exact_baseline_tree": self.baseline_tree, "decision_id": "issue-7-implement", "parent_claims": ["AC-1"]},
            ["app/jobs.py"], request([self.candidate("strict", self.strict)]),
            {"immutable_paths": ["tests/test_jobs.py"], "evaluator": self.evaluator}).digest(), "a changed limit is another study")
        with self.assertRaisesRegex(InvestigationRefused, "overlap"):
            compile_code_experiment({"exact_baseline_tree": self.baseline_tree, "decision_id": "d", "parent_claims": []},
                                    ["tests/test_jobs.py"], request([self.candidate("c", self.strict)]),
                                    {"immutable_paths": ["tests/test_jobs.py"], "evaluator": self.evaluator})
        with self.assertRaisesRegex(InvestigationRefused, "trusted evaluator"):
            compile_code_experiment({"exact_baseline_tree": self.baseline_tree, "decision_id": "d", "parent_claims": []},
                                    ["app/jobs.py"], request([self.candidate("c", self.strict)]), {"evaluator": "python tests"})

    def test_freeze_applies_only_to_the_exact_baseline_and_reverts(self):
        frozen = freeze_candidate(self.plan, self.candidate("strict", self.strict), self.stage, baseline_commit=self.baseline_commit)
        self.assertEqual(frozen.changed_subject_refs, ("app/jobs.py",))
        self.assertNotEqual(frozen.resulting_tree, self.baseline_tree)
        self.assertEqual(frozen.patch_object_digest, sha256_bytes(self.strict.encode()))
        self.assertEqual((self.stage.head_commit(), self.stage.head_tree(), self.stage.is_clean()), (self.baseline_commit, self.baseline_tree, True))
        self.assertEqual((self.repo / "app" / "jobs.py").read_text(encoding="utf-8"), MODULE_WRONG, "reverted after freezing")
        test_edit = diff(self.repo, "tests/test_jobs.py", TEST.replace("assertFalse(is_valid(10, 10))", "assertTrue(is_valid(10, 10))"))
        with self.assertRaisesRegex(InvestigationRefused, "frozen acceptance test"):
            freeze_candidate(self.plan, self.candidate("cheat", test_edit), self.stage, baseline_commit=self.baseline_commit)
        self.assertTrue(self.stage.is_clean(), "a refused candidate leaves the baseline clean")
        outside = "--- /dev/null\n+++ b/app/other.py\n@@ -0,0 +1 @@\n+x = 1\n"
        with self.assertRaisesRegex(InvestigationRefused, "outside the design envelope"):
            freeze_candidate(self.plan, self.candidate("stray", outside), self.stage, baseline_commit=self.baseline_commit)
        broken = self.strict.replace("return now <= expiry", "return now == expiry")
        with self.assertRaisesRegex(InvestigationRefused, "does not apply"):
            freeze_candidate(self.plan, self.candidate("stale", broken), self.stage, baseline_commit=self.baseline_commit)
        noop = diff(self.repo, "app/jobs.py", MODULE_WRONG)
        with self.assertRaisesRegex(InvestigationRefused, "changes nothing|could not be listed|does not apply"):
            freeze_candidate(self.plan, self.candidate("noop", noop or "--- a/app/jobs.py\n+++ b/app/jobs.py\n"), self.stage, baseline_commit=self.baseline_commit)
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "moved")
        with self.assertRaisesRegex(InvestigationRefused, "not the clean exact baseline"):
            freeze_candidate(self.plan, self.candidate("strict", self.strict), self.stage, baseline_commit=self.baseline_commit)

    def test_baseline_first_execution_selects_the_correct_operator_and_keeps_the_loser(self):
        lenient = diff(self.repo, "app/jobs.py", MODULE_WRONG.replace("<=", "!="))
        candidates = [freeze_candidate(self.plan, self.candidate(cid, patch), self.stage, baseline_commit=self.baseline_commit)
                      for cid, patch in (("strict", self.strict), ("unequal", lenient))]
        observations = execute_registered(self.plan, candidates, self.stage, baseline_commit=self.baseline_commit,
                                          environment={**os.environ, **GIT_ENV})
        self.assertEqual([o.candidate_id for o in observations], [BASELINE_ID, "strict", "unequal"], "baseline first, then id order")
        by_id = {o.candidate_id: o for o in observations}
        self.assertEqual((by_id[BASELINE_ID].hard_pass, by_id["strict"].hard_pass, by_id["unequal"].hard_pass), (False, True, False))
        self.assertTrue(all(o.complete for o in observations))
        self.assertEqual((self.stage.head_commit(), self.stage.is_clean()), (self.baseline_commit, True), "stage restored after every run")
        selection = compare_candidates(self.plan, observations, candidates)
        self.assertEqual((selection.outcome, selection.selected_candidate_id), ("provisional", "strict"))
        self.assertFalse(selection.to_dict()["merge_authorized"])
        self.assertEqual([r["id"] for r in selection.comparison.rejected_with_reasons], [BASELINE_ID, "unequal"], "losers stay in the record")
        proposed = prepare_selected_patch(selection, self.stage.head_tree(), self.plan, candidates)
        self.assertEqual((proposed.candidate_id, proposed.patch_bytes), ("strict", self.strict.encode()))
        moved = prepare_selected_patch(selection, "f" * 40, self.plan, candidates)
        self.assertEqual(moved.reason_codes, ("rebase_required",))
        lesson = lesson_proposal(self.plan, selection, candidates)
        self.assertEqual((lesson["status"], lesson["selected_candidate_id"]), ("proposed", "strict"))
        self.assertIn("global optimality", lesson["not_established"])

    def test_tie_keeps_baseline_and_timeout_is_incomplete_not_a_measurement(self):
        repo = make_repo(self.root / "right", MODULE_RIGHT)
        stage = WorktreeStage(repo)
        commit, tree = stage.head_commit(), stage.head_tree()
        cosmetic = diff(repo, "app/jobs.py", MODULE_RIGHT + "\n# same behaviour\n")
        plan = compile_code_experiment({"exact_baseline_tree": tree, "decision_id": "d", "parent_claims": ["AC-1"]}, ["app/jobs.py"],
                                       request([self.candidate("cosmetic", cosmetic)]),
                                       {"immutable_paths": ["tests/test_jobs.py"], "evaluator": unittest_evaluator(repo)})
        candidates = [freeze_candidate(plan, self.candidate("cosmetic", cosmetic), stage, baseline_commit=commit)]
        observations = execute_registered(plan, candidates, stage, baseline_commit=commit, environment={**os.environ, **GIT_ENV})
        selection = compare_candidates(plan, observations, candidates)
        self.assertEqual((selection.outcome, selection.selected_candidate_id), ("tie", BASELINE_ID))
        self.assertEqual(prepare_selected_patch(selection, tree, plan, candidates).reason_codes, ("nothing_to_apply",))
        slow = Mock(side_effect=subprocess.TimeoutExpired(["python"], 1))
        timed_out = execute_registered(plan, candidates, stage, baseline_commit=commit, runner=slow)
        self.assertTrue(all(not o.complete and o.incomplete_reasons == ("timeout",) for o in timed_out))
        self.assertEqual(compare_candidates(plan, timed_out, candidates).comparison.reason_codes, ("measurement_incomplete",))
        self.assertEqual((stage.head_commit(), stage.is_clean()), (commit, True))
        partial = compare_candidates(plan, observations[:1], candidates)
        self.assertEqual(partial.comparison.reason_codes, ("measurement_missing",))
        self.assertEqual(select_finite("A", [{"id": "A", "metric": 10, "hard_pass": True, "complete": True},
                                             {"id": "B", "metric": 8, "hard_pass": True, "complete": True}]).selected_candidate_id, "B")


class RuntimeHookTests(unittest.TestCase):
    """The kernel's own hook, on a real disposable repository, with the evaluator overridden to a
    trusted stand-in for the proof program (the proof program itself needs the full artifact set
    of a factory build; `_investigation_evaluator` is where that argv is frozen)."""

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.repo = make_repo(self.root)
        run = self.root / "runs" / "issue-7"
        self.paths = RunPaths(root=run, artifacts=run / "artifacts", transcripts=run / "transcripts")
        self.paths.artifacts.mkdir(parents=True)
        self.paths.transcripts.mkdir(parents=True)
        (self.paths.artifacts / "design.json").write_text(json.dumps({"planned_files": ["app/jobs.py"], "allowed_new_files": []}))
        (self.paths.artifacts / "red-proof.json").write_text(json.dumps({"version": "2.0", "checkpoints": [],
                                                                         "files": {"tests/test_jobs.py": sha256_bytes(TEST.encode())}}))
        (self.paths.artifacts / "task-contract.json").write_text(json.dumps({"issue": {"number": 7}}))
        self.rt = object.__new__(KernelRuntime)
        self.rt.repo_root = ROOT
        self.rt.check_stop = Mock()
        self.rt._investigation_evaluator = lambda paths: unittest_evaluator(self.repo)
        self.env = {**os.environ, **GIT_ENV}

    def write_request(self, candidates):
        (self.paths.artifacts / "investigation_request.json").write_text(json.dumps(request(candidates)), encoding="utf-8")

    def test_no_request_means_the_old_direct_path(self):
        self.assertIsNone(self.rt._host_investigation(self.repo, self.paths, self.env, stage="implement"))
        self.rt.check_stop.assert_not_called()
        self.assertFalse((self.paths.artifacts / "investigation-implement.json").exists())

    def test_winner_is_committed_through_the_design_envelope_and_the_record_keeps_every_alternative(self):
        strict = diff(self.repo, "app/jobs.py", MODULE_RIGHT)
        lenient = diff(self.repo, "app/jobs.py", MODULE_WRONG.replace("<=", "!="))
        self.write_request([{"id": "strict", "mechanism_family": "operator", "patch": strict, "predicted_effects": ["equality fails"]},
                            {"id": "unequal", "mechanism_family": "operator", "patch": lenient, "predicted_effects": []}])
        before = git(self.repo, "rev-parse", "HEAD")
        with patch("factory_kernel.runtime.scoped_environment", side_effect=lambda env, scope: dict(os.environ if env is None else env)):
            outcome = self.rt._host_investigation(self.repo, self.paths, self.env, stage="implement")
        self.assertEqual((outcome["status"], outcome["applied_candidate_id"], outcome["merge_authorized"]), ("applied", "strict", False))
        self.assertEqual((self.repo / "app" / "jobs.py").read_text(encoding="utf-8"), MODULE_RIGHT)
        self.assertNotEqual(git(self.repo, "rev-parse", "HEAD"), before)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "", "committed through the authority, nothing left dirty")
        self.assertIn("select measured alternative strict for issue #7", git(self.repo, "log", "-1", "--format=%s"))
        self.assertIn("Fixes #7", git(self.repo, "log", "-1", "--format=%b"))
        self.assertEqual(git(self.repo, "rev-list", "--count", "HEAD"), "2", "disposable candidate commits never survive")
        record = json.loads((self.paths.artifacts / "investigation-implement.json").read_text(encoding="utf-8"))
        self.assertEqual(record["selection"]["outcome"], "provisional")
        self.assertEqual({c["id"] for c in record["candidates"]}, {"strict", "unequal"})
        self.assertEqual(len(record["observations"]), 3)
        self.assertEqual(record["lesson_proposal"]["status"], "proposed")
        self.assertTrue(all(o["complete"] for o in record["observations"]))
        # Resumed with the same request: decided once, never re-measured.
        self.rt._investigation_evaluator = Mock(side_effect=AssertionError("must not re-run"))
        again = self.rt._host_investigation(self.repo, self.paths, self.env, stage="implement")
        self.assertEqual(again["status"], "applied")

    def test_refused_request_keeps_the_baseline_and_records_the_reason(self):
        cheat = diff(self.repo, "tests/test_jobs.py", TEST.replace("assertFalse(is_valid(10, 10))", "assertTrue(is_valid(10, 10))"))
        self.write_request([{"id": "cheat", "mechanism_family": "test-edit", "patch": cheat, "predicted_effects": []}])
        before = git(self.repo, "rev-parse", "HEAD")
        with patch("factory_kernel.runtime.scoped_environment", side_effect=lambda env, scope: dict(os.environ if env is None else env)):
            outcome = self.rt._host_investigation(self.repo, self.paths, self.env, stage="repair")
        self.assertEqual(outcome["status"], "refused")
        self.assertIn("frozen acceptance test", outcome["reason"])
        self.assertEqual((git(self.repo, "rev-parse", "HEAD"), git(self.repo, "status", "--porcelain")), (before, ""))
        self.assertEqual((self.repo / "tests" / "test_jobs.py").read_text(encoding="utf-8"), TEST)
        (self.paths.artifacts / "investigation_request.json").write_text("not json", encoding="utf-8")
        (self.paths.artifacts / "investigation-repair.json").unlink()
        with patch("factory_kernel.runtime.scoped_environment", side_effect=lambda env, scope: dict(os.environ if env is None else env)):
            malformed = self.rt._host_investigation(self.repo, self.paths, self.env, stage="repair")
        self.assertEqual(malformed["status"], "refused")
        self.assertIn("not valid JSON", malformed["reason"])

    def test_the_default_evaluator_freezes_the_kernels_proof_program(self):
        rt = object.__new__(KernelRuntime)
        rt.repo_root = ROOT
        with patch.object(KernelRuntime, "_kernel_checkout", ROOT):
            evaluator = KernelRuntime._investigation_evaluator(rt, self.paths)
        self.assertEqual(evaluator.id, "acceptance-green-v1")
        self.assertEqual(evaluator.argv[1], str((ROOT / "scripts" / "factory_proof.py").resolve()))
        self.assertEqual(evaluator.argv[2:4], ("green", "--proof"))
        self.assertTrue(evaluator.argv[-1].endswith("investigation-green.json"))
        again = KernelRuntime._investigation_evaluator.__wrapped__ if hasattr(KernelRuntime._investigation_evaluator, "__wrapped__") else None
        self.assertIsNone(again)
        (self.paths.artifacts / "red-proof.json").write_text("{\"version\": \"2.0\", \"checkpoints\": [], \"files\": {}}")
        with patch.object(KernelRuntime, "_kernel_checkout", ROOT):
            changed = KernelRuntime._investigation_evaluator(rt, self.paths)
        self.assertNotEqual(changed.authority_closure_digest, evaluator.authority_closure_digest, "a changed proof is a different evaluator")

    def test_build_and_repair_call_the_hook_after_the_worker_commit(self):
        source = (ROOT / "factory_kernel" / "runtime.py").read_text(encoding="utf-8")
        implement = source.index('self._agent(\n                "implement",')
        green = source.index('"--output", str(paths.artifacts / "green-proof.json")')
        hook = source.index('self._host_investigation(worktree.path, paths, env, stage="implement")')
        self.assertTrue(implement < hook < green, "the hook sits between the implementer and the GREEN gate")
        repair = source.index('"repair",\n            worktree.path,')
        repair_hook = source.index('self._host_investigation(worktree.path, paths, env, stage="repair")')
        self.assertTrue(repair < repair_hook < source.index('"green-after-repair.json"'))
        for name in ("implement.md", "repair.md"):
            self.assertIn("investigation_request.json", (ROOT / ".factory" / "prompts" / name).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
