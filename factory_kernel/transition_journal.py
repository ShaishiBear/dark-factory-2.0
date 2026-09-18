"""Append-only phase and effect receipts for a governed programme transition (C07, WP03).

Receipts are canonical private decision events appended through the one project append
primitive (`project_events`, C02): the same lock, hash chain, capacity and replay rules as
every other decision, no second journal. Each receipt records one legal phase event of
`programme_transition` and is validated INSIDE the lock against the receipts already in the
history, so a receipt can never record a skipped phase, an observation for a request that was
never made, or an observation under a different request id or digest than the request it
closes (C07: reuse the same request id/digest when observing; never create a new transition to
escape uncertainty).

The service writes a request receipt BEFORE a remote effect and an observation receipt AFTER
the independent observation of that effect. A crash between the two leaves the request pending
(the journal's derived phase is the request phase); the recovery observer marks it uncertain or
resolves it by re-observation. `compare_with_store` says whether the journal and the effect
store agree; a mismatch is `reconciliation_required`, not a choice of the side further ahead.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping

from .frontdoor_intent import IntentRefused, Principal
from .programme_transition import (
    EVENTS,
    OBSERVE_EVENT_OF,
    RECOVERY_EVENTS,
    REMOTE_REQUESTS,
    TransitionRefused,
    advance,
    compare_stores,
    is_uncertain,
    request_of,
    validate_phase,
)
from .project_events import OperationSpec, ProjectEvents, exact_replay

OPERATION = "transition-receipt"
RECEIPT_SCHEMA = "dark-factory/transition-receipt"
RECEIPT_VERSION = "1.0"
ROLES = frozenset({"owner", "service"})
HEX32 = re.compile(r"[0-9a-f]{32}")
HEX64 = re.compile(r"[0-9a-f]{64}")
REQUEST_EVENTS = frozenset(event for event, (_, after) in EVENTS.items() if after in REMOTE_REQUESTS)
CLOSING_EVENTS = frozenset(OBSERVE_EVENT_OF.values()) | RECOVERY_EVENTS | {"mark_uncertain"}


COMMAND_KEYS = ("transition_id", "event", "request_id", "request_sha256", "stop")


def receipt_replay(old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
    """A stored receipt replays a command when the command's five fields are the receipt's;
    the receipt's derived phases and effect are the service's, not the caller's."""
    return exact_replay({key: old.get(key) for key in COMMAND_KEYS}, dict(new))


def transition_operations() -> dict[str, OperationSpec]:
    """The registration a `ProjectEvents` needs to accept transition receipts."""
    return {OPERATION: OperationSpec(OPERATION, ROLES, receipt_replay)}


def receipts_of(events: list[dict], transition_id: str) -> list[dict]:
    """The receipt payloads for one transition, in history order."""
    return [deepcopy(event["command"]["payload"]) for event in events
            if event["command"]["operation"] == OPERATION and event["command"]["payload"].get("transition_id") == transition_id]


def verify(receipts: list[dict]) -> str:
    """Replay the receipts and return the derived phase. Every receipt must record exactly the
    phase the previous ones derive and a legal event from it; the first inconsistency refuses."""
    phase = "reviewed"
    for index, receipt in enumerate(receipts):
        if (receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("schema_version") != RECEIPT_VERSION
                or receipt.get("phase_before") != phase):
            raise IntentRefused(f"transition receipt {index} does not continue the recorded phase {phase!r}")
        try:
            phase = advance(phase, receipt["event"], stop=bool(receipt.get("stop")))
        except (TransitionRefused, KeyError) as exc:
            raise IntentRefused(f"transition receipt {index} records an illegal step: {exc}") from exc
        if receipt.get("phase_after") != phase:
            raise IntentRefused(f"transition receipt {index} records phase {receipt.get('phase_after')!r}, derived {phase!r}")
    return phase


def pending_request(receipts: list[dict]) -> dict | None:
    """The request receipt still open (its phase is the current request phase or its uncertain
    marker), or None."""
    phase = verify(receipts)
    request = phase if phase in REMOTE_REQUESTS else (request_of(phase) if is_uncertain(phase) else None)
    if request is None:
        return None
    for receipt in reversed(receipts):
        if receipt["event"] in REQUEST_EVENTS and receipt["phase_after"] == request:
            return receipt
    raise IntentRefused("derived phase is a pending request but no request receipt exists")


