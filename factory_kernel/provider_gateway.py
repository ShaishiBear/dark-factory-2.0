"""Bounded provider gateway contract (SPECIFICATION 5 "provider_gateway" and 6.2, contract C06, WP02).

A session is a per-invocation authenticated channel that permits exactly one configured
upstream operation (method, path, model) under fixed body, response and time limits, with
at-most-once accounting through a validation-meter scope. The caller side (a tool-bearing
worker, the application under validation) holds only the session token; the upstream credential
lives inside the gateway and is never returned, logged or forwarded to the caller. Caller
headers are stripped to an allowlist; the model and endpoint come from the protected route;
redirects are refused; a request id reused with a different body is refused; a reconnect for a
request already transmitted returns what was recorded and never re-sends upstream generation;
a stream cut short is recorded as a partial with unknown spend.

Transport is injected: `upstream(request) -> UpstreamResponse`. The fake transport in the tests
and a future native adapter share this contract. This module establishes the accounting and
channel rules, not network containment (WP06) and not provider billing (billing_reconciliation).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import secrets
from typing import Any, Callable, Iterable, Mapping

from .canonical import sha256_bytes, sha256_value
from .validation_meter import MeterRefused, ValidationScope, observe_call, reserve_call, start_call

ALLOWED_HEADERS = frozenset({"content-type", "accept", "anthropic-version", "anthropic-beta", "user-agent"})
STRIPPED_HEADERS = frozenset({"authorization", "x-api-key", "cookie", "proxy-authorization", "x-forwarded-for",
                              "x-forwarded-host", "host", "x-real-ip", "forwarded"})
MAX_REQUEST_BYTES = 1_000_000
MAX_RESPONSE_BYTES = 4_000_000
MAX_SESSION_CALLS = 1000
REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}")
OUTCOMES = ("returned", "failed", "partial", "refused")


class GatewayRefused(ValueError):
    pass


@dataclass(frozen=True)
class Route:
    """The protected description of the one operation a session may perform."""
    route_id: str
    method: str
    path: str
    model: str | None
    spend_class: str
    ceiling_microusd: int
    timeout_seconds: int
    # Telemetry extractor: the cost the upstream reports, in micro-dollars, or None. Telemetry,
    # never an invoice: the ceiling stands and reconciliation compares receipts later.
    cost_extractor: Callable[[bytes], int | None] | None = None


@dataclass
class UpstreamResponse:
    status: int
    headers: Mapping[str, str]
    chunks: Iterable[bytes]
    provider_request_id: str | None = None


@dataclass
class RecordedRequest:
    request_id: str
    body_sha256: str
    call_id: str
    outcome: str
    status: int | None
    body: bytes
    partial: bool
    provider_request_id: str | None
    detail: str = ""

    def to_dict(self) -> dict:
        return {"request_id": self.request_id, "body_sha256": self.body_sha256, "call_id": self.call_id,
                "outcome": self.outcome, "status": self.status, "response_sha256": sha256_bytes(self.body),
                "response_bytes": len(self.body), "partial": self.partial, "provider_request_id": self.provider_request_id,
                "detail": self.detail}


@dataclass
class GatewaySession:
    session_id: str
    route: Route
    scope: ValidationScope
    token_sha256: str
    max_calls: int
    _upstream: Callable[[Mapping[str, Any]], UpstreamResponse]
    _credential: Callable[[], Mapping[str, str]]
    clock: Callable[[], float]
    requests: dict = field(default_factory=dict)
    closed: bool = False

    def to_dict(self) -> dict:
        return {"session_id": self.session_id, "route_id": self.route.route_id, "scope_id": self.scope.scope_id,
                "calls": len(self.requests), "closed": self.closed,
                "requests": [row.to_dict() for row in self.requests.values()]}


def reserve_session(scope: ValidationScope, route: Route, *, upstream: Callable[[Mapping[str, Any]], UpstreamResponse],
                    credential: Callable[[], Mapping[str, str]], clock: Callable[[], float],
                    max_calls: int = MAX_SESSION_CALLS) -> tuple[GatewaySession, str]:
    """Open a channel bound to one meter scope and one route. Returns the session and the one
    secret the caller side gets: the channel token. The upstream credential provider is a
    closure the gateway calls at transmission time; its values never enter the session record."""
    if route.spend_class != scope.scope_class:
        raise GatewayRefused(f"route class {route.spend_class!r} differs from scope class {scope.scope_class!r}")
    if route.method.upper() not in {"GET", "POST"} or not route.path.startswith("/"):
        raise GatewayRefused("route must name a GET or POST path")
    if not isinstance(max_calls, int) or isinstance(max_calls, bool) or not 1 <= max_calls <= MAX_SESSION_CALLS:
        raise GatewayRefused("session call bound out of range")
    token = secrets.token_urlsafe(32)
    session = GatewaySession(session_id=secrets.token_hex(8), route=route, scope=scope, token_sha256=sha256_bytes(token.encode()),
                             max_calls=max_calls, _upstream=upstream, _credential=credential, clock=clock)
    return session, token


def _authenticate(session: GatewaySession, token: str) -> None:
    if session.closed:
        raise GatewayRefused("session is closed")
    if not isinstance(token, str) or sha256_bytes(token.encode()) != session.token_sha256:
        raise GatewayRefused("channel token refused")


def _clean_headers(headers: Mapping[str, str]) -> dict:
    cleaned = {}
    for name, value in headers.items():
        key = str(name).lower()
        if key in STRIPPED_HEADERS or key not in ALLOWED_HEADERS:
            continue
        cleaned[key] = str(value)[:200]
    return cleaned


def handle_request(session: GatewaySession, *, token: str, method: str, path: str, headers: Mapping[str, str],
                   body: bytes, request_id: str) -> dict:
    """The one transmission path. See the module docstring for the rules; every refusal before
    the meter reserves is `not_started`, every refusal after a start is charged or unknown."""
    _authenticate(session, token)
    route = session.route
    if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
        raise GatewayRefused("request id is malformed")
    if not isinstance(body, (bytes, bytearray)):
        raise GatewayRefused("request body must be bytes")
    body = bytes(body)
    if len(body) > MAX_REQUEST_BYTES:
        raise GatewayRefused("request body exceeds the gateway bound")
    body_sha = sha256_bytes(body)
    previous = session.requests.get(request_id)
    if previous is not None:
        if previous.body_sha256 != body_sha:
            raise GatewayRefused("request id collision with a different body")
        # A reconnect: what was recorded, never a second upstream generation.
        return {**previous.to_dict(), "replayed": True, "body": previous.body}
    if method.upper() != route.method.upper() or path != route.path:
        raise GatewayRefused(f"only {route.method} {route.path} is permitted on this channel")
    if len(session.requests) >= session.max_calls:
        raise GatewayRefused("session call bound reached")
    payload: Any = None
    if route.method.upper() == "POST":
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except (ValueError, UnicodeDecodeError) as exc:
            raise GatewayRefused(f"request body is not JSON: {exc}") from exc
        if route.model is not None:
            if not isinstance(payload, dict):
                raise GatewayRefused("request body must be a JSON object")
            if payload.get("model") not in (None, route.model):
                raise GatewayRefused("request names a model the route does not permit")
            payload["model"] = route.model
            body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    call = reserve_call(session.scope, call_class=route.spend_class, microusd=route.ceiling_microusd, request_sha256=body_sha)
    start_call(session.scope, call.call_id)
    record = RecordedRequest(request_id=request_id, body_sha256=body_sha, call_id=call.call_id, outcome="failed",
                             status=None, body=b"", partial=False, provider_request_id=None)
    session.requests[request_id] = record
    upstream_request = {"method": route.method.upper(), "path": route.path, "headers": {**_clean_headers(headers), **dict(session._credential())},
                        "body": body, "timeout_seconds": route.timeout_seconds, "follow_redirects": False}
    started_at = session.clock()
    collected = bytearray()
    partial = False
    detail = ""
    try:
        response = session._upstream(upstream_request)
        if 300 <= int(response.status) < 400:
            detail = "redirect refused"
            raise GatewayRefused(detail)
        record.status = int(response.status)
        record.provider_request_id = response.provider_request_id
        for chunk in response.chunks:
            collected.extend(bytes(chunk))
            if len(collected) > MAX_RESPONSE_BYTES:
                partial, detail = True, "response exceeded the gateway bound"
                break
            if session.clock() - started_at > route.timeout_seconds:
                partial, detail = True, "response exceeded the time cap"
                break
    except GatewayRefused:
        record.outcome, record.detail = "refused", detail
        observe_call(session.scope, call.call_id, reported_microusd=None, outcome="failed", telemetry={"detail": detail})
        raise
    except Exception as exc:  # transport failure after transmission: spend unknown
        partial, detail = True, f"{type(exc).__name__}: {str(exc)[:200]}"
    record.body = bytes(collected)
    record.partial = partial
    record.detail = detail
    if partial:
        record.outcome = "partial"
        observe_call(session.scope, call.call_id, reported_microusd=None, outcome="partial", telemetry={"detail": detail, "bytes": len(collected)})
    else:
        cost = None
        if route.cost_extractor is not None:
            try:
                cost = route.cost_extractor(record.body)
            except Exception:
                cost = None
        record.outcome = "returned" if record.status is not None and 200 <= record.status < 300 else "failed"
        observe_call(session.scope, call.call_id, reported_microusd=cost, outcome="returned" if record.outcome == "returned" else "failed",
                     telemetry={"status": record.status, "bytes": len(collected), "provider_request_id": record.provider_request_id})
    return {**record.to_dict(), "replayed": False, "body": record.body}


def handle_model_request(session: GatewaySession, **kwargs: Any) -> dict:
    if session.route.spend_class not in {"worker-model", "diagnostic-probe", "exploration", "maintenance"}:
        raise GatewayRefused("this channel is not a model channel")
    return handle_request(session, **kwargs)


def handle_validation_request(session: GatewaySession, **kwargs: Any) -> dict:
    if session.route.spend_class not in {"validation-llm", "validation-embedding", "validation-transcript"}:
        raise GatewayRefused("this channel is not a validation channel")
    return handle_request(session, **kwargs)


def close_session(session: GatewaySession) -> dict:
    """Close the channel. Calls still in flight stay unknown; the scope's own close observes the
    bundle. The credential closure is dropped here."""
    session.closed = True
    session._credential = lambda: {}
    return session.to_dict()


def openrouter_cost_extractor(body: bytes) -> int | None:
    """OpenRouter reports `usage.cost` in dollars when asked; a missing or malformed value is unknown."""
    try:
        value = json.loads(body.decode("utf-8"))
        cost = value.get("usage", {}).get("cost")
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
        return None
    return int(round(cost * 1_000_000))


__all__ = ["ALLOWED_HEADERS", "GatewayRefused", "GatewaySession", "MAX_REQUEST_BYTES", "MAX_RESPONSE_BYTES", "Route",
           "STRIPPED_HEADERS", "UpstreamResponse", "close_session", "handle_model_request", "handle_request",
           "handle_validation_request", "openrouter_cost_extractor", "reserve_session"]
