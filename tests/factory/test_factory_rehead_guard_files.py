"""A guard file is hashed by RED but never changed by the test-author commit (D-072).

Build run of issue #103 produced PR #134, main moved, and the model-free re-head refused three
times: `rebased test-author commit does not change exactly the RED-hashed files`. Measured from
the PR, the test-author commit `c15e61e` changes exactly one file,
`app/frontend/src/__tests__/ChatArea-strictmode-first-send.test.tsx`, while the RED proof's
`files` map holds two -- that one and the existing `useStreamingResponse.test.ts` its three
guard checkpoints pin. The map is the union of red and guard files, hashed for immutability
(D-058); the commit's diff is the red half alone (D-064). Comparing one to the other refuses
every honest build that declares a guard.

These tests pin the rule that replaces it, in the re-head and in the evidence bundle that
follows it: the commit's changed files are matched against the *red*-checkpoint files exactly;
a guard file no red checkpoint declares must not appear in the diff; and every guard file must
exist at the rebased commit and hash there to what the pack recorded -- immutability checked
rather than inferred from an equality that happened to cover it.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.rehearsal import NEW_TEST_COMMIT, Scenario, rehearse  # noqa: E402
from tests.factory.test_factory_refusals import WT_OK, refusal_marker  # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evidence = _load("factory_evidence_rehead_guards", "scripts/factory_evidence.py")


# The real shape of PR #134: one new test file a red checkpoint declares, one existing hook
# test three guard checkpoints pin, and a `files` map that hashes both.
RED_TEST = "app/frontend/src/__tests__/ChatArea-strictmode-first-send.test.tsx"
GUARD_TEST = "app/frontend/src/hooks/useStreamingResponse.test.ts"
RED_TEXT = "it('sends once in strict mode', () => {})\n"
GUARD_TEXT = "it('streams tokens', () => {})\n"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


GUARDED_FILES = {RED_TEST: sha(RED_TEXT), GUARD_TEST: sha(GUARD_TEXT)}
GUARDED_CHECKPOINTS = (
    {"acceptance_id": "AC-1", "cwd": "app/frontend", "argv": ["bun", "run", "test"],
     "files": [RED_TEST], "expected_failure": "expected one POST"},
    {"acceptance_id": "AC-2", "kind": "guard", "cwd": "app/frontend",
     "argv": ["bun", "run", "test"], "files": [GUARD_TEST]},
    {"acceptance_id": "AC-3", "kind": "guard", "cwd": "app/frontend",
     "argv": ["bun", "run", "test"], "files": [GUARD_TEST]},
    {"acceptance_id": "AC-4", "kind": "guard", "cwd": "app/frontend",
     "argv": ["bun", "run", "test"], "files": [GUARD_TEST]},
)
GUARDED_WORKTREE = dict(WT_OK, **{RED_TEST: RED_TEXT, GUARD_TEST: GUARD_TEXT})


def guarded(name: str, **overrides) -> Scenario:
    """A stale-base PR whose certified build declares one red checkpoint and three guards."""
    values = dict(
        name=name, command="rehead", labels=("factory:needs-fix",),
        comments=(refusal_marker("stale_base"),),
        red_files=GUARDED_FILES, worktree_files=GUARDED_WORKTREE,
        pack_checkpoints=GUARDED_CHECKPOINTS,
        test_commit_changed=(RED_TEST,),
    )
    values.update(overrides)
    return Scenario(**values)


class GuardedReheadTests(unittest.TestCase):
    """The measured shape of PR #134 re-heads."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.trace = rehearse(guarded("rehead-with-guards"))

    def test_the_rehead_completes(self):
        self.assertEqual(self.trace.outcome, "returned", self.trace.error)

    def test_red_is_replayed_at_the_rebased_test_commit(self):
        t = self.trace
        self.assertEqual(len(t.execs("factory_proof.py", "red")), 1)
        self.assertIn(f"git:checkout-detach:{NEW_TEST_COMMIT}", t.names())
        self.assertEqual(t.rehead_red_proof["test_commit"], NEW_TEST_COMMIT)

    def test_the_guards_travel_into_the_replayed_spec_as_guards(self):
        spec = self.trace.rehead_red_spec
        self.assertEqual(spec["version"], "2.1")
        self.assertEqual([cp["acceptance_id"] for cp in spec["checkpoints"]],
                         ["AC-1", "AC-2", "AC-3", "AC-4"])
        kinds = [cp.get("kind") for cp in spec["checkpoints"]]
        self.assertEqual(kinds, [None, "guard", "guard", "guard"])
        self.assertNotIn("expected_failure", spec["checkpoints"][1])

    def test_every_guard_file_is_hashed_at_the_rebased_commit(self):
        asked = [n for n in self.trace.names() if n.startswith("git:blob:")]
        self.assertIn(f"git:blob:{NEW_TEST_COMMIT}:{GUARD_TEST}", asked)
        self.assertNotIn(f"git:blob:{NEW_TEST_COMMIT}:{RED_TEST}", asked,
                         "a red file is verified by the diff, not by a guard hash")

    def test_the_pr_is_handed_back_to_validation(self):
        self.assertIn("add_pr_label:factory:needs-review", self.trace.names())
        self.assertTrue(any(n.startswith("push_branch") for n in self.trace.names()))