class TransitionJournal:
    def __init__(self, events: ProjectEvents, project: str) -> None:
        if OPERATION not in events.registry:
            raise IntentRefused("the project events registry does not accept transition receipts")
        self.events, self.project = events, project

    def receipts(self, transition_id: str, principal: Principal) -> list[dict]:
        return receipts_of(list(self.events.read(self.project, principal)), transition_id)

    def phase(self, transition_id: str, principal: Principal) -> str:
        return verify(self.receipts(transition_id, principal))

    def record(self, *, transition_id: str, event: str, principal: Principal, expected_version: int, idempotency_key: str,
               request_id: str | None = None, request_sha256: str | None = None, effect: Mapping[str, Any] | None = None,
               stop: bool = False, note: str = "") -> dict:
        """Append one receipt. Validation runs inside the store lock against the verified history.
        Returns the stored receipt payload with the resulting project version."""
        if not isinstance(transition_id, str) or not HEX64.fullmatch(transition_id):
            raise IntentRefused("transition_id must be 64 lowercase hex characters")
        if not isinstance(event, str) or not isinstance(note, str) or len(note) > 2000:
            raise IntentRefused("receipt event and note must be bounded strings")
        if effect is not None and not isinstance(effect, Mapping):
            raise IntentRefused("receipt effect must be a mapping or None")
        if event in REQUEST_EVENTS or event in CLOSING_EVENTS:
            if not isinstance(request_id, str) or not HEX32.fullmatch(request_id) \
                    or not isinstance(request_sha256, str) or not HEX64.fullmatch(request_sha256):
                raise IntentRefused(f"{event!r} needs the remote request id (32 hex) and request digest (64 hex)")
        elif request_id is not None or request_sha256 is not None:
            raise IntentRefused(f"{event!r} is not a remote request and carries no request id")

        def validate_transition(history: list[dict]) -> dict:
            prior = receipts_of(history, transition_id)
            before = verify(prior)
            try:
                after = advance(before, event, stop=bool(stop))
            except TransitionRefused as exc:
                raise IntentRefused(str(exc)) from exc
            if event in CLOSING_EVENTS:
                open_request = pending_request(prior)
                if open_request is None:
                    raise IntentRefused(f"{event!r} closes a remote request but none is pending")
                if open_request["request_id"] != request_id or open_request["request_sha256"] != request_sha256:
                    raise IntentRefused("an observation must reuse the pending request's id and digest; a different request is a new transition, not a recovery")
            elif event in REQUEST_EVENTS and any(r.get("request_id") == request_id for r in prior):
                raise IntentRefused("request id already used in this transition")
            return {"schema": RECEIPT_SCHEMA, "schema_version": RECEIPT_VERSION, "transition_id": transition_id,
                    "event": event, "phase_before": before, "phase_after": after, "stop": bool(stop),
                    "request_id": request_id, "request_sha256": request_sha256,
                    "effect": deepcopy(dict(effect)) if effect is not None else None, "note": note}

        # The command is what replay compares (receipt_replay); the receipt itself is computed
        # inside the lock from the verified history and is what gets stored.
        command = {"transition_id": transition_id, "event": event, "request_id": request_id,
                   "request_sha256": request_sha256, "stop": bool(stop)}
        appended = self.events.append(project=self.project, principal=principal, expected_version=expected_version,
                                      idempotency_key=idempotency_key, operation=OPERATION, payload=command,
                                      validate_transition=validate_transition)
        payload = deepcopy(appended.event["command"]["payload"])
        payload["project_version"] = appended.project_version
        payload["replayed"] = appended.replayed
        return payload

    def compare_with_store(self, transition_id: str, principal: Principal, store_state: str | None) -> str:
        return compare_stores(self.phase(transition_id, principal), store_state)


__all__ = ["OPERATION", "RECEIPT_SCHEMA", "TransitionJournal", "pending_request", "receipts_of", "transition_operations", "verify"]
