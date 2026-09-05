"""A re-head replays RED at the rebased test-author commit; it never re-binds a proof by hand.

The first production re-head (run 33944595689) rebased PR #88 and handed validation a RED
proof whose `test_commit` was the pre-rebase hash, which no longer existed in the branch. The
evidence bundle refused it, correctly. These tests pin D-045: after the rebase the kernel
locates the rebased test-author commit by shape, runs `factory_proof.py red` there so the
re-issued proof is bound to a commit that is an ancestor of the new head, refuses when a
checkpoint no longer fails, and refuses a history that is not this build's.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.rehearsal import (  # noqa: E402
    NEW_HEAD, NEW_TEST_COMMIT, RED_SUBJECT, Scenario, rehearse,
)
from tests.factory.test_factory_refusals import RED_OK, WT_OK, refusal_marker  # noqa: E402


def stale(name: str, **overrides) -> Scenario:
    values = dict(
        name=name, command="rehead", labels=("factory:needs-fix",),
        comments=(refusal_marker("stale_base"),), red_files=RED_OK, worktree_files=WT_OK,
    )
    values.update(overrides)
    return Scenario(**values)


class ReheadReissuesRedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.trace = rehearse(stale("rehead-red"))

    def test_the_rehead_completes(self):
        self.assertEqual(self.trace.outcome, "returned", self.trace.error)

    def test_red_is_replayed_at_the_rebased_test_commit_between_rebase_and_green(self):
        t = self.trace
        reds = t.execs("factory_proof.py", "red")
        self.assertEqual(len(reds), 1, "exactly one RED re-issue")
        rebase = t.index("git:rebase")
        detach = t.index(f"git:checkout-detach:{NEW_TEST_COMMIT}")
        red = t.steps.index(reds[0])
        back = t.index("git:checkout-branch")
        first_green = t.steps.index(t.execs("factory_proof.py", "green")[0])
        self.assertLess(rebase, detach)
        self.assertLess(detach, red)
        self.assertLess(red, back)
        self.assertLess(back, first_green)

    def test_the_reissued_proof_is_bound_to_the_rebased_commit(self):
        proof = self.trace.rehead_red_proof
        self.assertIsNotNone(proof, "the re-head wrote no red-proof.json")
        self.assertEqual(proof["test_commit"], NEW_TEST_COMMIT)
        self.assertNotEqual(proof["test_commit"], "3" * 40, "the pack's pre-rebase commit must not survive")
        red = self.trace.execs("factory_proof.py", "red")[0]
        self.assertTrue(red.argv[red.argv.index("--output") + 1].endswith("red-proof.json"))

    def test_the_spec_is_reconstructed_from_the_packs_checkpoints(self):
        spec = self.trace.rehead_red_spec
        self.assertIsNotNone(spec, "the re-head wrote no rehead-test-spec.json")
        self.assertEqual(spec["version"], "2.0")
        self.assertEqual([cp["acceptance_id"] for cp in spec["checkpoints"]], ["AC-1"])
        self.assertEqual(spec["checkpoints"][0]["files"], sorted(RED_OK))
        self.assertNotIn("seams", spec["checkpoints"][0], "the proof compiler derives seams itself")

    def test_the_rebased_test_commit_is_verified_an_ancestor_of_the_new_head(self):
        t = self.trace
        self.assertIn("git:red-commit-is-ancestor", t.names())
        self.assertLess(t.index("git:red-commit-is-ancestor"),
                        t.steps.index(t.execs("factory_proof.py", "green")[0]))

    def test_the_rebased_history_is_read_by_shape_not_by_the_packs_hash(self):
        t = self.trace
        self.assertIn("git:log", t.names())
        self.assertLess(t.index("git:log"), t.index(f"git:checkout-detach:{NEW_TEST_COMMIT}"))

    def test_the_rehead_still_hands_the_pr_back(self):
        self.assertIn("add_pr_label:factory:needs-review", self.trace.names())
        self.assertEqual(self.trace.execs("factory_proof.py", "green").__len__(), 2)


class ReheadRedRefusalTests(unittest.TestCase):
    def assert_refused_before_green_or_push(self, t) -> None:
        self.assertEqual(t.outcome, "NeedsHuman", t.error)
        self.assertEqual(t.execs("factory_proof.py", "green"), [])
        self.assertFalse(any(n.startswith("push_branch") for n in t.names()))
        self.assertIn("add_pr_label:factory:needs-human", t.names())

    def test_a_checkpoint_that_passes_after_the_rebase_refuses(self):
        t = rehearse(stale("red-passes", red_passes_after_rebase=True))
        self.assertEqual(t.outcome, "ToolRefused", t.error)
        self.assertIn("unexpectedly passed", t.error)
        self.assertEqual(t.execs("factory_proof.py", "green"), [])
        self.assertFalse(any(n.startswith("push_branch") for n in t.names()))
        self.assertIn("git:checkout-branch", t.names(), "the worktree is returned to the tip on failure")

    def test_a_history_without_the_test_commit_first_refuses(self):
        t = rehearse(stale("wrong-order", rebased_log=(
            (NEW_HEAD, "fix(factory): satisfy issue #42"), (NEW_TEST_COMMIT, RED_SUBJECT))))
        self.assert_refused_before_green_or_push(t)
        self.assertIn("does not start with the test-author commit", t.error)
        self.assertEqual(t.execs("factory_proof.py", "red"), [])

    def test_a_history_with_two_test_commits_refuses(self):
        t = rehearse(stale("two-red", rebased_log=(
            (NEW_TEST_COMMIT, RED_SUBJECT), ("7" * 40, RED_SUBJECT), (NEW_HEAD, "fix(factory): satisfy issue #42"))))
        self.assert_refused_before_green_or_push(t)
        self.assertIn("more than one test-author commit", t.error)

    def test_a_test_commit_that_changes_other_files_refuses(self):
        # The rehearsed parent diff of any commit other than NEW_TEST_COMMIT is production files.
        t = rehearse(stale("wrong-files", rebased_log=(
            ("7" * 40, RED_SUBJECT), (NEW_HEAD, "fix(factory): satisfy issue #42"))))
        self.assert_refused_before_green_or_push(t)
        self.assertIn("does not change exactly the RED-hashed files", t.error)
        self.assertEqual(t.execs("factory_proof.py", "red"), [])

    def test_an_empty_rebased_history_refuses(self):
        t = rehearse(stale("empty", rebased_log=()))
        self.assert_refused_before_green_or_push(t)


class ReheadRedSourceTests(unittest.TestCase):
    """The re-issue is a replay, never an edit of the proof."""

    def test_the_kernel_never_writes_test_commit_into_a_proof(self):
        source = (ROOT / "factory_kernel" / "runtime.py").read_text(encoding="utf-8")
        self.assertNotIn('["test_commit"] =', source)
        self.assertNotIn("'test_commit'] =", source)
        self.assertIn('"python", "scripts/factory_proof.py", "red",', source)
        self.assertIn("self._reissue_red(pack, paths, worktree.path, env, new_base=new_base, new_head=new_head)", source)


if __name__ == "__main__":
    unittest.main()
