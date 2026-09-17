"""Metered spend scopes over the existing execution ledger (SPECIFICATION 6.2, contract C06, WP02).

No second allowance table. A validation scope is one reservation in the existing execution
budget (`reserve` -> `start` -> `observe`), taken as a prepaid bundle whose per-call ceilings
must sum within it. Nested calls (an application LLM turn, an embedding batch, a transcript
fetch, a diagnostic probe) each take their own one-use call identity inside the bundle:
reserve a ceiling, start at most once, observe a bounded receipt or an unknown. The bundle is
observed when the scope closes: the sum of known receipts, or unknown if any started call has
no known receipt, which the ledger turns into an unresolved attempt that freezes further spend
under the existing policy. Nothing is refunded and no failed start is reissued.

Spend classes are explicit and cannot borrow from each other: a scope opened for one class
refuses a call of another. The ledger behind the scope is the local `ExecutionBudget` on the
Front Door host or the authenticated `ExecutionClient` exchange from a hosted worker; the
meter itself never sees a credential, a provider or a clock beyond the values it is handed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import secrets
from typing import Any, Mapping, Protocol

from .canonical import canonical_bytes, sha256_bytes

SPEND_CLASSES = ("worker-model", "diagnostic-probe", "exploration", "validation-llm",
                 "validation-embedding", "validation-transcript", "maintenance")
CALL_STATES = ("reserved", "started", "observed", "not_started")
SHA256 = re.compile(r"[0-9a-f]{64}")
MAX_CALLS = 10000
MAX_MICROUSD = 1_000_000_000
RECORD_SCHEMA = "dark-factory/validation-meter"
RECORD_SCHEMA_VERSION = "1.0"


class MeterRefused(ValueError):
    """The meter refused to reserve, start, observe or close. Never a refund."""


class Ledger(Protocol):
    """The existing execution ledger, as the meter needs it. Two adapters exist below."""

    def reserve(self, call: Mapping[str, Any]) -> int: ...
    def start(self, call: Mapping[str, Any], reserved_version: int) -> None: ...
    def observe(self, call: Mapping[str, Any], *, reported_microusd: int | None, outcome: str) -> None: ...


class ExchangeLedger:
    """The hosted worker's authenticated exchange (`ExecutionClient.exchange`)."""

    def __init__(self, client: Any):
        self.client = client

    def reserve(self, call: Mapping[str, Any]) -> int:
        reservation = self.client.exchange("reserve", dict(call), {})
        if reservation.get("status") != "reserved":
            raise MeterRefused("historical reservation cannot authorize a validation bundle")
        return int(reservation["project_version"])

    def start(self, call: Mapping[str, Any], reserved_version: int) -> None:
        started = self.client.exchange("start", dict(call), {"reservation_version": reserved_version})
        if started.get("status") != "start-once":
            raise MeterRefused("validation bundle was already consumed")

    def observe(self, call: Mapping[str, Any], *, reported_microusd: int | None, outcome: str) -> None:
        self.client.exchange("observe", dict(call), {"reported_microusd": reported_microusd, "outcome": outcome})


class LocalLedger:
    """The Front Door host's own `ExecutionBudget` under the authenticated owner."""

    def __init__(self, budget: Any, project: str, principal: Any, observe_source: Any):
        self.budget, self.project, self.principal, self.observe_source = budget, project, principal, observe_source

    def reserve(self, call: Mapping[str, Any]) -> int:
        version = self.budget.snapshot(self.project, principal=self.principal)["project_version"]
        result = self.budget.reserve(self.project, {
            "idempotency_key": call["id"], "expected_project_version": version,
            "request": {"programme_sha256": call["programme_sha256"], "role": call["role"], "microusd": call["microusd"],
                        "attempt": call["attempt"], "execution_id": call["execution_id"]}},
            principal=self.principal, observe=self.observe_source)
        if not result["execute"]:
            raise MeterRefused("historical reservation cannot authorize a validation bundle")
        return int(result["state"]["reservations"][call["id"]]["reserved_project_version"])

    def start(self, call: Mapping[str, Any], reserved_version: int) -> None:
        result = self.budget.start(self.project, {
            "idempotency_key": call["id"] + ":start", "expected_project_version": reserved_version,
            "request": {"id": call["id"], "execution_id": call["execution_id"]}}, principal=self.principal)
        if not result["execute"]:
            raise MeterRefused("validation bundle was already consumed")

    def observe(self, call: Mapping[str, Any], *, reported_microusd: int | None, outcome: str) -> None:
        version = self.budget.snapshot(self.project, principal=self.principal)["project_version"]
        self.budget.observe(self.project, {
            "idempotency_key": call["id"] + ":observe", "expected_project_version": version,
            "request": {"id": call["id"], "reported_microusd": reported_microusd, "outcome": outcome}}, principal=self.principal)


