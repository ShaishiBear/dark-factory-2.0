"""For bug issues, the kernel executes the proposed repro and refuses to continue unless it
fails for the named reason. A root cause proposed before the failure has been seen going red is
a guess; this stage makes the red loop a precondition of the contract."""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel import repro as r  # noqa: E402

RUNTIME = ROOT / "factory_kernel" / "runtime.py"
PROMPT = ROOT / ".factory" / "prompts" / "investigate.md"


def deferred(**over):
    base = {"version": "1.0",
            "reason": "No existing test asserts the fallback return value of formatCitation, so no runner fails today.",
            "seam": "app/frontend/src/lib/exportMarkdown.ts:formatCitation",
            "expected_symptom": "Test Video (timestamp link unavailable)"}
    base.update(over)
    return base


def good(**over):
    base = {"version": "1.0", "argv": ["pytest", "tests/test_x.py"], "cwd": ".",
            "expect_failure_containing": "boom happened"}
    base.update(over)
    return base


class ValidationTests(unittest.TestCase):
    def test_valid_repro_loads(self):
        rp = r.validate_repro(good())
        self.assertEqual(rp.argv[0], "pytest")
        self.assertEqual(rp.expect_failure_containing, "boom happened")

    def test_command_must_match_an_allowlisted_shape(self):
        for argv in (["bash", "x"], ["sh", "-c", "x"], ["/usr/bin/python", "-m", "pytest"],
                     ["python3", "-m", "pytest"], ["./run.sh"], ["curl", "http://x"]):
            with self.subTest(argv=argv), self.assertRaisesRegex(r.ReproRefused, "shape"):
                r.validate_repro(good(argv=argv))

    def test_symptom_and_argv_shape_are_required(self):
        with self.assertRaisesRegex(r.ReproRefused, "symptom"):
            r.validate_repro(good(expect_failure_containing="ab"))
        with self.assertRaisesRegex(r.ReproRefused, "argv"):
            r.validate_repro(good(argv=[]))
        with self.assertRaisesRegex(r.ReproRefused, "control"):
            r.validate_repro(good(argv=["pytest", "tests/x.py\nimport os"]))

    def test_cwd_must_stay_inside_the_checkout(self):
        for cwd in ("/etc", "../other", "C:\\x", "app/../../x"):
            with self.subTest(cwd=cwd), self.assertRaisesRegex(r.ReproRefused, "inside"):
                r.validate_repro(good(cwd=cwd))

    def test_missing_or_unreadable_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(r.ReproRefused, "no repro.json"):
                r.load_repro(Path(tmp) / "repro.json")
            (Path(tmp) / "repro.json").write_text("{not json", encoding="utf-8")
            with self.assertRaisesRegex(r.ReproRefused, "unreadable"):
                r.load_repro(Path(tmp) / "repro.json")


def fake_pytest_runner(exit_code: int, output: str):
    """Stand in for the test runner the shape names: the kernel only sees rc and output."""

    def run(argv, cwd, env, timeout):
        return subprocess.CompletedProcess(list(argv), exit_code, output, "")

    return run


