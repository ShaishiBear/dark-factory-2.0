"""Ordering and refusal properties of the validation control plane, exercised end to end.

Every gate the validator applies already had tests. The *sequence* had none: `validate_pr` had
never run end to end in test or in production, because the factory is dormant until its trust
root lands and the only fake GitHub served dispatch selection. These assertions cover what no
per-gate test can -- that the gates run in an order that is safe, and that nothing reaches the
irreversible action unless every one of them passed.

The happy path must genuinely merge. Without it, every "this scenario did not merge" assertion
would hold for the wrong reason, in exactly the way a mutation run reporting zero caught out of
zero holds.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.rehearsal import SCENARIOS, Scenario, rehearse  # noqa: E402

AUTHORITIES = ("holdout", "architecture-holdout", "contract-certifier",
               "design-certifier", "governor-certifier")


class HappyPathTests(unittest.TestCase):
    """If this class is wrong, every refusal assertion below is vacuous."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.trace = rehearse(Scenario("happy"))

    def test_the_happy_path_actually_merges(self):
        self.assertEqual(self.trace.outcome, "returned", self.trace.error)
        self.assertTrue(self.trace.happened("merge_squash"))

    def test_the_deterministic_guard_runs_before_any_model_is_invoked(self):
        """A model must never see the change until the guard that cannot be persuaded has passed."""
        guard = self.trace.index("factory_security.py")
        agents = [i for i, s in enumerate(self.trace.steps) if s.kind == "agent"]
        self.assertTrue(agents, "no authority ran at all")
        self.assertLess(guard, min(agents))

    def test_every_authority_runs_before_the_evidence_bundle_is_built(self):
        bundle = self.trace.index("factory_evidence.py")
        for role in AUTHORITIES:
            self.assertLess(self.trace.index(role), bundle, f"{role} ran after the bundle")

    def test_the_merge_is_authorized_before_it_happens_and_verified_after(self):
        self.assertTrue(self.trace.before("merge_verify.py:pre", "merge_squash"))
        self.assertTrue(self.trace.before("merge_squash", "merge_verify.py:post"))

    def test_the_stop_button_is_reread_immediately_before_the_irreversible_action(self):
        """A stop pressed during a twenty-minute validation must still prevent the merge."""
        steps = self.trace.names()
        merge = steps.index("merge_squash")
        self.assertEqual(steps[merge - 1], "check_stop",
                         f"nothing re-checked the stop state before merging: {steps[merge-3:merge+1]}")

    def test_the_worktree_is_always_removed(self):
        self.assertTrue(self.trace.happened("worktree_removed"))


class RefusalTests(unittest.TestCase):
    """No gate may be bypassed, and a refusal must leave the branch unmerged."""

    def test_no_scenario_merges_before_its_gates_pass(self):
        # merge-verification-fails is excluded deliberately and asserted on its own below: post
        # verification runs after the squash, so it detects a bad merge rather than preventing it.
        for scenario in SCENARIOS:
            if scenario.name in {"happy"} or scenario.name.startswith("merge-verification-fails"):
                continue
            with self.subTest(scenario.name):
                trace = rehearse(scenario)
                self.assertFalse(
                    trace.happened("merge_squash"),
                    f"{scenario.name} reached the irreversible action anyway",
                )

    def test_every_authority_can_stop_the_merge(self):
        """All five, with no exceptions carved out.

        An earlier version of this test skipped `architecture-holdout` because it is the one
        authority `validate_pr` does not itself reject -- the runtime only requires a mapping back,
        and the refusal lives inside the evidence authority, which the rehearsal stands in for. The
        skip made the suite's claim that every gate refuses quietly untrue. The seam now runs the
        production rule instead of omitting it, so the case is covered rather than excluded.
        """
        for role in AUTHORITIES:
            with self.subTest(role):
                trace = rehearse(Scenario(f"reject-{role}", reject=role))
                self.assertFalse(trace.happened("merge_squash"),
                                 f"a rejecting {role} did not stop the merge")

    def test_a_rejecting_architecture_holdout_is_refused_by_the_production_rule(self):
        """And refused by the real function, not by a restatement of it in the harness."""
        trace = rehearse(Scenario("architecture-holdout-rejects", reject="architecture-holdout"))
        self.assertFalse(trace.happened("merge_squash"))
        self.assertTrue(trace.happened("factory_evidence.py"))
        self.assertIn("architecture holdout refused", trace.error)

    def test_a_failing_deterministic_tool_stops_the_run(self):
        for tool in ("factory_security.py", "factory_provenance.py",
                     "factory_evidence.py", "merge_verify.py:pre"):
            with self.subTest(tool):
                trace = rehearse(Scenario(f"fail-{tool}", fail=tool))
                self.assertFalse(trace.happened("merge_squash"))

    def test_merge_disabled_authorizes_but_does_not_merge(self):
        trace = rehearse(Scenario("merge-disabled", merge=False))
        self.assertEqual(trace.outcome, "returned", trace.error)
        self.assertTrue(trace.happened("merge_verify.py:pre"))
        self.assertFalse(trace.happened("merge_squash"))

    def test_a_pr_failing_its_identity_checks_never_reaches_a_worktree(self):
        for scenario in ("pr-not-open", "pr-unlabelled", "pr-without-exact-oids"):
            with self.subTest(scenario):
                trace = rehearse(next(s for s in SCENARIOS if s.name == scenario))
                self.assertEqual(trace.outcome, "NeedsHuman")
                self.assertFalse(trace.happened("factory_security.py"))
                self.assertEqual(trace.names("agent"), [])

    def test_a_failed_run_labels_the_pr_and_says_so(self):
        trace = rehearse(Scenario("holdout-rejects", reject="holdout"))
        self.assertIn("add_pr_label:factory:needs-fix", trace.names())
        self.assertIn("comment_pr", trace.names())
        self.assertTrue(trace.happened("worktree_removed"))


