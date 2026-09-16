"""Canonical private advice reaches exact consent; changed evidence cannot authorize effects."""
from copy import deepcopy
from datetime import timedelta
import json
import unittest
from unittest.mock import Mock, patch

from factory_kernel.canonical import sha256_value
from factory_kernel.exploration import Exploration
from factory_kernel.exploration_repository import _analyse
from factory_kernel.frontdoor_intent import IntentRefused, Principal
from factory_kernel.publication_strategy import StoredStrategyReviews
from factory_kernel import publication_policy as policy
from tests.factory import test_exploration as exploration
from tests.factory import test_publication_dispatch as dispatch
from tests.factory import test_publication_dispatch_http as http
from tests.factory import test_publication_source as source


class StrategyPublicationTests(unittest.TestCase):
    def setUp(self):
        self.f = dispatch.PublicationDispatchTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.github.programme_issues.return_value = []
        self.context = _analyse("b" * 40, ["app/backend/rag/tools.py"],
                                lambda _path, _limit: b"# synthetic committed source\n")
        self.engine = Exploration(self.f.store, lambda: deepcopy(self.context), check_stop=Mock(),
                                  app_login=policy.APP_LOGIN)
        self.counter = 0
        self.act("open", {"question": "Which lookup architecture?", "parent_session": None,
                          "policy": exploration.policy()})
        self.act("add_candidates", {"claims": [exploration.claim("scan-assumption"), exploration.claim("index-assumption")],
            "candidates": [exploration.candidate("scan", "linear", 10, 20),
                           exploration.candidate("index", "hash", 40, 60)]})
        self.act("recommend", {"stop_reason": "sufficient-support", "rationale": "Current predicted bounds distinguish candidates.",
            "remaining_uncertainty": ["Production latency remains unknown."],
            "next_useful_experiment": "Measure representative production workload."})
        self.act("handoff", {"proposal": self.f.review["proposal"]})
        self.inspect = Mock(side_effect=lambda *a, **kw: deepcopy(self.context))
        self.resolver = StoredStrategyReviews(self.f.store, self.f.github, app_login=policy.APP_LOGIN,
                                              inspect=self.inspect)
        self.requests = self.f.service.requests
        self.requests.strategy_reviews = self.resolver
        self.reference = self.resolver.reference(policy.PROJECT, "lookup",
            expected_project_version=self.version(), principal=self.f.owner)
        self.preview = self.f.service.preview(policy.PROJECT, self.reference, principal=self.f.owner)
        self.command = {"request_id": "a" * 32, "review": self.reference,
                        "destination": self.preview["destination"]}

    def version(self):
        return self.f.store.snapshot(policy.PROJECT, principal=self.f.owner)["project_version"]

    def act(self, operation, request):
        self.counter += 1
        return getattr(self.engine, operation)(policy.PROJECT, {"idempotency_key": f"strategy-{self.counter}",
            "session_id": "lookup", "expected_project_version": self.version(), "request": request},
            principal=self.f.owner)

    def rehash(self):
        self.context["identity"] = sha256_value({k: v for k, v in self.context.items() if k != "identity"})

    def reserve(self):
        return self.requests.reserve(policy.PROJECT, self.command, principal=self.f.owner,
                                     observation=self.f.observation)

    def current(self):
        return self.requests.current(policy.PROJECT, "a" * 32, principal=self.f.owner,
                                     observation=self.f.observation)

    def test_exact_canonical_advice_is_public_without_private_wording_or_proof_authority(self):
        before = self.resolver.records.read(policy.PROJECT, self.f.owner)
        result = self.f.service.publish(policy.PROJECT, self.command, principal=self.f.owner)
        self.assertEqual(result["state"], "dispatch-submitted")
        payload = self.f.cipher.encrypt.call_args.args[0]
        self.assertEqual(payload["input"], self.preview["input"])
        self.assertEqual(payload["input"]["version"], "1.1")
        self.assertEqual(payload["input"]["strategy"]["qualification_status"], "UNPROVEN")
        self.assertIs(payload["input"]["strategy"]["proof_reuse_allowed"], False)
        self.assertNotIn("Private", json.dumps(payload))
        from factory_kernel.publication_worker import compile_payload
        manifest = compile_payload(payload, "a" * 32, "b" * 40, 123)
        self.assertEqual(manifest["input"], self.preview["input"])
        forged = deepcopy(payload)
        forged["input"]["strategy"]["candidate"]["mechanism"] = "Advice changed after consent."
        with self.assertRaises(IntentRefused):
            compile_payload(forged, "a" * 32, "b" * 40, 123)
        self.assertEqual(self.f.service.publish(policy.PROJECT, self.command, principal=self.f.owner), result)
        self.f.github.run.assert_called_once()
        self.assertEqual(self.resolver.records.read(policy.PROJECT, self.f.owner), before)
        self.assertTrue(self.inspect.call_args_list)
        for call in self.inspect.call_args_list:
            self.assertEqual(call.args[1], ["app/backend/rag/tools.py"])

    def test_forged_reference_and_caller_advice_are_rejected(self):
        for key in ("recommendation_sha256", "handoff_sha256", "session_id"):
            value = deepcopy(self.reference)
            value["exploration"][key] = "f" * 64
            with self.subTest(key=key), self.assertRaises(IntentRefused):
                self.resolver.resolve(policy.PROJECT, value, principal=self.f.owner)
        for key in ("strategy", "proposal"):
            value = {**self.reference, key: self.preview["input"].get(key)}
            with self.subTest(key=key), self.assertRaises(IntentRefused):
                self.resolver.resolve(policy.PROJECT, value, principal=self.f.owner)
        self.f.github.run.assert_not_called()

    def test_regeneration_is_read_only_and_disabled_unless_explicitly_configured(self):
        before = self.resolver.records.read(policy.PROJECT, self.f.owner)
        first = self.resolver.resolve(policy.PROJECT, self.reference, principal=self.f.owner)
        self.assertEqual(self.resolver.resolve(policy.PROJECT, self.reference, principal=self.f.owner), first)
        self.assertEqual(self.resolver.records.read(policy.PROJECT, self.f.owner), before)
        self.requests.strategy_reviews = None
        with self.assertRaisesRegex(IntentRefused, "not enabled"):
            self.reserve()

    def test_changed_context_after_consent_prevents_currency_and_post(self):
        self.reserve()
        self.context["commit"] = "c" * 40
        self.rehash()
        with self.assertRaises(IntentRefused):
            self.current()
        with self.assertRaises(IntentRefused):
            self.f.service.publish(policy.PROJECT, self.command, principal=self.f.owner)
        self.f.github.run.assert_not_called()

    def test_invalidated_claim_revokes_current_request(self):
        self.reserve()
        self.act("observe_claim", {"claim_id": "scan-assumption", "status": "invalidated",
            "observation": "Workload exceeds the bound.", "source": "owner"})
        with self.assertRaises(IntentRefused):
            self.current()
        self.assertEqual(self.resolver.choices(policy.PROJECT, principal=self.f.owner), [])

    def test_context_change_during_encryption_prevents_dispatch(self):
        def encrypt(_payload):
            self.context["commit"] = "c" * 40
            self.rehash()
            return "encrypted"
        self.f.cipher.encrypt.side_effect = encrypt
        with self.assertRaises(IntentRefused):
            self.f.service.publish(policy.PROJECT, self.command, principal=self.f.owner)
        self.f.github.run.assert_not_called()
        self.assertEqual(list(self.f.service.directory.glob("*.json")), [])

    def test_context_regeneration_cannot_race_owner_change_or_request_expiry(self):
        self.reserve()
        original = self.requests.review
        def expire(*args, **kwargs):
            result = original(*args, **kwargs)
            self.f.now += timedelta(hours=2)
            return result
        with patch.object(self.requests, "review", side_effect=expire), self.assertRaisesRegex(IntentRefused, "expired"):
            self.current()
        self.f.now -= timedelta(hours=2)
        def change(*args, **kwargs):
            result = original(*args, **kwargs)
            self.f.version = self.version()
            self.f.write("add-exploration", {"wording": "New owner decision."})
            return result
        with patch.object(self.requests, "review", side_effect=change), self.assertRaisesRegex(IntentRefused, "owner decisions changed"):
            self.current()

    def test_existing_active_scope_cannot_be_replaced_with_new_advice(self):
        self.f.observation["active_input"] = deepcopy(self.preview["input"])
        self.f.observation["active_input"]["strategy"]["candidate"]["mechanism"] = "Different advice."
        preview = self.f.service.preview(policy.PROJECT, self.reference, principal=self.f.owner)
        self.assertEqual(preview["state"], "already-active")
        self.assertEqual(preview["replanning"]["disposition"], "strategy-change")
        self.assertTrue(preview["replanning"]["strategy"]["changed"])
        self.assertEqual(self.f.service.publish(policy.PROJECT, self.command, principal=self.f.owner)["state"], "already-active")
        self.f.github.run.assert_not_called()

    def test_stop_foreign_owner_and_project_refuse(self):
        for principal in (Principal(policy.OWNER, "worker"), Principal("someone", "owner")):
            with self.assertRaises(IntentRefused):
                self.resolver.resolve(policy.PROJECT, self.reference, principal=principal)
        with self.assertRaises(IntentRefused):
            self.resolver.resolve("other", self.reference, principal=self.f.owner)
        self.f.github.programme_issues.return_value = [{"number": 1, "state": "open", "labels": [{"name": "factory:stop"}]}]
        with self.assertRaisesRegex(IntentRefused, "clear stop"):
            self.reserve()

    def test_remote_source_observation_preserves_strategy_and_version(self):
        fixture = source.PublicationSourceTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        value = deepcopy(self.preview["input"])
        # This source fixture uses a different repository; preserve its approved spec binding.
        value["spec"]["repository"] = fixture.github.repository
        value["proposal"]["spec_sha256"] = sha256_value(value["spec"])
        value["strategy"]["spec_sha256"] = sha256_value(value["spec"])
        fixture.active = value
        self.assertEqual(source.observe_publication_source(fixture.github)["active_input"], value)