class GuardedReheadRefusalTests(unittest.TestCase):
    def assert_refused_before_red(self, t) -> None:
        self.assertEqual(t.outcome, "NeedsHuman", t.error)
        self.assertEqual(t.execs("factory_proof.py", "red"), [])
        self.assertEqual(t.execs("factory_proof.py", "green"), [])
        self.assertFalse(any(n.startswith("push_branch") for n in t.names()))
        self.assertIn("add_pr_label:factory:needs-human", t.names())

    def test_a_commit_that_also_changed_a_guard_file_is_refused_by_name(self):
        t = rehearse(guarded("guard-rewritten", test_commit_changed=(RED_TEST, GUARD_TEST)))
        self.assert_refused_before_red(t)
        self.assertIn("changed guard checkpoint files", t.error)
        self.assertIn(GUARD_TEST, t.error)
        self.assertIn("must not rewrite", t.error)

    def test_a_commit_missing_a_red_file_is_refused_by_name(self):
        t = rehearse(guarded("red-unchanged", test_commit_changed=()))
        self.assert_refused_before_red(t)
        self.assertIn("red checkpoint files", t.error)
        self.assertIn(RED_TEST, t.error)
        self.assertIn("must be new or modified", t.error)

    def test_a_commit_that_changed_an_undeclared_file_is_refused_by_name(self):
        t = rehearse(guarded("stray", test_commit_changed=(RED_TEST, "app/backend/main.py")))
        self.assert_refused_before_red(t)
        self.assertIn("changed undeclared files ['app/backend/main.py']", t.error)
        self.assertIn("must be declared by a checkpoint", t.error)

    def test_a_guard_file_deleted_at_the_rebased_commit_is_refused(self):
        t = rehearse(guarded("guard-absent", blob_hashes={GUARD_TEST: None}))
        self.assert_refused_before_red(t)
        self.assertIn(f"guard checkpoint file {GUARD_TEST!r} does not exist", t.error)
        self.assertIn(NEW_TEST_COMMIT, t.error)
        self.assertIn("a guard pins an existing test", t.error)

    def test_a_guard_file_whose_hash_moved_is_refused_with_both_hashes(self):
        moved = sha("it('streams tokens differently', () => {})\n")
        t = rehearse(guarded("guard-moved", blob_hashes={GUARD_TEST: moved}))
        self.assert_refused_before_red(t)
        self.assertIn(f"guard checkpoint file {GUARD_TEST!r} differs", t.error)
        self.assertIn(f"RED hashed {GUARDED_FILES[GUARD_TEST]}", t.error)
        self.assertIn(f"the commit holds {moved}", t.error)

    def test_a_proof_without_checkpoints_is_refused(self):
        t = rehearse(guarded("no-checkpoints", pack_checkpoints=()))
        self.assert_refused_before_red(t)
        self.assertIn("has no checkpoints", t.error)

    def test_a_proof_of_only_guards_is_refused(self):
        t = rehearse(guarded("guards-only", pack_checkpoints=GUARDED_CHECKPOINTS[1:]))
        self.assert_refused_before_red(t)
        self.assertIn("declares no red checkpoint", t.error)


class RedOnlyReheadUnchangedTests(unittest.TestCase):
    """A proof from before guards existed behaves exactly as it did."""

    def test_a_red_only_proof_still_reheads(self):
        t = rehearse(Scenario(
            name="red-only", command="rehead", labels=("factory:needs-fix",),
            comments=(refusal_marker("stale_base"),),
            red_files={RED_TEST: sha(RED_TEXT)},
            worktree_files=dict(WT_OK, **{RED_TEST: RED_TEXT}),
            pack_checkpoints=(
                {"acceptance_id": "AC-1", "cwd": ".", "argv": ["pytest"],
                 "files": [RED_TEST], "expected_failure": "AssertionError"},
            ),
        ))
        self.assertEqual(t.outcome, "returned", t.error)
        self.assertEqual(t.rehead_red_spec["version"], "2.0")
        self.assertEqual([n for n in t.names() if n.startswith("git:blob:")], [],
                         "a red-only proof asks for no guard hash")


