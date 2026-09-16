"""Authenticated single-use worker reservations, distinct from publication currency.

No owner approval, budget increase, refund, scope change or product qualification can be
performed through this protocol. Authentication precedes every disk or platform read.
"""
from __future__ import annotations

from copy import deepcopy
import hmac
import re
import secrets
import time

from .canonical import canonical_bytes, sha256_value
from .execution_budget import MAX_MICROUSD, _integer
from .frontdoor_intent import IntentRefused, _shape
from .worker_policy import ROLE_MAX_BUDGET_USD

DOMAIN = b"dark-factory/execution-reservation/v1/"
MAX_AGE_SECONDS = 60
MAX_ENVELOPE = 16000
ROLES = frozenset(ROLE_MAX_BUDGET_USD) - {
    "preflight-proposer", "preflight-challenger", "intent-proposer", "intent-auditor", "programme-proposer"}


def _hex(value, size):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{%d}" % size, value):
        raise IntentRefused("invalid execution exchange identity")


class ExecutionProtocol:
    def __init__(self, key, *, repository, project, clock=None):
        if not isinstance(key, bytes) or len(key) != 32:
            raise IntentRefused("execution key must contain 32 bytes")
        self.key = hmac.digest(key, DOMAIN + b"key", "sha256")
        self.repository, self.project = repository, project
        self.clock = clock or time.time

    def _seal(self, payload, purpose):
        raw = canonical_bytes(payload)
        if len(raw) > MAX_ENVELOPE:
            raise IntentRefused("execution envelope too large")
        return {"payload": deepcopy(payload), "mac": hmac.digest(self.key, DOMAIN + purpose + raw, "sha256").hex()}

    def _open(self, envelope, purpose):
        _shape(envelope, {"payload", "mac"})
        _hex(envelope["mac"], 64)
        if not hmac.compare_digest(envelope["mac"], self._seal(envelope["payload"], purpose)["mac"]):
            raise IntentRefused("execution exchange authentication refused")
        return deepcopy(envelope["payload"])

    def _validate(self, payload):
        _shape(payload, {"repository", "project", "phase", "call", "request", "nonce", "issued_at"})
        if (payload["repository"] != self.repository or payload["project"] != self.project
                or payload["phase"] not in {"reserve", "start", "observe"}
                or type(payload["issued_at"]) is not int
                or not -5 <= self.clock() - payload["issued_at"] <= MAX_AGE_SECONDS):
            raise IntentRefused("execution exchange destination, phase or freshness refused")
        _hex(payload["nonce"], 32)
        call = payload["call"]
        _shape(call, {"id", "run_id", "run_attempt", "source_sha", "programme_sha256",
                      "role", "microusd", "request_sha256"})
        for key, size in (("id", 32), ("source_sha", 40), ("programme_sha256", 64), ("request_sha256", 64)):
            _hex(call[key], size)
        _integer(call["run_id"], 10**20)
        if type(call["run_attempt"]) is not int or call["run_attempt"] != 1 or call["role"] not in ROLES:
            raise IntentRefused("execution role or rerun refused")
        _integer(call["microusd"], MAX_MICROUSD)
        if call["microusd"] > int(ROLE_MAX_BUDGET_USD[call["role"]] * 1_000_000):
            raise IntentRefused("execution request exceeds protected role policy")
        request = payload["request"]
        fields = {"reserve": set(), "start": {"reservation_version"}, "observe": {"reported_microusd", "outcome"}}
        _shape(request, fields[payload["phase"]])
        if payload["phase"] == "start":
            _integer(request["reservation_version"], 10000)
        if payload["phase"] == "observe":
            if request["reported_microusd"] is not None:
                _integer(request["reported_microusd"], MAX_MICROUSD, zero=True)
            if request["outcome"] not in {"returned", "failed"}:
                raise IntentRefused("execution outcome refused")
        return payload

    def challenge(self, phase, call, request):
        return self._seal(self._validate({"repository": self.repository, "project": self.project,
            "phase": phase, "call": deepcopy(call), "request": deepcopy(request),
            "nonce": secrets.token_hex(16), "issued_at": int(self.clock())}), b"request/")

    def answer(self, envelope, *, service):
        payload = self._validate(self._open(envelope, b"request/"))
        # Disk and GitHub reads happen only after authentication and shape/freshness checks.
        decision = service.apply(payload, check_fresh=lambda: self._validate(payload))
        self._validate(payload)
        return self._seal({"challenge": payload, "observed_at": int(self.clock()), "decision": decision}, b"response/")

    def verify(self, envelope, request):
        challenge = self._validate(self._open(request, b"request/"))
        response = self._open(envelope, b"response/")
        _shape(response, {"challenge", "observed_at", "decision"})
        if (response["challenge"] != challenge or type(response["observed_at"]) is not int
                or not challenge["issued_at"] - 5 <= response["observed_at"] <= self.clock() + 5
                or self.clock() - response["observed_at"] > MAX_AGE_SECONDS):
            raise IntentRefused("execution response is stale or belongs to another challenge")
        decision = response["decision"]
        _shape(decision, {"call_sha256", "reservation_id", "project_version", "status"})
        allowed = {"reserve": {"reserved", "already-reserved"}, "start": {"start-once", "already-started"},
                   "observe": {"observed", "already-observed"}}
        if (decision["call_sha256"] != sha256_value(challenge["call"])
                or decision["reservation_id"] != "worker-" + challenge["call"]["id"]
                or decision["status"] not in allowed[challenge["phase"]]):
            raise IntentRefused("execution response binding refused")
        _integer(decision["project_version"], 10000)
        return decision


