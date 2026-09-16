"""A stale/reflected/foreign currency response cannot authorize a publisher's next step."""
from copy import deepcopy
from datetime import timedelta
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from factory_kernel.canonical import sha256_value
from factory_kernel.frontdoor_intent import IntentRefused
from factory_kernel.publication_currency import CurrencyProtocol, key_from_identity
from tests.factory import test_publication_request as request_tests
from tests.factory.test_frontdoor_intent import OWNER


class PublicationCurrencyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = request_tests.PublicationRequestTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.record = self.fixture.reserve()
        self.protocol = CurrencyProtocol(b"x" * 32, repository=self.fixture.fixture.store.repository,
                                         project="citations", clock=lambda: self.fixture.now.timestamp())
        self.observer = Mock(side_effect=lambda: self.fixture.observation)
        self.request = self.challenge()

    def challenge(self, phase="branch"):
        return self.protocol.challenge(request_id=self.fixture.command["request_id"],
                                       request_sha256=sha256_value(self.record),
                                       main_sha=self.fixture.observation["main_sha"], phase=phase)

    def answer(self, request=None):
        return self.protocol.answer(request or self.request, requests=self.fixture.service,
                                     principal=OWNER, observe=self.observer)

    def test_fresh_exchange_returns_only_bound_identities_and_rechecks_remote_source(self):
        result = self.protocol.verify(self.answer(), self.request)
        self.assertEqual(result, self.fixture.current())
        self.observer.assert_called_once_with()
        self.assertNotIn("input", result)
        self.assertNotIn("wording", result)

    def test_unsigned_or_altered_request_refuses_before_remote_reads(self):
        for field, value in (("main_sha", "c" * 40), ("phase", "merge"), ("request_id", "b" * 32)):
            request = deepcopy(self.request)
            request["payload"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(IntentRefused, "authentication"):
                self.answer(request)
        self.observer.assert_not_called()

    def test_wrong_shared_key_and_reflected_request_are_not_responses(self):
        other = CurrencyProtocol(b"y" * 32, repository=self.protocol.repository,
                                  project="citations", clock=self.protocol.clock)
        with self.assertRaisesRegex(IntentRefused, "authentication"):
            other.verify(self.answer(), self.request)
        with self.assertRaisesRegex(IntentRefused, "authentication"):
            self.protocol.verify(self.request, self.request)

    def test_response_for_another_nonce_or_phase_refuses(self):
        response = self.answer()
        for request in (self.challenge(), self.challenge("merge")):
            with self.subTest(request=request["payload"]["phase"]), self.assertRaisesRegex(IntentRefused, "another challenge"):
                self.protocol.verify(response, request)

    def test_authenticated_foreign_project_and_arbitrary_operation_refuse(self):
        for field, value in (("project", "other"), ("repository", "other/repo"),
                             ("phase", "approve-spec"), ("issued_at", True)):
            request = deepcopy(self.request["payload"])
            request[field] = value
            with self.subTest(field=field), self.assertRaises(IntentRefused):
                self.answer(self.protocol._seal(request, b"request/"))
        self.observer.assert_not_called()

    def test_stale_request_and_response_are_refused(self):
        response = self.answer()
        self.fixture.now += timedelta(seconds=61)
        with self.assertRaisesRegex(IntentRefused, "freshness"):
            self.answer()
        with self.assertRaisesRegex(IntentRefused, "freshness"):
            self.protocol.verify(response, self.request)

    def test_remote_reads_outliving_challenge_are_refused(self):
        def observe():
            self.fixture.now += timedelta(seconds=61)
            return self.fixture.observation
        self.observer.side_effect = observe
        with self.assertRaisesRegex(IntentRefused, "freshness"):
            self.answer()

    def test_new_owner_decision_or_stop_is_not_signed_as_current(self):
        self.fixture.fixture.write("add-exploration", {"wording": "A later owner decision"})
        with self.assertRaisesRegex(IntentRefused, "current owner decisions"):
            self.answer()
        self.fixture.observation["stop"] = {"state": "stopped", "issues": [17]}
        with self.assertRaisesRegex(IntentRefused, "clear stop"):
            self.answer()

    def test_modified_response_or_wrong_source_is_refused(self):
        response = self.answer()
        response["payload"]["current"]["input_sha256"] = "a" * 64
        with self.assertRaisesRegex(IntentRefused, "authentication"):
            self.protocol.verify(response, self.request)
        payload = deepcopy(self.request["payload"])
        payload["main_sha"] = "c" * 40
        with self.assertRaisesRegex(IntentRefused, "different request or source"):
            self.answer(self.protocol._seal(payload, b"request/"))

    def test_request_expiring_between_answer_and_consumption_refuses(self):
        self.fixture.now += timedelta(minutes=59, seconds=58)
        self.request = self.challenge()
        response = self.answer()
        self.fixture.now += timedelta(seconds=3)
        with self.assertRaisesRegex(IntentRefused, "expired before response consumption"):
            self.protocol.verify(response, self.request)

    def test_authenticated_response_still_requires_current_owner_identity(self):
        response = self.answer()["payload"]
        for field, value in (("decision", "approved-by-model"), ("project_version", True),
                             ("repository", "elsewhere/repo")):
            payload = deepcopy(response)
            payload["current"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(IntentRefused, "identity refused"):
                self.protocol.verify(self.protocol._seal(payload, b"response/"), self.request)

    def test_key_derivation_ignores_age_comments_and_requires_a_private_native_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.age"
            path.write_text("# fixture only, not a usable encryption credential\nAGE-SECRET-KEY-1" + "A" * 58 + "\n")
            path.chmod(0o600)
            key = key_from_identity(path)
            self.assertEqual(len(key), 32)
            path.write_text("AGE-SECRET-KEY-1" + "A" * 58 + "\n")
            self.assertEqual(key_from_identity(path), key)
            if os.name != "nt":
                path.chmod(0o644)
                with self.assertRaisesRegex(IntentRefused, "private regular file"):
                    key_from_identity(path)
                path.chmod(0o600)
            path.write_text("AGE-PLUGIN-SECRET-KEY-1fixture")
            with self.assertRaisesRegex(IntentRefused, "native age"):
                key_from_identity(path)


if __name__ == "__main__":
    unittest.main()
