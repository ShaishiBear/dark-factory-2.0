"""Atomic lease and grant store (SPECIFICATION 4.2, contract C05, WP07), with a pure guard.

One SQLite database (foreign keys, WAL, synchronous FULL, bounded busy timeout). An
`operational_epoch` plus a persistent per-resource generation counter make every lease bundle a
unique identity: epoch, resource generations, owner, role, immutable request hash and exact
subject. Acquiring sorted resources happens in one `BEGIN IMMEDIATE`: if any resource is held by
another live owner or has an unresolved operation, nothing is acquired; otherwise every generation
increments and one bundle is written. Leases are released or expire into tombstones; generations
never reset. A heartbeat extends expiry only for a matching live bundle; a stale heartbeat cannot
recreate ownership. A release cannot clear an ambiguous in-flight operation.

Grants in this store: `started -> observed_success | observed_failure` (final, never reopened)
or `started -> uncertain`, which is reconciled at most once by an explicit independent
observation to `observed_success | observed_failure` and blocks its resources until then. C05's
`issued`, `revoked` and `expired` states are named in GRANT_STATES for the coordinator that will
issue grants ahead of consumption, but nothing here produces them yet: `consume_grant` records a
grant as `started` in the same transaction that issues it. Operation ids are unique under
(project, semantic operation, request id); the same id with different request bytes is a
conflict; an identical replay of an observed operation returns the recorded result and never
executes again (the replay answer comes from the record, before the lease guard runs).

Time is supplied by a trusted clock observer (`now`, integer seconds); expiry is `now >=
expires_at`; a clock that moves backwards blocks time-dependent authority. Network calls never
happen inside a transaction; this module makes none.

`validate_generation` is the pure guard the public lease_guard vectors exercise: schema, then
identity, then live state, then uncertainty and replay; a refusal has no side effect.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Any, Iterable, Mapping, Sequence

from .canonical import sha256_value

SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 5000
MAX_RESOURCES = 64
SHA256 = re.compile(r"[0-9a-f]{64}")
IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}")
GRANT_STATES = ("issued", "started", "observed_success", "observed_failure", "uncertain", "revoked", "expired")
# Final: never reopened. `uncertain` is NOT final; it is unresolved until an explicit observation.
TERMINAL = frozenset({"observed_success", "observed_failure", "revoked", "expired"})
UNRESOLVED = frozenset({"issued", "started", "uncertain"})


class LeaseRefused(ValueError):
    pass


@dataclass(frozen=True)
class GuardResult:
    status: str
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict:
        return {"status": self.status, "reason_codes": list(self.reason_codes)}


def _int(value: Any, name: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise LeaseRefused(f"{name} must be an integer >= {minimum}")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not IDENT.fullmatch(value):
        raise LeaseRefused(f"{name} is malformed")
    return value


def validate_generation(lease: Mapping[str, Any], current: Mapping[str, Any], *, now: int) -> GuardResult:
    """The pure guard. `lease` is what the caller presents (epoch, generation or generations,
    owner, expires_at, active, consumed, uncertain); `current` is what the store holds now
    (current_epoch, current_generation(s), current_owner). Priority: schema -> identity -> live
    state -> uncertainty -> replay. The first failing class is reported, nothing changes."""
    try:
        now = _int(now, "now")
        epoch, current_epoch = _text(lease.get("epoch"), "epoch"), _text(current.get("current_epoch"), "current_epoch")
        owner, current_owner = _text(lease.get("owner"), "owner"), _text(current.get("current_owner"), "current_owner")
        expires_at = _int(lease.get("expires_at"), "expires_at")
        gen = lease.get("generations", lease.get("generation"))
        cur = current.get("current_generations", current.get("current_generation"))
        if isinstance(gen, Mapping) or isinstance(cur, Mapping):
            if not isinstance(gen, Mapping) or not isinstance(cur, Mapping):
                raise LeaseRefused("generations must be compared per resource")
            generations = {_text(k, "resource"): _int(v, "generation") for k, v in gen.items()}
            current_generations = {_text(k, "resource"): _int(v, "current_generation") for k, v in cur.items()}
        else:
            generations = {"*": _int(gen, "generation")}
            current_generations = {"*": _int(cur, "current_generation")}
        for flag in ("active", "consumed", "uncertain"):
            if not isinstance(lease.get(flag), bool):
                raise LeaseRefused(f"{flag} must be a boolean")
    except LeaseRefused:
        return GuardResult("refused", ("schema_invalid",))
    if epoch != current_epoch or owner != current_owner or generations != current_generations:
        return GuardResult("refused", ("lease_identity_mismatch",))
    if not lease["active"]:
        return GuardResult("refused", ("lease_inactive",))
    if now >= expires_at:
        return GuardResult("refused", ("lease_expired",))
    if lease["uncertain"]:
        return GuardResult("refused", ("effect_uncertain",))
    if lease["consumed"]:
        return GuardResult("refused", ("grant_consumed",))
    return GuardResult("eligible", ())


DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS resources (resource TEXT PRIMARY KEY, generation INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS leases (
    lease_id TEXT PRIMARY KEY, epoch TEXT NOT NULL, owner TEXT NOT NULL, role TEXT NOT NULL,
    request_sha256 TEXT NOT NULL, subject_sha256 TEXT NOT NULL, acquired_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1, ended_at INTEGER, end_reason TEXT);
CREATE TABLE IF NOT EXISTS lease_resources (
    lease_id TEXT NOT NULL REFERENCES leases(lease_id), resource TEXT NOT NULL REFERENCES resources(resource),
    generation INTEGER NOT NULL, PRIMARY KEY (lease_id, resource));
CREATE TABLE IF NOT EXISTS grants (
    grant_id TEXT PRIMARY KEY, lease_id TEXT NOT NULL REFERENCES leases(lease_id), project TEXT NOT NULL,
    semantic_operation TEXT NOT NULL, request_id TEXT NOT NULL, request_sha256 TEXT NOT NULL, state TEXT NOT NULL,
    issued_at INTEGER NOT NULL, started_at INTEGER, observed_at INTEGER, result TEXT,
    UNIQUE (project, semantic_operation, request_id));
"""


