"""The provenance pack records the base the branch was cut from, and consumers read it.

The first production re-head (worker run 33938048704, PR #85) could not fetch the pack it was
meant to re-head: publish had recorded `base_sha` as GitHub's baseRefOid, which is the current
tip of main and had advanced past the branch point mid-build (14701b8, not an ancestor of the
head aa38448), while the re-head guessed the base with `merge-base` (0c17566, the true cut
point). Two programs, two answers, and the pack was the one lying (D-042).

Three properties, each pinned here:
1. A pack records the exact commit the branch was cut from, never the base branch tip.
2. A pack whose base is not an ancestor of its head is refused, at publish and at every read.
3. A consumer reads the base the pack declares and verifies it; it never recomputes it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.canonical import sha256_value  # noqa: E402
from factory_kernel.provenance import NOTE_REF, pack_identity, verify_pack  # noqa: E402
from factory_kernel.worker_policy import KERNEL_COMMIT_ARGS  # noqa: E402
from harness.rehearsal import BASE, HEAD, NEW_BASE, Scenario, builder_pack, rehearse  # noqa: E402

PROVENANCE = ROOT / "scripts" / "factory_provenance.py"
TRUE_BASE = "6" * 40   # what a pack declares when it differs from what a guess would return


def refusal_marker(reason: str) -> str:
    from factory_kernel.refusal import render_refusal_marker
    return render_refusal_marker({
        "version": "1.0", "pr": 77, "reason_code": reason, "authority": "rehearsed",
        "stage": "evidence_spine", "head": HEAD, "base": BASE, "timestamp": "2026-09-05T00:00:00Z",
    })


import hashlib  # noqa: E402

RED_OK = {"tests/red_test.py": hashlib.sha256(b"assert True\n").hexdigest()}
WT_OK = {"tests/red_test.py": "assert True\n"}


def rehead(**overrides) -> object:
    values = dict(
        name="rehead", command="rehead", labels=("factory:needs-fix",),
        comments=(refusal_marker("stale_base"),), red_files=RED_OK, worktree_files=WT_OK,
    )
    values.update(overrides)
    return rehearse(Scenario(**values))


class PackIdentityTests(unittest.TestCase):
    def test_identity_is_read_before_contents_are_trusted(self):
        pack = builder_pack(base=TRUE_BASE)
        self.assertEqual(pack_identity(pack), {"head_sha": HEAD, "base_sha": TRUE_BASE, "issue": 42})

    def test_a_base_that_is_not_an_ancestor_of_the_head_is_refused(self):
        pack = builder_pack(base=TRUE_BASE)
        with self.assertRaisesRegex(ValueError, "not an ancestor"):
            verify_pack(pack, is_ancestor=lambda base, head: False)
        self.assertEqual(verify_pack(pack, is_ancestor=lambda base, head: True), pack)

    def test_the_ancestor_check_sees_the_declared_base_and_head(self):
        seen = {}

        def is_ancestor(base: str, head: str) -> bool:
            seen.update(base=base, head=head)
            return True

        verify_pack(builder_pack(base=TRUE_BASE), is_ancestor=is_ancestor)
        self.assertEqual(seen, {"base": TRUE_BASE, "head": HEAD})


class ReheadReadsThePackTests(unittest.TestCase):
    """The rehearsal's fake `merge-base` returns BASE on purpose; the pack declares TRUE_BASE."""

    def test_a_pack_whose_base_differs_from_a_merge_base_guess_is_still_fetched(self):
        trace = rehead(pack_base=TRUE_BASE)
        self.assertEqual(trace.outcome, "returned", trace.error)
        fetch = trace.execs("factory_provenance.py", "fetch")[0]
        self.assertEqual(fetch.argv[fetch.argv.index("--base") + 1], TRUE_BASE)
        self.assertIn("git:is-ancestor", trace.names())
        self.assertNotIn("git:merge-base-guess", trace.names(), "the base was recomputed instead of read")

    def test_the_declared_base_is_read_before_the_pack_is_fetched(self):
        trace = rehead(pack_base=TRUE_BASE)
        peek = trace.execs("factory_provenance.py", "peek")[0]
        fetch = trace.execs("factory_provenance.py", "fetch")[0]
        self.assertLess(trace.steps.index(peek), trace.steps.index(fetch))

    def test_a_declared_base_that_is_not_an_ancestor_is_refused_before_any_rebase(self):
        trace = rehead(pack_base=TRUE_BASE, pack_base_is_ancestor=False)
        self.assertEqual(trace.outcome, "NeedsHuman")
        self.assertIn("not an ancestor", trace.error)
        self.assertFalse(trace.happened("git:rebase"))

    def test_the_republished_pack_records_the_rebased_base(self):
        trace = rehead(pack_base=TRUE_BASE)
        publish = trace.execs("factory_provenance.py", "publish")[0]
        self.assertEqual(publish.argv[publish.argv.index("--base") + 1], NEW_BASE)