@dataclass
class MeteredCall:
    call_id: str
    call_class: str
    microusd: int
    request_sha256: str
    state: str = "reserved"
    reported_microusd: int | None = None
    outcome: str | None = None
    telemetry: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"call_id": self.call_id, "call_class": self.call_class, "microusd": self.microusd,
                "request_sha256": self.request_sha256, "state": self.state, "reported_microusd": self.reported_microusd,
                "outcome": self.outcome, "telemetry": dict(self.telemetry)}


@dataclass
class ValidationScope:
    scope_id: str
    scope_class: str
    bundle: dict
    reserved_version: int
    limit_microusd: int
    max_calls: int
    ledger: Any
    record_path: Path | None = None
    calls: dict = field(default_factory=dict)
    closed: bool = False
    summary: dict | None = None

    def ceiling_sum(self) -> int:
        return sum(call.microusd for call in self.calls.values())

    def to_dict(self) -> dict:
        return {"schema": RECORD_SCHEMA, "schema_version": RECORD_SCHEMA_VERSION, "scope_id": self.scope_id,
                "scope_class": self.scope_class, "bundle_id": self.bundle["id"], "reserved_version": self.reserved_version,
                "limit_microusd": self.limit_microusd, "max_calls": self.max_calls, "ceiling_sum_microusd": self.ceiling_sum(),
                "calls": [call.to_dict() for call in self.calls.values()], "closed": self.closed, "summary": self.summary,
                "authority": "spend-record-only"}


def _record(scope: ValidationScope) -> None:
    if scope.record_path is not None:
        scope.record_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = scope.record_path.with_name(scope.record_path.name + ".partial")
        tmp.write_bytes(canonical_bytes(scope.to_dict()))
        tmp.replace(scope.record_path)