@dataclass(frozen=True)
class LeaseBundle:
    lease_id: str
    epoch: str
    owner: str
    role: str
    generations: Mapping[str, int]
    request_sha256: str
    subject_sha256: str
    expires_at: int

    def to_dict(self) -> dict:
        return {"lease_id": self.lease_id, "epoch": self.epoch, "owner": self.owner, "role": self.role,
                "generations": dict(self.generations), "request_sha256": self.request_sha256,
                "subject_sha256": self.subject_sha256, "expires_at": self.expires_at}


class LeaseStore:
    """The transactional store. Every public method takes the observed `now`; none makes a
    network call; a refusal changes nothing."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.connection = sqlite3.connect(str(self.path), isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000)
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        try:
            self._migrate()
        except Exception:
            self.connection.close()
            raise

    # ---- schema and clock ----------------------------------------------------------------------
    def _migrate(self) -> None:
        # executescript commits on its own; the schema is created before the metadata transaction.
        self.connection.executescript(DDL)
        with self._transaction():
            version = self._meta("schema_version")
            if version is None:
                self.connection.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            elif int(version) != SCHEMA_VERSION:
                raise LeaseRefused(f"lease store schema {version} is not {SCHEMA_VERSION}")
            if self._meta("operational_epoch") is None:
                self.connection.execute("INSERT INTO meta VALUES ('operational_epoch', ?)", (secrets.token_hex(8),))
            if self._meta("last_now") is None:
                self.connection.execute("INSERT INTO meta VALUES ('last_now', '0')")

    def _meta(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    class _Tx:
        def __init__(self, connection: sqlite3.Connection):
            self.connection = connection

        def __enter__(self):
            self.connection.execute("BEGIN IMMEDIATE")
            return self

        def __exit__(self, kind, value, traceback):
            if kind is None:
                self.connection.execute("COMMIT")
            else:
                self.connection.execute("ROLLBACK")
            return False

    def _transaction(self) -> "LeaseStore._Tx":
        return LeaseStore._Tx(self.connection)

    def _observe_now(self, now: int) -> int:
        """Trusted clock observation inside the transaction: a rollback blocks authority."""
        now = _int(now, "now")
        last = int(self._meta("last_now") or 0)
        if now < last:
            raise LeaseRefused("clock rollback observed; time-dependent authority blocked")
        self.connection.execute("UPDATE meta SET value = ? WHERE key = 'last_now'", (str(now),))
        return now

    @property
    def epoch(self) -> str:
        return str(self._meta("operational_epoch"))

    def rotate_epoch(self) -> str:
        """A restore or an operator reset: every existing bundle becomes an identity mismatch."""
        with self._transaction():
            value = secrets.token_hex(8)
            self.connection.execute("UPDATE meta SET value = ? WHERE key = 'operational_epoch'", (value,))
        return value

    # ---- leases --------------------------------------------------------------------------------
    def _generation_rows(self, resources: Sequence[str]) -> dict:
        marks = ",".join("?" for _ in resources)
        rows = self.connection.execute(f"SELECT resource, generation FROM resources WHERE resource IN ({marks})", tuple(resources)).fetchall()
        return {str(r): int(g) for r, g in rows}

    def _live_holders(self, resources: Sequence[str], now: int) -> list:
        marks = ",".join("?" for _ in resources)
        return self.connection.execute(
            f"SELECT lr.resource, l.owner, l.lease_id FROM lease_resources lr JOIN leases l ON l.lease_id = lr.lease_id "
            f"WHERE lr.resource IN ({marks}) AND l.active = 1 AND l.expires_at > ?", (*resources, now)).fetchall()

    def _unresolved_operations(self, resources: Sequence[str]) -> list:
        marks = ",".join("?" for _ in resources)
        states = ",".join("?" for _ in UNRESOLVED)
        return self.connection.execute(
            f"SELECT DISTINCT lr.resource FROM grants g JOIN lease_resources lr ON lr.lease_id = g.lease_id "
            f"WHERE lr.resource IN ({marks}) AND g.state IN ({states})", (*resources, *sorted(UNRESOLVED))).fetchall()

    def acquire_many(self, *, owner: str, role: str, resources: Iterable[str], request_sha256: str, subject_sha256: str,
                     ttl_seconds: int, now: int) -> LeaseBundle:
        """All sorted resources or none, in one immediate transaction."""
        owner, role = _text(owner, "owner"), _text(role, "role")
        names = sorted({_text(r, "resource") for r in resources})
        if not names or len(names) > MAX_RESOURCES:
            raise LeaseRefused("a lease needs between one and 64 resources")
        for name, value in (("request_sha256", request_sha256), ("subject_sha256", subject_sha256)):
            if not isinstance(value, str) or not SHA256.fullmatch(value):
                raise LeaseRefused(f"{name} must be a sha256")
        ttl = _int(ttl_seconds, "ttl_seconds", minimum=1)
        with self._transaction():
            now = self._observe_now(now)
            held = self._live_holders(names, now)
            if held:
                raise LeaseRefused("resource held by a live lease: " + ", ".join(sorted({f"{r}@{o}" for r, o, _ in held})))
            pending = self._unresolved_operations(names)
            if pending:
                raise LeaseRefused("resource has an unresolved operation: " + ", ".join(sorted(str(r[0]) for r in pending)))
            for name in names:
                self.connection.execute("INSERT OR IGNORE INTO resources(resource, generation) VALUES (?, 0)", (name,))
                self.connection.execute("UPDATE resources SET generation = generation + 1 WHERE resource = ?", (name,))
            generations = self._generation_rows(names)
            lease_id = secrets.token_hex(16)
            epoch = self.epoch
            self.connection.execute(
                "INSERT INTO leases(lease_id, epoch, owner, role, request_sha256, subject_sha256, acquired_at, expires_at, active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)", (lease_id, epoch, owner, role, request_sha256, subject_sha256, now, now + ttl))
            for name in names:
                self.connection.execute("INSERT INTO lease_resources(lease_id, resource, generation) VALUES (?, ?, ?)",
                                        (lease_id, name, generations[name]))
        return LeaseBundle(lease_id, epoch, owner, role, generations, request_sha256, subject_sha256, now + ttl)

    def _lease_view(self, lease_id: str) -> tuple[dict, dict] | None:
        row = self.connection.execute("SELECT epoch, owner, role, request_sha256, subject_sha256, expires_at, active FROM leases WHERE lease_id = ?",
                                      (lease_id,)).fetchone()
        if row is None:
            return None
        generations = {str(r): int(g) for r, g in self.connection.execute(
            "SELECT resource, generation FROM lease_resources WHERE lease_id = ?", (lease_id,)).fetchall()}
        current_generations = self._generation_rows(sorted(generations)) if generations else {}
        unresolved = self.connection.execute(
            "SELECT COUNT(*) FROM grants WHERE lease_id = ? AND state IN ('started', 'uncertain')", (lease_id,)).fetchone()[0]
        uncertain = self.connection.execute("SELECT COUNT(*) FROM grants WHERE lease_id = ? AND state = 'uncertain'", (lease_id,)).fetchone()[0]
        lease = {"epoch": row[0], "owner": row[1], "role": row[2], "request_sha256": row[3], "subject_sha256": row[4],
                 "expires_at": int(row[5]), "active": bool(row[6]), "generations": generations,
                 "uncertain": uncertain > 0, "consumed": False, "in_flight": unresolved > 0}
        current = {"current_epoch": self.epoch, "current_owner": row[1], "current_generations": current_generations}
        return lease, current

    def _guard(self, bundle: LeaseBundle, now: int, *, consumed: bool = False) -> tuple[GuardResult, dict]:
        view = self._lease_view(bundle.lease_id)
        if view is None:
            return GuardResult("refused", ("lease_identity_mismatch",)), {}
        lease, current = view
        presented = {"epoch": bundle.epoch, "owner": bundle.owner, "generations": dict(bundle.generations),
                     "expires_at": lease["expires_at"], "active": lease["active"], "consumed": consumed, "uncertain": lease["uncertain"]}
        # The store's own record of the bundle must also match what is presented.
        if (lease["role"], lease["request_sha256"], lease["subject_sha256"]) != (bundle.role, bundle.request_sha256, bundle.subject_sha256):
            return GuardResult("refused", ("lease_identity_mismatch",)), lease
        return validate_generation(presented, current, now=now), lease

    def heartbeat(self, bundle: LeaseBundle, *, ttl_seconds: int, now: int) -> GuardResult:
        ttl = _int(ttl_seconds, "ttl_seconds", minimum=1)
        with self._transaction():
            now = self._observe_now(now)
            result, _ = self._guard(bundle, now)
            if result.status != "eligible":
                return result
            self.connection.execute("UPDATE leases SET expires_at = ? WHERE lease_id = ?", (now + ttl, bundle.lease_id))
        return result

    def release(self, bundle: LeaseBundle, *, now: int) -> GuardResult:
        with self._transaction():
            now = self._observe_now(now)
            result, lease = self._guard(bundle, now)
            if result.status != "eligible" and result.reason_codes != ("lease_expired",):
                return result
            if lease.get("in_flight"):
                return GuardResult("refused", ("effect_uncertain",))
            self.connection.execute("UPDATE leases SET active = 0, ended_at = ?, end_reason = 'released' WHERE lease_id = ?",
                                    (now, bundle.lease_id))
        return GuardResult("eligible", ())

    def reap(self, *, now: int) -> tuple[str, ...]:
        """Expired leases become inactive tombstones; permission is removed, presence is not proved."""
        with self._transaction():
            now = self._observe_now(now)
            rows = self.connection.execute("SELECT lease_id FROM leases WHERE active = 1 AND expires_at <= ?", (now,)).fetchall()
            ids = tuple(sorted(str(r[0]) for r in rows))
            for lease_id in ids:
                self.connection.execute("UPDATE leases SET active = 0, ended_at = ?, end_reason = 'expired' WHERE lease_id = ?", (now, lease_id))
                self.connection.execute("UPDATE grants SET state = 'expired', observed_at = ? WHERE lease_id = ? AND state = 'issued'", (now, lease_id))
        return ids

    # ---- grants --------------------------------------------------------------------------------
    def consume_grant(self, bundle: LeaseBundle, *, project: str, semantic_operation: str, request_id: str,
                      request_sha256: str, now: int) -> dict:
        """Validate the live bundle, reject a pending or uncertain prior operation on it, refuse an id
        reused with different bytes, replay an identical observed operation without executing, and
        otherwise durably record `started` for exactly one new operation."""
        project, semantic_operation, request_id = _text(project, "project"), _text(semantic_operation, "semantic_operation"), _text(request_id, "request_id")
        if not isinstance(request_sha256, str) or not SHA256.fullmatch(request_sha256):
            raise LeaseRefused("request_sha256 must be a sha256")
        with self._transaction():
            now = self._observe_now(now)
            existing = self.connection.execute(
                "SELECT grant_id, lease_id, request_sha256, state, result FROM grants WHERE project = ? AND semantic_operation = ? AND request_id = ?",
                (project, semantic_operation, request_id)).fetchone()
            if existing is not None:
                grant_id, lease_id, prior_sha, state, result = existing
                if prior_sha != request_sha256:
                    raise LeaseRefused("operation id reused with different request bytes (conflict)")
                if state in {"observed_success", "observed_failure"}:
                    return {"grant_id": grant_id, "state": state, "replayed": True, "result": json.loads(result) if result else None,
                            "execute": False}
                return {"grant_id": grant_id, "state": state, "replayed": True, "result": None, "execute": False,
                        "refused": "prior operation is pending or uncertain"}
            result, lease = self._guard(bundle, now)
            if result.status != "eligible":
                raise LeaseRefused("lease guard refused: " + ",".join(result.reason_codes))
            if lease.get("in_flight"):
                raise LeaseRefused("a prior operation on this lease is pending or uncertain")
            grant_id = secrets.token_hex(16)
            self.connection.execute(
                "INSERT INTO grants(grant_id, lease_id, project, semantic_operation, request_id, request_sha256, state, issued_at, started_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'started', ?, ?)", (grant_id, bundle.lease_id, project, semantic_operation, request_id, request_sha256, now, now))
        return {"grant_id": grant_id, "state": "started", "replayed": False, "result": None, "execute": True}

    def observe_operation(self, grant_id: str, *, outcome: str, result: Mapping[str, Any] | None, now: int) -> dict:
        """Record the independent observation of one started operation. Terminal states are final;
        a lost response is `uncertain` and stays so until an operator reconciles it (no replay)."""
        if outcome not in {"success", "failure", "uncertain"}:
            raise LeaseRefused("unknown operation outcome")
        state = {"success": "observed_success", "failure": "observed_failure", "uncertain": "uncertain"}[outcome]
        with self._transaction():
            now = self._observe_now(now)
            row = self.connection.execute("SELECT state FROM grants WHERE grant_id = ?", (grant_id,)).fetchone()
            if row is None:
                raise LeaseRefused("unknown grant")
            if row[0] != "started" and not (row[0] == "uncertain" and outcome != "uncertain"):
                raise LeaseRefused(f"grant in state {row[0]!r} cannot be observed as {outcome!r}")
            self.connection.execute("UPDATE grants SET state = ?, observed_at = ?, result = ? WHERE grant_id = ?",
                                    (state, now, json.dumps(dict(result or {}), sort_keys=True), grant_id))
        return {"grant_id": grant_id, "state": state}

    def grant(self, grant_id: str) -> dict | None:
        row = self.connection.execute("SELECT grant_id, lease_id, project, semantic_operation, request_id, state, result FROM grants WHERE grant_id = ?",
                                      (grant_id,)).fetchone()
        if row is None:
            return None
        return {"grant_id": row[0], "lease_id": row[1], "project": row[2], "semantic_operation": row[3], "request_id": row[4],
                "state": row[5], "result": json.loads(row[6]) if row[6] else None}

    def lease(self, lease_id: str) -> dict | None:
        view = self._lease_view(lease_id)
        return None if view is None else {**view[0], **view[1], "lease_id": lease_id}

    def close(self) -> None:
        self.connection.close()


__all__ = ["GRANT_STATES", "GuardResult", "LeaseBundle", "LeaseRefused", "LeaseStore", "TERMINAL", "validate_generation"]
