"""Spec and Standards are reviewed by separate processes and combined deterministically."""
from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel import review as r  # noqa: E402
from factory_kernel.worker_policy import ROLE_TOOLS  # noqa: E402

RUNTIME = ROOT / "factory_kernel" / "runtime.py"


def axis(name, verdict="pass", findings=()):
    return {"version": "1.0", "axis": name, "verdict": verdict, "findings": list(findings)}


def finding(severity, desc="x"):
    return {"severity": severity, "file": "a.py", "line": 1, "description": desc}


class AggregatorTruthTable(unittest.TestCase):
    def test_both_pass(self):
        out = r.aggregate({"spec": axis("spec"), "standards": axis("standards")})
        self.assertEqual(out.verdict, "pass")
        self.assertEqual(out.axes, ("spec", "standards"))

    def test_spec_fails(self):
        out = r.aggregate({"spec": axis("spec", "fail", [finding("high")]), "standards": axis("standards")})
        self.assertEqual(out.verdict, "fail")
        self.assertEqual([f["axis"] for f in out.findings], ["spec"])

    def test_standards_fails(self):
        out = r.aggregate({"spec": axis("spec"), "standards": axis("standards", "fail", [finding("critical")])})
        self.assertEqual(out.verdict, "fail")
        self.assertEqual([f["axis"] for f in out.findings], ["standards"])

    def test_both_fail_collects_both(self):
        out = r.aggregate({"spec": axis("spec", "fail", [finding("high")]),
                           "standards": axis("standards", "fail", [finding("high")])})
        self.assertEqual(out.verdict, "fail")
        self.assertEqual(len(out.findings), 2)

    def test_low_findings_do_not_block(self):
        out = r.aggregate({"spec": axis("spec", "pass", [finding("low")]), "standards": axis("standards", "pass", [finding("medium")])})
        self.assertEqual(out.verdict, "pass")
        self.assertEqual(len(out.findings), 2)


class AggregatorRefusals(unittest.TestCase):
    def test_missing_axis_refused(self):
        with self.assertRaisesRegex(r.ReviewInvalid, "missing"):
            r.aggregate({"spec": axis("spec")})

    def test_mislabelled_axis_refused(self):
        with self.assertRaisesRegex(r.ReviewInvalid, "labelled"):
            r.aggregate({"spec": axis("spec"), "standards": axis("spec")})

    def test_pass_with_blocking_finding_refused(self):
        with self.assertRaisesRegex(r.ReviewInvalid, "despite a blocking"):
            r.aggregate({"spec": axis("spec", "pass", [finding("critical")]), "standards": axis("standards")})

    def test_fail_without_blocking_finding_refused(self):
        with self.assertRaisesRegex(r.ReviewInvalid, "without a blocking"):
            r.aggregate({"spec": axis("spec", "fail", [finding("low")]), "standards": axis("standards")})

    def test_invalid_severity_and_missing_description_refused(self):
        with self.assertRaisesRegex(r.ReviewInvalid, "severity"):
            r.aggregate({"spec": axis("spec", "pass", [{"severity": "meh", "description": "x"}]), "standards": axis("standards")})
        with self.assertRaisesRegex(r.ReviewInvalid, "description"):
            r.aggregate({"spec": axis("spec", "pass", [{"severity": "low", "description": ""}]), "standards": axis("standards")})

    def test_non_object_and_bad_version_refused(self):
        with self.assertRaises(r.ReviewInvalid):
            r.aggregate({"spec": "nope", "standards": axis("standards")})
        with self.assertRaises(r.ReviewInvalid):
            r.aggregate({"spec": {**axis("spec"), "version": "0.9"}, "standards": axis("standards")})