def _amount(value: Any, name: str, *, zero: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0 or (value == 0 and not zero) or value > MAX_MICROUSD:
        raise MeterRefused(f"{name} must be a positive integer of micro-dollars within bound")
    return value


def open_validation_scope(ledger: Ledger, *, scope_class: str, binding: Mapping[str, Any], execution_id: str, attempt: int,
                          limit_microusd: int, max_calls: int, request_sha256: str,
                          record_dir: str | Path | None = None) -> ValidationScope:
    """Reserve and durably start one bundle for `scope_class`. `binding` carries the ledger's
    call identity fields (run_id, run_attempt, source_sha, programme_sha256). Refuses an unknown
    class, a zero limit, or a ledger that reports the bundle as historical or already consumed."""
    if scope_class not in SPEND_CLASSES:
        raise MeterRefused(f"unknown spend class {scope_class!r}")
    _amount(limit_microusd, "limit_microusd")
    if not isinstance(max_calls, int) or isinstance(max_calls, bool) or not 1 <= max_calls <= MAX_CALLS:
        raise MeterRefused("max_calls must be a positive integer within bound")
    if not isinstance(request_sha256, str) or not SHA256.fullmatch(request_sha256):
        raise MeterRefused("bundle request digest must be a sha256")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        raise MeterRefused("attempt must be a positive integer")
    bundle = {**dict(binding), "id": secrets.token_hex(16), "role": scope_class, "microusd": limit_microusd,
              "request_sha256": request_sha256, "execution_id": str(execution_id), "attempt": attempt}
    reserved_version = ledger.reserve(bundle)
    ledger.start(bundle, reserved_version)
    scope = ValidationScope(scope_id=secrets.token_hex(8), scope_class=scope_class, bundle=bundle,
                            reserved_version=reserved_version, limit_microusd=limit_microusd, max_calls=max_calls,
                            ledger=ledger, record_path=(Path(record_dir) / f"meter-{bundle['id']}.json") if record_dir else None)
    _record(scope)
    return scope


def reserve_call(scope: ValidationScope, *, call_class: str, microusd: int, request_sha256: str) -> MeteredCall:
    """A per-call ceiling inside the bundle. The class must be the scope's own; the ceilings must
    sum within the bundle; the count must fit. A refused reservation records nothing."""
    if scope.closed:
        raise MeterRefused("validation scope is closed")
    if call_class != scope.scope_class:
        raise MeterRefused(f"scope {scope.scope_class!r} cannot spend for class {call_class!r}")
    _amount(microusd, "call ceiling")
    if not isinstance(request_sha256, str) or not SHA256.fullmatch(request_sha256):
        raise MeterRefused("call request digest must be a sha256")
    if len(scope.calls) + 1 > scope.max_calls:
        raise MeterRefused("validation scope call count would exceed its bundle")
    if scope.ceiling_sum() + microusd > scope.limit_microusd:
        raise MeterRefused("call ceilings would exceed the bundle reservation")
    call = MeteredCall(call_id=secrets.token_hex(12), call_class=call_class, microusd=microusd, request_sha256=request_sha256)
    scope.calls[call.call_id] = call
    _record(scope)
    return call


def start_call(scope: ValidationScope, call_id: str) -> MeteredCall:
    """One-use start. A second start of the same call refuses: a lost response after the first
    start is charged or unknown, never repeated."""
    call = scope.calls.get(call_id)
    if call is None or scope.closed:
        raise MeterRefused("unknown call or closed scope")
    if call.state != "reserved":
        raise MeterRefused("call was already started; a lost response cannot be reissued")
    call.state = "started"
    _record(scope)
    return call


def observe_call(scope: ValidationScope, call_id: str, *, reported_microusd: int | None, outcome: str,
                 telemetry: Mapping[str, Any] | None = None) -> MeteredCall:
    """A bounded receipt (or None for unknown) for a started call; a reserved-but-never-started
    call may be observed as `not_started`. The ceiling stays counted either way."""
    call = scope.calls.get(call_id)
    if call is None or scope.closed:
        raise MeterRefused("unknown call or closed scope")
    if outcome not in {"returned", "failed", "partial", "not_started"}:
        raise MeterRefused("unknown call outcome")
    if reported_microusd is not None:
        _amount(reported_microusd, "reported_microusd", zero=True)
    if call.state == "reserved":
        if outcome != "not_started" or reported_microusd not in (None, 0):
            raise MeterRefused("a call that never started can only be observed as not_started")
        call.state, call.outcome, call.reported_microusd = "not_started", outcome, 0
    elif call.state == "started":
        if outcome == "not_started":
            raise MeterRefused("a started call cannot be observed as not_started")
        call.state, call.outcome, call.reported_microusd = "observed", outcome, reported_microusd
    else:
        raise MeterRefused("call was already observed")
    call.telemetry = {str(k): v for k, v in (telemetry or {}).items()}
    _record(scope)
    return call


def close_validation_scope(scope: ValidationScope) -> dict:
    """Observe the bundle on the ledger: the sum of known receipts when every started call has one,
    else unknown (None), which the ledger records as an unresolved attempt. Calls still in flight
    are unknown. Idempotent once closed."""
    if scope.closed:
        return dict(scope.summary or {})
    known = 0
    unknown = 0
    for call in scope.calls.values():
        if call.state == "started":
            unknown += 1
        elif call.state == "observed" and call.reported_microusd is None:
            unknown += 1
        elif call.state == "observed":
            known += int(call.reported_microusd)
    reported = None if unknown else known
    outcome = "returned" if not unknown and all(c.state in {"observed", "not_started"} for c in scope.calls.values()) else "failed"
    scope.ledger.observe(scope.bundle, reported_microusd=reported, outcome=outcome)
    scope.closed = True
    scope.summary = {"bundle_id": scope.bundle["id"], "scope_class": scope.scope_class, "calls": len(scope.calls),
                     "ceiling_sum_microusd": scope.ceiling_sum(), "limit_microusd": scope.limit_microusd,
                     "reported_microusd": reported, "unknown_calls": unknown, "outcome": outcome}
    _record(scope)
    return dict(scope.summary)


def load_record(path: str | Path) -> dict:
    raw = Path(path).read_bytes()
    record = json.loads(raw.decode("utf-8"))
    if not isinstance(record, dict) or record.get("schema") != RECORD_SCHEMA:
        raise MeterRefused("not a validation meter record")
    return {**record, "sha256": sha256_bytes(raw)}


__all__ = ["CALL_STATES", "ExchangeLedger", "Ledger", "LocalLedger", "MeterRefused", "MeteredCall", "SPEND_CLASSES",
           "ValidationScope", "close_validation_scope", "load_record", "observe_call", "open_validation_scope",
           "reserve_call", "start_call"]
