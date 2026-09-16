"""Reasoning sessions preserve frozen policy, scope currency, cost reservations and UNPROVEN handoff."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from factory_kernel.canonical import sha256_value
from factory_kernel.frontdoor_intent import IntentRefused
from factory_kernel.preflight_context import repository_context, validate_context
from factory_kernel.preflight_prepare import PreflightPreparation
from factory_kernel.worker_policy import INTAKE_ROLES
from tests.factory import test_frontdoor_programme as programme_fixture
from tests.factory.test_frontdoor_intent import OWNER, WORKER
from tests.factory.test_preflight import candidate, challenge, policy


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = programme_fixture.ProgrammeReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store
        self.command = {**deepcopy(self.fixture.request), "idempotency_key": "preflight-once", "item_id": "snippet",
                        "policy": policy(), "user_candidates": [], "direct_candidate": None}
        self.provider = Mock(config=None)
        self.context_value = {"commit": "a" * 40, "tracked_files": ["app/file0.py"],
                              "policy_files": {".factory/architecture.json": '{"principles":[]}',
                                               "FACTORY_RULES.md": "Independent qualification required."}}
        self.context = Mock(side_effect=lambda: deepcopy(self.context_value))
        self.stop = Mock()
        self.service = PreflightPreparation(self.store, self.provider, self.context,
                                           app_login="factory[bot]", check_stop=self.stop)
        self.provider.run.side_effect = self.respond
        self.requests = []

    def record(self):
        files = list(self.service.directory.glob("*.json"))
        self.assertEqual(len(files), 1)
        return json.loads(files[0].read_text())

    def respond(self, request):
        self.requests.append(request)
        record = self.record()
        self.assertEqual(record["stages"][-1]["state"], "pending")
        self.assertEqual(record["registration_sha256"], sha256_value(record["registration"]))
        self.assertEqual(list(Path(request.cwd).iterdir()), [])
        self.assertEqual(request.allowed_tools, ())
        self.assertEqual(request.environment, {})
        self.assertLessEqual(request.max_budget_usd, self.command["policy"]["budget"]["max_usd"] / 2)
        self.assertLessEqual(request.timeout_seconds, min(338, self.command["policy"]["budget"]["wall_seconds"]))
        self.assertNotIn(request.role, INTAKE_ROLES, "new reasoning roles must not gain the hosted intake transport")
        if request.role == "preflight-proposer":
            output = {"registration_sha256": record["registration_sha256"],
                      "candidates": [candidate(), candidate("alternative", 2)]}
        else:
            output = challenge(record["registration"], record["candidates"])
        return SimpleNamespace(structured_output=output, content="", model="fixture", cost_usd=0.01)

    def prepare(self, principal=OWNER):
        return self.service.prepare("citations", self.command, principal=principal)

    def test_end_to_end_reasoning_retains_alternatives_and_same_approved_scope_unproven(self):
        before = self.store.snapshot("citations", principal=OWNER)
        result = self.prepare()
        self.assertEqual(result["state"], "provisional-recommendation", result)
        self.assertEqual(result["handoff"]["candidate_id"], "baseline")
        self.assertEqual(result["handoff"]["qualification_status"], "UNPROVEN")
        self.assertIs(result["handoff"]["proof_reuse_allowed"], False)
        self.assertEqual(result["handoff"]["activation"], "requires-normal-admission-and-fresh-qualification")
        self.assertEqual(result["handoff"]["spec_sha256"], before["approvals"][-1]["spec_sha256"])
        self.assertEqual(result["handoff"]["repository_commit"], "a" * 40)
        self.assertEqual(len(result["decision"]["retained_alternatives"]), 2)
        self.assertEqual(len(result["handoff"]["assumption_ids"]), 1)
        self.assertEqual(result["reserved_usd"], 2)
        self.assertAlmostEqual(sum(row["reported_usd"] for row in result["stages"]), 0.02)
        self.assertEqual(self.store.snapshot("citations", principal=OWNER), before)
        self.assertEqual(self.stop.call_count, 4)
        self.assertTrue(all(not Path(request.cwd).exists() for request in self.requests))

    def test_challenger_is_a_separate_call_without_origin_or_baseline_attribution(self):
        result = self.prepare()
        self.assertEqual([request.role for request in self.requests], ["preflight-proposer", "preflight-challenger"])
        self.assertNotEqual(self.requests[0].cwd, self.requests[1].cwd)
        supplied = json.loads(self.requests[1].prompt.split("\n", 1)[1])
        self.assertEqual(supplied["candidates_sha256"], sha256_value(result["candidates"]))
        for row in supplied["candidates"]:
            self.assertNotIn("origin", row)
            self.assertNotIn("is_baseline", row)
        self.assertNotIn("winner", supplied)

    def test_user_strategy_is_preserved_and_can_beat_generated_alternatives(self):
        proposed = candidate("owner-option")
        self.command["user_candidates"] = [deepcopy(proposed)]
        original = self.provider.run.side_effect
        def alternatives(request):
            response = original(request)
            if request.role == "preflight-proposer":
                response.structured_output["candidates"] = [candidate(count=2), candidate("alternative", 3)]
            return response
        self.provider.run.side_effect = alternatives
        result = self.prepare()
        self.assertEqual(result["handoff"]["candidate_id"], "owner-option", result)
        retained = next(row for row in result["candidates"] if row["origin"] == "user")
        self.assertEqual({key: value for key, value in retained.items() if key != "origin"}, proposed)

    def test_policy_and_spec_constraints_are_frozen_before_first_spend(self):
        original = self.provider.run.side_effect
        def inspect(request):
            record = self.record()
            self.assertEqual(record["registration"]["policy"], self.command["policy"])
            self.assertEqual(record["registration"]["constraints"][0]["text"], self.fixture.spec["constraints"][0])
            self.assertIn("acceptance-AC1", {row["id"] for row in record["registration"]["constraints"]})
            self.assertIn("non-goal-1", {row["id"] for row in record["registration"]["constraints"]})
            return original(request)
        self.provider.run.side_effect = inspect
        self.prepare()

    def test_wrong_owner_missing_approval_and_stale_project_spend_nothing(self):
        with self.assertRaises(IntentRefused):
            self.prepare(WORKER)
        for key, value in (("expected_project_version", 2), ("approval_version", True), ("spec_sha256", "f" * 64),
                           ("item_id", "foreign-item")):
            original = self.command[key]
            self.command[key] = value
            with self.assertRaises(IntentRefused):
                self.prepare()
            self.command[key] = original
        self.provider.run.assert_not_called()

    def test_new_intent_invalidates_old_approval_even_when_caller_updates_project_version(self):
        self.fixture.write("record-intent", {"wording": "Different current intent."})
        self.command["expected_project_version"] = self.fixture.version
        with self.assertRaises(IntentRefused):
            self.prepare()
        self.provider.run.assert_not_called()

    def test_replay_new_key_and_posthoc_priority_change_never_repeat_spend(self):
        result = self.prepare()
        self.assertEqual(self.prepare(), result)
        for field, value in (("idempotency_key", "different"), ("policy", {**policy(), "priorities": ["surface"]})):
            original = self.command[field]
            self.command[field] = value
            with self.assertRaises(IntentRefused):
                self.prepare()
            self.command[field] = original
        self.assertEqual(self.provider.run.call_count, 2)

    def test_cached_comparison_is_not_reused_after_repository_changes(self):
        self.prepare()
        self.context_value["commit"] = "b" * 40
        with self.assertRaises(IntentRefused):
            self.prepare()
        self.assertEqual(self.provider.run.call_count, 2)

    def test_timeout_and_pending_reservation_do_not_retry_after_restart(self):
        self.provider.run.side_effect = TimeoutError("SECRET token in subprocess error")
        result = self.prepare()
        self.assertEqual(result["state"], "failed")
        self.assertNotIn("SECRET", json.dumps(result))
        restarted = PreflightPreparation(self.store, self.provider, self.context,
                                        app_login="factory[bot]", check_stop=self.stop)
        self.assertEqual(restarted.prepare("citations", self.command, principal=OWNER), result)
        self.provider.run.assert_called_once()

    def test_concurrent_same_request_observes_pending_reservation_without_spend(self):
        original = self.provider.run.side_effect
        observed = []
        def concurrent(request):
            observed.append(self.prepare())
            return original(request)
        self.provider.run.side_effect = concurrent
        self.assertEqual(self.prepare()["state"], "provisional-recommendation")
        self.assertEqual(self.provider.run.call_count, 2)
        self.assertTrue(all(row["stages"][-1]["state"] == "pending" for row in observed))
        self.assertTrue(all("handoff" not in row for row in observed))

    def test_scope_or_repository_change_during_reasoning_prevents_handoff(self):
        original = self.provider.run.side_effect
        def changed(request):
            response = original(request)
            if request.role == "preflight-challenger":
                self.context_value["commit"] = "b" * 40
            return response
        self.provider.run.side_effect = changed
        result = self.prepare()
        self.assertEqual(result["state"], "failed")
        self.assertNotIn("handoff", result)

    def test_changed_intent_during_reasoning_prevents_handoff(self):
        original = self.provider.run.side_effect
        def changed(request):
            response = original(request)
            if request.role == "preflight-challenger":
                self.fixture.write("record-intent", {"wording": "Changed during comparison."})
            return response
        self.provider.run.side_effect = changed
        self.assertNotIn("handoff", self.prepare())

    def test_changed_scope_before_second_call_does_not_spend_again(self):
        original = self.provider.run.side_effect
        def changed(request):
            response = original(request)
            self.fixture.write("record-intent", {"wording": "New scope before the challenge."})
            return response
        self.provider.run.side_effect = changed
        self.assertEqual(self.prepare()["state"], "failed")
        self.provider.run.assert_called_once()

    def test_stop_is_fresh_at_entry_each_spend_and_handoff(self):
        self.stop.side_effect = RuntimeError("stopped")
        with self.assertRaises(RuntimeError):
            self.prepare()
        self.provider.run.assert_not_called()
        self.stop.side_effect = [None, None, RuntimeError("stopped")]
        result = self.prepare()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(self.provider.run.call_count, 1)
        self.assertNotIn("handoff", result)

    def test_stop_before_final_handoff_withholds_recommendation_capability(self):
        self.stop.side_effect = [None, None, None, RuntimeError("stopped")]
        result = self.prepare()
        self.assertEqual(result["state"], "failed")
        self.assertNotIn("handoff", result)

    def test_unknown_spend_stops_before_second_call(self):
        original = self.provider.run.side_effect
        def unknown(request):
            response = original(request)
            response.cost_usd = None
            return response
        self.provider.run.side_effect = unknown
        result = self.prepare()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["stages"][0]["cost_status"], "unknown")
        self.provider.run.assert_called_once()

    def test_cumulative_wall_exhaustion_stops_before_second_call(self):
        self.service.clock = Mock(side_effect=[0, 0, 700])
        result = self.prepare()
        self.assertEqual(result["state"], "failed")
        self.provider.run.assert_called_once()

    def test_challenge_returned_after_total_deadline_cannot_produce_handoff(self):
        self.service.clock = Mock(side_effect=[0, 0, 0, 0, 700])
        result = self.prepare()
        self.assertEqual(result["state"], "failed")
        self.assertNotIn("handoff", result)
        self.assertEqual(self.provider.run.call_count, 2)

    def test_provider_auto_retries_are_not_permitted(self):
        provider = Mock(config=SimpleNamespace(transient_retries=1))
        with self.assertRaises(IntentRefused):
            PreflightPreparation(self.store, provider, self.context, app_login="factory[bot]", check_stop=self.stop)

    def test_smaller_decision_budget_narrows_both_provider_requests(self):
        self.command["policy"]["budget"].update(max_usd=0.5, wall_seconds=100)
        self.service.clock = lambda: 0
        result = self.prepare()
        self.assertEqual(result["state"], "provisional-recommendation", result)
        self.assertEqual(result["reserved_usd"], 0.5)
        self.assertTrue(all(request.max_budget_usd == 0.25 and request.timeout_seconds == 100
                            for request in self.requests))

    def test_direct_mode_records_skip_reason_and_spends_nothing(self):
        self.command["policy"].update(signals=[], direct_reason="One mechanical one-file change with no strategy choice.")
        self.command["direct_candidate"] = candidate()
        result = self.prepare()
        self.assertEqual(result["state"], "direct-unproven", result)
        self.assertEqual(result["handoff"]["qualification_status"], "UNPROVEN")
        self.assertEqual(result["reserved_usd"], 0)
        self.provider.run.assert_not_called()

    def test_direct_mode_cannot_suppress_known_exploration_trigger(self):
        self.command["direct_candidate"] = candidate()
        self.command["policy"]["direct_reason"] = "Skip anyway."
        result = self.prepare()
        self.assertEqual(result["state"], "failed")
        self.assertNotIn("handoff", result)
        self.provider.run.assert_not_called()

    def test_deep_trigger_is_recorded_without_fake_deep_analysis_or_probe(self):
        self.command["policy"]["signals"] = ["irreversible-migration"]
        result = self.prepare()
        self.assertEqual(result["state"], "needs-deep-analysis")
        self.assertNotIn("handoff", result)
        self.provider.run.assert_not_called()

    def test_generated_security_surface_triggers_deep_mode_before_more_spend(self):
        original = self.provider.run.side_effect
        def sensitive(request):
            response = original(request)
            response.structured_output["candidates"][1]["planned_files"] = ["app/backend/auth.py"]
            return response
        self.provider.run.side_effect = sensitive
        result = self.prepare()
        self.assertEqual(result["state"], "needs-deep-analysis")
        self.assertIn("security", result["observed_signals"])
        self.assertNotIn("handoff", result)
        self.provider.run.assert_called_once()

    def test_direct_mode_cannot_skip_a_detectable_security_surface(self):
        self.command["policy"].update(signals=[], direct_reason="Mechanically simple.")
        self.command["direct_candidate"] = candidate()
        self.command["direct_candidate"]["planned_files"] = ["app/auth.py"]
        self.context_value["tracked_files"].append("app/auth.py")
        result = self.prepare()
        self.assertEqual(result["state"], "failed")
        self.assertNotIn("handoff", result)
        self.provider.run.assert_not_called()

    def test_unresolved_comparison_preserves_candidates_without_handoff(self):
        original = self.provider.run.side_effect
        def uncertain(request):
            response = original(request)
            if request.role == "preflight-challenger":
                response.structured_output["assessments"][0]["constraints"][0]["status"] = "unknown"
            return response
        self.provider.run.side_effect = uncertain
        result = self.prepare()
        self.assertEqual(result["state"], "needs-evidence")
        self.assertEqual(len(result["candidates"]), 2)
        self.assertNotIn("handoff", result)

    def test_generator_cannot_rewrite_frozen_policy(self):
        original = self.provider.run.side_effect
        def changed(request):
            response = original(request)
            response.structured_output["registration_sha256"] = "f" * 64
            return response
        self.provider.run.side_effect = changed
        self.assertEqual(self.prepare()["state"], "failed")
        self.provider.run.assert_called_once()


class ContextTests(unittest.TestCase):
    def test_context_reads_committed_policy_and_inventory_without_executing_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".factory").mkdir()
            (root / ".factory/architecture.json").write_text('{"version":"1.0"}')
            (root / "FACTORY_RULES.md").write_text("Original protected policy.")
            def git(*args):
                return subprocess.check_output(["git", "-C", directory, *args], stderr=subprocess.DEVNULL)
            git("init", "-q")
            git("add", ".")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "policy")
            (root / "FACTORY_RULES.md").write_text("Uncommitted SECRET")
            (root / "private-secret").write_text("SECRET")
            context = repository_context(root)
            self.assertNotIn("SECRET", json.dumps(context))
            self.assertNotIn("private-secret", context["tracked_files"])
            self.assertEqual(context["commit"], git("rev-parse", "HEAD").decode().strip())
            self.assertEqual(context["policy_files"]["FACTORY_RULES.md"], "Original protected policy.")

    def test_missing_or_unbounded_repository_context_refuses(self):
        for context in ({}, {"commit": "a" * 40, "tracked_files": [], "policy_files": {}}):
            with self.assertRaises(IntentRefused):
                validate_context(context)


if __name__ == "__main__":
    unittest.main()
