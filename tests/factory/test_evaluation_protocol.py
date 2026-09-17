"""Frozen protocols and exact statistics (LINE_LEVEL_CLAIMS 3, C09).

Negative cases named by the specification: evaluator mutation (an evaluator outside the trusted
registry), tie exaggerated as superiority, timeout/crash excluded and reported, best-of-many
noise (sequential looks refused), retry resetting exposure, boolean measurements, alpha from
data. The exact sign test is checked against hand-computed binomial tails.
"""
from __future__ import annotations

import unittest

from factory_kernel.evaluation_protocol import (
    Evaluator, ProtocolRefused, assess_paired, freeze_protocol, paired_sign_test, rational_at_most, record_exposure,
    screen_finite, validate_observation,
)

CONTRACT = Evaluator("acceptance-green-v1", "1.0", "a" * 64, "frozen acceptance contract", ("python", "proof.py"))
BENCH = Evaluator("workload-runner-v1", "1.0", "b" * 64, "registered latency workload", ("python", "bench.py"))


class SignTestTests(unittest.TestCase):
    def test_exact_binomial_tail_ties_excluded_and_booleans_refused(self):
        self.assertEqual(paired_sign_test([1, 1, 1, 1, 1]), {"status": "computed", "reason_codes": [], "n": 5, "k": 5, "ties": 0, "numerator": 1, "denominator": 32})
        self.assertEqual(paired_sign_test([1, 1, 1, 1, -1])["numerator"], 6)
        self.assertEqual(paired_sign_test([-1] * 5)["numerator"], 32)
        result = paired_sign_test([1, 0, 1, 0, 1, 1, 1])
        self.assertEqual((result["n"], result["k"], result["ties"], result["numerator"]), (5, 5, 2, 1))
        self.assertEqual(paired_sign_test([0, 0]), {"status": "insufficient", "reason_codes": ["no_nonzero_pairs"], "ties": 2})
        self.assertEqual(paired_sign_test([True, 1]), {"status": "refused", "reason_codes": ["invalid_measurement"]})
        self.assertEqual(paired_sign_test([1.0, 1])["status"], "refused")
        self.assertTrue(rational_at_most(1, 32, 1, 20))
        self.assertFalse(rational_at_most(6, 32, 1, 20))


