"""Durable work, outbox, executor registration and provider-call rows (Revision 4, C4/C5).

One database. The lease store already owns the operational SQLite file -- leases, grants,
resource generations, the operational epoch and the trusted clock observation -- and this
module adds the work item, its attempts, the outbox and the executor/provider indexes to that
same file, on that same connection, inside that same `BEGIN IMMEDIATE`. That is the point:
reserving an attempt and acquiring its lease bundle must be one transaction or the pair can
be observed half-done, and a second connection to the same file would be a second writer
rather than a second table.

What this module deliberately does not do:

  * It never calls the network. No row here is written after an outbound request that has not
    already been recorded; `record_started` exists so the durable record precedes the send.
  * It never decides an allowance. `df_provider_calls` is an audit index that links a call to
    the existing ExecutionBudget reservation and meter; summing it creates no budget and
    refunds nothing, which is why `billed_microusd` stays NULL until something independently
    observes a bill.
  * It never re-implements the lease guard. Reservation calls the store's own transaction-
    aware primitive, so a refusal is the store's refusal, with the store's reason codes.

The project journal stays where it is. An operational transition and the reference to the
event that explains it commit together here; the event itself is delivered afterwards through
the outbox under the idempotency key `op:<outbox_id>`, so a crash between the two redelivers
the identical bytes and gets back the identical canonical receipt instead of writing a second
event (C5, C08). A pending outbox row is not an approval and never counts as one.

Timestamps in these tables are UTC milliseconds, because the Revision 4 records are
milliseconds. The lease store's own timestamps remain integer seconds and are converted at
this boundary rather than reinterpreted: `seconds_of(ms)` is the only place the two meet.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .lease_store import LeaseBundle, LeaseRefused, LeaseStore

MIGRATION_VERSION = 2
MIGRATION_FILE = Path(__file__).resolve().parent / "migrations" / "002_operational_work.sql"

SHA256 = re.compile(r"[0-9a-f]{64}")
IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}")

READY = "ready"
BUDGET_PENDING = "budget_pending"
RESERVED = "reserved"
DISPATCHING = "dispatching"
DISPATCHED = "dispatched"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"
UNCERTAIN = "uncertain"
STALE = "stale"

TERMINAL = frozenset({SUCCEEDED, FAILED, CANCELLED})
NONTERMINAL_OWNERSHIP = frozenset({BUDGET_PENDING, RESERVED, DISPATCHING, DISPATCHED, RUNNING, UNCERTAIN})
PRE_START = frozenset({READY, BUDGET_PENDING, RESERVED, DISPATCHING})

# The state machine, exactly as C4 draws it. Terminal states have no outgoing edge: a failed
# attempt is retried by creating a NEW attempt number under the same work key, never by
# reopening the one that was observed to fail.
TRANSITIONS: Mapping[str, frozenset[str]] = {
    READY: frozenset({BUDGET_PENDING, STALE, CANCELLED}),
    BUDGET_PENDING: frozenset({RESERVED, STALE, FAILED, CANCELLED, UNCERTAIN}),
    RESERVED: frozenset({DISPATCHING, STALE, FAILED, CANCELLED}),
    DISPATCHING: frozenset({DISPATCHED, UNCERTAIN, FAILED, CANCELLED, STALE}),
    DISPATCHED: frozenset({RUNNING, UNCERTAIN, FAILED, CANCELLED}),
    RUNNING: frozenset({SUCCEEDED, FAILED, CANCELLED, UNCERTAIN}),
    UNCERTAIN: frozenset({SUCCEEDED, FAILED, CANCELLED}),
    STALE: frozenset(),
    SUCCEEDED: frozenset(),
    FAILED: frozenset(),
    CANCELLED: frozenset(),
}

OUTBOX_KINDS = ("budget_reserve", "project_event", "dispatch", "cancel", "wake")
OUTBOX_PENDING, OUTBOX_SENDING = "pending", "sending"
OUTBOX_DELIVERED, OUTBOX_UNCERTAIN, OUTBOX_REFUSED = "delivered", "uncertain", "refused"

CALL_RESERVED, CALL_STARTED = "reserved", "started"
CALL_OBSERVED, CALL_NOT_STARTED, CALL_UNCERTAIN = "observed", "not_started", "uncertain"


class OperationalRefused(ValueError):
    """A refusal changes nothing. It is never a fallback into a weaker guarantee."""


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    work_key: str
    project: str
    attempt_number: int
    state: str
    version: int
    lease_id: str | None
    budget_receipt_sha256: str | None
    envelope_sha256: str | None
    result_sha256: str | None

    def to_dict(self) -> dict:
        return {
            "attempt_id": self.attempt_id, "work_key": self.work_key, "project": self.project,
            "attempt_number": self.attempt_number, "state": self.state, "version": self.version,
            "lease_id": self.lease_id, "budget_receipt_sha256": self.budget_receipt_sha256,
            "envelope_sha256": self.envelope_sha256, "result_sha256": self.result_sha256,
        }


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise OperationalRefused(f"{name} must be a sha256")
    return value


def _ident(value: Any, name: str) -> str:
    if not isinstance(value, str) or not IDENT.fullmatch(value):
        raise OperationalRefused(f"{name} is malformed")
    return value


def _ms(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise OperationalRefused(f"{name} must be a non-negative integer of milliseconds")
    return value


def seconds_of(now_ms: int) -> int:
    """The one place milliseconds meet the lease store's seconds.

    Floor, not round: a lease must never be treated as acquired a fraction of a second in the
    future, and the store refuses a clock that moves backwards, so an inconsistent conversion
    would block authority rather than merely misreport it.
    """
    return _ms(now_ms, "now_ms") // 1000


def migration_sql() -> str:
    return MIGRATION_FILE.read_text(encoding="utf-8")


def migration_sha256() -> str:
    return hashlib.sha256(MIGRATION_FILE.read_bytes()).hexdigest()


def canonical_request_sha256(payload: Mapping[str, Any]) -> str:
    """The request identity an idempotency key is compared against.

    Sorted keys and compact separators, exactly like the journal's canonical bytes, so the
    same request produces the same digest in another process and a different request cannot
    replay under an identical key.
    """
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


class OperationalState:
    """Work, outbox, registration and provider-call rows on the lease store's own connection."""

    def __init__(self, store: LeaseStore):
        self.store = store
        self.connection: sqlite3.Connection = store.connection
        self.ensure_schema()

    # ---- schema ---------------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Additive migration, recorded with its own checksum.

        Applying the same version twice is a no-op; applying a DIFFERENT file under the same
        version is a refusal, because two trees would then disagree about what the schema is
        while both believing they had migrated.
        """
        self.store.apply_migration(MIGRATION_VERSION, migration_sql(), migration_sha256())

    def migration_row(self) -> dict | None:
        row = self.connection.execute(
            "SELECT version, sql_sha256, applied_at_ms FROM df_schema_migrations WHERE version = ?",
            (MIGRATION_VERSION,)).fetchone()
        return None if row is None else {"version": int(row[0]), "sql_sha256": str(row[1]),
                                         "applied_at_ms": int(row[2])}

    # ---- work and attempts ----------------------------------------------------------------

    def put_work(self, *, work_key: str, project: str, proposal_sha256: str,
                 programme_sha256: str, created_sequence: int, priority: int) -> str:
        """Register a work item. A repeated wakeup for the same key inserts no duplicate.

        The key already contains the programme, the exact subject, the stage and the plan and
        policy digests, so an identical key IS the same work; a changed head produces a
        different key and therefore a new item, leaving the old one in history.
        """
        work_key, project = _digest(work_key, "work_key"), _ident(project, "project")
        proposal_sha256 = _digest(proposal_sha256, "proposal_sha256")
        programme_sha256 = _digest(programme_sha256, "programme_sha256")
        if not isinstance(created_sequence, int) or isinstance(created_sequence, bool) or created_sequence < 0:
            raise OperationalRefused("created_sequence must be a non-negative integer")
        if not isinstance(priority, int) or isinstance(priority, bool) or not 0 <= priority <= 4:
            raise OperationalRefused("priority must be one of the five readiness classes")
        with self.store._transaction():
            row = self.connection.execute(
                "SELECT project, proposal_sha256, programme_sha256 FROM df_work WHERE work_key = ?",
                (work_key,)).fetchone()
            if row is not None:
                if (str(row[0]), str(row[1]), str(row[2])) != (project, proposal_sha256, programme_sha256):
                    raise OperationalRefused("work key already names different work")
                return work_key
            self.connection.execute(
                "INSERT INTO df_work(work_key, project, proposal_sha256, programme_sha256, created_sequence, priority) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (work_key, project, proposal_sha256, programme_sha256, created_sequence, priority))
        return work_key

    def open_attempt(self, *, work_key: str, project: str, request: Mapping[str, Any],
                     now_ms: int, attempt_id: str | None = None) -> Attempt:
        """Claim the project's single pending attempt and queue its budget reserve request.

        This is step 3 of the reserve protocol and it commits before anything touches the
        canonical ExecutionBudget: the attempt exists as `budget_pending` with an outbox row
        naming it, so a crash immediately afterwards finds the same attempt id and replays the
        same reserve command rather than creating a second one. No lease and no effect is
        executable yet.
        """
        work_key, project = _digest(work_key, "work_key"), _ident(project, "project")
        request_sha256 = canonical_request_sha256(request)
        now_ms = _ms(now_ms, "now_ms")
        attempt_id = _ident(attempt_id, "attempt_id") if attempt_id else secrets.token_hex(16)
        with self.store._transaction():
            if self.connection.execute("SELECT 1 FROM df_work WHERE work_key = ? AND project = ?",
                                       (work_key, project)).fetchone() is None:
                raise OperationalRefused("unknown work cannot open an attempt")
            existing = self.connection.execute(
                "SELECT attempt_id, work_key, state FROM df_attempts WHERE project = ? AND state IN "
                "('budget_pending','reserved','dispatching','dispatched','running','uncertain')",
                (project,)).fetchone()
            if existing is not None:
                raise OperationalRefused(
                    f"project {project} already owns attempt {existing[0]} in {existing[2]}")
            number = int(self.connection.execute(
                "SELECT COALESCE(MAX(attempt_number), 0) FROM df_attempts WHERE work_key = ?",
                (work_key,)).fetchone()[0]) + 1
            self.connection.execute(
                "INSERT INTO df_attempts(attempt_id, work_key, project, attempt_number, request_sha256, "
                "state, version, created_at_ms, updated_at_ms) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (attempt_id, work_key, project, number, request_sha256, BUDGET_PENDING, now_ms, now_ms))
            self._insert_outbox(attempt_id=attempt_id, project=project, kind="budget_reserve",
                                payload_sha256=request_sha256, now_ms=now_ms)
        return self.attempt(attempt_id)

    def attempt(self, attempt_id: str) -> Attempt:
        row = self.connection.execute(
            "SELECT attempt_id, work_key, project, attempt_number, state, version, lease_id, "
            "budget_receipt_sha256, envelope_sha256, result_sha256 FROM df_attempts WHERE attempt_id = ?",
            (attempt_id,)).fetchone()
        if row is None:
            raise OperationalRefused(f"unknown attempt {attempt_id}")
        return Attempt(str(row[0]), str(row[1]), str(row[2]), int(row[3]), str(row[4]), int(row[5]),
                       None if row[6] is None else str(row[6]),
                       None if row[7] is None else str(row[7]),
                       None if row[8] is None else str(row[8]),
                       None if row[9] is None else str(row[9]))

    def attempts_for(self, work_key: str) -> tuple[Attempt, ...]:
        rows = self.connection.execute(
            "SELECT attempt_id FROM df_attempts WHERE work_key = ? ORDER BY attempt_number",
            (_digest(work_key, "work_key"),)).fetchall()
        return tuple(self.attempt(str(row[0])) for row in rows)

    def record_budget_receipt(self, *, attempt_id: str, receipt_sha256: str,
                              expected_version: int, now_ms: int) -> Attempt:
        """The canonical reservation's actual receipt, kept beside the attempt that owns it.

        Recovery looks the reservation up by this attempt id and verifies the exact bytes, so
        a historical replay is recovered AS that reservation and never mistaken for a fresh
        execute authorization. The ledger has no refund, so a receipt once recorded stays.
        """
        receipt_sha256 = _digest(receipt_sha256, "receipt_sha256")
        with self.store._transaction():
            current = self._locked_attempt(attempt_id, expected_version)
            if current.state != BUDGET_PENDING:
                raise OperationalRefused(f"attempt {attempt_id} is {current.state}, not budget_pending")
            if current.budget_receipt_sha256 not in (None, receipt_sha256):
                raise OperationalRefused("a different budget receipt is already recorded")
            self.connection.execute(
                "UPDATE df_attempts SET budget_receipt_sha256 = ?, version = version + 1, updated_at_ms = ? "
                "WHERE attempt_id = ?", (receipt_sha256, _ms(now_ms, "now_ms"), attempt_id))
        return self.attempt(attempt_id)

    def reserve(self, *, attempt_id: str, expected_version: int, owner: str, role: str,
                resources: Sequence[str], subject_sha256: str, envelope_sha256: str,
                ttl_seconds: int, now_ms: int) -> tuple[Attempt, LeaseBundle]:
        """Acquire the resource bundle and become `reserved` in ONE transaction.

        Step 5 of the reserve protocol. The lease is taken through the store's own
        transaction-aware primitive on this same connection, so there is no second lease
        connection and no nested transaction, and its refusals are the store's refusals. A
        budget receipt must already be recorded: a valid lease is not permission to spend.
        """
        envelope_sha256 = _digest(envelope_sha256, "envelope_sha256")
        subject_sha256 = _digest(subject_sha256, "subject_sha256")
        now_ms = _ms(now_ms, "now_ms")
        with self.store._transaction():
            current = self._locked_attempt(attempt_id, expected_version)
            if current.state != BUDGET_PENDING:
                raise OperationalRefused(f"attempt {attempt_id} is {current.state}, not budget_pending")
            if not current.budget_receipt_sha256:
                raise OperationalRefused("budget ambiguity blocks reservation; no receipt recorded")
            bundle = self.store._acquire_locked(
                owner=owner, role=role, resources=resources,
                request_sha256=current.request_sha256,
                subject_sha256=subject_sha256, ttl_seconds=ttl_seconds,
                now=seconds_of(now_ms))
            self.connection.execute(
                "UPDATE df_attempts SET state = ?, lease_id = ?, envelope_sha256 = ?, "
                "version = version + 1, updated_at_ms = ? WHERE attempt_id = ?",
                (RESERVED, bundle.lease_id, envelope_sha256, now_ms, attempt_id))
            self._insert_outbox(attempt_id=attempt_id, project=current.project, kind="dispatch",
                                payload_sha256=envelope_sha256, now_ms=now_ms)
        return self.attempt(attempt_id), bundle

    def transition(self, *, attempt_id: str, to_state: str, expected_version: int, now_ms: int,
                   result_sha256: str | None = None) -> Attempt:
        """Move an attempt along C4's state machine, or refuse and change nothing.

        Terminal states have no outgoing edge, so a `failed` attempt cannot be nudged back to
        `running`; a retry is a new attempt number authorized by what is left of the aggregate
        limits. `stale` is only reachable before the attempt has started.
        """
        if to_state not in TRANSITIONS:
            raise OperationalRefused(f"unknown state {to_state}")
        now_ms = _ms(now_ms, "now_ms")
        if result_sha256 is not None:
            result_sha256 = _digest(result_sha256, "result_sha256")
        with self.store._transaction():
            current = self._locked_attempt(attempt_id, expected_version)
            allowed = TRANSITIONS[current.state]
            if to_state not in allowed:
                raise OperationalRefused(
                    f"{current.state} -> {to_state} is not a transition"
                    + (" (terminal work never returns to ready)" if current.state in TERMINAL else ""))
            if to_state == STALE and current.state not in PRE_START:
                raise OperationalRefused("only pre-start work becomes stale")
            self.connection.execute(
                "UPDATE df_attempts SET state = ?, result_sha256 = COALESCE(?, result_sha256), "
                "version = version + 1, updated_at_ms = ? WHERE attempt_id = ?",
                (to_state, result_sha256, now_ms, attempt_id))
        return self.attempt(attempt_id)

    def _locked_attempt(self, attempt_id: str, expected_version: int) -> "_LockedAttempt":
        row = self.connection.execute(
            "SELECT attempt_id, work_key, project, attempt_number, state, version, lease_id, "
            "budget_receipt_sha256, envelope_sha256, result_sha256, request_sha256 "
            "FROM df_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        if row is None:
            raise OperationalRefused(f"unknown attempt {attempt_id}")
        if not isinstance(expected_version, int) or isinstance(expected_version, bool):
            raise OperationalRefused("expected_version must be an integer")
        if int(row[5]) != expected_version:
            raise OperationalRefused(
                f"attempt {attempt_id} is at version {int(row[5])}, not {expected_version}")
        return _LockedAttempt(str(row[0]), str(row[2]), str(row[4]), int(row[5]),
                              None if row[7] is None else str(row[7]), str(row[10]))

    # ---- outbox ---------------------------------------------------------------------------

    def _insert_outbox(self, *, attempt_id: str | None, project: str, kind: str,
                       payload_sha256: str, now_ms: int, outbox_id: str | None = None) -> str:
        if kind not in OUTBOX_KINDS:
            raise OperationalRefused(f"unknown outbox kind {kind}")
        outbox_id = outbox_id or secrets.token_hex(16)
        self.connection.execute(
            "INSERT INTO df_outbox(outbox_id, attempt_id, project, kind, payload_sha256, state, "
            "version, created_at_ms) VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            (outbox_id, attempt_id, _ident(project, "project"), kind,
             _digest(payload_sha256, "payload_sha256"), OUTBOX_PENDING, _ms(now_ms, "now_ms")))
        return outbox_id

    def enqueue(self, *, project: str, kind: str, payload_sha256: str, now_ms: int,
                attempt_id: str | None = None) -> str:
        with self.store._transaction():
            return self._insert_outbox(attempt_id=attempt_id, project=project, kind=kind,
                                       payload_sha256=payload_sha256, now_ms=now_ms)

    def idempotency_key(self, outbox_id: str) -> str:
        """`op:<outbox_id>` -- the same key for every redelivery of the same row (C5)."""
        return f"op:{_ident(outbox_id, 'outbox_id')}"

    def pending_outbox(self, *, project: str | None = None, limit: int = 100) -> tuple[dict, ...]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise OperationalRefused("limit must be between 1 and 1000")
        sql = ("SELECT outbox_id, attempt_id, project, kind, payload_sha256, state, version, "
               "receipt_sha256 FROM df_outbox WHERE state IN ('pending','sending','uncertain')")
        args: list[Any] = []
        if project is not None:
            sql += " AND project = ?"
            args.append(_ident(project, "project"))
        sql += " ORDER BY created_at_ms, outbox_id LIMIT ?"
        args.append(limit)
        rows = self.connection.execute(sql, tuple(args)).fetchall()
        return tuple({"outbox_id": str(r[0]), "attempt_id": None if r[1] is None else str(r[1]),
                      "project": str(r[2]), "kind": str(r[3]), "payload_sha256": str(r[4]),
                      "state": str(r[5]), "version": int(r[6]),
                      "receipt_sha256": None if r[7] is None else str(r[7])} for r in rows)

    def record_delivery(self, *, outbox_id: str, receipt_sha256: str, expected_version: int) -> dict:
        """The canonical receipt for a delivered event, or a refusal if the bytes differ.

        An identical redelivery after a crash returns the same receipt and writes nothing new.
        Different bytes under the same key are corruption, never a second event: the journal
        has one entry for this operation and this is the record of which one.
        """
        receipt_sha256 = _digest(receipt_sha256, "receipt_sha256")
        with self.store._transaction():
            row = self._locked_outbox(outbox_id, expected_version)
            if row["state"] == OUTBOX_DELIVERED:
                if row["receipt_sha256"] != receipt_sha256:
                    raise OperationalRefused(
                        "a different receipt is already recorded for this outbox id; "
                        "different bytes under one idempotency key are corruption, not a second event")
                return row
            self.connection.execute(
                "UPDATE df_outbox SET state = ?, receipt_sha256 = ?, version = version + 1 "
                "WHERE outbox_id = ?", (OUTBOX_DELIVERED, receipt_sha256, outbox_id))
        return self.outbox(outbox_id)

    def mark_outbox(self, *, outbox_id: str, state: str, expected_version: int,
                    next_observation_at_ms: int | None = None) -> dict:
        if state not in (OUTBOX_SENDING, OUTBOX_UNCERTAIN, OUTBOX_REFUSED):
            raise OperationalRefused(f"{state} is not a settable outbox state")
        with self.store._transaction():
            row = self._locked_outbox(outbox_id, expected_version)
            if row["state"] == OUTBOX_DELIVERED:
                raise OperationalRefused("a delivered outbox row is final")
            self.connection.execute(
                "UPDATE df_outbox SET state = ?, version = version + 1, next_observation_at_ms = ? "
                "WHERE outbox_id = ?",
                (state, None if next_observation_at_ms is None else _ms(next_observation_at_ms, "next_observation_at_ms"),
                 outbox_id))
        return self.outbox(outbox_id)

    def outbox(self, outbox_id: str) -> dict:
        row = self.connection.execute(
            "SELECT outbox_id, attempt_id, project, kind, payload_sha256, state, version, receipt_sha256 "
            "FROM df_outbox WHERE outbox_id = ?", (outbox_id,)).fetchone()
        if row is None:
            raise OperationalRefused(f"unknown outbox id {outbox_id}")
        return {"outbox_id": str(row[0]), "attempt_id": None if row[1] is None else str(row[1]),
                "project": str(row[2]), "kind": str(row[3]), "payload_sha256": str(row[4]),
                "state": str(row[5]), "version": int(row[6]),
                "receipt_sha256": None if row[7] is None else str(row[7])}

    def _locked_outbox(self, outbox_id: str, expected_version: int) -> dict:
        row = self.outbox(outbox_id)
        if not isinstance(expected_version, int) or isinstance(expected_version, bool):
            raise OperationalRefused("expected_version must be an integer")
        if row["version"] != expected_version:
            raise OperationalRefused(
                f"outbox {outbox_id} is at version {row['version']}, not {expected_version}")
        return row

    # ---- executor registration ------------------------------------------------------------

    def register_executor(self, *, attempt_id: str, repository_id: str, run_id: str,
                          run_attempt: int, oidc_token_sha256: str, envelope_sha256: str,
                          credential_sha256: str, expires_at_ms: int, now_ms: int) -> dict:
        """Once-only consumption of one envelope by one authenticated run (C07, C15).

        Three uniqueness constraints do the work: one registration per attempt, one per OIDC
        token digest, one per (repository, run, attempt). A second authentic run presenting
        the same envelope is refused; the same run re-presenting its own registration gets its
        own row back, because a retry of a registration is not a second executor.
        """
        attempt_id = _ident(attempt_id, "attempt_id")
        oidc_token_sha256 = _digest(oidc_token_sha256, "oidc_token_sha256")
        envelope_sha256 = _digest(envelope_sha256, "envelope_sha256")
        credential_sha256 = _digest(credential_sha256, "credential_sha256")
        if not isinstance(run_attempt, int) or isinstance(run_attempt, bool) or run_attempt < 1:
            raise OperationalRefused("run_attempt must be a positive integer")
        now_ms, expires_at_ms = _ms(now_ms, "now_ms"), _ms(expires_at_ms, "expires_at_ms")
        if expires_at_ms <= now_ms:
            raise OperationalRefused("a registration that has already expired grants nothing")
        with self.store._transaction():
            attempt = self.attempt(attempt_id)
            if attempt.envelope_sha256 != envelope_sha256:
                raise OperationalRefused("the presented envelope is not this attempt's envelope")
            existing = self.connection.execute(
                "SELECT registration_id, repository_id, run_id, run_attempt, oidc_token_sha256, revoked "
                "FROM df_executor_registrations WHERE attempt_id = ?", (attempt_id,)).fetchone()
            if existing is not None:
                same_run = (str(existing[1]), str(existing[2]), int(existing[3])) == (
                    _ident(repository_id, "repository_id"), _ident(run_id, "run_id"), run_attempt)
                if not (same_run and str(existing[4]) == oidc_token_sha256):
                    raise OperationalRefused(
                        "this envelope is already consumed by another run; a second authentic "
                        "identity does not make a second execution")
                return self.registration(str(existing[0]))
            if self.connection.execute(
                    "SELECT 1 FROM df_executor_registrations WHERE oidc_token_sha256 = ?",
                    (oidc_token_sha256,)).fetchone() is not None:
                raise OperationalRefused("this OIDC token has already registered a job")
            registration_id = secrets.token_hex(16)
            self.connection.execute(
                "INSERT INTO df_executor_registrations(registration_id, attempt_id, repository_id, run_id, "
                "run_attempt, oidc_token_sha256, envelope_sha256, credential_sha256, expires_at_ms, "
                "last_heartbeat_at_ms, revoked) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (registration_id, attempt_id, repository_id, run_id, run_attempt, oidc_token_sha256,
                 envelope_sha256, credential_sha256, expires_at_ms, now_ms))
        return self.registration(registration_id)

    def heartbeat_executor(self, *, registration_id: str, now_ms: int, ttl_ms: int) -> dict:
        """Extend a live registration. At equality the credential is expired, not renewable."""
        now_ms, ttl_ms = _ms(now_ms, "now_ms"), _ms(ttl_ms, "ttl_ms")
        if ttl_ms < 1:
            raise OperationalRefused("ttl_ms must be positive")
        with self.store._transaction():
            row = self.registration(registration_id)
            if row["revoked"]:
                raise OperationalRefused("a revoked registration cannot be renewed")
            if now_ms >= row["expires_at_ms"]:
                raise OperationalRefused("the credential expired; a stale heartbeat cannot revive it")
            self.connection.execute(
                "UPDATE df_executor_registrations SET expires_at_ms = ?, last_heartbeat_at_ms = ? "
                "WHERE registration_id = ?", (now_ms + ttl_ms, now_ms, registration_id))
        return self.registration(registration_id)

    def revoke_executor(self, *, registration_id: str) -> dict:
        with self.store._transaction():
            self.registration(registration_id)
            self.connection.execute(
                "UPDATE df_executor_registrations SET revoked = 1 WHERE registration_id = ?",
                (registration_id,))
        return self.registration(registration_id)

    def registration(self, registration_id: str) -> dict:
        row = self.connection.execute(
            "SELECT registration_id, attempt_id, repository_id, run_id, run_attempt, envelope_sha256, "
            "credential_sha256, expires_at_ms, last_heartbeat_at_ms, revoked "
            "FROM df_executor_registrations WHERE registration_id = ?", (registration_id,)).fetchone()
        if row is None:
            raise OperationalRefused(f"unknown registration {registration_id}")
        return {"registration_id": str(row[0]), "attempt_id": str(row[1]), "repository_id": str(row[2]),
                "run_id": str(row[3]), "run_attempt": int(row[4]), "envelope_sha256": str(row[5]),
                "credential_sha256": str(row[6]), "expires_at_ms": int(row[7]),
                "last_heartbeat_at_ms": int(row[8]), "revoked": bool(row[9])}

    # ---- wakeups --------------------------------------------------------------------------

    def record_wakeup(self, *, project: str, source_event_id: str, payload_sha256: str) -> bool:
        """A canonical event id wakes a project at most once. Returns True if it was new."""
        project = _ident(project, "project")
        source_event_id = _ident(source_event_id, "source_event_id")
        payload_sha256 = _digest(payload_sha256, "payload_sha256")
        with self.store._transaction():
            row = self.connection.execute(
                "SELECT payload_sha256 FROM df_wakeups WHERE project = ? AND source_event_id = ?",
                (project, source_event_id)).fetchone()
            if row is not None:
                if str(row[0]) != payload_sha256:
                    raise OperationalRefused("this event id already woke the project with other bytes")
                return False
            self.connection.execute(
                "INSERT INTO df_wakeups(project, source_event_id, payload_sha256) VALUES (?, ?, ?)",
                (project, source_event_id, payload_sha256))
        return True

    def mark_wakeup_processed(self, *, project: str, source_event_id: str, now_ms: int) -> None:
        with self.store._transaction():
            self.connection.execute(
                "UPDATE df_wakeups SET processed_at_ms = ? WHERE project = ? AND source_event_id = ?",
                (_ms(now_ms, "now_ms"), _ident(project, "project"),
                 _ident(source_event_id, "source_event_id")))

    def unprocessed_wakeups(self, *, project: str) -> tuple[str, ...]:
        rows = self.connection.execute(
            "SELECT source_event_id FROM df_wakeups WHERE project = ? AND processed_at_ms IS NULL "
            "ORDER BY source_event_id", (_ident(project, "project"),)).fetchall()
        return tuple(str(row[0]) for row in rows)

    # ---- provider call index --------------------------------------------------------------

    def reserve_call(self, *, call_id: str, attempt_id: str, invocation_id: str,
                     request_sha256: str, allowance_receipt_sha256: str,
                     reserved_microusd: int) -> dict:
        """A durable row BEFORE anything is sent. Reserving is not spending and not a budget."""
        if not isinstance(reserved_microusd, int) or isinstance(reserved_microusd, bool) or reserved_microusd < 0:
            raise OperationalRefused("reserved_microusd must be a non-negative integer of microusd")
        call_id = _ident(call_id, "call_id")
        with self.store._transaction():
            self.attempt(attempt_id)
            existing = self.connection.execute(
                "SELECT request_sha256 FROM df_provider_calls WHERE call_id = ?", (call_id,)).fetchone()
            if existing is not None:
                if str(existing[0]) != _digest(request_sha256, "request_sha256"):
                    raise OperationalRefused("this call id already names a different request")
                return self.call(call_id)
            self.connection.execute(
                "INSERT INTO df_provider_calls(call_id, attempt_id, invocation_id, request_sha256, "
                "allowance_receipt_sha256, state, reserved_microusd) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (call_id, _ident(attempt_id, "attempt_id"), _ident(invocation_id, "invocation_id"),
                 _digest(request_sha256, "request_sha256"),
                 _digest(allowance_receipt_sha256, "allowance_receipt_sha256"),
                 CALL_RESERVED, reserved_microusd))
        return self.call(call_id)

    def record_started(self, *, call_id: str, grant_id: str) -> dict:
        """Durable `started` before the outbound request, bound to an existing grant."""
        with self.store._transaction():
            row = self.call(call_id)
            if row["state"] != CALL_RESERVED:
                raise OperationalRefused(f"call {call_id} is {row['state']}, not reserved")
            self.connection.execute(
                "UPDATE df_provider_calls SET state = ?, grant_id = ? WHERE call_id = ?",
                (CALL_STARTED, _ident(grant_id, "grant_id"), call_id))
        return self.call(call_id)

    def observe_call(self, *, call_id: str, result_sha256: str,
                     billed_microusd: int | None = None,
                     estimated_microusd: int | None = None) -> dict:
        """An independently observed result. An absent bill stays NULL; it never becomes zero."""
        for name, value in (("billed_microusd", billed_microusd), ("estimated_microusd", estimated_microusd)):
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise OperationalRefused(f"{name} must be a non-negative integer or unknown")
        with self.store._transaction():
            row = self.call(call_id)
            if row["state"] not in (CALL_STARTED, CALL_UNCERTAIN):
                raise OperationalRefused(f"call {call_id} is {row['state']}; nothing was sent to observe")
            self.connection.execute(
                "UPDATE df_provider_calls SET state = ?, result_sha256 = ?, billed_microusd = ?, "
                "estimated_microusd = ?, unknown_reason = NULL WHERE call_id = ?",
                (CALL_OBSERVED, _digest(result_sha256, "result_sha256"), billed_microusd,
                 estimated_microusd, call_id))
        return self.call(call_id)

    def mark_call_uncertain(self, *, call_id: str, unknown_reason: str) -> dict:
        """The request may have been sent. The reservation is retained and nothing is refunded."""
        if not isinstance(unknown_reason, str) or not unknown_reason.strip():
            raise OperationalRefused("an uncertain call must say why it is uncertain")
        with self.store._transaction():
            row = self.call(call_id)
            if row["state"] == CALL_OBSERVED:
                raise OperationalRefused("an observed call is not uncertain")
            self.connection.execute(
                "UPDATE df_provider_calls SET state = ?, unknown_reason = ? WHERE call_id = ?",
                (CALL_UNCERTAIN, unknown_reason[:400], call_id))
        return self.call(call_id)

    def mark_call_not_started(self, *, call_id: str) -> dict:
        """Cancelled before anything left the process. Only legal from `reserved`."""
        with self.store._transaction():
            row = self.call(call_id)
            if row["state"] != CALL_RESERVED:
                raise OperationalRefused(
                    f"call {call_id} is {row['state']}; once started, not-started is a claim about "
                    "the provider that this process cannot make")
            self.connection.execute("UPDATE df_provider_calls SET state = ? WHERE call_id = ?",
                                    (CALL_NOT_STARTED, call_id))
        return self.call(call_id)

    def call(self, call_id: str) -> dict:
        row = self.connection.execute(
            "SELECT call_id, attempt_id, invocation_id, request_sha256, allowance_receipt_sha256, "
            "grant_id, state, reserved_microusd, billed_microusd, estimated_microusd, result_sha256, "
            "unknown_reason FROM df_provider_calls WHERE call_id = ?", (call_id,)).fetchone()
        if row is None:
            raise OperationalRefused(f"unknown call {call_id}")
        return {"call_id": str(row[0]), "attempt_id": str(row[1]), "invocation_id": str(row[2]),
                "request_sha256": str(row[3]), "allowance_receipt_sha256": str(row[4]),
                "grant_id": None if row[5] is None else str(row[5]), "state": str(row[6]),
                "reserved_microusd": int(row[7]),
                "billed_microusd": None if row[8] is None else int(row[8]),
                "estimated_microusd": None if row[9] is None else int(row[9]),
                "result_sha256": None if row[10] is None else str(row[10]),
                "unknown_reason": None if row[11] is None else str(row[11])}

    def unresolved_calls(self, *, attempt_id: str | None = None) -> tuple[dict, ...]:
        sql = ("SELECT call_id FROM df_provider_calls WHERE state IN ('reserved','started','uncertain')")
        args: list[Any] = []
        if attempt_id is not None:
            sql += " AND attempt_id = ?"
            args.append(_ident(attempt_id, "attempt_id"))
        rows = self.connection.execute(sql + " ORDER BY call_id", tuple(args)).fetchall()
        return tuple(self.call(str(row[0])) for row in rows)


@dataclass(frozen=True)
class _LockedAttempt:
    """An attempt read under the caller's open transaction, at a version it expected."""

    attempt_id: str
    project: str
    state: str
    version: int
    budget_receipt_sha256: str | None
    request_sha256: str


__all__ = [
    "Attempt", "OperationalRefused", "OperationalState", "TRANSITIONS", "MIGRATION_VERSION",
    "canonical_request_sha256", "migration_sha256", "migration_sql", "seconds_of",
    "LeaseRefused",
]
