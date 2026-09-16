from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from factory_kernel.canonical import canonical_bytes, sha256_value
from factory_kernel.decision_history import explain_history
from factory_kernel.frontdoor_intent import IntentRefused, IntentStore, Principal
from tests.factory import test_frontdoor_intent as intent


class DecisionHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = IntentStore(self.root, repository=intent.REPO, owner=intent.OWNER.identity)
        self.version = 0

    def command(self, operation, payload, principal=intent.OWNER):
        command = {"idempotency_key": str(self.version), "expected_project_version": self.version,
                   "operation": operation, "payload": payload}
        result = self.store.execute("citations", command, principal=principal)
        self.version = result["project_version"]
        return result, command

    def history(self, principal=intent.OWNER):
        return explain_history(self.store, "citations", principal=principal)

    def draft(self, revision=1):
        self.command("record-intent", {"wording": "Private owner intent"})
        spec = intent.example_spec()
        spec["revision"] = revision
        return self.command("propose-spec", {"spec": spec, "assumptions": ["Keep modal"],
                                             "open_questions": [], "technical_questions": []}, intent.WORKER)[0]["draft"]

    def approve(self, draft):
        return self.command("approve-spec", {"draft_version": draft["draft_version"],
                                             "spec_sha256": draft["spec_sha256"], "wording": "Approve"})

    def rewrite(self, mutate):
        path = self.root / "citations.json"
        events = json.loads(path.read_text())
        mutate(events)
        previous = None
        for version, event in enumerate(events, 1):
            event["project_version"] = version
            event["previous"] = previous
            previous = sha256_value(event)
        path.write_bytes(canonical_bytes(events))

    def test_history_retains_old_proposals_and_approval_basis(self):
        self.approve(self.draft())
        self.command("add-exploration", {"wording": "Consider another design"})
        self.approve(self.draft(2))
        history = self.history()
        self.assertEqual(history["project_version"], 7)
        first, second = [e for e in history["events"] if e["operation"] == "approve-spec"]
        self.assertEqual(second["supersedes"], first["event_id"])
        self.assertEqual(history["events"][1]["basis"], [history["events"][0]["event_id"]])
        self.assertEqual(first["basis"], [history["events"][1]["event_id"]])
        self.assertEqual(history["execution_status"], "not-activated-by-history")
        self.assertTrue(all(e["proof_status"] == "not-established" for e in history["events"]))
        path = self.root / "citations.json"
        before = path.read_bytes()
        self.assertEqual(self.history(), history)
        self.assertEqual(path.read_bytes(), before)

    def test_legitimate_command_replay_is_not_a_new_decision(self):
        _, command = self.command("record-intent", {"wording": "Intent"})
        before = self.history()
        self.store.execute("citations", command, principal=intent.OWNER)
        self.assertEqual(before, self.history())

    def test_duplicate_persisted_event_is_refused_even_with_recomputed_chain(self):
        self.draft()
        self.rewrite(lambda events: events.append(deepcopy(events[-1])))
        with self.assertRaisesRegex(IntentRefused, "duplicate/replayed"):
            self.history()

    def test_forged_owner_cannot_approve_in_history(self):
        self.approve(self.draft())
        self.rewrite(lambda events: events[-1].update(actor={"role": "proposal", "identity": "model"}))
        with self.assertRaisesRegex(IntentRefused, "configured owner"):
            self.history()

    def test_wrong_spec_hash_is_refused(self):
        self.approve(self.draft())
        self.rewrite(lambda events: events[-1]["command"]["payload"].update(spec_sha256="f" * 64))
        with self.assertRaisesRegex(IntentRefused, "current recorded proposal"):
            self.history()

    def test_duplicate_approval_with_new_command_key_is_refused(self):
        self.approve(self.draft())
        def duplicate(events):
            event = deepcopy(events[-1])
            event["command"]["idempotency_key"] = "different-key"
            event["command"]["expected_project_version"] = len(events)
            events.append(event)
        self.rewrite(duplicate)
        with self.assertRaisesRegex(IntentRefused, "current recorded proposal"):
            self.history()

    def test_later_intent_does_not_erase_prior_approval_or_approve_new_scope(self):
        self.approve(self.draft())
        approval = self.history()["latest_recorded_approval"]
        self.command("record-intent", {"wording": "A new unapproved scope"})
        history = self.history()
        self.assertEqual(history["latest_recorded_approval"], approval)
        self.assertEqual(history["events"][-1]["operation"], "record-intent")
        self.assertEqual(history["execution_status"], "not-activated-by-history")

    def test_hash_chain_tampering_is_refused_by_existing_store(self):
        self.draft()
        path = self.root / "citations.json"
        events = json.loads(path.read_text())
        events[0]["command"]["payload"]["wording"] = "edited"
        path.write_bytes(canonical_bytes(events))
        with self.assertRaisesRegex(IntentRefused, "cannot be verified"):
            self.history()

    def test_other_principals_cannot_read_private_history(self):
        self.draft()
        for principal in (None, intent.WORKER, Principal("other", "owner"), Principal("stranger", "guest")):
            with self.subTest(principal=principal), self.assertRaises(IntentRefused):
                self.history(principal)

    def test_empty_history_is_explicit(self):
        history = self.history()
        self.assertEqual(history["events"], [])
        self.assertIsNone(history["latest_recorded_approval"])
        self.assertIn("qualification-not-assessed", history["gaps"])


if __name__ == "__main__":
    unittest.main()