class FreezeTests(unittest.TestCase):
    def paired(self, **overrides):
        proposal = {"adapter": "paired-fixed-v1", "evaluators": [BENCH.authority_closure_digest],
                    "objective_vector": [{"name": "latency_ns", "unit": "ns", "direction": "minimise"}],
                    "practical_effect_thresholds": {"latency_ns": 1000}, "cost_and_resource_limits": {"runs": 10},
                    "statistic_id": "exact-paired-sign-v1", "alpha": [1, 20], "repetitions": 5}
        proposal.update(overrides)
        return freeze_protocol(proposal, [CONTRACT, BENCH])

    def test_evaluators_come_from_the_registry_and_thresholds_are_explicit(self):
        protocol = self.paired()
        self.assertEqual(protocol.evaluators[0].id, "workload-runner-v1")
        self.assertEqual(protocol.alpha, (1, 20))
        self.assertEqual(protocol.digest(), self.paired().digest(), "frozen identity is deterministic")
        self.assertNotEqual(protocol.digest(), self.paired(repetitions=6).digest(), "a changed protocol is another study")
        with self.assertRaisesRegex(ProtocolRefused, "outside the trusted registry"):
            self.paired(evaluators=["c" * 64])
        for bad in (dict(alpha=[1, 1]), dict(alpha=[0.05]), dict(alpha=None), dict(practical_effect_thresholds={}),
                    dict(statistic_id="t-test"), dict(repetitions=0), dict(repetitions=True), dict(maximum_looks=2),
                    dict(cost_and_resource_limits={}), dict(objective_vector=[{"name": "x", "unit": "ns"}]),
                    dict(objective_vector=[{"name": "x", "unit": "ns", "direction": "lower"}]), dict(adapter="vibes-v1")):
            with self.subTest(bad=bad), self.assertRaises(ProtocolRefused):
                self.paired(**bad)
        with self.assertRaisesRegex(ProtocolRefused, "deterministic"):
            freeze_protocol({"adapter": "finite-contract-v1", "evaluators": [CONTRACT.authority_closure_digest],
                             "cost_and_resource_limits": {"runs": 1}, "statistic_id": "exact-paired-sign-v1"}, [CONTRACT])
        with self.assertRaisesRegex(ProtocolRefused, "exposure"):
            self.paired(); freeze_protocol({"adapter": "finite-contract-v1", "evaluators": [CONTRACT.authority_closure_digest],
                                            "cost_and_resource_limits": {"runs": 1}}, [CONTRACT], exposure_state={"remaining_confirmations": 0})


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.protocol = freeze_protocol({"adapter": "finite-contract-v1", "evaluators": [CONTRACT.authority_closure_digest],
                                         "cost_and_resource_limits": {"runs": 4}}, [CONTRACT])

    def run_record(self, **overrides):
        raw = {"evaluator_id": "acceptance-green-v1", "protocol_digest": self.protocol.digest(), "status": "returned",
               "exit_code": 0, "duration_ns": 5, "output_sha256": "0" * 64, "metrics": {}}
        raw.update(overrides)
        return raw

    def test_timeout_and_crash_are_incomplete_never_numbers(self):
        ok = validate_observation(self.protocol, "B", self.run_record())
        self.assertEqual((ok.complete, ok.hard_pass), (True, True))
        timeout = validate_observation(self.protocol, "B", self.run_record(status="timeout", exit_code=None))
        self.assertEqual((timeout.complete, timeout.hard_pass, timeout.incomplete_reasons), (False, None, ("timeout",)))
        with self.assertRaisesRegex(ProtocolRefused, "outside the frozen protocol"):
            validate_observation(self.protocol, "B", self.run_record(evaluator_id="workload-runner-v1"))
        with self.assertRaisesRegex(ProtocolRefused, "different frozen protocol"):
            validate_observation(self.protocol, "B", self.run_record(protocol_digest="f" * 64))
        with self.assertRaises(ProtocolRefused):
            validate_observation(self.protocol, "B", self.run_record(exit_code="0"))
        with self.assertRaises(ProtocolRefused):
            validate_observation(self.protocol, "B", self.run_record(status="great"))


class ScreeningTests(unittest.TestCase):
    def row(self, cid, metric, hard=True, complete=True):
        return {"id": cid, "metric": metric, "hard_pass": hard, "complete": complete}

    def test_hard_constraints_first_then_lower_metric_ties_keep_baseline(self):
        self.assertEqual(screen_finite("A", [self.row("A", 10), self.row("B", 8)]).selected_candidate_id, "B")
        fast_wrong = screen_finite("A", [self.row("A", 10), self.row("B", 1, hard=False)])
        self.assertEqual((fast_wrong.outcome, fast_wrong.selected_candidate_id), ("provisional", "A"))
        self.assertEqual(fast_wrong.rejected_with_reasons, ({"id": "B", "reason": "hard_constraint_failed"},))
        tie = screen_finite("A", [self.row("A", 10), self.row("B", 10)])
        self.assertEqual((tie.outcome, tie.selected_candidate_id), ("tie", "A"))
        tie_without_baseline = screen_finite("A", [self.row("A", 10, hard=False), self.row("B", 3), self.row("C", 3)])
        self.assertEqual((tie_without_baseline.outcome, tie_without_baseline.selected_candidate_id), ("tie", None))
        self.assertEqual(screen_finite("A", [self.row("A", 10, hard=False)]).outcome, "no_eligible")
        self.assertEqual(screen_finite("A", [self.row("A", 10), self.row("B", None, complete=False)]).reason_codes, ("measurement_incomplete",))
        self.assertEqual(screen_finite("A", []).reason_codes, ("measurement_missing",))
        self.assertEqual(screen_finite("A", [self.row("A", True)]).outcome, "refused")
        self.assertEqual(screen_finite("A", [self.row("A", 1), self.row("A", 2)]).outcome, "refused")
        for result in (tie, fast_wrong):
            self.assertFalse(result.merge_authorized)
            self.assertFalse(result.to_dict()["merge_authorized"])