class ExecutionExchange:
    def __init__(self, budget, project, principal, observe):
        self.budget, self.project, self.principal, self.observer = budget, project, principal, observe

    def apply(self, payload, *, check_fresh):
        call, phase = payload["call"], payload["phase"]
        reservation_id, identity = "worker-" + call["id"], sha256_value(call)
        source = self.observer(call, phase)
        check_fresh()
        state = self.budget.snapshot(self.project, principal=self.principal)
        row = state["reservations"].get(reservation_id)
        if row is not None and row["execution_id"] != identity:
            raise IntentRefused("worker call identity was reused with different content")

        def result(status, current):
            return {"call_sha256": identity, "reservation_id": reservation_id,
                    "project_version": current["project_version"], "status": status}

        if phase == "reserve":
            if row is not None:
                return result("already-reserved", state)
            def fresh_source():
                fresh = self.observer(call, phase)
                check_fresh()
                if fresh != source:
                    raise IntentRefused("worker source changed during reservation")
                return fresh
            command = {"idempotency_key": reservation_id, "expected_project_version": state["project_version"],
                "request": {"programme_sha256": call["programme_sha256"], "role": call["role"],
                    "microusd": call["microusd"], "attempt": 1, "execution_id": identity}}
            reserved = self.budget.reserve(self.project, command, principal=self.principal, observe=fresh_source)
            return result("reserved" if reserved["execute"] else "already-reserved", reserved["state"])
        if row is None:
            raise IntentRefused("worker reservation does not exist")
        if phase == "start":
            if row["started"] is not None:
                return result("already-started", state)
            check_fresh()
            started = self.budget.start(self.project, {"idempotency_key": "start-" + reservation_id,
                "expected_project_version": payload["request"]["reservation_version"],
                "request": {"id": reservation_id, "execution_id": identity}}, principal=self.principal)
            return result("start-once" if started["execute"] else "already-started", started["state"])
        if row["started"] is None:
            raise IntentRefused("unstarted worker reservation cannot be settled")
        request = {"id": reservation_id, **payload["request"]}
        if row["observation"] is not None:
            if row["observation"] != request:
                raise IntentRefused("worker observation cannot be changed")
            return result("already-observed", state)
        check_fresh()
        observed = self.budget.observe(self.project, {"idempotency_key": "observed-" + reservation_id,
            "expected_project_version": state["project_version"], "request": request}, principal=self.principal)
        return result("observed", observed)