class ExecutionTests(unittest.TestCase):
    def test_failing_repro_with_symptom_is_observed(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp)
            rp = r.validate_repro(good())
            obs = r.execute(rp, worktree=wt, runner=fake_pytest_runner(3, "FAILED boom happened"))
            self.assertEqual(obs.rc, 3)
            self.assertEqual(obs.matched_symptom, "boom happened")
            self.assertIn("boom happened", obs.output_tail)
            rec = r.observed_record(rp, obs)
            self.assertEqual(rec["version"], "1.0")
            self.assertEqual(rec["argv"], list(rp.argv))
            self.assertEqual(len(rec["output_sha256"]), 64)

    def test_passing_repro_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            rp = r.validate_repro(good())
            with self.assertRaisesRegex(r.ReproRefused, "does not go red"):
                r.execute(rp, worktree=Path(tmp), runner=fake_pytest_runner(0, "boom happened"))

    def test_failing_repro_without_the_symptom_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            rp = r.validate_repro(good())
            with self.assertRaisesRegex(r.ReproRefused, "named symptom"):
                r.execute(rp, worktree=Path(tmp), runner=fake_pytest_runner(1, "other"))

    def test_cwd_outside_worktree_is_refused_at_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            rp = r.Repro(argv=("pytest", "x"), expect_failure_containing="boom", cwd="missing-dir")
            with self.assertRaisesRegex(r.ReproRefused, "outside the worktree or missing"):
                r.execute(rp, worktree=Path(tmp), runner=fake_pytest_runner(1, "boom"))

    def test_child_env_holds_no_github_credentials(self):
        seen = {}

        def capture(argv, cwd, env, timeout):
            seen.update(env)
            return subprocess.CompletedProcess(argv, 1, "boom happened", "")

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["GH_TOKEN"] = "leak-me"
            os.environ["GITHUB_TOKEN"] = "leak-me-too"
            try:
                r.execute(r.validate_repro(good()), worktree=Path(tmp), runner=capture)
            finally:
                os.environ.pop("GH_TOKEN", None)
                os.environ.pop("GITHUB_TOKEN", None)
        self.assertNotIn("GH_TOKEN", seen)
        self.assertNotIn("GITHUB_TOKEN", seen)

    def test_timeout_is_refused(self):
        def slow(argv, cwd, env, timeout):
            raise subprocess.TimeoutExpired(argv, timeout)

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(r.ReproRefused, "timed out"):
                r.execute(r.validate_repro(good()), worktree=Path(tmp), runner=slow, timeout=1)

    def test_default_runner_never_uses_a_shell(self):
        src = (ROOT / "factory_kernel" / "repro.py").read_text(encoding="utf-8")
        self.assertIn("shell=False", src)