class StrategyPublicationHTTPTests(unittest.TestCase):
    def test_real_routes_share_strategy_currency_and_owner_boundary(self):
        fixture = StrategyPublicationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        transport = http.PublicationDispatchHTTPTests()
        transport.setUp()
        self.addCleanup(transport.doCleanups)
        from factory_kernel.frontdoor_http import FrontDoorApplication
        from tests.factory.test_frontdoor_http import TOKEN, ORIGIN
        f = fixture.f
        transport.http.app = FrontDoorApplication(store=f.store, project=policy.PROJECT, token=TOKEN,
            origin=ORIGIN, github=f.github, labels={}, app_login=policy.APP_LOGIN,
            publication_key=b"x" * 32, publisher=f.service)
        path = "/api/strategy-publication-preview"
        request = {"session_id": "lookup", "expected_project_version": fixture.version()}
        self.assertEqual(transport.http.call(path, body=request, HTTP_AUTHORIZATION="")["status"], "401 Unauthorized")
        self.assertEqual(transport.http.call(path, body=request, HTTP_ORIGIN="https://other.invalid")["status"], "403 Forbidden")
        response = transport.http.call(path, body=request)
        self.assertEqual(response["status"], "200 OK", response)
        self.assertEqual(response["json"], fixture.preview)
        self.assertEqual(transport.http.call("/api/programme-publish", body=fixture.command)["status"], "202 Accepted")
        record = f.service.requests._read(f.service.requests._path(policy.PROJECT, "a" * 32))
        protocol = transport.http.app.currency
        challenge = protocol.challenge(request_id="a" * 32, request_sha256=sha256_value(record),
                                       main_sha="b" * 40, phase="merge")
        with patch("factory_kernel.frontdoor_http.observe_publication_source", return_value=f.observation):
            response = transport.http.call("/api/publication-currency", body=challenge, HTTP_AUTHORIZATION="")
            self.assertEqual(response["status"], "200 OK", response)
            current = protocol.verify(response["json"], challenge)
            self.assertEqual(current["input_sha256"], fixture.preview["input_sha256"])
            self.assertNotIn("input", current)
            fixture.context["commit"] = "c" * 40
            fixture.rehash()
            self.assertEqual(transport.http.call("/api/publication-currency", body=challenge,
                             HTTP_AUTHORIZATION="")["status"], "409 Conflict")


if __name__ == "__main__":
    unittest.main()
