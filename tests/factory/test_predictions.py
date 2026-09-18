"""Predictions frozen before results (SPECIFICATION 8.3): exact subject, measurement contract,
data cutoff, and outcomes that say only what can be said."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.frontdoor_intent import IntentRefused  # noqa: E402
from factory_kernel.predictions import OUTCOMES, SCHEMA, freeze_prediction, frozen_predictions, match_actual, outcomes_for  # noqa: E402

CTX = "c" * 64
OTHER = "d" * 64
CRITERION = {"id": "lookup", "question": "q", "kind": "measurement", "unit": "comparisons", "ceiling": 1000}
JUDGMENT = {"id": "fit", "question": "q", "kind": "judgment", "unit": "rating", "ceiling": None}


def session(**overrides) -> dict:
    value = {"id": "lookup", "round": 1, "context": {"identity": CTX},
             "policy": {"criteria": [CRITERION, JUDGMENT], "priorities": ["lookup", "fit"]},
             "candidates": {"scan": {"predictions": {"lookup": {"low": 10, "high": 20, "basis": "estimate"},
                                                     "fit": {"assessment": "favourable", "basis": "reasoning"}},
                                     "predictions_context_identity": CTX},
                            "index": {"predictions": {"lookup": {"low": 40, "high": 60, "basis": "estimate"},
                                                      "fit": {"assessment": "mixed", "basis": "reasoning"}},
                                      "predictions_context_identity": CTX}},
             "assessments": []}
    value.update(overrides)
    return value


class FreezeTests(unittest.TestCase):
    def test_a_frozen_prediction_carries_subject_contract_cutoff_source_and_says_it_is_uncalibrated(self) -> None:
        record = freeze_prediction(session_id="lookup", round_number=1, candidate_id="scan", criterion=CRITERION,
                                   prediction={"low": 10, "high": 20, "basis": "estimate"}, context_identity=CTX,
                                   source="candidate-registration")
        self.assertEqual(record["schema"], SCHEMA)
        self.assertEqual(record["subject"], {"session_id": "lookup", "candidate_id": "scan", "criterion_id": "lookup"})
        self.assertEqual((record["kind"], record["low"], record["high"], record["measurement_contract"]["unit"]), ("interval", 10, 20, "comparisons"))
        self.assertEqual((record["data_cutoff"], record["source"], record["calibration"], record["authority"]),
                         (CTX, "candidate-registration", "uncalibrated-interval", "forecast-record-only"))
        again = freeze_prediction(session_id="lookup", round_number=1, candidate_id="scan", criterion=CRITERION,
                                  prediction={"low": 10, "high": 20, "basis": "estimate"}, context_identity=CTX,
                                  source="candidate-registration")
        self.assertEqual(record["identity"], again["identity"])
        judged = freeze_prediction(session_id="lookup", round_number=1, candidate_id="scan", criterion=JUDGMENT,
                                   prediction={"assessment": "favourable", "basis": "reasoning"}, context_identity=CTX,
                                   source="round-assessment")
        self.assertEqual((judged["kind"], judged["assessment"], judged["calibration"]), ("judgment", "favourable", "uncalibrated-judgment"))
        for bad in ({"basis": "x"}, "not a mapping"):
            with self.subTest(bad), self.assertRaises(IntentRefused):
                freeze_prediction(session_id="s", round_number=1, candidate_id="c", criterion=CRITERION, prediction=bad,
                                  context_identity=CTX, source="candidate-registration")
        with self.assertRaises(IntentRefused):
            freeze_prediction(session_id="s", round_number=1, candidate_id="c", criterion=CRITERION,
                              prediction={"low": 1, "high": 2, "basis": ""}, context_identity=CTX, source="model-opinion")

    def test_the_prediction_in_force_is_the_rounds_assessment_else_the_registration(self) -> None:
        records = {(r["subject"]["candidate_id"], r["subject"]["criterion_id"]): r for r in frozen_predictions(session())}
        self.assertEqual(set(records), {("scan", "lookup"), ("scan", "fit"), ("index", "lookup"), ("index", "fit")})
        self.assertEqual((records[("scan", "lookup")]["source"], records[("scan", "lookup")]["low"]), ("candidate-registration", 10))
        revised = session(assessments=[{"round": 1, "candidates": {"scan": {"lookup": {"low": 100, "high": 200, "basis": "revised"},
                                                                             "fit": {"assessment": "adverse", "basis": "revised"}}}}])
        records = {(r["subject"]["candidate_id"], r["subject"]["criterion_id"]): r for r in frozen_predictions(revised)}
        self.assertEqual((records[("scan", "lookup")]["source"], records[("scan", "lookup")]["low"]), ("round-assessment", 100))
        self.assertEqual(records[("index", "lookup")]["source"], "candidate-registration")
        old_round = session(assessments=[{"round": 0, "candidates": {"scan": {"lookup": {"low": 1, "high": 2, "basis": "old"}}}}])
        self.assertEqual(frozen_predictions(old_round)[0]["low"], 10)  # an earlier round's assessment is not in force

    def test_a_registration_made_under_another_context_is_inapplicable(self) -> None:
        moved = session(context={"identity": OTHER})
        records = frozen_predictions(moved)
        self.assertTrue(all(not r["applicability"]["applicable"] for r in records))
        self.assertEqual(records[0]["applicability"]["reason"], "repository-context-changed-since-the-forecast")
        # The record keeps the cutoff the forecast was made under, not the context it is stale against,
        # so its identity is the same record as before the context moved, and match_actual's own
        # context comparison is what makes it inapplicable.
        self.assertEqual((records[0]["data_cutoff"], records[0]["applicability"]["context_identity"]), (CTX, CTX))
        before = {r["subject"]["criterion_id"]: r["identity"] for r in frozen_predictions(session()) if r["subject"]["candidate_id"] == "scan"}
        after = {r["subject"]["criterion_id"]: r["identity"] for r in records if r["subject"]["candidate_id"] == "scan"}
        self.assertNotEqual(before, after)  # applicability is part of the record
        self.assertEqual(match_actual({**records[0], "applicability": {**records[0]["applicability"], "applicable": True}},
                                      value=15, unit="comparisons", context_identity=OTHER, receipt_sha256="r" * 64)["outcome"], "inapplicable")
        revised = session(context={"identity": OTHER}, assessments=[{"round": 1, "candidates": {"scan": {"lookup": {"low": 5, "high": 6, "basis": "now"}}}}])
        scan = [r for r in frozen_predictions(revised) if r["subject"]["candidate_id"] == "scan"][0]
        self.assertTrue(scan["applicability"]["applicable"])  # re-assessed under the current context


class MatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.record = freeze_prediction(session_id="lookup", round_number=1, candidate_id="scan", criterion=CRITERION,
                                        prediction={"low": 10, "high": 20, "basis": "estimate"}, context_identity=CTX,
                                        source="candidate-registration")

    def test_outcomes_say_only_what_can_be_said(self) -> None:
        cases = {
            "within": dict(value=15, unit="comparisons", context_identity=CTX),
            "outside": dict(value=2000, unit="comparisons", context_identity=CTX),
            "not-comparable": dict(value=15, unit="build_items", context_identity=CTX),
            "inapplicable": dict(value=15, unit="comparisons", context_identity=OTHER),
            "unknown": dict(value=None, unit="comparisons", context_identity=CTX),
        }
        for expected, kwargs in cases.items():
            with self.subTest(expected):
                outcome = match_actual(self.record, receipt_sha256="r" * 64, **kwargs)
                self.assertEqual(outcome["outcome"], expected)
                self.assertIn(outcome["outcome"], OUTCOMES)
                self.assertEqual(outcome["prediction_identity"], self.record["identity"])
        self.assertEqual(match_actual(self.record, value=2000, unit="comparisons", context_identity=CTX, receipt_sha256="r" * 64)["distance"], 1980)
        self.assertEqual(match_actual(self.record, value=10, unit="comparisons", context_identity=CTX, receipt_sha256="r" * 64)["outcome"], "within")
        judged = freeze_prediction(session_id="lookup", round_number=1, candidate_id="scan", criterion=JUDGMENT,
                                   prediction={"assessment": "favourable", "basis": "r"}, context_identity=CTX, source="candidate-registration")
        self.assertEqual(match_actual(judged, value=1, unit="rating", context_identity=CTX, receipt_sha256="r" * 64)["outcome"], "not-comparable")
        self.assertEqual(match_actual(self.record, value=True, unit="comparisons", context_identity=CTX, receipt_sha256="r" * 64)["outcome"], "not-comparable")
        with self.assertRaises(IntentRefused):
            match_actual({"schema": "other"}, value=1, unit="comparisons", context_identity=CTX, receipt_sha256="r" * 64)

    def test_an_observation_is_matched_measurement_by_measurement(self) -> None:
        measurements = [{"candidate_id": "scan", "criterion_id": "lookup", "metric": "comparisons", "falsifies_claim": None, "value": 2000},
                        {"candidate_id": "index", "criterion_id": "lookup", "metric": "comparisons", "falsifies_claim": None, "value": 50},
                        {"candidate_id": "ghost", "criterion_id": "lookup", "metric": "comparisons", "falsifies_claim": None, "value": 1}]
        outcomes = outcomes_for(session(), measurements=measurements, context_identity=CTX, receipt_sha256="r" * 64)
        self.assertEqual([o["outcome"] for o in outcomes], ["outside", "within", "unknown"])
        self.assertIsNone(outcomes[2]["prediction_identity"])
        self.assertEqual(outcomes[0]["actual"]["receipt_sha256"], "r" * 64)
        stale = outcomes_for(session(context={"identity": OTHER}), measurements=measurements[:1], context_identity=OTHER, receipt_sha256="r" * 64)
        self.assertEqual(stale[0]["outcome"], "inapplicable")


if __name__ == "__main__":
    unittest.main()
