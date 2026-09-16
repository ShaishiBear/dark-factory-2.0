"""A retained refusal informs an explicit owner judgment, never new proof authority."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from factory_kernel.canonical import canonical_bytes, sha256_value
from factory_kernel.decision_history import explain_history
from factory_kernel.frontdoor_intent import IntentRefused
from factory_kernel.reconsideration import Reconsideration, RetainedRun
from factory_kernel.refusal import describe, refusal_record
from tests.factory import test_exploration as exploration
from tests.factory import test_claim_explanation as evidence
from tests.factory.test_frontdoor_intent import OWNER, WORKER


class ReconsiderationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = exploration.ExplorationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.engine = self.fixture.engine
        self.fixture.add()
        self.fixture.recommend()
        self.engine.handoff("citations", self.fixture.command({"proposal": self.fixture.fixture.request["proposal"]}), principal=OWNER)
        self.input = self.engine.prepare_handoff("citations", "lookup",
            expected_project_version=self.fixture.command({})["expected_project_version"],
            principal=OWNER, include_strategy=True)["input"]
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = RetainedRun(self.input, self.input["proposal"]["items"][0]["id"], 42, 1, 17,
            self.root, evidence.ROOT / ".factory/evidence-spine.json", "b" * 40, "a" * 40)
        self.write_refusal()
        self.service = Reconsideration(self.engine, {"run-42": self.source})

    def write_refusal(self, **changes):
        row = refusal_record(describe("code_holdout", RuntimeError("candidate fails declared constraint")),
            pr=17, head="b" * 40, base="a" * 40, stage="code_holdout", timestamp="2026-09-16T00:00:00Z")
        row.update(changes)
        (self.root / "validation-refusal.json").write_bytes(canonical_bytes(row))

    def preview(self):
        return self.service.preview("citations", "lookup", "run-42", principal=OWNER)

    def request(self, classification="strategy-failure", **changes):
        return {"source_id": "run-42", "observation_sha256": self.preview()["observation_sha256"],
            "classification": classification, "supersedes": None,
            "claim_ids": ["scan-assumption"] if classification == "strategy-failure" else [],
            "evidence_claim_ids": ["validation-refusal"],
            "rationale": "Owner finds the scan workload assumption incompatible with the observed requirement.",
            "alternatives_considered": "A coding defect and unsuitable test data were considered; attribution is owner judgment.",
            **changes}

    def review(self, classification="strategy-failure", **changes):
        return self.service.review("citations", self.fixture.command(self.request(classification, **changes)), principal=OWNER)

    def test_feedback_reopens_only_affected_choice_then_recompiles_retained_b_without_proof(self):
        self.fixture.open("unrelated")
        self.fixture.add("unrelated", "-other")
        self.fixture.recommend("unrelated")
        before = self.engine.records.read("citations", OWNER)[0]
        state = self.review()
        record = next(iter(state["feedback"].values()))
        self.assertEqual(record["judgment"]["independent_strategy_rejection"], "not-established")
        self.assertEqual(record["judgment"]["authority"], "owner-causal-assessment")
        self.assertEqual(state["sessions"]["lookup"]["status"], "reconsideration-required")
        self.assertEqual(state["sessions"]["unrelated"], before["sessions"]["unrelated"])
        self.assertEqual(record["frontier"][0]["session_id"], "lookup")
        self.assertEqual(len(record["frontier"]), 1)
        with self.assertRaisesRegex(IntentRefused, "no current"):
            self.engine.prepare_handoff("citations", "lookup", expected_project_version=state["project_version"],
                                        principal=OWNER, include_strategy=True)
        reopened = self.service.reopen("citations", self.fixture.command({
            "feedback_sha256": record["id"], "reason": "Reassess the retained alternative."}), principal=OWNER)
        self.assertEqual(reopened["budgets"], before["budgets"])
        self.assertEqual(reopened["sessions"]["lookup"]["candidates"], before["sessions"]["lookup"]["candidates"])
        self.assertEqual(reopened["sessions"]["lookup"]["reopenings"][-1]["feedback_sha256"], record["id"])
        self.assertEqual(self.fixture.inspect()["comparison"]["preferred"], "index")
        self.fixture.recommend()
        self.engine.handoff("citations", self.fixture.command({"proposal": self.input["proposal"]}), principal=OWNER)
        result = self.engine.prepare_handoff("citations", "lookup",
            expected_project_version=self.fixture.command({})["expected_project_version"], principal=OWNER, include_strategy=True)
        self.assertEqual(result["input"]["strategy"]["candidate"]["id"], "index")
        self.assertEqual(result["input"]["spec"], self.input["spec"])
        self.assertNotEqual(result["input_sha256"], sha256_value(self.input))
        self.assertIs(result["input"]["strategy"]["proof_reuse_allowed"], False)
        final = self.engine.records.read("citations", OWNER)[0]
        self.assertEqual(final["feedback"][record["id"]], record)
        self.assertEqual(explain_history(self.fixture.store, "citations", principal=OWNER)["exploration"], final)
        self.assertEqual(self.preview()["observation"], record["observation"])

    def test_implementation_failure_does_not_change_claims_recommendations_or_budget(self):
        before = self.engine.records.read("citations", OWNER)[0]
        after = self.review("implementation-failure")
        for key in ("sessions", "claims", "budgets"):
            self.assertEqual(after[key], before[key])
        self.assertEqual(next(iter(after["feedback"].values()))["frontier"], [])

    def test_missing_refusal_is_inconclusive_not_failure(self):
        (self.root / "validation-refusal.json").unlink()
        with self.assertRaisesRegex(IntentRefused, "inconclusive"):
            self.review()
        state = self.review("inconclusive", evidence_claim_ids=[])
        record = next(iter(state["feedback"].values()))
        self.assertEqual(record["observation"]["refusal"]["status"], "absent")
        self.assertEqual(state["claims"]["scan-assumption"]["status"], "active")

    def test_wrong_refusal_subject_authority_and_malformed_fields_cannot_change_claims(self):
        for changes in ({"head": "f" * 40}, {"base": "f" * 40}, {"pr": 999},
                        {"authority": "builder-proclaims-independent-rejection"}, {"version": "2.0"},
                        {"detail": "x" * 2001}, {"rc": True}, {"reason_code": {}}, {"extra": "field"}):
            with self.subTest(changes=changes):
                self.write_refusal(**changes)
                with self.assertRaisesRegex(IntentRefused, "inconclusive"):
                    self.review()

    def test_operational_or_unattributed_refusal_is_not_strategy_evidence(self):
        from factory_kernel.refusal import AUTHORITY
        for code in ("unknown", "identity_expired", "stale_base", "trust_root_currency"):
            self.write_refusal(reason_code=code, authority=AUTHORITY[code])
            with self.assertRaisesRegex(IntentRefused, "inconclusive"):
                self.review()

    def test_changed_evidence_after_preview_refuses_without_event(self):
        command = self.fixture.command(self.request())
        self.write_refusal(detail="Different recorded finding.")
        with self.assertRaisesRegex(IntentRefused, "changed since"):
            self.service.review("citations", command, principal=OWNER)
        self.assertEqual(self.engine.records.read("citations", OWNER)[0]["feedback"], {})

    def test_foreign_principal_cannot_even_read_evidence(self):
        with patch("factory_kernel.reconsideration.explain_run", side_effect=AssertionError("private read")):
            with self.assertRaises(IntentRefused):
                self.service.preview("citations", "lookup", "run-42", principal=WORKER)

    def test_wrong_programme_or_item_cannot_claim_an_existing_selection(self):
        wrong = deepcopy(self.input)
        wrong["strategy"]["candidate"]["mechanism"] = "Unrecorded mechanism."
        for source in (replace(self.source, programme_input=wrong), replace(self.source, item_id="unknown")):
            self.service = Reconsideration(self.engine, {"run-42": source})
            with self.assertRaises(IntentRefused):
                self.preview()

    def test_outside_ancestry_and_self_certification_refused(self):
        for changes in ({"claim_ids": ["index-assumption"]}, {"qualification_status": "QUALIFIED"},
                        {"classification": "independently-proven-strategy-failure"},
                        {"evidence_claim_ids": ["made-up-proof"]}, {"claim_ids": []}):
            with self.subTest(changes=changes), self.assertRaises(IntentRefused):
                self.review(**changes)

    def test_concurrent_project_change_refuses_without_feedback(self):
        command = self.fixture.command(self.request())
        self.fixture.open("concurrent")
        with self.assertRaisesRegex(IntentRefused, "stale project version"):
            self.service.review("citations", command, principal=OWNER)

    def test_replay_is_read_only_and_new_key_cannot_repeat_attempt(self):
        command = self.fixture.command(self.request())
        first = self.service.review("citations", command, principal=OWNER)
        self.assertEqual(self.service.review("citations", command, principal=OWNER), first)
        with self.assertRaisesRegex(IntentRefused, "already reviewed"):
            self.review()
        second_name = Reconsideration(self.engine, {"alias": self.source})
        request = deepcopy(command["request"])
        request.update(source_id="alias", observation_sha256=second_name.preview(
            "citations", "lookup", "alias", principal=OWNER)["observation_sha256"])
        with self.assertRaisesRegex(IntentRefused, "already reviewed"):
            second_name.review("citations", self.fixture.command(request), principal=OWNER)

    def test_stop_after_evidence_read_prevents_feedback(self):
        request = self.request()
        from factory_kernel.claim_explanation import explain_run
        def stopped(**kwargs):
            result = explain_run(**kwargs)
            self.fixture.stop.side_effect = IntentRefused("stopped")
            return result
        with patch("factory_kernel.reconsideration.explain_run", side_effect=stopped):
            with self.assertRaisesRegex(IntentRefused, "stopped"):
                self.service.review("citations", self.fixture.command(request), principal=OWNER)

    def test_new_unapproved_intent_invalidates_review(self):
        command = self.fixture.command(self.request())
        self.fixture.store.execute("citations", {"idempotency_key": "changed-intent",
            "expected_project_version": command["expected_project_version"], "operation": "record-intent",
            "payload": {"wording": "New scope"}}, principal=OWNER)
        with self.assertRaises(IntentRefused):
            self.service.review("citations", command, principal=OWNER)

    def test_reopen_cannot_target_unrelated_or_spend_another_round_twice(self):
        state = self.review()
        feedback = next(iter(state["feedback"]))
        request = {"feedback_sha256": feedback, "reason": "Reconsider."}
        self.fixture.open("unrelated")
        with self.assertRaisesRegex(IntentRefused, "does not affect"):
            self.service.reopen("citations", self.fixture.command(request, "unrelated"), principal=OWNER)
        command = self.fixture.command(request)
        first = self.service.reopen("citations", command, principal=OWNER)
        self.assertEqual(self.service.reopen("citations", command, principal=OWNER), first)
        with self.assertRaisesRegex(IntentRefused, "already opened"):
            self.service.reopen("citations", self.fixture.command(request), principal=OWNER)

    def test_partial_proof_is_preserved_as_a_gap_not_manufactured_qualification(self):
        preview = self.preview()
        self.assertTrue(all(row["evidence_status"] == "absent" for row in preview["observation"]["evidence"]["claims"]))
        self.assertEqual(preview["observation"]["factory_completion"], "not-assessed")
        self.assertIs(preview["observation"]["proof_reuse_allowed"], False)
        self.assertEqual(preview["observation"]["refusal"]["issuer_authentication"], "not-established")
        self.assertEqual(self.fixture.command({})["expected_project_version"], preview["project_version"])

    def test_stale_policy_cannot_invalidate_strategy(self):
        bundle = evidence.ClaimExplanationTests()
        bundle.setUp()
        self.addCleanup(bundle.doCleanups)
        self.source = replace(self.source, artifacts=bundle.root, head_sha=evidence.closure.HEAD,
                              base_sha=evidence.closure.BASE)
        raw = json.loads((self.root / "validation-refusal.json").read_text())
        raw.update(head=self.source.head_sha, base=self.source.base_sha)
        (bundle.root / "validation-refusal.json").write_bytes(canonical_bytes(raw))
        bundle.bundle["spine"]["policy_sha256"] = "f" * 64
        bundle.write("evidence-bundle.json", bundle.bundle)
        self.service = Reconsideration(self.engine, {"run-42": self.source})
        with self.assertRaisesRegex(IntentRefused, "inconclusive"):
            self.review()

    def test_round_limit_and_pending_effects_are_preserved(self):
        for _ in range(3):
            self.engine.reopen("citations", self.fixture.command({"reason": "Review again."}), principal=OWNER)
        state = self.review()
        with self.assertRaisesRegex(IntentRefused, "round budget"):
            self.service.reopen("citations", self.fixture.command({"feedback_sha256": next(iter(state["feedback"])),
                "reason": "Cannot buy a new round by recording feedback."}), principal=OWNER)

    def test_pending_probe_must_be_reconciled_before_feedback_reopen(self):
        self.engine.reopen("citations", self.fixture.command({"reason": "Investigate."}), principal=OWNER)
        def interrupted(*args, **kwargs):
            self.fixture.stop.side_effect = IntentRefused("stopped")
            raise IntentRefused("stopped")
        with patch("factory_kernel.exploration.run_probe", side_effect=interrupted):
            with self.assertRaises(IntentRefused):
                self.engine.experiment("citations", self.fixture.command(self.fixture.experiment_request()), principal=OWNER)
        self.fixture.stop.side_effect = None
        state = self.review()
        with self.assertRaisesRegex(IntentRefused, "pending effects"):
            self.service.reopen("citations", self.fixture.command({"feedback_sha256": next(iter(state["feedback"])),
                "reason": "Pending work does not vanish."}), principal=OWNER)
        self.assertGreater(next(iter(state["budgets"].values()))["probe_units"], 0)

    def test_dependency_ancestor_invalidates_transitive_frontier(self):
        self.fixture.open("downstream")
        self.engine.add_candidates("citations", self.fixture.command({
            "claims": [exploration.claim("dependent", ["scan-assumption"]), exploration.claim("separate")],
            "candidates": [exploration.candidate("dependent-choice", "linear", 1, 2, ["dependent"]),
                           exploration.candidate("separate-choice", "hash", 10, 20, ["separate"])]}, "downstream"), principal=OWNER)
        self.fixture.recommend("downstream")
        state = self.review()
        record = next(iter(state["feedback"].values()))
        self.assertEqual(record["affected_claim_ids"], ["dependent", "scan-assumption"])
        self.assertEqual({row["session_id"] for row in record["frontier"]}, {"lookup", "downstream"})
        self.assertEqual(state["claims"]["dependent"]["status"], "active")
        self.assertEqual(state["sessions"]["downstream"]["status"], "reconsideration-required")

    def test_reconsideration_preserves_spent_probe_budget_and_refreshes_context(self):
        self.fixture.open("work")
        self.fixture.add("work", "-work")
        request = self.fixture.experiment_request()
        request["claim_ids"] = [key + "-work-assumption" for key in ("scan", "index")]
        for target in request["targets"]:
            target["candidate_id"] += "-work"
            target["falsifies_claim"] = target["candidate_id"] + "-assumption"
        self.engine.experiment("citations", self.fixture.command(request, "work"), principal=OWNER)
        state = self.review()
        budget = deepcopy(state["budgets"])
        self.assertGreater(next(iter(budget.values()))["probe_units"], 0)
        self.fixture.context["commit"] = "d" * 40
        self.fixture.rehash()
        result = self.service.reopen("citations", self.fixture.command({"feedback_sha256": next(iter(state["feedback"])),
            "reason": "Current repository requires fresh assessment."}), principal=OWNER)
        self.assertEqual(result["budgets"], budget)
        self.assertEqual(result["sessions"]["lookup"]["context"], self.fixture.context)
        self.assertFalse(self.fixture.inspect()["comparison"]["sufficient_support"])

    def test_obsolete_feedback_cannot_reopen_a_new_recommendation(self):
        state = self.review()
        request = {"feedback_sha256": next(iter(state["feedback"])), "reason": "Reconsider."}
        self.service.reopen("citations", self.fixture.command(request), principal=OWNER)
        self.fixture.recommend()
        with self.assertRaisesRegex(IntentRefused, "does not affect"):
            self.service.reopen("citations", self.fixture.command(request), principal=OWNER)

    def test_inconclusive_review_can_be_followed_up_without_erasing_original(self):
        (self.root / "validation-refusal.json").unlink()
        first = self.review("inconclusive", evidence_claim_ids=[])
        old = next(iter(first["feedback"].values()))
        self.write_refusal()
        with self.assertRaisesRegex(IntentRefused, "already reviewed"):
            self.review()
        second = self.review(supersedes=old["id"])
        self.assertEqual(len(second["feedback"]), 2)
        self.assertEqual(second["feedback"][old["id"]], old)
        self.assertEqual(second["claims"]["scan-assumption"]["status"], "invalidated")

    def test_implementation_feedback_cannot_be_replayed_under_a_new_key(self):
        self.review("implementation-failure")
        with self.assertRaisesRegex(IntentRefused, "already reviewed"):
            self.review("implementation-failure")


if __name__ == "__main__":
    unittest.main()
