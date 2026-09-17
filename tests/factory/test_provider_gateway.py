"""The bounded provider gateway contract (C06): one operation per channel, stripped headers, no
redirects, at-most-once accounting through a real meter scope, replay without re-transmission,
partial streams recorded as unknown spend, and a credential the caller never sees."""
from __future__ import annotations

import json
import unittest

from factory_kernel.canonical import sha256_bytes
from factory_kernel.provider_gateway import (
    GatewayRefused,
    Route,
    UpstreamResponse,
    close_session,
    handle_model_request,
    handle_request,
    handle_validation_request,
    openrouter_cost_extractor,
    reserve_session,
)
from factory_kernel.validation_meter import MeterRefused, close_validation_scope, open_validation_scope
from tests.factory.test_validation_meter import BINDING, FakeLedger, digest


class FakeUpstream:
    """Records exactly what reached it; answers from a script."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("upstream called more often than scripted")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def ok(body: dict, *, pid="gen-1", status=200):
    raw = json.dumps(body).encode()
    return UpstreamResponse(status=status, headers={"content-type": "application/json"}, chunks=[raw[:5], raw[5:]], provider_request_id=pid)


ROUTE = Route(route_id="messages", method="POST", path="/v1/messages", model="anthropic/claude-sonnet-4.6",
              spend_class="worker-model", ceiling_microusd=50_000, timeout_seconds=30, cost_extractor=openrouter_cost_extractor)
VALIDATION = Route(route_id="embeddings", method="POST", path="/v1/embeddings", model="openai/text-embedding-3-small",
                   spend_class="validation-embedding", ceiling_microusd=1_000, timeout_seconds=10)


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.ledger = FakeLedger()
        self.scope = open_validation_scope(self.ledger, scope_class="worker-model", binding=BINDING, execution_id="run-1", attempt=1,
                                           limit_microusd=120_000, max_calls=5, request_sha256=digest("bundle"))
        self.time = [100.0]
        self.credential_reads = 0

        def credential():
            self.credential_reads += 1
            return {"authorization": "Bearer sk-secret-never-shown"}

        self.credential = credential

    def session(self, upstream, route=ROUTE, **kwargs):
        return reserve_session(self.scope, route, upstream=upstream, credential=self.credential, clock=lambda: self.time[0], **kwargs)

    def request(self, session, token, body: dict, *, request_id="r1", headers=None, method="POST", path="/v1/messages"):
        return handle_model_request(session, token=token, method=method, path=path,
                                    headers=headers or {"Authorization": "Bearer caller-token", "Content-Type": "application/json", "X-Api-Key": "leak"},
                                    body=json.dumps(body).encode(), request_id=request_id)

    def test_one_transmission_with_stripped_headers_fixed_model_and_the_gateways_credential(self):
        upstream = FakeUpstream([ok({"id": "m1", "usage": {"cost": 0.0123}})])
        session, token = self.session(upstream)
        result = self.request(session, token, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 8})
        self.assertEqual((result["outcome"], result["status"], result["replayed"], result["partial"]), ("returned", 200, False, False))
        sent = upstream.requests[0]
        self.assertEqual(sent["headers"], {"content-type": "application/json", "authorization": "Bearer sk-secret-never-shown"})
        self.assertNotIn("x-api-key", sent["headers"])
        self.assertEqual(json.loads(sent["body"])["model"], ROUTE.model, "the model comes from the protected route")
        self.assertEqual((sent["method"], sent["path"], sent["follow_redirects"]), ("POST", "/v1/messages", False))
        call = self.scope.calls[result["call_id"]]
        self.assertEqual((call.state, call.reported_microusd, call.outcome), ("observed", 12_300, "returned"))
        self.assertEqual(self.credential_reads, 1)
        self.assertNotIn("sk-secret", json.dumps(session.to_dict()))
        self.assertEqual(session.to_dict()["requests"][0]["provider_request_id"], "gen-1")

    def test_a_reconnect_replays_the_record_and_never_transmits_again(self):
        upstream = FakeUpstream([ok({"id": "m1"})])
        session, token = self.session(upstream)
        body = {"messages": [], "max_tokens": 1}
        first = self.request(session, token, body)
        again = self.request(session, token, body)
        self.assertEqual((again["replayed"], again["body"], len(upstream.requests)), (True, first["body"], 1))
        self.assertEqual(len(self.scope.calls), 1, "one reservation, one start")
        with self.assertRaises(GatewayRefused) as ctx:
            self.request(session, token, {"messages": [], "max_tokens": 2})
        self.assertIn("collision", str(ctx.exception))
        self.assertEqual(len(upstream.requests), 1)

    def test_only_the_routes_operation_and_model_are_permitted_before_anything_is_reserved(self):
        upstream = FakeUpstream([])
        session, token = self.session(upstream)
        cases = {
            "other path": dict(path="/v1/complete"),
            "other method": dict(method="GET"),
            "bad token": dict(),
        }
        with self.assertRaises(GatewayRefused):
            self.request(session, token, {"model": "someone/else", "messages": []})
        with self.assertRaises(GatewayRefused):
            self.request(session, token, {"messages": []}, path="/v1/complete")
        with self.assertRaises(GatewayRefused):
            self.request(session, token, {"messages": []}, method="GET")
        with self.assertRaises(GatewayRefused):
            self.request(session, "wrong-token", {"messages": []})
        with self.assertRaises(GatewayRefused):
            handle_model_request(session, token=token, method="POST", path="/v1/messages", headers={}, body=b"x" * 1_000_001, request_id="big")
        self.assertEqual((len(self.scope.calls), len(upstream.requests)), (0, 0), "refusals before transmission reserve nothing")
        self.assertEqual(self.credential_reads, 0)

    def test_a_redirect_is_refused_and_the_call_is_observed_as_failed_with_unknown_spend(self):
        upstream = FakeUpstream([UpstreamResponse(status=302, headers={"location": "https://elsewhere"}, chunks=[])])
        session, token = self.session(upstream)
        with self.assertRaises(GatewayRefused):
            self.request(session, token, {"messages": []})
        call = next(iter(self.scope.calls.values()))
        self.assertEqual((call.state, call.outcome, call.reported_microusd), ("observed", "failed", None))

    def test_an_oversized_or_slow_or_broken_stream_is_a_recorded_partial_with_unknown_spend(self):
        # Three transmissions at the route ceiling need a bundle of at least 150_000.
        self.scope = open_validation_scope(self.ledger, scope_class="worker-model", binding=BINDING, execution_id="run-3", attempt=1,
                                           limit_microusd=200_000, max_calls=5, request_sha256=digest("bundle-3"))
        big = UpstreamResponse(status=200, headers={}, chunks=[b"x" * 3_000_000, b"y" * 2_000_000])
        session, token = self.session(FakeUpstream([big]))
        result = self.request(session, token, {"messages": []}, request_id="big")
        self.assertEqual((result["outcome"], result["partial"]), ("partial", True))
        self.assertIn("bound", result["detail"])
        self.assertIsNone(self.scope.calls[result["call_id"]].reported_microusd)

        def slow():
            yield b"a"
            self.time[0] += 31
            yield b"b"

        session, token = self.session(FakeUpstream([UpstreamResponse(status=200, headers={}, chunks=slow())]))
        result = self.request(session, token, {"messages": []}, request_id="slow")
        self.assertEqual((result["outcome"], result["body"]), ("partial", b"ab"))
        self.assertIn("time cap", result["detail"])
        session, token = self.session(FakeUpstream([ConnectionError("reset")]))
        result = self.request(session, token, {"messages": []}, request_id="broken")
        self.assertEqual((result["outcome"], result["status"]), ("partial", None))
        self.assertIn("ConnectionError", result["detail"])
        summary = close_validation_scope(self.scope)
        self.assertEqual((summary["reported_microusd"], summary["unknown_calls"], summary["outcome"]), (None, 3, "failed"))
        self.assertEqual(self.ledger.observed[-1]["reported_microusd"], None, "an unknown bill reaches the ledger as unknown")

    def test_the_scope_bounds_what_a_channel_may_spend(self):
        session, token = self.session(FakeUpstream([ok({}), ok({})]), max_calls=2)
        self.request(session, token, {"messages": []}, request_id="a")
        self.request(session, token, {"messages": []}, request_id="b")
        with self.assertRaises(MeterRefused):
            # 3 x 50_000 > 120_000: the meter refuses before the gateway transmits.
            session2, token2 = self.session(FakeUpstream([ok({})]))
            self.request(session2, token2, {"messages": []}, request_id="c")
        with self.assertRaises(GatewayRefused):
            self.request(session, token, {"messages": []}, request_id="d")

    def test_channels_are_partitioned_by_spend_class_and_closed_sessions_refuse(self):
        with self.assertRaises(GatewayRefused):
            self.session(FakeUpstream([]), route=VALIDATION)
        scope = open_validation_scope(self.ledger, scope_class="validation-embedding", binding=BINDING, execution_id="run-2", attempt=1,
                                      limit_microusd=5_000, max_calls=5, request_sha256=digest("bundle-2"))
        upstream = FakeUpstream([ok({"data": []}, pid="emb-1")])
        session, token = reserve_session(scope, VALIDATION, upstream=upstream, credential=self.credential, clock=lambda: 0.0)
        with self.assertRaises(GatewayRefused):
            handle_model_request(session, token=token, method="POST", path="/v1/embeddings", headers={}, body=b"{}", request_id="x")
        result = handle_validation_request(session, token=token, method="POST", path="/v1/embeddings", headers={}, body=b'{"input":"q"}', request_id="e1")
        self.assertEqual(result["outcome"], "returned")
        record = close_session(session)
        self.assertEqual((record["closed"], record["calls"]), (True, 1))
        with self.assertRaises(GatewayRefused):
            handle_validation_request(session, token=token, method="POST", path="/v1/embeddings", headers={}, body=b"{}", request_id="e2")
        self.assertEqual(session._credential(), {}, "the credential closure is dropped at close")

    def test_cost_telemetry_is_extracted_conservatively(self):
        self.assertEqual(openrouter_cost_extractor(b'{"usage": {"cost": 0.5}}'), 500_000)
        for raw in (b"not json", b'{"usage": {"cost": "1"}}', b'{"usage": {"cost": -1}}', b'{"usage": {"cost": true}}', b"{}"):
            self.assertIsNone(openrouter_cost_extractor(raw), raw)
        self.assertEqual(sha256_bytes(b""), sha256_bytes(b""))


if __name__ == "__main__":
    unittest.main()
