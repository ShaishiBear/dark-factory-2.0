"""Cumulative spending authority: interruption, generation changes and trust boundaries."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from factory_kernel.agents import AgentRequest
from factory_kernel.canonical import sha256_value
from factory_kernel.decision_history import explain_history
from factory_kernel.execution_budget import ExecutionBudget, microusd, observe_opening
from factory_kernel.frontdoor_http import FrontDoorApplication
from factory_kernel.frontdoor_intent import IntentRefused, IntentStore, Principal
from factory_kernel.programme import compile_programme
from factory_kernel.publication_source import observe_publication_source
from tests.factory import test_programme_turnover as turnover


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.github = turnover.TurnoverGitHub()
        self.source = deepcopy(self.github.source)
        self.programme = compile_programme(self.source, repository=turnover.REPO)
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.store = IntentStore(self.directory, repository=turnover.REPO, owner="owner")
        self.budget = ExecutionBudget(self.store)
        self.owner = Principal("owner", "owner")
        self.intent("record-intent", {"wording": "Improve citations."})
        self.intent("propose-spec", {"spec": self.source["spec"], "assumptions": [],
                                    "open_questions": [], "technical_questions": []})
        self.intent("approve-spec", {"draft_version": 2, "spec_sha256": sha256_value(self.source["spec"]),
                                     "wording": "Approve this scope."})

    def version(self):
        return self.store.snapshot("citations", principal=self.owner)["project_version"]

    def intent(self, operation, payload):
        version = self.version()
        return self.store.execute("citations", {"idempotency_key": "intent-" + str(version),
            "expected_project_version": version, "operation": operation, "payload": payload}, principal=self.owner)

    def command(self, key, request):
        return {"idempotency_key": key, "expected_project_version": self.version(), "request": request}

    def approve(self, *, dollars=3_000_000, calls=3, fresh=True):
        self.github.source = None if fresh else deepcopy(self.source)
        command = self.command("allowance-" + str(self.version()), {"spec_sha256": sha256_value(self.source["spec"]),
                                             "limit_microusd": dollars, "max_calls": calls})
        result = self.budget.approve("citations", command, principal=self.owner,
                                     github=self.github, app_login=turnover.BOT)
        self.github.source = deepcopy(self.source)
        return result

    def reserve(self, key="first", *, amount=1_000_000, attempt=1, execution="build", programme=None):
        return self.budget.reserve("citations", self.command(key, {
            "programme_sha256": programme or self.programme.sha256, "role": "plan", "microusd": amount,
            "attempt": attempt, "execution_id": execution}), principal=self.owner,
            observe=lambda: observe_publication_source(self.github))

    def observe(self, key="first", *, reported=100_000):
        return self.budget.observe("citations", self.command("observed-" + key, {
            "id": key, "reported_microusd": reported, "outcome": "returned"}), principal=self.owner)

    def test_no_implicit_allowance_and_unknown_legacy_spend_never_becomes_zero_capacity(self):
        with self.assertRaisesRegex(IntentRefused, "not-approved"):
            self.reserve()
        state = self.approve(fresh=False)
        self.assertEqual(state["status"], "historical-spend-unknown")
        with self.assertRaisesRegex(IntentRefused, "historical-spend-unknown"):
            self.reserve()
        self.assertEqual(self.version(), 4)

    def test_opening_requires_empty_protected_source_complete_unchanged_inventory(self):
        self.github.source = None
        with patch.object(self.github, "programme_issues", return_value=[{
                "user": {"login": turnover.BOT}, "body": f"Specification: {self.source['spec']['id']} v1"}]):
            self.assertEqual(observe_opening(self.github, self.source["spec"], turnover.BOT)["status"], "unknown")
        source = observe_publication_source(self.github)
        with patch("factory_kernel.execution_budget.observe_publication_source", return_value=source):
            with patch.object(self.github, "programme_issues", side_effect=[[], [{"number": 1}]]):
                with self.assertRaisesRegex(IntentRefused, "opening changed"):
                    observe_opening(self.github, self.source["spec"], turnover.BOT)
            with patch.object(self.github, "programme_issues", return_value=None):
                with self.assertRaises(IntentRefused):
                    observe_opening(self.github, self.source["spec"], turnover.BOT)

    def test_owner_authentication_scope_and_version_precede_allowance(self):
        command = self.command("allowance", {"spec_sha256": sha256_value(self.source["spec"]),
                                             "limit_microusd": 1_000_000, "max_calls": 1})
        for principal in (Principal("intruder", "owner"), Principal("model", "proposal"), Principal("worker", "worker")):
            with self.assertRaises(IntentRefused):
                self.budget.approve("citations", command, principal=principal, github=self.github, app_login=turnover.BOT)
        with patch("factory_kernel.execution_budget.observe_opening") as read:
            command["request"]["spec_sha256"] = "a" * 64
            with self.assertRaisesRegex(IntentRefused, "approved scope"):
                self.budget.approve("citations", command, principal=self.owner, github=self.github, app_login=turnover.BOT)
            read.assert_not_called()

    def test_amounts_are_integer_finite_positive_and_round_up_without_float_capacity(self):
        self.assertEqual(microusd("0.0000001"), 1)
        self.assertEqual(microusd(0.1), 100000)
        for amount in (True, None, "NaN", "Infinity", "-1", [], "not-money"):
            with self.assertRaises(IntentRefused):
                microusd(amount)
        self.github.source = None
        for amount in (True, 0, -1, 1.1, "100", float("nan")):
            with self.assertRaises(IntentRefused):
                self.approve(dollars=amount)
        self.assertEqual(self.version(), 3)

    def test_reservation_is_durable_before_effect_and_unknown_spend_blocks_restart(self):
        self.approve()
        self.reserve()
        restarted = ExecutionBudget(IntentStore(self.directory, repository=turnover.REPO, owner="owner"))
        state = restarted.snapshot("citations", principal=self.owner)
        self.assertEqual(state["reserved_microusd"], 1_000_000)
        self.assertEqual(state["status"], "unresolved-attempt")
        with self.assertRaisesRegex(IntentRefused, "unresolved-attempt"):
            self.reserve("second", attempt=2)
        self.observe(reported=None)
        self.assertEqual(self.budget.snapshot("citations", principal=self.owner)["status"], "unresolved-attempt")

    def test_lower_actual_cost_does_not_refund_and_changed_programme_keeps_same_cap(self):
        self.approve(dollars=2_000_000)
        self.reserve()
        self.observe(reported=1)
        replacement = deepcopy(self.source)
        replacement["proposal"]["items"][1]["id"] = "new-approach"
        self.github.source = replacement
        replacement_sha = compile_programme(replacement, repository=turnover.REPO).sha256
        result = self.reserve("second", programme=replacement_sha, execution="new-issue")
        self.assertEqual(result["state"]["reserved_microusd"], 2_000_000)
        self.observe("second", reported=0)
        with self.assertRaisesRegex(IntentRefused, "exhausted"):
            self.reserve("third", programme=replacement_sha, execution="third-issue")
        with self.assertRaisesRegex(IntentRefused, "already has"):
            self.approve(dollars=100_000_000)
        self.assertFalse(result["state"]["refund_allowed"])
        self.assertFalse(result["state"]["reset_allowed"])

    def test_dollar_and_call_limits_are_separate_and_overruns_stop_further_calls(self):
        self.approve(dollars=1_500_000, calls=2)
        self.reserve()
        self.observe()
        with self.assertRaisesRegex(IntentRefused, "exceeds cumulative"):
            self.reserve("second", attempt=2)
        self.reserve("second", amount=1, attempt=2)
        self.observe("second", reported=2)
        with self.assertRaisesRegex(IntentRefused, "overrun"):
            self.reserve("third", amount=1, attempt=3)
        self.assertEqual(self.budget.snapshot("citations", principal=self.owner)["calls"], 2)

    def test_idempotency_replay_is_history_not_another_spending_capability(self):
        self.approve()
        command = self.command("first", {"programme_sha256": self.programme.sha256, "role": "plan",
            "microusd": 1_000_000, "attempt": 1, "execution_id": "build"})
        first = self.budget.reserve("citations", command, principal=self.owner,
                                    observe=lambda: observe_publication_source(self.github))
        replay = self.budget.reserve("citations", command, principal=self.owner,
                                     observe=lambda: observe_publication_source(self.github))
        self.assertTrue(first["execute"])
        self.assertFalse(replay["execute"])
        self.assertEqual(replay["state"]["calls"], 1)
        self.observe()
        with self.assertRaisesRegex(IntentRefused, "already reserved"):
            self.reserve("different-key")
        command["request"]["microusd"] = 1
        with self.assertRaisesRegex(IntentRefused, "already used"):
            self.budget.reserve("citations", command, principal=self.owner,
                                 observe=lambda: observe_publication_source(self.github))

    def test_call_limit_exhausts_even_when_dollar_allowance_remains(self):
        self.approve(calls=1)
        self.reserve(amount=1)
        self.observe(reported=0)
        with self.assertRaisesRegex(IntentRefused, "exhausted"):
            self.reserve("second", amount=1, attempt=2)

    def test_allowance_replay_and_scope_revision_preserve_original_project_cap(self):
        self.approve()
        with self.store._locked("citations") as path:
            command = self.store._read(path)[-1]["command"]["payload"]["request"]
        before = self.version()
        self.budget.approve("citations", command, principal=self.owner, github=self.github, app_login=turnover.BOT)
        self.assertEqual(self.version(), before)
        self.reserve()
        self.observe()
        revision = deepcopy(self.source)
        revision["spec"]["revision"] = 2
        revision["proposal"]["spec_sha256"] = sha256_value(revision["spec"])
        self.intent("propose-spec", {"spec": revision["spec"], "assumptions": [],
                                    "open_questions": [], "technical_questions": []})
        self.intent("approve-spec", {"draft_version": self.version(), "spec_sha256": sha256_value(revision["spec"]),
                                     "wording": "Approve the next revision."})
        self.github.source = revision
        second = compile_programme(revision, repository=turnover.REPO)
        result = self.reserve("second", programme=second.sha256, attempt=2)
        self.assertEqual(result["state"]["reserved_microusd"], 2_000_000)
        self.assertEqual(result["state"]["allowance"]["limit_microusd"], 3_000_000)

    def test_concurrent_reservations_cannot_both_claim_last_capacity(self):
        self.approve(dollars=1_000_000)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self.reserve, key, execution=key) for key in ("one", "two")]
        results = []
        for future in futures:
            try:
                results.append(future.result())
            except IntentRefused:
                pass
        self.assertEqual(len(results), 1)
        self.assertEqual(self.budget.snapshot("citations", principal=self.owner)["calls"], 1)

    def test_stale_programme_source_stop_and_unapproved_intent_refuse_before_reservation(self):
        self.approve()
        source = observe_publication_source(self.github)
        command = self.command("first", {"programme_sha256": self.programme.sha256, "role": "plan",
            "microusd": 1, "attempt": 1, "execution_id": "build"})
        for changed in ({**source, "protected": False}, {**source, "stop": {"state": "stopped", "issues": [1]}}):
            with self.assertRaises(IntentRefused):
                self.budget.reserve("citations", command, principal=self.owner, observe=lambda: changed)
        with self.assertRaisesRegex(IntentRefused, "source changed"):
            self.budget.reserve("citations", command, principal=self.owner,
                observe=Mock(side_effect=[source, {**source, "main_sha": "e" * 40}]))
        self.intent("record-intent", {"wording": "Changed outcome."})
        with self.assertRaisesRegex(IntentRefused, "new intent"):
            self.reserve()
        self.assertEqual(self.budget.snapshot("citations", principal=self.owner)["calls"], 0)

    def test_local_executor_charges_before_provider_and_records_lower_cost_without_refund(self):
        self.approve()
        def provider_call(*_args, **_kwargs):
            state = ExecutionBudget(self.store).snapshot("citations", principal=self.owner)
            self.assertEqual(state["reserved_microusd"], 1_000_000)
            self.assertEqual(state["status"], "unresolved-attempt")
            return SimpleNamespace(cost_usd=0.1)
        provider = Mock()
        provider.run.side_effect = provider_call
        self.run_attempt(provider)
        state = self.budget.snapshot("citations", principal=self.owner)
        self.assertEqual(state["reserved_microusd"], 1_000_000)
        self.assertEqual(state["status"], "available")
        self.assertEqual(state["reservations"]["local"]["observation"]["reported_microusd"], 100_000)

    def run_attempt(self, provider, *, observe=None):
        return self.budget.run("citations", principal=self.owner, provider=provider,
            request=AgentRequest(role="plan", prompt="Plan the work", cwd=str(self.directory), max_budget_usd=1),
            observe_source=observe or (lambda: observe_publication_source(self.github)),
            programme_sha256=self.programme.sha256, execution_id="build", attempt=1, reservation_id="local")

    def test_provider_timeout_and_internal_retry_keep_reservation_and_block_more_spend(self):
        self.approve()
        def retry(*_args, **kwargs):
            kwargs["before_retry"](2)
            self.fail("provider started unreserved retry")
        provider = Mock()
        provider.run.side_effect = retry
        with self.assertRaisesRegex(IntentRefused, "unresolved spend"):
            self.run_attempt(provider)
        self.assertEqual(self.budget.snapshot("citations", principal=self.owner)["status"], "unresolved-attempt")
        with self.assertRaises(IntentRefused):
            self.run_attempt(provider)
        provider.run.assert_called_once()

    def test_failed_durable_write_cannot_start_a_provider(self):
        self.approve()
        provider = Mock()
        with patch.object(self.store, "_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.run_attempt(provider)
        provider.run.assert_not_called()
        self.assertEqual(self.budget.snapshot("citations", principal=self.owner)["calls"], 0)

    def test_late_source_change_keeps_reservation_and_prevents_paid_effect(self):
        self.approve()
        source = observe_publication_source(self.github)
        provider = Mock()
        with self.assertRaisesRegex(IntentRefused, "changed after reservation"):
            self.run_attempt(provider, observe=Mock(side_effect=[source, source, {**source, "main_sha": "e" * 40}]))
        provider.run.assert_not_called()
        self.assertEqual(self.budget.snapshot("citations", principal=self.owner)["calls"], 1)

    def test_late_owner_or_source_change_keeps_reservation_but_never_calls_provider(self):
        self.approve()
        source = observe_publication_source(self.github)
        calls = 0
        def changing():
            nonlocal calls
            calls += 1
            if calls == 3:
                self.intent("add-exploration", {"wording": "Investigate another approach before continuing."})
            return deepcopy(source)
        provider = Mock()
        with self.assertRaises(IntentRefused):
            self.run_attempt(provider, observe=changing)
        provider.run.assert_not_called()
        self.assertEqual(self.budget.snapshot("citations", principal=self.owner)["calls"], 1)

    def test_budget_history_is_owner_visible_and_intake_cannot_forge_reservations(self):
        self.approve()
        self.reserve()
        result = explain_history(self.store, "citations", principal=self.owner)
        self.assertEqual(result["execution_budget"]["calls"], 1)
        self.assertEqual(result["events"][-1]["proof_status"], "not-established")
        with self.assertRaisesRegex(IntentRefused, "unknown intake"):
            self.intent("execution-budget-event", {"kind": "approved", "data": {}})

    def test_http_approval_requires_owner_origin_and_never_exposes_reservation_route(self):
        app = FrontDoorApplication(store=self.store, project="citations", token="a" * 64,
            origin="https://factory.example", github=self.github, labels={}, app_login=turnover.BOT)
        command = self.command("allowance", {"spec_sha256": sha256_value(self.source["spec"]),
                                             "limit_microusd": 1_000_000, "max_calls": 1})
        raw = json.dumps(command).encode()
        def call(path, token="a" * 64, origin="https://factory.example"):
            result = {}
            environ = {"REQUEST_METHOD": "POST", "PATH_INFO": path, "HTTP_HOST": "factory.example",
                "HTTP_ORIGIN": origin, "HTTP_AUTHORIZATION": "Bearer " + token, "CONTENT_TYPE": "application/json",
                "CONTENT_LENGTH": str(len(raw)), "wsgi.input": BytesIO(raw)}
            body = b"".join(app(environ, lambda status, headers: result.update(status=status)))
            return result["status"], json.loads(body)
        self.assertEqual(call("/api/execution-budget", token="wrong")[0], "401 Unauthorized")
        self.assertEqual(call("/api/execution-budget", origin="https://foreign.example")[0], "403 Forbidden")
        status, result = call("/api/execution-budget")
        self.assertEqual(status, "200 OK")
        self.assertEqual(result["status"], "historical-spend-unknown")
        self.assertEqual(call("/api/execution-budget/reserve")[0], "404 Not Found")
        self.assertEqual(result["enforcement"], "local-executor-only-hosted-worker-not-connected")


if __name__ == "__main__":
    unittest.main()
