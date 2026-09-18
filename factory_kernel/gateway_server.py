"""A loopback HTTP transport for the provider gateway contract (SPECIFICATION 6.2, WP02).

`provider_gateway` decides what a channel permits; this module puts that decision on a socket
so a validation process that only knows an HTTP base URL and a bearer token can be pointed at
it. The process under validation never sees the upstream credential: it receives a shared
channel token, the gateway holds the credential in its own closure and injects it at
transmission, redirects are refused, every request is metered through a validation scope with
the preregistered per-call ceiling of its route, and the meter records are written where the
launcher says.

Boundaries this module does NOT cross. It is an in-process server today (the launcher runs it
in a thread of the harness process), not the separate-OS-identity broker of SPEC 5; it buffers
an upstream response before answering (the contract collects chunks), so a streaming upstream
reaches the caller as one body; it does not stop the process under validation from reaching the
upstream directly (egress containment is WP06). Thresholds are inputs: the owner's
`validation.gateway` policy in `.factory/kernel.json` names the upstream origin, the routes with
their models and ceilings, and the bundle limits; nothing here invents an allowance, and the
recording ledger the harness uses says so in every record.
"""
from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time
from typing import Any, Callable, Mapping
import urllib.error
import urllib.request

from .canonical import sha256_bytes
from .provider_gateway import MAX_SESSION_CALLS, GatewayRefused, GatewaySession, Route, UpstreamResponse, close_session, handle_validation_request, \
    openrouter_cost_extractor, reserve_session
from .validation_meter import MeterRefused, ValidationScope, close_validation_scope, open_validation_scope

POLICY_FIELDS = {"upstream_origin", "bundle_limit_microusd", "bundle_max_calls", "routes"}
ROUTE_FIELDS = {"route_id", "method", "path", "model", "spend_class", "ceiling_microusd", "timeout_seconds"}
VALIDATION_CLASSES = ("validation-llm", "validation-embedding", "validation-transcript")
MAX_UPSTREAM_CHUNK = 65536


class GatewayServerRefused(ValueError):
    pass


# ---- policy ------------------------------------------------------------------------------------

def parse_gateway_policy(value: Any) -> dict:
    """The owner's preregistered gateway policy: exact fields, positive integer limits, https
    upstream origin, one or more routes of validation spend classes."""
    if not isinstance(value, Mapping) or set(value) != POLICY_FIELDS:
        raise GatewayServerRefused(f"validation.gateway needs exactly {sorted(POLICY_FIELDS)}")
    origin = value["upstream_origin"]
    if not isinstance(origin, str) or not origin.startswith("https://") or origin.endswith("/") or "?" in origin:
        raise GatewayServerRefused("upstream_origin must be an https origin without a trailing slash")
    for key in ("bundle_limit_microusd", "bundle_max_calls"):
        if type(value[key]) is not int or value[key] <= 0:
            raise GatewayServerRefused(f"{key} must be a positive integer")
    routes = value["routes"]
    if not isinstance(routes, list) or not routes:
        raise GatewayServerRefused("routes must be a nonempty list")
    seen = set()
    parsed = []
    for raw in routes:
        if not isinstance(raw, Mapping) or set(raw) != ROUTE_FIELDS:
            raise GatewayServerRefused(f"each route needs exactly {sorted(ROUTE_FIELDS)}")
        if raw["method"] != "POST" or not isinstance(raw["path"], str) or not raw["path"].startswith("/") or "?" in raw["path"]:
            raise GatewayServerRefused("a route is a POST to an absolute path without a query")
        if raw["spend_class"] not in VALIDATION_CLASSES:
            raise GatewayServerRefused(f"route {raw['route_id']!r}: spend class must be a validation class")
        if not isinstance(raw["model"], str) or not raw["model"]:
            raise GatewayServerRefused(f"route {raw['route_id']!r}: model must be named")
        for key in ("ceiling_microusd", "timeout_seconds"):
            if type(raw[key]) is not int or raw[key] <= 0:
                raise GatewayServerRefused(f"route {raw['route_id']!r}: {key} must be a positive integer")
        if (raw["method"], raw["path"]) in seen:
            raise GatewayServerRefused(f"route {raw['route_id']!r} repeats a method and path")
        seen.add((raw["method"], raw["path"]))
        parsed.append(dict(raw))
    return {"upstream_origin": origin, "bundle_limit_microusd": value["bundle_limit_microusd"],
            "bundle_max_calls": value["bundle_max_calls"], "routes": parsed}


def load_gateway_policy(kernel_json: str | Path) -> dict | None:
    """The `validation.gateway` block of the kernel policy, parsed, or None when the owner has
    not registered one (the launcher then refuses to enable the gateway)."""
    raw = json.loads(Path(kernel_json).read_text(encoding="utf-8"))
    block = raw.get("validation", {}).get("gateway") if isinstance(raw.get("validation"), Mapping) else None
    return None if block is None else parse_gateway_policy(block)


