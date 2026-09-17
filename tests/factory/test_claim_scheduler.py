"""Pure allowed-actions compiler in shadow (SPECIFICATION 4.1, C04): proposals only, control
before budget, merge only with the entire closure current for the exact head, missing
predecessors block, and the legacy comparison classifies rather than decides."""
from __future__ import annotations

from pathlib import Path
import unittest

from factory_kernel.canonical import sha256_value
from factory_kernel.claim_scheduler import (
    ACTIONS,
    ActionProposal,
    allowed_actions,
    compare_legacy_frontier,
    explain_actions,
)
from factory_kernel.claims import bind_programme_claims, compile_requirement_claims
from factory_kernel.dispatch_plan import DispatchObservation, select_dispatch
from factory_kernel.programme import compile_programme, compile_spec
from factory_kernel.spine import load_policy
from tests.factory.test_claims import REPO, programme_input, spec

ROOT = Path(__file__).resolve().parents[2]
POLICY = load_policy(ROOT / ".factory" / "evidence-spine.json")
HEAD = "b" * 40
OTHER = "c" * 40


def observed(**overrides) -> dict:
    fields = {"control_observed": True, "stopped": False, "fenced": False, "reconciliation_required": False,
              "budget": True, "review": [], "rehead": [], "build": [], "resume": None, "programme_ready": False,
              "continuation": "", "heads": {}, "issue_items": {}, "leases": {}, "exact_head_checked": {}}
    fields.update(overrides)
    return fields


def full_proof(head: str = HEAD, *, except_for: tuple[str, ...] = (), status: str = "current") -> dict:
    return {head: {req.claim_id: {"status": "stale" if req.claim_id in except_for else status, "attestation_id": f"att-{req.claim_id}"}
                   for req in POLICY.requirements}}


def kinds(result) -> list:
    return [(p.kind, dict(p.subject or {})) for p in result["proposals"]]


def blocked(result, kind: str) -> list:
    return [tuple(b.reason_codes) for b in result["blocked"] if b.kind == kind]


class ControlTests(unittest.TestCase):
    def test_an_unobservable_or_stopped_or_fenced_control_plane_blocks_everything_before_budget(self):
        for override, code in (({"control_observed": False}, "control_unobserved"), ({"stopped": True}, "stopped"), ({"fenced": True}, "fenced")):
            with self.subTest(code):
                result = explain_actions(None, POLICY, {}, None, observed(review=[5], build=[7], budget=False, **override))
                self.assertEqual(result["proposals"], ())
                self.assertEqual({tuple(b.reason_codes) for b in result["blocked"]}, {(code,)})
                self.assertEqual(len(result["blocked"]), len(ACTIONS))
                self.assertTrue(all(b.reason_codes != ("budget_required",) for b in result["blocked"]), "control outranks budget")

    def test_a_lease_the_reaper_would_release_proposes_reconciliation_and_holds_work(self):
        result = explain_actions(None, POLICY, {}, None, observed(reconciliation_required=True, review=[5]))
        self.assertEqual(kinds(result), [("reconcile-lease", {"kind": "leases"})])
        self.assertEqual(blocked(result, "validate-pr"), [("reconciliation_pending",)])

    def test_paid_actions_need_an_observed_allowance_and_unpaid_ones_do_not(self):
        unobserved = explain_actions(None, POLICY, {}, None, observed(review=[5], rehead=[9], budget=None))
        self.assertEqual(kinds(unobserved), [("rehead-pr", {"pr": 9})])
        self.assertEqual(blocked(unobserved, "validate-pr"), [("budget_unobserved",)])
        exhausted = explain_actions(None, POLICY, {}, None, observed(build=[7], budget=False))
        self.assertEqual(blocked(exhausted, "build-issue"), [("budget_required",)])
        self.assertEqual(kinds(exhausted), [])

    def test_a_held_lease_is_contested_ownership(self):
        result = explain_actions(None, POLICY, {}, None, observed(review=[5], leases={"pr:5": "run-1"}))
        self.assertEqual(blocked(result, "validate-pr"), [("lease_held",)])


