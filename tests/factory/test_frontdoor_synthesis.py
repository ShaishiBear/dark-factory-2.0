"""Programme synthesis cannot spend without approval or activate its own proposal."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from factory_kernel.frontdoor_intent import IntentRefused
from factory_kernel.frontdoor_synthesis import ProgrammePreparation
from factory_kernel.programme import compile_programme
from tests.factory.test_frontdoor_intent import OWNER, WORKER
from tests.factory import test_frontdoor_programme as review_tests


class SynthesisTests(unittest.TestCase):
    def setUp(self):
        self.fixture = review_tests.ProgrammeReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store
        self.command = {key: value for key, value in self.fixture.request.items() if key != "proposal"}
        self.command["idempotency_key"] = "synthesis-once"
        self.proposal = deepcopy(self.fixture.request["proposal"])
        self.provider = Mock()
        self.service = ProgrammePreparation(self.store, self.provider, lambda: {"commit": "a" * 40},
                                            app_login="factory[bot]")

        def run(request):
            path = self.service.directory / f"citations-{self.fixture.version}.json"
            self.assertEqual(json.loads(path.read_text())["state"], "pending")
            self.assertEqual(list(Path(request.cwd).iterdir()), [])
            return SimpleNamespace(content=json.dumps(self.proposal), structured_output=None,
                                   model="fixture", cost_usd=0.01)
        self.provider.run.side_effect = run

    def prepare(self, principal=OWNER):
        return self.service.prepare("citations", self.command, principal=principal)

    def test_approved_scope_becomes_compiled_review_with_no_store_or_execution_effect(self):
        before = self.store.snapshot("citations", principal=OWNER)
        result = self.prepare()
        self.assertEqual(result["state"], "ready-for-review", result)
        review = result["review"]
        self.assertEqual(review["activation"], "requires-protected-main-review")
        self.assertEqual(review["programme_sha256"], compile_programme(review["input"], repository=self.store.repository).sha256)
        self.assertEqual(review["input"]["spec"], before["approvals"][-1]["spec"])
        self.assertEqual(self.store.snapshot("citations", principal=OWNER), before)
        request = self.provider.run.call_args.args[0]
        self.assertEqual(request.role, "programme-proposer")
        self.assertEqual(request.allowed_tools, ())
        self.assertEqual(request.environment, {})
        self.assertEqual((request.max_turns, request.max_budget_usd, request.timeout_seconds), (5, 1.0, 338))
        self.assertFalse(Path(request.cwd).exists())

    def test_unapproved_or_stale_scope_spends_nothing(self):
        for key, value in (("approval_version", 2), ("approval_version", True),
                           ("spec_sha256", "f" * 64), ("expected_project_version", 2)):
            original = self.command[key]
            self.command[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(IntentRefused):
                self.prepare()
            self.command[key] = original
        with self.assertRaises(IntentRefused):
            self.prepare(WORKER)
        with self.assertRaises(IntentRefused):
            self.service.prepare("unapproved", self.command, principal=OWNER)
        self.provider.run.assert_not_called()

    def test_replay_and_new_key_do_not_repeat_spend(self):
        result = self.prepare()
        self.assertEqual(self.prepare(), result)
        self.command["idempotency_key"] = "new-key"
        with self.assertRaises(IntentRefused):
            self.prepare()
        self.provider.run.assert_called_once()

    def test_failed_and_pending_records_survive_restart_without_retry(self):
        self.provider.run.side_effect = TimeoutError("private token in provider error")
        result = self.prepare()
        self.assertEqual(result["state"], "failed")
        self.assertNotIn("private token", json.dumps(result))
        restarted = ProgrammePreparation(self.store, self.provider, Mock(), app_login="factory[bot]")
        self.assertEqual(restarted.prepare("citations", self.command, principal=OWNER), result)
        result["state"] = "pending"
        self.service._save(self.service.directory / f"citations-{self.fixture.version}.json", result)
        self.assertEqual(self.prepare()["state"], "pending")
        self.provider.run.assert_called_once()

    def test_invalid_coverage_never_becomes_a_ready_programme(self):
        self.proposal["items"] = []
        result = self.prepare()
        self.assertEqual(result["state"], "failed")
        self.assertNotIn("review", result)

    def test_new_intent_during_synthesis_refuses_the_stale_review(self):
        original = self.provider.run.side_effect

        def run(request):
            result = original(request)
            self.fixture.write("record-intent", {"wording": "Clarified while planning"})
            return result

        self.provider.run.side_effect = run
        result = self.prepare()
        self.assertEqual(result["state"], "failed")
        self.assertNotIn("review", result)
        self.assertEqual(self.store.snapshot("citations", principal=OWNER)["ledger"][-1]["wording"],
                         "Clarified while planning")


if __name__ == "__main__":
    unittest.main()
