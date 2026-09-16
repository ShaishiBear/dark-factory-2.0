"""A verified policy counterexample changes only the preregistered causal frontier."""
import base64
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from factory_kernel.canonical import canonical_bytes
from factory_kernel.exploration_records import projection
from factory_kernel.feedback_observation import observe_feedback
from factory_kernel.frontdoor_intent import IntentRefused
from factory_kernel.programme import compile_programme
from factory_kernel.strategy_findings import PROGRAMS, POLICY, establish
from factory_kernel.strategy_rejection import StrategyRejection
from factory_kernel.strategy_rules import KIND, register, validate_rules
from tests.factory import test_exploration as exploration
from tests.factory import test_factory_feedback as feedback
from tests.factory.test_exploration_repository import blob_oid
from tests.factory.test_frontdoor_intent import OWNER, WORKER

ROOT = Path(__file__).resolve().parents[2]


def rule(**changes):
    return {"id": "edge-permission", "kind": KIND, "claim_id": "scan-assumption",
            "from_layer": "routes", "to_layer": "storage", **changes}


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.case = exploration.ExplorationTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.add()

    def register(self, **changes):
        return register(self.case.engine, "citations", self.case.command({"rules": [rule(**changes)]}), principal=OWNER)

    def test_rules_freeze_once_before_selection_and_bind_programme_identity(self):
        state = self.register()
        self.assertEqual(state["sessions"]["lookup"]["rejection_rules"], [rule()])
        with self.assertRaisesRegex(IntentRefused, "once, before"):
            self.register()
        self.case.recommend()
        self.case.engine.handoff("citations", self.case.command({"proposal": self.case.fixture.request["proposal"]}), principal=OWNER)
        prepared = self.case.engine.prepare_handoff("citations", "lookup",
            expected_project_version=self.case.command({})["expected_project_version"], principal=OWNER, include_strategy=True)
        with_rules = compile_programme(prepared["input"], repository=self.case.store.repository)
        self.assertEqual(with_rules.strategy["rejection_rules"], [rule()])
        del prepared["input"]["strategy"]["rejection_rules"]
        self.assertNotEqual(with_rules.sha256, compile_programme(prepared["input"], repository=self.case.store.repository).sha256)

    def test_late_foreign_unsupported_and_proposal_worker_rules_refuse(self):
        for changes in ({"claim_id": "foreign"}, {"kind": "trust-failure-prose"}, {"to_layer": "routes"}):
            with self.subTest(changes=changes), self.assertRaises(IntentRefused):
                self.register(**changes)
        with self.assertRaises(IntentRefused):
            register(self.case.engine, "citations", self.case.command({"rules": [rule()]}), principal=WORKER)
        self.case.recommend()
        with self.assertRaisesRegex(IntentRefused, "before selection"):
            self.register()

    def test_bounded_distinct_active_assumptions_only(self):
        state = self.case.engine.records.read("citations", OWNER)[0]
        for rows in ([], [rule()] * 2, [rule(id="rule" + str(i)) for i in range(9)],
                     [{**rule(), "verdict": "contradicted"}]):
            with self.subTest(rows=rows), self.assertRaises(IntentRefused):
                validate_rules(rows, state["claims"])
        state["claims"]["scan-assumption"]["status"] = "invalidated"
        with self.assertRaises(IntentRefused):
            validate_rules([rule()], state["claims"])


