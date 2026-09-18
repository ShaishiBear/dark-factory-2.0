"""The loopback transport of the provider gateway (SPECIFICATION 6.2, WP02).

Every test runs the real server on a real socket against a fake upstream on another socket;
the process-under-validation side is plain urllib, and the launcher test runs the real
`HttpApp` with a stub child that calls the gateway the way the app does. Nothing here reaches
a paid provider."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HARNESS = ROOT / "harness"
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

from factory_kernel import gateway_server as gs  # noqa: E402
from factory_kernel.gateway_server import (GatewayServer, GatewayServerRefused, RecordingLedger, UrllibUpstream,  # noqa: E402
                                           close_validation_channels, load_gateway_policy, open_validation_channels,
                                           parse_gateway_policy)

REAL_KEY = "real-" + "k" * 24


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


appproc = _load("factory_appproc_gateway", HARNESS / "appproc.py")


def policy(**overrides) -> dict:
    base = {"upstream_origin": "https://openrouter.example", "bundle_limit_microusd": 5_000_000, "bundle_max_calls": 20,
            "routes": [{"route_id": "chat", "method": "POST", "path": "/v1/chat/completions", "model": "test/model",
                        "spend_class": "validation-llm", "ceiling_microusd": 100_000, "timeout_seconds": 20},
                       {"route_id": "embed", "method": "POST", "path": "/v1/embeddings", "model": "test/embed",
                        "spend_class": "validation-embedding", "ceiling_microusd": 10_000, "timeout_seconds": 20}]}
    base.update(overrides)
    return base


class FakeUpstream:
    """An OpenAI-shaped provider on loopback that records every request it sees."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.mode = "ok"
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _send(self, status, body, ctype="application/json", extra=None):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                outer.requests.append({"method": "POST", "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                       "body": body})
                if outer.mode.startswith("redirect"):
                    # 302 is what urllib's default opener follows on a POST (as a GET); 307 it does not.
                    self._send(302 if outer.mode == "redirect" else 307, b"", extra={"Location": f"http://127.0.0.1:{outer.port}/elsewhere"})
                elif outer.mode == "error":
                    self._send(500, json.dumps({"error": {"message": "upstream down"}}).encode())
                elif outer.mode == "stream":
                    self._send(200, b"data: {\"choices\":[]}\n\ndata: [DONE]\n\n", ctype="text/event-stream")
                else:
                    payload = json.loads(body.decode() or "{}")
                    self._send(200, json.dumps({"id": "resp-1", "model": payload.get("model"), "choices": [{"message": {"content": "hello"}}],
                                                "usage": {"cost": 0.0123}}).encode())

            def do_GET(self):
                outer.requests.append({"method": "GET", "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}})
                self._send(200, b"{}")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def call(base: str, path: str, token: str, body: dict | None, *, method: str = "POST", header: str = "Authorization") -> tuple[int, dict, str]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers[header] = f"Bearer {token}" if header == "Authorization" else token
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw.decode()) if raw.startswith(b"{") else {"raw": raw.decode()}), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        exc.close()
        return exc.code, json.loads(raw.decode()), exc.headers.get("Content-Type", "")


class PolicyTests(unittest.TestCase):
    def test_a_well_formed_policy_parses_to_its_routes(self) -> None:
        parsed = parse_gateway_policy(policy())
        self.assertEqual([r["route_id"] for r in parsed["routes"]], ["chat", "embed"])
        self.assertEqual(parsed["upstream_origin"], "https://openrouter.example")

    def test_refusals(self) -> None:
        cases = {
            "extra field": policy(extra=1),
            "http origin": policy(upstream_origin="http://openrouter.example"),
            "trailing slash": policy(upstream_origin="https://openrouter.example/"),
            "zero limit": policy(bundle_limit_microusd=0),
            "bool calls": policy(bundle_max_calls=True),
            "no routes": policy(routes=[]),
            "worker class": policy(routes=[{**policy()["routes"][0], "spend_class": "worker-model"}]),
            "GET route": policy(routes=[{**policy()["routes"][0], "method": "GET"}]),
            "query in path": policy(routes=[{**policy()["routes"][0], "path": "/v1/chat?x=1"}]),
            "empty model": policy(routes=[{**policy()["routes"][0], "model": ""}]),
            "zero ceiling": policy(routes=[{**policy()["routes"][0], "ceiling_microusd": 0}]),
            "float timeout": policy(routes=[{**policy()["routes"][0], "timeout_seconds": 1.5}]),
            "route field missing": policy(routes=[{k: v for k, v in policy()["routes"][0].items() if k != "model"}]),
            "duplicate path": policy(routes=[policy()["routes"][0], {**policy()["routes"][0], "route_id": "chat2"}]),
        }
        for name, value in cases.items():
            with self.subTest(name), self.assertRaises(GatewayServerRefused):
                parse_gateway_policy(value)

    def test_the_repository_kernel_policy_registers_no_gateway_yet(self) -> None:
        # The owner has not preregistered routes and ceilings; the launcher must refuse to enable
        # the gateway rather than invent them.
        self.assertIsNone(load_gateway_policy(ROOT / ".factory" / "kernel.json"))

    def test_a_kernel_policy_with_the_block_loads_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kernel.json"
            path.write_text(json.dumps({"validation": {"quick_command": "x", "gateway": policy()}}), encoding="utf-8")
            self.assertEqual(load_gateway_policy(path)["bundle_max_calls"], 20)
            path.write_text(json.dumps({"validation": {"gateway": policy(bundle_max_calls=0)}}), encoding="utf-8")
            with self.assertRaises(GatewayServerRefused):
                load_gateway_policy(path)


class TransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.upstream = FakeUpstream()
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = RecordingLedger()
        self.credential_calls = 0
        self.channels = None
        self.server = None

    def tearDown(self) -> None:
        if self.server is not None:
            self.server.stop()
        if self.channels is not None and not self.channels.closed:
            close_validation_channels(self.channels)
        self.upstream.close()
        self.tmp.cleanup()

    def open(self, **overrides) -> tuple[str, str]:
        def credential():
            self.credential_calls += 1
            return {"authorization": f"Bearer {REAL_KEY}"}

        self.channels = open_validation_channels(
            policy(**overrides), credential=credential, ledger=self.ledger,
            binding={"run_id": "r1", "run_attempt": 1, "source_sha": "a" * 40, "programme_sha256": "b" * 64},
            execution_id="exec-1", attempt=1, record_dir=self.tmp.name, upstream=UrllibUpstream(self.upstream.origin))
        self.server = GatewayServer(self.channels).start()
        return self.server.base_url, self.channels.shared_token

    def test_a_registered_route_reaches_the_upstream_with_the_credential_injected(self) -> None:
        base, token = self.open()
        status, body, ctype = call(base, "/v1/chat/completions", token, {"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual((status, body["choices"][0]["message"]["content"], ctype), (200, "hello", "application/json"))
        self.assertEqual(body["model"], "test/model")  # the route's model, injected by the contract
        seen = self.upstream.requests[-1]
        self.assertEqual(seen["headers"]["authorization"], f"Bearer {REAL_KEY}")
        self.assertNotIn(token, json.dumps(seen["headers"]))
        self.assertEqual(seen["path"], "/v1/chat/completions")
        self.assertEqual(json.loads(seen["body"].decode())["model"], "test/model")
        self.assertEqual(self.credential_calls, 1)

    def test_the_shared_token_never_equals_a_session_token_and_the_record_holds_neither(self) -> None:
        base, token = self.open()
        for channel in self.channels.channels.values():
            self.assertNotEqual(channel.token, token)
        summary = close_validation_channels(self.channels)
        text = json.dumps(summary)
        self.assertNotIn(token, text)
        self.assertNotIn(REAL_KEY, text)
        for channel in self.channels.channels.values():
            self.assertNotIn(channel.token, text)
            self.assertEqual(channel.session._credential(), {})  # the credential closure is dropped at close

    def test_a_wrong_token_or_no_token_is_refused_before_any_transmission(self) -> None:
        base, token = self.open()
        for presented, header in (("nope", "Authorization"), (None, "Authorization"), (token + "x", "x-api-key")):
            with self.subTest(presented=presented, header=header):
                status, body, _ = call(base, "/v1/chat/completions", presented, {"messages": []}, header=header)
                self.assertEqual((status, body["error"]["type"]), (401, "gateway_refused"))
        self.assertEqual(self.upstream.requests, [])
        status, _, _ = call(base, "/v1/chat/completions", token, {"messages": []}, header="x-api-key")
        self.assertEqual(status, 200)  # the token is accepted on either header the SDKs use

    def test_unregistered_paths_and_methods_are_refused_without_a_reservation(self) -> None:
        base, token = self.open()
        for method, path in (("POST", "/v1/completions"), ("GET", "/v1/chat/completions"), ("POST", "/v1/chat/completions/extra"),
                             ("GET", "/api/hello"), ("DELETE", "/v1/embeddings")):
            with self.subTest(method=method, path=path):
                status, body, _ = call(base, path, token, {"messages": []} if method != "GET" else None, method=method)
                self.assertEqual((status, body["error"]["type"]), (403, "gateway_refused"))
        self.assertEqual(self.upstream.requests, [])
        self.assertEqual(sum(len(s.calls) for s in self.channels.scopes.values()), 0)

    def test_a_query_string_does_not_widen_the_route(self) -> None:
        base, token = self.open()
        status, _, _ = call(base, "/v1/chat/completions?beta=true", token, {"messages": []})
        self.assertEqual(status, 200)
        self.assertEqual(self.upstream.requests[-1]["path"], "/v1/chat/completions")  # the route's path, not the caller's

    def test_a_foreign_model_name_is_refused(self) -> None:
        base, token = self.open()
        status, body, _ = call(base, "/v1/chat/completions", token, {"model": "other/model", "messages": []})
        self.assertEqual(status, 403)
        self.assertIn("model", body["error"]["message"])
        self.assertEqual(self.upstream.requests, [])

    def test_an_upstream_redirect_is_refused_and_never_followed(self) -> None:
        base, token = self.open()
        for mode in ("redirect", "redirect-307"):
            with self.subTest(mode):
                self.upstream.mode = mode
                self.upstream.requests.clear()
                status, body, _ = call(base, "/v1/chat/completions", token, {"messages": [mode]})
                self.assertEqual((status, body["error"]["message"]), (403, "redirect refused"))
                self.assertEqual([r["method"] for r in self.upstream.requests], ["POST"])  # no GET to the Location
        scope = self.channels.scopes["validation-llm"]
        self.assertEqual([c.outcome for c in scope.calls.values()], ["failed", "failed"])

    def test_an_upstream_error_status_passes_through_as_the_providers_envelope(self) -> None:
        self.upstream.mode = "error"
        base, token = self.open()
        status, body, _ = call(base, "/v1/chat/completions", token, {"messages": []})
        self.assertEqual((status, body["error"]["message"]), (500, "upstream down"))
        scope = self.channels.scopes["validation-llm"]
        self.assertEqual([(c.outcome, c.reported_microusd) for c in scope.calls.values()], [("failed", None)])

    def test_a_streaming_upstream_arrives_buffered_with_its_content_type(self) -> None:
        self.upstream.mode = "stream"
        base, token = self.open()
        status, body, ctype = call(base, "/v1/chat/completions", token, {"messages": [], "stream": True})
        self.assertEqual((status, ctype), (200, "text/event-stream"))
        self.assertIn("data: [DONE]", body["raw"])

    def test_the_bundle_limit_and_call_bound_refuse_further_calls(self) -> None:
        base, token = self.open(bundle_limit_microusd=150_000)  # room for one 100_000 chat call, not two
        self.assertEqual(call(base, "/v1/chat/completions", token, {"messages": [1]})[0], 200)
        status, body, _ = call(base, "/v1/chat/completions", token, {"messages": [2]})
        self.assertEqual((status, body["error"]["type"]), (429, "meter_refused"))
        self.assertEqual(len(self.upstream.requests), 1)
        self.server.stop()
        close_validation_channels(self.channels)
        base, token = self.open(bundle_max_calls=1)
        self.assertEqual(call(base, "/v1/embeddings", token, {"input": "x"})[0], 200)
        status, body, _ = call(base, "/v1/embeddings", token, {"input": "y"})
        self.assertEqual(status, 403)
        self.assertIn("call bound", body["error"]["message"])

    def test_closing_writes_meter_records_and_refuses_later_calls(self) -> None:
        base, token = self.open()
        call(base, "/v1/chat/completions", token, {"messages": [1]})
        call(base, "/v1/embeddings", token, {"input": "x"})
        summary = close_validation_channels(self.channels)
        self.assertEqual(summary["schema"], "dark-factory/validation-gateway-summary")
        self.assertEqual({k: v["calls"] for k, v in summary["sessions"].items()}, {"POST /v1/chat/completions": 1, "POST /v1/embeddings": 1})
        llm = summary["bundles"]["validation-llm"]
        self.assertEqual((llm["calls"], llm["reported_microusd"], llm["outcome"], llm["limit_microusd"]), (1, 12300, "returned", 5_000_000))
        self.assertEqual(summary["ledger"]["allowance"], None)
        self.assertEqual([e["kind"] for e in summary["ledger"]["events"]], ["reserve", "start", "reserve", "start", "observe", "observe"])
        records = sorted(Path(self.tmp.name).glob("meter-*.json"))
        self.assertEqual(len(records), 2)
        loaded = [json.loads(p.read_text(encoding="utf-8")) for p in records]
        self.assertEqual({r["scope_class"] for r in loaded}, {"validation-llm", "validation-embedding"})
        self.assertTrue(all(r["closed"] and r["authority"] == "spend-record-only" for r in loaded))
        status, body, _ = call(base, "/v1/chat/completions", token, {"messages": [3]})
        self.assertEqual((status, body["error"]["message"]), (503, "gateway is closed"))
        with self.assertRaises(GatewayServerRefused):
            close_validation_channels(self.channels)

    def test_process_environment_names_the_gateway_not_the_upstream(self) -> None:
        base, token = self.open()
        env = self.server.process_environment(base_url_variable="OPENROUTER_BASE_URL", key_variable="OPENROUTER_API_KEY")
        self.assertEqual(env, {"OPENROUTER_BASE_URL": base + "/v1", "OPENROUTER_API_KEY": token})
        self.assertNotIn(REAL_KEY, env.values())

    def test_a_reconnect_with_the_same_request_id_replays_without_a_second_transmission(self) -> None:
        base, token = self.open()
        req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps({"messages": [1]}).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "x-request-id": "same"})
        for _ in range(2):
            with urllib.request.urlopen(req, timeout=20) as resp:
                self.assertEqual(resp.status, 200)
        self.assertEqual(len(self.upstream.requests), 1)


class LauncherTests(unittest.TestCase):
    """`HttpApp` with FACTORY_VALIDATION_GATEWAY=1: the child gets the gateway, never the key."""

    def setUp(self) -> None:
        self.saved = {k: os.environ.get(k) for k in ("FACTORY_VALIDATION_GATEWAY", "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "ARTIFACTS_DIR")}
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        (self.root / ".factory").mkdir(parents=True)
        self.artifacts = Path(self.tmp.name) / "artifacts"
        self.artifacts.mkdir()
        os.environ["ARTIFACTS_DIR"] = str(self.artifacts)
        os.environ["FACTORY_VALIDATION_GATEWAY"] = "1"
        os.environ["OPENROUTER_API_KEY"] = REAL_KEY
        os.environ.pop("OPENROUTER_BASE_URL", None)
        self.upstream = FakeUpstream()
        self.stub = Path(__file__).with_name("gateway_stub_app.py")

    def tearDown(self) -> None:
        self.upstream.close()
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _cfg(self) -> dict:
        for path in (sys.executable, str(self.stub)):
            if any(ch.isspace() for ch in path):
                self.skipTest(f"appproc splits the start command on whitespace: {path!r}")
        return {"http": {"start": f"{sys.executable} {self.stub} --port {{port}}", "health_path": "/health",
                         "health_contains": "ok", "boot_timeout_s": 25}}

    def _write_policy(self, **overrides) -> None:
        (self.root / ".factory" / "kernel.json").write_text(json.dumps({"validation": {"gateway": policy(**overrides)}}), encoding="utf-8")

    def test_off_by_default_leaves_the_environment_alone(self) -> None:
        os.environ.pop("FACTORY_VALIDATION_GATEWAY")
        self.assertIsNone(appproc.HttpApp(self._cfg())._gateway_environment())

    def test_asking_for_the_gateway_without_a_policy_refuses_the_run(self) -> None:
        (self.root / ".factory" / "kernel.json").write_text(json.dumps({"validation": {"quick_command": "x"}}), encoding="utf-8")
        with mock.patch.object(appproc, "ROOT", self.root), self.assertRaises(appproc.AppDidNotStart) as ctx:
            appproc.HttpApp(self._cfg())._gateway_environment()
        self.assertIn("no validation.gateway policy", str(ctx.exception))

    def test_asking_for_the_gateway_without_a_credential_refuses_the_run(self) -> None:
        self._write_policy()
        os.environ["OPENROUTER_API_KEY"] = ""
        with mock.patch.object(appproc, "ROOT", self.root), self.assertRaises(appproc.AppDidNotStart) as ctx:
            appproc.HttpApp(self._cfg())._gateway_environment()
        self.assertIn("no OPENROUTER_API_KEY", str(ctx.exception))

    def test_the_child_reaches_the_provider_only_through_the_gateway(self) -> None:
        self._write_policy()
        out = io.StringIO()
        origin = self.upstream.origin
        with (mock.patch.object(appproc, "ROOT", self.root),
              mock.patch.object(gs, "UrllibUpstream", lambda _origin: UrllibUpstream(origin)),
              contextlib.redirect_stdout(out), appproc.HttpApp(self._cfg()) as app):
            status, body, _ = app.get("/ask")
            answer = json.loads(body)
            self.assertEqual((status, answer["upstream_status"], answer["body"]["choices"][0]["message"]["content"]), (200, 200, "hello"))
            self.assertTrue(answer["key_looks_like_channel_token"])
            self.assertEqual(answer["base"], app.gateway.base_url + "/v1")
            status, body, _ = app.get("/ask?wrong")
            self.assertEqual(json.loads(body)["upstream_status"], 403)
            summary_path = self.artifacts / f"validation-gateway-{app.port}.json"
        self.assertEqual(self.upstream.requests[-1]["headers"]["authorization"], f"Bearer {REAL_KEY}")
        self.assertEqual(len(self.upstream.requests), 1)
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(summary["sessions"]["POST /v1/chat/completions"]["calls"], 1)
        self.assertEqual(summary["bundles"]["validation-llm"]["reported_microusd"], 12300)
        self.assertEqual(summary["ledger"]["allowance"], None)
        meters = list(self.artifacts.glob("meter-*.json"))
        self.assertEqual(len(meters), 2)
        for meter in meters:
            self.assertNotIn(REAL_KEY, meter.read_text(encoding="utf-8"))
        printed = out.getvalue()
        self.assertIn("VALIDATION_GATEWAY_STARTED", printed)
        self.assertIn("ledger=recording-only", printed)
        self.assertIn("VALIDATION_GATEWAY_CLOSED", printed)
        self.assertNotIn(REAL_KEY, printed)
        self.assertNotIn(REAL_KEY, summary_path.read_text(encoding="utf-8"))
        self.assertIsNone(app.gateway)

    def test_a_child_that_never_becomes_healthy_still_closes_the_gateway(self) -> None:
        self._write_policy()
        cfg = self._cfg()
        cfg["http"]["health_path"] = "/never"
        cfg["http"]["boot_timeout_s"] = 3
        origin = self.upstream.origin
        with (mock.patch.object(appproc, "ROOT", self.root),
              mock.patch.object(gs, "UrllibUpstream", lambda _origin: UrllibUpstream(origin)),
              contextlib.redirect_stdout(io.StringIO()), self.assertRaises(appproc.AppDidNotStart)):
            with appproc.HttpApp(cfg):
                pass
        self.assertEqual(len(list(self.artifacts.glob("validation-gateway-*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