class KernelWiringTests(unittest.TestCase):
    def test_build_issue_executes_the_repro_for_bugs_before_the_contract(self):
        tree = ast.parse(RUNTIME.read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "build_issue")
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
        names = []
        for c in calls:
            if isinstance(c.func, ast.Attribute):
                names.append(c.func.attr)
        self.assertIn("_observe_repro", names)
        agent_roles = [c.args[0].value for c in calls if isinstance(c.func, ast.Attribute) and c.func.attr == "_agent"
                       and c.args and isinstance(c.args[0], ast.Constant)]
        self.assertIn("contract", agent_roles)
        src = RUNTIME.read_text(encoding="utf-8")
        self.assertLess(src.index("self._observe_repro("), src.index('self._agent("contract"'))
        # The observation must be reachable: guarded by the real bug/plan decision, not a
        # constant that a mutation could flip to dead code while the call remains in the source.
        guards = [n for n in ast.walk(fn) if isinstance(n, ast.If)
                  and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                          and c.func.attr == "_observe_repro" for c in ast.walk(n))]
        self.assertEqual(len(guards), 1)
        test = guards[0].test
        self.assertIsInstance(test, ast.Compare)
        self.assertIsInstance(test.left, ast.Name)
        self.assertEqual(test.left.id, "role")
        self.assertEqual([c.value for c in test.comparators if isinstance(c, ast.Constant)], ["investigate"])

    def test_observe_repro_refuses_on_any_repro_problem_and_records_on_success(self):
        from factory_kernel.runtime import KernelRuntime, NeedsHuman

        rt = KernelRuntime.__new__(KernelRuntime)
        rt._write_json = lambda path, value: Path(path).write_text(json.dumps(value), encoding="utf-8")
        rt._git = lambda *args, cwd=None: ""  # a clean, unchanging worktree
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "a"; artifacts.mkdir()
            wt = Path(tmp) / "wt"; wt.mkdir()
            with self.assertRaisesRegex(NeedsHuman, "repro"):
                rt._observe_repro(artifacts, wt, runner=lambda *a: None)
            (artifacts / "repro.json").write_text(json.dumps(good()), encoding="utf-8")
            with self.assertRaisesRegex(NeedsHuman, "does not go red"):
                rt._observe_repro(artifacts, wt, runner=fake_pytest_runner(0, "boom happened"))
            ctx = rt._observe_repro(artifacts, wt, runner=fake_pytest_runner(2, "boom happened"))
            self.assertIn("REPRO OBSERVED", ctx)
            rec = json.loads((artifacts / "repro-observed.json").read_text(encoding="utf-8"))
            self.assertEqual(rec["rc"], 2)

    def test_deferred_record_is_accepted_and_handed_to_the_contract(self):
        from factory_kernel.runtime import KernelRuntime

        rt = KernelRuntime.__new__(KernelRuntime)
        rt._write_json = lambda path, value: Path(path).write_text(json.dumps(value), encoding="utf-8")
        rt._git = lambda *args, cwd=None: ""
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "a"; artifacts.mkdir()
            wt = Path(tmp) / "wt"; wt.mkdir()
            (artifacts / "repro-deferred.json").write_text(json.dumps(deferred()), encoding="utf-8")
            ctx = rt._observe_repro(artifacts, wt, runner=lambda *a: self.fail("nothing must execute"))
            self.assertIn("REPRO DEFERRED TO RED", ctx)
            self.assertIn("Test Video (timestamp link unavailable)", ctx)
            rec = json.loads((artifacts / "repro-observed.json").read_text(encoding="utf-8"))
            self.assertEqual(rec["mode"], "deferred")
            self.assertIsNone(rec["observed_in_red"])

    def test_both_records_or_neither_are_refused(self):
        from factory_kernel.runtime import KernelRuntime, NeedsHuman

        rt = KernelRuntime.__new__(KernelRuntime)
        rt._write_json = lambda path, value: Path(path).write_text(json.dumps(value), encoding="utf-8")
        rt._git = lambda *args, cwd=None: ""
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "a"; artifacts.mkdir()
            wt = Path(tmp) / "wt"; wt.mkdir()
            with self.assertRaisesRegex(NeedsHuman, "wrote no repro"):
                rt._observe_repro(artifacts, wt, runner=lambda *a: None)
            (artifacts / "repro.json").write_text(json.dumps(good()), encoding="utf-8")
            (artifacts / "repro-deferred.json").write_text(json.dumps(deferred()), encoding="utf-8")
            with self.assertRaisesRegex(NeedsHuman, "both repro.json and repro-deferred.json"):
                rt._observe_repro(artifacts, wt, runner=fake_pytest_runner(2, "boom happened"))
            self.assertFalse((artifacts / "repro-observed.json").exists())

    def test_deferred_record_is_validated(self):
        for broken, message in (
            (deferred(reason="short"), "reason"),
            (deferred(seam=""), "seam"),
            (deferred(seam="/etc/passwd"), "seam"),
            (deferred(expected_symptom="abc"), "expected_symptom"),
            ({"version": "0.9"}, "version 1.0"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(r.ReproRefused, message):
                r.validate_deferred(broken)

    def test_deferred_loop_is_closed_against_red_output(self):
        record = r.deferred_record(r.validate_deferred(deferred()))
        proof = {"checkpoints": [
            {"acceptance_id": "AC-1", "red_exit": 1, "red_output_tail": "expected '- Test Video (timestamp link unavailable) — 0:10' ..."},
            {"acceptance_id": "AC-2", "red_exit": 1, "red_output_tail": "other failure"},
        ]}
        match = r.verify_deferred_in_red(record, proof)
        self.assertEqual(match, {"checkpoint": "AC-1", "matched": True,
                                 "symptom": "Test Video (timestamp link unavailable)"})
        with self.assertRaisesRegex(r.ReproRefused, "never observed in RED"):
            r.verify_deferred_in_red(record, {"checkpoints": [
                {"acceptance_id": "AC-1", "red_exit": 1, "red_output_tail": "something else entirely"}]})
        with self.assertRaisesRegex(r.ReproRefused, "no checkpoints"):
            r.verify_deferred_in_red(record, {"checkpoints": []})
        with self.assertRaisesRegex(r.ReproRefused, "only a deferred"):
            r.verify_deferred_in_red({"mode": "executed"}, proof)

    def test_kernel_closes_the_deferred_loop_after_red_and_records_the_match(self):
        from factory_kernel.runtime import KernelRuntime, NeedsHuman

        rt = KernelRuntime.__new__(KernelRuntime)
        rt._write_json = lambda path, value: Path(path).write_text(json.dumps(value), encoding="utf-8")
        rt._read_json = lambda path: json.loads(Path(path).read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            rt._close_deferred_repro(artifacts)  # not a bug issue: nothing to close
            record = r.deferred_record(r.validate_deferred(deferred()))
            (artifacts / "repro-observed.json").write_text(json.dumps(record), encoding="utf-8")
            (artifacts / "red-proof.json").write_text(json.dumps({"checkpoints": [
                {"acceptance_id": "AC-3", "red_exit": 1, "red_output_tail": "no symptom here"}]}), encoding="utf-8")
            with self.assertRaisesRegex(NeedsHuman, "never observed in RED"):
                rt._close_deferred_repro(artifacts)
            (artifacts / "red-proof.json").write_text(json.dumps({"checkpoints": [
                {"acceptance_id": "AC-3", "red_exit": 1, "red_output_tail": "got: Test Video (timestamp link unavailable) — 0:10"}]}), encoding="utf-8")
            rt._close_deferred_repro(artifacts)
            rec = json.loads((artifacts / "repro-observed.json").read_text(encoding="utf-8"))
            self.assertEqual(rec["observed_in_red"]["checkpoint"], "AC-3")
            self.assertTrue(rec["observed_in_red"]["matched"])
            executed = {"mode": "executed", "rc": 2}
            (artifacts / "repro-observed.json").write_text(json.dumps(executed), encoding="utf-8")
            (artifacts / "red-proof.json").write_text(json.dumps({"checkpoints": []}), encoding="utf-8")
            rt._close_deferred_repro(artifacts)  # executed mode was observed before the contract

    def test_build_issue_closes_the_deferred_loop_after_red_before_implement(self):
        src = RUNTIME.read_text(encoding="utf-8")
        red = src.index('"python", "scripts/factory_proof.py", "red"')
        close = src.index("self._close_deferred_repro(paths.artifacts)")
        implement = src.index('self._agent(\n                "implement"')
        self.assertLess(red, close)
        self.assertLess(close, implement)

    def test_red_proof_records_a_bounded_output_tail(self):
        source = (ROOT / "scripts" / "factory_proof.py").read_text(encoding="utf-8")
        self.assertIn("red_output_tail=out[-RED_TAIL_CHARS:]", source)
        self.assertIn("RED_TAIL_CHARS=2000", source)

    @unittest.skipUnless(PROMPT.exists(), "repo-shaped copy without prompts (mutation runner)")
    def test_prompt_names_both_shapes(self):
        text = PROMPT.read_text(encoding="utf-8")
        self.assertIn("repro-deferred.json", text)
        self.assertIn("expected_symptom", text)
        self.assertIn("never both, never neither", text)
        contract = (ROOT / ".factory" / "prompts" / "contract.md").read_text(encoding="utf-8")
        self.assertIn("REPRO DEFERRED TO RED", contract)

    @unittest.skipUnless(PROMPT.exists(), "repo-shaped copy without prompts (mutation runner)")
    def test_prompt_names_the_allowed_shapes(self):
        text = PROMPT.read_text(encoding="utf-8")
        self.assertIn("repro.json", text)
        self.assertIn("expect_failure_containing", text)
        for shape in r.ALLOWED_SHAPES[:3]:
            self.assertIn(" ".join(shape), text)
        self.assertNotIn("`argv[0]` must be one of", text, "the old program-name allowlist wording")
        self.assertIn("`python -c`, `npx`", text, "the prompt must say these are refused")


if __name__ == "__main__":
    unittest.main()