class RejectionTests(unittest.TestCase):
    def setUp(self):
        self.case = feedback.FeedbackTests()
        recommend = exploration.ExplorationTests.recommend
        def registered(case, *args, **kwargs):
            register(case.engine, "citations", case.command({"rules": [rule()]}), principal=OWNER)
            return recommend(case, *args, **kwargs)
        with patch.object(exploration.ExplorationTests, "recommend", registered):
            self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.engine = self.case.engine
        self.policy = {"version": "1.0", "graph": {"enforce_new_forbidden_edges": True}, "layers": [
            {"id": "routes", "allowed_imports": []}, {"id": "storage", "allowed_imports": []}]}
        self.case.responses[""] = {"full_name": feedback.REPO, "private": False, "default_branch": "main"}
        self.tree = self.case.responses[f"git/trees/{feedback.BASE}?recursive=1"]
        self.tree["sha"] = "d" * 40
        self.case.responses["git/trees/" + "d" * 40 + "?recursive=1"] = self.tree
        self.case.responses["branches/main"]["commit"]["commit"] = {"tree": {"sha": "d" * 40}}
        for name in PROGRAMS:
            self.put(name, (ROOT / name).read_bytes())
        self.put(POLICY, canonical_bytes(self.policy))
        self.case.github.json.side_effect = lambda args, **kwargs: deepcopy(
            self.case.responses[args[1].removeprefix("repos/" + feedback.REPO).lstrip("/")])
        patcher = patch("factory_kernel.strategy_rejection.observe_feedback", side_effect=lambda github, request:
            observe_feedback(github, request, download=self.case.download, now=feedback.NOW))
        patcher.start()
        self.addCleanup(patcher.stop)

    def put(self, name, raw):
        oid = blob_oid(raw)
        self.tree["tree"] = [row for row in self.tree["tree"] if row["path"] != name]
        self.tree["tree"].append({"path": name, "mode": "100644", "type": "blob", "sha": oid, "size": len(raw)})
        self.case.responses["git/blobs/" + oid] = {"sha": oid, "size": len(raw), "encoding": "base64",
            "content": base64.b64encode(raw).decode()}

    def state(self):
        return self.engine.records.read("citations", OWNER)[0]

    def test_import_automatically_rejects_only_the_contradicted_frontier_and_retains_b(self):
        self.case.fixture.open("unrelated")
        self.case.fixture.add("unrelated", "-other")
        self.case.fixture.recommend("unrelated")
        before = self.state()
        result = self.case.import_outcome()
        after = self.state()
        assessment = result["assessment"]
        self.assertEqual(assessment["decision"], "reconsider")
        self.assertEqual(assessment["invalidated_claim_ids"], ["scan-assumption"])
        self.assertEqual(assessment["failure_cause"], "unresolved")
        self.assertEqual(assessment["qualification_status"], "UNPROVEN")
        self.assertFalse(assessment["proof_reuse_allowed"])
        self.assertEqual(after["sessions"]["lookup"]["status"], "reconsideration-required")
        self.assertEqual(after["claims"]["index-assumption"]["status"], "active")
        self.assertEqual(after["sessions"]["unrelated"], before["sessions"]["unrelated"])
        self.assertEqual(after["budgets"], before["budgets"])
        self.engine.reopen("citations", self.case.fixture.command({"reason": "Registered assumption contradicted."}), principal=OWNER)
        # Retained B requires current review and a wholly new qualification; no receipt is transferred.
        self.case.fixture.recommend()
        self.assertEqual(self.state()["sessions"]["lookup"]["recommendations"][-1]["candidate_id"], "index")

    def test_implementation_refusal_with_supported_assumption_does_not_reject_strategy(self):
        self.policy["layers"][0]["allowed_imports"] = ["storage"]
        self.put(POLICY, canonical_bytes(self.policy))
        before = self.state()
        result = self.case.import_outcome()
        self.assertEqual(result["assessment"]["decision"], "no-contradiction-established")
        for key in ("sessions", "claims", "budgets"):
            self.assertEqual(self.state()[key], before[key])

    def test_unknown_layer_malformed_policy_and_unavailable_authority_stay_unresolved(self):
        for defect in ("unknown-layer", "invalid-policy", "stale-authority", "disabled-rule"):
            policy = deepcopy(self.policy)
            if defect == "unknown-layer":
                policy["layers"].pop()
            elif defect == "invalid-policy":
                policy["layers"].append(deepcopy(policy["layers"][0]))
            elif defect == "disabled-rule":
                policy["graph"]["enforce_new_forbidden_edges"] = False
            self.put(POLICY, canonical_bytes(policy))
            self.put(PROGRAMS[0], b"# different authority\n" if defect == "stale-authority" else (ROOT / PROGRAMS[0]).read_bytes())
            with self.subTest(defect=defect):
                try:
                    report = establish(self.case.github, [rule()], self.state()["claims"], expected_revision=feedback.BASE, check_stop=lambda: None)
                except IntentRefused:
                    continue
                self.assertEqual(report["findings"][0]["status"], "unresolved")

    def test_missing_evidence_can_be_explicitly_recovered_without_reimporting_or_spend(self):
        self.put(PROGRAMS[0], b"# unavailable authority\n")
        result = self.case.import_outcome()
        self.assertEqual(result["assessment"]["decision"], "unresolved")
        self.assertEqual(self.state()["claims"]["scan-assumption"]["status"], "active")
        before = self.state()["budgets"]
        self.put(PROGRAMS[0], (ROOT / PROGRAMS[0]).read_bytes())
        command = self.case.fixture.command({"outcome_id": result["outcome"]["id"]})
        service = StrategyRejection(self.engine, self.case.github)
        assessed = service.assess("citations", command, principal=OWNER)
        self.assertEqual(assessed["assessment"]["decision"], "reconsider")
        self.assertEqual(assessed, service.assess("citations", command, principal=OWNER))
        self.assertEqual(len(self.state()["factory_outcomes"]), 1)
        self.assertEqual(self.state()["budgets"], before)

    def test_crash_between_import_and_assessment_is_visible_and_recoverable(self):
        with patch.object(StrategyRejection, "assess", side_effect=OSError("interrupted")):
            result = self.case.import_outcome()
        self.assertEqual(result["assessment_status"], "pending-explicit-recovery")
        self.assertEqual(len(self.state()["factory_outcomes"]), 1)
        self.assertEqual(self.state()["strategy_assessments"], {})
        service = StrategyRejection(self.engine, self.case.github)
        assessed = service.assess("citations", self.case.fixture.command({"outcome_id": result["outcome"]["id"]}), principal=OWNER)
        self.assertEqual(assessed["assessment"]["decision"], "reconsider")

    def test_issuer_and_source_currency_rechecked_before_assessment(self):
        with patch.object(StrategyRejection, "assess", side_effect=OSError("interrupted")):
            result = self.case.import_outcome()
        self.case.responses["pulls/17"]["head"]["sha"] = "f" * 40
        with self.assertRaises(IntentRefused):
            StrategyRejection(self.engine, self.case.github).assess("citations",
                self.case.fixture.command({"outcome_id": result["outcome"]["id"]}), principal=OWNER)
        self.assertEqual(self.state()["strategy_assessments"], {})

    def test_replay_never_invalidates_twice_and_forced_verdict_is_not_input(self):
        command = self.case.fixture.command(self.case.request)
        first = self.case.import_outcome(command)
        second = self.case.import_outcome(command)
        self.assertEqual(first["assessment"], second["assessment"])
        self.assertEqual(len(self.state()["strategy_assessments"]), 1)
        with self.assertRaises(IntentRefused):
            StrategyRejection(self.engine, self.case.github).assess("citations", self.case.fixture.command({
                "outcome_id": first["outcome"]["id"], "decision": "reconsider"}), principal=OWNER)

    def test_fact_revision_programme_or_stop_changes_cannot_invalidate(self):
        with self.assertRaises(IntentRefused):
            establish(self.case.github, [rule()], self.state()["claims"], expected_revision="f" * 40, check_stop=lambda: None)
        before = self.state()
        self.case.fixture.stop.side_effect = IntentRefused("stopped")
        with self.assertRaises(IntentRefused):
            self.case.import_outcome()
        self.assertEqual(before, self.state())

    def test_projection_replay_preserves_causal_identity_and_all_receipts(self):
        self.case.import_outcome()
        with self.engine.records.store._locked("citations") as path:
            events = self.engine.records.store._read(path)
        restored = projection(deepcopy(events))
        self.assertEqual(restored, self.state())
        self.assertEqual(next(iter(restored["factory_outcomes"].values()))["observation"]["cause"], "unresolved")


if __name__ == "__main__":
    unittest.main()
