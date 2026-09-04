"""A kernel authority is executed from the kernel's checkout, never from the subject's copy.

The kernel named its deterministic programs by repository-relative path and ran them with the
working directory set to the PR-head worktree. Python resolved `scripts/factory_provenance.py`
against that directory, so the PR head's copy was the program that judged the PR: the resume of
the factory's first PR (#74, worker run 33927770223) ran a copy that predated #75 and died on an
import main had already fixed. A PR that edited a validator would have been judged by its own
edit just as readily. `factory_kernel.trusted_programs.resolve_trusted_program` now rewrites the
program path to the kernel's checkout inside `_exec`, and every script derives the tree it
inspects from its working directory, so main's code operates on the PR's tree.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel import runtime as runtime_module  # noqa: E402
from factory_kernel.refusal import ToolRefused  # noqa: E402
from factory_kernel.runtime import KernelRuntime  # noqa: E402
from factory_kernel.trusted_programs import (  # noqa: E402
    AUTHORITY_PROGRAMS,
    TrustedProgramMissing,
    is_authority,
    resolve_trusted_program,
)

TRAP = "raise SystemExit(99)  # the subject's copy of an authority must never run\n"
SCRIPTS = sorted(ROOT.glob("scripts/factory_*.py"))
HARNESS_AUTHORITIES = [ROOT / "harness" / "merge_verify.py", ROOT / "harness" / "post_merge.py"]
# ROOT is the tree under test and must come from the working directory; a sibling program
# reached as `ROOT / "scripts" / "factory_x.py"` would be the subject's copy of that authority.
# (`ROOT / "scripts"` alone is a legitimate tree reference: impact scans the tree's sources.)
TREE_FROM_CWD = re.compile(r"^ROOT = Path\.cwd\(\)\.resolve\(\)$", re.M)
SIBLING_FROM_TREE = re.compile(r"""ROOT\s*/\s*['"]scripts['"]\s*/\s*['"]factory_""")


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "core.autocrlf=false", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stdout}{proc.stderr}")
    return proc.stdout.strip()


def bare_runtime(repo_root: Path = ROOT) -> KernelRuntime:
    rt = object.__new__(KernelRuntime)
    rt.repo_root = repo_root
    return rt


class ResolverTests(unittest.TestCase):
    def test_scripts_are_resolved_to_the_kernel_checkout(self):
        out = resolve_trusted_program(ROOT, ["python", "scripts/factory_provenance.py", "publish", "--pr", "74"])
        self.assertEqual(out[0], "python")
        self.assertEqual(Path(out[1]), (ROOT / "scripts" / "factory_provenance.py").resolve())
        self.assertTrue(Path(out[1]).is_absolute())
        self.assertEqual(out[2:], ["publish", "--pr", "74"])

    def test_the_two_harness_authorities_are_resolved_and_the_harness_under_test_is_not(self):
        for rel in sorted(AUTHORITY_PROGRAMS):
            with self.subTest(program=rel):
                out = resolve_trusted_program(ROOT, ["python", rel, "pre"])
                self.assertEqual(Path(out[1]), (ROOT / rel).resolve())
        untouched = ["python", "harness/ci.py", "--quick"]
        self.assertEqual(resolve_trusted_program(ROOT, untouched), untouched)
        self.assertFalse(is_authority("harness/ci.py"))

    def test_non_python_and_absolute_and_inline_programs_pass_through(self):
        for argv in (
            ["git", "status", "--porcelain"],
            ["uv", "sync", "--frozen"],
            ["python", "-c", "print(1)"],
            ["python", str((ROOT / "scripts" / "factory_security.py").resolve()), "--worktree"],
            ["python"],
        ):
            with self.subTest(argv=argv):
                self.assertEqual(resolve_trusted_program(ROOT, argv), argv)

    def test_a_program_missing_from_the_kernel_checkout_is_refused(self):
        with self.assertRaises(TrustedProgramMissing):
            resolve_trusted_program(ROOT, ["python", "scripts/factory_does_not_exist.py"])

    def test_windows_spelling_is_resolved_too(self):
        out = resolve_trusted_program(ROOT, ["python", "scripts\\factory_security.py", "--worktree"])
        self.assertEqual(Path(out[1]), (ROOT / "scripts" / "factory_security.py").resolve())


class ExecTests(unittest.TestCase):
    """The real `_exec`: program from the kernel checkout, working directory the worktree."""

    def test_exec_runs_the_kernel_copy_in_the_worktree(self):
        rt = bare_runtime()
        worktree = Path(tempfile.mkdtemp(prefix="dark-factory-subject-"))
        with mock.patch.object(runtime_module.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="ok", stderr="")
            rt._exec(["python", "scripts/factory_provenance.py", "publish", "--pr", "74"], cwd=worktree)
        argv = run.call_args.args[0]
        self.assertEqual(Path(argv[1]), (ROOT / "scripts" / "factory_provenance.py").resolve())
        self.assertEqual(Path(run.call_args.kwargs["cwd"]), worktree, "the tree under test is the worktree")

    def test_a_bare_runtime_without_a_repo_root_still_runs_the_kernel_copy(self):
        """Tests build KernelRuntime with object.__new__ and no repo_root; the module's own
        checkout is the kernel's copy, and plain commands never look the checkout up."""
        rt = object.__new__(KernelRuntime)
        self.assertFalse(hasattr(rt, "repo_root"))
        with mock.patch.object(runtime_module.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            rt._exec(["true"], cwd=Path("/tmp"))
            rt._exec(["python", "scripts/factory_security.py", "--worktree"], cwd=ROOT)
        plain, authority = (call.args[0] for call in run.call_args_list)
        self.assertEqual(plain, ["true"])
        self.assertEqual(Path(authority[1]), (ROOT / "scripts" / "factory_security.py").resolve())

    def test_the_checkout_is_looked_up_only_for_an_authority(self):
        calls = []

        def root():
            calls.append(1)
            return ROOT

        self.assertEqual(resolve_trusted_program(root, ["git", "status"]), ["git", "status"])
        self.assertEqual(resolve_trusted_program(root, ["python", "-c", "pass"]), ["python", "-c", "pass"])
        self.assertEqual(calls, [])
        resolve_trusted_program(root, ["python", "scripts/factory_security.py"])
        self.assertEqual(calls, [1])

    def test_exec_leaves_git_and_the_quick_gate_alone(self):
        rt = bare_runtime()
        with mock.patch.object(runtime_module.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            rt._exec(["git", "status", "--porcelain"], cwd=ROOT)
            rt._exec(["python", "harness/ci.py", "--quick"], cwd=ROOT)
        seen = [call.args[0] for call in run.call_args_list]
        self.assertEqual(seen, [["git", "status", "--porcelain"], ["python", "harness/ci.py", "--quick"]])

    def test_a_broken_subject_copy_is_never_the_program_that_runs(self):
        """Real subprocess. The worktree carries a trapped `factory_security.py` and a dirty
        protected file; the kernel's copy must run, and it must inspect the worktree."""
        with tempfile.TemporaryDirectory(prefix="dark-factory-subject-") as tmp:
            subject = Path(tmp) / "subject"
            subject.mkdir()
            git(subject, "init", "-q")
            git(subject, "symbolic-ref", "HEAD", "refs/heads/main")
            git(subject, "config", "core.autocrlf", "false")
            (subject / "scripts").mkdir()
            (subject / "scripts" / "factory_security.py").write_text(TRAP, encoding="utf-8")
            (subject / "FACTORY_RULES.md").write_text("rules\n", encoding="utf-8")
            git(subject, "add", "-A")
            git(subject, "commit", "-q", "-m", "subject")
            (subject / "FACTORY_RULES.md").write_text("tampered\n", encoding="utf-8")

            rt = bare_runtime()
            with self.assertRaises(ToolRefused) as ctx:
                rt._exec(["python", "scripts/factory_security.py", "--worktree"], cwd=subject)
            refused = ctx.exception
            self.assertEqual(refused.rc, 1, refused.tail)  # the guard's verdict, not the trap's 99
            verdict = json.loads(refused.tail.strip().splitlines()[-1])
            self.assertEqual(verdict["protected_paths"], ["FACTORY_RULES.md"],
                             "main's guard inspected the subject's tree")
            # And the trap is live: run the subject's copy directly and it exits 99.
            direct = subprocess.run([sys.executable, "scripts/factory_security.py", "--worktree"],
                                    cwd=subject, capture_output=True, text=True)
            self.assertEqual(direct.returncode, 99)


class ScriptTreeTests(unittest.TestCase):
    """Every script locates the tree under test from its working directory, not its own path."""

    def test_every_authority_derives_root_from_cwd(self):
        for script in SCRIPTS + HARNESS_AUTHORITIES:
            if script.name in {"factory_lease.py", "factory_shapes.py"}:
                continue  # no tree of their own: gh-only, and a pure helper
            with self.subTest(script=script.name):
                text = script.read_text(encoding="utf-8")
                self.assertRegex(text, TREE_FROM_CWD, f"{script.name} must take ROOT from the working directory")
                self.assertNotRegex(text, r"^ROOT = .*__file__", f"{script.name} takes ROOT from its own location")
                self.assertIsNone(SIBLING_FROM_TREE.search(text),
                                  f"{script.name} references a sibling authority through the tree under test")

    def test_evidence_loads_its_validators_from_beside_itself(self):
        """A trapped `factory_protocol.py` in the tree under test must not be what evidence runs."""
        spec = importlib.util.spec_from_file_location("evidence_under_test", ROOT / "scripts" / "factory_evidence.py")
        evidence = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(evidence)
        with tempfile.TemporaryDirectory(prefix="dark-factory-subject-") as tmp:
            subject = Path(tmp)
            (subject / "scripts").mkdir()
            for name in ("factory_protocol.py", "factory_security.py", "factory_architecture_guard.py"):
                (subject / "scripts" / name).write_text(TRAP, encoding="utf-8")
            evidence.ROOT = subject
            self.assertTrue(hasattr(evidence.load_protocol(), "validate_contract"))
            self.assertTrue(hasattr(evidence.load_security(), "evaluate"))
            self.assertTrue(hasattr(evidence.load_architecture_guard(), "layer_table"))

    def test_a_script_run_from_a_subject_tree_inspects_that_tree(self):
        """`factory_security.py --worktree` executed by absolute path from the kernel checkout,
        with the working directory set to another repository, reports that repository."""
        with tempfile.TemporaryDirectory(prefix="dark-factory-subject-") as tmp:
            subject = Path(tmp) / "subject"
            subject.mkdir()
            git(subject, "init", "-q")
            git(subject, "symbolic-ref", "HEAD", "refs/heads/main")
            git(subject, "config", "core.autocrlf", "false")
            (subject / "app" / "backend").mkdir(parents=True)
            (subject / "app" / "backend" / "chat.py").write_text("safe = True\n", encoding="utf-8")
            git(subject, "add", "-A")
            git(subject, "commit", "-q", "-m", "subject")
            (subject / "app" / "backend" / "chat.py").write_text("safe = False\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "factory_security.py"), "--worktree"],
                cwd=subject, capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            verdict = json.loads(proc.stdout.strip().splitlines()[-1])
            self.assertEqual(verdict["verdict"], "pass")
            self.assertEqual(verdict["protected_paths"], [])


class RehearsalTraceTests(unittest.TestCase):
    """What the control plane actually did: every authority absolute under the kernel checkout,
    every PR-tree operation with the worktree as its working directory."""

    @staticmethod
    def _load(name: str):
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @classmethod
    def setUpClass(cls) -> None:
        from harness.rehearsal import Scenario, rehearse

        stale_pr_scenario = cls._load("test_factory_refusals").stale_pr_scenario
        resume_scenario = cls._load("test_factory_resume").resume_scenario
        cls.traces = {
            "validate": rehearse(Scenario("happy")),
            "rehead": rehearse(stale_pr_scenario("rehead-happy")),
            "resume": rehearse(resume_scenario("resume-happy")),
        }

    def _authority_steps(self, trace):
        for step in trace.steps:
            if step.kind != "exec" or len(step.argv) < 2 or step.argv[0] != "python":
                continue
            name = Path(step.argv[1]).name
            if name.startswith("factory_") or name in {"merge_verify.py", "post_merge.py"}:
                yield step

    def test_every_authority_runs_from_the_kernel_checkout(self):
        for command, trace in self.traces.items():
            self.assertEqual(trace.outcome, "returned", f"{command}: {trace.error}")
            steps = list(self._authority_steps(trace))
            self.assertTrue(steps, f"{command} ran no authority")
            for step in steps:
                with self.subTest(command=command, tool=step.name):
                    program = Path(step.argv[1])
                    self.assertTrue(program.is_absolute(), step.argv)
                    self.assertIn(ROOT.resolve(), program.resolve().parents, step.argv)
                    self.assertTrue(program.is_file(), step.argv)

    def test_pr_tree_operations_run_in_the_worktree(self):
        # Programs that read or edit the PR's tree take it from their working directory. The
        # base-anchored guard and the provenance fetch run in the kernel checkout by design.
        kernel_side = {"factory_security.py", "factory_lease.py"}
        for command, trace in self.traces.items():
            worktrees = {step.cwd for step in trace.steps if step.kind == "exec" and step.cwd
                         and Path(step.cwd).name == "worktree"}
            self.assertEqual(len(worktrees), 1, f"{command}: {worktrees}")
            (worktree,) = worktrees
            for step in self._authority_steps(trace):
                name = Path(step.argv[1]).name
                phase = step.argv[2] if len(step.argv) > 2 else ""
                if name in kernel_side or (name == "factory_provenance.py" and phase == "fetch"):
                    continue
                with self.subTest(command=command, tool=step.name):
                    self.assertEqual(step.cwd, worktree, f"{step.name} must operate on the PR worktree")


if __name__ == "__main__":
    unittest.main()
