"""Kernel commits are attributed to the GitHub Actions bot.

A commit whose author email maps to no GitHub account resolves to null on GitHub: attributable
to nobody, and a candidate for the ruleset's extra-approval rule that the autonomous path can
never satisfy. The kernel commits as `github-actions[bot]` with the address GitHub maps to that
Bot account, from one constant used by every commit site.
"""
from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.git_authority import _commit  # noqa: E402
from factory_kernel.worker_policy import KERNEL_COMMIT_ARGS, KERNEL_COMMIT_EMAIL, KERNEL_COMMIT_NAME  # noqa: E402

BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stdout}{proc.stderr}")
    return proc.stdout.strip()


class CommitIdentityTests(unittest.TestCase):
    def test_identity_is_the_github_mapped_actions_bot(self):
        self.assertEqual(KERNEL_COMMIT_EMAIL, BOT_EMAIL)
        self.assertEqual(KERNEL_COMMIT_NAME, "github-actions[bot]")
        self.assertEqual(KERNEL_COMMIT_ARGS, ("-c", f"user.name={KERNEL_COMMIT_NAME}", "-c", f"user.email={KERNEL_COMMIT_EMAIL}"))

    def test_a_kernel_commit_carries_the_bot_identity(self):
        with tempfile.TemporaryDirectory(prefix="dark-factory-identity-") as tmp:
            root = Path(tmp)
            git(root, "init", "-q")
            git(root, "config", "user.name", "Someone Else")
            git(root, "config", "user.email", "someone@example.invalid")
            git(root, "config", "core.autocrlf", "false")
            (root / "x.txt").write_text("one\n", encoding="utf-8")
            git(root, "add", "x.txt")
            git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "base")
            (root / "x.txt").write_text("two\n", encoding="utf-8")
            sha = _commit(root, ["x.txt"], "fix(factory): change")
            self.assertEqual(git(root, "log", "-1", "--format=%an <%ae> | %cn <%ce>", sha),
                             f"github-actions[bot] <{BOT_EMAIL}> | github-actions[bot] <{BOT_EMAIL}>")

    def test_every_commit_site_uses_the_shared_identity(self):
        for rel in ("factory_kernel/git_authority.py", "factory_kernel/worker_runtime.py"):
            source = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("user.email=", source, f"{rel} must not spell out an identity")
            self.assertNotIn("dark-factory@users.noreply", source)
            self.assertIn("KERNEL_COMMIT_ARGS", source)
        tree = ast.parse((ROOT / "factory_kernel/git_authority.py").read_text(encoding="utf-8"))
        starred = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Starred) and isinstance(n.value, ast.Name) and n.value.id == "KERNEL_COMMIT_ARGS"
        ]
        self.assertTrue(starred, "git_authority must splice KERNEL_COMMIT_ARGS into its git argv")

    def test_no_unmapped_identity_remains_anywhere_in_the_kernel(self):
        for path in (ROOT / "factory_kernel").glob("*.py"):
            self.assertNotIn("dark-factory@users.noreply", path.read_text(encoding="utf-8"), path.name)


if __name__ == "__main__":
    unittest.main()
