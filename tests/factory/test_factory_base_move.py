"""A base move is cheap to notice and free to recover from (D-077).

Two things happen every time a maintainer merges to `main` under an open factory pull request,
and both of them cost more than they should.

**It is noticed late.** Whether a PR's trust root is still current with `main` is a `git diff`,
and until now it was asked only inside the Evidence Bundle -- after the blinded code holdout,
the architecture holdout and the three pre-code certifiers had all run. Run 34112301646 spent
869 s and $1.64 on those five judges and was then refused in 0.395 s by exactly that diff. It
was the fifth time PR #134 paid for it. The pack-base comparison the kernel already does
(D-042) does not catch this case: a pack cut from the current base can still carry a trust root
that `main` has since moved past.

**It is recovered from by hand.** `rehead_eligible` allowed exactly one re-head per pull
request. The budget is there to bound a loop nobody has data to size, but a base that moved
because a human merged is not that loop: the re-head is model-free, it re-proves RED at the
rebased commit and every downstream gate, and how many happen is decided by how often a
maintainer merges. PR #134 hit the wall three times in nine hours and each time a maintainer
deleted the marker comment by hand to let the factory continue.

These tests pin both halves: the cheap check runs before anything is paid for and its refusal
still classifies as a stale base, and the budget counts base moves rather than pull requests
while still refusing a PR that has actually changed.
"""

from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import factory_kernel.refusal as R  # noqa: E402

HEAD_A = "a" * 40
HEAD_B = "b" * 40
HEAD_C = "c" * 40


def refusal(code: str) -> str:
    return f"{R.REFUSAL_MARKER} {json.dumps({'reason_code': code, 'pr': 1})} -->"


def rehead(new_head: str) -> str:
    return f"{R.REHEAD_MARKER} {json.dumps({'new_head': new_head, 'pr': 1})} -->"


class ReheadBudgetTests(unittest.TestCase):
    """The budget counts base moves, not pull requests."""

    def test_a_first_stale_base_is_eligible(self):
        self.assertTrue(R.rehead_eligible([refusal("stale_base")], head=HEAD_A))

    def test_a_second_stale_base_on_the_head_the_last_rehead_produced_is_eligible(self):
        # Nothing happened to this PR since the last re-head except that main moved again.
        bodies = [refusal("stale_base"), rehead(HEAD_B), refusal("stale_base")]
        self.assertTrue(R.rehead_eligible(bodies, head=HEAD_B))

    def test_a_third_one_is_eligible_too_because_maintainer_merges_bound_it(self):
        bodies = [
            refusal("stale_base"), rehead(HEAD_B),
            refusal("stale_base"), rehead(HEAD_C),
            refusal("stale_base"),
        ]
        self.assertTrue(R.rehead_eligible(bodies, head=HEAD_C))

    def test_a_head_that_is_not_the_last_reheads_output_is_refused(self):
        # The pull request itself changed after the re-head. That IS the loop the budget bounds.
        bodies = [refusal("stale_base"), rehead(HEAD_B), refusal("stale_base")]
        self.assertFalse(R.rehead_eligible(bodies, head=HEAD_C))

    def test_only_the_latest_rehead_marker_decides(self):
        bodies = [rehead(HEAD_A), rehead(HEAD_B), refusal("stale_base")]
        self.assertFalse(R.rehead_eligible(bodies, head=HEAD_A))
        self.assertTrue(R.rehead_eligible(bodies, head=HEAD_B))

    def test_a_caller_that_cannot_say_the_head_gets_the_strict_rule(self):
        # A budget that cannot check its own condition must refuse, not assume.
        bodies = [refusal("stale_base"), rehead(HEAD_B), refusal("stale_base")]
        self.assertFalse(R.rehead_eligible(bodies))
        self.assertFalse(R.rehead_eligible(bodies, head=""))

    def test_a_caller_with_no_head_is_still_eligible_before_any_rehead(self):
        self.assertTrue(R.rehead_eligible([refusal("stale_base")]))

    def test_any_other_latest_refusal_is_not_eligible(self):
        for code in ("evidence_spine", "code_holdout", "security_guard", "merge_preauth"):
            with self.subTest(code=code):
                self.assertFalse(R.rehead_eligible([refusal(code)], head=HEAD_A))

    def test_a_stale_base_followed_by_another_refusal_is_not_eligible(self):
        bodies = [refusal("stale_base"), refusal("code_holdout")]
        self.assertFalse(R.rehead_eligible(bodies, head=HEAD_A))

    def test_no_refusal_at_all_is_not_eligible(self):
        self.assertFalse(R.rehead_eligible([], head=HEAD_A))


