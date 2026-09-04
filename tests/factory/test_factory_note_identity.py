"""Every object the kernel writes carries the kernel identity, the provenance note included.

The GitHub runner configures no git user. Worker commits, the re-head rebase and the safe revert
already splice KERNEL_COMMIT_ARGS; the provenance note write never had it, and it was the first
kernel-made object no run had reached until #79 (D-037). These tests run the real notes write
in a repository with empty global and system git config, so the runner's condition is exercised,
not assumed.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.provenance import NOTE_REF  # noqa: E402
from factory_kernel.worker_policy import KERNEL_COMMIT_ARGS, KERNEL_COMMIT_EMAIL, KERNEL_COMMIT_NAME  # noqa: E402

PROVENANCE = ROOT / "scripts" / "factory_provenance.py"


def identityless_env(tmp: Path) -> dict[str, str]:
    """An environment in which git can find no user.name/user.email anywhere."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({
        "HOME": str(tmp / "home"),
        "XDG_CONFIG_HOME": str(tmp / "xdg"),
        "GIT_CONFIG_GLOBAL": str(tmp / "no-global-config"),
        "GIT_CONFIG_SYSTEM": str(tmp / "no-system-config"),
        "GIT_CONFIG_NOSYSTEM": "1",
    })
    env.pop("GIT_AUTHOR_NAME", None)
    env.pop("GIT_COMMITTER_NAME", None)
    (tmp / "home").mkdir(exist_ok=True)
    return env


def git(cwd: Path, env: dict[str, str], *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if check and proc.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stdout}{proc.stderr}")
    return proc


class NoteIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-note-identity-")
        tmp = Path(self.tmp.name)
        self.env = identityless_env(tmp)
        self.repo = tmp / "repo"
        self.repo.mkdir()
        git(self.repo, self.env, "init", "-q")
        (self.repo / "x.txt").write_text("x\n", encoding="utf-8")
        git(self.repo, self.env, "add", "x.txt")
        # The subject commit itself needs an identity; give it one explicitly and only here.
        git(self.repo, self.env, "-c", "user.name=subject", "-c", "user.email=subject@example.invalid",
            "commit", "-q", "-m", "subject")
        self.head = git(self.repo, self.env, "rev-parse", "HEAD").stdout.strip()
        self.note = tmp / "note.json"
        self.note.write_text('{"version":"1.0"}\n', encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_the_runner_condition_is_real(self) -> None:
        """Without an identity the notes write fails exactly as it did on the runner."""
        proc = git(self.repo, self.env, "notes", f"--ref={NOTE_REF}", "add", "-f", "-F", str(self.note),
                   self.head, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Author identity unknown", proc.stderr + proc.stdout)

    def test_the_kernel_identity_lets_the_note_be_written(self) -> None:
        git(self.repo, self.env, *KERNEL_COMMIT_ARGS, "notes", f"--ref={NOTE_REF}", "add", "-f", "-F",
            str(self.note), self.head)
        shown = git(self.repo, self.env, "notes", f"--ref={NOTE_REF}", "show", self.head).stdout
        self.assertIn('"version":"1.0"', shown)
        author = git(self.repo, self.env, "log", "-1", "--format=%an <%ae>|%cn <%ce>", NOTE_REF).stdout.strip()
        expected = f"{KERNEL_COMMIT_NAME} <{KERNEL_COMMIT_EMAIL}>"
        self.assertEqual(author, f"{expected}|{expected}")

    def test_the_provenance_script_spells_the_notes_write_with_the_identity(self) -> None:
        """The script's notes write is the exact argv the test above proves; reads stay bare."""
        tree = ast.parse(PROVENANCE.read_text(encoding="utf-8"))
        writes, reads = [], []
        for node in ast.walk(tree):
            if not isinstance(node, ast.List) or not node.elts:
                continue
            first = node.elts[0]
            if not (isinstance(first, ast.Constant) and first.value == "git"):
                continue
            literals = [e.value for e in node.elts if isinstance(e, ast.Constant)]
            starred = [e.value.id for e in node.elts if isinstance(e, ast.Starred) and isinstance(e.value, ast.Name)]
            if "notes" in literals and "add" in literals:
                writes.append(starred)
            elif "notes" in literals and "show" in literals:
                reads.append(starred)
        self.assertEqual(writes, [["KERNEL_COMMIT_ARGS"]], "the notes write must splice KERNEL_COMMIT_ARGS")
        self.assertEqual(reads, [[]], "notes reads must not carry an identity")


class ObjectCreatingCallsCarryIdentityTests(unittest.TestCase):
    """The inventory of kernel git calls that create objects, each spliced with the identity."""

    SITES = {
        "factory_kernel/git_authority.py": "commit",
        "factory_kernel/runtime.py": "rebase",
        "factory_kernel/worker_runtime.py": "revert",
        "scripts/factory_provenance.py": "notes",
    }

    def test_every_object_creating_git_call_splices_the_identity(self) -> None:
        for rel, verb in self.SITES.items():
            with self.subTest(rel=rel, verb=verb):
                source = (ROOT / rel).read_text(encoding="utf-8")
                tree = ast.parse(source)
                found = False
                for node in ast.walk(tree):
                    # A git argv is either a list literal (["git", ..]) or the positional args
                    # of a helper call (self._git(..)); the identity must be spliced in either.
                    if isinstance(node, (ast.List, ast.Tuple)):
                        elts = node.elts
                    elif isinstance(node, ast.Call):
                        elts = node.args
                    else:
                        continue
                    literals = [e.value for e in elts if isinstance(e, ast.Constant)]
                    if verb not in literals:
                        continue
                    starred = [e.value.id for e in elts if isinstance(e, ast.Starred) and isinstance(e.value, ast.Name)]
                    if "KERNEL_COMMIT_ARGS" in starred:
                        found = True
                self.assertTrue(found, f"{rel}: the {verb!r} call must splice KERNEL_COMMIT_ARGS")


if __name__ == "__main__":
    unittest.main()