class MergeTests(unittest.TestCase):
    def test_merge_needs_every_obligation_current_for_the_exact_verified_head(self):
        base = observed(review=[5], heads={5: HEAD}, exact_head_checked={5: HEAD})
        complete = explain_actions(None, POLICY, full_proof(), None, base)
        merge = [p for p in complete["proposals"] if p.kind == "merge-pr"]
        self.assertEqual(len(merge), 1)
        self.assertEqual(merge[0].obligation_ids, tuple(r.claim_id for r in POLICY.requirements))
        self.assertEqual(len(merge[0].prerequisite_attestation_ids), len(POLICY.requirements))
        self.assertEqual(ACTIONS["merge-pr"]["paid"], False)
        one_stale = explain_actions(None, POLICY, full_proof(except_for=("mutation",)), None, base)
        self.assertEqual(blocked(one_stale, "merge-pr"), [("obligation_incomplete:mutation",)])
        wrong_head = explain_actions(None, POLICY, full_proof(OTHER), None, base)
        self.assertEqual(blocked(wrong_head, "merge-pr"), [("proof_unavailable",)])
        unchecked = explain_actions(None, POLICY, full_proof(), None, observed(review=[5], heads={5: HEAD}))
        self.assertEqual(blocked(unchecked, "merge-pr"), [("exact_head_unverified",)])
        no_head = explain_actions(None, POLICY, full_proof(), None, observed(review=[5]))
        self.assertEqual(blocked(no_head, "merge-pr"), [("proof_unavailable",)])

    def test_only_new_claims_being_current_is_not_enough(self):
        proof = full_proof()
        del proof[HEAD]["contract"]
        result = explain_actions(None, POLICY, proof, None, observed(review=[5], heads={5: HEAD}, exact_head_checked={5: HEAD}))
        self.assertEqual(blocked(result, "merge-pr"), [("obligation_incomplete:contract",)])


class ProgrammeTests(unittest.TestCase):
    def setUp(self):
        compiled = compile_spec(spec(), repository=REPO)
        self.programme = compile_programme(programme_input(compiled=compiled), repository=REPO)
        requirements = compile_requirement_claims(compiled, repository_id="1341036238", project="citations")
        self.claims = bind_programme_claims(requirements, self.programme, None, POLICY)

    def test_a_build_names_its_items_obligations_and_a_blocked_item_needs_its_predecessor_proved(self):
        base = observed(build=[11, 12], issue_items={11: "snippet", 12: "playback"}, heads={})
        result = explain_actions(self.claims, POLICY, {}, self.programme, base)
        builds = {p.subject["item"]: p for p in result["proposals"] if p.kind == "build-issue"}
        self.assertEqual(set(builds), {"snippet"})
        self.assertEqual(builds["snippet"].obligation_ids, self.claims.item_obligations["snippet"])
        self.assertEqual(blocked(result, "build-issue"), [("predecessor_incomplete:snippet",)])
        # The predecessor's PR carries a complete current closure: playback becomes buildable.
        proved = observed(build=[12], items={"snippet": {"issue": 11, "pr": 5, "head": HEAD}, "playback": {"issue": 12}})
        result = explain_actions(self.claims, POLICY, full_proof(), self.programme, proved)
        self.assertEqual([p.subject["item"] for p in result["proposals"]], ["playback"])

    def test_work_outside_the_current_programme_is_not_proposed_as_programme_work(self):
        result = explain_actions(self.claims, POLICY, {}, self.programme, observed(build=[13], issue_items={13: "ghost"}))
        self.assertEqual(blocked(result, "build-issue"), [("subject_outside_programme",)])
        unbound = explain_actions(self.claims, POLICY, {}, self.programme, observed(build=[14]))
        self.assertEqual(kinds(unbound), [("build-issue", {"issue": 14, "item": None})], "an accepted issue without a programme marker is still legacy work")

    def test_proposals_are_deterministic_and_carry_the_policy_and_input_identity(self):
        base = observed(review=[5, 3], rehead=[9], build=[7], issue_items={7: "snippet"})
        first = allowed_actions(self.claims, POLICY, {}, self.programme, base)
        again = allowed_actions(self.claims, POLICY, {}, self.programme, dict(base))
        self.assertEqual(first, again)
        self.assertEqual([p.kind for p in first], ["validate-pr", "validate-pr", "rehead-pr", "build-issue"])
        self.assertTrue(all(p.policy_digest == POLICY.sha256() for p in first))
        self.assertEqual(len({p.input_digest for p in first}), 1)
        self.assertTrue(all(isinstance(p, ActionProposal) for p in first))
        changed = allowed_actions(self.claims, POLICY, {}, self.programme, observed(review=[5]))
        self.assertNotEqual(changed[0].input_digest, first[0].input_digest)