class PostMergeIncidentTests(unittest.TestCase):
    """Detection after the merge is inherent. Continuing to run afterwards is not.

    `merge_verify post` runs after the squash, so a discrepancy between what was authorized and
    what GitHub merged is found once the commit already exists on main. Nothing can change that
    ordering. What can change is the response: the ordinary failure handler would have told
    humans "No merge was authorized" -- false here -- and invited a fresh rebuild from current
    main, which is the very commit in doubt. It would also have left the hourly worker free to
    build the next issue on top of it.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.trace = rehearse(Scenario("post-merge-fails", fail="merge_verify.py:post"))

    def test_the_merge_has_already_happened_when_it_is_detected(self):
        self.assertTrue(self.trace.happened("merge_squash"))
        self.assertTrue(self.trace.before("merge_squash", "merge_verify.py:post"))
        self.assertEqual(self.trace.outcome, "PostMergeUnverified")

    def test_a_durable_remote_stop_is_raised(self):
        """A local kill file cannot contain this: the next run is a fresh hosted runner."""
        self.assertIn("create_issue:factory:stop", self.trace.names(),
                      "no stop issue was opened, so the factory would dispatch again in an hour")

    def test_the_pull_request_is_marked_for_a_human_not_for_a_rebuild(self):
        names = self.trace.names()
        self.assertIn("add_pr_label:factory:needs-human", names)
        self.assertNotIn("add_pr_label:factory:needs-fix", names)

    def test_the_humans_are_told_the_truth(self):
        body = self.trace.incident_body
        self.assertIn("POST-MERGE VERIFICATION FAILED", body)
        self.assertIn("UNTRUSTED", body)
        self.assertNotIn("No merge was authorized", body)
        self.assertNotIn("eligible for a bounded fresh rebuild", body)

    def test_a_github_outage_does_not_silently_lose_the_incident(self):
        trace = rehearse(Scenario("post-merge-fails-github-down",
                                  fail="merge_verify.py:post", refuse_issue_creation=True))
        self.assertEqual(trace.outcome, "PostMergeUnverified")
        self.assertNotIn("create_issue:factory:stop", trace.names())
        self.assertIn("comment_pr", trace.names())


class BlindingTests(unittest.TestCase):
    """The rehearsal's provider refuses to answer if it can see the repository or an environment.

    So a run that completes at all is evidence the authorities were invoked blinded; these
    assertions confirm the check was genuinely exercised rather than skipped.
    """

    def test_every_authority_was_actually_invoked_under_the_blinding_check(self):
        trace = rehearse(Scenario("happy"))
        for role in AUTHORITIES:
            self.assertIn(role, trace.names("agent"))


if __name__ == "__main__":
    unittest.main()
