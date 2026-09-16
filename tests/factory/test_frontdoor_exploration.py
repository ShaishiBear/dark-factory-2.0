"""Hosted adaptive effects require owner authority, durable bounds and explicit recovery."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from factory_kernel.agents import AgentRequest
from factory_kernel.frontdoor_exploration import FrontDoorExploration, DEFAULT_POLICY
from factory_kernel.frontdoor_hosted import validate_payload
from factory_kernel.frontdoor_intent import IntentRefused, IntentStore
from factory_kernel.exploration_repository import inspect_repository
from factory_kernel.hosted_exploration_call import SCHEMA, execute, request_limits
from factory_kernel.worker_policy import allowed_tools, effort
from tests.factory import test_exploration as fixture
from tests.factory import test_frontdoor_hosted as transport
from tests.factory import test_frontdoor_http as http
from tests.factory import test_exploration_repository as repository_fixture
from tests.factory.test_frontdoor_intent import OWNER, WORKER


def reply(action="stop", request=None):
    return {"action": action, "request": request or {"reason": "Representative workload evidence is unavailable."},
            "reason": "Investigate a decision-changing uncertainty."}


class ProtectedAdapterTests(unittest.TestCase):
    def setUp(self):
        self.fixture = repository_fixture.ProtectedRepositoryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = IntentStore(self.fixture.root / "private-state",
            repository=self.fixture.gh.repository, owner=OWNER.identity)
        self.provider = Mock(config=None)

    def test_configured_service_reads_real_protected_context_with_frozen_paths(self):
        paths = list(self.fixture.paths)
        expected = inspect_repository(self.fixture.root, paths)
        with patch("factory_kernel.frontdoor_exploration.require_clear_stop") as stop:
            service = FrontDoorExploration.protected(self.store, self.provider,
                self.fixture.gh, paths, app_login="factory[bot]")
            paths.append("../../untrusted.py")  # Caller mutation must not change configured scope.
            try:
                actual = service.engine._context()
            except IntentRefused as exc:
                self.fail("Valid configured protected context was refused: " + str(exc))
            self.assertEqual(actual, expected)
            self.assertGreaterEqual(stop.call_count, len(self.fixture.gh.calls))
            self.assertEqual(actual["proof_status"], "not-established")
        self.provider.run.assert_not_called()

    def test_configured_service_still_refuses_stop_and_unprotected_main(self):
        with patch("factory_kernel.frontdoor_exploration.require_clear_stop") as stop:
            service = FrontDoorExploration.protected(self.store, self.provider,
                self.fixture.gh, self.fixture.paths, app_login="factory[bot]")
            stop.side_effect = IntentRefused("stopped")
            with self.assertRaisesRegex(IntentRefused, "stopped"):
                service.engine._context()
            self.assertEqual(self.fixture.gh.calls, [])
            stop.side_effect = None
            self.fixture.gh.branch["protected"] = False
            with self.assertRaisesRegex(IntentRefused, "protected main"):
                service.engine._context()
        self.provider.run.assert_not_called()


class AdaptiveJobsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.ExplorationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.provider = Mock(config=None)
        self.provider.run.return_value = SimpleNamespace(structured_output=reply(), cost_usd=0.01, content="")
        self.service = self.make_service()

    def make_service(self):
        return FrontDoorExploration(self.fixture.store, self.provider, lambda: deepcopy(self.fixture.context),
            check_stop=self.fixture.stop, app_login="factory[bot]")

    def command(self, **request):
        return self.fixture.command(request or {"max_steps": 4, "max_usd_per_call": 1})

    def snapshot(self):
        return self.service.snapshot("citations", principal=OWNER)

    def start(self, command=None):
        return self.service.start("citations", command or self.command(), principal=OWNER)

    def join(self):
        with self.service._lock:
            threads = list(self.service._threads.values())
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive(), "bounded fixture job did not terminate")

    def test_durable_job_and_spend_precede_provider_then_stop_without_publication(self):
        def respond(request):
            state = self.snapshot()
            self.assertEqual(state["runs"][0]["status"], "running")
            self.assertEqual(state["budget"]["calls"], 1)
            self.assertEqual(state["budget"]["usd"], 1)
            self.assertEqual(request.allowed_tools, ())
            return SimpleNamespace(structured_output=reply(), cost_usd=0.01, content="")
        self.provider.run.side_effect = respond
        command = self.command()
        first = self.start(command)
        self.join()
        result = self.snapshot()
        self.assertEqual(result["runs"][0]["status"], "needs-evidence-or-owner-tradeoff")
        self.assertEqual(result["runs"][0]["steps_completed"], 1)
        self.assertEqual(result["sessions"][0]["handoffs"], [])
        self.assertFalse(result["proof_reuse_allowed"])
        with patch("factory_kernel.frontdoor_exploration.threading.Thread") as thread:
            self.assertEqual(self.start(command)["run_id"], first["run_id"])
            thread.assert_not_called()
        self.assertEqual(self.provider.run.call_count, 1)

    def test_live_job_prevents_second_start_and_manual_recovery(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def wait(request):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("fixture release missing")
            return SimpleNamespace(structured_output=reply(), cost_usd=0.01, content="")
        self.provider.run.side_effect = wait
        run = self.start()
        self.assertTrue(entered.wait(3))
        try:
            with self.assertRaises(IntentRefused):
                self.start()
            with self.assertRaises(IntentRefused):
                self.service.recover("citations", self.command(run_id=run["run_id"]), principal=OWNER)
            with self.assertRaises(IntentRefused):
                self.service.command("citations", "reopen", self.command(reason="Reconsider."), principal=OWNER)
            self.assertEqual(self.provider.run.call_count, 1)
        finally:
            release.set()
            self.join()

    def test_restart_never_dispatches_reserved_job_and_recovery_does_not_refund(self):
        with patch("factory_kernel.frontdoor_exploration.threading.Thread"):
            command = self.command()
            first = self.start(command)
        self.service = self.make_service()
        self.assertEqual(self.snapshot()["runs"][0]["observation"], "interrupted")
        self.start(command)
        self.provider.run.assert_not_called()
        with self.assertRaises(IntentRefused):
            self.start()
        recovered = self.service.recover("citations", self.command(run_id=first["run_id"]), principal=OWNER)
        self.assertEqual(recovered["runs"][0]["status"], "abandoned")
        self.assertEqual(recovered["budget"]["calls"], 0)
        self.start()
        self.join()
        self.assertEqual(self.provider.run.call_count, 1)

    def test_unknown_spend_freezes_followup_and_child_question(self):
        self.provider.run.return_value.cost_usd = None
        self.start()
        self.join()
        self.assertTrue(self.snapshot()["budget"]["uncertain"])
        with self.assertRaises(IntentRefused):
            self.start()
        self.fixture.open("child", parent="lookup")
        command = self.command()
        command["session_id"] = "child"
        with self.assertRaises(IntentRefused):
            self.start(command)
        self.assertEqual(self.provider.run.call_count, 1)

    def test_context_change_after_provider_leaves_charged_pending_result(self):
        def respond(request):
            self.fixture.context["commit"] = "b" * 40
            self.fixture.rehash()
            return SimpleNamespace(structured_output=reply(), cost_usd=0.01, content="")
        self.provider.run.side_effect = respond
        self.start()
        self.join()
        state = self.snapshot()
        self.assertEqual(state["runs"][0]["status"], "failed")
        self.assertEqual(state["budget"]["calls"], 1)
        self.assertEqual(list(state["sessions"][0]["reservations"].values())[0]["status"], "pending")
        self.assertEqual(state["sessions"][0]["observations"], [])

    def test_stop_before_job_has_no_event_or_effect(self):
        before = self.snapshot()["project_version"]
        self.fixture.stop.side_effect = IntentRefused("stop")
        with self.assertRaises(IntentRefused):
            self.start()
        self.assertEqual(self.snapshot()["project_version"], before)
        self.provider.run.assert_not_called()

    def test_stop_during_call_closes_job_but_cannot_apply_or_refund_result(self):
        def respond(request):
            self.fixture.stop.side_effect = IntentRefused("stop")
            return SimpleNamespace(structured_output=reply(), cost_usd=0.01, content="")
        self.provider.run.side_effect = respond
        self.start()
        self.join()
        state = self.snapshot()
        self.assertEqual(state["runs"][0]["status"], "failed")
        self.assertEqual(state["budget"]["usd"], 1)
        self.assertEqual(state["sessions"][0]["observations"], [])

    def test_steps_reach_unproven_handoff_without_authorizing_execution(self):
        outputs = [reply("add-candidates", {"claims": [fixture.claim("scan-assumption"), fixture.claim("index-assumption")],
            "candidates": [fixture.candidate("scan", "linear", 10, 20), fixture.candidate("index", "hash", 40, 60)]}),
            reply("experiment", self.fixture.experiment_request()),
            reply("recommend", {"stop_reason": "sufficient-support", "rationale": "Measured work changes preference.",
                  "remaining_uncertainty": ["Production latency unknown."], "next_useful_experiment": "Representative workload."}),
            reply("handoff", {"proposal": self.fixture.fixture.request["proposal"]})]
        self.provider.run.side_effect = [SimpleNamespace(structured_output=value, cost_usd=0.01, content="") for value in outputs]
        self.start()
        self.join()
        state = self.snapshot()
        self.assertEqual(state["runs"][0]["status"], "handoff-ready", state["runs"])
        self.assertEqual(state["budget"]["calls"], 4)
        self.assertEqual(state["sessions"][0]["handoffs"][0]["candidate_id"], "index")
        review = self.service.engine.prepare_handoff("citations", "lookup", principal=OWNER,
            expected_project_version=state["project_version"], include_strategy=True)
        self.assertEqual(review["input"]["version"], "1.1")
        self.assertEqual(review["activation"], "requires-protected-main-review")

    def test_owner_cas_and_boundaries_are_enforced_before_start(self):
        for command in [self.command(max_steps=0, max_usd_per_call=1), self.command(max_steps=True, max_usd_per_call=1),
                        self.command(max_steps=9, max_usd_per_call=1), self.command(max_steps=1, max_usd_per_call=2),
                        {**self.command(), "actor": "owner"}, {**self.command(), "expected_project_version": 0}]:
            with self.subTest(command=command), self.assertRaises(IntentRefused):
                self.start(command)
        with self.assertRaises(IntentRefused):
            self.service.start("citations", self.command(), principal=WORKER)
        with self.assertRaises(IntentRefused):
            self.service.snapshot("citations", principal=WORKER)
        self.provider.run.assert_not_called()

    def test_default_policy_is_valid_and_new_question_keeps_existing_budget(self):
        policy = self.snapshot()["default_policy"]
        self.assertEqual(policy["budget"], fixture.policy()["budget"])
        self.service.open("citations", self.fixture.command({"question": "Which architecture?",
            "parent_session": "lookup", "policy": policy}, "architecture"), principal=OWNER)
        self.assertEqual(len(self.snapshot()["sessions"]), 2)

    def test_observation_waits_for_local_transaction_without_losing_job_or_result(self):
        started = threading.Event()
        def observe():
            started.set()
            return self.snapshot()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.fixture.store._locked("citations"):
                observation = pool.submit(observe)
                self.assertTrue(started.wait(2))
                self.assertFalse(observation.done())
            self.assertTrue(observation.result(timeout=3)["ready"])
        self.start()
        with ThreadPoolExecutor(max_workers=3) as pool:
            observations = list(pool.map(lambda _: self.snapshot(), range(12)))
        self.join()
        self.assertTrue(all(row["ready"] for row in observations))
        self.assertEqual(self.snapshot()["runs"][0]["status"], "needs-evidence-or-owner-tradeoff")


class HostedExplorationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = transport.HostedTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.provider = self.fixture.provider
        self.provider.check_stop = Mock()
        role = "preflight-proposer"
        self.request = AgentRequest(role=role, prompt="bounded reasoning", cwd=".", allowed_tools=allowed_tools(role),
            effort=effort(role), max_turns=5, max_budget_usd=0.25, timeout_seconds=338)

    def test_encrypted_request_and_worker_preserve_narrowed_spend(self):
        self.provider.run(self.request)
        payload = self.fixture.payloads[0]
        self.assertEqual(payload["schema"], SCHEMA)
        self.assertEqual(payload["limits"]["max_usd"], 0.25)
        validate_payload(payload, repository=payload["repository"], request_id=payload["request_id"], head=payload["head"])
        provider = Mock()
        provider.run.return_value = SimpleNamespace(structured_output=reply(), cost_usd=0.01, model="fixture")
        output, telemetry = execute(payload, provider, Mock())
        request = provider.run.call_args.args[0]
        self.assertEqual(request.max_budget_usd, 0.25)
        self.assertEqual(request.allowed_tools, ())
        self.assertEqual(request.environment, {})
        self.assertEqual(output, reply())
        self.assertEqual(telemetry["cost_usd"], 0.01)

    def test_tools_overrides_and_overspend_fail_before_remote_observation(self):
        for changes in ({"allowed_tools": ("Bash",)}, {"environment": {"SECRET": "value"}}, {"model": "other"},
                        {"max_budget_usd": 2}, {"max_turns": 6}, {"timeout_seconds": 339}, {"effort": "low"}):
            with self.subTest(changes=changes), self.assertRaises(IntentRefused):
                self.provider.run(replace(self.request, **changes))
        self.fixture.github.json.assert_not_called()
        self.fixture.github.run.assert_not_called()

    def test_stop_before_dispatch_and_at_worker_prevents_paid_call(self):
        self.provider.check_stop.side_effect = [None, IntentRefused("stop")]
        with self.assertRaises(IntentRefused):
            self.provider.run(self.request)
        self.fixture.github.run.assert_not_called()
        worker = Mock()
        with self.assertRaises(IntentRefused):
            execute({"role": self.request.role, "prompt": "bounded", "limits": request_limits(self.request)},
                    worker, Mock(side_effect=IntentRefused("stop")))
        worker.run.assert_not_called()

    def test_exploration_requires_stop_observer_and_ambiguous_post_is_not_repeated(self):
        self.provider.check_stop = None
        with self.assertRaises(IntentRefused):
            self.provider.run(self.request)
        self.fixture.github.json.assert_not_called()
        self.provider.check_stop = Mock()
        self.fixture.behaviour = "uncertain"
        self.provider.run(self.request)
        self.assertEqual(len(self.fixture.dispatched), 1)

    def test_payload_role_schema_and_limits_cannot_be_forged(self):
        self.provider.run(self.request)
        original = self.fixture.payloads[0]
        for changes in ({"role": "implement"}, {"schema": "dark-factory/hosted-proposal-v1"},
                        {"limits": {"max_usd": 2, "max_turns": 5, "timeout_seconds": 338}}, {"tools": ["Bash"]}):
            with self.subTest(changes=changes), self.assertRaises(IntentRefused):
                validate_payload({**original, **changes}, repository=original["repository"],
                                 request_id=original["request_id"], head=original["head"])


class AdaptiveHTTPTests(unittest.TestCase):
    def setUp(self):
        self.fixture = http.FrontDoorHTTPTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_authentication_and_same_origin_precede_adaptive_dispatch(self):
        service = Mock()
        self.fixture.app.explorer = service
        for path in ("open", "start", "recover", "reopen", "abandon"):
            self.assertEqual(self.fixture.call("/api/exploration/" + path, body={"actor": "owner"},
                HTTP_AUTHORIZATION="")["status"], "401 Unauthorized")
            self.assertEqual(self.fixture.call("/api/exploration/" + path, body={},
                HTTP_ORIGIN="https://evil.example")["status"], "403 Forbidden")
        self.assertEqual(self.fixture.call("/api/exploration", HTTP_AUTHORIZATION="")["status"], "401 Unauthorized")
        self.assertEqual(service.mock_calls, [])

    def test_disabled_by_default_and_server_principal_used_for_bounded_start(self):
        self.assertEqual(self.fixture.call("/api/exploration/start", body={})["status"], "503 Service Unavailable")
        service = Mock()
        service.start.return_value = {"state": "recorded"}
        self.fixture.app.explorer = service
        self.assertEqual(self.fixture.call("/api/exploration/start", body={"request": {}})["status"], "202 Accepted")
        self.assertEqual(service.start.call_args.kwargs["principal"], OWNER)
        self.assertEqual(self.fixture.call("/api/exploration/dispatch", body={})["status"], "404 Not Found")


if __name__ == "__main__":
    unittest.main()