class PairedTests(unittest.TestCase):
    def setUp(self):
        self.protocol = freeze_protocol({"adapter": "paired-fixed-v1", "evaluators": [BENCH.authority_closure_digest],
                                         "objective_vector": [{"name": "latency_ns", "unit": "ns", "direction": "minimise"}],
                                         "practical_effect_thresholds": {"latency_ns": 100}, "cost_and_resource_limits": {"runs": 10},
                                         "statistic_id": "exact-paired-sign-v1", "alpha": [1, 20], "repetitions": 6}, [BENCH])

    def test_supported_needs_alpha_and_practical_threshold_and_never_claims_equivalence(self):
        supported = assess_paired(self.protocol, [150, 200, 120, 300, 110, 180], metric="latency_ns", candidate_id="B", baseline_id="A")
        self.assertEqual((supported.outcome, supported.selected_candidate_id), ("supported", "B"))
        self.assertEqual(supported.uncertainty["p_numerator"], 1)
        small = assess_paired(self.protocol, [5, 6, 4, 7, 5, 6], metric="latency_ns", candidate_id="B", baseline_id="A")
        self.assertEqual((small.outcome, small.reason_codes), ("inconclusive", ("below_practical_threshold",)))
        self.assertIn("not equivalence", " ".join(small.limitations))
        mixed = assess_paired(self.protocol, [150, -200, 120, -300, 110, 180], metric="latency_ns", candidate_id="B", baseline_id="A")
        self.assertEqual(mixed.outcome, "inconclusive")
        self.assertIn("not_significant", mixed.reason_codes)
        short = assess_paired(self.protocol, [150, 200], metric="latency_ns", candidate_id="B", baseline_id="A")
        self.assertEqual(short.reason_codes, ("scheduled_units_incomplete",))
        with self.assertRaises(ProtocolRefused):
            assess_paired(freeze_protocol({"adapter": "finite-contract-v1", "evaluators": [CONTRACT.authority_closure_digest],
                                           "cost_and_resource_limits": {"runs": 1}}, [CONTRACT]), [1], metric="x", candidate_id="B", baseline_id="A")


class ExposureTests(unittest.TestCase):
    def test_exposure_is_keyed_by_lineage_and_a_retry_does_not_reset_it(self):
        journal: dict = {}
        first = freeze_protocol({"adapter": "finite-contract-v1", "evaluators": [CONTRACT.authority_closure_digest],
                                 "cost_and_resource_limits": {"runs": 1}}, [CONTRACT])
        retry = freeze_protocol({"adapter": "finite-contract-v1", "evaluators": [CONTRACT.authority_closure_digest],
                                 "cost_and_resource_limits": {"runs": 2}}, [CONTRACT])
        self.assertNotEqual(first.digest(), retry.digest())
        receipt = record_exposure(first, "reserve", journal, lineage_key="family-x", maximum_confirmations=2)
        self.assertEqual((receipt.consumed, receipt.remaining_confirmations), (1, 1))
        record_exposure(first, "abandon", journal, lineage_key="family-x", maximum_confirmations=2)
        self.assertEqual(journal["family-x"]["consumed"], 1, "abandoned exposure stays consumed")
        record_exposure(retry, "reserve", journal, lineage_key="family-x", maximum_confirmations=2)
        with self.assertRaisesRegex(ProtocolRefused, "exhausted"):
            record_exposure(retry, "reserve", journal, lineage_key="family-x", maximum_confirmations=2)
        with self.assertRaisesRegex(ProtocolRefused, "without a prior reservation"):
            record_exposure(first, "reveal", {}, lineage_key="family-y", maximum_confirmations=2)
        with self.assertRaises(ProtocolRefused):
            record_exposure(first, "reserve", journal, lineage_key="family-x", maximum_confirmations=3)


if __name__ == "__main__":
    unittest.main()
