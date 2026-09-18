"""Lesson admission (C10): a strict protected policy, the fixed gate order, three statuses,
unknown never admits, admission records that keep their lineage, and retrieval that needs
admitted AND current AND an eligible role."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from factory_kernel.canonical import sha256_value
from factory_kernel.lessons import (
    GATES,
    ORDER,
    LessonRefused,
    admit,
    evaluate,
    gate_results,
    load_policy,
    parse_policy,
    retrieval_eligible,
)

POLICY = {
    "schema": "dark-factory/lesson-admission-policy", "schema_version": "1.0", "policy_id": "citations-v1", "version": "1.0",
    "eligible_roles": ["implement", "repair"], "task_families": ["boundary-operator", "off-by-one"], "cohort_digest": "a" * 64,
    "baseline_method": "no-memory-challenger", "equal_total_cost_cap_microusd": 2_000_000, "minimum_family_coverage": 2,
    "minimum_samples": 12, "hard_regression_constraints": ["acceptance-regression", "security-regression"],
    "benefit_metric": "first-pass-green-rate", "minimum_meaningful_effect": 0.10, "uncertainty_rule": "exact-sign-test-p<=0.05",
    "maximum_confirmation_exposures": 3, "negative_transfer_limit": 0.05, "expiry_observations": 50, "drift_triggers": ["toolchain", "policy"],
}


def gates(**overrides) -> dict:
    fields = {gate: True for gate in GATES}
    fields["hard_regression"] = False
    fields.update(overrides)
    return fields


class PolicyTests(unittest.TestCase):
    def test_the_policy_loader_is_strict_and_never_invents_a_threshold(self):
        policy = parse_policy(POLICY)
        self.assertEqual((policy.policy_id, policy.minimum_samples, policy.sha256), ("citations-v1", 12, sha256_value(POLICY)))
        for name, mutate in (
            ("extra field", lambda r: r.update(bonus=1)),
            ("missing field", lambda r: r.pop("minimum_samples")),
            ("zero coverage", lambda r: r.update(minimum_family_coverage=0)),
            ("effect above one", lambda r: r.update(minimum_meaningful_effect=1.5)),
            ("bool effect", lambda r: r.update(minimum_meaningful_effect=True)),
            ("bad digest", lambda r: r.update(cohort_digest="x")),
            ("no roles", lambda r: r.update(eligible_roles=[])),
            ("other schema", lambda r: r.update(schema_version="2.0")),
        ):
            raw = json.loads(json.dumps(POLICY)); mutate(raw)
            with self.subTest(name), self.assertRaises(LessonRefused):
                parse_policy(raw)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            path.write_text(json.dumps(POLICY), encoding="utf-8")
            self.assertEqual(load_policy(path).sha256, policy.sha256)
            with self.assertRaises(LessonRefused):
                load_policy(Path(tmp) / "missing.json")


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.policy = parse_policy(POLICY)

    def test_every_gate_passing_admits_and_each_failing_gate_reports_its_own_class_in_order(self):
        self.assertEqual(evaluate(gates(), self.policy).status, "admit")
        expected = {"authenticated": ("reject", "evidence_unauthenticated"), "policy": ("insufficient", "policy_missing"),
                    "in_scope": ("reject", "outside_applicability"), "clean_split": ("insufficient", "cohort_contaminated"),
                    "adequate": ("insufficient", "coverage_insufficient"), "hard_regression": ("reject", "hard_regression"),
                    "benefit": ("reject", "benefit_not_met"), "uncertainty_supported": ("insufficient", "uncertainty_unsupported"),
                    "current": ("insufficient", "lesson_dormant")}
        for gate, (status, code) in expected.items():
            failing = gates(**{gate: not gates()[gate]})
            result = evaluate(failing, None if gate == "policy" else self.policy)
            self.assertEqual((result.status, result.reason_codes), (status, (code,)), gate)
        # The order is fixed: with several failures the earliest gate in ORDER decides.
        many = gates(authenticated=False, hard_regression=True, benefit=False, current=False)
        self.assertEqual(evaluate(many, self.policy).reason_codes, ("evidence_unauthenticated",))
        later = gates(hard_regression=True, benefit=False, current=False)
        self.assertEqual(evaluate(later, self.policy).reason_codes, ("hard_regression",))
        self.assertEqual([g for g, _, _ in ORDER], list(GATES))

    def test_unknown_never_admits(self):
        unknown = gates(); del unknown["adequate"]
        result = evaluate(unknown, self.policy)
        self.assertEqual((result.status, result.reason_codes, result.gates["adequate"]), ("insufficient", ("coverage_insufficient",), None))
        unknown = gates(); unknown["benefit"] = "yes"
        self.assertEqual(evaluate(unknown, self.policy).status, "insufficient", "a non-boolean gate is unknown, never a pass")
        self.assertEqual(evaluate(gates(), None).reason_codes, ("policy_missing",), "no policy: proposed lessons and offline reports only")

    def test_raw_observations_are_decided_by_the_policy_not_by_the_proposal(self):
        raw = {"authenticated": True, "role": "implement", "task_family": "boundary-operator", "cohort_digest": "a" * 64, "contaminated": False,
               "families_covered": 2, "samples": 12, "regressions": [], "negative_transfer": 0.01, "effect": 0.12,
               "total_cost_microusd": 1_500_000, "uncertainty_rule_met": True, "observations_since_admission": 3, "drift_events": [],
               "contradicted": False, "evidence_refs": ["att:1", "att:2"]}
        result = evaluate(raw, self.policy)
        self.assertEqual((result.status, result.evidence_refs, result.policy_sha256), ("admit", ("att:1", "att:2"), self.policy.sha256))
        self.assertEqual(evaluate({**raw, "role": "judge"}, self.policy).reason_codes, ("outside_applicability",))
        self.assertEqual(evaluate({**raw, "cohort_digest": "b" * 64}, self.policy).reason_codes, ("cohort_contaminated",))
        self.assertEqual(evaluate({**raw, "samples": 11}, self.policy).reason_codes, ("coverage_insufficient",))
        self.assertEqual(evaluate({**raw, "regressions": ["security-regression"]}, self.policy).reason_codes, ("hard_regression",))
        self.assertEqual(evaluate({**raw, "negative_transfer": 0.2}, self.policy).reason_codes, ("hard_regression",))
        self.assertEqual(evaluate({**raw, "effect": 0.05}, self.policy).reason_codes, ("benefit_not_met",))
        self.assertEqual(evaluate({**raw, "total_cost_microusd": 3_000_000}, self.policy).reason_codes, ("benefit_not_met",),
                         "benefit counts only under an equal total budget")
        self.assertEqual(evaluate({**raw, "uncertainty_rule_met": False}, self.policy).reason_codes, ("uncertainty_unsupported",))
        self.assertEqual(evaluate({**raw, "drift_events": ["toolchain"]}, self.policy).reason_codes, ("lesson_dormant",))
        self.assertEqual(evaluate({**raw, "contradicted": True}, self.policy).reason_codes, ("lesson_dormant",))
        self.assertEqual(evaluate({**raw, "observations_since_admission": 50}, self.policy).reason_codes, ("lesson_dormant",))
        missing = dict(raw); del missing["samples"]
        self.assertEqual(evaluate(missing, self.policy).reason_codes, ("coverage_insufficient",), "unknown sample sufficiency never admits")
        self.assertEqual(gate_results({}, None)["policy"], False)


class RecordTests(unittest.TestCase):
    def test_admission_records_keep_lineage_and_only_admit_admits(self):
        policy = parse_policy(POLICY)
        proposal = {"id": "lesson-boundary-1", "schema": "dark-factory/lesson-proposal", "status": "proposed", "hypothesis": "h"}
        admitted = admit(proposal, evaluate(gates(), policy), lineage=["lesson-boundary-0"])
        self.assertEqual((admitted["status"], admitted["retrieval_eligible"], admitted["lineage"]), ("admitted", True, ["lesson-boundary-0"]))
        self.assertEqual(admitted["record_sha256"], sha256_value({k: v for k, v in admitted.items() if k != "record_sha256"}))
        rejected = admit(proposal, evaluate(gates(hard_regression=True), policy))
        self.assertEqual((rejected["status"], rejected["retrieval_eligible"], rejected["evaluation"]["reason_codes"]), ("proposed", False, ["hard_regression"]))
        # Insufficient evidence is not a rejection, yet it keeps a proposal a proposal.
        insufficient = admit(proposal, evaluate(gates(), None))
        self.assertEqual((insufficient["status"], insufficient["retrieval_eligible"], insufficient["evaluation"]["status"]), ("proposed", False, "insufficient"))
        with self.assertRaises(LessonRefused):
            admit({"status": "proposed"}, evaluate(gates(), policy))
        # Retrieval: admitted AND current AND an eligible role; blind roles and missing policy get nothing.
        self.assertTrue(retrieval_eligible(admitted, role="implement", policy=policy, current=True))
        self.assertFalse(retrieval_eligible(admitted, role="judge", policy=policy, current=True))
        self.assertFalse(retrieval_eligible(admitted, role="implement", policy=policy, current=False))
        self.assertFalse(retrieval_eligible(admitted, role="implement", policy=None, current=True))
        self.assertFalse(retrieval_eligible(rejected, role="implement", policy=policy, current=True))


if __name__ == "__main__":
    unittest.main()