# ---- ledger and transport ----------------------------------------------------------------------

class RecordingLedger:
    """A ledger that records reservations, starts and observations and holds NO allowance.

    The meter still enforces the preregistered ceilings and bundle limits against it, and every
    record carries `allowance: none` so nobody reads the bundle as funded. It exists for the
    validation lane, where the credential is the worker's and the owner's exchange is not yet
    reachable (the activation hold); the receipts it keeps are what billing reconciliation
    compares later."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self._versions = 0

    def reserve(self, call: Mapping[str, Any]) -> int:
        self._versions += 1
        self.events.append({"kind": "reserve", "call": dict(call), "version": self._versions})
        return self._versions

    def start(self, call: Mapping[str, Any], reserved_version: int) -> None:
        self.events.append({"kind": "start", "call_id": call["id"], "reserved_version": reserved_version})

    def observe(self, call: Mapping[str, Any], *, reported_microusd: int | None, outcome: str) -> None:
        self.events.append({"kind": "observe", "call_id": call["id"], "reported_microusd": reported_microusd, "outcome": outcome})

    def to_dict(self) -> dict:
        return {"ledger": "recording-only", "allowance": None,
                "note": "no owner allowance backs these reservations; ceilings and bundle limits are enforced by the meter only",
                "events": list(self.events)}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class UrllibUpstream:
    """The one transmission to the upstream origin: method, path, cleaned headers plus the
    injected credential, body, timeout; redirects are never followed; an HTTP error status is
    returned as a response (the caller gets the provider's envelope), a transport failure
    raises (the contract records the call as partial with unknown spend)."""

    def __init__(self, origin: str) -> None:
        self.origin = origin.rstrip("/")
        self._local = threading.local()

    def last_headers(self) -> dict:
        """The upstream response headers of the call this thread transmitted last (the handler
        thread that asked the contract to transmit is the thread that answers the caller)."""
        return dict(getattr(self._local, "headers", {}))

    def __call__(self, request: Mapping[str, Any]) -> UpstreamResponse:
        if request.get("follow_redirects", False):
            raise GatewayRefused("the transport never follows redirects")
        url = self.origin + request["path"]
        req = urllib.request.Request(url, data=request.get("body") or None, method=request["method"], headers=dict(request["headers"]))
        opener = urllib.request.build_opener(NoRedirect())
        timeout = int(request.get("timeout_seconds") or 60)
        try:
            response = opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            headers = {k.lower(): v for k, v in exc.headers.items()}
            body = exc.read()
            exc.close()
            self._local.headers = headers
            return UpstreamResponse(status=exc.code, headers=headers, chunks=[body], provider_request_id=headers.get("x-request-id"))
        headers = {k.lower(): v for k, v in response.headers.items()}
        self._local.headers = headers

        def chunks():
            with response:
                while True:
                    piece = response.read(MAX_UPSTREAM_CHUNK)
                    if not piece:
                        break
                    yield piece

        return UpstreamResponse(status=response.status, headers=headers, chunks=chunks(), provider_request_id=headers.get("x-request-id"))


# ---- channels ----------------------------------------------------------------------------------

@dataclass
class Channel:
    route: Route
    session: GatewaySession
    token: str  # the session's own token, held by the server only


@dataclass
class ValidationChannels:
    scopes: dict[str, ValidationScope]
    channels: dict[tuple[str, str], Channel]
    shared_token: str
    ledger: RecordingLedger | Any
    upstream: Any
    closed: bool = False
    counter: int = 0


def open_validation_channels(policy: Mapping[str, Any], *, credential: Callable[[], Mapping[str, str]], ledger: Any,
                             binding: Mapping[str, Any], execution_id: str, attempt: int, record_dir: str | Path | None,
                             upstream: Callable[[Mapping[str, Any]], UpstreamResponse] | None = None,
                             clock: Callable[[], float] = time.monotonic) -> ValidationChannels:
    """One meter scope per spend class the policy's routes use, one session per route, one
    shared channel token for the process under validation. The credential closure is the
    gateway's; the returned object never contains its value."""
    parsed = parse_gateway_policy(policy)
    transport = upstream if upstream is not None else UrllibUpstream(parsed["upstream_origin"])
    scopes: dict[str, ValidationScope] = {}
    channels: dict[tuple[str, str], Channel] = {}
    for raw in parsed["routes"]:
        spend_class = raw["spend_class"]
        if spend_class not in scopes:
            scopes[spend_class] = open_validation_scope(
                ledger, scope_class=spend_class, binding=binding, execution_id=f"{execution_id}:{spend_class}", attempt=attempt,
                limit_microusd=parsed["bundle_limit_microusd"], max_calls=parsed["bundle_max_calls"],
                request_sha256=sha256_bytes(json.dumps({"execution_id": execution_id, "attempt": attempt, "class": spend_class}, sort_keys=True).encode()),
                record_dir=record_dir)
        route = Route(route_id=raw["route_id"], method=raw["method"], path=raw["path"], model=raw["model"], spend_class=spend_class,
                      ceiling_microusd=raw["ceiling_microusd"], timeout_seconds=raw["timeout_seconds"],
                      cost_extractor=openrouter_cost_extractor)
        session, token = reserve_session(scopes[spend_class], route, upstream=transport, credential=credential, clock=clock,
                                         max_calls=min(parsed["bundle_max_calls"], MAX_SESSION_CALLS))
        channels[(route.method, route.path)] = Channel(route=route, session=session, token=token)
    return ValidationChannels(scopes=scopes, channels=channels, shared_token=secrets.token_urlsafe(32), ledger=ledger, upstream=transport)


def close_validation_channels(value: ValidationChannels) -> dict:
    """Close every session (dropping the credential closures) and every scope; the summary is
    what the launcher retains beside the meter records."""
    if value.closed:
        raise GatewayServerRefused("channels already closed")
    value.closed = True
    sessions = {f"{m} {p}": close_session(c.session) for (m, p), c in value.channels.items()}
    bundles = {cls: close_validation_scope(scope) for cls, scope in value.scopes.items()}
    ledger = value.ledger.to_dict() if hasattr(value.ledger, "to_dict") else {"ledger": type(value.ledger).__name__}
    return {"schema": "dark-factory/validation-gateway-summary", "schema_version": "1.0", "sessions": sessions, "bundles": bundles,
            "ledger": ledger}


# ---- the server --------------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    channels: ValidationChannels | None = None
    lock = threading.Lock()

    def log_message(self, *_):
        pass

    def _reply(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _refuse(self, status: int, message: str, kind: str = "gateway_refused") -> None:
        self._reply(status, json.dumps({"error": {"message": message, "type": kind}}).encode("utf-8"))

    def _any(self) -> None:
        value = self.channels
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if value is None or value.closed:
            self._refuse(503, "gateway is closed")
            return
        auth = self.headers.get("Authorization") or ""
        presented = auth[7:] if auth.startswith("Bearer ") else self.headers.get("x-api-key") or ""
        if not presented or sha256_bytes(presented.encode()) != sha256_bytes(value.shared_token.encode()):
            self._refuse(401, "channel token refused")
            return
        path = self.path.split("?", 1)[0]
        channel = value.channels.get((self.command, path))
        if channel is None:
            self._refuse(403, f"only the registered routes are permitted on this channel; {self.command} {path} is not one")
            return
        with type(self).lock:
            value.counter += 1
            request_id = self.headers.get("x-request-id") or f"g{value.counter}-{sha256_bytes(body)[:16]}"
        try:
            result = handle_validation_request(channel.session, token=channel.token, method=self.command, path=path,
                                               headers={k: v for k, v in self.headers.items()}, body=body, request_id=request_id)
        except MeterRefused as exc:
            self._refuse(429, f"meter refused: {exc}", "meter_refused")
            return
        except GatewayRefused as exc:
            self._refuse(403, str(exc))
            return
        last = getattr(value.upstream, "last_headers", None)
        content_type = (last() if callable(last) else {}).get("content-type", "application/json")
        if result["outcome"] == "partial":
            self._reply(502, json.dumps({"error": {"message": f"upstream response incomplete: {result['detail']}", "type": "gateway_partial",
                                                    "request_id": request_id}}).encode("utf-8"))
            return
        self._reply(int(result["status"] or 502), result["body"], content_type)

    do_POST = do_GET = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _any  # noqa: N815


class GatewayServer:
    """The loopback listener. `start` binds an ephemeral port and serves from a daemon thread;
    `stop` closes the listener. The channels are closed by the caller, not here."""

    def __init__(self, channels: ValidationChannels) -> None:
        self.channels = channels
        handler = type("BoundHandler", (_Handler,), {"channels": channels, "lock": threading.Lock()})
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> "GatewayServer":
        self._thread = threading.Thread(target=self._server.serve_forever, name="validation-gateway", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def process_environment(self, *, base_url_variable: str, key_variable: str, path_prefix: str = "/v1") -> dict[str, str]:
        """The two variables the process under validation receives instead of the upstream
        origin and credential: the gateway's base URL and the shared channel token."""
        return {base_url_variable: self.base_url + path_prefix, key_variable: self.channels.shared_token}


__all__ = ["Channel", "GatewayServer", "GatewayServerRefused", "RecordingLedger", "UrllibUpstream", "ValidationChannels",
           "close_validation_channels", "load_gateway_policy", "open_validation_channels", "parse_gateway_policy"]