class LegacyComparisonTests(unittest.TestCase):
    def legacy(self, **kwargs) -> dict:
        fields = dict(control_observed=True, stopped=False, fenced=False, reconciliation_required=False)
        fields.update(kwargs)
        return select_dispatch(DispatchObservation(**fields)).record()

    def test_agreement_and_each_disagreement_class_are_recorded_not_acted_on(self):
        review = ({"number": 5, "updatedAt": "1"}, {"number": 3, "updatedAt": "2"})
        shadow = explain_actions(None, POLICY, {}, None, observed(review=[5, 3], budget=True))
        agree = compare_legacy_frontier(shadow, self.legacy(review=review, budget=True))
        self.assertEqual((agree["classification"], agree["authority"]), ("agree", "shadow-record"))
        self.assertEqual(agree["legacy"], {"status": "ready", "action": "validate-pr", "subject": 5, "reason_codes": []})
        stricter = compare_legacy_frontier(explain_actions(None, POLICY, {}, None, observed(review=[5], leases={"pr:5": "run"})),
                                           self.legacy(review=review[:1], budget=True))
        self.assertEqual(stricter["classification"], "shadow_stricter")
        looser = compare_legacy_frontier(explain_actions(None, POLICY, {}, None, observed(review=[5], budget=True)),
                                         self.legacy(review=review[:1], budget=False))
        self.assertEqual(looser["classification"], "shadow_looser")
        idle = compare_legacy_frontier(explain_actions(None, POLICY, {}, None, observed()), self.legacy())
        self.assertEqual(idle["classification"], "agree")
        stopped = compare_legacy_frontier(explain_actions(None, POLICY, {}, None, observed(stopped=True, review=[5])),
                                          self.legacy(stopped=True, review=review[:1]))
        self.assertEqual(stopped["classification"], "agree")
        unseen = compare_legacy_frontier(explain_actions(None, POLICY, {}, None, observed(review=[3], budget=True)),
                                         self.legacy(review=review[:1], budget=True))
        self.assertEqual(unseen["classification"], "different_choice", "legacy chose a subject the shadow never saw")
        held = compare_legacy_frontier(explain_actions(None, POLICY, {}, None, observed(review=[5], rehead=[9], budget=None)),
                                       self.legacy(review=review[:1], rehead=(9,), budget=True))
        self.assertEqual(held["classification"], "shadow_stricter", "an unobserved allowance blocks the paid action legacy would start")
        budget = compare_legacy_frontier(explain_actions(None, POLICY, {}, None, observed(review=[5], budget=False)),
                                         self.legacy(review=review[:1], budget=False))
        self.assertEqual(budget["classification"], "agree")
        self.assertEqual(sha256_value(held["proposals"]), sha256_value([p.to_dict() for p in
                         explain_actions(None, POLICY, {}, None, observed(review=[5], rehead=[9], budget=None))["proposals"]]))


if __name__ == "__main__":
    unittest.main()
