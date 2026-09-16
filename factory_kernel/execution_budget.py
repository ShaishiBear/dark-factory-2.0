"""Durable cumulative reservations; no qualification or programme activation authority.

The owner approves a project allowance once. A trusted executor charges a whole attempt
before starting it. Reservations are never refunded, including crashes and lost responses.
This local service is not a remote worker authentication protocol.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re

from .canonical import canonical_bytes, sha256_value
from .exploration_records import approved_scope
from .frontdoor_intent import IntentRefused, _shape, _text
from .programme import compile_programme, parse_json
from .publication_source import observe_publication_source

OPERATION = "execution-budget-event"
MAX_MICROUSD = 1_000_000_000  # Representation bound, never an implicit owner allowance.
MAX_CALLS = 10000


def _integer(value, maximum, *, zero=False):
    if type(value) is not int or not (0 if zero else 1) <= value <= maximum:
        raise IntentRefused("execution budget requires bounded integer amounts")
    return value


def microusd(value):
    """No binary rounding, NaN, infinity or negative reported spend."""
    if type(value) not in (int, float, str):
        raise IntentRefused("invalid dollar amount")
    try:
        amount = Decimal(str(value)) * 1_000_000
        if not amount.is_finite() or amount < 0 or amount > MAX_MICROUSD:
            raise IntentRefused("invalid dollar amount")
        # Round up: a fractional microdollar cannot create spending capacity.
        return int(amount.to_integral_value(rounding="ROUND_CEILING"))
    except InvalidOperation as exc:
        raise IntentRefused("invalid dollar amount") from exc


def projection(events):
    result = {"status": "not-approved", "allowance": None, "opening": None,
              "reserved_microusd": 0, "calls": 0, "reservations": {},
              "uncertain": False, "overrun": False, "reset_allowed": False,
              "refund_allowed": False, "project_version": len(events),
              "enforcement": "local-executor-only-hosted-worker-not-connected"}
    for event in events:
        if event["command"]["operation"] != OPERATION:
            continue
        payload = event["command"]["payload"]
        kind, data = payload["kind"], payload["data"]
        if kind == "approved":
            if result["allowance"] is not None:
                raise IntentRefused("execution allowance cannot be reset")
            result["allowance"] = deepcopy(data)
            result["opening"] = deepcopy(data["opening"])
        elif kind == "reserved":
            if result["allowance"] is None or data["id"] in result["reservations"]:
                raise IntentRefused("execution reservation history is invalid")
            result["reservations"][data["id"]] = {**deepcopy(data), "observation": None}
            result["reserved_microusd"] += data["microusd"]
            result["calls"] += 1
        elif kind == "observed":
            row = result["reservations"].get(data["id"])
            if row is None or row["observation"] is not None:
                raise IntentRefused("execution observation history is invalid")
            row["observation"] = deepcopy(data)
        else:
            raise IntentRefused("unknown execution budget event")
    if result["allowance"] is not None:
        for row in result["reservations"].values():
            observation = row["observation"]
            if observation is None or observation["reported_microusd"] is None:
                result["uncertain"] = True
            elif observation["reported_microusd"] > row["microusd"]:
                result["overrun"] = True
        result["status"] = ("historical-spend-unknown" if result["opening"]["status"] != "verified-empty" else
                            "overrun" if result["overrun"] else "unresolved-attempt" if result["uncertain"] else
                            "exhausted" if result["calls"] >= result["allowance"]["max_calls"] or
                            result["reserved_microusd"] >= result["allowance"]["limit_microusd"] else "available")
    return result


def observe_opening(github, spec, app_login):
    """Only a never-materialized scope with no active programme gets an empty opening.

    A current/previous programme is not backfilled from model-reported totals. Unknown
    historical spending requires separate independently verified migration support.
    """
    source = observe_publication_source(github)
    issues = github.programme_issues()  # Adapter must establish a complete inventory.
    if not isinstance(issues, list):
        raise IntentRefused("execution opening inventory unavailable")
    prefix = f"Specification: {spec['id']} v"
    seen = [row for row in issues if row.get("user", {}).get("login") == app_login
            and prefix in str(row.get("body", ""))]
    if source != observe_publication_source(github) or issues != github.programme_issues():
        raise IntentRefused("execution opening changed during observation")
    return {"status": "verified-empty" if source["active_input"] is None and not seen else "unknown",
            "source_sha": source["main_sha"], "inventory_sha256": sha256_value(issues),
            "scope_id": spec["id"], "basis": "protected-source-and-complete-programme-inventory"}


class ExecutionBudget:
    def __init__(self, store):
        self.store = store

    def _owner(self, principal):
        self.store._authorize(principal)
        if principal.role != "owner":
            raise IntentRefused("execution budgets require the authenticated owner")

    def snapshot(self, project, *, principal):
        self._owner(principal)
        with self.store._locked(project) as path:
            return projection(self.store._read(path))

    def _append(self, path, events, principal, command, kind, data):
        events.append({"schema": "dark-factory/intent-event", "schema_version": "1.0",
            "project": path.stem, "repository": self.store.repository, "project_version": len(events) + 1,
            "command": {"idempotency_key": command["idempotency_key"], "expected_project_version": len(events),
                        "operation": OPERATION, "payload": {"request": deepcopy(command), "kind": kind, "data": data}},
            "actor": {"identity": principal.identity, "role": principal.role},
            "created_at": datetime.now(timezone.utc).isoformat(),
            "previous": sha256_value(events[-1]) if events else None})
        self.store._write(path, events)
        return projection(events)

    @staticmethod
    def _command(command):
        command = parse_json(canonical_bytes(command).decode())
        _shape(command, {"idempotency_key", "expected_project_version", "request"})
        _text(command["idempotency_key"], 100)
        _integer(command["expected_project_version"], 10000, zero=True)
        return command

    @staticmethod
    def _replay(events, command, kind):
        for event in events:
            old = event["command"]
            if old["idempotency_key"] == command["idempotency_key"]:
                if (old["operation"] != OPERATION or old["payload"]["kind"] != kind
                        or old["payload"]["request"] != command):
                    raise IntentRefused("execution idempotency key already used")
                return deepcopy(old["payload"]["data"])
        if len(events) != command["expected_project_version"]:
            raise IntentRefused("stale project version")
        return None

    def approve(self, project, command, *, principal, github, app_login):
        self._owner(principal)
        command = self._command(command)
        request = command["request"]
        _shape(request, {"spec_sha256", "limit_microusd", "max_calls"})
        _integer(request["limit_microusd"], MAX_MICROUSD)
        _integer(request["max_calls"], MAX_CALLS)
        if github.repository != self.store.repository:
            raise IntentRefused("execution budget repository differs from owner scope")
        with self.store._locked(project) as path:
            events = self.store._read(path)
            if self._replay(events, command, "approved") is not None:
                return projection(events)
            approval = approved_scope(self.store, events)
            if approval["spec_sha256"] != request["spec_sha256"]:
                raise IntentRefused("execution budget requires current approved scope")
            if projection(events)["allowance"] is not None:
                raise IntentRefused("project already has an execution allowance; replacement cannot reset it")
            head = sha256_value(events[-1])
        opening = observe_opening(github, approval["spec"], app_login)
        with self.store._locked(project) as path:
            events = self.store._read(path)
            if len(events) != command["expected_project_version"] or sha256_value(events[-1]) != head:
                raise IntentRefused("owner scope changed during budget approval")
            data = {**request, "scope_id": approval["spec"]["id"], "opening": opening}
            return self._append(path, events, principal, command, "approved", data)

    def reserve(self, project, command, *, principal, observe):
        """Trusted local executor only. Replay reports history and NEVER grants another call.

        observe is an authenticated executor's independent source/control read, never a
        caller-supplied JSON observation. Remote transport is deliberately not exposed.
        """
        self._owner(principal)
        command = self._command(command)
        request = command["request"]
        _shape(request, {"programme_sha256", "role", "microusd", "attempt", "execution_id"})
        _integer(request["microusd"], MAX_MICROUSD)
        _integer(request["attempt"], MAX_CALLS)
        for key in ("role", "execution_id"):
            _text(request[key], 100)
        if not isinstance(request["programme_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", request["programme_sha256"]):
            raise IntentRefused("execution reservation requires an exact programme")
        source = observe()
        programme = compile_programme(source["active_input"], repository=self.store.repository)
        if (source["repository"] != self.store.repository or source["protected"] is not True
                or source["stop"] != {"state": "clear", "issues": []}
                or programme.sha256 != request["programme_sha256"]):
            raise IntentRefused("execution reservation source, stop or programme differs")
        with self.store._locked(project) as path:
            events = self.store._read(path)
            if self._replay(events, command, "reserved") is not None:
                return {"state": projection(events), "execute": False}
            state = projection(events)
            approval = approved_scope(self.store, events)
            if state["status"] != "available":
                raise IntentRefused("execution budget is not available: " + state["status"])
            if (programme.spec != approval["spec"] or programme.spec["id"] != state["allowance"]["scope_id"]):
                raise IntentRefused("execution programme differs from current approved scope")
            if state["reserved_microusd"] + request["microusd"] > state["allowance"]["limit_microusd"]:
                raise IntentRefused("execution reservation exceeds cumulative dollar allowance")
            # An attempt has one identity across process restarts and programme generations.
            if any(row["execution_id"] == request["execution_id"] and row["attempt"] == request["attempt"]
                   for row in state["reservations"].values()):
                raise IntentRefused("execution attempt was already reserved")
            if source != observe():
                raise IntentRefused("execution source changed before reservation")
            data = {**request, "id": command["idempotency_key"], "source_sha": source["main_sha"],
                    "spec_sha256": approval["spec_sha256"]}
            state = self._append(path, events, principal, command, "reserved", data)
            return {"state": state, "execute": True}

    def observe(self, project, command, *, principal):
        """Record telemetry without refunding the reservation or granting another attempt."""
        self._owner(principal)
        command = self._command(command)
        request = command["request"]
        _shape(request, {"id", "reported_microusd", "outcome"})
        _text(request["id"], 100)
        if request["reported_microusd"] is not None:
            _integer(request["reported_microusd"], MAX_MICROUSD, zero=True)
        if request["outcome"] not in {"returned", "failed"}:
            raise IntentRefused("unknown execution observation")
        with self.store._locked(project) as path:
            events = self.store._read(path)
            if self._replay(events, command, "observed") is not None:
                return projection(events)
            row = projection(events)["reservations"].get(request["id"])
            if row is None or row["observation"] is not None:
                raise IntentRefused("execution reservation missing or already observed")
            return self._append(path, events, principal, command, "observed", deepcopy(request))

    def run(self, project, *, principal, provider, request, observe_source,
            programme_sha256, execution_id, attempt, reservation_id, transcript=None):
        """Execute one bounded local attempt. The reservation survives every exit path.

        A provider retry has unknown spend until its first attempt's telemetry returns,
        so this adapter refuses internal retry. A later explicitly new attempt must pass
        the ledger again. Existing hosted factory workers do not yet use this adapter.
        """
        bound = microusd(request.max_budget_usd)
        command = {"idempotency_key": reservation_id,
            "expected_project_version": self.snapshot(project, principal=principal)["project_version"],
            "request": {"programme_sha256": programme_sha256, "role": request.role, "microusd": bound,
                        "attempt": attempt, "execution_id": execution_id}}
        reserved = self.reserve(project, command, principal=principal, observe=observe_source)
        if not reserved["execute"]:
            raise IntentRefused("historical execution reservation cannot authorize another call")

        def no_unobserved_retry(_attempt):
            raise IntentRefused("previous execution attempt has unresolved spend; retry refused")

        def record(cost, outcome):
            try:
                reported = microusd(cost) if cost is not None else None
            except IntentRefused:
                reported = None
            self.observe(project, {"idempotency_key": "observed-" + reservation_id,
                "expected_project_version": self.snapshot(project, principal=principal)["project_version"],
                "request": {"id": reservation_id, "reported_microusd": reported, "outcome": outcome}}, principal=principal)

        try:
            # Re-observe controls immediately before the effect, after the durable write.
            source = observe_source()
            with self.store._locked(project) as path:
                events = self.store._read(path)
                current = approved_scope(self.store, events)
                if (len(events) != reserved["state"]["project_version"]
                        or current["spec_sha256"] != reserved["state"]["reservations"][reservation_id]["spec_sha256"]):
                    raise IntentRefused("owner decisions changed after reservation")
            if (compile_programme(source["active_input"], repository=self.store.repository).sha256 != programme_sha256
                    or source["main_sha"] != reserved["state"]["reservations"][reservation_id]["source_sha"]
                    or source["stop"] != {"state": "clear", "issues": []} or source["protected"] is not True):
                raise IntentRefused("execution changed after reservation")
            result = provider.run(request, before_retry=no_unobserved_retry, transcript=transcript)
        except BaseException:
            # A process death before this append leaves an equally nonrefundable reservation.
            record(None, "failed")
            raise
        record(getattr(result, "cost_usd", None), "returned")
        return result
