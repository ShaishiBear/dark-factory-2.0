"""Encrypted proposal transport preserves local scope authority and one-shot spending."""
from copy import deepcopy
from dataclasses import replace
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from factory_kernel.canonical import sha256_value
from factory_kernel.frontdoor_hosted import (
    AgeCipher, HostedPreparationProvider, MAX_CIPHERTEXT, WORKFLOW, WORKFLOW_PATH,
    validate_payload, verify_run,
)
from factory_kernel.frontdoor_hosted_worker import authorize_job, refuse_replay
from factory_kernel import frontdoor_hosted_worker as worker
from factory_kernel.frontdoor_intent import IntentRefused
from tests.factory import test_frontdoor_prepare as preparation_tests
from tests.factory.test_frontdoor_intent import OWNER


class JsonFixtureCipher:
    """Transport tests inspect content; the separate age test proves real encryption."""
    def encrypt(self, value):
        return json.dumps(value)

    def decrypt(self, value):
        return json.loads(value)


def run_record(payload, run_id=42):
    return {"id": run_id, "repository": {"full_name": payload["repository"]},
            "actor": {"login": OWNER.identity}, "triggering_actor": {"login": OWNER.identity},
            "event": "workflow_dispatch", "head_branch": "main", "head_sha": payload["head"],
            "run_attempt": 1, "workflow_id": 7, "path": WORKFLOW_PATH,
            "display_title": "frontdoor-" + payload["request_id"],
            "status": "completed", "conclusion": "success"}


