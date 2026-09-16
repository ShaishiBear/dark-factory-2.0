"""Owner consent reaches one encrypted workflow POST, with durable uncertainty and no replay."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from factory_kernel.canonical import sha256_value
from factory_kernel.frontdoor_intent import IntentRefused, IntentStore, Principal
from factory_kernel.publication_dispatch import PublicationDispatches
from factory_kernel.publication_request import PublicationRequests
from factory_kernel import publication_policy as policy
from tests.factory.test_frontdoor_intent import example_spec


class PublicationDispatchTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.owner = Principal(policy.OWNER, "owner")
        self.store = IntentStore(Path(directory), repository=policy.REPOSITORY, owner=policy.OWNER)
        self.spec = example_spec()
        self.spec["repository"] = policy.REPOSITORY
        self.version = 0
        self.write("record-intent", {"wording": "Private interview wording must stay here."})
        self.write("propose-spec", {"spec": self.spec, "assumptions": [], "open_questions": [], "technical_questions": []})
        draft = self.store.snapshot(policy.PROJECT, principal=self.owner)["draft"]
        self.write("approve-spec", {"draft_version": draft["draft_version"], "spec_sha256": draft["spec_sha256"],
                                    "wording": "Private approval wording must stay here."})
        self.review = {"expected_project_version": 3, "approval_version": 3, "spec_sha256": draft["spec_sha256"],
                       "proposal": {"spec_sha256": draft["spec_sha256"], "items": [
                           {"id": "snippet", "acceptance": ["AC1"], "blocked_by": []}]}}
        self.observation = {"repository": policy.REPOSITORY, "visibility": "public", "main_sha": "b" * 40,
                            "protected": True, "active_input": None, "stop": {"state": "clear", "issues": []}}
        self.observe = Mock(side_effect=lambda _github: deepcopy(self.observation))
        self.github = Mock(repository=policy.REPOSITORY)
        self.github.json.return_value = {"id": 17, "path": policy.WORKFLOW_PATH, "state": "active"}
        self.cipher = Mock()
        self.cipher.encrypt.return_value = "encrypted-approved-input"
        self.now = datetime.now(timezone.utc)
        requests = PublicationRequests(self.store, app_login=policy.APP_LOGIN, clock=lambda: self.now)
        self.service = PublicationDispatches(self.store, self.github, self.cipher, app_login=policy.APP_LOGIN,
                                             requests=requests, observe=self.observe)
        preview = self.service.preview(policy.PROJECT, self.review, principal=self.owner)
        self.command = {"request_id": "a" * 32, "review": self.review, "destination": preview["destination"]}

    def write(self, operation, payload):
        value = self.store.execute(policy.PROJECT, {"idempotency_key": "command-" + str(self.version),
            "expected_project_version": self.version, "operation": operation, "payload": payload}, principal=self.owner)
        self.version = value["project_version"]

    def publish(self):
        return self.service.publish(policy.PROJECT, self.command, principal=self.owner)

    def test_preview_shows_exact_public_payload_and_destination_without_creating_consent(self):
        preview = self.service.preview(policy.PROJECT, self.review, principal=self.owner)
        self.assertEqual(preview["state"], "ready-for-consent")
        self.assertEqual(preview["input_sha256"], sha256_value(preview["input"]))
        self.assertEqual(preview["destination"]["visibility"], "public")
        self.assertNotIn("Private interview", json.dumps(preview))
        self.assertNotIn("Private approval", json.dumps(preview))
        self.assertEqual(list(self.service.requests.directory.glob("*.json")), [])
        self.github.run.assert_not_called()

    def test_exact_consent_is_immutable_and_pending_dispatch_precedes_one_encrypted_post(self):
        def post(args):
            path = self.service.directory / f"{policy.PROJECT}-3.json"
            self.assertEqual(json.loads(path.read_text())["state"], "dispatch-pending")
            self.assertEqual(args[:7], ["workflow", "run", policy.WORKFLOW, "-R", policy.REPOSITORY, "--ref", "main"])
            self.assertNotIn("Private", str(args))
        self.github.run.side_effect = post
        first = self.publish()
        request = self.service.requests._read(self.service.requests._path(policy.PROJECT, "a" * 32))
        self.assertEqual(first["request_sha256"], sha256_value(request))
        self.assertEqual(first["state"], "dispatch-submitted")
        self.assertEqual(self.publish(), first)
        self.github.run.assert_called_once()
        self.github.run_as_app.assert_not_called()
        payload = self.cipher.encrypt.call_args.args[0]
        self.assertEqual(payload["input_sha256"], first["input_sha256"])
        self.assertNotIn("Private", json.dumps(payload))
        self.assertEqual(sha256_value(request), first["request_sha256"])

    def test_uncertain_dispatch_survives_reload_and_never_repeats_post(self):
        self.github.run.side_effect = TimeoutError("response may have been lost after acceptance")
        first = self.publish()
        self.assertEqual(first["state"], "dispatch-uncertain")
        self.service = PublicationDispatches(self.store, self.github, self.cipher, app_login=policy.APP_LOGIN,
                                             requests=self.service.requests, observe=self.observe)
        self.assertEqual(self.publish(), first)
        self.github.run.assert_called_once()

    def test_concurrent_requests_only_dispatch_once(self):
        entered, release = threading.Event(), threading.Event()
        def pending(_args):
            entered.set()
            self.assertTrue(release.wait(5))
        self.github.run.side_effect = pending
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.publish)
            try:
                self.assertTrue(entered.wait(5))
                second = self.publish()
                self.assertEqual(second["state"], "dispatch-pending")
            finally:
                release.set()
            outcomes = [first.result(), second]
        self.assertTrue(all(row["state"] in {"dispatch-pending", "dispatch-submitted"} for row in outcomes))
        self.github.run.assert_called_once()

    def test_owner_change_during_encryption_refuses_before_reserving_a_post(self):
        def encrypt(_payload):
            self.write("add-exploration", {"wording": "A new owner decision"})
            return "encrypted"
        self.cipher.encrypt.side_effect = encrypt
        with self.assertRaisesRegex(IntentRefused, "owner decisions changed"):
            self.publish()
        self.github.run.assert_not_called()
        self.assertEqual(list(self.service.directory.glob("*.json")), [])

    def test_existing_scope_or_different_programme_never_dispatches(self):
        preview = self.service.preview(policy.PROJECT, self.review, principal=self.owner)
        self.observation["active_input"] = deepcopy(preview["input"])
        self.assertEqual(self.publish()["state"], "already-active")
        self.github.run.assert_not_called()
        self.cipher.encrypt.assert_not_called()

    def test_known_pre_post_failure_can_only_resume_the_same_current_reservation(self):
        self.github.json.return_value["state"] = "disabled"
        with self.assertRaisesRegex(IntentRefused, "not active"):
            self.publish()
        self.github.run.assert_not_called()
        preview = self.service.preview(policy.PROJECT, self.review, principal=self.owner)
        self.assertEqual(preview["request_id"], self.command["request_id"])
        self.assertEqual(preview["state"], "ready-for-consent")
        self.now += timedelta(hours=2)
        self.assertEqual(self.service.preview(policy.PROJECT, self.review, principal=self.owner)["state"],
                         "requires-reconciliation")
        with self.assertRaises(IntentRefused):
            self.publish()
        self.github.run.assert_not_called()

    def test_proposal_role_foreign_project_and_changed_visibility_cannot_publish(self):
        for project, principal in (("other", self.owner), (policy.PROJECT, Principal("worker", "proposal"))):
            with self.subTest(project=project), self.assertRaises(IntentRefused):
                self.service.publish(project, self.command, principal=principal)
        self.observation["visibility"] = "private"
        with self.assertRaises(IntentRefused):
            self.publish()
        self.github.run.assert_not_called()

    def test_observation_requires_platform_provenance_and_never_turns_success_into_completion(self):
        self.publish()
        run = {"id": 23, "run_attempt": 1, "event": "workflow_dispatch", "path": policy.WORKFLOW_PATH,
               "workflow_id": 17, "head_branch": "main", "head_sha": "b" * 40,
               "display_title": "programme-" + "a" * 32,
               "repository": {"full_name": policy.REPOSITORY}, "head_repository": {"full_name": policy.REPOSITORY},
               "actor": {"login": policy.OWNER, "type": "User"},
               "triggering_actor": {"login": policy.OWNER, "type": "User"},
               "status": "completed", "conclusion": "success"}
        self.github.json.return_value = {"total_count": 1, "workflow_runs": [run]}
        observed = self.service.latest(policy.PROJECT)
        self.assertEqual(observed["workflow_observation"], "observed")
        self.assertEqual(observed["workflow_conclusion"], "success")
        self.assertNotIn("product_complete", observed)
        run["head_sha"] = "f" * 40
        self.assertEqual(self.service.latest(policy.PROJECT)["workflow_observation"], "unavailable")
        self.github.run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