class BudgetClauseTests(unittest.TestCase):
    """The clause on its own, for the moment the kernel is still writing the refusal.

    `_record_validation_failure` decides whether to escalate to a human WHILE it composes the
    stale-base refusal comment, so that marker is not in the comments yet and the eligibility
    predicate would answer False for the wrong reason. It must ask the budget, not the whole
    predicate, or the message it posts contradicts what the next dispatch actually does.
    """

    def test_the_first_base_move_is_within_budget(self):
        self.assertTrue(R.rehead_budget_allows([], head=HEAD_A))

    def test_a_second_base_move_on_the_same_head_is_within_budget(self):
        self.assertTrue(R.rehead_budget_allows([rehead(HEAD_B)], head=HEAD_B))

    def test_a_second_base_move_on_a_different_head_is_not(self):
        self.assertFalse(R.rehead_budget_allows([rehead(HEAD_B)], head=HEAD_C))

    def test_it_needs_no_refusal_marker_to_answer(self):
        # The whole point: no refusal in the comments, and it still answers.
        self.assertTrue(R.rehead_budget_allows([rehead(HEAD_B)], head=HEAD_B))
        self.assertFalse(R.rehead_eligible([rehead(HEAD_B)], head=HEAD_B))

    def test_the_two_agree_once_the_refusal_is_posted(self):
        for head in (HEAD_B, HEAD_C):
            with self.subTest(head=head):
                posted = [rehead(HEAD_B), refusal("stale_base")]
                self.assertEqual(
                    R.rehead_budget_allows([rehead(HEAD_B)], head=head),
                    R.rehead_eligible(posted, head=head),
                )

    def test_the_kernel_asks_the_budget_and_not_the_old_count(self):
        runtime = (ROOT / "factory_kernel" / "runtime.py").read_text(encoding="utf-8")
        self.assertIn(
            "rehead_budget_allows(self.github.pr_comments(pr_number), head=head)", runtime
        )
        # The escalation message must not still claim a rule the kernel no longer applies.
        self.assertNotIn("The re-head budget is one per PR", runtime)


class CurrencyRefusalClassTests(unittest.TestCase):
    """Refused early, and still a stale base -- which is what makes the re-head follow."""

    def refuse(self, stage: str, detail: str) -> R.Refusal:
        exc = R.ToolRefused(
            ["python", "scripts/factory_evidence.py", "--pr", "134", "--currency-only"],
            rc=1,
            output=f"EVIDENCE_FAIL: {detail}",
        )
        return R.describe(stage, exc)

    def test_the_early_check_refusal_is_classified_stale_base(self):
        found = self.refuse(
            "trust_root_currency",
            "PR trust root is not current with origin/main; rebase required: harness/ci.py",
        )
        self.assertEqual(found.reason_code, "stale_base")

    def test_a_non_stale_failure_of_the_early_check_names_its_own_stage(self):
        found = self.refuse("trust_root_currency", "validator worktree is stale: HEAD=x PR=y")
        self.assertEqual(found.reason_code, "trust_root_currency")
        self.assertIn("factory_evidence.py", found.authority)

    def test_the_new_stage_is_a_known_reason_code_with_an_authority(self):
        self.assertIn("trust_root_currency", R.REASON_CODES)
        self.assertIn("trust_root_currency", R.AUTHORITY)
        self.assertIn("trust_root_currency", R.STAGE_CODES)

    def test_every_reason_code_still_names_an_authority(self):
        self.assertEqual(set(R.REASON_CODES), set(R.AUTHORITY))


class OrderTests(unittest.TestCase):
    """Cheap and deterministic runs before expensive and paid, or none of this saves anything."""

    def setUp(self):
        self.runtime = (ROOT / "factory_kernel" / "runtime.py").read_text(encoding="utf-8")

    def test_the_currency_check_runs_before_the_blinded_code_holdout(self):
        currency = self.runtime.index('"--currency-only",')
        holdout = self.runtime.index("_run_blinded_holdout(paths, holdout_context)")
        self.assertLess(currency, holdout)

    def test_the_currency_check_runs_before_the_precode_certifiers(self):
        currency = self.runtime.index('"--currency-only",')
        certifiers = self.runtime.index("_certify_precode_claims(\n")
        self.assertLess(currency, certifiers)

    def test_the_currency_check_is_its_own_named_stage(self):
        self.assertIn('stage = "trust_root_currency"', self.runtime)

    def test_the_currency_check_asks_for_no_more_credentials_than_it_needs(self):
        # The window between the flag and the next stage carries the scope for this call.
        window = self.runtime[
            self.runtime.index('"--currency-only",'):
            self.runtime.index('stage = "attached_evidence"',
                               self.runtime.index('"--currency-only",'))
        ]
        self.assertIn('credential_scope="github"', window)
        self.assertNotIn("validation", window)

    def test_the_dispatch_tells_the_budget_what_the_head_is(self):
        self.assertIn('head=str(pr.get("headRefOid") or "")', self.runtime)


