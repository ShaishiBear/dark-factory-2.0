"""Transition receipts as canonical decision events (C02 + C07): validated inside the lock,
request before effect and observation after, the same request id and digest on observation,
illegal steps append nothing, a crash between request and observation leaves the request
pending and the phase survives reopening the store."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from factory_kernel.frontdoor_intent import IntentRefused, IntentStore, Principal
from factory_kernel.project_events import ProjectEvents, verify_chain
from factory_kernel.transition_journal import (
    OPERATION,
    TransitionJournal,
    pending_request,
    receipts_of,
    transition_operations,
    verify,
)

REPO = "owner/product"
OWNER = Principal("maintainer", "owner")
SERVICE = Principal("transition-service", "service")
STRANGER = Principal("stranger", "guest")
TID = "f" * 64
REQ = "1" * 32
REQ_SHA = "2" * 64


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="transition-journal-")))
        self.store = IntentStore(self.directory, repository=REPO, owner=OWNER.identity)
        self.events = ProjectEvents(self.store, transition_operations())
        self.journal = TransitionJournal(self.events, "p")
        self.version = 0

    def record(self, event, *, request_id=None, request_sha256=None, principal=SERVICE, stop=False, key=None, **kw):
        result = self.journal.record(transition_id=TID, event=event, principal=principal, expected_version=self.version,
                                     idempotency_key=key or f"{event}-{self.version}", request_id=request_id,
                                     request_sha256=request_sha256, stop=stop, **kw)
        self.version = result["project_version"]
        return result

    def test_request_before_effect_then_observation_under_the_same_request(self):
        first = self.record("request_fence", request_id=REQ, request_sha256=REQ_SHA, effect={"kind": "push_branch"})
        self.assertEqual((first["phase_before"], first["phase_after"], first["replayed"]), ("reviewed", "fence_requested", False))
        receipts = self.journal.receipts(TID, OWNER)
        self.assertEqual(pending_request(receipts)["request_id"], REQ, "a crash here leaves the request pending, never absent")
        self.assertEqual(self.journal.compare_with_store(TID, OWNER, "started"), "consistent")
        self.assertEqual(self.journal.compare_with_store(TID, OWNER, None), "reconciliation_required")
        with self.assertRaises(IntentRefused):
            self.record("observe_fence", request_id="3" * 32, request_sha256=REQ_SHA)
        with self.assertRaises(IntentRefused):
            self.record("observe_fence", request_id=REQ, request_sha256="4" * 64)
        self.assertEqual(self.journal.phase(TID, OWNER), "fence_requested", "refused observations appended nothing")
        done = self.record("observe_fence", request_id=REQ, request_sha256=REQ_SHA, effect={"main_sha": "e" * 40})
        self.assertEqual(done["phase_after"], "fenced")
        self.assertIsNone(pending_request(self.journal.receipts(TID, OWNER)))
        # Reopen the store: the phase is derived from the receipts, not from memory.
        again = TransitionJournal(ProjectEvents(IntentStore(self.directory, repository=REPO, owner=OWNER.identity), transition_operations()), "p")
        self.assertEqual(again.phase(TID, OWNER), "fenced")
        verify_chain(list(self.events.read("p", OWNER)), project="p", repository=REPO)

    def test_illegal_steps_and_orphan_observations_append_nothing(self):
        with self.assertRaises(IntentRefused):
            self.record("observe_fence", request_id=REQ, request_sha256=REQ_SHA)  # no request pending
        with self.assertRaises(IntentRefused):
            self.record("begin_drain")  # skips the fence
        with self.assertRaises(IntentRefused):
            self.record("request_fence")  # a remote request without its id and digest
        with self.assertRaises(IntentRefused):
            self.record("reconcile", request_id=REQ, request_sha256=REQ_SHA)  # not a remote request
        self.assertEqual(len(self.events.read("p", OWNER)), 0)
        self.assertEqual(self.journal.phase(TID, OWNER), "reviewed")
        with self.assertRaises(IntentRefused):
            self.record("request_fence", request_id=REQ, request_sha256=REQ_SHA, principal=STRANGER)
        with self.assertRaises(IntentRefused):
            self.journal.record(transition_id="nothex", event="request_fence", principal=SERVICE, expected_version=0,
                                idempotency_key="k", request_id=REQ, request_sha256=REQ_SHA)

    def test_uncertainty_is_retained_and_resolved_under_the_same_request_and_stop_blocks_release(self):
        self.record("request_fence", request_id=REQ, request_sha256=REQ_SHA)
        self.record("mark_uncertain", request_id=REQ, request_sha256=REQ_SHA, note="response lost")
        self.assertEqual(self.journal.phase(TID, OWNER), "fence_requested:uncertain")
        self.assertEqual(self.journal.compare_with_store(TID, OWNER, "uncertain"), "uncertain")
        with self.assertRaises(IntentRefused):
            self.record("request_fence", request_id="5" * 32, request_sha256="6" * 64)  # no new transition to escape uncertainty
        self.record("observe_no_effect", request_id=REQ, request_sha256=REQ_SHA)
        self.assertEqual(self.journal.phase(TID, OWNER), "reviewed")
        with self.assertRaises(IntentRefused):
            self.record("request_fence", request_id=REQ, request_sha256=REQ_SHA)  # a request id is used once
        self.record("request_fence", request_id="7" * 32, request_sha256="8" * 64)
        self.record("observe_fence", request_id="7" * 32, request_sha256="8" * 64)
        for event in ("begin_drain", "observe_drained", "reconcile"):
            self.record(event, stop=True)
        with self.assertRaises(IntentRefused):
            self.record("retire_predecessor", stop=True)
        self.assertEqual(self.journal.phase(TID, OWNER), "reconciled")
        self.record("require_reconciliation")
        self.assertEqual(self.journal.phase(TID, OWNER), "reconciliation_required")
        with self.assertRaises(IntentRefused):
            self.record("retire_predecessor")

    def test_replay_returns_the_original_receipt_and_the_verifier_rejects_a_rewritten_history(self):
        first = self.record("request_fence", request_id=REQ, request_sha256=REQ_SHA, key="same")
        again = self.journal.record(transition_id=TID, event="request_fence", principal=SERVICE, expected_version=99,
                                    idempotency_key="same", request_id=REQ, request_sha256=REQ_SHA)
        self.assertEqual((again["replayed"], again["project_version"], again["phase_after"]), (True, first["project_version"], "fence_requested"))
        receipts = receipts_of(list(self.events.read("p", OWNER)), TID)
        self.assertEqual(verify(receipts), "fence_requested")
        forged = [dict(receipts[0], phase_after="fenced")]
        with self.assertRaises(IntentRefused):
            verify(forged)
        skipped = [dict(receipts[0], event="begin_drain", phase_before="fenced", phase_after="draining")]
        with self.assertRaises(IntentRefused):
            verify(skipped)
        self.assertEqual(self.events.read("p", OWNER)[0]["command"]["operation"], OPERATION)
        with self.assertRaises(IntentRefused):
            TransitionJournal(ProjectEvents(self.store, {}), "p")


if __name__ == "__main__":
    unittest.main()
