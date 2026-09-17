"""The canonical project-decision journal's one append primitive (C02, section 3.2).

`IntentStore` already owns the lock, the file, the hash chain and the capacity rules for a
project's decision history. Four services appended to that history through their own copies
of the same sequence (intake, exploration, execution budget, replacement intent). This module
is that sequence written once, behind a registered-operation boundary, so a fifth service
cannot invent a new envelope or skip a step, and no second journal exists.

What it does not do: it does not decide what an operation means. Each service keeps its own
semantic validator and runs it, inside the lock, against the verified events. It does not
accept an operation from HTTP; a service selects its statically registered operation. It does
not infer a principal from a payload; transport constructs `Principal`. It does not change
one historical byte: the envelope, `created_at` source and chain rule are the ones the
services already wrote, which the byte-identical fixture test pins.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any, Callable, Mapping

from .canonical import canonical_bytes, sha256_value
from .frontdoor_intent import IntentRefused, IntentStore, Principal, _shape, _text
from .programme import parse_json

SCHEMA = "dark-factory/intent-event"
SCHEMA_VERSION = "1.0"
MAX_EVENTS = 10000
PAGE_LIMIT = 200


@dataclass(frozen=True)
class OperationSpec:
    """A statically registered operation: who may append it and how a replay is recognised.

    `replay_matches(old_payload, new_payload)` says whether an event already carrying the
    idempotency key records the same command. The legacy services compare different parts
    of their payloads (intake: the whole command; exploration and budget: the caller's
    request; replacement: the request digest), so the rule is the service's, not this
    module's. `roles` is the allowlist of principal roles; `owner` still needs the configured
    owner identity, which `IntentStore._authorize` enforces.
    """
    operation: str
    roles: frozenset[str]
    replay_matches: Callable[[Mapping[str, Any], Mapping[str, Any]], bool]


def exact_replay(old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
    return old == new


@dataclass(frozen=True)
class EventAppend:
    project_version: int
    event_sha256: str
    replayed: bool
    # The stored event (a copy) so a caller can build its own legacy projection/receipt.
    event: Mapping[str, Any]
    # The index of the event in the verified history (replay: the original's index).
    index: int


@dataclass(frozen=True)
class EventPage:
    project_version: int
    after_version: int
    events: tuple[Mapping[str, Any], ...]
    next_after: int | None
    status: str  # "ok" | "cursor_ahead"


class ProjectEvents:
    def __init__(self, store: IntentStore, registry: Mapping[str, OperationSpec]) -> None:
        self.store = store
        self.registry = dict(registry)
        for name, spec in self.registry.items():
            if not isinstance(spec, OperationSpec) or spec.operation != name or not spec.roles:
                raise ValueError(f"invalid operation registration: {name!r}")

    def _authenticate(self, principal: Principal) -> None:
        """Owner and proposal principals pass the store's own gate (owner identity checked
        there). Any other role must be one some registered operation explicitly allows."""
        if not isinstance(principal, Principal) or not principal.identity:
            raise IntentRefused("authenticated principal required")
        if principal.role in {"owner", "proposal"}:
            self.store._authorize(principal)
            return
        allowed = set().union(*(spec.roles for spec in self.registry.values()))
        if principal.role not in allowed:
            raise IntentRefused(f"principal role {principal.role!r} has no registered project operation")

    # ---------- reads ----------

    def read(self, project: str, principal: Principal) -> tuple[dict, ...]:
        """Verified history under the same lock and authorisation; copies, never the store's list."""
        self._authenticate(principal)
        with self.store._locked(project) as path:
            return tuple(deepcopy(event) for event in self.store._read(path))

    def page(self, project: str, principal: Principal, *, after_version: int, limit: int) -> EventPage:
        """Bounded read after a version cursor. A cursor beyond the verified version is
        `cursor_ahead`; the chain is never restarted to satisfy a client."""
        if type(after_version) is not int or after_version < 0:
            raise IntentRefused("after_version must be a nonnegative integer")
        if type(limit) is not int or not 1 <= limit <= PAGE_LIMIT:
            raise IntentRefused(f"limit must be an integer from 1 to {PAGE_LIMIT}")
        self._authenticate(principal)
        with self.store._locked(project) as path:
            events = self.store._read(path)
        version = len(events)
        if after_version > version:
            return EventPage(version, after_version, (), None, "cursor_ahead")
        rows = tuple(deepcopy(event) for event in events[after_version:after_version + limit])
        last = after_version + len(rows)
        return EventPage(version, after_version, rows, last if last < version else None, "ok")

    # ---------- the append primitive ----------

    def append(self, *, project: str, principal: Principal, expected_version: int, idempotency_key: str,
               operation: str, payload: Any, validate_transition: Callable[[list[dict]], Any]) -> EventAppend:
        """C02 sequence. `validate_transition(events)` runs inside the lock against the verified
        history and returns the payload to append, or raises. It is an in-process reviewed
        function chosen by the service; nothing here ever loads a validator from an event.

        Replay (same key, same actor, same command by the operation's rule) returns the original
        event without appending, even when the version has moved on. Any other reuse of the key
        is an `idempotency_conflict`. Only then is `expected_version` compared.
        """
        spec = self.registry.get(operation)
        if spec is None:
            raise IntentRefused(f"unknown project operation: {operation!r}")
        # Authenticate outside the lock: the operation's own role allowlist from its
        # registration; an owner additionally has to be the configured owner. A project
        # service holds an explicit allowlisted permission, never owner impersonation (C02).
        self._authenticate(principal)
        if principal.role not in spec.roles:
            raise IntentRefused(f"principal role {principal.role!r} may not append {operation!r}")
        _text(idempotency_key, 100)
        if type(expected_version) is not int or expected_version < 0:
            raise IntentRefused("expected project version must be a nonnegative integer")
        # Strictly parse and detach caller-owned mutable objects; refuse duplicate keys,
        # non-JSON values and non-finite numbers (C01) before anything is compared or stored.
        try:
            json.dumps(payload, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise IntentRefused("event payload must be finite JSON") from exc
        detached = parse_json(canonical_bytes(payload).decode("utf-8"))
        actor = {"identity": principal.identity, "role": principal.role}
        with self.store._locked(project) as path:
            events = self.store._read(path)
            for index, event in enumerate(events):
                old = event["command"]
                if old["idempotency_key"] != idempotency_key:
                    continue
                if (old["operation"] != operation or event["actor"] != actor
                        or not spec.replay_matches(old["payload"], detached)):
                    raise IntentRefused("idempotency key already used for another command or actor (idempotency_conflict)")
                return EventAppend(len(events), sha256_value(event), True, deepcopy(event), index)
            if expected_version != len(events):
                raise IntentRefused("stale project version; reload before changing intent")
            appended_payload = validate_transition(events)
            command = {"idempotency_key": idempotency_key, "expected_project_version": len(events),
                       "operation": operation, "payload": appended_payload}
            event = {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "project": project,
                     "repository": self.store.repository, "project_version": len(events) + 1,
                     "command": command, "actor": actor, "created_at": self.store._now(),
                     "previous": sha256_value(events[-1]) if events else None}
            if len(events) + 1 > MAX_EVENTS:
                raise IntentRefused("intent history capacity reached")
            events.append(event)
            self.store._write(path, events)  # capacity in bytes is refused there, before any write
            return EventAppend(len(events), sha256_value(event), False, deepcopy(event), len(events) - 1)


def verify_chain(events: list[dict], *, project: str, repository: str) -> None:
    """The chain rule `IntentStore._read` applies, available to readers of a detached copy."""
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise IntentRefused("invalid event list")
    previous = None
    for version, event in enumerate(events, 1):
        _shape(event, {"schema", "schema_version", "project", "repository", "project_version",
                       "command", "actor", "created_at", "previous"})
        if (event["project_version"] != version or event["previous"] != previous
                or event["project"] != project or event["repository"] != repository
                or event["schema"] != SCHEMA or event["schema_version"] != SCHEMA_VERSION):
            raise IntentRefused("broken event chain")
        previous = sha256_value(event)
