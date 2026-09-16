"""Exercise the real transport, owner store and closed command boundary without live effects."""
from io import BytesIO
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import subprocess
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from wsgiref.simple_server import make_server

from factory_kernel.frontdoor_http import FrontDoorApplication, FrontDoorServer, QuietHandler, verify_host_identity
from factory_kernel.frontdoor_intent import IntentStore
from tests.factory.test_frontdoor_intent import OWNER, REPO, example_spec

TOKEN = "b" * 64
ORIGIN = "https://factory.example"


class FrontDoorHTTPTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = IntentStore(Path(tmp.name), repository=REPO, owner=OWNER.identity)
        self.github = Mock()
        self.github.programme_issues.return_value = []
        self.app = FrontDoorApplication(store=self.store, project="citations", token=TOKEN,
                                        origin=ORIGIN, github=self.github, labels={}, app_login="factory[bot]")

    def call(self, path, *, body=None, raw=None, method=None, absent=(), **overrides):
        data = raw if raw is not None else json.dumps(body).encode() if body is not None else b""
        environ = {"REQUEST_METHOD": method or ("POST" if data else "GET"), "PATH_INFO": path,
                   "HTTP_HOST": "factory.example", "HTTP_ORIGIN": ORIGIN,
                   "HTTP_AUTHORIZATION": f"Bearer {TOKEN}", "CONTENT_LENGTH": str(len(data)),
                   "CONTENT_TYPE": "application/json", "QUERY_STRING": "", "wsgi.input": BytesIO(data),
                   **overrides}
        for key in absent:
            environ.pop(key, None)
        response = {}

        def start(status, headers):
            response.update(status=status, headers=dict(headers))

        value = b"".join(self.app(environ, start))
        response["body"] = value
        if response["headers"]["Content-Type"].startswith("application/json"):
            response["json"] = json.loads(value)
        return response

    def command(self, **changes):
        return {"idempotency_key": "test-command", "expected_project_version": 0,
                "operation": "record-intent", "payload": {"wording": "Show cited words."}, **changes}

    def test_private_reads_and_commands_require_real_bearer_authentication(self):
        for path, body in (("/api/snapshot", None), ("/api/history", None), ("/api/commands", self.command()),
                           ("/api/stop", {"request_id": "a" * 32, "reason": "Pause"})):
            for authorization in ("", "Bearer wrong", f"Basic {TOKEN}"):
                with self.subTest(path=path, authorization=authorization):
                    result = self.call(path, body=body, HTTP_AUTHORIZATION=authorization)
                    self.assertEqual(result["status"], "401 Unauthorized")
        self.github.run.assert_not_called()
        self.assertEqual(self.store.snapshot("citations", principal=OWNER)["project_version"], 0)

    def test_cross_origin_missing_origin_and_host_rebinding_cannot_write(self):
        for overrides in ({"HTTP_ORIGIN": "https://attacker.example"}, {"HTTP_ORIGIN": ""},
                          {"HTTP_HOST": "attacker.example"}):
            with self.subTest(overrides=overrides):
                self.assertEqual(self.call("/api/commands", body=self.command(), **overrides)["status"], "403 Forbidden")
        self.assertEqual(self.call("/api/commands", body=self.command(), absent=("HTTP_ORIGIN",))["status"], "403 Forbidden")
        self.assertEqual(self.store.snapshot("citations", principal=OWNER)["project_version"], 0)

    def test_authenticated_commands_use_server_owner_and_preserve_cas_and_replay(self):
        result = self.call("/api/commands", body=self.command())
        self.assertEqual(result["status"], "200 OK")
        self.assertEqual(result["json"]["ledger"][0]["actor"], {"identity": OWNER.identity, "role": "owner"})
        self.assertEqual(self.call("/api/commands", body=self.command())["json"], result["json"])
        stale = self.command(idempotency_key="second-command")
        self.assertEqual(self.call("/api/commands", body=stale)["status"], "409 Conflict")
        forged = self.command(actor={"identity": "intruder", "role": "owner"})
        self.assertEqual(self.call("/api/commands", body=forged)["status"], "409 Conflict")

    def test_duplicate_keys_oversize_and_unknown_content_type_refuse_before_effects(self):
        requests = [dict(raw=b'{"operation":"record-intent","operation":"approve-spec"}'),
                    dict(body=self.command(), CONTENT_LENGTH="250001"),
                    dict(body=self.command(), CONTENT_TYPE="text/plain"),
                    dict(body=self.command(), HTTP_TRANSFER_ENCODING="chunked")]
        for request in requests:
            with self.subTest(request=request):
                self.assertEqual(self.call("/api/commands", **request)["status"], "409 Conflict")
        self.assertEqual(self.store.snapshot("citations", principal=OWNER)["project_version"], 0)

    def test_programme_read_failure_preserves_independent_stop_observation(self):
        for failure in (RuntimeError("credential-shaped private failure"), subprocess.TimeoutExpired(["gh"], 60)):
            with self.subTest(failure=failure), patch("factory_kernel.frontdoor_http.ProgrammeQueue") as queue:
                queue.return_value.status.side_effect = failure
                result = self.call("/api/snapshot")
            self.assertEqual(result["status"], "200 OK")
            self.assertFalse(result["json"]["observation_available"])
            self.assertIsNone(result["json"]["observed_at"])
            self.assertEqual(result["json"]["stop"], {"state": "clear", "issues": []})
            self.assertIsNotNone(result["json"]["stop_observed_at"])
            self.assertIsNone(result["json"]["execution_observed_at"])
            self.assertIsNone(result["json"]["execution"])
            self.assertNotIn(b"credential-shaped", result["body"])

    def test_damaged_programme_cannot_hide_observed_emergency_stop(self):
        self.github.programme_issues.return_value = [
            {"number": 194, "state": "open", "labels": [{"name": "factory:stop"}]}]
        with patch("factory_kernel.frontdoor_http.ProgrammeQueue") as queue:
            queue.return_value.status.side_effect = ValueError("App-created issue lost its programme binding")
            result = self.call("/api/snapshot")["json"]
        self.assertEqual(result["stop"], {"state": "stopped", "issues": [194]})
        self.assertIsNone(result["execution"])
        self.assertFalse(result["observation_available"])

    def test_stop_read_failure_does_not_erase_progress_or_invent_clear_stop(self):
        self.github.programme_issues.side_effect = RuntimeError("private read failure")
        progress = {"status": "incomplete", "items": []}
        with patch("factory_kernel.frontdoor_http.ProgrammeQueue") as queue:
            queue.return_value.status.return_value = progress
            result = self.call("/api/snapshot")["json"]
        self.assertIsNone(result["stop"])
        self.assertIsNone(result["stop_observed_at"])
        self.assertEqual(result["execution"], progress)
        self.assertIsNotNone(result["execution_observed_at"])
        self.assertFalse(result["observation_available"])

    def test_observation_uses_existing_verifier_and_no_execution_effect(self):
        status = {"status": "incomplete", "items": [{"status": "closed-without-verified-completion"}]}
        with patch("factory_kernel.frontdoor_http.ProgrammeQueue") as queue:
            queue.return_value.status.return_value = status
            result = self.call("/api/snapshot")
            queue.return_value.status.assert_called_once_with({})
        self.assertEqual(result["json"]["execution"], status)
        self.assertTrue(result["json"]["observation_available"])
        self.github.run.assert_not_called()

    def test_history_preserves_old_drafts_and_binds_approval_without_effects(self):
        self.call("/api/commands", body=self.command())
        for version, title in ((1, "Earlier scope"), (2, "Reviewed scope")):
            spec = example_spec()
            spec["title"] = title
            state = self.call("/api/commands", body=self.command(
                idempotency_key=f"draft-{version}", expected_project_version=version,
                operation="propose-spec", payload={"spec": spec, "assumptions": [],
                                                    "open_questions": [], "technical_questions": []}))["json"]
        self.call("/api/commands", body=self.command(
            idempotency_key="approve", expected_project_version=3, operation="approve-spec",
            payload={"draft_version": 3, "spec_sha256": state["draft"]["spec_sha256"], "wording": "Approved exact draft"}))
        before = self.store.snapshot("citations", principal=OWNER)
        history = self.call("/api/history")["json"]
        self.assertEqual([row["record"]["spec"]["title"] for row in history["events"] if row["operation"] == "propose-spec"],
                         ["Earlier scope", "Reviewed scope"])
        self.assertEqual(history["events"][-1]["basis"], [history["events"][2]["event_id"]])
        self.assertEqual(history["execution_status"], "not-activated-by-history")
        self.assertEqual(self.store.snapshot("citations", principal=OWNER), before)
        self.github.run.assert_not_called()
        self.github.run_as_app.assert_not_called()

    def test_history_is_fixed_to_server_project_owner_and_cannot_write(self):
        with patch("factory_kernel.frontdoor_http.explain_history", return_value={"events": []}) as history:
            self.assertEqual(self.call("/api/history")["status"], "200 OK")
            history.assert_called_once_with(self.store, "citations", principal=OWNER)
        self.assertEqual(self.call("/api/history", QUERY_STRING="project=private-other")["status"], "400 Bad Request")
        self.assertEqual(self.call("/api/history", body={"actor": "intruder"})["status"], "404 Not Found")

    def test_successful_observation_records_a_timezone_aware_timestamp(self):
        with patch("factory_kernel.frontdoor_http.ProgrammeQueue") as queue:
            queue.return_value.status.return_value = {"programme": None, "items": []}
            result = self.call("/api/snapshot")["json"]
        self.assertTrue(result["observation_available"])
        self.assertIsNotNone(datetime.fromisoformat(result["observed_at"]).utcoffset())

    def test_preparation_is_explicit_and_uses_the_server_owner_principal(self):
        body = {"idempotency_key": "prepare", "expected_project_version": 1}
        self.assertEqual(self.call("/api/prepare", body=body)["status"], "503 Service Unavailable")
        preparer = self.app.preparer = Mock()
        preparer.prepare.return_value = {"state": "pending"}
        self.assertEqual(self.call("/api/prepare", body=body, HTTP_AUTHORIZATION="")["status"], "401 Unauthorized")
        preparer.prepare.assert_not_called()
        self.assertEqual(self.call("/api/prepare", body=body)["json"]["state"], "pending")
        preparer.prepare.assert_called_once_with("citations", body, principal=OWNER)

    def test_synthesis_uses_authenticated_owner_and_is_disabled_by_default(self):
        body = {"idempotency_key": "programme", "expected_project_version": 3,
                "approval_version": 3, "spec_sha256": "a" * 64}
        self.assertEqual(self.call("/api/programme-prepare", body=body)["status"], "503 Service Unavailable")
        synthesizer = self.app.synthesizer = Mock()
        synthesizer.prepare.return_value = {"state": "pending"}
        self.assertEqual(self.call("/api/programme-prepare", body=body, HTTP_AUTHORIZATION="")["status"], "401 Unauthorized")
        synthesizer.prepare.assert_not_called()
        self.assertEqual(self.call("/api/programme-prepare", body=body)["json"]["state"], "pending")
        synthesizer.prepare.assert_called_once_with("citations", body, principal=OWNER)

    def test_recovery_requires_owner_same_origin_and_fixed_preparer(self):
        body = {"idempotency_key": "recover", "expected_project_version": 1,
                "failed_preparation_sha256": "a" * 64, "reason": "One replacement draft, up to $2."}
        self.assertEqual(self.call("/api/prepare-recovery", body=body)["status"], "503 Service Unavailable")
        preparer = self.app.preparer = Mock()
        preparer.recover.return_value = {"state": "pending"}
        self.assertEqual(self.call("/api/prepare-recovery", body=body, HTTP_AUTHORIZATION="")["status"], "401 Unauthorized")
        self.assertEqual(self.call("/api/prepare-recovery", body=body, absent=("HTTP_ORIGIN",))["status"], "403 Forbidden")
        preparer.recover.assert_not_called()
        self.assertEqual(self.call("/api/prepare-recovery", body=body)["json"]["state"], "pending")
        preparer.recover.assert_called_once_with("citations", body, principal=OWNER)

    def test_stop_only_dispatches_fixed_owner_workflow_and_never_claims_observed_stop(self):
        payload = {"request_id": "c" * 32, "reason": "Pause before more work."}
        response = self.call("/api/stop", body=payload)
        self.assertEqual(response["status"], "202 Accepted")
        self.assertEqual(response["json"]["state"], "stop-requested")
        self.github.run.assert_called_once_with([
            "workflow", "run", "dark-factory-owner-stop.yml", "-R", REPO, "--ref", "main",
            "-f", f"request_id={payload['request_id']}", "-f", f"reason={payload['reason']}",
        ])
        self.github.run_as_app.assert_not_called()
        self.assertEqual(self.call("/api/stop", body={**payload, "workflow": "arbitrary.yml"})["status"], "409 Conflict")
        self.assertEqual(self.github.run.call_count, 1)

    def test_uncertain_stop_is_not_retried_or_rendered_as_success(self):
        self.github.run.side_effect = RuntimeError("private token detail")
        response = self.call("/api/stop", body={"request_id": "d" * 32, "reason": "Pause"})
        self.assertEqual(response["status"], "503 Service Unavailable")
        self.assertNotIn(b"private token detail", response["body"])
        self.assertEqual(self.github.run.call_count, 1)

    def test_login_assets_are_static_and_do_not_embed_secret_or_intent(self):
        for path in ("/", "/frontdoor.js", "/frontdoor.css"):
            response = self.call(path, HTTP_AUTHORIZATION="")
            self.assertEqual(response["status"], "200 OK")
            self.assertNotIn(TOKEN.encode(), response["body"])
            self.assertEqual(response["headers"]["Cache-Control"], "no-store")
            self.assertIn("frame-ancestors 'none'", response["headers"]["Content-Security-Policy"])
        self.assertEqual(self.call("/../.factory/kernel.json")["status"], "404 Not Found")
        self.assertEqual(self.call("/api/snapshot", QUERY_STRING=f"token={TOKEN}")["status"], "400 Bad Request")

    def test_remote_cleartext_origin_and_weak_tokens_refuse_at_startup(self):
        for origin, token in (("http://remote.example", TOKEN), (ORIGIN, "password"),
                               ("https://factory.example/path", TOKEN), ("https://user@factory.example", TOKEN)):
            with self.subTest(origin=origin, token=token), self.assertRaises(ValueError):
                FrontDoorApplication(store=self.store, project="citations", token=token,
                                     origin=origin, github=self.github, labels={}, app_login="factory[bot]")

    def test_slow_observation_does_not_queue_stop_behind_it(self):
        entered, release = threading.Event(), threading.Event()

        def app(environ, start):
            if environ["PATH_INFO"] == "/slow":
                entered.set()
                release.wait(5)
            start("200 OK", [("Content-Length", "2")])
            return [b"ok"]

        with make_server("127.0.0.1", 0, app, server_class=FrontDoorServer, handler_class=QuietHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def get(path):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                try:
                    connection.request("GET", path)
                    response = connection.getresponse()
                    return response.status, response.read()
                finally:
                    connection.close()

            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    slow = pool.submit(get, "/slow")
                    try:
                        self.assertTrue(entered.wait(2), "slow observation did not start")
                        try:
                            self.assertEqual(get("/stop"), (200, b"ok"))
                        except OSError as exc:
                            self.fail(f"stop queued behind a slow observation: {exc}")
                        self.assertFalse(slow.done(), "observation should still be waiting")
                    finally:
                        release.set()
                    self.assertEqual(slow.result(timeout=2), (200, b"ok"))
            finally:
                release.set()
                server.shutdown()
                thread.join(timeout=2)

    def test_host_dispatch_credential_is_the_actual_repository_owner(self):
        github = Mock(repository="owner/product")
        github.json.return_value = {"login": "owner", "type": "User"}
        verify_host_identity(github, "owner")
        for identity, owner in (({"login": "owner", "type": "Bot"}, "owner"),
                                 ({"login": "someone", "type": "User"}, "owner"),
                                 ({"login": "someone", "type": "User"}, "someone")):
            github.json.return_value = identity
            with self.subTest(identity=identity, owner=owner), self.assertRaises(ValueError):
                verify_host_identity(github, owner)


if __name__ == "__main__":
    unittest.main()