class ValidateClassifiesEarlyTests(unittest.TestCase):
    def test_a_pack_base_that_differs_from_the_current_base_is_a_stale_base_refusal(self):
        # GitHub reports BASE as the PR base; the pack declares a different cut point.
        trace = rehearse(Scenario("validate-stale", pack_base=TRUE_BASE))
        self.assertEqual(trace.outcome, "ToolRefused")
        self.assertEqual(trace.refusal_record["reason_code"], "stale_base")
        self.assertEqual(trace.refusal_record["stage"], "provenance")
        self.assertFalse(trace.happened("factory_evidence.py"))


class BuildRecordsTheCutPointTests(unittest.TestCase):
    """publish receives build_issue's initial base even when origin/main advances mid-build."""

    def test_publish_base_is_the_commit_the_branch_was_cut_from(self):
        source = (ROOT / "factory_kernel" / "runtime.py").read_text(encoding="utf-8")
        start = source.index("    def build_issue(")
        end = source.index("    def _attach_and_publish(")
        body = source[start:end]
        self.assertIn('base_sha = self._git("rev-parse", f"origin/{self.config.default_branch}")', body)
        self.assertIn("base_sha=base_sha", body, "build_issue must hand its cut point to _run_env")
        attach = source[end:source.index("    def _hand_to_review(")]
        self.assertIn('"--base", base_sha,', attach)
        self.assertIn('env.get("FACTORY_BASE_SHA")', attach)
        self.assertNotIn("baseRefOid", attach)

    def test_publish_refuses_without_the_cut_point(self):
        from factory_kernel.runtime import KernelRuntime, RunPaths

        rt = object.__new__(KernelRuntime)
        calls = []
        rt._exec = lambda argv, **kw: calls.append(argv) or ""  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "r")
            with self.assertRaisesRegex(RuntimeError, "FACTORY_BASE_SHA"):
                rt._attach_and_publish(paths, Path(tmp), {"ARTIFACTS_DIR": tmp}, 77)
        self.assertEqual(len(calls), 2, "both attach programs ran; publish did not")


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *KERNEL_COMMIT_ARGS, "-c", "core.autocrlf=false", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stdout}{proc.stderr}")
    return proc.stdout.strip()


class ScriptTests(unittest.TestCase):
    """The script's peek/publish against a real repository and a real note."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-pack-base-")
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        (self.repo / "a.txt").write_text("a\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "b.txt").write_text("b\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "head")
        self.head = git(self.repo, "rev-parse", "HEAD")
        # An unrelated commit that is NOT an ancestor of head: what main looked like later.
        git(self.repo, "checkout", "-q", "--orphan", "later")
        (self.repo / "c.txt").write_text("c\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "later")
        self.later = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "checkout", "-q", self.head)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_script(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / "scripts")])
        env.pop("GH_TOKEN", None)
        env.pop("GITHUB_TOKEN", None)
        return subprocess.run(
            [sys.executable, str(PROVENANCE), *args],
            cwd=self.repo, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )

    def note(self, base: str) -> None:
        pack = builder_pack(head=self.head, base=base)
        note_file = self.repo / ".." / "note.json"
        note_file.write_text(json.dumps(pack), encoding="utf-8")
        git(self.repo, "notes", f"--ref={NOTE_REF}", "add", "-f", "-F", str(note_file), self.head)

    def test_peek_prints_the_binding_the_note_declares(self):
        self.note(self.base)
        proc = self.run_script("peek", "--head", self.head, "--local-notes")
        line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("{")), "")
        self.assertEqual(json.loads(line), {"base_sha": self.base, "head_sha": self.head, "issue": 42}, proc.stdout + proc.stderr)

    def test_peek_refuses_a_note_attached_to_another_head(self):
        self.note(self.base)
        proc = self.run_script("peek", "--head", self.base, "--local-notes")
        self.assertNotEqual(proc.returncode, 0)

    def test_fetch_refuses_a_note_whose_base_is_not_an_ancestor_of_its_head(self):
        self.note(self.later)
        out = self.repo / ".." / "out"
        proc = self.run_script(
            "fetch", "--head", self.head, "--base", self.later, "--issue", "42",
            "--output-dir", str(out), "--local-notes",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not an ancestor", proc.stdout + proc.stderr)

    def test_fetch_accepts_the_true_cut_point(self):
        self.note(self.base)
        out = self.repo / ".." / "out"
        proc = self.run_script(
            "fetch", "--head", self.head, "--base", self.base, "--issue", "42",
            "--output-dir", str(out), "--local-notes",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PROVENANCE_FETCHED", proc.stdout)

    def test_publish_refuses_a_base_that_is_not_an_ancestor(self):
        """publish needs a PR and a token to get further; the ancestry refusal comes first only
        after the PR lookup, so exercise the guard function directly on the real repository."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("fp", PROVENANCE)
        module = importlib.util.module_from_spec(spec)
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            spec.loader.exec_module(module)
            self.assertTrue(module._is_ancestor(self.base, self.head))
            self.assertFalse(module._is_ancestor(self.later, self.head))
        finally:
            os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