class KernelWiring(unittest.TestCase):
    @unittest.skipUnless((ROOT / ".factory/prompts/review-spec.md").exists(),
                         "repo-shaped copy without prompts (mutation runner)")
    def test_roles_and_prompts_exist_and_the_single_review_role_is_gone(self):
        self.assertIn("review-spec", ROLE_TOOLS)
        self.assertIn("review-standards", ROLE_TOOLS)
        self.assertNotIn("review", ROLE_TOOLS)
        prompts = json.loads((ROOT / ".factory/kernel.json").read_text(encoding="utf-8"))["prompts"]
        self.assertEqual(prompts["review-spec"], ".factory/prompts/review-spec.md")
        self.assertEqual(prompts["review-standards"], ".factory/prompts/review-standards.md")
        self.assertNotIn("review", prompts)
        self.assertFalse((ROOT / ".factory/prompts/review.md").exists())
        spec_prompt = (ROOT / ".factory/prompts/review-spec.md").read_text(encoding="utf-8")
        std_prompt = (ROOT / ".factory/prompts/review-standards.md").read_text(encoding="utf-8")
        self.assertIn("review-spec.json", spec_prompt)
        self.assertIn("review-standards.json", std_prompt)
        self.assertNotIn("Standards", spec_prompt.split("Judge exactly one thing")[1].split(".")[0])

    def test_each_axis_receives_only_its_own_review_method(self):
        from factory_kernel.methods import method_block
        spec = method_block(ROOT, "review-spec")
        std = method_block(ROOT, "review-standards")
        self.assertIn("# Method: code review, Spec axis", spec)
        self.assertNotIn("# Method: code review, Standards axis", spec)
        self.assertIn("# Method: code review, Standards axis", std)
        self.assertNotIn("# Method: code review, Spec axis", std)
        # Standards also receives the design and complexity methods; Spec must not, so the
        # correctness judgement is not softened by taste.
        self.assertIn("# Method: minimal complexity", std)
        self.assertNotIn("# Method: minimal complexity", spec)

    def test_review_and_repair_runs_both_axes_then_repairs_then_both_again(self):
        """Drive the real _review_and_repair with a stand-in _agent that writes the axis artifacts."""
        from factory_kernel.runtime import KernelRuntime, NeedsHuman

        rt = KernelRuntime.__new__(KernelRuntime)
        calls: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"; artifacts.mkdir()
            paths = mock.Mock(artifacts=artifacts, transcripts=Path(tmp) / "t")
            worktree = mock.Mock(path=Path(tmp))
            state = {"round": 0}

            def fake_agent(role, cwd, p, *, context="", env):
                calls.append(role)
                if role in ("review-spec", "review-standards"):
                    ax = role.split("-")[1]
                    verdict, findings = ("fail", [finding("high")]) if (ax == "standards" and state["round"] == 0) else ("pass", [])
                    (artifacts / f"review-{ax}.json").write_text(json.dumps(axis(ax, verdict, findings)), encoding="utf-8")
                if role == "repair":
                    state["round"] = 1

            rt._agent = fake_agent
            rt._exec = lambda *a, **k: calls.append("exec:" + a[0][2]) or ""
            rt._read_json = lambda path: json.loads(Path(path).read_text(encoding="utf-8"))
            rt._write_json = lambda path, value: Path(path).write_text(json.dumps(value), encoding="utf-8")
            rt._review_and_repair(worktree, paths, {})
            self.assertEqual(calls, ["review-spec", "review-standards", "repair", "exec:green",
                                     "review-spec", "review-standards"])
            merged = json.loads((artifacts / "code-review.json").read_text(encoding="utf-8"))
            self.assertEqual(merged["verdict"], "pass")
            self.assertEqual(merged["axes"], ["spec", "standards"])

    def test_a_rejecting_standards_reviewer_that_persists_escalates(self):
        from factory_kernel.runtime import KernelRuntime, NeedsHuman

        rt = KernelRuntime.__new__(KernelRuntime)
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"; artifacts.mkdir()
            paths = mock.Mock(artifacts=artifacts)
            worktree = mock.Mock(path=Path(tmp))

            def fake_agent(role, cwd, p, *, context="", env):
                if role == "review-spec":
                    (artifacts / "review-spec.json").write_text(json.dumps(axis("spec")), encoding="utf-8")
                if role == "review-standards":
                    (artifacts / "review-standards.json").write_text(
                        json.dumps(axis("standards", "fail", [finding("critical")])), encoding="utf-8")

            rt._agent = fake_agent
            rt._exec = lambda *a, **k: ""
            rt._read_json = lambda path: json.loads(Path(path).read_text(encoding="utf-8"))
            rt._write_json = lambda path, value: Path(path).write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(NeedsHuman, "still contains blockers"):
                rt._review_and_repair(worktree, paths, {})

    def test_a_missing_axis_artifact_escalates_instead_of_passing(self):
        from factory_kernel.runtime import KernelRuntime, NeedsHuman

        rt = KernelRuntime.__new__(KernelRuntime)
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"; artifacts.mkdir()
            paths = mock.Mock(artifacts=artifacts)
            worktree = mock.Mock(path=Path(tmp))

            def fake_agent(role, cwd, p, *, context="", env):
                if role == "review-spec":
                    (artifacts / "review-spec.json").write_text(json.dumps(axis("spec")), encoding="utf-8")
                # standards reviewer writes nothing

            rt._agent = fake_agent
            rt._read_json = lambda path: json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else None
            rt._write_json = lambda path, value: None
            with self.assertRaises(NeedsHuman):
                rt._review_and_repair(worktree, paths, {})

    def test_runtime_source_invokes_both_axis_roles(self):
        tree = ast.parse(RUNTIME.read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_two_axis_review")
        loops = [n for n in ast.walk(fn) if isinstance(n, ast.For)]
        self.assertTrue(any(isinstance(l.iter, ast.Name) and l.iter.id == "AXES" for l in loops),
                        "the review must iterate over every axis, not a slice")
        source = RUNTIME.read_text(encoding="utf-8")
        self.assertNotIn('self._agent("review",', source)
        self.assertNotIn('"review",\n            worktree.path', source)


if __name__ == "__main__":
    unittest.main()
