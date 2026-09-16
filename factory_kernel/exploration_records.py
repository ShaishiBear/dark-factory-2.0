"""Exploration events in the existing project log, never a second proof lifecycle."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from .canonical import canonical_bytes, sha256_value
from .frontdoor_intent import IntentRefused, Principal, _shape, _text
from .programme import _id, parse_json

OPERATION = "preflight-event"


def approved_scope(store, events):
    state = store._snapshot(events)
    if not state["approvals"]:
        raise IntentRefused("exploration requires approved intent")
    approval = state["approvals"][-1]
    latest = next(row for row in reversed(state["ledger"]) if row["kind"] == "record-intent")
    if approval["source_intent_version"] != latest["version"]:
        raise IntentRefused("new intent needs its own approval")
    return approval


def projection(events):
    result = {"sessions": {}, "claims": {}, "budgets": {}, "feedback": {}, "adaptive_runs": {}, "project_version": len(events)}
    for event in events:
        if event["command"]["operation"] != OPERATION:
            continue
        payload = event["command"]["payload"]
        kind, data, key = payload["kind"], payload["data"], payload["session_id"]
        if kind == "opened":
            result["sessions"][key] = {**deepcopy(data), "id": key, "candidates": {},
                "observations": [], "reservations": {}, "recommendations": [], "handoffs": [], "assessments": [],
                "reopenings": [], "round": 1, "status": "exploring"}
            budget = data["binding"]["spec_sha256"]
            result["budgets"].setdefault(budget, {"limits": deepcopy(data["policy"]["budget"]),
                                                "calls": 0, "usd": 0, "probe_units": 0, "uncertain": False})
        elif kind == "candidates-added":
            session = result["sessions"][key]
            for claim in data["claims"]:
                result["claims"][claim["id"]] = {**deepcopy(claim), "status": "active", "observations": [],
                    "spec_sha256": session["binding"]["spec_sha256"]}
            for candidate in data["candidates"]:
                session["candidates"][candidate["id"]] = {**deepcopy(candidate),
                    "predictions_context_identity": session["context"]["identity"]}
        elif kind == "reserved":
            session = result["sessions"][key]
            session["reservations"][data["id"]] = {**deepcopy(data), "status": "pending"}
            budget = result["budgets"][session["binding"]["spec_sha256"]]
            for name in ("calls", "usd", "probe_units"):
                budget[name] += data[name]
        elif kind == "observed":
            session = result["sessions"][key]
            session["reservations"][data["reservation_id"]]["status"] = data["status"]
            session["observations"].append(deepcopy(data))
            reservation = session["reservations"][data["reservation_id"]]
            if reservation["calls"] and (data.get("reported_usd") is None
                                          or data["reported_usd"] > reservation["usd"]):
                result["budgets"][session["binding"]["spec_sha256"]]["uncertain"] = True
            for observation in data.get("claim_observations", []):
                claim = result["claims"][observation["claim_id"]]
                claim["observations"].append(deepcopy(observation))
                if observation["outcome"] == "contradicted":
                    claim["status"] = "invalidated"
                    invalidate_recommendations(result, observation["claim_id"], "invalidated")
        elif kind == "recommended":
            session = result["sessions"][key]
            session["recommendations"].append(deepcopy(data))
            session["status"] = "recommended"
        elif kind == "assessed":
            result["sessions"][key]["assessments"].append(deepcopy(data))
        elif kind == "claim-observed":
            claim = result["claims"][data["claim_id"]]
            claim["status"] = data["status"]
            claim["observations"].append(deepcopy(data))
            invalidate_recommendations(result, data["claim_id"], data["status"])
        elif kind == "reopened":
            session = result["sessions"][key]
            session["round"] += 1
            session["status"] = "exploring"
            session["context"] = deepcopy(data["context"])
            session["reopenings"].append(deepcopy(data))
        elif kind == "factory-feedback":
            result["feedback"][data["id"]] = deepcopy(data)
            for claim_id in data["judgment"]["claim_ids"]:
                claim = result["claims"][claim_id]
                claim["status"] = "invalidated"
                claim["observations"].append({"feedback_sha256": data["id"], "status": "invalidated",
                    "evidence_class": "owner-causal-assessment", "qualification_status": "UNPROVEN"})
                invalidate_recommendations(result, claim_id, "invalidated")
        elif kind == "adaptive-run-started":
            result["adaptive_runs"][data["id"]] = deepcopy(data)
        elif kind == "adaptive-run-finished":
            result["adaptive_runs"][data["id"]].update(deepcopy(data))
        elif kind == "handoff":
            result["sessions"][key]["handoffs"].append(deepcopy(data))
        else:
            raise IntentRefused("unknown canonical exploration event")
    return result


def invalidate_recommendations(state, claim_id, status):
    affected = affected_claims(state["claims"], {claim_id})
    for session in state["sessions"].values():
        if session["recommendations"] and affected.intersection(session["recommendations"][-1]["claim_ids"]):
            session["status"] = "reconsideration-required" if status == "invalidated" else "challenged"


def affected_claims(claims, changed):
    affected = set(changed)
    while True:
        enlarged = affected | {key for key, claim in claims.items() if affected.intersection(claim["depends_on"])}
        if enlarged == affected:
            return affected
        affected = enlarged


class ExplorationRecords:
    """Shares IntentStore locking, versioning, identity and atomic append semantics.

    Only the service calls append with a validated payload. The ordinary intake execute
    endpoint refuses this operation; JSON cannot manufacture a principal or probe result.
    Hashes detect drift, not a malicious writer with access to the service directory.
    """

    def __init__(self, store):
        self.store = store

    def read(self, project, principal):
        self._owner(principal)
        with self.store._locked(project) as path:
            events = self.store._read(path)
            return projection(events), approved_scope(self.store, events)

    def _owner(self, principal):
        self.store._authorize(principal)
        if not isinstance(principal, Principal) or principal.role != "owner":
            raise IntentRefused("exploration is owner scoped")

    def append(self, project, principal, command, transition, replay_guard):
        self._owner(principal)
        command = parse_json(canonical_bytes(command).decode())
        _shape(command, {"idempotency_key", "expected_project_version", "session_id", "request"})
        _text(command["idempotency_key"], 100)
        _id(command["session_id"])
        if type(command["expected_project_version"]) is not int:
            raise IntentRefused("project version must be an integer")
        actor = {"identity": principal.identity, "role": principal.role}
        with self.store._locked(project) as path:
            events = self.store._read(path)
            for event in events:
                old = event["command"]
                if old["idempotency_key"] == command["idempotency_key"]:
                    if (old["operation"] != OPERATION or old["payload"]["request"] != command
                            or event["actor"] != actor):
                        raise IntentRefused("idempotency key already used")
                    replay_guard(projection(events), approved_scope(self.store, events), old["payload"])
                    return projection(events), deepcopy(old["payload"]), False
            if command["expected_project_version"] != len(events):
                raise IntentRefused("stale project version")
            state, approval = projection(events), approved_scope(self.store, events)
            kind, data = transition(state, approval)
            payload = {"session_id": command["session_id"], "request": command, "kind": kind, "data": data}
            if len(canonical_bytes(payload)) > 150000:
                raise IntentRefused("exploration event exceeds size bound")
            event = {"schema": "dark-factory/intent-event", "schema_version": "1.0", "project": project,
                     "repository": self.store.repository, "project_version": len(events) + 1,
                     "command": {"idempotency_key": command["idempotency_key"],
                         "expected_project_version": len(events), "operation": OPERATION, "payload": payload},
                     "actor": actor, "created_at": datetime.now(timezone.utc).isoformat(),
                     "previous": sha256_value(events[-1]) if events else None}
            events.append(event)
            self.store._write(path, events)
            return projection(events), deepcopy(payload), True
