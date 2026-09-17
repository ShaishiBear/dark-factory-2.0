"""Remote spending authority is source-bound, authenticated and consumed at most once."""
import base64
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from factory_kernel.agents import AgentRequest
from factory_kernel.canonical import canonical_bytes, sha256_value
from factory_kernel.credential_env import SCOPES, scoped_environment
from factory_kernel.execution_authority import ExecutionAuthority, POLICY_FILES, PROGRAMS, WORKFLOW_PATH
from factory_kernel.execution_client import ExecutionClient
from factory_kernel.execution_exchange import ExecutionExchange, ExecutionProtocol
from factory_kernel.frontdoor_http import FrontDoorApplication
from factory_kernel.frontdoor_intent import IntentRefused
from factory_kernel.programme import ACTIVE_PATH
from factory_kernel.providers import ClaudeCliProvider
from factory_kernel.publication_currency import CurrencyProtocol
from factory_kernel.publication_source import observe_publication_source
from factory_kernel import publication_policy as policy
from tests.factory import test_execution_budget as budget_tests
from tests.factory.test_exploration_repository import blob_oid

ROOT = Path(__file__).resolve().parents[2]


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.case = budget_tests.BudgetTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.approve()
        self.source = observe_publication_source(self.case.github)
        self.now = 1000
        self.protocol = ExecutionProtocol(b"x" * 32, repository=self.case.store.repository,
                                         project="citations", clock=lambda: self.now)
        self.observer = Mock(side_effect=lambda call, phase: deepcopy(self.source))
        self.service = ExecutionExchange(self.case.budget, "citations", self.case.owner, self.observer)
        self.call = {"id": "a" * 32, "run_id": 42, "run_attempt": 1, "source_sha": self.source["main_sha"],
            "programme_sha256": self.case.programme.sha256, "role": "plan", "microusd": 1_000_000,
            "request_sha256": "b" * 64}

    def request(self, phase="reserve", payload=None):
        return self.protocol.challenge(phase, self.call, payload or {})

    def exchange(self, phase="reserve", payload=None):
        request = self.request(phase, payload)
        response = self.protocol.answer(request, service=self.service)
        return self.protocol.verify(response, request)

    def start(self):
        reserved = self.exchange()
        return self.exchange("start", {"reservation_version": reserved["project_version"]})

    def test_fresh_call_reserves_consumes_once_and_never_refunds(self):
        reserved = self.exchange()
        self.assertEqual(reserved["status"], "reserved")
        started = self.exchange("start", {"reservation_version": reserved["project_version"]})
        self.assertEqual(started["status"], "start-once")
        replay = self.exchange("start", {"reservation_version": reserved["project_version"]})
        self.assertEqual(replay["status"], "already-started")
        self.assertEqual(self.exchange()["status"], "already-reserved")
        self.exchange("observe", {"reported_microusd": 1, "outcome": "returned"})
        state = self.case.budget.snapshot("citations", principal=self.case.owner)
        self.assertEqual(state["calls"], 1)
        self.assertEqual(state["reserved_microusd"], 1_000_000)
        self.assertEqual(state["status"], "available")
        self.assertIsNotNone(state["reservations"][reserved["reservation_id"]]["started"])

    def test_forgery_reflection_and_other_protocol_refuse_before_state_reads(self):
        service = Mock()
        service.apply.return_value = {"status": "unexpected-effect"}
        altered = self.request()
        altered["payload"]["call"]["microusd"] = 1
        with self.assertRaisesRegex(IntentRefused, "authentication"):
            self.protocol.answer(altered, service=service)
        with self.assertRaisesRegex(IntentRefused, "authentication"):
            self.protocol.verify(self.request(), self.request())
        other = CurrencyProtocol(b"x" * 32, repository=self.protocol.repository, project="citations", clock=lambda: self.now)
        with self.assertRaisesRegex(IntentRefused, "authentication"):
            self.protocol.answer(other._seal(self.request()["payload"], b"request/"), service=service)
        service.apply.assert_not_called()

    def test_restart_and_competing_starters_cannot_issue_two_execution_grants(self):
        reserved = self.exchange()
        restarted = ExecutionExchange(self.case.budget, "citations", self.case.owner, self.observer)
        def start():
            request = self.request("start", {"reservation_version": reserved["project_version"]})
            response = self.protocol.answer(request, service=restarted)
            return self.protocol.verify(response, request)["status"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: start(), range(2)))
        self.assertEqual(sorted(results), ["already-started", "start-once"])

    def test_foreign_destination_unsupported_roles_reruns_and_extra_fields_refuse(self):
        for field, value in (("repository", "foreign/repo"), ("project", "foreign"), ("phase", "approve"), ("issued_at", True)):
            raw = self.request()["payload"]
            raw[field] = value
            with self.subTest(field=field), self.assertRaises(IntentRefused):
                self.protocol.answer(self.protocol._seal(raw, b"request/"), service=self.service)
        for field, value in (("role", "intent-proposer"), ("run_attempt", 2), ("run_attempt", True),
                             ("microusd", 12_000_001), ("run_id", True), ("source_sha", "main")):
            raw = self.request()["payload"]
            raw["call"][field] = value
            with self.subTest(field=field), self.assertRaises(IntentRefused):
                self.protocol.answer(self.protocol._seal(raw, b"request/"), service=self.service)
        self.observer.assert_not_called()

    def test_nonce_phase_and_freshness_bind_every_response(self):
        request = self.request()
        response = self.protocol.answer(request, service=self.service)
        with self.assertRaisesRegex(IntentRefused, "another challenge"):
            self.protocol.verify(response, self.request())
        self.now += 61
        with self.assertRaisesRegex(IntentRefused, "freshness"):
            self.protocol.verify(response, request)
        with self.assertRaisesRegex(IntentRefused, "freshness"):
            self.protocol.answer(request, service=self.service)

    def test_observation_outliving_challenge_cannot_reserve(self):
        def expired(call, phase):
            self.now += 61
            return deepcopy(self.source)
        self.observer.side_effect = expired
        with self.assertRaisesRegex(IntentRefused, "freshness"):
            self.exchange()
        self.assertEqual(self.case.budget.snapshot("citations", principal=self.case.owner)["calls"], 0)

    def test_drifted_owner_decisions_stop_reserved_call_and_keep_its_charge(self):
        reserved = self.exchange()
        self.case.intent("add-exploration", {"wording": "Investigate another approach."})
        with self.assertRaisesRegex(IntentRefused, "stale project"):
            self.exchange("start", {"reservation_version": reserved["project_version"]})
        with self.assertRaisesRegex(IntentRefused, "cannot be started"):
            self.exchange("start", {"reservation_version": self.case.version()})
        state = self.case.budget.snapshot("citations", principal=self.case.owner)
        self.assertEqual(state["reserved_microusd"], 1_000_000)
        self.assertIsNone(state["reservations"][reserved["reservation_id"]]["started"])

    def test_reused_identity_unstarted_or_changed_observation_cannot_settle(self):
        reserved = self.exchange()
        with self.assertRaisesRegex(IntentRefused, "unstarted"):
            self.exchange("observe", {"reported_microusd": 0, "outcome": "returned"})
        original = deepcopy(self.call)
        self.call["request_sha256"] = "c" * 64
        with self.assertRaisesRegex(IntentRefused, "identity was reused"):
            self.exchange()
        self.call = original
        self.exchange("start", {"reservation_version": reserved["project_version"]})
        self.exchange("observe", {"reported_microusd": None, "outcome": "failed"})
        self.assertEqual(self.exchange("observe", {"reported_microusd": None, "outcome": "failed"})["status"], "already-observed")
        with self.assertRaisesRegex(IntentRefused, "cannot be changed"):
            self.exchange("observe", {"reported_microusd": 0, "outcome": "returned"})

    def client(self, opener):
        self.enterContext(patch.multiple(policy, REPOSITORY=self.protocol.repository, PROJECT="citations"))
        return ExecutionClient(self.protocol, {key: self.call[key] for key in
            ("run_id", "run_attempt", "source_sha", "programme_sha256")}, opener=opener)

    def transport(self, request, timeout):
        self.assertEqual(timeout, 30)
        self.assertEqual(request.full_url, policy.ORIGIN + "/api/execution-reservation")
        body = self.protocol.answer(json.loads(request.data), service=self.service)
        response = BytesIO(canonical_bytes(body))
        response.status = 200
        response.geturl = lambda: request.full_url
        return response

    def agent_request(self):
        return AgentRequest(role="plan", prompt="Private wording never sent over this exchange.",
                            cwd=str(self.case.directory), max_budget_usd=1)

    def test_client_runs_only_after_durable_start_and_sends_no_prompt(self):
        opener = Mock()
        opener.open.side_effect = self.transport
        client = self.client(opener)
        provider = Mock()
        def effect(*_args, **_kwargs):
            state = self.case.budget.snapshot("citations", principal=self.case.owner)
            self.assertIsNotNone(next(iter(state["reservations"].values()))["started"])
            return SimpleNamespace(cost_usd=0.1)
        provider.run.side_effect = effect
        client.run(provider, self.agent_request())
        provider.run.assert_called_once()
        self.assertEqual(opener.open.call_count, 3)
        self.assertTrue(all(b"Private wording" not in row.args[0].data for row in opener.open.call_args_list))

    def test_lost_start_response_never_runs_or_retries_and_leaves_unresolved_charge(self):
        opener = Mock()
        def lost(request, timeout):
            response = self.transport(request, timeout)
            if json.loads(request.data)["payload"]["phase"] == "start":
                raise TimeoutError("response lost after durable start")
            return response
        opener.open.side_effect = lost
        client = self.client(opener)
        provider = Mock()
        with self.assertRaises(TimeoutError):
            client.run(provider, self.agent_request())
        provider.run.assert_not_called()
        self.assertEqual(opener.open.call_count, 2)
        state = self.case.budget.snapshot("citations", principal=self.case.owner)
        self.assertEqual(state["status"], "unresolved-attempt")
        self.assertIsNotNone(next(iter(state["reservations"].values()))["started"])

    def test_retry_refusal_is_recorded_without_calling_restore_or_another_provider(self):
        opener = Mock()
        opener.open.side_effect = self.transport
        client = self.client(opener)
        provider, restore = Mock(), Mock()
        provider.run.side_effect = lambda *args, **kwargs: kwargs["before_retry"](2)
        with self.assertRaisesRegex(IntentRefused, "unobserved worker retry"):
            client.run(provider, self.agent_request(), before_retry=restore)
        restore.assert_not_called()
        provider.run.assert_called_once()
        self.assertEqual(self.case.budget.snapshot("citations", principal=self.case.owner)["status"], "unresolved-attempt")

    def test_client_rejects_foreign_response_origin_without_retry(self):
        opener = Mock()
        response = BytesIO(b"{}")
        response.status = 200
        response.geturl = lambda: "https://foreign.example"
        opener.open.return_value = response
        with self.assertRaisesRegex(IntentRefused, "origin"):
            self.client(opener).run(Mock(), self.agent_request())
        opener.open.assert_called_once()

    def test_client_removes_identity_before_source_reads_and_only_accepts_canonical_workflow(self):
        self.enterContext(patch.multiple(policy, REPOSITORY=self.protocol.repository, PROJECT="citations"))
        env = {"FRONTDOOR_AGE_IDENTITY": "fixture-only-key", "GITHUB_REPOSITORY": self.protocol.repository,
            "GITHUB_WORKFLOW_REF": self.protocol.repository + "/.github/workflows/dark-factory-worker.yml@refs/heads/main",
            "GITHUB_RUN_ATTEMPT": "1", "GITHUB_RUN_ID": "42", "GITHUB_SHA": self.source["main_sha"]}
        def source(_github):
            self.assertNotIn("FRONTDOOR_AGE_IDENTITY", os.environ)
            return deepcopy(self.source)
        with patch.dict(os.environ, env, clear=True), patch("factory_kernel.execution_client.key_from_identity", return_value=b"x" * 32) as derive:
            with patch("factory_kernel.execution_client.observe_publication_source", side_effect=source):
                client = ExecutionClient.from_environment(self.case.github)
            self.assertEqual(client.binding["programme_sha256"], self.case.programme.sha256)
            self.assertFalse(derive.call_args.args[0].exists())
        for change in ({"GITHUB_WORKFLOW_REF": "foreign"}, {"GITHUB_RUN_ATTEMPT": "2"}, {"FRONTDOOR_AGE_IDENTITY": ""}):
            with patch.dict(os.environ, {**env, **change}, clear=True):
                with patch("factory_kernel.execution_client.observe_publication_source") as observed:
                    with self.assertRaises(IntentRefused):
                        ExecutionClient.from_environment(self.case.github)
                    observed.assert_not_called()

    def test_remote_http_does_not_accept_owner_bearer_as_worker_provenance(self):
        app = FrontDoorApplication(store=self.case.store, project="citations", token="a" * 64,
            origin="https://factory.example", github=self.case.github, labels={}, app_login="factory[bot]", publication_key=b"x" * 32)
        app.execution_exchange = self.service
        app.execution_protocol = self.protocol
        def post(body, origin="https://factory.example", owner=True):
            raw = canonical_bytes(body)
            response = {}
            env = {"REQUEST_METHOD": "POST", "PATH_INFO": "/api/execution-reservation", "HTTP_HOST": "factory.example",
                "HTTP_ORIGIN": origin, "HTTP_AUTHORIZATION": "Bearer " + "a" * 64,
                "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(raw)), "wsgi.input": BytesIO(raw)}
            if not owner:
                env.pop("HTTP_AUTHORIZATION")
            b"".join(app(env, lambda status, headers: response.update(status=status)))
            return response["status"]
        self.assertEqual(post({"payload": self.request()["payload"], "mac": "0" * 64}), "409 Conflict")
        self.observer.assert_not_called()
        self.assertEqual(post(self.request(), origin=""), "403 Forbidden")
        self.assertEqual(post(self.request(), owner=False), "200 OK")

    def test_host_credential_is_absent_from_every_deterministic_and_model_child(self):
        source = {"PATH": "bin", "FRONTDOOR_AGE_IDENTITY": "private-key", "GH_TOKEN": "observation"}
        for scope in SCOPES:
            self.assertNotIn("FRONTDOOR_AGE_IDENTITY", scoped_environment(scope=scope, source=source))
        with self.assertRaises(ValueError):
            scoped_environment({"FRONTDOOR_AGE_IDENTITY": "injected"}, source=source)
        with patch.dict("os.environ", source, clear=True):
            self.assertNotIn("FRONTDOOR_AGE_IDENTITY", ClaudeCliProvider._worker_env({"FRONTDOOR_AGE_IDENTITY": "injected"}))


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        fixture = budget_tests.BudgetTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.repository = fixture.store.repository
        self.enterContext(patch.multiple(policy, REPOSITORY=self.repository, OWNER="owner", APP_LOGIN="factory[bot]"))
        self.call = {"run_id": 42, "run_attempt": 1, "source_sha": "a" * 40, "programme_sha256": fixture.programme.sha256}
        self.run = {"id": 42, "workflow_id": 9, "path": WORKFLOW_PATH, "head_branch": "main", "head_sha": "a" * 40,
            "run_attempt": 1, "event": "workflow_dispatch", "status": "in_progress", "head_repository": {"full_name": self.repository},
            "actor": {"login": "owner"}, "triggering_actor": {"login": "owner"}}
        self.jobs = {"total_count": 1, "jobs": [{"name": "dispatch", "status": "in_progress", "run_id": 42, "head_sha": "a" * 40}]}
        self.tree = {"sha": "b" * 40, "truncated": False, "tree": []}
        self.blobs = {}
        for name in PROGRAMS:
            self.put("factory_kernel/" + name, (ROOT / "factory_kernel" / name).read_bytes())
        for path in POLICY_FILES:
            self.put(path, (ROOT / path).read_bytes())
        self.put(ACTIVE_PATH, canonical_bytes(fixture.source))
        self.github = Mock(repository=self.repository)
        self.github.programme_issues.return_value = []
        self.github.json.side_effect = self.read
        self.authority = ExecutionAuthority(self.github)

    def put(self, name, raw):
        oid = blob_oid(raw)
        self.tree["tree"] = [row for row in self.tree["tree"] if row["path"] != name]
        self.tree["tree"].append({"path": name, "mode": "100644", "type": "blob", "sha": oid, "size": len(raw)})
        self.blobs[oid] = {"sha": oid, "size": len(raw), "encoding": "base64", "content": base64.b64encode(raw).decode()}

    def read(self, args, **kwargs):
        path = args[1].removeprefix("repos/" + self.repository)
        if path == "":
            value = {"full_name": self.repository, "private": False, "default_branch": "main"}
        elif path == "/branches/main":
            value = {"protected": True, "commit": {"sha": "a" * 40, "commit": {"tree": {"sha": "b" * 40}}}}
        elif path.startswith("/git/trees/"):
            value = self.tree
        elif path.startswith("/git/blobs/"):
            value = self.blobs[path.rsplit("/", 1)[-1]]
        elif path == "/actions/workflows/dark-factory-worker.yml":
            value = {"id": 9, "path": WORKFLOW_PATH, "state": "active"}
        elif path == "/actions/runs/42":
            value = self.run
        elif path == "/actions/runs/42/attempts/1/jobs?per_page=100":
            value = self.jobs
        else:
            raise AssertionError(path)
        return deepcopy(value)

    def test_exact_protected_source_and_running_canonical_job_are_required(self):
        result = self.authority(self.call, "reserve")
        self.assertEqual(result["main_sha"], "a" * 40)
        self.assertEqual(self.authority(self.call, "start"), result)

    def test_foreign_workflow_rerun_actor_source_status_and_repository_refuse(self):
        original = deepcopy(self.run)
        for change in ({"path": ".github/workflows/dark-factory-ci.yml"}, {"run_attempt": 2}, {"event": "pull_request"},
                       {"head_sha": "c" * 40}, {"status": "completed"}, {"actor": {"login": "foreign"}},
                       {"triggering_actor": {"login": "foreign"}}, {"head_repository": {"full_name": "foreign/repo"}}):
            self.run = {**original, **change}
            with self.subTest(change=change), self.assertRaises(IntentRefused):
                self.authority(self.call, "reserve")

    def test_incomplete_jobs_and_stale_host_authority_refuse(self):
        self.jobs["total_count"] = 2
        with self.assertRaises(IntentRefused):
            self.authority(self.call, "reserve")
        self.jobs["total_count"] = 1
        self.jobs["jobs"] = [self.jobs["jobs"][0]] * 2
        self.jobs["total_count"] = 2
        with self.assertRaises(IntentRefused):
            self.authority(self.call, "reserve")
        self.jobs["jobs"] = self.jobs["jobs"][:1]
        self.jobs["total_count"] = 1
        self.put("factory_kernel/execution_budget.py", b"# stale host must not mint new spending capabilities\n")
        with self.assertRaisesRegex(IntentRefused, "differs from protected source"):
            self.authority(self.call, "reserve")
        self.put("factory_kernel/execution_budget.py", (ROOT / "factory_kernel/execution_budget.py").read_bytes())
        # The project profile is closure policy: a host whose profile drifted from protected
        # main names a different repository/App/project and must refuse like drifted code.
        drifted = json.loads((ROOT / ".factory/project-profile.json").read_text(encoding="utf-8"))
        drifted["repository_id"] += 1
        self.put(".factory/project-profile.json", canonical_bytes(drifted))
        with self.assertRaisesRegex(IntentRefused, "policy differs from protected source"):
            self.authority(self.call, "reserve")

    def test_completed_exact_run_can_record_spending_without_opening_controls(self):
        self.run["status"] = "completed"
        self.jobs["jobs"][0]["status"] = "completed"
        self.put(".factory/programmes/execution-fence.json", b"{}")
        self.assertIsNone(self.authority(self.call, "observe"))
        with self.assertRaises(IntentRefused):
            self.authority(self.call, "start")


if __name__ == "__main__":
    unittest.main()