class OneBudgetTests(unittest.TestCase):
    """Every asker of the re-head budget asks the same question, with a head (D-080).

    The budget has three askers in the kernel: the dispatcher deciding what to do next, the
    re-head itself guarding its own precondition, and the refusal path deciding whether to
    escalate to a human. `head=None` falls back to the strict pre-D-077 rule -- correct as a
    default, because a budget that cannot check its own condition must refuse -- which means an
    asker that simply forgets the argument silently applies the OLD rule while its neighbours
    apply the new one.

    That is not hypothetical. Run 34122778543: the dispatcher chose `rehead-pr` under the new
    rule and `rehead_pr` refused it under the old one -- `PR #134 is not a first stale-base
    refusal` -- leaving the pull request with no way forward at all.
    """

    def setUp(self):
        self.source = (ROOT / "factory_kernel" / "runtime.py").read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def budget_calls(self) -> list[ast.Call]:
        names = {"rehead_eligible", "rehead_budget_allows"}
        return [
            node for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id in names
        ]

    def test_the_kernel_asks_the_budget_from_every_place_that_needs_it(self):
        # Dispatcher, re-head guard, refusal path. If this drops, one of them stopped asking.
        self.assertGreaterEqual(len(self.budget_calls()), 3)

    def test_every_asker_passes_a_head(self):
        for call in self.budget_calls():
            with self.subTest(line=call.lineno):
                self.assertTrue(
                    any(kw.arg == "head" for kw in call.keywords),
                    f"factory_kernel/runtime.py:{call.lineno} asks the re-head budget without a "
                    f"head, so it silently applies the pre-D-077 rule while its neighbours do "
                    f"not",
                )

    def test_the_reheads_own_guard_no_longer_claims_a_first_refusal(self):
        self.assertNotIn("is not a first stale-base refusal", self.source)


class RehearsedRunTests(unittest.TestCase):
    """The whole validate path, rehearsed: the check runs, and refusing it stops the run early."""

    @classmethod
    def setUpClass(cls):
        from harness.rehearsal import SCENARIOS, rehearse  # noqa: PLC0415

        cls.rehearse = staticmethod(rehearse)
        cls.scenarios = {s.name: s for s in SCENARIOS}

    def test_the_currency_check_is_a_step_of_a_healthy_run(self):
        trace = self.rehearse(self.scenarios["happy"])
        self.assertTrue(trace.happened("factory_evidence.py:currency"))

    def test_it_runs_before_the_holdout_and_before_the_bundle(self):
        trace = self.rehearse(self.scenarios["happy"])
        self.assertTrue(trace.before("factory_evidence.py:currency", "holdout"))
        self.assertTrue(trace.before("factory_evidence.py:currency", "factory_evidence.py"))

    def test_refusing_it_pays_for_no_judge_and_never_merges(self):
        trace = self.rehearse(self.scenarios["trust-root-currency-fails"])
        for judge in ("holdout", "architecture-holdout", "contract-certifier",
                      "design-certifier", "governor-certifier"):
            with self.subTest(judge=judge):
                self.assertFalse(trace.happened(judge))
        self.assertFalse(trace.happened("merge_squash"))

    def test_its_own_failure_names_its_own_stage(self):
        trace = self.rehearse(self.scenarios["trust-root-currency-fails"])
        self.assertEqual(R.latest_refusal(trace.pr_comments)["reason_code"], "trust_root_currency")

    def test_a_stale_base_found_early_is_still_a_stale_base(self):
        # The classification is what makes the next dispatch re-head instead of asking a human.
        trace = self.rehearse(self.scenarios["trust-root-currency-finds-a-stale-base"])
        self.assertEqual(R.latest_refusal(trace.pr_comments)["reason_code"], "stale_base")
        self.assertTrue(R.rehead_eligible(trace.pr_comments))

    def test_the_bundle_scenario_still_refuses_at_the_bundle(self):
        # The two calls share a program; naming them apart is what keeps this scenario aimed
        # at the Evidence Bundle rather than at the pre-check that now runs first.
        trace = self.rehearse(self.scenarios["evidence-bundle-fails"])
        self.assertEqual(R.latest_refusal(trace.pr_comments)["reason_code"], "evidence_spine")
        self.assertTrue(trace.happened("holdout"))


class EvidenceCliTests(unittest.TestCase):
    """The flag short-circuits after the three cheap checks and before anything is parsed."""

    def setUp(self):
        self.source = (ROOT / "scripts" / "factory_evidence.py").read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def test_the_flag_exists(self):
        self.assertIn('"--currency-only"', self.source)

    def test_the_short_circuit_is_after_the_drift_check_and_before_the_contract(self):
        drift = self.source.index("PR trust root is not current with origin/main")
        short = self.source.index("EVIDENCE_CURRENCY_OK")
        contract = self.source.index('extract(body, "contract")')
        self.assertLess(drift, short)
        self.assertLess(short, contract)

    def test_the_bundle_arguments_are_required_without_the_flag(self):
        self.assertIn(
            "--verdict, --architecture-verdict and --output are required without", self.source
        )

    def test_the_full_run_still_asks_all_three_questions(self):
        # The early check is an extra refusal point, never a replacement: the authority is the
        # bundle run, which must still refuse a stale worktree, a touched trust root and drift.
        for probe in (
            "validator worktree is stale",
            "autonomous PR touched factory trust root",
            "PR trust root is not current with origin/main",
        ):
            with self.subTest(probe=probe):
                self.assertIn(probe, self.source)

    def test_the_program_still_parses(self):
        self.assertTrue(any(isinstance(n, ast.FunctionDef) and n.name == "main"
                            for n in ast.walk(self.tree)))


if __name__ == "__main__":
    unittest.main()
