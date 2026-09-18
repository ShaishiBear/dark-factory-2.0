"""Calibration joins frozen forecasts with recorded outcomes and reports without deciding
(SPECIFICATION 11.1, C13, WP11): strict joins, one trajectory once, unknown stays unknown,
no invented actual, preregistered cohorts, exact arithmetic."""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from factory_kernel import calibration
from factory_kernel.calibration import (CalibrationRefused, binary_forecasts, brier_score, calibration_report, cohort_of,
                                        interval_metrics, join_outcomes, load_protocol, parse_protocol)
from factory_kernel.canonical import sha256_value
from factory_kernel.exploration_records import projection
from factory_kernel.lessons import gate_results, parse_policy
from factory_kernel.predictions import frozen_predictions
from tests.factory import test_exploration as exploration_fixture
from tests.factory import test_lessons as lessons_fixture
from tests.factory.test_frontdoor_intent import OWNER, REPO

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = ROOT / "harness" / "experiments" / "learning_protocol.json"


def protocol(**overrides) -> dict:
    raw = {"schema": "dark-factory/learning-protocol", "schema_version": "1.0", "protocol_id": "test-protocol", "version": "1",
           "cohorts": {"development": {"projects": ["citations"]}, "evaluation": {"projects": ["held-out"]}},
           "grouping": ["project", "task_family", "method_version", "model"], "note": "fixture configuration, not evidence"}
    raw.update(overrides)
    return raw