class HostedTests(unittest.TestCase):
    def setUp(self):
        self.fixture = preparation_tests.PreparationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store
        self.github = Mock()
        self.payloads, self.dispatched = [], []
        self.behaviour = "success"
        self.result_mutation = lambda value: value
        self.provider = HostedPreparationProvider(self.store, self.github, JsonFixtureCipher(),
                                                   clock=lambda: 0, sleep=lambda _: None)
        self.fixture.preparer.provider = self.provider
        self.github.json.side_effect = self.read
        self.github.run.side_effect = self.effect

    def effect(self, args):
        if args[:2] == ["workflow", "run"]:
            self.assertEqual(args[2], WORKFLOW)
            self.assertEqual(args[5:7], ["--ref", "main"])
            payload = json.loads(next(value.removeprefix("ciphertext=") for value in args if value.startswith("ciphertext=")))
            records = list(self.provider.records.directory.glob("*.json"))
            reserved = [json.loads(path.read_text()) for path in records]
            self.assertTrue(any(row["request_id"] == payload["request_id"] and row["state"] == "dispatch-reserved"
                                for row in reserved), "dispatch must follow a durable reservation")
            self.payloads.append(payload)
            self.dispatched.append(args)
            if self.behaviour == "uncertain":
                raise RuntimeError("network failed after POST")
            if self.behaviour == "stale":
                self.store.execute("citations", {"operation": "record-intent", "idempotency_key": "clarify",
                    "expected_project_version": 1, "payload": {"wording": "Keep the new scope current."}}, principal=OWNER)
                self.behaviour = "success"
            return ""
        self.assertEqual(args[:2], ["run", "download"])
        payload = self.payloads[-1]
        output = preparation_tests.proposal() if payload["role"] == "intent-proposer" else preparation_tests.audit()
        result = {"request_sha256": sha256_value(payload), "run_id": 42, "head": payload["head"],
                  "output": output, "telemetry": {"model": "fixture", "cost_usd": 0.01}}
        self.result_mutation(result)
        (Path(args[-1]) / "result.age.b64").write_text(json.dumps(result))
        return ""

    def read(self, args):
        route = args[1]
        if route.endswith("git/ref/heads/main"):
            return {"object": {"sha": "a" * 40}}
        if route.endswith("/" + WORKFLOW):
            return {"id": 7, "path": WORKFLOW_PATH, "state": "active"}
        if "/runs?" in route:
            run = run_record(self.payloads[-1])
            if self.behaviour == "wrong-owner":
                run["actor"]["login"] = "outsider"
            if self.behaviour == "rerun":
                run["run_attempt"] = 2
            if self.behaviour == "failed":
                run["conclusion"] = "failure"
            return {"workflow_runs": [run, deepcopy(run)] if self.behaviour == "duplicate" else [run]}
        if route.endswith("/artifacts"):
            if self.behaviour == "missing":
                return {"artifacts": []}
            return {"artifacts": [{"name": "frontdoor-" + self.payloads[-1]["request_id"],
                                    "expired": False, "size_in_bytes": 1000}]}
        raise AssertionError(route)

    def test_complete_hosted_draft_is_locally_audited_and_never_approved(self):
        result = self.fixture.prepare()
        self.assertEqual(result["state"], "ready-for-review", result)
        self.assertEqual([p["role"] for p in self.payloads], ["intent-proposer", "intent-auditor"])
        self.assertEqual(self.fixture.snapshot()["approvals"], [])
        self.assertEqual(self.fixture.prepare(), result)
        self.assertEqual(len(self.dispatched), 2)
        self.assertEqual(len(list(self.provider.records.directory.glob("*.json"))), 2)

    def test_ambiguous_post_is_observed_without_repeating_it(self):
        self.behaviour = "uncertain"
        self.assertEqual(self.fixture.prepare()["state"], "ready-for-review")
        self.assertEqual(len(self.dispatched), 2, "one POST for each of the two distinct roles")

    def test_bad_runs_and_missing_artifacts_fail_without_retry_or_approval(self):
        for behaviour in ("wrong-owner", "rerun", "failed", "duplicate", "missing"):
            with self.subTest(behaviour=behaviour):
                case = HostedTests()
                case.setUp()
                try:
                    case.behaviour = behaviour
                    result = case.fixture.prepare()
                    self.assertEqual(result["state"], "failed")
                    self.assertEqual(case.fixture.prepare(), result)
                    self.assertEqual(len(case.dispatched), 1)
                    self.assertIsNone(case.fixture.snapshot()["draft"])
                finally:
                    case.doCleanups()

    def test_result_request_hash_run_and_head_are_independently_bound(self):
        for field, value in (("request_sha256", "b" * 64), ("run_id", 43), ("head", "b" * 40)):
            with self.subTest(field=field):
                self.result_mutation = lambda result: result.update({field: value})
                payload = {"request_id": "d" * 32, "head": "a" * 40, "role": "intent-proposer"}
                self.payloads = [payload]
                with self.assertRaises(IntentRefused):
                    self.provider._result(42, payload)

    def test_owner_clarification_during_remote_calls_prevents_stale_publication(self):
        self.behaviour = "stale"
        self.assertEqual(self.fixture.prepare()["state"], "failed")
        self.assertIsNone(self.fixture.snapshot()["draft"])
        self.assertEqual(self.fixture.snapshot()["ledger"][-1]["wording"], "Keep the new scope current.")

    def test_transport_timeout_has_no_second_post(self):
        ticks = iter([0, 601])
        self.provider.clock = lambda: next(ticks)
        result = self.fixture.prepare()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(self.fixture.prepare(), result)
        self.assertEqual(len(self.dispatched), 1)

    def test_oversize_ciphertext_never_dispatches(self):
        self.provider.cipher.encrypt = lambda _: "x" * (MAX_CIPHERTEXT + 1)
        self.assertEqual(self.fixture.prepare()["state"], "failed")
        self.assertEqual(self.dispatched, [])

    def test_transport_refuses_extra_tools_environment_model_and_budget(self):
        self.fixture.preparer.provider = self.fixture.provider
        self.fixture.prepare()
        request = self.fixture.requests[0]
        for overrides in ({"allowed_tools": ("Read",)}, {"environment": {"GH_TOKEN": "fixture"}},
                          {"role": "implement"}, {"model": "other"}, {"max_turns": 6},
                          {"max_budget_usd": 2}, {"timeout_seconds": 339}, {"effort": "high"}):
            with self.subTest(overrides=overrides), self.assertRaises(IntentRefused):
                self.provider.run(replace(request, **overrides))
        self.github.json.assert_not_called()


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"schema": "dark-factory/hosted-proposal-v1", "request_id": "a" * 32,
                        "repository": "ShaishiBear/dark-factory-2.0", "head": "b" * 40,
                        "role": "intent-proposer", "prompt": "Private scope", "issued_at": 1000}
        self.environ = {"GITHUB_REPOSITORY": self.payload["repository"], "GITHUB_REPOSITORY_OWNER": "ShaishiBear",
                        "GITHUB_ACTOR": "ShaishiBear", "GITHUB_TRIGGERING_ACTOR": "ShaishiBear",
                        "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "workflow_dispatch",
                        "GITHUB_RUN_ATTEMPT": "1", "GITHUB_RUN_ID": "42", "GITHUB_SHA": self.payload["head"],
                        "GITHUB_WORKFLOW_REF": f"{self.payload['repository']}/{WORKFLOW_PATH}@refs/heads/main"}
        self.event = {"inputs": {"request_id": self.payload["request_id"]}}
        self.run = run_record(self.payload)
        self.run["actor"] = self.run["triggering_actor"] = {"login": "ShaishiBear"}
        self.github = Mock()
        self.github.json.side_effect = lambda args: {"id": 7} if args[1].endswith(WORKFLOW) else self.run

    def test_only_first_owner_dispatch_on_main_can_reach_worker(self):
        self.assertEqual(authorize_job(self.environ, self.event, self.github)[-1], 42)
        for key, value in (("GITHUB_ACTOR", "outsider"), ("GITHUB_TRIGGERING_ACTOR", "outsider"),
                           ("GITHUB_REF", "refs/heads/candidate"), ("GITHUB_RUN_ATTEMPT", "2"),
                           ("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_REPOSITORY", "other/repo"),
                           ("GITHUB_WORKFLOW_REF", "untrusted")):
            with self.subTest(key=key), self.assertRaises(IntentRefused):
                authorize_job({**self.environ, key: value}, self.event, self.github)

    def test_run_provenance_checks_each_platform_binding(self):
        fields = {"repository": {"full_name": "other/repo"}, "actor": {"login": "outsider"},
                  "triggering_actor": {"login": "outsider"}, "event": "push", "head_branch": "candidate",
                  "head_sha": "c" * 40, "run_attempt": 2, "workflow_id": 8, "path": "other.yml",
                  "display_title": "frontdoor-" + "c" * 32}
        for field, value in fields.items():
            with self.subTest(field=field), self.assertRaises(IntentRefused):
                verify_run({**self.run, field: value}, repository=self.payload["repository"], owner="ShaishiBear",
                           request_id=self.payload["request_id"], head=self.payload["head"], workflow_id=7)

    def test_payload_cannot_change_scope_of_hosted_capability(self):
        args = {"repository": self.payload["repository"], "request_id": self.payload["request_id"], "head": self.payload["head"]}
        self.assertEqual(validate_payload(self.payload, **args), self.payload)
        for change in ({"role": "implement"}, {"head": "c" * 40}, {"request_id": "d" * 32},
                       {"repository": "other/repo"}, {"tools": ["Bash"]}, {"budget": 20}):
            with self.subTest(change=change), self.assertRaises(IntentRefused):
                validate_payload({**self.payload, **change}, **args)

    def test_duplicate_expired_or_incomplete_listing_refuses_paid_call(self):
        good = {"total_count": 1, "workflow_runs": [self.run]}
        self.github.json.side_effect = None
        self.github.json.return_value = good
        refuse_replay(self.github, self.payload, 42, now=1001)
        for result, now in ((good, 999), (good, 1901), ({**good, "total_count": 100}, 1001),
                            ({"total_count": 0, "workflow_runs": []}, 1001),
                            ({"total_count": 2, "workflow_runs": [self.run, {**self.run, "id": 43}]}, 1001)):
            self.github.json.return_value = result
            with self.subTest(result=result, now=now), self.assertRaises(IntentRefused):
                refuse_replay(self.github, self.payload, 42, now=now)

    def test_workflow_keeps_credentials_off_candidates_and_artifacts_encrypted(self):
        source = (Path(__file__).parents[2] / WORKFLOW_PATH).read_text()
        for guard in ("github.actor == github.repository_owner", "github.triggering_actor == github.repository_owner",
                      "github.ref == 'refs/heads/main'", "github.run_attempt == 1",
                      "github.repository == 'ShaishiBear/dark-factory-2.0'"):
            self.assertIn(guard, source)
        self.assertIn("ref: ${{ github.sha }}", source)
        self.assertIn("persist-credentials: false", source)
        self.assertIn("ANTHROPIC_AUTH_TOKEN: ${{ secrets.OPENROUTER_API_KEY }}", source)
        self.assertNotIn("DARK_FACTORY_APP_PRIVATE_KEY", source)
        self.assertNotIn(": write", source)
        self.assertNotIn("continue-on-error", source)
        self.assertIn("path: ${{ runner.temp }}/frontdoor-result/result.age.b64", source)
        self.assertNotIn("inputs.ciphertext }}", source, "inputs are parsed from the event, never interpolated in shell")

    @unittest.skipUnless(shutil.which("age") and shutil.which("age-keygen"), "age CLI required for worker integration")
    def test_worker_encrypts_real_result_and_never_prints_private_output_or_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identity = root / "identity"
            subprocess.run(["age-keygen", "-o", str(identity)], capture_output=True, check=True)
            identity.chmod(0o600)
            cipher = AgeCipher(identity)
            self.payload["issued_at"] = int(__import__("time").time())
            self.event["inputs"]["ciphertext"] = cipher.encrypt(self.payload)
            event_file = root / "event.json"
            event_file.write_text(json.dumps(self.event))
            self.environ.update(GITHUB_EVENT_PATH=str(event_file), RUNNER_TEMP=str(root),
                                FRONTDOOR_AGE_IDENTITY=identity.read_text())
            def read(args):
                if "/runs?" in args[1]:
                    return {"total_count": 1, "workflow_runs": [self.run]}
                return {"id": 7} if args[1].endswith(WORKFLOW) else self.run
            self.github.json.side_effect = read
            provider = Mock()
            provider.run.return_value = SimpleNamespace(content='{"private":"Model output"}',
                                                        structured_output=None, model="fixture", cost_usd=0.01)
            with patch.object(worker, "load_config", return_value=SimpleNamespace(repository=self.payload["repository"], provider=None)), \
                    patch.object(worker, "GitHubClient", return_value=self.github), \
                    patch.object(worker, "api_provider", return_value=provider):
                output = io.StringIO()
                with patch.dict("os.environ", self.environ, clear=True), redirect_stdout(output):
                    self.assertEqual(worker.main(), 0)
                self.assertEqual(output.getvalue(), "FRONTDOOR_ENCRYPTED_RESULT_READY\n")
                files = list((root / "frontdoor-result").iterdir())
                self.assertEqual([file.name for file in files], ["result.age.b64"])
                result = cipher.decrypt(files[0].read_text())
                self.assertEqual(result["output"], {"private": "Model output"})
                self.assertEqual(result["request_sha256"], sha256_value(self.payload))
                request = provider.run.call_args.args[0]
                self.assertEqual((request.allowed_tools, request.environment, request.max_turns,
                                  request.max_budget_usd, request.timeout_seconds), ((), {}, 5, 1.0, 338))
                self.assertFalse(Path(request.cwd).exists())
                provider.run.side_effect = RuntimeError("secret private model failure")
                output = io.StringIO()
                with patch.dict("os.environ", self.environ, clear=True), redirect_stdout(output):
                    self.assertEqual(worker.main(), 1)
                self.assertEqual(output.getvalue(), "FRONTDOOR_PREPARATION_REFUSED\n")


class CipherTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("age") and shutil.which("age-keygen"), "age CLI required for encryption integration")
    def test_standard_age_round_trip_confidentiality_tamper_and_wrong_key(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "identity"
            other = Path(directory) / "other"
            for path in (key, other):
                subprocess.run(["age-keygen", "-o", str(path)], capture_output=True, check=True)
                path.chmod(0o600)
            cipher = AgeCipher(key)
            value = {"original": "Private transcript intent\nKeep all wording."}
            encrypted = cipher.encrypt(value)
            self.assertNotIn("Private transcript", encrypted)
            self.assertEqual(cipher.decrypt(encrypted), value)
            with self.assertRaises(IntentRefused):
                AgeCipher(other).decrypt(encrypted)
            changed = ("A" if encrypted[0] != "A" else "B") + encrypted[1:]
            with self.assertRaises(IntentRefused):
                cipher.decrypt(changed)
            if __import__("os").name != "nt":
                key.chmod(0o644)
                with self.assertRaises(IntentRefused):
                    AgeCipher(key)


if __name__ == "__main__":
    unittest.main()
