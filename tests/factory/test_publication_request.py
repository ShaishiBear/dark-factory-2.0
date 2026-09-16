"""Owner consent is exact, fresh, private and cannot restart existing approved work."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest

from factory_kernel.canonical import sha256_value
from factory_kernel.frontdoor_intent import IntentRefused
from factory_kernel.publication_request import PublicationRequests
from tests.factory.test_frontdoor_intent import OWNER, WORKER
from tests.factory import test_frontdoor_programme as review_tests


class PublicationRequestTests(unittest.TestCase):
    def setUp(self):
        self.fixture = review_tests.ProgrammeReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.now = datetime(2026, 9, 16, 10, tzinfo=timezone.utc)
        self.service = PublicationRequests(self.fixture.store, app_login="factory[bot]", clock=lambda: self.now)
        self.review = self.fixture.prepare()
        self.command = {"request_id": "a" * 32, "review": self.fixture.request,
                        "destination": {"repository": self.fixture.store.repository, "visibility": "public",
                                        "input_sha256": self.review["input_sha256"]}}
        self.observation = {"repository": self.fixture.store.repository, "visibility": "public",
                            "main_sha": "b" * 40, "protected": True, "active_input": None,
                            "stop": {"state": "clear", "issues": []}}

    def reserve(self, principal=OWNER):
        return self.service.reserve("citations", self.command, principal=principal, observation=self.observation)

    def current(self):
        return self.service.current("citations", self.command["request_id"],
                                    principal=OWNER, observation=self.observation)

    def test_record_retains_only_exact_public_payload_and_hash_bound_consent(self):
        before = self.fixture.store.snapshot("citations", principal=OWNER)
        record = self.reserve()
        self.assertEqual(record["state"], "reserved")
        self.assertEqual(record["input"], self.review["input"])
        self.assertNotIn("Show me the cited words.", json.dumps(record))
        self.assertNotIn("Approve this exact citation scope.", json.dumps(record))
        self.assertEqual(self.current()["request_sha256"], sha256_value(record))
        self.assertEqual(self.current()["decision"], "current-owner-request")
        self.assertNotIn("input", self.current())
        self.assertEqual(self.fixture.store.snapshot("citations", principal=OWNER), before)
        self.assertEqual(self.reserve(), record)
        self.assertEqual(len(list(self.service.directory.glob("*.json"))), 1)

    def test_proposal_role_and_cross_project_cannot_reserve_or_query_owner_request(self):
        with self.assertRaisesRegex(IntentRefused, "only the authenticated owner"):
            self.reserve(WORKER)
        self.reserve()
        with self.assertRaises(IntentRefused):
            self.service.current("citations", "a" * 32, principal=WORKER, observation=self.observation)
        for project in ("other", "../citations"):
            with self.subTest(project=project), self.assertRaises(IntentRefused):
                self.service.current(project, "a" * 32, principal=OWNER, observation=self.observation)

    def test_exact_destination_visibility_and_payload_consent_required(self):
        for field, value in (("repository", "elsewhere/private"), ("visibility", "private"),
                             ("input_sha256", "c" * 64)):
            original = self.command["destination"][field]
            self.command["destination"][field] = value
            with self.subTest(field=field), self.assertRaises(IntentRefused):
                self.reserve()
            self.command["destination"][field] = original
        self.assertEqual(list(self.service.directory.glob("*.json")), [])

    def test_stopped_unprotected_unknown_or_foreign_source_never_reserves(self):
        for field, value in (("repository", "elsewhere/repo"), ("protected", False),
                             ("main_sha", "main"), ("visibility", "unknown"),
                             ("stop", None), ("stop", {"state": "stopped", "issues": [17]})):
            original = self.observation[field]
            self.observation[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(IntentRefused):
                self.reserve()
            self.observation[field] = original

    def test_same_approved_scope_is_already_active_even_if_decomposition_differs(self):
        active = deepcopy(self.review["input"])
        active["proposal"]["items"][0]["id"] = "different-decomposition"
        self.observation["active_input"] = active
        self.assertEqual(self.reserve()["state"], "already-active")
        with self.assertRaisesRegex(IntentRefused, "does not permit a new programme"):
            self.current()

    def test_different_active_scope_requires_separate_governed_replacement(self):
        active = deepcopy(self.review["input"])
        active["spec"]["revision"] = 2
        active["proposal"]["spec_sha256"] = sha256_value(active["spec"])
        self.observation["active_input"] = active
        self.assertEqual(self.reserve()["state"], "requires-governed-replacement")
        with self.assertRaises(IntentRefused):
            self.current()

    def test_request_id_replay_cannot_change_content_or_create_a_second_request(self):
        self.reserve()
        self.command["review"]["proposal"]["items"][0]["id"] = "changed"
        self.command["destination"]["input_sha256"] = self.fixture.prepare()["input_sha256"]
        with self.assertRaisesRegex(IntentRefused, "different content"):
            self.reserve()
        self.command["request_id"] = "d" * 32
        with self.assertRaisesRegex(IntentRefused, "already has a publication request"):
            self.reserve()

    def test_new_owner_decision_invalidates_old_request_before_an_effect(self):
        self.reserve()
        self.fixture.write("add-exploration", {"wording": "Keep this as an optional idea."})
        with self.assertRaisesRegex(IntentRefused, "current owner decisions"):
            self.current()

    def test_current_main_visibility_active_scope_and_stop_are_rechecked(self):
        self.reserve()
        for field, value in (("main_sha", "d" * 40), ("visibility", "private"),
                             ("active_input", self.review["input"]),
                             ("stop", {"state": "stopped", "issues": [17]})):
            original = self.observation[field]
            self.observation[field] = value
            with self.subTest(field=field), self.assertRaises(IntentRefused):
                self.current()
            self.observation[field] = original

    def test_expired_and_backward_clock_requests_never_become_current(self):
        self.reserve()
        created = self.now
        for delta in (timedelta(hours=1), timedelta(seconds=-1)):
            self.now = created + delta
            with self.subTest(delta=delta), self.assertRaisesRegex(IntentRefused, "expired or clock"):
                self.current()
        self.now = created + timedelta(minutes=59)
        self.assertEqual(self.current()["decision"], "current-owner-request")

    def test_record_payload_corruption_is_refused_without_recompilation_authority(self):
        record = self.reserve()
        record["input"]["proposal"]["items"][0]["id"] = "corrupt"
        self.service._save(self.service._path("citations", "a" * 32), record)
        with self.assertRaisesRegex(IntentRefused, "payload changed"):
            self.current()


if __name__ == "__main__":
    unittest.main()
