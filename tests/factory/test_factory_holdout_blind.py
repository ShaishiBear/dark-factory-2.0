"""The holdout is absent from the builder's worktree, and only from the builder's.

Protection from modification is not protection from reading. Build-side workers hold
Read/Glob/Grep over their checkout, so a holdout that sits in it is inside the optimisation loop
it exists to sit outside of. The kernel creates every build worktree as a sparse checkout with the
holdout programs absent from disk, verifies the blind took, and never blinds the validator.
"""
from __future__ import annotations

import ast
import glob
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel import worktree as worktree_module  # noqa: E402
from factory_kernel.worker_policy import BUILDER_BLIND_PATHS  # noqa: E402
from factory_kernel.worktree import WorktreeError, blind_paths, create_detached, remove  # noqa: E402

RUNTIME = ROOT / "factory_kernel" / "runtime.py"
HOLDOUT_FILES = (".factory/holdout/run.py", ".factory/holdout/citations.py")


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "core.autocrlf=false", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stdout}{proc.stderr}")
    return proc.stdout.strip()


def repo_shaped(tmp: Path) -> Path:
    """A repository with the same holdout layout as this one, plus something to build."""
    root = tmp / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "symbolic-ref", "HEAD", "refs/heads/main")
    # The kernel's own git calls carry no -c overrides; pin the repository so a developer
    # machine's global autocrlf cannot make a freshly created worktree look modified.
    git(root, "config", "core.autocrlf", "false")
    files = {
        ".factory/holdout/run.py": "SCENARIOS = 3\n",
        ".factory/holdout/citations.py": "PROBE = 1\n",
        ".factory/holdout/nested/probe.py": "DEEP = 1\n",
        ".factory/holdout/immunity.json": "{\"entries\": []}\n",
        ".factory/kernel.json": "{}\n",
        "app/backend/main.py": "safe = True\n",
        "tests/factory/test_x.py": "pass\n",
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root


class BlindWorktreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-blind-")
        self.root = repo_shaped(Path(self.tmp.name))
        self.head = git(self.root, "rev-parse", "HEAD")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_blinded_worktree_has_no_holdout_programs_but_keeps_everything_else(self):
        wt = create_detached(self.root, self.head, base_dir=Path(self.tmp.name) / "wt", blind=BUILDER_BLIND_PATHS)
        try:
            self.assertEqual(wt.blind, BUILDER_BLIND_PATHS)
            for rel in (".factory/holdout/run.py", ".factory/holdout/citations.py", ".factory/holdout/nested/probe.py"):
                self.assertFalse((wt.path / rel).exists(), rel)
            for rel in (".factory/holdout/immunity.json", ".factory/kernel.json", "app/backend/main.py", "tests/factory/test_x.py"):
                self.assertTrue((wt.path / rel).exists(), rel)
            self.assertEqual(git(wt.path, "status", "--porcelain"), "")
            flagged = [l[2:] for l in git(wt.path, "ls-files", "-t").splitlines() if l.startswith("S ")]
            self.assertEqual(sorted(flagged), [".factory/holdout/citations.py", ".factory/holdout/nested/probe.py", ".factory/holdout/run.py"])
        finally:
            remove(self.root, wt)

    def test_blind_does_not_change_what_gets_committed(self):
        """The PR head must be complete: the validator runs the holdout from it."""
        wt = create_detached(self.root, self.head, base_dir=Path(self.tmp.name) / "wt", blind=BUILDER_BLIND_PATHS)
        try:
            (wt.path / "app/backend/main.py").write_text("safe = False\n", encoding="utf-8")
            git(wt.path, "add", "-A", "--", "app/backend/main.py")
            git(wt.path, "commit", "-q", "-m", "change")
            tree = git(wt.path, "ls-tree", "-r", "--name-only", "HEAD").split()
            for rel in HOLDOUT_FILES:
                self.assertIn(rel, tree)
            self.assertEqual(git(wt.path, "show", "HEAD:.factory/holdout/run.py"), "SCENARIOS = 3")
            self.assertEqual(git(wt.path, "status", "--porcelain"), "")
        finally:
            remove(self.root, wt)
        self.assertTrue((self.root / ".factory/holdout/run.py").exists(), "the main checkout is never blinded")

    def test_unblinded_worktree_is_unchanged(self):
        wt = create_detached(self.root, self.head, base_dir=Path(self.tmp.name) / "wt")
        try:
            self.assertEqual(wt.blind, ())
            for rel in HOLDOUT_FILES:
                self.assertTrue((wt.path / rel).exists(), rel)
        finally:
            remove(self.root, wt)

    def _run_with(self, intercept):
        """Wrap the primitive's git runner so a git that ignores the blind can be simulated."""
        real = worktree_module._run

        def fake(repo, argv, *, check=True):
            replaced = intercept(argv)
            if replaced is not None:
                return replaced
            return real(repo, argv, check=check)

        return mock.patch.object(worktree_module, "_run", side_effect=fake)

    def test_a_git_that_ignores_the_blind_is_refused(self):
        """Index entries never marked skip-worktree: the worktree must not reach a worker."""
        wt = create_detached(self.root, self.head, base_dir=Path(self.tmp.name) / "wt")
        try:
            def intercept(argv):
                if argv[:2] == ["sparse-checkout", "set"]:
                    return subprocess.CompletedProcess(argv, 0, "", "")
                return None

            with self._run_with(intercept), self.assertRaises(WorktreeError) as ctx:
                blind_paths(wt.path, BUILDER_BLIND_PATHS)
            self.assertIn("blind did not take", str(ctx.exception))
            self.assertTrue((wt.path / ".factory/holdout/run.py").exists())
        finally:
            remove(self.root, wt)

    def test_a_blind_that_flags_the_index_but_leaves_files_on_disk_is_refused(self):
        wt = create_detached(self.root, self.head, base_dir=Path(self.tmp.name) / "wt")
        try:
            def intercept(argv):
                if argv[:2] == ["sparse-checkout", "set"]:
                    return subprocess.CompletedProcess(argv, 0, "", "")
                if argv[:2] == ["ls-files", "-t"]:
                    listed = subprocess.run(["git", "-C", str(wt.path), "ls-files", "--", *argv[3:]],
                                            capture_output=True, text=True).stdout.split()
                    return subprocess.CompletedProcess(argv, 0, "".join(f"S {p}\n" for p in listed), "")
                return None

            with self._run_with(intercept), self.assertRaises(WorktreeError) as ctx:
                blind_paths(wt.path, BUILDER_BLIND_PATHS)
            self.assertIn("still on disk", str(ctx.exception))
        finally:
            remove(self.root, wt)

    def test_empty_blind_is_a_no_op(self):
        wt = create_detached(self.root, self.head, base_dir=Path(self.tmp.name) / "wt", blind=())
        try:
            blind_paths(wt.path, ())
            self.assertTrue((wt.path / ".factory/holdout/run.py").exists())
        finally:
            remove(self.root, wt)


class PolicyTests(unittest.TestCase):
    @unittest.skipUnless((ROOT / ".factory/holdout/run.py").exists(),
                         "repo-shaped copy without the holdout (mutation runner)")
    def test_blind_covers_every_holdout_program_in_this_repository(self):
        matched = sorted(
            Path(p).relative_to(ROOT).as_posix()
            for pattern in BUILDER_BLIND_PATHS
            for p in glob.glob(str(ROOT / pattern), recursive=True)
        )
        for rel in HOLDOUT_FILES:
            self.assertIn(rel, matched)
        self.assertNotIn(".factory/holdout/immunity.json", matched)
        self.assertTrue(all(m.startswith(".factory/holdout/") for m in matched), matched)

    def test_blind_is_not_empty(self):
        self.assertTrue(BUILDER_BLIND_PATHS)


class KernelWiringTests(unittest.TestCase):
    """The build worktree is blinded and the validator worktree is not, read from the AST."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tree = ast.parse(RUNTIME.read_text(encoding="utf-8"))

    def calls(self, function_name: str) -> list[ast.Call]:
        func = next(
            n for n in ast.walk(self.tree)
            if isinstance(n, ast.FunctionDef) and n.name == function_name
        )
        return [
            n for n in ast.walk(func)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "create_detached"
        ]

    def test_build_worktree_is_created_blind(self):
        calls = self.calls("build_issue")
        self.assertEqual(len(calls), 1)
        kw = {k.arg: k.value for k in calls[0].keywords}
        self.assertIn("blind", kw)
        self.assertIsInstance(kw["blind"], ast.Name)
        self.assertEqual(kw["blind"].id, "BUILDER_BLIND_PATHS")

    def test_validator_worktree_is_never_blinded(self):
        calls = self.calls("validate_pr")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("blind", {k.arg for k in calls[0].keywords})


if __name__ == "__main__":
    unittest.main()