class CalibrationTests(unittest.TestCase):
    """The join runs over a real exploration ledger: a real IntentStore, the real Exploration engine
    and the real lookup-workload-v1 runner (deterministic, offline, no paid call)."""

    def setUp(self) -> None:
        self.fixture = exploration_fixture.ExplorationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store
        self.engine = self.fixture.engine

    def events(self) -> list:
        return self.store._read(self.store.directory / "citations.json")

    @staticmethod
    def event(events: list, kind: str) -> dict:
        """The first exploration event of one kind (other operations carry no `kind`)."""
        return next(e for e in events if e["command"]["operation"] == "preflight-event" and e["command"]["payload"]["kind"] == kind)

    def run_experiment(self) -> dict:
        self.fixture.add()
        return self.engine.experiment("citations", self.fixture.command(self.fixture.experiment_request()), principal=OWNER)

    def test_a_real_experiment_joins_each_measurement_to_the_forecast_frozen_before_it(self) -> None:
        state = self.run_experiment()
        observation = state["sessions"]["lookup"]["observations"][-1]
        join = join_outcomes(self.events(), project="citations")
        self.assertEqual(join["duplicates_dropped"], 0)
        self.assertEqual(join["unjoined"], [])
        self.assertEqual(join["unmeasured_candidates"], [])
        rows = {row["candidate_id"]: row for row in join["rows"]}
        self.assertEqual(set(rows), {"scan", "index"})
        for key, row in rows.items():
            outcome = next(o for o in observation["prediction_outcomes"] if o["subject"]["candidate_id"] == key)
            self.assertEqual(row["forecast"]["identity"], outcome["prediction_identity"])
            self.assertEqual(row["forecast"]["source"], "candidate-registration")
            self.assertEqual((row["outcome"], row["distance"]), (outcome["outcome"], outcome["distance"]))
            self.assertEqual(row["receipt_sha256"], observation["receipt_sha256"])
            self.assertEqual((row["method_version"], row["model"], row["status"]), ("lookup-workload-v1", "unrecorded", "complete"))
            self.assertEqual(row["cost_usd"], 0)  # the reservation made no paid call: cost known, zero
        self.assertEqual((rows["scan"]["task_family"], rows["index"]["task_family"]), ("linear", "hash"))
        # Both forecasts were outside their intervals: the report retains both negatives and says coverage 0/2.
        report = calibration_report({"citations": join}, protocol=parse_protocol(protocol()))
        self.assertEqual((report["samples"], report["authority"]), (2, "report-only"))
        group = next(g for g in report["groups"] if g["key"]["task_family"] == "linear")
        self.assertEqual((group["samples"], group["outcomes"]["outside"], group["interval"]["coverage"]), (1, 1, {"exact": "0/1", "decimal": 0.0}))
        self.assertEqual(group["interval"]["absolute_error"]["mean"], {"exact": f"{rows['scan']['distance']}/1", "decimal": float(rows["scan"]["distance"])})
        self.assertEqual(group["cost_usd"], {"known_samples": 1, "known_sum": 0.0, "unknown_count": 0})
        self.assertEqual(report["cohorts"]["development"]["samples"], 2)
        self.assertEqual(report["cohorts"]["development"]["families_covered"], 2)
        self.assertEqual(report["cohorts"]["evaluation"]["samples"], 0)
        self.assertFalse(report["contaminated"])
        self.assertEqual(report["binary_note"][:30], "the exploration ledger freezes")
        self.assertIsNone(group["binary"])

    def test_a_forecast_made_after_the_outcome_cannot_be_joined_to_it(self) -> None:
        self.run_experiment()
        events = self.events()
        # A post-hoc assessment (recorded after the observation) freezes new forecasts; an outcome
        # row that names one of them is a leak, and the join refuses it even though the final
        # projection knows the identity.
        self.engine.assess("citations", self.fixture.command({"candidates": {
            "scan": {"lookup": {"low": 1900, "high": 2100, "basis": "After seeing the number."}},
            "index": {"lookup": {"low": 15, "high": 25, "basis": "After seeing the number."}}}, "basis": "post hoc"}), principal=OWNER)
        later = self.events()
        post_hoc = {r["subject"]["candidate_id"]: r["identity"] for r in frozen_predictions(projection(later)["sessions"]["lookup"])}
        self.assertNotEqual(post_hoc["scan"], join_outcomes(events, project="citations")["rows"][0]["forecast"]["identity"])
        tampered = deepcopy(later)
        observed = self.event(tampered, "observed")
        for outcome in observed["command"]["payload"]["data"]["prediction_outcomes"]:
            if outcome["subject"]["candidate_id"] == "scan":
                outcome["prediction_identity"] = post_hoc["scan"]
                outcome["outcome"] = "within"  # the post-hoc interval would have covered the number
        join = join_outcomes(tampered, project="citations")
        self.assertEqual([row["candidate_id"] for row in join["rows"]], ["index"])
        self.assertEqual([(u["candidate_id"], u["reason"]) for u in join["unjoined"]], [("scan", "unjoined-forecast")])
        report = calibration_report({"citations": join}, protocol=parse_protocol(protocol()))
        self.assertEqual((report["samples"], len(report["unjoined_forecasts"])), (1, 1))
        # The honest join over the untampered later ledger still joins the registration forecasts.
        self.assertEqual({r["forecast"]["source"] for r in join_outcomes(later, project="citations")["rows"]}, {"candidate-registration"})

    def test_a_duplicated_observation_counts_once(self) -> None:
        self.run_experiment()
        events = self.events()
        observed = self.event(events, "observed")
        duplicated = events + [deepcopy(observed)]
        join = join_outcomes(duplicated, project="citations")
        self.assertEqual((len(join["rows"]), join["duplicates_dropped"]), (2, 2))
        self.assertEqual(calibration_report({"citations": join}, protocol=parse_protocol(protocol()))["samples"], 2)

    def test_missing_cost_stays_unknown_and_never_becomes_zero(self) -> None:
        self.run_experiment()
        events = self.events()
        reserved = self.event(events, "reserved")
        paid = deepcopy(events)
        # The same experiment, had it made a paid call whose cost was never reported.
        self.event(paid, "reserved")["command"]["payload"]["data"]["calls"] = 1
        self.assertEqual(reserved["command"]["payload"]["data"]["usd"], 0)
        join = join_outcomes(paid, project="citations")
        self.assertEqual({row["cost_usd"] for row in join["rows"]}, {None})
        group = calibration_report({"citations": join}, protocol=parse_protocol(protocol()))["groups"][0]
        self.assertEqual(group["cost_usd"], {"known_samples": 0, "known_sum": 0.0, "unknown_count": 1})
        reported = deepcopy(paid)
        self.event(reported, "observed")["command"]["payload"]["data"]["reported_usd"] = 0.25
        self.assertEqual({row["cost_usd"] for row in join_outcomes(reported, project="citations")["rows"]}, {0.25})

    def test_an_unmeasured_candidate_has_no_invented_actual(self) -> None:
        self.fixture.add()
        self.engine.add_candidates("citations", self.fixture.command({
            "claims": [exploration_fixture.claim("tree-assumption")],
            "candidates": [exploration_fixture.candidate("tree", "hash", 30, 70, ["tree-assumption"])]}), principal=OWNER)
        self.engine.experiment("citations", self.fixture.command(self.fixture.experiment_request()), principal=OWNER)
        join = join_outcomes(self.events(), project="citations")
        self.assertEqual({row["candidate_id"] for row in join["rows"]}, {"scan", "index"})
        self.assertEqual(join["unmeasured_candidates"], [{"session_id": "lookup", "candidate_id": "tree"}])
        report = calibration_report({"citations": join}, protocol=parse_protocol(protocol()))
        self.assertEqual(report["unmeasured_candidates"], [{"session_id": "lookup", "candidate_id": "tree", "project": "citations"}])
        self.assertEqual(report["samples"], 2)

    def test_a_failed_experiment_is_retained_as_a_failure_with_no_sample(self) -> None:
        self.fixture.add()
        with patch("factory_kernel.exploration.execute_registered", side_effect=RuntimeError("runner crashed")):
            self.engine.experiment("citations", self.fixture.command(self.fixture.experiment_request()), principal=OWNER)
        join = join_outcomes(self.events(), project="citations")
        self.assertEqual(join["rows"], [])
        self.assertEqual([(f["status"], f["failure"]) for f in join["failed_observations"]], [("failed", "RuntimeError")])
        self.assertEqual(sorted(u["candidate_id"] for u in join["unmeasured_candidates"]), ["index", "scan"])
        report = calibration_report({"citations": join}, protocol=parse_protocol(protocol()))
        self.assertEqual((report["samples"], len(report["failed_observations"])), (0, 1))

    def test_the_same_trajectory_in_two_cohorts_is_contamination_and_the_policy_decides_coverage(self) -> None:
        self.run_experiment()
        join = join_outcomes(self.events(), project="citations")
        # A session assigned to the evaluation cohort while its project is the development cohort
        # is refused by the protocol itself.
        for order in ({"development": {"projects": ["citations"]}, "evaluation": {"sessions": [["citations", "lookup"]]}},
                      {"evaluation": {"sessions": [["citations", "lookup"]]}, "development": {"projects": ["citations"]}}):
            with self.subTest(list(order)), self.assertRaises(CalibrationRefused):
                parse_protocol(protocol(cohorts=order))  # whichever cohort is written first
        # The same receipt under two cohorts (two projects holding copies of one trajectory).
        copy = deepcopy(join)
        contaminated = calibration_report({"citations": join, "held-out": copy}, protocol=parse_protocol(protocol()))
        self.assertTrue(contaminated["contaminated"])
        self.assertEqual(contaminated["admission_observations"]["contaminated"], True)
        clean = calibration_report({"citations": join}, protocol=parse_protocol(protocol()))
        self.assertFalse(clean["contaminated"])
        # The report's coverage observations are decided by the protected policy, not by the report:
        # the evaluation cohort is empty here, so a policy that requires one family and one sample
        # says the split is clean but the coverage inadequate; the report itself admits nothing.
        policy = parse_policy({**lessons_fixture.POLICY, "cohort_digest": clean["protocol"]["cohort_digest"],
                               "minimum_family_coverage": 1, "minimum_samples": 1})
        gates = gate_results(clean["admission_observations"], policy)
        self.assertEqual((gates["clean_split"], gates["adequate"]), (True, False))
        self.assertEqual(gate_results(contaminated["admission_observations"], policy)["clean_split"], False)
        self.assertNotIn("admit", json.dumps(clean["admission_observations"]))
        self.assertEqual((clean["refusals"], clean["repairs"]), ("not-recorded-in-the-exploration-ledger",) * 2)

    def test_unassigned_sessions_are_reported_not_scored_in_a_cohort(self) -> None:
        self.run_experiment()
        join = join_outcomes(self.events(), project="citations")
        report = calibration_report({"citations": join}, protocol=parse_protocol(protocol(cohorts={"evaluation": {"projects": ["held-out"]}})))
        self.assertEqual(report["unassigned_sessions"], [{"project": "citations", "session_id": "lookup"}])
        self.assertEqual(report["samples"], 2)  # still grouped and retained
        self.assertEqual(report["cohorts"]["evaluation"]["samples"], 0)
        self.assertIsNone(cohort_of(parse_protocol(protocol(cohorts={"evaluation": {"projects": ["held-out"]}})), "citations", "lookup"))

    def test_the_arithmetic_is_exact_and_refuses_impossible_inputs(self) -> None:
        self.assertIsNone(brier_score([]))
        self.assertEqual(brier_score([(Fraction(1, 2), 1), (Fraction(1, 2), 0)]), {"samples": 2, "brier": {"exact": "1/4", "decimal": 0.25}})
        self.assertAlmostEqual(brier_score([(0.9, 1), (0.1, 0)])["brier"]["decimal"], 0.01)
        self.assertEqual(brier_score([(Fraction(9, 10), 1), (Fraction(1, 10), 0)])["brier"]["exact"], "1/100")
        for bad in ([(1.5, 1)], [(-0.1, 0)], [(0.5, 2)], [(0.5, True)], [(True, 1)]):
            with self.subTest(bad), self.assertRaises(CalibrationRefused):
                brier_score(bad)
        rows = [{"outcome": "within", "distance": 0}, {"outcome": "outside", "distance": 30}, {"outcome": "unknown"},
                {"outcome": "not-comparable"}, {"outcome": "inapplicable"}]
        metrics = interval_metrics(rows)
        self.assertEqual((metrics["compared"], metrics["coverage"]), (2, {"exact": "1/2", "decimal": 0.5}))
        self.assertEqual(metrics["absolute_error"], {"samples": 2, "mean": {"exact": "15/1", "decimal": 15.0}, "max": 30.0})
        self.assertEqual(interval_metrics([{"outcome": "unknown"}])["coverage"], None)
        self.assertEqual(binary_forecasts([{"forecast": {"kind": "interval"}, "actual": {"value": 1}}]), [])
        self.assertEqual(binary_forecasts([{"forecast": {"kind": "probability", "probability": 0.7}, "actual": {"value": 1}}]), [(0.7, 1)])

    def test_the_protocol_loader_is_strict_and_the_repository_protocol_declares_an_empty_evaluation_cohort(self) -> None:
        loaded = load_protocol(PROTOCOL_PATH)
        self.assertEqual(loaded["cohorts"]["evaluation"], {"projects": []})
        self.assertEqual(loaded["cohorts"]["development"], {"projects": ["citations"]})
        self.assertEqual(loaded["cohort_digest"], sha256_value({"cohorts": loaded["cohorts"], "grouping": loaded["grouping"],
                                                                "protocol_id": loaded["protocol_id"], "version": loaded["version"]}))
        for bad in (protocol(extra=1), protocol(schema="other"), protocol(grouping=[]), protocol(grouping=["model", "model"]),
                    protocol(grouping=["criterion"]), protocol(cohorts={}), protocol(cohorts={"a": {"projects": ["p"]}, "b": {"projects": ["p"]}}),
                    protocol(cohorts={"a": {"sessions": [["p", "s"]]}, "b": {"sessions": [["p", "s"]]}}),
                    protocol(cohorts={"a": {"projects": ["p"], "when": "now"}}), protocol(cohorts={"a": {"sessions": ["p/s"]}}),
                    protocol(note=""), "not a mapping"):
            with self.subTest(str(bad)[:60]), self.assertRaises(CalibrationRefused):
                parse_protocol(bad)
        with self.assertRaises(CalibrationRefused):
            load_protocol(PROTOCOL_PATH.with_name("missing.json"))

    def test_the_command_line_reports_over_the_real_store_and_refuses_what_it_cannot_read(self) -> None:
        self.run_experiment()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "protocol.json"
            path.write_text(json.dumps(protocol()), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with patch.object(sys, "stdout", out), patch.object(sys, "stderr", err):
                code = calibration.main(["--store", str(self.store.directory), "--repository", REPO, "--project", "citations",
                                         "--protocol", str(path)])
            self.assertEqual((code, err.getvalue()), (0, ""))
            report = json.loads(out.getvalue())
            self.assertEqual((report["schema"], report["samples"], report["authority"]), ("dark-factory/calibration-report", 2, "report-only"))
            with patch.object(sys, "stdout", io.StringIO()) as md, patch.object(sys, "stderr", io.StringIO()):
                self.assertEqual(calibration.main(["--store", str(self.store.directory), "--repository", REPO, "--project", "citations",
                                                   "--protocol", str(path), "--format", "markdown"]), 0)
            self.assertIn("coverage 0/1", md.getvalue())
            # Wrong repository: the store's event chain does not verify; nothing is reported.
            with patch.object(sys, "stdout", io.StringIO()) as quiet, patch.object(sys, "stderr", io.StringIO()) as refused:
                self.assertEqual(calibration.main(["--store", str(self.store.directory), "--repository", "other/repo", "--project", "citations",
                                                   "--protocol", str(path)]), 2)
            self.assertEqual(quiet.getvalue(), "")
            self.assertTrue(refused.getvalue().startswith("CALIBRATION_REFUSED:"))
            with patch.object(sys, "stderr", io.StringIO()):
                self.assertEqual(calibration.main(["--store", str(Path(tmp) / "nowhere"), "--repository", REPO, "--project", "citations",
                                                   "--protocol", str(path)]), 2)
            self.assertFalse((Path(tmp) / "nowhere").exists())  # the report never creates a store


if __name__ == "__main__":
    unittest.main()
