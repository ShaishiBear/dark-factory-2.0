"""Preparation cannot approve, repeat uncertain API calls, or overwrite newer intent."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from factory_kernel.canonical import sha256_value
from factory_kernel.frontdoor_intent import IntentStore, IntentRefused, Principal
from factory_kernel.frontdoor_prepare import CHECKS, SCENARIOS, IntentPreparation, api_provider
from tests.factory.test_frontdoor_intent import OWNER, WORKER, REPO, example_spec


def proposal():
    return {"spec": example_spec(), "assumptions": [], "open_questions": [], "technical_questions": []}


def audit(draft=None):
    return {"draft_sha256": sha256_value(draft or proposal()), "decision": "ready",
            "checks": {name: {"status": "met", "basis": "Source intent and existing product policy."} for name in CHECKS},
            "scenarios": {name: "Concrete user-visible outcome under existing policy." for name in SCENARIOS},
            "question": None}


class PreparationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = IntentStore(Path(tmp.name), repository=REPO, owner=OWNER.identity)
        self.store.execute("citations", {"operation": "record-intent", "idempotency_key": "intent",
            "expected_project_version": 0, "payload": {"wording": "Show cited words.\nKeep video."}}, principal=OWNER)
        self.provider = Mock()
        self.requests = []
        self.outputs = [proposal(), audit()]

        def run(request):
            self.requests.append(request)
            pending = json.loads((self.preparer.directory / "citations-1.json").read_text())
            self.assertEqual(pending["state"], "pending", "API call must follow durable reservation")
            self.assertEqual(list(Path(request.cwd).iterdir()), [])
            return SimpleNamespace(content=json.dumps(self.outputs.pop(0)), structured_output=None,
                                   model="fixture", cost_usd=0.01)
        self.provider.run.side_effect = run
        self.preparer = IntentPreparation(self.store, self.provider, lambda: {"commit": "a" * 40, "facts": "private conversations"})
        self.command = {"idempotency_key": "prepare", "expected_project_version": 1}

    def prepare(self, **kwargs):
        return self.preparer.prepare("citations", self.command, principal=kwargs.get("principal", OWNER))

    def snapshot(self):
        return self.store.snapshot("citations", principal=OWNER)

    def test_complete_intent_produces_review_without_questions_or_approval(self):
        result = self.prepare()
        self.assertEqual(result["state"], "ready-for-review", result)
        self.assertEqual(self.snapshot()["approvals"], [])
        self.assertEqual(self.snapshot()["draft"]["open_questions"], [])
        self.assertEqual(self.snapshot()["ledger"][0]["wording"], "Show cited words.\nKeep video.")
        self.assertEqual(result["audit"]["draft_sha256"], sha256_value(result["draft"]))
        self.assertEqual(self.preparer.latest("citations"), result)
        self.assertEqual(len(self.requests), 2)
        for request in self.requests:
            self.assertEqual(request.allowed_tools, ())
            self.assertEqual(request.environment, {})
            self.assertEqual(request.max_turns, 5)
            self.assertEqual(request.max_budget_usd, 1.0)
            self.assertEqual(request.timeout_seconds, 338)
            self.assertFalse(Path(request.cwd).exists())

    def test_exact_replay_and_another_key_cannot_repeat_a_spend(self):
        result = self.prepare()
        self.assertEqual(self.prepare(), result)
        self.command["idempotency_key"] = "another"
        with self.assertRaisesRegex(IntentRefused, "already has a preparation"):
            self.prepare()
        self.assertEqual(self.provider.run.call_count, 2)

    def test_pending_and_failed_calls_survive_restarts_without_retry(self):
        self.provider.run.side_effect = TimeoutError("private credential detail")
        result = self.prepare()
        self.assertEqual(result["state"], "failed")
        self.assertNotIn("private credential", json.dumps(result))
        restarted = IntentPreparation(self.store, self.provider, Mock())
        self.assertEqual(restarted.prepare("citations", self.command, principal=OWNER), result)
        self.assertEqual(self.provider.run.call_count, 1)
        path = self.preparer.directory / "citations-1.json"
        result["state"] = "pending"
        self.preparer._save(path, result)
        self.assertEqual(self.prepare()["state"], "pending")
        self.assertEqual(self.provider.run.call_count, 1)

    def test_only_owner_can_request_and_stale_input_spends_nothing(self):
        for principal in (WORKER, Principal("someone-else", "owner")):
            with self.subTest(principal=principal), self.assertRaises(IntentRefused):
                self.prepare(principal=principal)
        self.command["expected_project_version"] = 2
        with self.assertRaises(IntentRefused):
            self.prepare()
        self.provider.run.assert_not_called()

    def test_new_intent_during_audit_is_not_overwritten(self):
        original = self.provider.run.side_effect

        def run(request):
            if request.role == "intent-auditor":
                self.store.execute("citations", {"operation": "record-intent", "idempotency_key": "changed",
                    "expected_project_version": 1, "payload": {"wording": "New scope"}}, principal=OWNER)
            return original(request)
        self.provider.run.side_effect = run
        self.assertEqual(self.prepare()["state"], "failed")
        self.assertIsNone(self.snapshot()["draft"])
        self.assertEqual(self.snapshot()["ledger"][-1]["wording"], "New scope")

    def test_question_requires_product_reason_and_prevents_approval(self):
        value = audit()
        value.update(decision="question", question={"text": "Who may see a shared citation?",
                     "why_owner": "Public and account-only links expose different information.",
                     "interpretations": ["Account only", "Anyone with link"]})
        value["checks"]["product_ambiguity"]["status"] = "open"
        self.outputs[1] = value
        result = self.prepare()
        self.assertEqual(result["state"], "question")
        self.assertEqual(result["audit"]["draft_sha256"], sha256_value(result["draft"]))
        draft = self.snapshot()["draft"]
        self.assertEqual(draft["open_questions"], [value["question"]["text"]])
        with self.assertRaisesRegex(IntentRefused, "blocking product questions"):
            self.store.execute("citations", {"operation": "approve-spec", "idempotency_key": "approve",
                "expected_project_version": 2, "payload": {"draft_version": draft["draft_version"],
                "spec_sha256": draft["spec_sha256"], "wording": "Approve"}}, principal=OWNER)

    def test_missing_audit_wrong_binding_or_unresolved_readiness_never_proposes(self):
        for defect in ("missing", "hash", "unresolved", "revision"):
            with self.subTest(defect=defect):
                self.setUp()
                if defect == "missing":
                    del self.outputs[1]["checks"]["actors"]
                elif defect == "hash":
                    self.outputs[1]["draft_sha256"] = "b" * 64
                elif defect == "unresolved":
                    self.outputs[1]["checks"]["actors"]["status"] = "open"
                else:
                    self.outputs[0]["spec"]["revision"] = 2
                    self.outputs[1] = audit(self.outputs[0])
                self.assertEqual(self.prepare()["state"], "failed")
                self.assertIsNone(self.snapshot()["draft"])

    def test_api_provider_requires_explicit_route_and_disables_retries(self):
        from factory_kernel.config import load_config
        config = load_config(Path(__file__).resolve().parents[2] / ".factory/kernel.json").provider
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(IntentRefused):
            api_provider(config)
        with patch.dict("os.environ", {"ANTHROPIC_BASE_URL": "https://openrouter.ai/api", "ANTHROPIC_AUTH_TOKEN": "fixture"}, clear=True):
            self.assertEqual(api_provider(config).config.transient_retries, 0)


if __name__ == "__main__":
    unittest.main()