class EvidenceReplayReadsTheRedHalfTests(unittest.TestCase):
    """The evidence bundle replays the same rule; it held the same conflation (D-072)."""

    def refusal(self, changed, checkpoints) -> str:
        err = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stderr(err):
            evidence.verify_test_commit_diff(changed, checkpoints)
        return err.getvalue()

    def test_a_commit_changing_only_the_red_file_is_accepted(self):
        self.assertIsNone(
            evidence.verify_test_commit_diff([RED_TEST], list(GUARDED_CHECKPOINTS))
        )

    def test_a_changed_guard_file_is_refused_by_name(self):
        message = self.refusal([RED_TEST, GUARD_TEST], list(GUARDED_CHECKPOINTS))
        self.assertIn("changed guard checkpoint files", message)
        self.assertIn(GUARD_TEST, message)

    def test_an_unchanged_red_file_is_refused_by_name(self):
        message = self.refusal([], list(GUARDED_CHECKPOINTS))
        self.assertIn("red checkpoint files", message)
        self.assertIn(RED_TEST, message)

    def test_an_undeclared_file_is_refused_by_name(self):
        message = self.refusal([RED_TEST, "app/backend/main.py"], list(GUARDED_CHECKPOINTS))
        self.assertIn("changed undeclared files ['app/backend/main.py']", message)

    def test_the_red_and_guard_halves_are_read_from_the_checkpoints(self):
        red, guard = evidence.checkpoint_file_sets(list(GUARDED_CHECKPOINTS))
        self.assertEqual(red, {RED_TEST})
        self.assertEqual(guard, {GUARD_TEST})

    def test_replay_red_asks_the_checkpoints_not_the_file_map(self):
        source = (ROOT / "scripts" / "factory_evidence.py").read_text(encoding="utf-8")
        self.assertIn(
            'verify_test_commit_diff([x for x in diff.splitlines() if x], proof["checkpoints"])',
            source,
        )
        self.assertNotIn('!= sorted(files):', source)


class ReheadSourceTests(unittest.TestCase):
    def test_the_kernel_never_compares_the_diff_to_the_whole_file_map(self):
        source = (ROOT / "factory_kernel" / "runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("if changed != sorted(files):", source)
        self.assertIn(
            "self._verify_test_commit_files(cwd, test_commit, changed, red_files, guard_files, files)",
            source,
        )

    def test_a_guard_hash_is_read_from_the_commit_as_bytes(self):
        source = (ROOT / "factory_kernel" / "runtime.py").read_text(encoding="utf-8")
        self.assertIn('["git", "cat-file", "blob", f"{commit}:{rel}"]', source)
        self.assertIn("hashlib.sha256(proc.stdout).hexdigest()", source)


class BlobHashIsReadFromRealGitTests(unittest.TestCase):
    """The one part the rehearsal fakes, run against a real repository."""

    def test_a_blob_hashes_to_its_bytes_and_an_absent_path_is_none(self):
        from factory_kernel.runtime import KernelRuntime

        def git(cwd, *args):
            proc = subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                 "-c", "core.autocrlf=false", *args],
                cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if proc.returncode:
                raise RuntimeError(f"git {args}: {proc.stdout}{proc.stderr}")
            return proc.stdout.strip()

        with tempfile.TemporaryDirectory(prefix="dark-factory-blob-") as tmp:
            root = Path(tmp)
            git(root, "init", "-q", "-b", "main", ".")
            target = root / "tests" / "kept.test.ts"
            target.parent.mkdir(parents=True)
            target.write_bytes(GUARD_TEXT.encode("utf-8"))
            git(root, "add", "tests/kept.test.ts")
            git(root, "commit", "-q", "-m", "kept")
            commit = git(root, "rev-parse", "HEAD")

            read = KernelRuntime._blob_sha256
            self.assertEqual(read(None, root, commit, "tests/kept.test.ts"), sha(GUARD_TEXT))
            self.assertEqual(
                read(None, root, commit, "tests/kept.test.ts"),
                hashlib.sha256(target.read_bytes()).hexdigest(),
                "the blob's bytes are the checkout's bytes",
            )
            self.assertIsNone(read(None, root, commit, "tests/gone.test.ts"))


class ProofFileMapIsStillTheUnionTests(unittest.TestCase):
    """The map itself does not change: both halves stay immutable from RED on (D-058)."""

    def test_the_red_gate_hashes_red_and_guard_files_alike(self):
        source = (ROOT / "scripts" / "factory_proof.py").read_text(encoding="utf-8")
        self.assertIn("declared=sorted(red_files|guard_files)", source)
        self.assertIn("files={f:sha(f) for f in declared}", source)

    def test_the_reheaded_tip_is_checked_against_the_whole_map(self):
        source = (ROOT / "factory_kernel" / "runtime.py").read_text(encoding="utf-8")
        block = source[source.index("def _verify_red_unchanged"):]
        self.assertIn("for rel, expected in files.items():", block[:1600])


if __name__ == "__main__":
    unittest.main()
